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


def test_invoice_carries_and_edits_the_product_discount(tenant):
    """The product % rides onto the invoice line and a draft can change it — the amount follows the delta."""
    from apps.core.tenancy import tenant_context
    from apps.sales.models import SalesInvoice, SalesInvoiceLine
    from apps.sales.services import InvoiceService
    from .conftest import seed_basic

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        inv = SalesInvoice.objects.create(number="INV-PD-1", customer=cust, subtotal=Decimal("1000"), discount=Decimal("100"), total=Decimal("900"), status="draft")
        line = SalesInvoiceLine.objects.create(invoice=inv, variant=v, description=v.name, qty=Decimal("10"), unit_price=Decimal("100"), unit_cost=Decimal("60"), discount_amount=Decimal("100"), product_discount_pct=Decimal("10"), line_total=Decimal("900"))
        InvoiceService.update_draft(inv, lines=[{"id": str(line.id), "product_discount_pct": "5"}])
        line.refresh_from_db()
        assert line.product_discount_pct == Decimal("5")
        assert line.discount_amount == Decimal("50.0000"), line.discount_amount  # 100 + 1000 x (5-10)% = 50


def test_the_customer_rate_rides_the_order_and_can_be_changed_at_every_step(api, tenant):
    """
    The owner's rule: the customer's standing discount shows up wherever the sale goes — on the order
    (changeable there, at create and on edit) and on the invoice issued from it (changeable at issue).
    """
    from apps.core.tenancy import tenant_context

    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        from apps.sales.models import DiscountContribution

        cust.default_discount_pct = Decimal("5")
        cust.save(update_fields=["default_discount_pct"])
        for total, dist, co in (("5", "3", "2"), ("7.5", "5", "2.5"), ("10", "6", "4")):
            DiscountContribution.objects.get_or_create(total_pct=Decimal(total), defaults={"distributor_pct": Decimal(dist), "company_pct": Decimal(co)})

    # created with no explicit % → the customer's rate lands on the order and its lines
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "lines": [{"variant": str(v.id), "qty": "10"}]}, format="json")
    assert r.status_code == 201, r.content
    assert Decimal(r.data["discount_pct"]) == Decimal("5")
    assert Decimal(r.data["lines"][0]["discount_pct"]) == Decimal("5")

    # changed on edit → the lines follow
    r2 = api.patch(f"/api/v1/sales-orders/{r.data['id']}", {"discount_pct": "7.5", "lines": [{"variant": str(v.id), "qty": "10"}]}, format="json")
    assert r2.status_code == 200, r2.content
    assert Decimal(r2.data["discount_pct"]) == Decimal("7.5")
    assert Decimal(r2.data["lines"][0]["discount_pct"]) == Decimal("7.5")

    # issue the goods, then invoice with a different % chosen on the create page
    assert api.post(f"/api/v1/sales-orders/{r.data['id']}/confirm", {}, format="json").status_code == 200
    pl = api.post(f"/api/v1/sales-orders/{r.data['id']}/load-form", {}, format="json")
    assert pl.status_code == 201, pl.content
    issues = [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl.data["lines"]]
    assert api.post(f"/api/v1/pick-lists/{pl.data['id']}/issue", {"issues": issues}, format="json").status_code == 200
    made = api.post(f"/api/v1/sales-orders/{r.data['id']}/invoice", {"discount_pct": "10"}, format="json")
    assert made.status_code in (200, 201), made.content
    inv = made.data[0] if isinstance(made.data, list) else made.data
    assert Decimal(inv["discount_pct"]) == Decimal("10")
    assert inv["status"] == "posted"
    assert Decimal(inv["discount"]) == (Decimal(inv["subtotal"]) * Decimal("10") / 100).quantize(Decimal(inv["discount"]))


def test_a_wrong_invoice_is_cancelled_and_its_delivery_billed_again_with_the_discount(api, tenant):
    """
    The DEPILEX case (19 Oct 2026): an office order made with the old form never applied the customer's
    standing %, and the posted invoice carried the % in its header with zero in the amounts. The repair
    path must work: cancel the invoice (which releases its delivery note — a OneToOne that would otherwise
    block rebilling forever) and issue it again with the % chosen at issue time.
    """
    from apps.core.tenancy import tenant_context

    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        cust.default_discount_pct = Decimal("10")
        cust.save(update_fields=["default_discount_pct"])

    # the old form's mistake, reproduced: an order that switched the customer's rate off
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "apply_customer_discount": False, "discount_pct": "0", "lines": [{"variant": str(v.id), "qty": "10"}]}, format="json")
    assert r.status_code == 201, r.content
    assert api.post(f"/api/v1/sales-orders/{r.data['id']}/confirm", {}, format="json").status_code == 200
    pl = api.post(f"/api/v1/sales-orders/{r.data['id']}/load-form", {}, format="json")
    issues = [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl.data["lines"]]
    assert api.post(f"/api/v1/pick-lists/{pl.data['id']}/issue", {"issues": issues}, format="json").status_code == 200
    made = api.post(f"/api/v1/sales-orders/{r.data['id']}/invoice", {}, format="json")
    first = (made.data[0] if isinstance(made.data, list) else made.data)
    assert Decimal(first["discount"]) == 0, "the wrong invoice: no discount in the amounts"

    # the repair: cancel, then issue again with the rate the customer should have had
    assert api.post(f"/api/v1/invoices/{first['id']}/cancel", {"reason": "reissue with discount"}, format="json").status_code == 200
    redo = api.post(f"/api/v1/sales-orders/{r.data['id']}/invoice", {"discount_pct": "10"}, format="json")
    assert redo.status_code in (200, 201), redo.content
    second = (redo.data[0] if isinstance(redo.data, list) else redo.data)
    assert second["id"] != first["id"] and second["status"] == "posted"
    assert Decimal(second["discount_pct"]) == Decimal("10")
    assert Decimal(second["discount"]) == (Decimal(second["subtotal"]) * Decimal("10") / 100).quantize(Decimal(second["discount"]))


def test_discounts_allowed_carries_only_the_distributors_share(tenant):
    """
    The owner's rule (21 Oct 2026): "Discounts Allowed is Trafco's head — only its own points land there."
    A 5% invoice (distributor 3 + company 2) debits Discounts Allowed 3% of the sale; the company's 2%
    debits the Distribution Margin head, which the claim journal credits straight back — so that head nets
    zero, the receivable carries the manufacturer's debt, and netting it off minuses the trade payable.
    """
    from apps.core.tenancy import tenant_context

    from .conftest import seed_basic

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        _posted_invoice(tenant, cust, v, pct="5")   # base 1000, discount 50 = own 30 + funded 20
        disc_d, disc_c = _role_bal_doc("discount", "sales_invoice")
        assert disc_d == Decimal("30"), (disc_d, disc_c)
        inc_d, inc_c = _role_bal("manufacturer_incentive")
        assert (inc_d, inc_c) == (Decimal("20"), Decimal("20")), "funded share in and claimed out — nets zero"
        assert _role_bal("manufacturer_claim")[0] == Decimal("20")


def _role_bal_doc(role, doc_type):
    from apps.accounting.models import JournalLine
    from apps.accounting.services import account_for

    acct = account_for(role, doc_type)
    agg = JournalLine.objects.filter(account=acct, entry__status="posted").aggregate(d=Sum("debit"), c=Sum("credit"))
    return (agg["d"] or Decimal("0"), agg["c"] or Decimal("0"))
