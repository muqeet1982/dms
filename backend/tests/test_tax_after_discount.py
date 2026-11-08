"""
How tax is worked out on a tax invoice (owner, 5 Nov 2026): "remove tax from t.p amount and remove discount and then add
tax on the amount". T.P 1,255 at 25% with 5% off: 1,004 less 50.20 is 953.80, plus 238.45 tax is 1,192.25.

This replaces the printed retail price as a floor under the tax on 3rd Schedule goods. The MRP is now only a figure on the
FBR filing, so its absence no longer stops the tax switch, though it still stops the filing.
"""
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from apps.core.tenancy import tenant_context

from .conftest import issue_order, seed_basic

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(api, tenant):
    from apps.catalog.models import Product, ProductVariant, TaxType

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        TaxType.objects.create(code="ST", name="Sales Tax", kind="sales_tax", applies_to="both")
        Product.objects.filter(pk=v.product_id).update(sale_price=D("1255"))
        ProductVariant.objects.filter(pk=v.pk).update(sale_price=D("1255"))
    return wh, cust, v


def _third_schedule(tenant, v, mrp):
    from apps.catalog.models import Product

    with tenant_context(tenant, tenant.owner):
        Product.objects.filter(pk=v.product_id).update(fbr_rate_desc="25%", fbr_hs_code="3305.9020", fbr_sale_type="3rd Schedule Goods",
                                                       fbr_uom="Numbers, pieces, units", fbr_sro_schedule="297(I)/2023-Table-I", fbr_sro_item="10", mrp_ex_tax=mrp)


def _order(api, wh, cust, v, qty="10", discount="0", charge_tax=True):
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "charge_tax": charge_tax,
                                          "lines": [{"variant": str(v.id), "qty": qty, "discount_pct": discount}]}, format="json")
    assert r.status_code == 201, r.content
    return r.data


def _invoice(api, order, **body):
    issue_order(api, order["id"])
    made = api.post(f"/api/v1/sales-orders/{order['id']}/invoice", {"apply_tax": True, **body}, format="json")
    assert made.status_code == 201, made.content
    return made.data[0] if isinstance(made.data, list) else made.data


def _figures(line):
    return D(line["unit_price"]), D(line["discount_amount"]), D(line["tax1_amount"]), D(line["line_total"])


def test_the_owners_sum_on_one_unit(api, tenant, world):
    wh, cust, v = world
    inv = _invoice(api, _order(api, wh, cust, v, qty="1", discount="5"))
    assert _figures(inv["lines"][0]) == (D("1004"), D("50.20"), D("238.45"), D("1192.25"))
    assert D(inv["subtotal"]) == D("1004") and D(inv["discount"]) == D("50.20") and D(inv["tax1_total"]) == D("238.45") and D(inv["total"]) == D("1192.25")


@pytest.mark.parametrize("mrp", [None, D("0"), D("1255"), D("1495")], ids=["no-mrp", "mrp-0", "mrp-is-the-tp", "mrp-is-the-printed-price"])
def test_a_3rd_schedule_item_is_taxed_on_the_amount_whatever_its_mrp(api, tenant, world, mrp):
    """Tax on 2,510 before: a discount never lowered it, and the customer paid 12,047.98 where the owner's sum says 11,922.50."""
    wh, cust, v = world
    _third_schedule(tenant, v, mrp)
    order = _order(api, wh, cust, v, qty="10", discount="5")
    assert (D(order["subtotal"]), D(order["tax1_total"]), D(order["total"])) == (D("10040"), D("2384.50"), D("11922.50")), "the order reads the same"
    inv = _invoice(api, order)
    assert _figures(inv["lines"][0]) == (D("1004"), D("502"), D("2384.50"), D("11922.50"))
    assert D(inv["total"]) == D(order["total"])


def test_advance_income_tax_comes_last_on_the_grand_total(api, tenant, world):
    """Owner, 5 Nov 2026: "advance income tax will be charged on grand total". T.P less tax, less discount, plus tax is the
    Grand Total; the customer's % is charged on that, on the order and on its invoice alike."""
    from apps.partners.models import Customer

    wh, cust, v = world
    with tenant_context(tenant, tenant.owner):
        Customer.objects.filter(pk=cust.pk).update(advance_income_tax_rate=D("0.5"))
    order = _order(api, wh, cust, v, qty="10", discount="5")
    assert D(order["advance_tax_total"]) == D("59.61") and D(order["total"]) == D("11982.11"), "0.5% of 11,922.50"
    inv = _invoice(api, order)
    assert D(inv["subtotal"]) == D("10040") and D(inv["discount"]) == D("502") and D(inv["tax1_total"]) == D("2384.50")
    assert D(inv["advance_tax_rate"]) == D("0.5") and D(inv["advance_tax_total"]) == D("59.61") and D(inv["total"]) == D("11982.11")

    plain = _invoice(api, _order(api, wh, cust, v, qty="10", discount="5", charge_tax=False), apply_tax=False)
    assert D(plain["advance_tax_total"]) == 0, "a plain invoice carries none (owner, 8 Nov 2026)"


def test_with_no_discount_the_line_comes_back_to_its_tp(api, tenant, world):
    wh, cust, v = world
    _third_schedule(tenant, v, D("1495"))
    inv = _invoice(api, _order(api, wh, cust, v, qty="3"))
    assert _figures(inv["lines"][0]) == (D("1004"), D("0"), D("753"), D("3765")), "3 × 1,255"


def test_the_switch_and_the_automatic_bill_no_longer_stop_at_a_missing_mrp(api, tenant, world):
    from apps.core.models import Notification
    from apps.partners.models import Customer

    wh, cust, v = world
    _third_schedule(tenant, v, D("0"))
    plain = _invoice(api, _order(api, wh, cust, v, qty="2", charge_tax=False), apply_tax=False, post=False)
    r = api.post(f"/api/v1/invoices/{plain['id']}/tax", {"apply": True}, format="json")
    assert r.status_code == 200 and D(r.data["tax1_total"]) == D("502"), r.content

    with tenant_context(tenant, tenant.owner):
        Customer.objects.filter(pk=cust.pk).update(charge_tax=True)
    order = _order(api, wh, cust, v, qty="2")
    issue_order(api, order["id"])
    assert api.post(f"/api/v1/sales-orders/{order['id']}/mark-delivered", {"receiver_name": "Shop"}, format="json").status_code == 200
    billed = api.get("/api/v1/invoices", {"order": order["id"]}).data["results"][0]
    assert D(billed["tax1_total"]) == D("502")
    with tenant_context(tenant, tenant.owner):
        assert not Notification.objects.filter(type="invoice.tax_skipped").exists()


def test_a_discount_typed_in_rupees_comes_off_the_price_without_tax(api, tenant, world):
    """Rs 500 used to read as off the T.P with its tax inside, so the invoice showed a discount of 400."""
    from apps.sales.models import SalesInvoiceLine

    wh, cust, v = world
    inv = _invoice(api, _order(api, wh, cust, v, qty="10"), post=False)
    line = inv["lines"][0]
    r = api.patch(f"/api/v1/invoices/{inv['id']}", {"lines": [{"id": line["id"], "qty": "10", "unit_price": "1255", "discount_amount": "500"}]}, format="json")
    assert r.status_code == 200, r.content
    assert _figures(r.data["lines"][0]) == (D("1004"), D("500"), D("2385"), D("11925")), "10,040 - 500 = 9,540, plus 25%"

    again = api.patch(f"/api/v1/invoices/{inv['id']}", {"lines": [{"id": line["id"], "qty": "10", "unit_price": "1255", "discount_amount": "500"}]}, format="json")
    assert _figures(again.data["lines"][0]) == (D("1004"), D("500"), D("2385"), D("11925")), "saving it untouched changes nothing"

    halved = api.patch(f"/api/v1/invoices/{inv['id']}", {"lines": [{"id": line["id"], "qty": "5", "unit_price": "1255", "discount_amount": "500"}]}, format="json")
    assert _figures(halved.data["lines"][0]) == (D("1004"), D("250"), D("1192.50"), D("5962.50")), "the discount travels with the quantity"

    off = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": False}, format="json")
    assert off.status_code == 200
    with tenant_context(tenant, tenant.owner):
        l = SalesInvoiceLine.objects.get(pk=line["id"])
        assert (l.unit_price, l.discount_amount, l.tax1_amount) == (D("1255"), D("312.5000"), D("0"))


def _as_before_1_nov(tenant, inv):
    """A line raised before the T.P included the tax: no T.P kept, the tax added on top. TDN-INV-00074 is one."""
    from apps.sales.models import SalesInvoiceLine

    with tenant_context(tenant, tenant.owner):
        SalesInvoiceLine.objects.filter(invoice_id=inv["id"]).update(tp=None, tp_discount=0, unit_price=D("1255"), discount_amount=0, tax1_amount=D("313.75"), line_total=D("1568.75"))


def test_a_draft_from_before_the_rule_follows_it_when_the_tax_is_pressed(api, tenant, world):
    """TDN-INV-00074 read 17,763.38 with tax on top of the T.P while its order read 14,210.70."""
    from apps.sales.models import SalesInvoiceLine

    wh, cust, v = world
    order = _order(api, wh, cust, v, qty="1")
    inv = _invoice(api, order, post=False)
    _as_before_1_nov(tenant, inv)

    assert api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": False}, format="json").status_code == 200
    on = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": True}, format="json")
    assert on.status_code == 200, on.content
    assert (D(on.data["subtotal"]), D(on.data["tax1_total"]), D(on.data["total"])) == (D("1004"), D("251"), D("1255"))
    assert D(on.data["total"]) == D(order["total"]), "the invoice and its order agree"
    with tenant_context(tenant, tenant.owner):
        assert SalesInvoiceLine.objects.get(invoice_id=inv["id"]).tp == D("1255")

    # pressed straight on, with no off first, it follows too
    other = _invoice(api, _order(api, wh, cust, v, qty="1"), post=False)
    _as_before_1_nov(tenant, other)
    direct = api.post(f"/api/v1/invoices/{other['id']}/tax", {"apply": True}, format="json")
    assert D(direct.data["total"]) == D("1255")


def test_a_posted_invoice_from_before_the_rule_keeps_its_figures(api, tenant, world):
    """It is on the books and may be filed: recomputing it must not quietly rewrite it."""
    from apps.sales.models import SalesInvoice, SalesInvoiceLine
    from apps.sales.services import InvoiceService

    wh, cust, v = world
    inv = _invoice(api, _order(api, wh, cust, v, qty="1"), post=True)
    assert inv["status"] != "draft"
    _as_before_1_nov(tenant, inv)
    with tenant_context(tenant, tenant.owner):
        InvoiceService.recompute(SalesInvoice.objects.get(pk=inv["id"]))
        line = SalesInvoiceLine.objects.get(invoice_id=inv["id"])
        assert line.tp is None and line.unit_price == D("1255")


def _filing(cust):
    return SimpleNamespace(seller_ntn_cnic="1234567", seller_business_name="Demo", seller_province="Punjab", seller_address="Lahore",
                           buyer_ntn_cnic="", buyer_business_name=cust.name, buyer_province="Punjab", buyer_address="Lahore",
                           buyer_registration_type="Unregistered")


def test_the_filing_carries_what_the_invoice_charged(api, tenant, world):
    """The filing recomputes each line and refuses if it disagrees with the invoice, so both must read the same base."""
    from apps.fbr.services import build_payload
    from apps.sales.models import SalesInvoice

    wh, cust, v = world
    _third_schedule(tenant, v, D("1255"))
    inv = _invoice(api, _order(api, wh, cust, v, qty="10", discount="5"), post=True)
    with tenant_context(tenant, tenant.owner):
        item = build_payload(SalesInvoice.objects.get(pk=inv["id"]), _filing(cust), use_sandbox=False)["items"][0]
    assert D(str(item["valueSalesExcludingST"])) == D("9538") and D(str(item["discount"])) == D("502")
    assert D(str(item["salesTaxApplicable"])) == D("2384.50") and D(str(item["totalValues"])) == D("11922.50")
    assert D(str(item["fixedNotifiedValueOrRetailPrice"])) == D("1004"), "the retail price still goes to FBR, as a figure"


def test_filing_still_asks_for_the_retail_price(api, tenant, world):
    from apps.catalog.models import Product
    from apps.fbr import engine

    wh, cust, v = world
    _third_schedule(tenant, v, D("0"))
    with tenant_context(tenant, tenant.owner):
        assert any("retail price" in x for x in engine.product_fbr_problems(Product.objects.get(pk=v.product_id)))
