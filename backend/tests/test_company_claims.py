"""
Co-funded discount: when an invoice carries a total discount, the manufacturer's share is raised as a
CompanyClaim per manufacturer, posted to a receivable, and settled either by a receipt from the company or by
netting against the supplier's payable.
"""
import pytest
from decimal import Decimal
from django.db.models import Sum

pytestmark = pytest.mark.django_db


def _role_bal(role):
    from apps.accounting.models import JournalLine
    from apps.accounting.services import account_for

    acct = account_for(role, "company_claim")
    agg = JournalLine.objects.filter(account=acct, entry__status="posted").aggregate(d=Sum("debit"), c=Sum("credit"))
    return (agg["d"] or Decimal("0"), agg["c"] or Decimal("0"))


def _posted_invoice(tenant, cust, v, *, pct="5", qty="10", price="100"):
    """A posted invoice with a flat total discount percent on its single line."""
    from decimal import Decimal as D

    from apps.sales.models import SalesInvoice, SalesInvoiceLine
    from apps.sales.services import InvoiceService

    gross = D(qty) * D(price)
    disc = (gross * D(pct) / 100)
    inv = SalesInvoice.objects.create(number=f"INV-T-{pct}", customer=cust, discount_pct=D(pct), subtotal=gross, discount=disc, total=gross - disc, status="draft")
    SalesInvoiceLine.objects.create(invoice=inv, variant=v, description=v.name, qty=D(qty), unit_price=D(price), unit_cost=D("60"), discount_amount=disc, line_total=gross - disc)
    return InvoiceService.post(inv)


def test_claim_is_raised_for_the_manufacturer_share(tenant):
    from apps.core.tenancy import tenant_context
    from apps.sales.models import CompanyClaim
    from .conftest import seed_basic

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        inv = _posted_invoice(tenant, cust, v, pct="5")  # 5% = distributor 3% + company 2%
        claims = list(CompanyClaim.objects.filter(invoice=inv))
        assert len(claims) == 1, claims
        claim = claims[0]
        # discount given = 50; company funds 2/5 of it = 20
        assert claim.supplier_id == sup.id
        assert claim.total == Decimal("20.0000"), claim.total
        assert claim.company_pct == Decimal("2.000")
        assert claim.lines.count() == 1
        # GL: Dr Manufacturer Claims Receivable 20, Cr incentive income 20
        assert _role_bal("manufacturer_claim")[0] == Decimal("20"), _role_bal("manufacturer_claim")
        assert _role_bal("manufacturer_incentive")[1] == Decimal("20"), _role_bal("manufacturer_incentive")


def test_no_claim_without_a_matching_table_row(tenant):
    from apps.core.tenancy import tenant_context
    from apps.sales.models import CompanyClaim
    from .conftest import seed_basic

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        inv = _posted_invoice(tenant, cust, v, pct="2")  # no 2% row in the table
        assert not CompanyClaim.objects.filter(invoice=inv).exists()


def test_settle_by_company_receipt(tenant):
    from apps.core.tenancy import tenant_context
    from apps.sales.models import CompanyClaim
    from apps.sales.services import CompanyClaimService
    from .conftest import seed_basic

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        inv = _posted_invoice(tenant, cust, v, pct="5")
        claim = CompanyClaim.objects.get(invoice=inv)
        CompanyClaimService.settle_receipt(claim, amount=Decimal("12"), method="cash")
        claim.refresh_from_db()
        assert claim.paid_amount == Decimal("12") and claim.status == "partially_paid"
        CompanyClaimService.settle_receipt(claim, method="cash")  # pay the rest
        claim.refresh_from_db()
        assert claim.status == "paid" and claim.balance == Decimal("0")
        # receipts cleared the receivable: 20 debit from the claim, 20 credit from the two receipts
        assert _role_bal("manufacturer_claim") == (Decimal("20"), Decimal("20"))


def test_settle_by_netting_against_purchases(tenant):
    from apps.core.tenancy import tenant_context
    from apps.sales.models import CompanyClaim
    from apps.sales.services import CompanyClaimService
    from .conftest import seed_basic

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        inv = _posted_invoice(tenant, cust, v, pct="6")  # 6% = 3% + 3% → company 3/6 of 60 = 30
        claim = CompanyClaim.objects.get(invoice=inv)
        assert claim.total == Decimal("30.0000")
        CompanyClaimService.net_off(claim)
        claim.refresh_from_db()
        assert claim.status == "paid" and claim.balance == Decimal("0")
        # netting debits AP (reduces payable) and credits the receivable
        assert _role_bal("manufacturer_claim") == (Decimal("30"), Decimal("30"))
        assert _role_bal("ap")[0] == Decimal("30"), _role_bal("ap")
