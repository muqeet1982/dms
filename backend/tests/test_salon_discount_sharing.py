"""
Salon discount sharing (owner, 8 Nov 2026). A salon buys at the R.P and its discount is off the R.P. Still at or above
the T.P after the discount, the distributor bears it all. Below the T.P, the salon bracket its discount % falls in says
what the company (the product's manufacturer) bears: a share of the part below the T.P, or of the whole discount (a
setting). The company's share is claimed exactly like a discount-table share, the split is stored on every invoice line,
and a return brings back only what the company funded.

The world: T.P 150, R.P 200, so a salon is at the T.P with 25% off.
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


def _brackets(tenant, *rows):
    from apps.sales.models import SalonDiscountBracket

    with tenant_context(tenant, tenant.owner):
        for up_to, company in rows:
            SalonDiscountBracket.objects.create(up_to_pct=D(up_to), company_pct=D(company))


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
    _brackets(tenant, ("30", "50"))
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


def test_below_the_tp_the_company_bears_its_bracket_of_the_part_below(tenant, salon):
    sup, v, cust = salon
    _brackets(tenant, ("30", "50"), ("40", "60"))
    # 30% off 2,000 = 600; down to the T.P (25%) is 500, the distributor's; 100 is below it, half of it the company's
    inv = _invoice(tenant, cust, v, "30")
    line = _line(tenant, inv)
    assert (line.discount_rule, line.share_pct, line.company_discount) == ("bracket", D("50"), D("50"))
    with tenant_context(tenant):
        claim = inv.company_claims.get()
        assert claim.supplier_id == sup.id and claim.total == D("50") and claim.company_pct == D("2.5")
    assert _gl(tenant, "discount", inv) == D("550") and _gl(tenant, "manufacturer_incentive", inv) == D("50")
    # 35% falls in the bracket up to 40%: 700 off, 200 below the T.P, 60% of it = 120
    line = _line(tenant, _invoice(tenant, cust, v, "35"))
    assert (line.share_pct, line.company_discount) == (D("60"), D("120"))


def test_the_whole_discount_when_the_setting_says_so(api, tenant, salon):
    sup, v, cust = salon
    _brackets(tenant, ("30", "50"))
    assert api.get("/api/v1/settings/salon-discount-sharing/basis").data == {"basis": "below_tp"}
    r = api.post("/api/v1/settings/salon-discount-sharing/basis", {"basis": "whole"}, format="json")
    assert r.status_code == 200 and r.data == {"basis": "whole"}
    assert api.post("/api/v1/settings/salon-discount-sharing/basis", {"basis": "half"}, format="json").status_code == 400
    tenant.refresh_from_db()
    line = _line(tenant, _invoice(tenant, cust, v, "30"))
    assert line.company_discount == D("300"), "half of the whole 600"
    # still nothing when the salon is at or above the T.P
    assert _line(tenant, _invoice(tenant, cust, v, "20")).company_discount == 0


def test_a_discount_with_no_bracket_is_the_distributors(tenant, salon):
    sup, v, cust = salon
    inv = _invoice(tenant, cust, v, "30")          # no brackets yet
    assert _line(tenant, inv).discount_rule == "no_bracket"
    _brackets(tenant, ("30", "50"))
    inv = _invoice(tenant, cust, v, "45")          # above every bracket
    assert (_line(tenant, inv).discount_rule, _line(tenant, inv).company_discount) == ("no_bracket", D("0"))
    with tenant_context(tenant):
        assert not inv.company_claims.exists()


def test_a_tax_invoice_compares_the_price_with_its_tax(tenant, salon):
    """Sold at R.P 200 including the tax, 160 before it: the T.P of 150 includes the tax too."""
    sup, v, cust = salon
    _brackets(tenant, ("30", "50"))
    # 30% off: 140 with the tax, below 150. Before tax: 1,600 less 480; down to the T.P is 400, 80 below, half = 40
    line = _line(tenant, _invoice(tenant, cust, v, "30", price="160", tp="200"))
    assert (line.discount_rule, line.company_discount) == ("bracket", D("40"))
    assert _line(tenant, _invoice(tenant, cust, v, "20", price="160", tp="200")).discount_rule == "above_tp"


def test_anyone_else_keeps_the_discount_table(tenant, salon):
    sup, v, cust = salon
    with tenant_context(tenant, tenant.owner):
        cust.type = "retailer"
        cust.save(update_fields=["type"])
    _brackets(tenant, ("30", "50"))
    line = _line(tenant, _invoice(tenant, cust, v, "5", price="150"))
    assert (line.discount_rule, line.share_pct, line.company_discount) == ("table", D("2"), D("30")), "2% of 1,500"


def test_a_return_brings_back_only_what_the_company_funded(tenant, salon):
    from apps.sales.services import CreditNoteService

    sup, v, cust = salon
    _brackets(tenant, ("30", "50"))
    inv = _invoice(tenant, cust, v, "30")           # the company funded 50 on 10 units: 5 a unit
    with tenant_context(tenant, tenant.owner):
        cn = CreditNoteService.create(customer=cust, invoice=inv, reason="salon return",
                                      lines=[{"variant": v, "qty": "2", "unit_price": "200", "unit_cost": "100", "condition": "good", "restock": True}])
        CreditNoteService.post(cn)
        assert inv.company_claims.get().total == D("40")


def test_the_report_shows_who_bore_each_discount(api, tenant, salon):
    sup, v, cust = salon
    _brackets(tenant, ("30", "50"))
    _invoice(tenant, cust, v, "10")
    _invoice(tenant, cust, v, "30")
    data = api.get("/api/v1/reports/discount_sharing", {"customer_type": "Salon"}).data
    by_pct = {Decimal(r["discount_pct"]): r for r in data["rows"]}
    above, below = by_pct[D("10")], by_pct[D("30")]
    assert above["rule"] == "Salon: At Or Above T.P" and D(above["company_share"]) == 0 and D(above["distributor_share"]) == D("200")
    assert below["rule"] == "Salon Bracket (Company Bears 50%)" and D(below["company_share"]) == D("50") and D(below["distributor_share"]) == D("550")
    assert D(below["list_price"]) == 200 and D(below["trade_price"]) == 150 and D(below["price_after"]) == 140
    assert below["company"] == "Maker" and below["customer_type"] == "Salon"
    t = data["totals"]
    assert D(t["discount"]) == D("800") and D(t["company_share"]) == D("50") and D(t["distributor_share"]) == D("750")
    assert api.get("/api/v1/reports/discount_sharing", {"customer_type": "Retailer"}).data["rows"] == []


def test_a_bracket_percentage_is_offered_as_a_discount(api, tenant):
    r = api.post("/api/v1/settings/salon-discount-sharing", {"up_to_pct": "20", "company_pct": "40"}, format="json")
    assert r.status_code == 201 and r.data["distributor_pct"] == "60.000"
    assert api.post("/api/v1/settings/salon-discount-sharing", {"up_to_pct": "20", "company_pct": "10"}, format="json").status_code == 400, "one bracket per %"
    assert api.post("/api/v1/settings/salon-discount-sharing", {"up_to_pct": "25", "company_pct": "120"}, format="json").status_code == 400
    rates = api.get("/api/v1/settings/discount-contributions/rates").data
    pcts = [D(x["total_pct"]) for x in rates]
    assert D("20") in pcts and D("5") in pcts and pcts == sorted(pcts), "the discount table's %s stay, in order"
    assert all(set(x) == {"id", "total_pct"} for x in rates), "totals only"
