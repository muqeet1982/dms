"""
A load form issues from the batches that hold the stock when it is issued (TDN, 11 Nov 2026): TDN-PL-00214 was made on
7 Nov holding batch 204400, the earliest expiry; two other notes sold that batch out within the hour, and four days later
the issue refused "on hand 0" while 6 sat in batch 212528. An item that is not batch-tracked draws on any batch, its own
first, then the earliest expiry. And an order on a load form keeps its stock hold until the warehouse issues it: TDN-SO-
00214's ran out after 48 hours and the next order took its stock.
"""
from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.utils import timezone

from apps.core.tenancy import tenant_context

from .conftest import seed_basic

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(api, tenant):
    """An item (not batch-tracked) in two batches: A expires first and holds 5, B holds 6."""
    from apps.catalog.models import UnitOfMeasure
    from apps.catalog.services import ProductService
    from apps.inventory.models import Batch
    from apps.inventory.services import StockService

    wh, _sup, _v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        p = ProductService.create_product(tenant, {"sku": "ASH-613", "name": "6.13 Dark Ash", "base_uom": UnitOfMeasure.objects.get(code="PC"), "cost_price": D("100"), "sale_price": D("150")})
        v = p.variants.first()
        today = timezone.localdate()
        a = Batch.objects.create(variant=v, batch_no="204400", expiry_date=today + timedelta(days=1200))
        b = Batch.objects.create(variant=v, batch_no="212528", expiry_date=today + timedelta(days=1500))
        StockService.move(warehouse=wh, variant=v, qty=D("5"), movement_type="grn", batch=a, unit_cost=D("100"))
        StockService.move(warehouse=wh, variant=v, qty=D("6"), movement_type="grn", batch=b, unit_cost=D("100"))
    return wh, v, cust, a, b


def _order(api, wh, v, cust, qty):
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": qty}]}, format="json")
    assert r.status_code == 201, r.content
    return r.data


def _form(api, order):
    r = api.post(f"/api/v1/sales-orders/{order['id']}/load-form", {"send": True}, format="json")
    assert r.status_code == 201, r.content
    return r.data


def _issue(api, form):
    return api.post(f"/api/v1/pick-lists/{form['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in form["lines"]]}, format="json")


def _on_hand(tenant, wh, batch):
    from apps.inventory.models import StockLevel

    with tenant_context(tenant):
        return sum(StockLevel.objects.filter(warehouse=wh, batch=batch).values_list("qty_on_hand", flat=True))


def test_a_batch_emptied_after_the_form_was_made_is_taken_from_the_next(api, tenant, world):
    wh, v, cust, a, b = world
    first = _form(api, _order(api, wh, v, cust, "2"))
    assert [l["batch_no"] if "batch_no" in l else l.get("batch") for l in first["lines"]] and len(first["lines"]) == 1
    # another order's form takes all of batch A and goes out first
    second = _form(api, _order(api, wh, v, cust, "5"))
    assert _issue(api, second).status_code == 200
    assert _on_hand(tenant, wh, a) == 0

    r = _issue(api, first)
    assert r.status_code == 200, r.content
    assert _on_hand(tenant, wh, b) == D("4"), "the 2 came out of batch B"
    from apps.delivery.models import DeliveryNoteLine, PickListLine

    with tenant_context(tenant):
        assert [(l.batch.batch_no, l.qty_picked) for l in PickListLine.objects.filter(picklist_id=first["id"])] == [("212528", D("2"))], "the form says which batch went"
        assert [(l.batch.batch_no, l.qty_loaded) for l in DeliveryNoteLine.objects.filter(delivery_note__picklist_id=first["id"])] == [("212528", D("2"))]


def test_a_line_its_batch_can_only_part_cover_is_split(api, tenant, world):
    wh, v, cust, a, b = world
    first = _form(api, _order(api, wh, v, cust, "3"))          # 3 from A
    second = _form(api, _order(api, wh, v, cust, "4"))         # 4 from A too: the forms do not hold batches
    assert _issue(api, second).status_code == 200              # A has 1 left
    assert _issue(api, first).status_code == 200
    from apps.delivery.models import PickListLine

    with tenant_context(tenant):
        got = sorted((l.batch.batch_no, l.qty_to_pick, l.qty_picked) for l in PickListLine.objects.filter(picklist_id=first["id"]))
    assert got == [("204400", D("1"), D("1")), ("212528", D("2"), D("2"))], got
    assert _on_hand(tenant, wh, a) == 0 and _on_hand(tenant, wh, b) == D("4")


def test_short_in_every_batch_still_says_so(api, tenant, world):
    from apps.inventory.services import StockService

    wh, v, cust, a, b = world
    first = _form(api, _order(api, wh, v, cust, "5"))          # 5 from A
    with tenant_context(tenant, tenant.owner):                 # then 9 of the 11 are written off: 2 left in all
        StockService.move(warehouse=wh, variant=v, qty=D("-3"), movement_type="adjustment", batch=a)
        StockService.move(warehouse=wh, variant=v, qty=D("-6"), movement_type="adjustment", batch=b)
    r = _issue(api, first)
    assert r.status_code == 422, r.content
    assert r.data["detail"] == "Not enough stock in Main. 6.13 Dark Ash: 2 in stock, 5 entered. Enter what the warehouse can hand over (0 leaves the item for later) and confirm again.", r.data
    assert _on_hand(tenant, wh, a) == D("2"), "nothing went out"


def test_an_item_out_of_stock_entered_as_0_lets_the_rest_go(api, tenant, world):
    """TDN-PL-00214: another order took the last of one item; the warehouse issues everything else and that item waits."""
    from apps.catalog.models import UnitOfMeasure
    from apps.catalog.services import ProductService
    from apps.inventory.services import StockService

    wh, v, cust, a, b = world
    with tenant_context(tenant, tenant.owner):
        other = ProductService.create_product(tenant, {"sku": "OTHER-1", "name": "9.31 Golden Ash", "base_uom": UnitOfMeasure.objects.get(code="PC"), "cost_price": D("100"), "sale_price": D("150")}).variants.first()
        StockService.move(warehouse=wh, variant=other, qty=D("10"), movement_type="grn", unit_cost=D("100"))
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(v.id), "qty": "2"}, {"variant": str(other.id), "qty": "3"}]}, format="json")
    assert r.status_code == 201, r.content
    form = _form(api, r.data)
    with tenant_context(tenant, tenant.owner):                 # every piece of the first item is gone
        StockService.move(warehouse=wh, variant=v, qty=D("-5"), movement_type="adjustment", batch=a)
        StockService.move(warehouse=wh, variant=v, qty=D("-6"), movement_type="adjustment", batch=b)
    assert _issue(api, form).status_code == 422
    issues = [{"line_id": l["id"], "qty_issued": "0" if l["variant_sku"] == "ASH-613" else l["qty_to_pick"]} for l in form["lines"]]
    done = api.post(f"/api/v1/pick-lists/{form['id']}/issue", {"issues": issues}, format="json")
    assert done.status_code == 200 and done.data["status"] == "partially_issued", done.content


def test_an_order_on_a_load_form_keeps_its_hold(api, tenant, world):
    from apps.sales.models import StockReservation
    from apps.sales.services import OrderService

    wh, v, cust, a, b = world
    held = _order(api, wh, v, cust, "2")
    loose = _order(api, wh, v, cust, "1")
    _form(api, held)
    with tenant_context(tenant):
        StockReservation.objects.filter(released=False).update(expires_at=timezone.now() - timedelta(hours=1))
        OrderService.expire_reservations()
        live = {r.order_line.order_id for r in StockReservation.objects.filter(released=False)}
    assert str(held["id"]) in {str(x) for x in live}, "on a load form: still held"
    assert str(loose["id"]) not in {str(x) for x in live}, "no load form: the hold ran out as before"


def test_the_issue_window_starts_at_what_is_in_stock(api, tenant, world):
    """TDN-PL-00214 (11 Nov 2026): the window offered the full 2 of an item with 0 left, so every Confirm Issue was
    refused. It now reads what each item can take out (every batch together) and starts Issued there."""
    from apps.catalog.models import UnitOfMeasure
    from apps.catalog.services import ProductService
    from apps.inventory.services import StockService

    wh, v, cust, a, b = world
    with tenant_context(tenant, tenant.owner):
        other = ProductService.create_product(tenant, {"sku": "OTHER-1", "name": "9.31 Golden Ash", "base_uom": UnitOfMeasure.objects.get(code="PC"), "cost_price": D("100"), "sale_price": D("150")}).variants.first()
        StockService.move(warehouse=wh, variant=other, qty=D("10"), movement_type="grn", unit_cost=D("100"))
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(v.id), "qty": "8"}, {"variant": str(other.id), "qty": "3"}]}, format="json")
    assert r.status_code == 201, r.content
    form = _form(api, r.data)
    def by_sku(got):          # an item one batch cannot cover is on two lines, one per batch: added up
        out = {}
        for l in form["lines"]:
            out[l["variant_sku"]] = format((D(out.get(l["variant_sku"], "0")) + D(got[l["id"]])).normalize(), "f")
        return out
    assert by_sku(api.get(f"/api/v1/pick-lists/{form['id']}/in-stock").data["lines"]) == {"ASH-613": "8", "OTHER-1": "3"}, "both batches together cover the 8"

    with tenant_context(tenant, tenant.owner):                 # another order empties batch A and takes 4 of B: 2 left
        StockService.move(warehouse=wh, variant=v, qty=D("-5"), movement_type="adjustment", batch=a)
        StockService.move(warehouse=wh, variant=v, qty=D("-4"), movement_type="adjustment", batch=b)
    got = by_sku(api.get(f"/api/v1/pick-lists/{form['id']}/in-stock").data["lines"])
    assert got == {"ASH-613": "2", "OTHER-1": "3"}, got
    issues = [{"line_id": l["id"], "qty_issued": api.get(f"/api/v1/pick-lists/{form['id']}/in-stock").data["lines"][l["id"]]} for l in form["lines"]]
    done = api.post(f"/api/v1/pick-lists/{form['id']}/issue", {"issues": issues}, format="json")
    assert done.status_code == 200 and done.data["status"] == "partially_issued", done.content
    assert _on_hand(tenant, wh, b) == 0, "the 2 left went out"
    assert len(form["lines"]) == 3, "the 8 was split over batches A and B when the form was made"


def test_with_negative_stock_allowed_nothing_is_capped(api, tenant, world):
    wh, v, cust, a, b = world
    wh.allow_negative_stock = True
    wh.save(update_fields=["allow_negative_stock"])
    form = _form(api, _order(api, wh, v, cust, "2"))
    assert api.get(f"/api/v1/pick-lists/{form['id']}/in-stock").data["lines"] is None
