"""
TDN-PO-00014, 28 Nov 2026: the order was saved again while its delivery was being keyed, and the receipt was refused line
by line ("po line: Invalid pk … object does not exist"). Saving an order re-made every row with a new id. Now a row that
stays keeps its id; a receipt still holding an id that is gone finds the order's row for the same item; and a row that
goods were already received against cannot be cut below them or taken off.
"""
import pytest

from .test_grn_edit import _approved_order, ready  # noqa: F401 — fixture

pytestmark = pytest.mark.django_db


def _edit(api, po, lines):
    full = api.get(f"/api/v1/purchase-orders/{po['id']}").data
    return api.patch(f"/api/v1/purchase-orders/{po['id']}", {"version": full["version"], "lines": lines}, format="json")


def _row(variant, qty, price):
    return {"variant": str(variant.id), "qty": qty, "unit_price": price}


def test_saving_an_order_again_keeps_its_rows(api, ready):
    po = _approved_order(api, ready)
    before = {str(l["variant"]): str(l["id"]) for l in po["lines"]}
    r = _edit(api, po, [_row(ready["variant"], "30", "100"), _row(ready["second"], "24", "210")])
    assert r.status_code == 200, r.content
    after = {str(l["variant"]): str(l["id"]) for l in api.get(f"/api/v1/purchase-orders/{po['id']}").data["lines"]}
    assert after == before, "the same items keep the same rows"
    lines = {str(l["variant"]): l for l in api.get(f"/api/v1/purchase-orders/{po['id']}").data["lines"]}
    assert float(lines[str(ready["variant"].id)]["qty_ordered"]) == 30 and float(lines[str(ready["second"].id)]["unit_price"]) == 210


def test_a_receipt_keyed_before_the_order_was_saved_again_still_saves(api, ready):
    po = _approved_order(api, ready)
    stale = po["lines"]                                   # what the open receiving screen holds
    from apps.core.tenancy import tenant_context
    from apps.purchasing.models import PurchaseOrderLine

    with tenant_context(ready["wh"].tenant, ready["wh"].tenant.owner):
        PurchaseOrderLine.objects.filter(po_id=po["id"]).delete()   # as the old save did: every row re-made
    assert _edit(api, po, [_row(ready["variant"], "24", "100"), _row(ready["second"], "24", "200")]).status_code == 200
    fresh = {str(l["variant"]): str(l["id"]) for l in api.get(f"/api/v1/purchase-orders/{po['id']}").data["lines"]}
    r = api.post("/api/v1/goods-receipts", {"warehouse": str(ready["wh"].id), "po": po["id"], "lines": [
        {"po_line": stale[0]["id"], "variant": stale[0]["variant"], "qty_accepted": "10", "batch_no": "B-1", "expiry_date": "2027-12-31"},
        {"po_line": stale[1]["id"], "variant": stale[1]["variant"], "qty_accepted": "5", "batch_no": "B-2", "expiry_date": "2028-06-30"},
    ]}, format="json")
    assert r.status_code == 201, r.content
    assert {str(l["po_line"]) for l in r.data["lines"]} == {fresh[str(stale[0]["variant"])], fresh[str(stale[1]["variant"])]}


def test_an_item_no_longer_on_the_order_is_refused_plainly(api, ready):
    po = _approved_order(api, ready)
    stale = po["lines"][1]
    assert _edit(api, po, [_row(ready["variant"], "24", "100")]).status_code == 200   # the second item taken off
    r = api.post("/api/v1/goods-receipts", {"warehouse": str(ready["wh"].id), "po": po["id"], "lines": [
        {"po_line": stale["id"], "variant": stale["variant"], "qty_accepted": "5", "batch_no": "B-2", "expiry_date": "2028-06-30"}]}, format="json")
    assert r.status_code == 400 and "no longer on" in str(r.content) and "reload the page" in str(r.content)


def test_what_already_arrived_holds_the_order_row(api, ready):
    """A posted receipt moves the order on, and a received order is not edited — the rows it came in on stay."""
    po = _approved_order(api, ready)
    r = api.post("/api/v1/goods-receipts", {"warehouse": str(ready["wh"].id), "po": po["id"], "post": True, "lines": [
        {"po_line": po["lines"][0]["id"], "variant": po["lines"][0]["variant"], "qty_accepted": "18", "batch_no": "B-1", "expiry_date": "2027-12-31"}]}, format="json")
    assert r.status_code == 201, r.content
    assert _edit(api, po, [_row(ready["second"], "24", "200")]).status_code == 422


def test_the_service_will_not_cut_a_row_below_what_arrived(api, ready):
    """The second lock, in the service itself, whatever screen calls it."""
    from decimal import Decimal

    from apps.core.exceptions import DomainError
    from apps.core.tenancy import tenant_context
    from apps.purchasing.models import PurchaseOrder, PurchaseOrderLine
    from apps.purchasing.services import PurchaseOrderService

    po = _approved_order(api, ready)
    tenant = ready["wh"].tenant
    with tenant_context(tenant, tenant.owner):
        PurchaseOrderLine.objects.filter(pk=po["lines"][0]["id"]).update(qty_received=Decimal("18"))
        order = PurchaseOrder.objects.get(pk=po["id"])
        with pytest.raises(DomainError) as fewer:
            PurchaseOrderService.write_lines(order, [{"variant": ready["variant"], "qty": "10", "unit_price": "100"}, {"variant": ready["second"], "qty": "24", "unit_price": "200"}])
        assert fewer.value.detail.code == "below_received"
        with pytest.raises(DomainError) as gone:
            PurchaseOrderService.write_lines(order, [{"variant": ready["second"], "qty": "24", "unit_price": "200"}])
        assert gone.value.detail.code == "line_received"
