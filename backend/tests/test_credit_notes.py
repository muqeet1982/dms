"""
Credit notes / sales returns, for the three cases the business cares about:
a blind return of good stock (customer returns something not on an invoice), a broken return, and an
expired return. Good stock goes back on hand; broken and expired stock is written off to its own loss
account and never re-enters sellable inventory.
"""
import pytest
from decimal import Decimal
from django.db.models import Sum

pytestmark = pytest.mark.django_db


def _balances(roles):
    """Posted debit/credit totals for each accounting role, for the current tenant."""
    from apps.accounting.models import JournalLine
    from apps.accounting.services import account_for

    out = {}
    for role in roles:
        acct = account_for(role, "credit_note")
        agg = JournalLine.objects.filter(account=acct, entry__status="posted").aggregate(d=Sum("debit"), c=Sum("credit"))
        out[role] = (agg["d"] or Decimal("0"), agg["c"] or Decimal("0"))
    return out


def _return(tenant, cust, variant, *, condition, restock=True, qty="5", price="150", cost="100"):
    from apps.sales.services import CreditNoteService

    cn = CreditNoteService.create(
        customer=cust,
        lines=[{"variant": variant, "qty": qty, "unit_price": price, "unit_cost": cost, "condition": condition, "restock": restock}],
        reason=f"{condition} return",
    )
    return CreditNoteService.post(cn)


def test_blind_return_of_good_stock(tenant):
    """A return with no invoice (item 'not sold' on a traceable doc) restocks sellable stock and credits the customer."""
    from apps.core.tenancy import tenant_context
    from apps.inventory.services import StockService
    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        before = StockService.on_hand(v, wh)[0]
        cn = _return(tenant, cust, v, condition="good")
        assert cn.status == "posted" and cn.journal_id and cn.invoice_id is None
        assert StockService.on_hand(v, wh)[0] == before + 5, "good stock goes back on hand"
        b = _balances(["sales_return", "ar", "inventory", "cogs", "damaged_goods", "expiry_loss"])
        assert b["sales_return"][0] == Decimal("750"), b  # 5 × 150 revenue reversed (debit contra-revenue)
        assert b["ar"][1] == Decimal("750"), b            # customer credited
        assert b["inventory"][0] == Decimal("500"), b     # 5 × 100 cost back into inventory
        assert b["cogs"][1] == Decimal("500"), b          # COGS reversed
        assert b["damaged_goods"][0] == Decimal("0") and b["expiry_loss"][0] == Decimal("0"), b


def test_broken_return_is_written_off_not_restocked(tenant):
    from apps.core.tenancy import tenant_context
    from apps.inventory.models import Warehouse
    from apps.inventory.services import StockService
    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        quar = Warehouse.objects.create(code="QUAR", name="Quarantine", type="quarantine")
        before = StockService.on_hand(v, wh)[0]
        _return(tenant, cust, v, condition="damaged", restock=False)
        assert StockService.on_hand(v, wh)[0] == before, "broken goods do NOT go back to sellable stock"
        assert StockService.on_hand(v, quar)[0] == 5, "they sit in quarantine"
        b = _balances(["damaged_goods", "expiry_loss", "inventory", "cogs"])
        assert b["damaged_goods"][0] == Decimal("500"), b  # cost written off to Damaged Goods
        assert b["expiry_loss"][0] == Decimal("0"), b
        assert b["inventory"][0] == Decimal("0"), b         # nothing back into inventory
        assert b["cogs"][1] == Decimal("500"), b


def test_expired_return_hits_the_expiry_loss_account(tenant):
    from apps.core.tenancy import tenant_context
    from apps.inventory.models import Warehouse
    from apps.inventory.services import StockService
    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        Warehouse.objects.create(code="QUAR", name="Quarantine", type="quarantine")
        before = StockService.on_hand(v, wh)[0]
        _return(tenant, cust, v, condition="expired", restock=False)
        assert StockService.on_hand(v, wh)[0] == before, "expired goods are not resold"
        b = _balances(["expiry_loss", "damaged_goods"])
        assert b["expiry_loss"][0] == Decimal("500"), b   # expired cost → Expiry Loss, not Damaged Goods
        assert b["damaged_goods"][0] == Decimal("0"), b


def test_restock_flag_is_ignored_for_non_good_stock(tenant):
    """Even if restock is left on, broken/expired stock is never restocked — the ledger and warehouse agree."""
    from apps.core.tenancy import tenant_context
    from apps.inventory.models import Warehouse
    from apps.inventory.services import StockService
    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        Warehouse.objects.create(code="QUAR", name="Quarantine", type="quarantine")
        before = StockService.on_hand(v, wh)[0]
        _return(tenant, cust, v, condition="damaged", restock=True)  # restock True but damaged
        assert StockService.on_hand(v, wh)[0] == before, "damaged never restocks, whatever the flag says"
        b = _balances(["inventory", "damaged_goods"])
        assert b["inventory"][0] == Decimal("0") and b["damaged_goods"][0] == Decimal("500"), b
