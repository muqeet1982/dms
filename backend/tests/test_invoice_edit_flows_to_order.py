"""
Owner, 27 Oct 2026: "reopen to edit should allow all kind of change and change here should also update sale order
as well".

A draft invoice (new, or a posted one reopened) takes any correction — price, discount, quantity, items added or
taken off — and the warehouse paperwork, the stock and the sale order follow it. Once the goods are delivered a
quantity is no longer a correction (it is a return or a second delivery), but prices still are.
"""
from decimal import Decimal

import pytest

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db
D = Decimal


def _second_item(tenant, wh, sup):
    from apps.catalog.models import UnitOfMeasure
    from apps.catalog.services import ProductService
    from apps.inventory.services import StockService

    with tenant_context(tenant, tenant.owner):
        p = ProductService.create_product(tenant, {"sku": "P-2", "name": "Developer 20", "supplier": sup, "base_uom": UnitOfMeasure.objects.get(code="PC"),
                                                   "cost_price": D("40"), "sale_price": D("60")})
        v2 = p.variants.first()
        StockService.move(warehouse=wh, variant=v2, qty=D("50"), movement_type="opening", unit_cost=D("40"))
        return v2


def _on_hand(tenant, wh, v):
    from apps.inventory.models import StockLevel

    with tenant_context(tenant):
        return sum((s.qty_on_hand for s in StockLevel.objects.filter(warehouse=wh, variant=v)), D("0"))


def _invoiced(api, wh, cust, v, qty="10", post=True):
    o = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(v.id), "qty": qty}]}, format="json").data
    pl = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json").data
    api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl["lines"]]}, format="json")
    inv = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": post}, format="json").data[0]
    return o, inv


def test_a_reopened_invoice_takes_any_change_and_the_order_and_stock_follow(api, tenant):
    from .conftest import seed_basic

    wh, sup, v, cust = seed_basic(tenant)
    v2 = _second_item(tenant, wh, sup)
    o, inv = _invoiced(api, wh, cust, v)                       # 10 × 150, posted
    assert _on_hand(tenant, wh, v) == D("90")
    r = api.post(f"/api/v1/invoices/{inv['id']}/reopen", {"reason": "customer wants fewer"}, format="json")
    assert r.status_code == 200, r.content

    line = api.get(f"/api/v1/invoices/{inv['id']}").data["lines"][0]
    r = api.patch(f"/api/v1/invoices/{inv['id']}", {"lines": [
        {"id": line["id"], "qty": "6", "unit_price": "140", "discount_amount": line["discount_amount"]},
        {"variant": str(v2.id), "qty": "3"},
    ]}, format="json")
    assert r.status_code == 200, r.content

    d = api.get(f"/api/v1/invoices/{inv['id']}").data
    assert d["status"] == "draft"
    got = {str(l["variant"]): (D(l["qty"]), D(l["unit_price"])) for l in d["lines"]}
    assert got == {str(v.id): (D("6"), D("140")), str(v2.id): (D("3"), D("60"))}
    assert D(d["total"]) == D("6") * 140 + 3 * 60

    # the stock: 4 back on the shelf, 3 of the new item out
    assert _on_hand(tenant, wh, v) == D("94") and _on_hand(tenant, wh, v2) == D("47")
    # the order follows: same items, quantities, prices and total
    so = api.get(f"/api/v1/sales-orders/{o['id']}").data
    ol = {str(l["variant"]): (D(l["qty_ordered"]), D(l["unit_price"]), D(l["qty_dispatched"])) for l in so["lines"]}
    assert ol == {str(v.id): (D("6"), D("140"), D("6")), str(v2.id): (D("3"), D("60"), D("3"))}
    assert D(so["total"]) == D(d["total"])
    # the delivery note carries what the van takes
    from apps.delivery.models import DeliveryNoteLine

    with tenant_context(tenant):
        loaded = {str(x.variant_id): x.qty_loaded for x in DeliveryNoteLine.objects.filter(delivery_note_id=d["delivery_note"])}
    assert loaded == {str(v.id): D("6"), str(v2.id): D("3")}

    # take the added item off again: back to stock, off the order
    added = next(l for l in d["lines"] if str(l["variant"]) == str(v2.id))
    r = api.patch(f"/api/v1/invoices/{inv['id']}", {"lines": [{"id": added["id"], "remove": True}]}, format="json")
    assert r.status_code == 200, r.content
    assert _on_hand(tenant, wh, v2) == D("50")
    so = api.get(f"/api/v1/sales-orders/{o['id']}").data
    assert [str(l["variant"]) for l in so["lines"]] == [str(v.id)]

    # posting again books the corrected figures
    r = api.post(f"/api/v1/invoices/{inv['id']}/post", {}, format="json")
    assert r.status_code == 200, r.content
    assert D(api.get(f"/api/v1/invoices/{inv['id']}").data["total"]) == D("840")


def test_the_discount_and_tax_switch_on_an_invoice_carry_to_its_order(api, tenant):
    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    o, inv = _invoiced(api, wh, cust, v, post=False)
    r = api.post(f"/api/v1/invoices/{inv['id']}/discount", {"action": "apply", "percent": "5"}, format="json")
    assert r.status_code == 200, r.content
    so = api.get(f"/api/v1/sales-orders/{o['id']}").data
    assert D(so["discount_pct"]) == D("5") and D(so["total"]) == D("1425") == D(r.data["total"])


def test_after_delivery_a_quantity_is_a_return_not_an_edit_but_a_price_still_is(api, tenant):
    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    o, inv = _invoiced(api, wh, cust, v, qty="4")
    dn = api.get(f"/api/v1/invoices/{inv['id']}").data["delivery_note"]
    assert api.post(f"/api/v1/delivery-notes/{dn}/deliver", {"receiver_name": "Shop"}, format="json").status_code == 200
    api.post(f"/api/v1/invoices/{inv['id']}/reopen", {"reason": "price"}, format="json")
    line = api.get(f"/api/v1/invoices/{inv['id']}").data["lines"][0]
    r = api.patch(f"/api/v1/invoices/{inv['id']}", {"lines": [{"id": line["id"], "qty": "3"}]}, format="json")
    assert r.status_code == 400 and "credit note" in str(r.content)
    r = api.patch(f"/api/v1/invoices/{inv['id']}", {"lines": [{"id": line["id"], "unit_price": "145"}]}, format="json")
    assert r.status_code == 200, r.content
    assert D(api.get(f"/api/v1/sales-orders/{o['id']}").data["lines"][0]["unit_price"]) == D("145")


def test_an_item_added_to_an_invoice_goes_out_by_batch_when_it_is_batch_tracked(api, tenant):
    from apps.catalog.models import Product
    from apps.inventory.services import StockService

    from .conftest import seed_basic

    wh, sup, v, cust = seed_basic(tenant)
    v2 = _second_item(tenant, wh, sup)
    with tenant_context(tenant, tenant.owner):
        Product.objects.filter(pk=v2.product_id).update(is_batch_tracked=True)
        b = StockService.get_or_create_batch(v2, "LOT-7")
        StockService.move(warehouse=wh, variant=v2, qty=D("5"), movement_type="opening", unit_cost=D("40"), batch=b)
    _o, inv = _invoiced(api, wh, cust, v, post=False)
    r = api.patch(f"/api/v1/invoices/{inv['id']}", {"lines": [{"variant": str(v2.id), "qty": "2"}]}, format="json")
    assert r.status_code == 200, r.content
    added = next(l for l in api.get(f"/api/v1/invoices/{inv['id']}").data["lines"] if str(l["variant"]) == str(v2.id))
    assert added["batch_no"] == "LOT-7" and D(added["qty"]) == D("2")


def test_an_invoice_edit_never_quietly_switches_its_orders_tax_off(api, tenant):
    """A tax order whose items have no FBR rate bills an untaxed draft (and says so); correcting that draft's
    price must not turn the ORDER's tax switch off — only flipping the invoice's tax switch on purpose does."""
    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    o = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "charge_tax": True,
                                          "lines": [{"variant": str(v.id), "qty": "2"}]}, format="json").data
    pl = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json").data
    api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl["lines"]]}, format="json")
    inv = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": False}, format="json").data[0]
    assert not inv["force_tax_type"], "unclassified item: the automatic tax was skipped"
    line = inv["lines"][0]
    assert api.patch(f"/api/v1/invoices/{inv['id']}", {"lines": [{"id": line["id"], "unit_price": "140"}]}, format="json").status_code == 200
    so = api.get(f"/api/v1/sales-orders/{o['id']}").data
    assert so["charge_tax"] is True and D(so["lines"][0]["unit_price"]) == D("140")
