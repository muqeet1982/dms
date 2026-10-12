"""
Correcting a delivery.

One purchase order collects as many receipts as the supplier sends deliveries — part now, the rest next week — but
only one receipt is ever *being keyed*. These tests pin the two halves of that: receiving again while a draft is
open fills in that draft instead of leaving a second one beside it, and a draft can be corrected rather than
re-keyed as a new receipt. A posted receipt stays as it is: its stock is in the warehouse and a journal entry
stands behind it, so it is cancelled and re-received, never quietly edited.
"""
import pytest
from decimal import Decimal

pytestmark = pytest.mark.django_db


@pytest.fixture
def ready(api, tenant):
    """A warehouse with a rack, a supplier and two products — enough for a receipt with a line to drop."""
    from apps.core.tenancy import tenant_context

    from .conftest import seed_basic

    wh, supplier, variant, _customer = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        from apps.catalog.models import UnitOfMeasure
        from apps.catalog.services import ProductService
        from apps.inventory.models import Bin

        bin_ = Bin.objects.create(warehouse=wh, code="A-01", name="Rack A shelf 1")
        variant.product.location = bin_  # where this product is normally put away, so a receipt can fall back to it
        variant.product.save(update_fields=["location"])
        second = ProductService.create_product(tenant, {
            "sku": "P-2", "name": "Colour 8.2", "supplier": supplier,
            "base_uom": UnitOfMeasure.objects.get(code="PC"), "cost_price": Decimal("200"), "sale_price": Decimal("260"),
        }).variants.first()
    return {"wh": wh, "supplier": supplier, "variant": variant, "second": second, "bin": bin_}


def _approved_order(api, ready, qty="24"):
    """An order for both products, approved and ready to receive against."""
    r = api.post("/api/v1/purchase-orders", {
        "supplier": str(ready["supplier"].id), "warehouse": str(ready["wh"].id), "reference": "GRN edit test",
        "lines": [
            {"variant": str(ready["variant"].id), "qty": qty, "unit_price": "100"},
            {"variant": str(ready["second"].id), "qty": qty, "unit_price": "200"},
        ],
    }, format="json")
    assert r.status_code == 201, r.content
    assert api.post(f"/api/v1/purchase-orders/{r.data['id']}/send", {}, format="json").status_code == 200
    return api.get(f"/api/v1/purchase-orders/{r.data['id']}").data


def _receipt(api, ready, po, *, lines=None, post=False, challan=""):
    body = {"warehouse": str(ready["wh"].id), "po": po["id"], "supplier_challan_no": challan, "post": post,
            "lines": lines if lines is not None else [
                {"po_line": po["lines"][0]["id"], "variant": po["lines"][0]["variant"], "qty_accepted": "18", "batch_no": "B-1", "expiry_date": "2027-12-31"},
                {"po_line": po["lines"][1]["id"], "variant": po["lines"][1]["variant"], "qty_accepted": "24", "batch_no": "B-2", "expiry_date": "2028-06-30"},
            ]}
    r = api.post("/api/v1/goods-receipts", body, format="json")
    assert r.status_code == 201, r.content
    return r.data


def _grns_of(api, po):
    r = api.get("/api/v1/goods-receipts", {"po": po["id"], "page_size": 50})
    assert r.status_code == 200, r.content
    return r.data["results"]


def test_a_draft_receipt_can_be_corrected_before_it_is_posted(api, ready):
    """
    The receiver keys 18 pieces against batch B-1, then the challan turns out to read 20 in batch B-9. If the only
    way to fix that is a fresh receipt, the order ends up with two GRNs and 38 pieces of stock instead of 20.
    """
    po = _approved_order(api, ready)
    grn = _receipt(api, ready, po, challan="CH-1")
    assert grn["status"] == "draft"

    patched = api.patch(f"/api/v1/goods-receipts/{grn['id']}", {
        "supplier_challan_no": "CH-1-REV",
        "lines": [{
            "po_line": po["lines"][0]["id"], "variant": po["lines"][0]["variant"], "qty_accepted": "20",
            "batch_no": "B-9", "expiry_date": "2028-01-31", "bin": str(ready["bin"].id), "unit_cost": "100",
        }],
    }, format="json")
    assert patched.status_code == 200, patched.content

    again = api.get(f"/api/v1/goods-receipts/{grn['id']}").data
    assert again["supplier_challan_no"] == "CH-1-REV", "the header is editable too"
    assert len(again["lines"]) == 1, "the second line was dropped by the correction"
    line = again["lines"][0]
    assert Decimal(line["qty_accepted"]) == 20 and line["batch_no"] == "B-9" and line["expiry_date"] == "2028-01-31"
    assert line["bin_name"].endswith("A-01"), "and where it goes"
    assert Decimal(again["total_value"]) == 2000, "the value follows the corrected quantity"
    assert again["number"] == grn["number"], "corrected in place — no second receipt, no second number"


def test_correcting_a_draft_keeps_the_rejection_reason_and_the_put_away_bin(api, ready):
    """
    Two cracked bottles with the reason written against them: changing the quantity on another line must not wipe
    that reason, or the supplier claim loses the only record of why the goods were refused.
    """
    po = _approved_order(api, ready)
    grn = _receipt(api, ready, po)
    patched = api.patch(f"/api/v1/goods-receipts/{grn['id']}", {
        "lines": [{
            "po_line": po["lines"][0]["id"], "variant": po["lines"][0]["variant"], "qty_accepted": "16",
            "qty_damaged": "2", "reject_reason": "two cracked", "note": "driver signed for it",
            "batch_no": "B-1", "expiry_date": "2027-12-31",
        }],
    }, format="json")
    assert patched.status_code == 200, patched.content
    line = api.get(f"/api/v1/goods-receipts/{grn['id']}").data["lines"][0]
    assert line["reject_reason"] == "two cracked" and line["note"] == "driver signed for it"
    assert line["bin_name"], "with no bin typed the product's own rack is still filled in, as on a new receipt"


def test_receiving_twice_against_one_order_adds_into_the_receipt_already_open(api, ready):
    """
    One order, one open receipt — and until it is posted, everything keyed into it adds up.

    This started as a fix for a reload or the back button leaving two draft receipts on one order, and the
    rows were *replaced* so a double submission could not double the stock. The owner then pointed out the
    case that breaks: a thousand items keyed five hundred before a break and five hundred after is not a
    double submission, and replacing threw the first five hundred away. Adding up is the rule now, posting is
    what moves the stock, and the running total on the screen is what shows a miskey.
    """
    po = _approved_order(api, ready)
    first = _receipt(api, ready, po)
    assert first["reused"] is False, "the first delivery opens the receipt"
    keyed_first = Decimal(next(l for l in first["lines"] if l["batch_no"] == "B-1")["qty_accepted"])

    second = _receipt(api, ready, po, lines=[{
        "po_line": po["lines"][0]["id"], "variant": po["lines"][0]["variant"], "qty_accepted": "20", "batch_no": "B-1", "expiry_date": "2027-12-31",
    }])
    assert second["id"] == first["id"] and second["number"] == first["number"], "it is the same receipt"
    assert second["reused"] is True, "and the screen is told so, instead of announcing a receipt that was never created"
    assert len(_grns_of(api, po)) == 1, "the order has one receipt, not two"
    # the default receipt keys two order lines (B-1 and B-2); the second sitting re-keys the first of them
    assert len(second["lines"]) == 2, "the same batch on the same order line stays one row"
    same = next(l for l in second["lines"] if l["batch_no"] == "B-1")
    assert Decimal(same["qty_accepted"]) == keyed_first + 20, "the second sitting adds to the first"

    receiving = api.get(f"/api/v1/purchase-orders/{po['id']}/workflow").data["steps"][2]
    assert receiving["action"]["to"] == f"/app/purchasing/grns/{first['id']}", "the order points at the open receipt"
    assert receiving["action"]["label"] == f"Continue {first['number']}", "rather than inviting a second one"


def test_a_posted_receipt_refuses_an_edit_and_says_what_to_do_instead(api, ready):
    """
    A posted receipt has moved stock and may carry a supplier bill, so editing it would leave the stock ledger and
    the inventory valuation disagreeing. The refusal has to name the way back — cancel, then receive again —
    because a user who is only told "no" keys a second receipt instead, which is how one order grows two GRNs.
    """
    from apps.core.tenancy import tenant_context
    from apps.inventory.services import StockService

    po = _approved_order(api, ready)
    grn = _receipt(api, ready, po, post=True)
    assert grn["status"] == "posted"

    refused = api.patch(f"/api/v1/goods-receipts/{grn['id']}", {
        "lines": [{"po_line": po["lines"][0]["id"], "variant": po["lines"][0]["variant"], "qty_accepted": "99", "batch_no": "B-1"}],
    }, format="json")
    assert refused.status_code == 400, refused.content
    assert refused.data["type"] == "grn_posted"
    assert grn["number"] in refused.data["detail"] and "Cancel" in refused.data["detail"], refused.data["detail"]
    assert Decimal(api.get(f"/api/v1/goods-receipts/{grn['id']}").data["lines"][0]["qty_accepted"]) == 18, "and nothing moved"

    # the advice has to work: cancelling reverses the stock, and the delivery can then be keyed again
    assert api.post(f"/api/v1/goods-receipts/{grn['id']}/cancel", {"reason": "keyed wrong"}, format="json").status_code == 200
    with tenant_context(ready["wh"].tenant, None):
        on_hand, _ = StockService.on_hand(ready["variant"], ready["wh"])
    assert on_hand == 100, "the 18 that were received are out again"
    redone = _receipt(api, ready, po, lines=[{
        "po_line": po["lines"][0]["id"], "variant": po["lines"][0]["variant"], "qty_accepted": "20", "batch_no": "B-1", "expiry_date": "2027-12-31",
    }], post=True)
    assert redone["id"] != grn["id"] and redone["status"] == "posted"


def test_a_second_delivery_after_the_first_was_posted_gets_its_own_receipt(api, ready):
    """
    Partial deliveries are normal in this trade: half the order now, the rest next week. Reusing the open draft
    must never fold a real second arrival into the posted first one — each arrival carries its own receipt, its own
    journal entry and its own batch, and the order stays partially received until the balance lands.
    """
    po = _approved_order(api, ready, qty="24")
    first = _receipt(api, ready, po, lines=[{
        "po_line": po["lines"][0]["id"], "variant": po["lines"][0]["variant"], "qty_accepted": "12", "batch_no": "FIRST", "expiry_date": "2027-12-31",
    }], post=True)
    assert api.get(f"/api/v1/purchase-orders/{po['id']}").data["status"] == "partially_received"

    second = _receipt(api, ready, po, lines=[{
        "po_line": po["lines"][0]["id"], "variant": po["lines"][0]["variant"], "qty_accepted": "12", "batch_no": "SECOND", "expiry_date": "2028-03-31",
    }], post=True)
    assert second["id"] != first["id"], "a posted receipt is never reopened for the next delivery"
    assert second["reused"] is False
    numbers = sorted(g["number"] for g in _grns_of(api, po))
    assert numbers == sorted([first["number"], second["number"]]) and len(numbers) == 2

    line = api.get(f"/api/v1/purchase-orders/{po['id']}").data["lines"][0]
    assert Decimal(line["qty_received"]) == 24 and Decimal(line["qty_pending"]) == 0, "both deliveries counted"


def test_a_draft_opened_for_scanning_is_not_overwritten_by_an_empty_submission(api, ready):
    """
    Scanner mode opens an empty receipt and scans cartons into it. Submitting the screen again with nothing keyed
    must hand back that receipt untouched — not wipe the cartons already counted on it.
    """
    po = _approved_order(api, ready)
    grn = _receipt(api, ready, po, lines=[])
    scanned = api.post(f"/api/v1/goods-receipts/{grn['id']}/scans", {"scans": [{"code": "1234567890123", "qty": 5}]}, format="json")
    assert scanned.status_code == 200, scanned.content
    assert Decimal(scanned.data["lines"][0]["qty_accepted"]) == 5

    again = _receipt(api, ready, po, lines=[])
    assert again["id"] == grn["id"] and again["reused"] is True
    assert Decimal(again["lines"][0]["qty_accepted"]) == 5, "the scanned count survives"


def test_a_cancelled_receipt_stops_counting_against_the_order(api, ready):
    """
    Once the receipt that was keyed twice is cancelled, the order has to read one receipt, not two — that count is
    the "GRNs" column on the list and the "Goods receipts" tile on the order, which is where the duplicate shows.
    """
    po = _approved_order(api, ready)
    grn = _receipt(api, ready, po, post=True)
    assert api.get(f"/api/v1/purchase-orders/{po['id']}").data["grns_count"] == 1

    cancelled = api.post(f"/api/v1/goods-receipts/{grn['id']}/cancel", {"reason": "keyed twice"}, format="json")
    assert cancelled.status_code == 200, cancelled.content
    assert api.get(f"/api/v1/purchase-orders/{po['id']}").data["grns_count"] == 0, "a receipt that was undone is not a receipt"
    row = next(r for r in api.get("/api/v1/purchase-orders", {"open": "1"}).data["results"] if r["id"] == po["id"])
    assert row["grns_count"] == 0, "the list counts it the same way as the order page"
    flow = api.get(f"/api/v1/purchase-orders/{po['id']}/workflow").data
    assert flow["steps"][2]["docs"] == [], "and the chain stops offering it as a document to open"


def test_cancelling_the_only_receipt_stops_the_order_claiming_goods_arrived(api, tenant):
    """
    An order's status only ever stepped forward, so cancelling its one receipt left it reading "partially
    received" with nothing received against it — and the buyer had no way to see the delivery was still owed.
    """
    from decimal import Decimal

    from apps.core.tenancy import tenant_context
    from apps.purchasing.models import PurchaseOrder, PurchaseOrderLine
    from apps.purchasing.services import GoodsReceiptService, PurchaseOrderService

    from .conftest import seed_basic

    wh, supplier, variant, customer = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        po = PurchaseOrderService.create(supplier=supplier, warehouse=wh,
                                         lines=[{"variant": variant, "qty": Decimal("50"), "unit_price": Decimal("100")}])
        PurchaseOrderService.send(po)
        # the line has to point at the order's line, or nothing is received *against the order*
        po_line = PurchaseOrderLine.objects.get(po=po)
        grn = GoodsReceiptService.create(supplier=supplier, warehouse=wh, po=po,
                                         lines=[{"variant": variant, "po_line": po_line, "qty_accepted": Decimal("20")}])
        GoodsReceiptService.post(grn)
        po.refresh_from_db()
        assert po.status == "partially_received"

        GoodsReceiptService.cancel(grn, reason="keyed by mistake")
        po.refresh_from_db()
        assert po.status == "sent", "with nothing received, the order is waiting on the supplier again"
        assert PurchaseOrderLine.objects.get(po=po).qty_received == 0


def test_an_expiry_tracked_product_cannot_be_posted_without_a_date(api, tenant):
    """
    "Expiry tracked" used to do nothing at all — no code branched on it, so a user ticked it and the system
    behaved identically. It now means what it says: the near-expiry alerts, the expiry report and shelf-life
    picking all read `Batch.expiry_date`, and a batch created without one is invisible to every one of them.

    The refusal is at *posting*, not at saving: the dock can save a draft and come back with the carton.
    """
    from decimal import Decimal

    from apps.core.exceptions import DomainError
    from apps.core.tenancy import tenant_context
    from apps.purchasing.models import PurchaseOrderLine
    from apps.purchasing.services import GoodsReceiptService, PurchaseOrderService

    from .conftest import seed_basic

    wh, supplier, variant, customer = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        product = variant.product
        product.is_expiry_tracked = True
        product.save(update_fields=["is_expiry_tracked"])

        po = PurchaseOrderService.create(supplier=supplier, warehouse=wh,
                                         lines=[{"variant": variant, "qty": Decimal("30"), "unit_price": Decimal("100")}])
        PurchaseOrderService.send(po)
        po_line = PurchaseOrderLine.objects.get(po=po)

        # the draft saves without a date — nothing is asked of the dock yet
        grn = GoodsReceiptService.create(supplier=supplier, warehouse=wh, po=po,
                                         lines=[{"variant": variant, "po_line": po_line, "qty_accepted": Decimal("10")}])
        assert grn.status == "draft"

        with pytest.raises(DomainError) as refused:
            GoodsReceiptService.post(grn)
        assert refused.value.detail.code == "expiry_required"
        assert variant.name in str(refused.value.detail)

        # with the date read off the box it posts, and the batch carries it
        line = grn.lines.first()
        line.expiry_date = "2027-06-30"
        line.save(update_fields=["expiry_date"])
        GoodsReceiptService.post(grn)
        grn.refresh_from_db()
        assert grn.status == "posted"
        assert str(grn.lines.first().batch.expiry_date) == "2027-06-30"


def test_an_expiry_tracked_product_gets_a_batch_even_when_it_is_not_batch_tracked(api, tenant):
    """A date needs something to hang on, so the receipt's own number becomes the batch — as it does for batches."""
    from decimal import Decimal

    from apps.core.tenancy import tenant_context
    from apps.purchasing.models import PurchaseOrderLine
    from apps.purchasing.services import GoodsReceiptService, PurchaseOrderService

    from .conftest import seed_basic

    wh, supplier, variant, customer = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        product = variant.product
        product.is_expiry_tracked, product.is_batch_tracked = True, False
        product.save(update_fields=["is_expiry_tracked", "is_batch_tracked"])

        po = PurchaseOrderService.create(supplier=supplier, warehouse=wh,
                                         lines=[{"variant": variant, "qty": Decimal("12"), "unit_price": Decimal("50")}])
        PurchaseOrderService.send(po)
        grn = GoodsReceiptService.create(supplier=supplier, warehouse=wh, po=po, supplier_challan_no="CH-991",
                                         lines=[{"variant": variant, "po_line": PurchaseOrderLine.objects.get(po=po),
                                                 "qty_accepted": Decimal("12"), "expiry_date": "2028-01-31"}])
        GoodsReceiptService.post(grn)
        batch = grn.lines.first().batch
        assert batch is not None and batch.batch_no == "CH-991", "the challan number is the batch"
        assert str(batch.expiry_date) == "2028-01-31"


def test_stock_already_on_the_shelf_under_an_undated_batch_still_moves(api, tenant):
    """The rule is for new batches. Blocking existing undated stock would strand what is already in the racks."""
    from decimal import Decimal

    from apps.core.tenancy import tenant_context
    from apps.inventory.models import Batch
    from apps.inventory.services import StockService

    from .conftest import seed_basic

    wh, supplier, variant, customer = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        legacy = Batch.objects.create(variant=variant, batch_no="OLD-1")       # created before the flag was set
        product = variant.product
        product.is_expiry_tracked = True
        product.save(update_fields=["is_expiry_tracked"])

        again = StockService.get_or_create_batch(variant, "OLD-1")
        assert again.pk == legacy.pk, "an existing batch is returned, not refused"

        # and a date supplied later is still backfilled onto it
        StockService.get_or_create_batch(variant, "OLD-1", "2029-12-31")
        legacy.refresh_from_db()
        assert str(legacy.expiry_date) == "2029-12-31"


def _po_with(tenant, wh, supplier, variant, qty="1000"):
    from apps.purchasing.models import PurchaseOrderLine
    from apps.purchasing.services import PurchaseOrderService

    po = PurchaseOrderService.create(supplier=supplier, warehouse=wh,
                                     lines=[{"variant": variant, "qty": Decimal(qty), "unit_price": Decimal("10")}])
    PurchaseOrderService.send(po)
    return po, PurchaseOrderLine.objects.get(po=po)


def test_a_thousand_items_counted_in_two_sittings_is_one_receipt_of_a_thousand(api, tenant):
    """
    The owner's second case, and the one that used to lose stock.

    A thousand items arrive. The storeman keys five hundred, breaks off, and comes back. Until the receipt is
    posted it is *the* record for that delivery, so the second five hundred add to the first. The old reuse
    replaced the rows instead, which silently threw the first five hundred away and would have posted half a
    delivery as if it were whole.
    """
    from decimal import Decimal

    from apps.core.tenancy import tenant_context
    from apps.inventory.services import StockService
    from apps.purchasing.services import GoodsReceiptService

    from .conftest import seed_basic

    wh, supplier, variant, _c = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        po, po_line = _po_with(tenant, wh, supplier, variant)
        before = StockService.on_hand(variant, wh)[0]

        first = GoodsReceiptService.create(supplier=supplier, warehouse=wh, po=po, supplier_challan_no="DC-700",
                                           lines=[{"variant": variant, "po_line": po_line, "qty_accepted": Decimal("500")}])
        assert first.lines.count() == 1 and first.lines.first().qty_accepted == 500

        # back from the break, keyed as a fresh receipt — the app's "New GRN" route
        second = GoodsReceiptService.create(supplier=supplier, warehouse=wh, po=po, supplier_challan_no="DC-700",
                                            lines=[{"variant": variant, "po_line": po_line, "qty_accepted": Decimal("500")}])
        assert second.pk == first.pk, "the same open record, not a second receipt"
        assert second.lines.count() == 1, "the same line, added to"
        assert second.lines.first().qty_accepted == 1000, "five hundred plus five hundred"
        assert StockService.on_hand(variant, wh)[0] == before, "and nothing is in stock until it is posted"

        GoodsReceiptService.post(second)
        assert StockService.on_hand(variant, wh)[0] == before + 1000
        po_line.refresh_from_db()
        assert po_line.qty_received == 1000


def test_five_items_two_today_three_tomorrow_are_two_posted_receipts(api, tenant):
    """
    The owner's first case: a genuine partial delivery. Each arrival posts on its own day, because the stock
    is physically on the shelf that day and the books have to say so.
    """
    from decimal import Decimal

    from apps.core.tenancy import tenant_context
    from apps.purchasing.models import PurchaseOrderLine
    from apps.purchasing.services import GoodsReceiptService, PurchaseOrderService

    from .conftest import seed_basic

    wh, supplier, variant, _c = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        po = PurchaseOrderService.create(supplier=supplier, warehouse=wh,
                                         lines=[{"variant": variant, "qty": Decimal("5"), "unit_price": Decimal("10")}])
        PurchaseOrderService.send(po)
        po_line = PurchaseOrderLine.objects.get(po=po)

        today = GoodsReceiptService.create(supplier=supplier, warehouse=wh, po=po, supplier_challan_no="DC-701",
                                           lines=[{"variant": variant, "po_line": po_line, "qty_accepted": Decimal("2")}])
        GoodsReceiptService.post(today)
        po.refresh_from_db()
        assert po.status == "partially_received"

        tomorrow = GoodsReceiptService.create(supplier=supplier, warehouse=wh, po=po, supplier_challan_no="DC-702",
                                              lines=[{"variant": variant, "po_line": po_line, "qty_accepted": Decimal("3")}])
        assert tomorrow.pk != today.pk, "a posted receipt is never added to — its stock has already moved"
        GoodsReceiptService.post(tomorrow)
        po.refresh_from_db()
        po_line.refresh_from_db()
        assert po_line.qty_received == 5 and po.status == "received"
        assert po.grns.exclude(status="cancelled").count() == 2, "two deliveries, two dated records"


def test_two_batches_of_the_same_item_stay_two_rows(api, tenant):
    """Adding up must not merge batches: each carries its own expiry, and picking depends on that."""
    from decimal import Decimal

    from apps.core.tenancy import tenant_context
    from apps.purchasing.services import GoodsReceiptService

    from .conftest import seed_basic

    wh, supplier, variant, _c = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        po, po_line = _po_with(tenant, wh, supplier, variant, qty="60")
        grn = GoodsReceiptService.create(supplier=supplier, warehouse=wh, po=po, supplier_challan_no="DC-703",
                                         lines=[{"variant": variant, "po_line": po_line, "qty_accepted": Decimal("20"),
                                                 "batch_no": "A1", "expiry_date": "2028-01-31"}])
        GoodsReceiptService.create(supplier=supplier, warehouse=wh, po=po, supplier_challan_no="DC-703",
                                   lines=[{"variant": variant, "po_line": po_line, "qty_accepted": Decimal("40"),
                                           "batch_no": "B2", "expiry_date": "2029-06-30"}])
        grn.refresh_from_db()
        rows = {l.batch_no: l.qty_accepted for l in grn.lines.all()}
        assert rows == {"A1": 20, "B2": 40}, rows


def test_the_same_delivery_note_cannot_start_a_second_receipt(api, tenant):
    """
    The challan is the supplier's own id for a delivery, so reusing it on a *new* receipt means the delivery
    is being keyed twice — the one case the screen cannot work out for itself.
    """
    from decimal import Decimal

    from apps.core.exceptions import DomainError
    from apps.core.tenancy import tenant_context
    from apps.purchasing.services import GoodsReceiptService

    from .conftest import seed_basic

    wh, supplier, variant, _c = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        po, po_line = _po_with(tenant, wh, supplier, variant, qty="100")
        first = GoodsReceiptService.create(supplier=supplier, warehouse=wh, po=po, supplier_challan_no="DC-900",
                                           lines=[{"variant": variant, "po_line": po_line, "qty_accepted": Decimal("40")}])
        GoodsReceiptService.post(first)

        po2, po2_line = _po_with(tenant, wh, supplier, variant, qty="100")
        with pytest.raises(DomainError) as refused:
            GoodsReceiptService.create(supplier=supplier, warehouse=wh, po=po2, supplier_challan_no="DC-900",
                                       lines=[{"variant": variant, "po_line": po2_line, "qty_accepted": Decimal("40")}])
        assert refused.value.detail.code == "duplicate_challan"
        assert first.number in str(refused.value.detail)

        # a supplier who really does reuse a number is not blocked for ever
        ok = GoodsReceiptService.create(supplier=supplier, warehouse=wh, po=po2, supplier_challan_no="DC-900",
                                        confirm_duplicate_challan=True,
                                        lines=[{"variant": variant, "po_line": po2_line, "qty_accepted": Decimal("40")}])
        assert ok.pk != first.pk
