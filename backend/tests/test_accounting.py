import pytest
from decimal import Decimal

from apps.core.exceptions import DomainError
from apps.core.tenancy import tenant_context

from .conftest import seed_basic

pytestmark = pytest.mark.django_db


def test_unbalanced_journal_is_rejected(tenant):
    from apps.accounting.models import Account
    from apps.accounting.services import Line, PostingService

    with tenant_context(tenant, tenant.owner):
        cash = Account.objects.get(code="1-01-001-0001")
        sales = Account.objects.get(code="4-01-001-0001")
        with pytest.raises(DomainError):
            PostingService.post([Line(cash, debit=100), Line(sales, credit=90)], memo="bad")
        je = PostingService.post([Line(cash, debit=100), Line(sales, credit=100)], memo="ok")
        assert je.status == "posted" and je.total_debit == je.total_credit == Decimal("100")


def test_posting_to_closed_period_is_refused(tenant):
    from django.utils import timezone

    from apps.accounting.coa_template import ensure_fiscal_year
    from apps.accounting.models import Account, AccountingPeriod
    from apps.accounting.services import Line, PeriodService, PostingService

    with tenant_context(tenant, tenant.owner):
        today = timezone.localdate()
        ensure_fiscal_year(tenant, today)
        period = AccountingPeriod.objects.get(start__lte=today, end__gte=today)
        PeriodService.close(period, force=True)
        cash = Account.objects.get(code="1-01-001-0001")
        cap = Account.objects.get(code="3-01-001-0001")
        with pytest.raises(DomainError):
            PostingService.post([Line(cash, debit=10), Line(cap, credit=10)], date_=today, memo="closed")
        PeriodService.reopen(period)
        assert PostingService.post([Line(cash, debit=10), Line(cap, credit=10)], date_=today, memo="open") is not None


def test_reverse_creates_mirror_entry(tenant):
    from apps.accounting.models import Account
    from apps.accounting.services import Line, PostingService

    with tenant_context(tenant, tenant.owner):
        cash = Account.objects.get(code="1-01-001-0001")
        cap = Account.objects.get(code="3-01-001-0001")
        je = PostingService.post([Line(cash, debit=500), Line(cap, credit=500)], memo="capital")
        rev = PostingService.reverse(je)
        je.refresh_from_db()
        assert je.status == "reversed" and je.reversed_by_entry_id == rev.id
        assert rev.lines.get(account=cash).credit == Decimal("500")


def test_the_standard_chart_of_accounts_cannot_be_renamed_through_the_api(api, tenant):
    """
    The balance sheet and P&L print the level 1–2 account names as their own headings, and the automatic
    journals resolve accounts by code prefix. Renaming or renumbering one of those rows therefore rewrites a
    report or breaks a posting — so the rule belongs in the API, not only in the form that hides the field.
    """
    from apps.accounting.models import Account
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        top = Account.objects.filter(level=1).first()
        group = Account.objects.filter(level=2).first()
        leaf = Account.objects.filter(level__gte=3, is_group=False).first()
        assert top and group and leaf, "this test needs a seeded chart of accounts"

    for account in (top, group):
        r = api.patch(f"/api/v1/accounting/accounts/{account.pk}", {"name": "Renamed By Hand"}, format="json")
        assert r.status_code == 400, f"{account.code} should be protected, got {r.status_code}"
        assert "standard chart of accounts" in str(r.data), r.data

    renumber = api.patch(f"/api/v1/accounting/accounts/{group.pk}", {"code": "9-99"}, format="json")
    assert renumber.status_code == 400, "a group's code is what the posting rules look up"

    # the accounts a distributor actually maintains stay editable
    ok = api.patch(f"/api/v1/accounting/accounts/{leaf.pk}", {"name": "HBL Current 1234"}, format="json")
    assert ok.status_code == 200, ok.content
    assert ok.data["name"] == "HBL Current 1234"

    # the level 1-2 rows are now fully locked, not only their identifying fields: changing the subtype, the
    # active flag or the description on one is refused too (the owner asked for the first two levels to be
    # entirely non-editable).
    for payload in ({"subtype": "changed"}, {"is_active": False}, {"description": "note"}):
        blocked = api.patch(f"/api/v1/accounting/accounts/{group.pk}", payload, format="json")
        assert blocked.status_code == 400, f"{payload} should be refused on a level-2 account, got {blocked.status_code}"
        assert "cannot be edited" in str(blocked.data), blocked.data

    # and a no-op save of a protected row is not treated as a change
    quiet = api.patch(f"/api/v1/accounting/accounts/{top.pk}", {"name": top.name}, format="json")
    assert quiet.status_code == 200, quiet.content

    # a level-3+ account's subtype and active flag stay editable
    ok2 = api.patch(f"/api/v1/accounting/accounts/{leaf.pk}", {"subtype": "bank"}, format="json")
    assert ok2.status_code == 200, ok2.content
