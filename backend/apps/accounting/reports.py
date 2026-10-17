"""Financial reports (§8.5). Each returns {columns, rows, totals, meta}."""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import Account, JournalEntry, JournalLine

Z = Decimal("0")


def _balances(date_from=None, date_to=None, cost_centre=None):
    """{account_id: (debit_sum, credit_sum)} for posted lines in the window."""
    qs = JournalLine.objects.filter(entry__status="posted")
    if date_from:
        qs = qs.filter(entry__date__gte=date_from)
    if date_to:
        qs = qs.filter(entry__date__lte=date_to)
    if cost_centre:
        qs = qs.filter(cost_centre_id=cost_centre)
    out = {}
    for r in qs.values("account_id").annotate(d=Sum("debit"), c=Sum("credit")):
        out[r["account_id"]] = (r["d"] or Z, r["c"] or Z)
    return out


def _roll_up(accounts, leaf_values):
    """Sum leaf values up the tree; returns {account_id: value}."""
    by_id = {a.id: a for a in accounts}
    totals = defaultdict(Decimal)
    for aid, v in leaf_values.items():
        node = by_id.get(aid)
        while node is not None:
            totals[node.id] += v
            node = by_id.get(node.parent_id)
    return totals


def trial_balance(date_from=None, date_to=None, level=4, **_):
    accounts = list(Account.objects.all().order_by("code"))
    opening = _balances(None, date_from - timezone.timedelta(days=1)) if date_from else {}
    period = _balances(date_from, date_to)
    rows, td, tc = [], Z, Z
    leaf_open, leaf_d, leaf_c = {}, {}, {}
    for a in accounts:
        if a.is_group:
            continue
        od, oc = opening.get(a.id, (Z, Z))
        ob = od - oc + a.opening_balance
        d, c = period.get(a.id, (Z, Z))
        leaf_open[a.id], leaf_d[a.id], leaf_c[a.id] = ob, d, c
    ro, rd, rc = _roll_up(accounts, leaf_open), _roll_up(accounts, leaf_d), _roll_up(accounts, leaf_c)
    for a in accounts:
        if a.level > level:
            continue
        ob, d, c = ro.get(a.id, Z), rd.get(a.id, Z), rc.get(a.id, Z)
        closing = ob + d - c
        if not a.is_group and ob == 0 and d == 0 and c == 0:
            continue
        if a.is_group and ob == 0 and d == 0 and c == 0:
            continue
        rows.append({"account_id": str(a.id), "code": a.code, "name": a.name, "level": a.level, "is_group": a.is_group, "type": a.type, "opening_debit": str(max(ob, Z)), "opening_credit": str(max(-ob, Z)), "debit": str(d), "credit": str(c), "closing_debit": str(max(closing, Z)), "closing_credit": str(max(-closing, Z))})
        if not a.is_group:
            td += max(closing, Z)
            tc += max(-closing, Z)
    return {"columns": ["code", "name", "opening_debit", "opening_credit", "debit", "credit", "closing_debit", "closing_credit"], "rows": rows, "totals": {"closing_debit": str(td), "closing_credit": str(tc), "balanced": td == tc}}


def general_ledger(account=None, date_from=None, date_to=None, partner_type=None, partner_id=None, **_):
    qs = JournalLine.objects.filter(entry__status="posted").select_related("entry", "account", "cost_centre").order_by("entry__date", "entry__created_at", "sequence")
    opening = Z
    acct = Account.objects.filter(pk=account).first() if account else None
    if acct:
        qs = qs.filter(account=acct)
        opening = acct.opening_balance
    if partner_type and partner_id:
        qs = qs.filter(partner_type=partner_type, partner_id=partner_id)
    if date_from:
        agg = qs.filter(entry__date__lt=date_from).aggregate(d=Sum("debit"), c=Sum("credit"))
        opening += (agg["d"] or Z) - (agg["c"] or Z)
        qs = qs.filter(entry__date__gte=date_from)
    if date_to:
        qs = qs.filter(entry__date__lte=date_to)
    rows, bal = [], opening
    lines = list(qs[:5000])
    # the voucher code per entry (CPV, BPV, JV …) — derived once per entry, as the owner's old ERP showed it
    from .vouchers import voucher_code

    by_entry = {}
    for l in lines:
        by_entry.setdefault(l.entry_id, []).append(l)
    # an account-filtered ledger hides the entry's other side, and the code depends on it (bank vs cash) —
    # so the undecided entries get their full line sets in one bulk query
    need = [eid for eid, ls in by_entry.items() if not ls[0].entry.voucher_type and ls[0].entry.type not in ("opening", "closing")]
    full = {}
    for l in JournalLine.objects.filter(entry_id__in=need).select_related("account"):
        full.setdefault(l.entry_id, []).append(l)
    codes = {eid: voucher_code(ls[0].entry, full.get(eid, ls)) for eid, ls in by_entry.items()}
    for l in lines:
        bal += l.debit - l.credit
        rows.append({"date": l.entry.date, "type": codes.get(l.entry_id, "JV"), "journal": l.entry.number, "journal_id": str(l.entry_id), "source": f"{l.entry.source_type} {l.entry.source_number}".strip(), "account": f"{l.account.code} {l.account.name}", "description": l.description or l.entry.memo, "partner": f"{l.partner_type}:{l.partner_id}" if l.partner_type else "", "cost_centre": l.cost_centre.name if l.cost_centre else "", "debit": str(l.debit), "credit": str(l.credit), "balance": str(bal)})
    return {"columns": ["date", "type", "journal", "source", "account", "description", "debit", "credit", "balance"], "rows": rows, "totals": {"opening": str(opening), "closing": str(bal)}}


def journal_register(date_from=None, date_to=None, type=None, status=None, **_):
    qs = JournalEntry.objects.all().prefetch_related("lines__account")
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    if type:
        qs = qs.filter(type=type)
    if status:
        qs = qs.filter(status=status)
    rows = [{"id": str(e.id), "number": e.number, "date": e.date, "type": e.type, "status": e.status, "memo": e.memo, "source": f"{e.source_type} {e.source_number}".strip(), "debit": str(e.total_debit), "credit": str(e.total_credit)} for e in qs[:2000]]
    return {"columns": ["number", "date", "type", "status", "memo", "source", "debit", "credit"], "rows": rows, "totals": {}}


def day_book(date=None, **_):
    date = date or timezone.localdate()
    return general_ledger(date_from=date, date_to=date)


def profit_and_loss(date_from=None, date_to=None, cost_centre=None, compare_from=None, compare_to=None, **_):
    def section(df, dt):
        bal = _balances(df, dt, cost_centre)
        accounts = list(Account.objects.filter(type__in=["income", "expense"]).order_by("code"))
        leaf = {a.id: ((bal.get(a.id, (Z, Z))[1] - bal.get(a.id, (Z, Z))[0]) if a.type == "income" else (bal.get(a.id, (Z, Z))[0] - bal.get(a.id, (Z, Z))[1])) for a in accounts if not a.is_group}
        rolled = _roll_up(accounts, leaf)
        rows = [{"account_id": str(a.id), "code": a.code, "name": a.name, "level": a.level, "is_group": a.is_group, "type": a.type, "amount": str(rolled.get(a.id, Z))} for a in accounts if rolled.get(a.id, Z) != 0 or a.level <= 2]
        income = sum((v for a, v in leaf.items() if Account.objects.get(pk=a).type == "income"), Z) if leaf else Z
        expense = sum((v for a, v in leaf.items() if Account.objects.get(pk=a).type == "expense"), Z) if leaf else Z
        cogs = sum((leaf.get(a.id, Z) for a in accounts if a.code.startswith("5-01") and not a.is_group), Z)
        return rows, income, expense, cogs

    rows, income, expense, cogs = section(date_from, date_to)
    out = {"columns": ["code", "name", "amount"], "rows": rows, "totals": {"income": str(income), "cost_of_sales": str(cogs), "gross_profit": str(income - cogs), "expenses": str(expense), "net_profit": str(income - expense)}}
    if compare_from and compare_to:
        crows, ci, ce, cc = section(compare_from, compare_to)
        out["compare"] = {"rows": crows, "totals": {"income": str(ci), "cost_of_sales": str(cc), "gross_profit": str(ci - cc), "expenses": str(ce), "net_profit": str(ci - ce)}}
    return out


def balance_sheet(as_of=None, **_):
    as_of = as_of or timezone.localdate()
    bal = _balances(None, as_of)
    accounts = list(Account.objects.all().order_by("code"))
    leaf = {}
    for a in accounts:
        if a.is_group:
            continue
        d, c = bal.get(a.id, (Z, Z))
        net = d - c + a.opening_balance
        leaf[a.id] = net if a.type in ("asset",) else -net  # liabilities/equity credit-positive
    # current-year profit = income − expense to date (not yet closed)
    pl = profit_and_loss(None, as_of)
    net_profit = Decimal(pl["totals"]["net_profit"])
    rolled = _roll_up(accounts, {k: v for k, v in leaf.items() if Account.objects.get(pk=k).type in ("asset", "liability", "equity")})
    rows = [{"account_id": str(a.id), "code": a.code, "name": a.name, "level": a.level, "is_group": a.is_group, "type": a.type, "amount": str(rolled.get(a.id, Z))} for a in accounts if a.type in ("asset", "liability", "equity") and (rolled.get(a.id, Z) != 0 or a.level <= 2)]
    assets = sum((v for k, v in leaf.items() if Account.objects.get(pk=k).type == "asset"), Z)
    liabilities = sum((v for k, v in leaf.items() if Account.objects.get(pk=k).type == "liability"), Z)
    equity = sum((v for k, v in leaf.items() if Account.objects.get(pk=k).type == "equity"), Z)
    rows.append({"account_id": "", "code": "", "name": "Current year profit (unclosed)", "level": 4, "is_group": False, "type": "equity", "amount": str(net_profit)})
    return {"columns": ["code", "name", "amount"], "rows": rows, "totals": {"assets": str(assets), "liabilities": str(liabilities), "equity": str(equity + net_profit), "liabilities_and_equity": str(liabilities + equity + net_profit), "balanced": abs(assets - (liabilities + equity + net_profit)) < Decimal("0.01")}}


def cash_flow(date_from=None, date_to=None, **_):
    """Indirect method (simplified): net profit ± working-capital changes + investing/financing from GL."""
    pl = profit_and_loss(date_from, date_to)
    net = Decimal(pl["totals"]["net_profit"])

    def delta(prefixes):
        accts = Account.objects.filter(is_group=False).filter(code__regex="^(" + "|".join(prefixes) + ")")
        total = Z
        for a in accts:
            d, c = _balances(date_from, date_to).get(a.id, (Z, Z))
            total += d - c
        return total

    ar = delta(["1-01-003"])
    inv = delta(["1-01-004"])
    adv = delta(["1-01-005"])
    ap = delta(["2-01-001", "2-01-002", "2-01-003"])
    fixed = delta(["1-02"])
    loans = delta(["2-02"])
    equity = delta(["3-01-001-0001", "3-01-001-0002"])
    operating = net - ar - inv - adv - ap
    investing = -fixed
    financing = -loans - equity
    cash_accounts = Account.objects.filter(is_group=False, code__startswith="1-01-00").filter(code__regex="^1-01-00[12]")
    cash_open = sum((__import__("apps.accounting.services", fromlist=["ReportQueries"]).ReportQueries.account_balance(a, date_from - timezone.timedelta(days=1)) if date_from else Z for a in cash_accounts), Z)
    cash_close = sum((__import__("apps.accounting.services", fromlist=["ReportQueries"]).ReportQueries.account_balance(a, date_to) for a in cash_accounts), Z)
    rows = [
        {"section": "Operating", "item": "Net profit", "amount": str(net)}, {"section": "Operating", "item": "(Increase)/decrease in receivables", "amount": str(-ar)},
        {"section": "Operating", "item": "(Increase)/decrease in inventory", "amount": str(-inv)}, {"section": "Operating", "item": "(Increase)/decrease in advances", "amount": str(-adv)},
        {"section": "Operating", "item": "Increase/(decrease) in payables & taxes", "amount": str(-ap)}, {"section": "Operating", "item": "Net cash from operating activities", "amount": str(operating)},
        {"section": "Investing", "item": "Fixed asset purchases", "amount": str(investing)}, {"section": "Financing", "item": "Loans / capital / drawings", "amount": str(financing)},
        {"section": "Summary", "item": "Net change in cash", "amount": str(operating + investing + financing)}, {"section": "Summary", "item": "Opening cash & bank", "amount": str(cash_open)}, {"section": "Summary", "item": "Closing cash & bank (per GL)", "amount": str(cash_close)},
    ]
    return {"columns": ["section", "item", "amount"], "rows": rows, "totals": {"operating": str(operating), "investing": str(investing), "financing": str(financing)}}


def cash_bank_book(account=None, date_from=None, date_to=None, **_):
    if account:
        return general_ledger(account=account, date_from=date_from, date_to=date_to)
    from .models import BankAccount, CashAccount

    rows = []
    for ca in CashAccount.objects.select_related("account"):
        r = general_ledger(account=ca.account_id, date_from=date_from, date_to=date_to)
        rows.append({"kind": "cash", "name": ca.name, "account_id": str(ca.account_id), "opening": r["totals"]["opening"], "closing": r["totals"]["closing"]})
    for ba in BankAccount.objects.select_related("account"):
        r = general_ledger(account=ba.account_id, date_from=date_from, date_to=date_to)
        rows.append({"kind": "bank", "name": ba.name, "account_id": str(ba.account_id), "opening": r["totals"]["opening"], "closing": r["totals"]["closing"]})
    return {"columns": ["kind", "name", "opening", "closing"], "rows": rows, "totals": {}}


def tax_summary(date_from=None, date_to=None, **_):
    from apps.catalog.models import TaxType

    rows = []
    for tt in TaxType.objects.all():
        qs = JournalLine.objects.filter(entry__status="posted", tax_type=tt)
        if date_from:
            qs = qs.filter(entry__date__gte=date_from)
        if date_to:
            qs = qs.filter(entry__date__lte=date_to)
        out_acct = tt.output_account_id
        in_acct = tt.input_account_id
        output = qs.filter(account__type="liability").aggregate(c=Sum("credit"), d=Sum("debit"))
        inp = qs.filter(account__type="asset").aggregate(c=Sum("credit"), d=Sum("debit"))
        o = (output["c"] or Z) - (output["d"] or Z)
        i = (inp["d"] or Z) - (inp["c"] or Z)
        rows.append({"tax_type_id": str(tt.id), "code": tt.code, "name": tt.name, "kind": tt.kind, "output_tax": str(o), "input_tax": str(i), "net_payable": str(o - i)})
    return {"columns": ["code", "name", "kind", "output_tax", "input_tax", "net_payable"], "rows": rows, "totals": {"net_payable": str(sum((Decimal(r["net_payable"]) for r in rows), Z))}}


def cash_with_reps(as_of=None, **_):
    from apps.partners.models import SupplierRep
    from apps.sales.models import RepCollection

    as_of = as_of or timezone.localdate()
    rows = []
    for rep in SupplierRep.objects.select_related("supplier"):
        held = RepCollection.objects.filter(rep=rep, status="held_by_rep", date__lte=as_of)
        total = held.aggregate(s=Sum("amount"))["s"] or Z
        oldest = held.order_by("date").values_list("date", flat=True).first()
        if total == 0 and not held.exists():
            continue
        rows.append({"rep_id": str(rep.id), "rep": rep.name, "supplier": rep.supplier.name, "collections": held.count(), "amount_held": str(total), "oldest": oldest, "days_outstanding": (as_of - oldest).days if oldest else 0})
    return {"columns": ["rep", "supplier", "collections", "amount_held", "oldest", "days_outstanding"], "rows": rows, "totals": {"amount_held": str(sum((Decimal(r["amount_held"]) for r in rows), Z))}}


def ar_ageing(as_of=None, customer=None, **_):
    from apps.partners.models import Customer
    from apps.sales.models import SalesInvoice

    as_of = as_of or timezone.localdate()
    rows, tot = [], defaultdict(Decimal)
    qs = SalesInvoice.objects.filter(status__in=["posted", "partially_paid", "overdue"]).select_related("customer")
    if customer:
        qs = qs.filter(customer_id=customer)
    per = defaultdict(lambda: defaultdict(Decimal))
    names = {}
    for inv in qs:
        bal = inv.total - inv.paid_amount
        days = (as_of - (inv.due_date or inv.date)).days
        key = "current" if days <= 0 else "1-30" if days <= 30 else "31-60" if days <= 60 else "61-90" if days <= 90 else "90+"
        per[inv.customer_id][key] += bal
        per[inv.customer_id]["total"] += bal
        names[inv.customer_id] = inv.customer
    for cid, b in per.items():
        c = names[cid]
        rows.append({"customer_id": str(cid), "customer": c.name, "code": c.code, "phone": c.phone, "credit_limit": str(c.credit_limit), **{k: str(b[k]) for k in ("current", "1-30", "31-60", "61-90", "90+", "total")}})
        for k in ("current", "1-30", "31-60", "61-90", "90+", "total"):
            tot[k] += b[k]
    rows.sort(key=lambda r: -Decimal(r["total"]))
    return {"columns": ["code", "customer", "phone", "current", "1-30", "31-60", "61-90", "90+", "total"], "rows": rows, "totals": {k: str(v) for k, v in tot.items()}}


def ap_ageing(as_of=None, supplier=None, **_):
    from apps.purchasing.models import PurchaseBill

    as_of = as_of or timezone.localdate()
    rows, tot = [], defaultdict(Decimal)
    qs = PurchaseBill.objects.filter(status__in=["posted", "partially_paid"]).select_related("supplier")
    if supplier:
        qs = qs.filter(supplier_id=supplier)
    per, names = defaultdict(lambda: defaultdict(Decimal)), {}
    for b in qs:
        bal = b.total - b.paid_amount
        days = (as_of - (b.due_date or b.bill_date)).days
        key = "current" if days <= 0 else "1-30" if days <= 30 else "31-60" if days <= 60 else "61-90" if days <= 90 else "90+"
        per[b.supplier_id][key] += bal
        per[b.supplier_id]["total"] += bal
        names[b.supplier_id] = b.supplier
    for sid, b in per.items():
        s = names[sid]
        rows.append({"supplier_id": str(sid), "supplier": s.name, "code": s.code, **{k: str(b[k]) for k in ("current", "1-30", "31-60", "61-90", "90+", "total")}})
        for k in ("current", "1-30", "31-60", "61-90", "90+", "total"):
            tot[k] += b[k]
    return {"columns": ["code", "supplier", "current", "1-30", "31-60", "61-90", "90+", "total"], "rows": rows, "totals": {k: str(v) for k, v in tot.items()}}


def expense_by_category(date_from=None, date_to=None, **_):
    bal = _balances(date_from, date_to)
    rows = []
    for a in Account.objects.filter(type="expense", is_group=False).order_by("code"):
        d, c = bal.get(a.id, (Z, Z))
        if d - c:
            rows.append({"code": a.code, "name": a.name, "amount": str(d - c)})
    return {"columns": ["code", "name", "amount"], "rows": rows, "totals": {"amount": str(sum((Decimal(r["amount"]) for r in rows), Z))}}


def inventory_reconciliation(as_of=None, **_):
    from apps.inventory.services import StockService

    from .services import ReportQueries, account_for

    from apps.delivery.models import DeliveryNoteLine

    as_of = as_of or timezone.localdate()
    val = Decimal(StockService.valuation()["total"])
    gl = ReportQueries.account_balance(account_for("inventory"), as_of) + ReportQueries.account_balance(account_for("in_transit"), as_of)
    # One-step dispatch: goods leave the stock ledger at trip start but COGS posts at invoice. Until delivery
    # (or trip close for returns) they sit on the van — a reconciling item, not an error.
    on_van = Decimal("0")
    for l in DeliveryNoteLine.objects.filter(delivery_note__stock_out_done=True, delivery_note__status__in=["pending", "loaded"]).select_related("delivery_note"):
        on_van += (l.qty_loaded * l.unit_cost).quantize(Decimal("0.0001"))
    for l in DeliveryNoteLine.objects.filter(delivery_note__stock_out_done=True, delivery_note__status__in=["partially_delivered", "failed", "returned"], delivery_note__trip__status__in=["out_for_delivery", "completed"]):
        on_van += (l.qty_returned * l.unit_cost).quantize(Decimal("0.0001"))
    diff = val + on_van - gl
    return {"columns": ["item", "amount"], "rows": [{"item": "Stock valuation (qty × avg cost)", "amount": str(val)}, {"item": "Dispatched, not yet invoiced (on van)", "amount": str(on_van)}, {"item": "GL stock accounts", "amount": str(gl)}, {"item": "Difference", "amount": str(diff)}], "totals": {"difference": str(diff), "on_van": str(on_van)}}


def audit_trail(date_from=None, date_to=None, **_):
    from apps.core.models import AuditLog

    qs = AuditLog.objects.filter(action__startswith="journal")
    if date_from:
        qs = qs.filter(at__date__gte=date_from)
    if date_to:
        qs = qs.filter(at__date__lte=date_to)
    rows = [{"at": a.at, "actor": a.actor.full_name if a.actor else "", "action": a.action, "object": a.object_repr, "after": a.after} for a in qs[:1000]]
    return {"columns": ["at", "actor", "action", "object"], "rows": rows, "totals": {}}


def company_claims(status=None, supplier=None, date_from=None, date_to=None, **_):
    """Manufacturer discount-share claims per company: raised, recovered and still outstanding."""
    from apps.sales.models import CompanyClaim

    qs = CompanyClaim.objects.exclude(status="cancelled").select_related("supplier")
    if status:
        qs = qs.filter(status=status)
    if supplier:
        qs = qs.filter(supplier_id=supplier)
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    per, names = defaultdict(lambda: defaultdict(Decimal)), {}
    for c in qs:
        per[c.supplier_id]["claimed"] += c.total
        per[c.supplier_id]["recovered"] += c.paid_amount
        per[c.supplier_id]["outstanding"] += (c.total - c.paid_amount)
        per[c.supplier_id]["claims"] += 1
        names[c.supplier_id] = c.supplier
    rows, tot = [], defaultdict(Decimal)
    for sid, b in per.items():
        s = names[sid]
        rows.append({"code": s.code, "supplier": s.name, "claims": str(int(b["claims"])), "claimed": str(b["claimed"]), "recovered": str(b["recovered"]), "outstanding": str(b["outstanding"])})
        for k in ("claimed", "recovered", "outstanding"):
            tot[k] += b[k]
    rows.sort(key=lambda r: Decimal(r["outstanding"]), reverse=True)
    return {"columns": ["code", "supplier", "claims", "claimed", "recovered", "outstanding"], "rows": rows, "totals": {k: str(v) for k, v in tot.items()}}
