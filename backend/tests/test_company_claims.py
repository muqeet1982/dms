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


def test_claims_deducted_when_paying_the_supplier(tenant):
    """Paying the manufacturer for purchases can deduct open claims: the bill settles for cash + deduction,
    the claims receivable is cleared, and cancelling the payment restores both."""
    from apps.accounting.services import PaymentService
    from apps.core.tenancy import tenant_context
    from apps.purchasing.models import PurchaseBill
    from apps.sales.models import CompanyClaim
    from .conftest import seed_basic

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        inv = _posted_invoice(tenant, cust, v, pct="5")  # claim of 20 against the supplier
        claim = CompanyClaim.objects.get(invoice=inv)
        bill = PurchaseBill.objects.create(number="PB-T-1", supplier=sup, subtotal=Decimal("100"), total=Decimal("100"), status="posted")

        pay = PaymentService.record(
            direction="out", partner_type="supplier", partner_id=str(sup.id), amount=Decimal("80"), method="cash",
            allocations=[{"document_type": "purchase_bill", "document_id": str(bill.pk), "amount": Decimal("100")}],
            claim_deductions=[{"claim_id": str(claim.pk), "amount": Decimal("20")}],
        )
        bill.refresh_from_db(); claim.refresh_from_db()
        assert bill.status == "paid" and bill.paid_amount == Decimal("100"), (bill.status, bill.paid_amount)
        assert claim.status == "paid" and claim.paid_amount == Decimal("20")
        # GL: AP debited for the full 100 (80 cash + 20 claims), receivable credited 20 by the payment
        b = _role_bal("manufacturer_claim")
        assert b == (Decimal("20"), Decimal("20")), b  # claim raised 20, cleared 20
        assert _role_bal("ap")[0] == Decimal("100"), _role_bal("ap")

        # cancelling the payment restores the bill and the claim, and reverses the journal
        PaymentService.cancel(pay, reason="test")
        bill.refresh_from_db(); claim.refresh_from_db()
        assert bill.status == "posted" and bill.paid_amount == Decimal("0")
        assert claim.status == "posted" and claim.paid_amount == Decimal("0")


def test_claims_cannot_be_deducted_on_a_cheque(tenant):
    from apps.accounting.services import PaymentService
    from apps.core.exceptions import DomainError
    from apps.core.tenancy import tenant_context
    from apps.sales.models import CompanyClaim
    from .conftest import seed_basic

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        inv = _posted_invoice(tenant, cust, v, pct="5")
        claim = CompanyClaim.objects.get(invoice=inv)
        with pytest.raises(DomainError):
            PaymentService.record(direction="out", partner_type="supplier", partner_id=str(sup.id), amount=Decimal("10"), method="cheque", cheque_no="123", claim_deductions=[{"claim_id": str(claim.pk), "amount": Decimal("5")}])


def test_product_discount_applies_and_stacks_but_is_not_cofunded(tenant):
    """A product's own discount % auto-applies on its lines (stacking with the customer %), is editable per
    line, and never inflates the manufacturer claim — only the co-funded % is claimed."""
    from apps.core.tenancy import tenant_context
    from apps.sales.models import CompanyClaim
    from apps.sales.services import OrderService
    from .conftest import seed_basic

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        v.product.discount_pct = Decimal("10")
        v.product.save(update_fields=["discount_pct"])
        cust.default_discount_pct = Decimal("5")
        cust.save(update_fields=["default_discount_pct"])
        order = OrderService.create(customer=cust, lines=[{"variant": v, "qty": Decimal("10"), "unit_price": None}], source="staff")
        line = order.lines.get()
        assert line.product_discount_pct == Decimal("10") and line.discount_pct == Decimal("5")
        gross = line.qty_ordered * line.unit_price
        assert line.discount_amount == (gross * Decimal("15") / 100).quantize(Decimal("0.0001")), line.discount_amount
        # the claim uses the co-funded 5% row (company 2 points of the sale) — the product's 10% is not claimed
        inv = _posted_invoice(tenant, cust, v, pct="5")
        claim = CompanyClaim.objects.get(invoice=inv)
        assert claim.total == Decimal("20.0000")  # 1000 base x 2%, untouched by any product discount
