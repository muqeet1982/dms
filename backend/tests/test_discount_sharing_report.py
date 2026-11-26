"""
"Discount sharing: distributor and company" (owner, 25 Nov 2026: "this report is not correct as well. double check and fix"):
a tax invoice's rows did not add up (the list price had the tax in it, the discount did not), goods that came back kept
their discount, a cancelled claim still read as the company's, and the same rule was worded two ways. The totals are now
Discounts Allowed and the company's claims, returns off.
"""
from decimal import Decimal as D

import pytest
from django.db.models import Sum
from django.utils import timezone

from apps.core.tenancy import tenant_context

from .conftest import seed_basic

pytestmark = pytest.mark.django_db

COLUMNS = ["date", "invoice", "customer", "label", "qty", "list_price", "trade_price", "discount_pct", "price_after", "discount", "distributor_share", "company_share", "rule", "company"]


def _invoice(tenant, cust, v, pct, *, qty="10", price="100", cost="60", tp=None):
    from apps.sales.models import SalesInvoice, SalesInvoiceLine
    from apps.sales.services import InvoiceService

    with tenant_context(tenant, tenant.owner):
        gross = D(qty) * D(price)
        disc = gross * D(pct) / 100
        inv = SalesInvoice.objects.create(number=f"INV-D-{SalesInvoice.objects.count()}", customer=cust, discount_pct=D(pct), subtotal=gross, discount=disc, total=gross - disc, status="draft")
        SalesInvoiceLine.objects.create(invoice=inv, variant=v, description=v.name, qty=D(qty), unit_price=D(price), unit_cost=D(cost), discount_amount=disc, line_total=gross - disc, tp=D(tp) if tp else None)
        return InvoiceService.post(inv)


def _return(tenant, inv, qty):
    from apps.sales.services import CreditNoteService

    with tenant_context(tenant, tenant.owner):
        inv.refresh_from_db()
        line = inv.lines.first()
        return CreditNoteService.post(CreditNoteService.create(customer=inv.customer, invoice=inv, reason="back", lines=[
            {"variant": line.variant, "qty": qty, "unit_price": str(line.unit_price), "unit_cost": str(line.unit_cost), "condition": "damaged", "restock": False}]))


def _books(tenant):
    """(Discounts Allowed, what the company's claims came to) as posted, returns off."""
    from apps.accounting.models import GL_LIVE, JournalLine
    from apps.accounting.services import account_for

    with tenant_context(tenant):
        ours = JournalLine.objects.filter(entry__status__in=GL_LIVE, account=account_for("discount", "sales_invoice")).aggregate(d=Sum("debit"), c=Sum("credit"))
        theirs = JournalLine.objects.filter(entry__status__in=GL_LIVE, account=account_for("manufacturer_claim", "company_claim")).aggregate(d=Sum("debit"), c=Sum("credit"))
        net = lambda a: (a["d"] or D(0)) - (a["c"] or D(0))  # noqa: E731
        return net(ours), net(theirs)


def _report(api, **params):
    return api.get("/api/v1/reports/discount_sharing", params).data


def _adds_up(row):
    q = D(row["qty"])
    return q * D(row["list_price"]) - D(row["discount"]) == q * D(row["price_after"]) and D(row["distributor_share"]) + D(row["company_share"]) == D(row["discount"])


@pytest.fixture
def shop(tenant):
    _wh, sup, v, cust = seed_basic(tenant)               # T.P 150
    with tenant_context(tenant, tenant.owner):
        tenant.settings = {**(tenant.settings or {}), "claim_settlement": "manual"}
        tenant.save()
    return sup, v, cust


def test_a_row_adds_up_and_the_totals_are_the_books(api, tenant, shop):
    _sup, v, cust = shop
    _invoice(tenant, cust, v, "5", price="150")          # 1500 less 75: 45 ours, 30 the company's
    data = _report(api)
    r = data["rows"][0]
    assert data["columns"] == COLUMNS and data["column_labels"]["invoice"] == "Invoice / Return No"
    assert (D(r["discount"]), D(r["distributor_share"]), D(r["company_share"])) == (D("75"), D("45"), D("30")) and _adds_up(r)
    assert r["rule"] == "Discount Table (Company 2% Of Sale)" and r["tax_note"] == ""
    assert (D(data["totals"]["distributor_share"]), D(data["totals"]["company_share"])) == (D("45"), D("30")) == _books(tenant)


def test_a_tax_invoice_reads_before_tax_all_along_the_row(api, tenant, shop):
    """6 × 1,255 less 3% read 180.72: the price had the 25% in it and the discount did not."""
    _sup, v, cust = shop
    _invoice(tenant, cust, v, "5", qty="6", price="120", tp="150")      # 150 with 25% in it
    r = _report(api)["rows"][0]
    assert (D(r["list_price"]), D(r["trade_price"]), D(r["price_after"]), D(r["discount"])) == (D("120"), D("120"), D("114"), D("36")) and _adds_up(r)
    assert r["tax_note"] == "Tax Invoice, Before Tax"


def test_a_return_is_a_row_of_its_own_in_minus(api, tenant, shop):
    _sup, v, cust = shop
    inv = _invoice(tenant, cust, v, "5")                 # 1000 less 50: 30 ours, 20 the company's
    cn = _return(tenant, inv, "4")
    data = _report(api)
    sale, back = data["rows"]
    assert sale["invoice"] == inv.number and back["invoice"] == cn.number and back["rule"] == f"Return Of {inv.number}"
    assert (D(back["qty"]), D(back["discount"]), D(back["distributor_share"]), D(back["company_share"])) == (D("-4"), D("-20"), D("-12"), D("-8")) and _adds_up(back)
    t = data["totals"]
    assert (D(t["discount"]), D(t["distributor_share"]), D(t["company_share"])) == (D("30"), D("18"), D("12"))
    assert (D("18"), D("12")) == _books(tenant)
    assert api.get("/api/v1/reports/sales_by_customer").data["totals"]["company_share"] == t["company_share"], "the share in the margin is this one"


def test_a_return_of_goods_the_company_did_not_fund(api, tenant, shop):
    _sup, v, cust = shop
    inv = _invoice(tenant, cust, v, "2")                 # no 2% row in the table
    _return(tenant, inv, "4")
    sale, back = _report(api)["rows"]
    assert sale["rule"] == "No Company Share" and back["company_share"] == "0", "never -0.00"
    assert D(back["distributor_share"]) == D("-8")


def test_the_filters_reach_the_returns(api, tenant, shop):
    from apps.partners.models import Customer, Supplier

    sup, v, cust = shop
    inv = _invoice(tenant, cust, v, "5")
    _return(tenant, inv, "4")
    with tenant_context(tenant, tenant.owner):
        other = Customer.objects.create(code="C-OTHER", name="Somebody Else", credit_limit=D("1000"))
        maker = Supplier.objects.create(code="S-OTHER", name="Another Maker")
    assert _report(api, customer=str(other.pk))["rows"] == [] and len(_report(api, customer=str(cust.pk))["rows"]) == 2
    assert _report(api, supplier=str(maker.pk))["rows"] == [] and len(_report(api, supplier=str(sup.pk))["rows"]) == 2
    assert _report(api, customer_type="Salon")["rows"] == [] and len(_report(api, customer_type=cust.get_type_display())["rows"]) == 2
    gone = (timezone.localdate() - timezone.timedelta(days=40)).isoformat()
    assert _report(api, date_from=gone, date_to=gone)["rows"] == []


def test_a_cancelled_claim_is_ours(api, tenant, shop):
    from apps.sales.models import CompanyClaim
    from apps.sales.services import CompanyClaimService

    _sup, v, cust = shop
    inv = _invoice(tenant, cust, v, "5")
    with tenant_context(tenant, tenant.owner):
        CompanyClaimService.cancel(CompanyClaim.objects.get(invoice=inv), reason="not funded")
    r = _report(api)["rows"][0]
    assert r["rule"] == "Claim Cancelled" and (D(r["distributor_share"]), D(r["company_share"])) == (D("50"), D("0"))


def test_an_older_invoice_is_worded_like_the_rest(api, tenant, shop):
    """Until 8 Nov 2026 only the claim said who bore it: those rows read "Discount Table 5%", and the share lost its last decimals."""
    from apps.sales.models import SalesInvoiceLine

    _sup, v, cust = shop
    inv = _invoice(tenant, cust, v, "5", qty="7", price="33.37")
    with tenant_context(tenant, tenant.owner):
        SalesInvoiceLine.objects.filter(invoice=inv).update(company_discount=0, discount_rule="", share_pct=0)
    data = _report(api)
    assert data["rows"][0]["rule"] == "Discount Table (Company 2% Of Sale)"
    assert (D(data["totals"]["distributor_share"]), D(data["totals"]["company_share"])) == _books(tenant)


def test_customers_of_the_same_name_carry_their_code(api, tenant, shop):
    from apps.partners.models import Customer

    _sup, v, cust = shop
    with tenant_context(tenant, tenant.owner):
        Customer.objects.create(code="C-TWIN", name=cust.name, credit_limit=D("1000"))
    _invoice(tenant, cust, v, "5")
    assert _report(api)["rows"][0]["customer"] == f"{cust.name} ({cust.code})"


def test_invoice_wise(api, tenant, shop):
    """Owner, 26 Nov 2026: "also need this report invoice wise" — one row per invoice or return, the same totals."""
    from apps.catalog.models import UnitOfMeasure
    from apps.catalog.services import ProductService
    from apps.sales.models import SalesInvoice, SalesInvoiceLine
    from apps.sales.services import InvoiceService

    _sup, v, cust = shop
    with tenant_context(tenant, tenant.owner):
        p = ProductService.create_product(tenant, {"sku": "DEV-20", "name": "Developer 20 Vol", "base_uom": UnitOfMeasure.objects.get(code="PC"), "cost_price": D("40"), "sale_price": D("50"), "supplier": _sup})
        v2 = p.variants.first()
        inv = SalesInvoice.objects.create(number="INV-D-TWO", customer=cust, discount_pct=D("5"), subtotal=D("1500"), discount=D("75"), total=D("1425"), status="draft")
        SalesInvoiceLine.objects.create(invoice=inv, variant=v, description=v.name, qty=D("10"), unit_price=D("100"), unit_cost=D("60"), discount_amount=D("50"), line_total=D("950"))
        SalesInvoiceLine.objects.create(invoice=inv, variant=v2, description=v2.name, qty=D("10"), unit_price=D("50"), unit_cost=D("40"), discount_amount=D("25"), line_total=D("475"))
        inv = InvoiceService.post(inv)
    _return(tenant, inv, "4")
    items = _report(api)
    data = _report(api, show="Invoice wise")
    assert data["columns"] == ["date", "invoice", "customer", "items", "gross", "discount_pct", "discount", "distributor_share", "company_share", "rule", "company"]
    sale, back = data["rows"]
    assert (sale["invoice"], sale["items"], D(sale["gross"]), sale["discount_pct"], D(sale["discount"]), D(sale["distributor_share"]), D(sale["company_share"])) == \
        ("INV-D-TWO", 2, D("1500"), "5.00", D("75"), D("45"), D("30"))
    assert back["invoice"].startswith(("CN", "ALPH-CN")) or back["rule"].startswith("Return Of"), back
    assert data["totals"] == items["totals"], "the same totals either way"
    assert len(items["rows"]) == 3 and len(data["rows"]) == 2
    spec = {r["key"]: r for r in api.get("/api/v1/reports").data}["discount_sharing"]
    assert {"name": "show", "type": "select", "options": ["Item wise", "Invoice wise"], "default": "Item wise"} in spec["params"]
    for fmt in ("csv", "xlsx", "pdf"):
        assert api.get("/api/v1/reports/discount_sharing", {"show": "Invoice wise", "format": fmt}).status_code == 200
