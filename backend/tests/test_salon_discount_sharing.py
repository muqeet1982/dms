"""
Salon discount sharing. A salon buys at the R.P and its discount is off the R.P (owner, 8 Nov 2026). Still at or above the
T.P after the discount, the distributor bears it all. Below the T.P (owner, 14 Nov 2026) the distributor bears a fixed
3% of the R.P, whatever the discount, and the company (the product's manufacturer) the rest. The company's share is
claimed exactly like a discount-table share, the split is stored on every invoice line, and a return brings back only
what the company funded.

The world: T.P 150, R.P 200, so a salon is at the T.P with 25% off; and the owner's own, R.P 650 and T.P 550.
"""
from decimal import Decimal

import pytest
from django.db.models import Sum

from apps.core.tenancy import tenant_context

from .conftest import seed_basic

pytestmark = pytest.mark.django_db
D = Decimal


@pytest.fixture
def salon(tenant):
    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        v.retail_price = D("200")
        v.save(update_fields=["retail_price"])
        cust.type = "salon"
        cust.save(update_fields=["type"])
    return sup, v, cust


def _prices(tenant, v, rp, tp):
    with tenant_context(tenant, tenant.owner):
        v.retail_price, v.sale_price = D(rp), D(tp)
        v.save(update_fields=["retail_price", "sale_price"])


def _invoice(tenant, cust, v, pct, *, qty="10", price="200", tp=None):
    """A posted invoice: `pct` % off `price` on one line (tp: the tax-included price the line was sold at)."""
    from apps.sales.models import SalesInvoice, SalesInvoiceLine
    from apps.sales.services import InvoiceService

    with tenant_context(tenant, tenant.owner):
        gross = D(qty) * D(price)
        disc = gross * D(pct) / 100
        inv = SalesInvoice.objects.create(number=f"INV-S-{pct}-{SalesInvoice.objects.count()}", customer=cust, discount_pct=D(pct), subtotal=gross, discount=disc, total=gross - disc, status="draft")
        SalesInvoiceLine.objects.create(invoice=inv, variant=v, description=v.name, qty=D(qty), unit_price=D(price), unit_cost=D("100"), discount_amount=disc, line_total=gross - disc, tp=D(tp) if tp else None)
        return InvoiceService.post(inv)


def _gl(tenant, role, inv):
    from apps.accounting.models import JournalLine
    from apps.accounting.services import account_for

    with tenant_context(tenant):
        doc = "company_claim" if role == "manufacturer_incentive" else "sales_invoice"
        agg = JournalLine.objects.filter(account=account_for(role, doc), entry__status="posted", entry__memo__contains=inv.number).aggregate(d=Sum("debit"))
        return agg["d"] or D("0")


def _line(tenant, inv):
    with tenant_context(tenant):
        return inv.lines.get()


def test_at_or_above_the_tp_the_distributor_bears_it_all(tenant, salon):
    sup, v, cust = salon
    for pct in ("10", "25"):                       # 180 and 150: above, and exactly at, the T.P of 150
        inv = _invoice(tenant, cust, v, pct)
        line = _line(tenant, inv)
        assert (line.discount_rule, line.company_discount) == ("above_tp", D("0")), pct
        with tenant_context(tenant):
            assert not inv.company_claims.exists()
        assert _gl(tenant, "discount", inv) == line.discount_amount and _gl(tenant, "manufacturer_incentive", inv) == 0


def test_the_discount_table_never_splits_a_salon_above_the_tp(tenant, salon):
    """The table's 5% = 3 + 2 is for the T.P trade: a salon 5% off the R.P is still well above the T.P."""
    from apps.sales.models import DiscountContribution

    sup, v, cust = salon
    with tenant_context(tenant):
        assert DiscountContribution.objects.filter(total_pct=5, company_pct=2).exists()
    inv = _invoice(tenant, cust, v, "5")
    assert _line(tenant, inv).discount_rule == "above_tp"
    with tenant_context(tenant):
        assert not inv.company_claims.exists()


def test_the_owners_example_rp_650_tp_550(tenant, salon):
    """Owner, 14 Nov 2026: 10% → the distributor bears all 65; 20% (130) → distributor 19.50 (3%), company 110.50 (17%);
    25% (162.50) → distributor 19.50, company 143 (22%). One piece each."""
    sup, v, cust = salon
    _prices(tenant, v, "650", "550")
    expect = {"10": ("above_tp", "0", "0", "65"), "20": ("below_tp", "110.50", "17", "19.50"), "25": ("below_tp", "143", "22", "19.50"),
              "30": ("below_tp", "175.50", "27", "19.50")}
    for pct, (rule, company, share, ours) in expect.items():
        inv = _invoice(tenant, cust, v, pct, qty="1", price="650")
        line = _line(tenant, inv)
        assert (line.discount_rule, line.company_discount, line.share_pct) == (rule, D(company), D(share)), pct
        assert line.discount_amount - line.company_discount == D(ours), pct
        assert _gl(tenant, "discount", inv) == D(ours) and _gl(tenant, "manufacturer_incentive", inv) == D(company), pct
        with tenant_context(tenant):
            assert sum((c.total for c in inv.company_claims.all()), D("0")) == D(company), pct


def test_below_the_tp_the_distributor_bears_three_percent_of_the_rp(tenant, salon):
    sup, v, cust = salon
    # 30% off 2,000 = 600: the distributor 3% of 2,000 = 60, the company 540 (27 points of the R.P)
    inv = _invoice(tenant, cust, v, "30")
    line = _line(tenant, inv)
    assert (line.discount_rule, line.share_pct, line.company_discount) == ("below_tp", D("27"), D("540"))
    with tenant_context(tenant):
        claim = inv.company_claims.get()
        assert claim.supplier_id == sup.id and claim.total == D("540")
    assert _gl(tenant, "discount", inv) == D("60") and _gl(tenant, "manufacturer_incentive", inv) == D("540")
    # 35%: still 60 for the distributor, 640 for the company
    line = _line(tenant, _invoice(tenant, cust, v, "35"))
    assert (line.share_pct, line.company_discount) == (D("32"), D("640"))


def test_the_distributors_share_is_a_setting(api, tenant, salon):
    sup, v, cust = salon
    assert api.get("/api/v1/settings/salon-discount-sharing/share").data == {"distributor_pct": "3"}
    r = api.post("/api/v1/settings/salon-discount-sharing/share", {"distributor_pct": "5"}, format="json")
    assert r.status_code == 200 and r.data == {"distributor_pct": "5"}
    for bad in ("150", "-1", "x"):
        assert api.post("/api/v1/settings/salon-discount-sharing/share", {"distributor_pct": bad}, format="json").status_code == 400, bad
    tenant.refresh_from_db()
    line = _line(tenant, _invoice(tenant, cust, v, "30"))
    assert line.company_discount == D("500"), "5% of 2,000 = 100 is the distributor's"
    assert _line(tenant, _invoice(tenant, cust, v, "20")).company_discount == 0, "still nothing at or above the T.P"


def test_the_distributor_never_bears_more_than_the_discount(tenant, salon):
    """A T.P just under the R.P: 2% off goes below it, and 3% of the R.P would be more than the discount."""
    sup, v, cust = salon
    _prices(tenant, v, "200", "199")
    line = _line(tenant, _invoice(tenant, cust, v, "2"))
    assert (line.discount_rule, line.company_discount) == ("below_tp", D("0"))


def test_a_tax_invoice_compares_the_price_with_its_tax(tenant, salon):
    """Sold at R.P 200 including the tax, 160 before it: the T.P of 150 includes the tax too."""
    sup, v, cust = salon
    # 30% off: 140 with the tax, below 150. Before tax: 1,600 less 480; the distributor 3% of 1,600 = 48, the company 432
    line = _line(tenant, _invoice(tenant, cust, v, "30", price="160", tp="200"))
    assert (line.discount_rule, line.company_discount) == ("below_tp", D("432"))
    assert _line(tenant, _invoice(tenant, cust, v, "20", price="160", tp="200")).discount_rule == "above_tp"


def test_anyone_else_keeps_the_discount_table(tenant, salon):
    sup, v, cust = salon
    with tenant_context(tenant, tenant.owner):
        cust.type = "retailer"
        cust.save(update_fields=["type"])
    line = _line(tenant, _invoice(tenant, cust, v, "5", price="150"))
    assert (line.discount_rule, line.share_pct, line.company_discount) == ("table", D("2"), D("30")), "2% of 1,500"


def test_a_return_brings_back_only_what_the_company_funded(tenant, salon):
    from apps.sales.services import CreditNoteService

    sup, v, cust = salon
    inv = _invoice(tenant, cust, v, "30")           # the company funded 540 on 10 units: 54 a unit
    with tenant_context(tenant, tenant.owner):
        cn = CreditNoteService.create(customer=cust, invoice=inv, reason="salon return",
                                      lines=[{"variant": v, "qty": "2", "unit_price": "200", "unit_cost": "100", "condition": "good", "restock": True}])
        CreditNoteService.post(cn)
        assert inv.company_claims.get().total == D("432")


def test_the_report_shows_who_bore_each_discount(api, tenant, salon):
    sup, v, cust = salon
    _invoice(tenant, cust, v, "10")
    _invoice(tenant, cust, v, "30")
    data = api.get("/api/v1/reports/discount_sharing", {"customer_type": "Salon"}).data
    by_pct = {Decimal(r["discount_pct"]): r for r in data["rows"]}
    above, below = by_pct[D("10")], by_pct[D("30")]
    assert above["rule"] == "Salon: At Or Above T.P" and D(above["company_share"]) == 0 and D(above["distributor_share"]) == D("200")
    assert below["rule"] == "Salon: Below T.P (Company 27% Of R.P)" and D(below["company_share"]) == D("540") and D(below["distributor_share"]) == D("60")
    assert D(below["list_price"]) == 200 and D(below["trade_price"]) == 150 and D(below["price_after"]) == 140
    assert below["company"] == "Maker" and below["customer_type"] == "Salon"
    t = data["totals"]
    assert D(t["discount"]) == D("800") and D(t["company_share"]) == D("540") and D(t["distributor_share"]) == D("260")
    assert api.get("/api/v1/reports/discount_sharing", {"customer_type": "Retailer"}).data["rows"] == []


def test_a_salon_discount_is_offered_in_the_dropdown(api, tenant):
    r = api.post("/api/v1/settings/salon-discount-sharing", {"up_to_pct": "20"}, format="json")
    assert r.status_code == 201 and set(r.data) == {"id", "up_to_pct", "is_active"}, r.content
    assert api.post("/api/v1/settings/salon-discount-sharing", {"up_to_pct": "20"}, format="json").status_code == 400, "one row per %"
    assert api.post("/api/v1/settings/salon-discount-sharing", {"up_to_pct": "120"}, format="json").status_code == 400
    rates = api.get("/api/v1/settings/discount-contributions/rates").data
    pcts = [D(x["total_pct"]) for x in rates]
    assert D("20") in pcts and D("5") in pcts and pcts == sorted(pcts), "the discount table's %s stay, in order"
    assert all(set(x) == {"id", "total_pct"} for x in rates), "totals only"
