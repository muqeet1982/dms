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


def test_impersonations_of_different_tenants_coexist(admin_user, tenant, tenant_b):
    """Two people share the platform admin login: one working in tenant A, one in tenant B. Starting or ending
    one tenant's impersonation must not sign the other person out (it used to end every tenant's)."""
    from apps.accounts.models import ImpersonationSession
    from apps.platform.services import TenantService

    a = TenantService.start_impersonation(admin_user, tenant, "support A")
    b = TenantService.start_impersonation(admin_user, tenant_b, "support B")
    a.refresh_from_db()
    assert a.ended_at is None, "tenant B's impersonation must not end tenant A's"
    # a second impersonation of the SAME tenant replaces the first
    a2 = TenantService.start_impersonation(admin_user, tenant, "again")
    a.refresh_from_db(); b.refresh_from_db()
    assert a.ended_at is not None and b.ended_at is None
    # ending one tenant leaves the other alone
    TenantService.end_impersonation(admin_user, tenant=tenant)
    a2.refresh_from_db(); b.refresh_from_db()
    assert a2.ended_at is not None and b.ended_at is None
    # ending with no tenant still ends everything (the old catch-all behaviour stays available)
    TenantService.end_impersonation(admin_user)
    b.refresh_from_db()
    assert b.ended_at is not None


def test_payment_form_shows_both_legs(api, tenant):
    """The form's two dropdowns: money accounts merged+searchable, and the partner leg resolved by the same
    code that posts the journal — locked for partners, free for 'other'."""
    r = api.get("/api/v1/accounting/payments/money-accounts")
    assert r.status_code == 200 and any(x["kind"] == "bank" for x in r.data) and any(x["kind"] == "cash" for x in r.data), r.data
    assert all(":" in x["id"] for x in r.data)
    s = api.get("/api/v1/accounting/payments/money-accounts", {"search": "main bank"})
    assert all("main bank" in x["label"].lower() for x in s.data) and s.data

    legs = api.get("/api/v1/accounting/payments/legs", {"direction": "in", "partner_type": "customer"})
    assert legs.status_code == 200 and legs.data["locked"] and "Receivable" in legs.data["account"], legs.data
    other = api.get("/api/v1/accounting/payments/legs", {"direction": "in", "partner_type": "other"})
    assert not other.data["locked"]


def test_the_voucher_actually_prints_its_three_signature_boxes(api, tenant):
    """The block was silently dropped for days: voucher.html overrode `signatures`, but _doc.html never
    declared that block, so Django discarded the whole strip and no voucher ever printed one."""
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        from django.template.loader import render_to_string

        from apps.accounting.models import JournalEntry
        from apps.accounting.vouchers import signature_boxes, voucher_code, voucher_title

        e = JournalEntry.objects.filter(status="posted").first()
        if e is None:
            from decimal import Decimal

            from apps.accounting.services import Line, PostingService, account_for

            e = PostingService.post([Line(account_for("cash"), debit=Decimal("100"), description="sig test"),
                                     Line(account_for("sales"), credit=Decimal("100"), description="sig test")],
                                    memo="signature strip test")
        lines = list(e.lines.select_related("account"))
        code = voucher_code(e, lines)
        html = render_to_string("accounting/voucher.html", {"e": e, "lines": lines, "voucher_code": code,
                                                            "voucher_title": voucher_title(code),
                                                            "signatures": signature_boxes(code), "tenant": tenant})
    for label in ("Prepared By", "Checked By", "Approved By"):
        assert label in html, f"{label} missing from the printed voucher"
    assert 'class="signs"' in html


def test_reset_to_draft_removes_the_posting_without_an_extra_entry(api, tenant):
    """The owner's ask: un-post a manual journal — no reversing entry, the figures just stop counting."""
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        from decimal import Decimal

        from apps.accounting.models import JournalEntry
        from apps.accounting.services import Line, PostingService, ReportQueries, account_for

        cash = account_for("cash")
        before = ReportQueries.account_balance(cash)
        e = PostingService.post([Line(cash, debit=Decimal("500"), description="reset test"),
                                 Line(account_for("sales"), credit=Decimal("500"), description="reset test")],
                                memo="reset-to-draft test")
        assert ReportQueries.account_balance(cash) - before == Decimal("500")
        entries_before = JournalEntry.objects.count()

    r = api.post(f"/api/v1/accounting/journals/{e.id}/reset-to-draft", {}, format="json")
    assert r.status_code == 200 and r.data["status"] == "draft", r.content

    with tenant_context(tenant, tenant.owner):
        assert ReportQueries.account_balance(cash) == before, "the figures stopped counting"
        assert JournalEntry.objects.count() == entries_before, "and NO extra entry was written"

    # a document's own journal refuses — those books follow their document
    with tenant_context(tenant, tenant.owner):
        doc_je = JournalEntry.objects.exclude(source_type="").exclude(source_type__isnull=True).filter(status="posted").first()
    if doc_je is not None:
        blocked = api.post(f"/api/v1/accounting/journals/{doc_je.id}/reset-to-draft", {}, format="json")
        assert blocked.status_code in (400, 422), blocked.content
