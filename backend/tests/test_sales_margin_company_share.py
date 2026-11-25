"""
Gross Margin counts the company's share of the discount (owner, 25 Nov 2026: "margin column not showing correct margin. are
you adding discount received from company in margin as well?"). Waqas Traders bought 596,935 at T.P less 7%, 3 of it ours
and 4 Keune's; the report took all 7 as ours and read 3.20% where the sale had made 7.50%. The margin of every sales
report is now the income statement's gross profit, and Company Share is a column so a row adds up as printed.
"""
from decimal import Decimal as D

import pytest
from django.db.models import Sum
from django.utils import timezone

from apps.core.tenancy import tenant_context

from .conftest import seed_basic

pytestmark = pytest.mark.django_db

ALL = ("sales_by_customer", "sales_by_rep", "sales_by_product", "sales_by_brand", "sales_by_supplier", "sales_by_territory", "sales_by_route", "gross_margin_by_supplier")


def _invoice(tenant, cust, v, pct, *, qty="10", price="100", cost="60", number=None):
    from apps.sales.models import SalesInvoice, SalesInvoiceLine
    from apps.sales.services import InvoiceService

    with tenant_context(tenant, tenant.owner):
        gross = D(qty) * D(price)
        disc = gross * D(pct) / 100
        inv = SalesInvoice.objects.create(number=number or f"INV-M-{SalesInvoice.objects.count()}", customer=cust, discount_pct=D(pct), subtotal=gross, discount=disc, total=gross - disc, status="draft")
        SalesInvoiceLine.objects.create(invoice=inv, variant=v, description=v.name, qty=D(qty), unit_price=D(price), unit_cost=D(cost), discount_amount=disc, line_total=gross - disc)
        return InvoiceService.post(inv)


def _return(tenant, inv, qty):
    """Goods back on a credit note of their own, as a return on the invoice posts it."""
    from apps.sales.services import CreditNoteService

    with tenant_context(tenant, tenant.owner):
        inv.refresh_from_db()
        line = inv.lines.first()
        return CreditNoteService.post(CreditNoteService.create(customer=inv.customer, invoice=inv, reason="back", lines=[
            {"variant": line.variant, "qty": qty, "unit_price": str(line.unit_price), "unit_cost": str(line.unit_cost), "condition": "damaged", "restock": False}]))


@pytest.fixture
def shop(tenant):
    _wh, sup, v, cust = seed_basic(tenant)
    return sup, v, cust


def _row(api, key="sales_by_customer", **params):
    data = api.get(f"/api/v1/reports/{key}", params).data
    return data["rows"][0], data["totals"]


def _books(tenant):
    """What the income statement calls revenue: sales, returns, discounts and the manufacturer's incentive, all credit less debit."""
    from apps.accounting.models import GL_LIVE, JournalLine
    from apps.accounting.services import account_for

    with tenant_context(tenant):
        heads = {account_for("sales", "sales_invoice").pk, account_for("sales_return", "credit_note").pk, account_for("discount", "sales_invoice").pk, account_for("manufacturer_incentive", "company_claim").pk}
        got = JournalLine.objects.filter(entry__status__in=GL_LIVE, account_id__in=heads).aggregate(c=Sum("credit"), d=Sum("debit"))
        return got["c"] - got["d"]


def test_the_company_share_is_in_the_margin_of_every_sales_report(api, tenant, shop):
    _sup, v, cust = shop
    _invoice(tenant, cust, v, "5")                       # 1000 less 50: 30 ours, 20 the company's
    for key in ALL:
        row, t = _row(api, key)
        assert D(row["net_sales"]) == D("950") and D(row["company_share"]) == D("20"), key
        assert D(row["gross_margin"]) == D("950") + D("20") - D("600") == D("370"), key
        assert row["margin_pct"] == "38.95", (key, "370 of 950")
        assert (D(t["company_share"]), D(t["gross_margin"]), t["margin_pct"]) == (D("20"), D("370"), "38.95"), key
    assert _books(tenant) == D("970"), "950 from the customer and 20 from the company: the income statement's revenue"


def test_the_columns_add_up_as_printed(api, tenant, shop):
    _sup, v, cust = shop
    _invoice(tenant, cust, v, "5")
    data = api.get("/api/v1/reports/sales_by_customer").data
    assert data["columns"] == ["label", "qty", "sales", "returns", "net_sales", "company_share", "cogs", "gross_margin", "margin_pct", "invoices"]
    assert data["column_labels"]["margin_pct"] == "Margin %"
    r = data["rows"][0]
    assert D(r["sales"]) - D(r["returns"]) == D(r["net_sales"]) and D(r["net_sales"]) + D(r["company_share"]) - D(r["cogs"]) == D(r["gross_margin"])


def test_a_discount_the_company_does_not_fund_is_all_ours(api, tenant, shop):
    _sup, v, cust = shop
    _invoice(tenant, cust, v, "2")                       # no 2% row in the table
    row, _t = _row(api)
    assert D(row["company_share"]) == 0 and D(row["gross_margin"]) == D("980") - D("600")


def test_goods_that_came_back_are_not_funded(api, tenant, shop):
    _sup, v, cust = shop
    inv = _invoice(tenant, cust, v, "5")
    _return(tenant, inv, "4")                            # 400 less 5% = 380 back, and 8 of the company's 20 with it
    for key in ALL:
        row, t = _row(api, key)
        assert (D(row["returns"]), D(row["net_sales"]), D(row["company_share"]), D(row["cogs"])) == (D("380"), D("570"), D("12"), D("360")), (key, row)
        assert D(row["gross_margin"]) == D("222") == D(t["gross_margin"]), key
    assert _books(tenant) == D("582"), "570 + 12: the books took the 8 back as well"


def test_a_return_never_takes_back_more_than_the_discount_it_reverses(api, tenant, shop):
    """A credit note whose own discount is less than the company's share of it (a hand-edited line): the share stops there, as the books do."""
    from apps.sales.models import CreditNote, CreditNoteLine

    _sup, v, cust = shop
    inv = _invoice(tenant, cust, v, "5")
    with tenant_context(tenant, tenant.owner):
        line = inv.lines.first()
        cn = CreditNote.objects.create(number="CN-M1", customer=cust, invoice=inv, date=timezone.localdate(), reason="back", status="posted", discount=D("3"), total=D("397"))
        CreditNoteLine.objects.create(credit_note=cn, variant=v, qty=D("4"), unit_price=line.unit_price, unit_cost=line.unit_cost, line_total=D("397"))
    row, _t = _row(api)
    assert D(row["company_share"]) == D("17"), "20 less 3, not less 8"


def test_an_invoice_from_before_the_split_was_stored_on_its_lines(api, tenant, shop):
    """Until 8 Nov 2026 only the claim carried the company's share: all of October's 114,299.64 and half of November's."""
    from apps.sales.models import SalesInvoiceLine

    _sup, v, cust = shop
    inv = _invoice(tenant, cust, v, "5")
    with tenant_context(tenant, tenant.owner):
        SalesInvoiceLine.objects.filter(invoice=inv).update(company_discount=0, discount_rule="", share_pct=0)
    row, _t = _row(api)
    assert D(row["company_share"]) == D("20") and D(row["gross_margin"]) == D("370")


def test_a_cancelled_claim_pays_nothing(api, tenant, shop):
    from apps.sales.models import CompanyClaim
    from apps.sales.services import CompanyClaimService

    _sup, v, cust = shop
    inv = _invoice(tenant, cust, v, "5")
    with tenant_context(tenant, tenant.owner):
        CompanyClaimService.cancel(CompanyClaim.objects.get(invoice=inv), reason="not funded")
    row, _t = _row(api)
    assert D(row["company_share"]) == 0 and D(row["gross_margin"]) == D("350")
    assert _books(tenant) == D("950")


def test_a_salon_below_the_tp(api, tenant, shop):
    """R.P 200, T.P 150, 30% off: the company pays all but 3% of the R.P."""
    _sup, v, cust = shop
    with tenant_context(tenant, tenant.owner):
        v.retail_price = D("200")
        v.save(update_fields=["retail_price"])
        cust.type = "salon"
        cust.save(update_fields=["type"])
    _invoice(tenant, cust, v, "30", price="200", cost="100")
    row, _t = _row(api, customer_type="Salon")
    assert (D(row["net_sales"]), D(row["company_share"]), D(row["gross_margin"])) == (D("1400"), D("540"), D("940"))
    assert _books(tenant) == D("1940")


def test_a_row_of_returns_alone_has_no_margin_rate(api, tenant, shop):
    """Goods sold last month and sent back in this one: -76 of margin on -760 of sales read as 10%."""
    from apps.sales.models import SalesInvoice

    _sup, v, cust = shop
    inv = _invoice(tenant, cust, v, "5")
    with tenant_context(tenant, tenant.owner):
        SalesInvoice.objects.filter(pk=inv.pk).update(date=timezone.localdate() - timezone.timedelta(days=40))
    _return(tenant, inv, "10")
    on = timezone.localdate().isoformat()
    row, t = _row(api, date_from=on, date_to=on)
    assert D(row["net_sales"]) == D("-950") and D(row["company_share"]) == D("-20") and D(row["gross_margin"]) == D("-370")
    assert row["margin_pct"] == "" and "margin_pct" not in t
    for fmt in ("csv", "xlsx", "pdf"):
        assert api.get("/api/v1/reports/sales_by_customer", {"date_from": on, "date_to": on, "format": fmt}).status_code in (200, 403)


def test_less_than_a_paisa_left_over_is_nothing(api, tenant, shop):
    """Rafi Mart: 14,931.90 sold, 14,931.904 returned — "-0.00" at a 100.00% margin. The total keeps to the books."""
    from apps.sales.models import CreditNote, CreditNoteLine

    _sup, v, cust = shop
    inv = _invoice(tenant, cust, v, "0", qty="1", price="149.90", cost="100")
    with tenant_context(tenant, tenant.owner):
        cn = CreditNote.objects.create(number="CN-M2", customer=cust, invoice=inv, date=timezone.localdate(), reason="back", status="posted", total=D("149.9040"))
        CreditNoteLine.objects.create(credit_note=cn, variant=v, qty=D("1"), unit_price=D("149.904"), unit_cost=D("100"), line_total=D("149.9040"))
    row, t = _row(api)
    assert (row["net_sales"], row["cogs"], row["gross_margin"], row["margin_pct"]) == ("0", "0", "0", "")
    assert D(t["net_sales"]) == D("-0.0040") == D(t["gross_margin"])


def test_customers_of_the_same_name_carry_their_code(api, tenant, shop):
    from apps.partners.models import Customer

    _sup, v, cust = shop
    with tenant_context(tenant, tenant.owner):
        other = Customer.objects.create(code="C-TWIN", name=f" {cust.name.upper()} ", credit_limit=D("100000"))
        alone = Customer.objects.create(code="C-ALONE", name="Only One Of These", credit_limit=D("100000"))
    _invoice(tenant, cust, v, "0")
    _invoice(tenant, alone, v, "0")
    labels = {r["key"]: r["label"] for r in api.get("/api/v1/reports/sales_by_customer").data["rows"]}
    assert labels[str(cust.pk)] == f"{cust.name} ({cust.code})", "its twin sold nothing here, and the name is still ambiguous"
    assert labels[str(alone.pk)] == "Only One Of These" and str(other.pk) not in labels
