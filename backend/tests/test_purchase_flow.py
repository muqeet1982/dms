"""
The buying flow the business runs: create → approve → receive (what arrived, what was rejected, what never came,
and where it was put) → either wait for the rest or close the line → bill → pay.
"""
import pytest
from decimal import Decimal

pytestmark = pytest.mark.django_db


@pytest.fixture
def ready(api, tenant):
    from apps.core.tenancy import tenant_context

    from .conftest import seed_basic

    wh, supplier, variant, _customer = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        from apps.inventory.models import Bin

        bin_ = Bin.objects.create(warehouse=wh, code="A-01", name="Rack A shelf 1")
        product = variant.product
        product.packing = 6
        product.item_code = "IT-6"
        product.upc = "8991000000019"  # one unit; the item code on the carton is one box of six
        product.save(update_fields=["packing", "item_code", "upc"])
        from apps.catalog.services import ProductService as _PS

        _PS.sync_codes(product)
        from apps.catalog.services import ProductService

        ProductService.sync_carton_packaging(product)
    return {"wh": wh, "supplier": supplier, "variant": variant, "bin": bin_}


def _po(api, ready, qty="24"):
    r = api.post("/api/v1/purchase-orders", {
        "supplier": str(ready["supplier"].id), "warehouse": str(ready["wh"].id), "reference": "Flow test",
        "lines": [{"variant": str(ready["variant"].id), "qty": qty, "unit_price": "100"}],
    }, format="json")
    assert r.status_code == 201, r.content
    return r.data


def test_the_chain_reads_from_the_documents(api, ready):
    po = _po(api, ready)
    flow = api.get(f"/api/v1/purchase-orders/{po['id']}/workflow")
    assert flow.status_code == 200, flow.content
    assert [s["key"] for s in flow.data["steps"]] == ["order", "approved", "receiving", "put_away", "billed", "paid"]
    assert flow.data["steps"][1]["action"]["post"].endswith("/send"), "approving is the next thing to do"
    line = flow.data["lines"][0]
    assert line["ordered"] == {"units": "24.000", "packing": 6, "boxes": 4, "loose": "0.000"}, "quantities come back in boxes too"


def test_approve_then_receive_with_a_rejection_and_a_put_away_location(api, ready):
    from apps.core.tenancy import tenant_context
    from apps.inventory.services import StockService

    po = _po(api, ready)
    approved = api.post(f"/api/v1/purchase-orders/{po['id']}/send", {}, format="json")
    assert approved.status_code == 200 and approved.data["status"] == "sent"
    assert approved.data["approved_at"], "approval is stamped so the chain can show when"

    line = api.get(f"/api/v1/purchase-orders/{po['id']}").data["lines"][0]
    grn = api.post("/api/v1/goods-receipts", {
        "supplier": str(ready["supplier"].id), "warehouse": str(ready["wh"].id), "po": po["id"],
        "lines": [{
            "po_line": line["id"], "variant": line["variant"], "qty_accepted": "18", "qty_damaged": "2", "qty_short": "0",
            "reject_reason": "two cracked", "bin": str(ready["bin"].id), "batch_no": "B-1", "expiry_date": "2027-12-31",
        }],
        "post": True,
    }, format="json")
    assert grn.status_code == 201, grn.content
    assert grn.data["lines"][0]["bin_name"].endswith("A-01"), "the receipt records where it went"
    assert grn.data["lines"][0]["reject_reason"] == "two cracked"

    with tenant_context(tenant_of(ready), None):
        pass
    flow = api.get(f"/api/v1/purchase-orders/{po['id']}/workflow").data
    row = flow["lines"][0]
    assert row["received"]["units"] == "18.000" and row["received"]["boxes"] == 3
    assert row["rejected"]["units"] == "2.000"
    assert row["pending"]["units"] == "6.000", "rejected goods are still owed by the supplier"
    assert row["placed"] and row["placed"][0]["where"].endswith("A-01")
    assert flow["steps"][3]["state"] == "done", "put-away shows as done once a receipt is posted"

    with tenant_context(ready["wh"].tenant, None):
        on_hand, _ = StockService.on_hand(ready["variant"], ready["wh"])
    assert on_hand == 118, "only the accepted 18 joined the 100 already there"


def tenant_of(ready):
    return ready["wh"].tenant


def test_closing_a_line_short_finishes_the_order(api, ready):
    po = _po(api, ready, qty="24")
    api.post(f"/api/v1/purchase-orders/{po['id']}/send", {}, format="json")
    line = api.get(f"/api/v1/purchase-orders/{po['id']}").data["lines"][0]
    api.post("/api/v1/goods-receipts", {
        "supplier": str(ready["supplier"].id), "warehouse": str(ready["wh"].id), "po": po["id"],
        "lines": [{"po_line": line["id"], "variant": line["variant"], "qty_accepted": "12", "qty_short": "12",
                   "reject_reason": "supplier short-shipped", "close_line": True, "batch_no": "B-2", "expiry_date": "2027-12-31"}],
        "post": True,
    }, format="json")
    after = api.get(f"/api/v1/purchase-orders/{po['id']}").data
    assert after["lines"][0]["closed"] is True
    assert Decimal(after["lines"][0]["qty_pending"]) == 0, "a closed line stops waiting"
    assert after["status"] == "received", "the order is finished with what arrived"


def test_close_remaining_from_the_order_page(api, ready):
    po = _po(api, ready, qty="24")
    api.post(f"/api/v1/purchase-orders/{po['id']}/send", {}, format="json")
    line = api.get(f"/api/v1/purchase-orders/{po['id']}").data["lines"][0]
    api.post("/api/v1/goods-receipts", {
        "supplier": str(ready["supplier"].id), "warehouse": str(ready["wh"].id), "po": po["id"],
        "lines": [{"po_line": line["id"], "variant": line["variant"], "qty_accepted": "6", "batch_no": "B-3", "expiry_date": "2027-12-31"}],
        "post": True,
    }, format="json")
    partial = api.get(f"/api/v1/purchase-orders/{po['id']}").data
    assert partial["status"] == "partially_received" and Decimal(partial["lines"][0]["qty_pending"]) == 18

    closed = api.post(f"/api/v1/purchase-orders/{po['id']}/close-remaining", {"reason": "not available"}, format="json")
    assert closed.status_code == 200 and closed.data["status"] == "received"
    assert closed.data["lines"][0]["closed_reason"] == "not available"
    assert api.post(f"/api/v1/purchase-orders/{po['id']}/close-remaining", {}, format="json").status_code == 422


def test_receipts_by_item_report_counts_boxes(api, ready):
    po = _po(api, ready, qty="24")
    api.post(f"/api/v1/purchase-orders/{po['id']}/send", {}, format="json")
    line = api.get(f"/api/v1/purchase-orders/{po['id']}").data["lines"][0]
    api.post("/api/v1/goods-receipts", {
        "supplier": str(ready["supplier"].id), "warehouse": str(ready["wh"].id), "po": po["id"],
        "lines": [{"po_line": line["id"], "variant": line["variant"], "qty_accepted": "18", "qty_damaged": "1",
                   "reject_reason": "dented", "bin": str(ready["bin"].id), "batch_no": "B-4", "expiry_date": "2027-12-31"}],
        "post": True,
    }, format="json")
    r = api.get("/api/v1/reports/receipts_by_item")
    assert r.status_code == 200, r.content
    row = next(x for x in r.data["rows"] if x["po"] == po["number"])
    assert (row["ordered_boxes"], row["received_boxes"], row["received_loose"]) == ("4", "3", "0.000")
    assert row["rejected"] == "1.000" and row["reason"] == "dented"
    assert row["placed_in"].endswith("A-01")
    assert row["per_box"] == "6"


def test_receiving_needs_an_approved_order(api, ready):
    """create → approve → receive: a draft order cannot be received against, and neither can a cancelled one."""
    po = _po(api, ready, qty="12")
    line = api.get(f"/api/v1/purchase-orders/{po['id']}").data["lines"][0]
    body = {"supplier": str(ready["supplier"].id), "warehouse": str(ready["wh"].id), "po": po["id"],
            "lines": [{"po_line": line["id"], "variant": line["variant"], "qty_accepted": "12", "batch_no": "B-9", "expiry_date": "2027-12-31"}], "post": True}
    too_early = api.post("/api/v1/goods-receipts", body, format="json")
    assert too_early.status_code == 422 and too_early.data["type"] == "po_not_approved", too_early.content
    assert api.get(f"/api/v1/purchase-orders/{po['id']}").data["status"] == "draft", "and the order is untouched"

    api.post(f"/api/v1/purchase-orders/{po['id']}/send", {}, format="json")
    api.post(f"/api/v1/purchase-orders/{po['id']}/cancel", {"reason": "not needed"}, format="json")
    cancelled = api.post("/api/v1/goods-receipts", body, format="json")
    assert cancelled.status_code == 422 and cancelled.data["type"] == "po_cancelled", cancelled.content


def test_a_loose_scan_adds_to_the_row_already_open(api, ready):
    """Three cartons then one loose piece: the tally for that item goes to 19, not onto a second row."""
    po = _po(api, ready, qty="24")
    api.post(f"/api/v1/purchase-orders/{po['id']}/send", {}, format="json")
    grn = api.post("/api/v1/goods-receipts", {"supplier": str(ready["supplier"].id), "warehouse": str(ready["wh"].id), "po": po["id"], "lines": []}, format="json")
    assert grn.status_code == 201, grn.content
    url = f"/api/v1/goods-receipts/{grn.data['id']}/scans"
    boxes = api.post(url, {"scans": [{"code": "IT-6"}, {"code": "IT-6"}, {"code": "IT-6"}]}, format="json")
    assert boxes.status_code == 200, boxes.content
    assert Decimal(boxes.data["lines"][0]["qty_accepted"]) == 18, "one scan of the item code is one box of six"
    loose = api.post(url, {"scans": [{"code": "8991000000019"}]}, format="json")  # the unit UPC: one piece
    assert len(loose.data["lines"]) == 1, "the loose piece joins the row that is already open"
    assert Decimal(loose.data["lines"][0]["qty_accepted"]) == 19


def test_a_receipt_takes_its_supplier_from_the_order(api, ready):
    """The receiving screen never asks who the goods came from — the order says so."""
    po = _po(api, ready, qty="12")
    api.post(f"/api/v1/purchase-orders/{po['id']}/send", {}, format="json")
    line = api.get(f"/api/v1/purchase-orders/{po['id']}").data["lines"][0]
    grn = api.post("/api/v1/goods-receipts", {
        "warehouse": str(ready["wh"].id), "po": po["id"],
        "lines": [{"po_line": line["id"], "variant": line["variant"], "qty_accepted": "12", "batch_no": "B-7", "expiry_date": "2027-12-31"}],
        "post": True,
    }, format="json")
    assert grn.status_code == 201, grn.content
    assert str(grn.data["supplier"]) == str(ready["supplier"].id) and grn.data["supplier_name"] == ready["supplier"].name

    # with neither an order nor a supplier there is nothing to receive against
    orphan = api.post("/api/v1/goods-receipts", {"warehouse": str(ready["wh"].id), "lines": []}, format="json")
    assert orphan.status_code == 400 and orphan.data["type"] == "po_required", orphan.content
