"""
Manual voucher families (CPV/BPV/JV …) with their own numbering series, cheques on bank voucher lines
(claimed on draft save, released on edit/delete), and the printed voucher — matching the owner's old ERP.
"""
import pytest
from decimal import Decimal

pytestmark = pytest.mark.django_db


def _accounts(tenant):
    from apps.accounting.models import Account, BankAccount

    bank = BankAccount.objects.filter(tenant=tenant).select_related("account").first()
    expense = Account.all_objects.filter(tenant=tenant, type="expense", is_group=False).first()
    return bank, expense


def test_bpv_numbering_and_cheque_lifecycle(api, tenant):
    from apps.accounting.models import ChequeLeaf
    from apps.accounting.services import ChequeBookService
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        bank, expense = _accounts(tenant)
        book = ChequeBookService.create(bank_account=bank, first_no=101, last_no=105)
        leaf = book.leaves.first()

    body = {"date": "2026-10-17", "memo": "Office rent by cheque", "voucher_type": "bpv", "lines": [
        {"account": str(expense.id), "debit": "100", "credit": "0", "description": "Rent"},
        {"account": str(bank.account_id), "debit": "0", "credit": "100", "description": "", "cheque_leaf": str(leaf.id)},
    ]}
    r = api.post("/api/v1/accounting/journals", body, format="json")
    assert r.status_code == 201, r.content
    assert r.data["number"].startswith(f"{tenant.doc_code}-BPV-"), r.data["number"]
    assert r.data["voucher_code"] == "BPV"
    leaf.refresh_from_db()
    assert leaf.status == "issued", "the cheque is claimed when the draft saves"

    # editing the draft without the cheque releases it
    body2 = {**body, "lines": [dict(body["lines"][0]), {**body["lines"][1], "cheque_leaf": None}], "version": r.data["version"]}
    r2 = api.patch(f"/api/v1/accounting/journals/{r.data['id']}", body2, format="json")
    assert r2.status_code == 200, r2.content
    leaf.refresh_from_db()
    assert leaf.status == "available", "a removed cheque goes back to the book"

    # the printed voucher answers as a PDF
    pdf = api.get(f"/api/v1/accounting/journals/{r.data['id']}/pdf")
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"


def test_each_voucher_family_numbers_its_own_series(api, tenant):
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        bank, expense = _accounts(tenant)
    mk = lambda vt: api.post("/api/v1/accounting/journals", {"date": "2026-10-17", "memo": vt, "voucher_type": vt, "lines": [
        {"account": str(expense.id), "debit": "10", "credit": "0"}, {"account": str(bank.account_id), "debit": "0", "credit": "10"}]}, format="json")
    cpv = mk("cpv"); crv = mk("crv"); cpv2 = mk("cpv")
    assert cpv.data["number"].endswith("CPV-00001") and cpv2.data["number"].endswith("CPV-00002"), (cpv.data["number"], cpv2.data["number"])
    assert crv.data["number"].endswith("CRV-00001")


def test_deleting_a_draft_releases_its_cheques(api, tenant):
    from apps.accounting.services import ChequeBookService
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        bank, expense = _accounts(tenant)
        book = ChequeBookService.create(bank_account=bank, first_no=201, last_no=203)
        leaf = book.leaves.first()
    r = api.post("/api/v1/accounting/journals", {"date": "2026-10-17", "memo": "x", "voucher_type": "bpv", "lines": [
        {"account": str(expense.id), "debit": "5", "credit": "0"},
        {"account": str(bank.account_id), "debit": "0", "credit": "5", "cheque_leaf": str(leaf.id)}]}, format="json")
    assert r.status_code == 201, r.content
    assert api.delete(f"/api/v1/accounting/journals/{r.data['id']}").status_code in (200, 204)
    leaf.refresh_from_db()
    assert leaf.status == "available"


def test_an_other_receipt_credits_the_chosen_account_head(api, tenant):
    """Receiving money that is not from a customer books against the head the user picks, not Misc income."""
    from apps.accounting.models import Account, JournalLine
    from apps.accounting.services import PaymentService
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        head = Account.all_objects.filter(tenant=tenant, type="income", is_group=False).exclude(name__icontains="misc").first()
        pay = PaymentService.record(direction="in", partner_type="other", partner_name="Landlord refund", amount="500", method="cash", other_account=head)
        assert pay.other_account_id == head.id
        line = JournalLine.objects.get(entry=pay.journal, credit__gt=0)
        assert line.account_id == head.id, (line.account.code, head.code)


def test_an_account_added_under_the_bank_group_becomes_a_pickable_bank(api, tenant):
    """Adding a ledger under Bank Accounts in the chart must create the operational bank record too — the
    owner added one straight into the chart and could not pick it on a receipt."""
    from apps.accounting.models import Account, BankAccount
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        group = Account.objects.get(code="1-01-002")
    r = api.post("/api/v1/accounting/accounts", {"code": "1-01-002-0099", "name": "HBL Test", "parent": str(group.id), "type": "asset", "is_group": False}, format="json")
    assert r.status_code == 201, r.content
    with tenant_context(tenant, tenant.owner):
        acct = Account.objects.get(code="1-01-002-0099")
        assert acct.subtype == "bank"
        assert BankAccount.objects.filter(account=acct, name="HBL Test").exists(), "the payment form lists bank records, so one must exist"
