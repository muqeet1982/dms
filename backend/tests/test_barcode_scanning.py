"""
Scanning: resolve a code, scan items into a goods receipt and a load form, generate an internal barcode for a
product that has none (the Keune catalogue), and print labels. A keyboard-wedge scanner just types the code and
presses Enter, so everything here is what the browser or the app sends when that happens.
"""
import pytest
from decimal import Decimal

pytestmark = pytest.mark.django_db


@pytest.fixture
def scanned(api, tenant):
    """A stocked, barcoded variant plus the warehouse and supplier to receive more of it."""
    from apps.core.tenancy import tenant_context

    from .conftest import seed_basic

    wh, supplier, variant, customer = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        code = variant.barcodes.first().code
    return {"wh": wh, "supplier": supplier, "variant": variant, "customer": customer, "code": code}


def test_resolve_by_barcode_and_by_sku(api, scanned):
    hit = api.get("/api/v1/barcodes/resolve", {"code": scanned["code"]})
    assert hit.status_code == 200, hit.content
    assert hit.data["variant"]["id"] == str(scanned["variant"].id)

    typed = api.get("/api/v1/barcodes/resolve", {"code": scanned["variant"].sku})
    assert typed.status_code == 200 and typed.data["variant"]["id"] == str(scanned["variant"].id)

    assert api.get("/api/v1/barcodes/resolve", {"code": "NOPE-0000"}).status_code == 404


def test_scan_lookup_shows_stock(api, scanned):
    r = api.get("/api/v1/inventory/stock/lookup", {"code": scanned["code"]})
    assert r.status_code == 200, r.content
    assert r.data["variant"]["sku"] == scanned["variant"].sku
    assert Decimal(r.data["total_on_hand"]) > 0
    assert r.data["levels"], "a scan must say where the stock is"


def test_scanning_into_a_goods_receipt(api, scanned):
    from apps.core.tenancy import tenant_context
    from apps.inventory.services import StockService

    def on_hand():
        with tenant_context(scanned["supplier"].tenant, None):
            return StockService.on_hand(scanned["variant"], scanned["wh"])[0]

    before = on_hand()
    grn = api.post("/api/v1/goods-receipts", {"supplier": str(scanned["supplier"].id), "warehouse": str(scanned["wh"].id), "lines": []}, format="json")
    assert grn.status_code == 201, grn.content

    first = api.post(f"/api/v1/goods-receipts/{grn.data['id']}/scans", {"scans": [{"code": scanned["code"], "qty": 3}]}, format="json")
    assert first.status_code == 200, first.content
    assert len(first.data["lines"]) == 1 and Decimal(first.data["lines"][0]["qty_accepted"]) == 3

    again = api.post(f"/api/v1/goods-receipts/{grn.data['id']}/scans", {"scans": [{"code": scanned["code"], "qty": 2}]}, format="json")
    assert Decimal(again.data["lines"][0]["qty_accepted"]) == 5, "a second scan of the same code adds to the line"

    posted = api.post(f"/api/v1/goods-receipts/{grn.data['id']}/post", {}, format="json")
    assert posted.status_code == 200 and posted.data["status"] == "posted"
    assert on_hand() == before + 5


def test_scanning_into_a_load_form(api, scanned):
    order = api.post("/api/v1/sales-orders", {
        "customer": str(scanned["customer"].id), "warehouse": str(scanned["wh"].id), "confirm": True,
        "lines": [{"variant": str(scanned["variant"].id), "qty": "4"}],
    }, format="json")
    assert order.status_code == 201, order.content
    form = api.post(f"/api/v1/sales-orders/{order.data['id']}/load-form", {}, format="json")
    assert form.status_code == 201, form.content

    picked = api.post(f"/api/v1/pick-lists/{form.data['id']}/scans", {"scans": [{"code": scanned["code"], "qty": 4}]}, format="json")
    assert picked.status_code == 200, picked.content
    assert any(Decimal(l["qty_picked"]) == 4 for l in picked.data["lines"])
    assert picked.data["status"] == "picking"

    unknown = api.post(f"/api/v1/pick-lists/{form.data['id']}/scans", {"scans": [{"code": "NOPE-0000", "qty": 1}]}, format="json")
    assert unknown.status_code == 404, "an unreadable scan must be reported, not silently counted"


def test_internal_barcode_for_a_product_that_has_none(api, tenant):
    from apps.catalog.models import UnitOfMeasure
    from apps.catalog.services import ProductService
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        pc = UnitOfMeasure.objects.get(code="PC")
        p = ProductService.create_product(tenant, {"sku": "NOBC-1", "name": "No barcode item", "base_uom": pc, "cost_price": 10, "sale_price": 20})
        variant_id = str(p.variants.first().id)

    gen = api.post(f"/api/v1/variants/{variant_id}/generate-barcode", {}, format="json")
    assert gen.status_code in (200, 201), gen.content
    code = gen.data["code"]
    assert code, "generating a barcode must return the code to print"
    assert api.get("/api/v1/barcodes/resolve", {"code": code}).data["variant"]["id"] == variant_id


def test_label_printing(api, scanned):
    r = api.post("/api/v1/labels/print", {"size": "50x30", "items": [{"variant": str(scanned["variant"].id), "qty": 2, "show_price": True}]}, format="json")
    assert r.status_code == 200, r.content
    assert r["Content-Type"] == "application/pdf"
    assert len(r.content) > 1000, "a label sheet should be a real PDF"


def test_product_codes_unit_and_box(api, tenant):
    """
    A product carries the UPC on the unit and (optionally) a different barcode on the box. Scanning the unit code
    counts one; scanning the box code counts a whole box.
    """
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        from apps.catalog.models import UnitOfMeasure

        uom = str(UnitOfMeasure.objects.get(code="PC").id)

    made = api.post("/api/v1/products", {
        "name": "Box test shampoo", "upc": "8964111000019", "box_upc": "18964111000016", "item_code": "SH-100",
        "base_uom": uom, "packing": 12, "cost_price": "100", "sale_price": "150",
    }, format="json")
    assert made.status_code == 201, made.content
    assert made.data["sku"] == "8964111000019", "the internal key follows the UPC when none is given"
    assert made.data["item_code"] == "SH-100"

    unit = api.get("/api/v1/barcodes/resolve", {"code": "8964111000019"})
    assert unit.status_code == 200 and unit.data["packaging"] in (None, "")
    box = api.get("/api/v1/barcodes/resolve", {"code": "18964111000016"})
    assert box.status_code == 200
    assert box.data["variant"]["id"] == unit.data["variant"]["id"], "both codes point at the same item"

    # the box barcode is attached to a packaging level of 12, which is what makes one scan count twelve
    with tenant_context(tenant, tenant.owner):
        from apps.catalog.models import Barcode

        bc = Barcode.objects.get(code="18964111000016")
        assert bc.packaging is not None and bc.packaging.qty_in_base == 12

    # a box barcode without a box size makes no sense
    bad = api.post("/api/v1/products", {"name": "No box size", "upc": "8964111000026", "box_upc": "18964111000023", "base_uom": uom, "packing": 1}, format="json")
    assert bad.status_code == 400 and "packing" in bad.data["errors"]

    # and two products cannot share a code
    clash = api.post("/api/v1/products", {"name": "Clashing", "upc": "8964111000019", "base_uom": uom}, format="json")
    assert clash.status_code == 400, clash.content


def test_scanning_a_box_into_a_receipt_counts_the_whole_box(api, tenant, scanned):
    """The point of the box barcode: one scan, twelve units."""
    from apps.core.tenancy import tenant_context
    from apps.inventory.services import StockService

    with tenant_context(tenant, tenant.owner):
        product = scanned["variant"].product
        product.packing = 12
        product.box_upc = "18964222000012"
        product.save(update_fields=["packing", "box_upc"])
        from apps.catalog.services import ProductService

        ProductService.sync_carton_packaging(product)
        ProductService.sync_codes(product)
        before = StockService.on_hand(scanned["variant"], scanned["wh"])[0]

    grn = api.post("/api/v1/goods-receipts", {"supplier": str(scanned["supplier"].id), "warehouse": str(scanned["wh"].id), "lines": []}, format="json")
    assert grn.status_code == 201, grn.content
    one_box = api.post(f"/api/v1/goods-receipts/{grn.data['id']}/scans", {"scans": [{"code": "18964222000012", "qty": 1}]}, format="json")
    assert one_box.status_code == 200, one_box.content
    assert float(one_box.data["lines"][0]["qty_accepted"]) == 12, "one box scan = 12 units"

    api.post(f"/api/v1/goods-receipts/{grn.data['id']}/scans", {"scans": [{"code": scanned["code"], "qty": 1}]}, format="json")
    posted = api.post(f"/api/v1/goods-receipts/{grn.data['id']}/post", {}, format="json")
    assert posted.status_code == 200
    with tenant_context(tenant, tenant.owner):
        assert StockService.on_hand(scanned["variant"], scanned["wh"])[0] == before + 13, "a box plus a loose unit"


def test_box_label_carries_the_item_code_and_the_batch(api, tenant):
    """
    A carton label like `16418.01#195654`: before the dot is the item code, after it is the batch number. One scan
    means one box, and the batch comes back even though no batch row exists yet.
    """
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        from apps.catalog.models import UnitOfMeasure
        from apps.catalog.services import BarcodeParser, ProductService

        product = ProductService.create_product(tenant, {
            "upc": "8964333000011", "item_code": "16418", "name": "Carton label item",
            "base_uom": UnitOfMeasure.objects.get(code="PC"), "packing": 20, "cost_price": 10, "sale_price": 20,
        })

        label = BarcodeParser.parse("16418.01#195654")
        assert label["variant"].product_id == product.pk
        assert label["unit"] == "box" and label["qty_per_scan"] == 20
        assert label["batch_no"] == "01#195654", "everything after the dot is the batch"
        assert label["batch"] is None, "the batch row does not have to exist yet"

        plain = BarcodeParser.parse("16418")
        assert plain["unit"] == "box" and plain["qty_per_scan"] == 20 and plain["batch_no"] == ""

        unit = BarcodeParser.parse("8964333000011")
        assert unit["unit"] == "unit" and unit["qty_per_scan"] == 1, "the UPC on the unit still counts one"

        no_separator = BarcodeParser.parse("16418195654")
        assert no_separator["batch_no"] == "195654", "a label printed without a dot splits after the item code"

    # the same thing through the API, which is what the receiving screen calls
    hit = api.get("/api/v1/barcodes/resolve", {"code": "16418.01#195654"})
    assert hit.status_code == 200, hit.content
    assert hit.data["unit"] == "box" and hit.data["qty_per_scan"] == "20.000"
    assert hit.data["batch_no"] == "01#195654" and hit.data["expiry_date"] is None
    assert hit.data["item_code"] == "16418" and hit.data["packing"] == 20


def test_units_per_box_decides_what_a_box_scan_counts(api, tenant):
    """A carton packaging left behind with a different size must not change what one scan means."""
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        from apps.catalog.models import Packaging, UnitOfMeasure
        from apps.catalog.services import BarcodeParser, ProductService

        product = ProductService.create_product(tenant, {
            "item_code": "77777", "name": "Stale carton item", "base_uom": UnitOfMeasure.objects.get(code="PC"),
            "packing": 10, "cost_price": 1, "sale_price": 2,
        })
        ProductService.sync_carton_packaging(product)
        Packaging.objects.filter(variant__product=product, name="carton").update(qty_in_base=144)

        hit = BarcodeParser.parse("77777")
        assert hit["qty_per_scan"] == 10, "the product's units-per-box wins"
        assert Packaging.objects.get(variant__product=product, name="carton").qty_in_base == 10, "and the packaging is repaired"


def test_scanning_into_a_receipt_is_matched_against_the_order(api, tenant):
    """A carton that is not on the order is refused, so the wrong goods cannot be received quietly."""
    from apps.core.tenancy import tenant_context

    from .conftest import seed_basic

    wh, supplier, variant, _c = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        from apps.catalog.models import UnitOfMeasure
        from apps.catalog.services import ProductService

        product = variant.product
        product.item_code, product.packing = "55501", 12
        product.save(update_fields=["item_code", "packing"])
        ProductService.sync_carton_packaging(product)
        other = ProductService.create_product(tenant, {
            "item_code": "99999", "name": "Not on the order", "base_uom": UnitOfMeasure.objects.get(code="PC"),
            "packing": 6, "cost_price": 1, "sale_price": 2,
        })
        other_code = other.item_code

    po = api.post("/api/v1/purchase-orders", {
        "supplier": str(supplier.id), "warehouse": str(wh.id),
        "lines": [{"variant": str(variant.id), "qty": "24", "unit_price": "100"}],
    }, format="json")
    api.post(f"/api/v1/purchase-orders/{po.data['id']}/send", {}, format="json")
    grn = api.post("/api/v1/goods-receipts", {"supplier": str(supplier.id), "warehouse": str(wh.id), "po": po.data["id"], "lines": []}, format="json")

    ok = api.post(f"/api/v1/goods-receipts/{grn.data['id']}/scans", {"scans": [{"code": "55501.B77"}]}, format="json")
    assert ok.status_code == 200, ok.content
    scanned = ok.data["scanned"][0]
    assert scanned["unit"] == "box" and scanned["counted"] == "12.000"
    assert scanned["batch_no"] == "B77" and scanned["needs_expiry"] is True, "the screen is told to ask for an expiry"
    assert scanned["po_line"], "the scan was matched to the order line"

    wrong = api.post(f"/api/v1/goods-receipts/{grn.data['id']}/scans", {"scans": [{"code": other_code}]}, format="json")
    assert wrong.status_code == 400 and wrong.data["type"] == "not_on_order", wrong.data
    assert "not on" in wrong.data["detail"]


def test_a_batch_is_optional_on_a_receipt_but_an_expiry_date_is_not(api, tenant):
    """
    The two flags ask different things of the person on the dock, and the difference is deliberate.

    A **batch** number can be substituted — the receipt's own number keeps the stock traceable and sellable, so
    nothing is asked. An **expiry date** cannot be invented, and it is the entire point of its flag: the
    near-expiry alerts, the expiry report and shelf-life picking all read it. So a batch-tracked product posts
    with no batch typed, and an expiry-tracked one is refused until the date is read off the carton.

    (This test previously asserted that no expiry was ever asked for, which was true only because the flag did
    nothing at all. The owner asked for it to mean something.)
    """
    from apps.core.tenancy import tenant_context

    from .conftest import seed_basic

    wh, supplier, variant, _c = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        product = variant.product
        product.is_batch_tracked, product.is_expiry_tracked = True, True
        product.save(update_fields=["is_batch_tracked", "is_expiry_tracked"])

    body = {
        "supplier": str(supplier.id), "warehouse": str(wh.id),
        "lines": [{"variant": str(variant.id), "qty_accepted": "5", "note": "left of the dock door"}],
        "post": True,
    }
    refused = api.post("/api/v1/goods-receipts", body, format="json")
    assert refused.status_code == 400 and refused.data["type"] == "expiry_required", refused.content

    with tenant_context(tenant, tenant.owner):
        product.is_expiry_tracked = False
        product.save(update_fields=["is_expiry_tracked"])

    grn = api.post("/api/v1/goods-receipts", body, format="json")
    assert grn.status_code == 201, grn.content
    assert grn.data["status"] == "posted"
    line = grn.data["lines"][0]
    assert line["expiry_date"] is None, "no expiry is asked of a product that is not expiry-tracked"
    assert line["batch_no"] == grn.data["number"], "a tracked product keeps its traceability: the receipt is the batch"
    assert line["note"] == "left of the dock door"

    # a product that is not batch-tracked takes nothing at all
    with tenant_context(tenant, tenant.owner):
        product.is_batch_tracked = product.is_expiry_tracked = False
        product.save(update_fields=["is_batch_tracked", "is_expiry_tracked"])
    plain = api.post("/api/v1/goods-receipts", {
        "supplier": str(supplier.id), "warehouse": str(wh.id),
        "lines": [{"variant": str(variant.id), "qty_accepted": "2"}], "post": True,
    }, format="json")
    assert plain.status_code == 201 and plain.data["lines"][0]["batch_no"] == ""


def test_promoting_a_barcode_demotes_the_old_primary(api, tenant):
    """
    A variant has one primary barcode — that is the code its labels print.

    The nested create route demoted the others; editing arrives through the flat route, which did not, so
    correcting a mistyped code and ticking "primary" left two primaries and the label printed whichever the
    database returned first. Scanning never depended on this flag, which is why it went unnoticed.
    """
    from apps.catalog.models import Barcode
    from apps.core.tenancy import tenant_context

    from .conftest import seed_basic

    wh, supplier, variant, customer = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        first = Barcode.objects.create(variant=variant, code="8719281000001", is_primary=True)
        second = Barcode.objects.create(variant=variant, code="8719281000002", is_primary=False)

    r = api.patch(f"/api/v1/barcodes/{second.pk}", {"is_primary": True}, format="json")
    assert r.status_code == 200, r.content

    with tenant_context(tenant, tenant.owner):
        first.refresh_from_db()
        second.refresh_from_db()
        assert second.is_primary is True
        assert first.is_primary is False, "two primaries on one variant means an unpredictable label"
        assert Barcode.objects.filter(variant=variant, is_primary=True).count() == 1


def test_correcting_a_code_to_one_already_in_use_is_refused(api, tenant):
    """The real case: two Keune item codes were shipped sharing one UPC, so this collision is live data."""
    from apps.catalog.models import Barcode
    from apps.core.tenancy import tenant_context

    from .conftest import seed_basic

    wh, supplier, variant, customer = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        Barcode.objects.create(variant=variant, code="8719281111443")
        other = Barcode.objects.create(variant=variant, code="8719281058533")

    r = api.patch(f"/api/v1/barcodes/{other.pk}", {"code": "8719281111443"}, format="json")
    assert r.status_code == 400, r.content
    assert "code" in (r.data.get("errors") or {}), r.data


def test_the_barcode_on_a_carton_is_read_as_a_carton(api, tenant):
    """
    A shipping carton carries a GTIN-14: the unit's EAN-13 behind a packaging indicator digit. Nobody registers
    those by hand — of 495 products in the live catalogue, 429 had a unit UPC and not one had a carton code — so
    the storeman scanned the carton and was told no product matched. The number inside it is the unit's, and the
    indicator says it is a case of them, so one scan counts the whole carton.
    """
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        from apps.catalog.models import UnitOfMeasure

        uom = str(UnitOfMeasure.objects.get(code="PC").id)

    made = api.post("/api/v1/products", {
        "name": "Carton code conditioner", "upc": "8964111000033", "item_code": "CC-200",
        "base_uom": uom, "packing": 24, "cost_price": "100", "sale_price": "150",
    }, format="json")
    assert made.status_code == 201, made.content

    unit = api.get("/api/v1/barcodes/resolve", {"code": "8964111000033"})
    assert unit.status_code == 200 and unit.data["unit"] == "unit" and unit.data["qty_per_scan"] == "1"

    carton = api.get("/api/v1/barcodes/resolve", {"code": "18964111000030"})  # indicator 1 + the EAN's body
    assert carton.status_code == 200, carton.content
    assert carton.data["variant"]["id"] == unit.data["variant"]["id"], "the same item, read off the carton"
    assert carton.data["unit"] == "box" and Decimal(carton.data["qty_per_scan"]) == 24

    # indicator 0 is not a case — it is the same single article padded to fourteen digits
    padded = api.get("/api/v1/barcodes/resolve", {"code": "08964111000033"})
    assert padded.status_code == 200 and padded.data["unit"] == "unit"

    # A UPC-A has twelve digits and an EAN-13 is the same code with a zero in front; which of the two a scanner
    # reports depends on the scanner, and both have to find the product.
    upca = api.post("/api/v1/products", {"name": "American carton", "upc": "012345678905", "item_code": "US-1",
                                         "base_uom": uom, "packing": 6, "cost_price": "10", "sale_price": "15"}, format="json")
    assert upca.status_code == 201, upca.content
    as_ean = api.get("/api/v1/barcodes/resolve", {"code": "0012345678905"})
    assert as_ean.status_code == 200, as_ean.content
    assert as_ean.data["item_code"] == "US-1" and as_ean.data["unit"] == "unit"

    # and a number that belongs to nothing is still refused rather than guessed at
    assert api.get("/api/v1/barcodes/resolve", {"code": "19999999999996"}).status_code == 404


def test_a_long_item_code_is_not_cut_short(api, tenant):
    """
    Item codes here run from five to eight digits. A label with no separator used to be split after five, so
    scanning `215370` quietly resolved to the *different* product coded `21537` — a wrong line on a receipt that
    nobody would catch. The whole code is tried as an item code first.
    """
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        from apps.catalog.models import UnitOfMeasure

        uom = str(UnitOfMeasure.objects.get(code="PC").id)

    for code in ("21537", "215370"):
        r = api.post("/api/v1/products", {"name": f"Item {code}", "item_code": code, "base_uom": uom,
                                          "packing": 12, "cost_price": "10", "sale_price": "15"}, format="json")
        assert r.status_code == 201, r.content

    long_code = api.get("/api/v1/barcodes/resolve", {"code": "215370"})
    assert long_code.status_code == 200, long_code.content
    assert long_code.data["item_code"] == "215370", "the six-digit code is its own product"
    assert long_code.data["batch_no"] == "", "nothing was taken off the end and called a batch"

    short = api.get("/api/v1/barcodes/resolve", {"code": "21537"})
    assert short.data["item_code"] == "21537"

    # the documented box label still splits: item code, then the batch after it
    labelled = api.get("/api/v1/barcodes/resolve", {"code": "21537.B2510"})
    assert labelled.status_code == 200 and labelled.data["item_code"] == "21537"
    assert labelled.data["batch_no"] == "B2510"


def test_a_carton_label_of_item_code_plus_batch_reads_the_whole_code(api, tenant):
    """
    The carton labels in use here are the item code followed by the batch — `16418.01#195654`, or the same
    thing with no separator at all. Ten products in the live catalogue differ from another only by a letter on
    the end (16418/16418D, 50037/50037B, 62051/62051UC), and the split was fixed at five characters: a carton of
    **16418D** was read as **16418**, a different product packed 6 to a carton instead of 96. Nobody would catch
    that on a receipt. The longest item code that actually exists wins.
    """
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        from apps.catalog.models import UnitOfMeasure

        uom = str(UnitOfMeasure.objects.get(code="PC").id)

    for code, packing in (("16418", 6), ("16418D", 96)):
        r = api.post("/api/v1/products", {"name": f"Colour {code}", "item_code": code, "base_uom": uom,
                                          "packing": packing, "cost_price": "100", "sale_price": "150"}, format="json")
        assert r.status_code == 201, r.content

    for label, item_code, packing, batch in [
        ("16418D.01#195654", "16418D", 96, "01#195654"),   # the documented label
        ("16418D195654", "16418D", 96, "195654"),          # the same label, printed without a separator
        ("16418.01#195654", "16418", 6, "01#195654"),       # and the plain code still means the plain product
        ("16418195654", "16418", 6, "195654"),
        ("16418D", "16418D", 96, ""),                       # typed by hand, no batch on it
    ]:
        hit = api.get("/api/v1/barcodes/resolve", {"code": label})
        assert hit.status_code == 200, (label, hit.content)
        assert hit.data["item_code"] == item_code, f"{label} resolved to {hit.data['item_code']}"
        assert hit.data["unit"] == "box" and Decimal(hit.data["qty_per_scan"]) == packing, label
        assert hit.data["batch_no"] == batch, label

    # a label whose leading part is nothing we stock is refused, not guessed at
    assert api.get("/api/v1/barcodes/resolve", {"code": "99999195654"}).status_code == 404


def test_a_carton_scan_does_not_depend_on_our_own_check_digit(api, tenant):
    """
    The check digit exists so a scanner can catch its own misreads, not so our records can be checked against
    it — and a catalogue typed in from a price list does not always agree: 8 of 429 codes in the live catalogue
    do not check out, and some are stored as twelve digits with the check digit missing altogether. Refusing a
    good carton because of our own typing would be the worse failure, so the article is matched on its digits.
    """
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        from apps.catalog.models import UnitOfMeasure

        uom = str(UnitOfMeasure.objects.get(code="PC").id)

    # 8719281128260 is NOT a valid EAN-13 (it should end in 7) — exactly the shape of the live data
    made = api.post("/api/v1/products", {"name": "Mistyped code shampoo", "upc": "8719281128260", "item_code": "MT-1",
                                         "base_uom": uom, "packing": 12, "cost_price": "10", "sale_price": "15"}, format="json")
    assert made.status_code == 201, made.content

    for carton in ("18719281128267", "18719281128260", "28719281128264"):  # any check digit, any indicator 1-8
        hit = api.get("/api/v1/barcodes/resolve", {"code": carton})
        assert hit.status_code == 200, (carton, hit.content)
        assert hit.data["item_code"] == "MT-1" and hit.data["unit"] == "box", carton
        assert Decimal(hit.data["qty_per_scan"]) == 12, carton

    # indicator 0 is the single article, not a case of them
    single = api.get("/api/v1/barcodes/resolve", {"code": "08719281128260"})
    assert single.status_code == 200 and single.data["unit"] == "unit"

    # and a number belonging to no article of ours is still refused
    assert api.get("/api/v1/barcodes/resolve", {"code": "17777777777770"}).status_code == 404
