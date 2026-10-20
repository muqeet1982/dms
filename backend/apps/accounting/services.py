"""
PostingService: the ONLY place that creates JournalEntry rows (§10.2).
Every method builds balanced lines from the PostingRule mapping and posts atomically.
"""
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.audit import AuditService
from apps.core.exceptions import DomainError
from apps.core.numbering import next_number
from apps.core.tenancy import get_current_tenant, get_current_user

from .coa_template import ensure_fiscal_year
from .models import Account, AccountingPeriod, Expense, FiscalYear, JournalEntry, JournalLine, OpeningBalanceBatch, Payment, PaymentAllocation, PostingRule

Q4 = Decimal("0.0001")


def q4(v):
    return Decimal(v or 0).quantize(Q4)


class Line:
    __slots__ = ("account", "debit", "credit", "description", "partner_type", "partner_id", "cost_centre", "tax_type")

    def __init__(self, account, debit=0, credit=0, description="", partner_type="", partner_id="", cost_centre=None, tax_type=None):
        self.account, self.debit, self.credit = account, q4(debit), q4(credit)
        self.description, self.partner_type, self.partner_id, self.cost_centre, self.tax_type = description[:200], partner_type, str(partner_id or ""), cost_centre, tax_type


def account_for(role, document_type="*"):
    rule = PostingRule.objects.filter(role=role, document_type=document_type).select_related("account").first() or PostingRule.objects.filter(role=role, document_type="*").select_related("account").first()
    if rule is None:
        raise DomainError(f"No posting rule for role '{role}'. Configure Settings → Posting rules.")
    return rule.account


def period_for(on_date):
    tenant = get_current_tenant()
    fy = ensure_fiscal_year(tenant, on_date)
    period = AccountingPeriod.objects.filter(fiscal_year=fy, start__lte=on_date, end__gte=on_date).first()
    if period is None:
        raise DomainError(f"No accounting period covers {on_date:%d/%m/%Y}.")
    if period.status == "closed" or fy.status == "closed":
        raise DomainError(f"Accounting period {period} is closed. Reopen it to post on {on_date:%d/%m/%Y}.", code="period_closed")
    return period


class PostingService:
    # ------------------------------------------------------------------ core
    @staticmethod
    @transaction.atomic
    def post(lines, *, date_=None, memo="", source=None, type_="auto", status="posted", number=None):
        date_ = date_ or timezone.localdate()
        lines = [l for l in lines if l.debit or l.credit]
        if not lines:
            return None
        total_d = sum((l.debit for l in lines), Decimal("0"))
        total_c = sum((l.credit for l in lines), Decimal("0"))
        if total_d != total_c:
            raise DomainError(f"Journal does not balance: debit {total_d} ≠ credit {total_c}.", code="unbalanced")
        period = period_for(date_) if status == "posted" else None
        entry = JournalEntry.objects.create(
            number=number or next_number("journal"), date=date_, type=type_, memo=memo[:300], status=status, period=period,
            source_type=source.__class__.__name__ if source is not None else "", source_id=str(source.pk) if source is not None else "", source_number=str(getattr(source, "number", "") or ""),
            posted_by=get_current_user() if getattr(get_current_user(), "is_authenticated", False) else None, posted_at=timezone.now() if status == "posted" else None,
            total_debit=total_d, total_credit=total_c,
        )
        for i, l in enumerate(lines):
            if l.account.is_group:
                raise DomainError(f"Cannot post to group account {l.account}.")
            JournalLine.objects.create(entry=entry, account=l.account, debit=l.debit, credit=l.credit, description=l.description, partner_type=l.partner_type, partner_id=l.partner_id, cost_centre=l.cost_centre, tax_type=l.tax_type, sequence=i)
        return entry

    @staticmethod
    @transaction.atomic
    def post_draft(entry):
        if entry.status != "draft":
            raise DomainError("Only draft journals can be posted.")
        lines = list(entry.lines.select_related("account"))
        d = sum((l.debit for l in lines), Decimal("0"))
        c = sum((l.credit for l in lines), Decimal("0"))
        if d != c or not lines:
            raise DomainError(f"Journal does not balance: debit {d} ≠ credit {c}.", code="unbalanced")
        for l in lines:
            if l.account.is_group or not l.account.allow_manual_posting and entry.type == "manual":
                raise DomainError(f"Account {l.account} does not allow manual posting.")
        entry.period = period_for(entry.date)
        entry.status = "posted"
        entry.posted_by = get_current_user()
        entry.posted_at = timezone.now()
        entry.total_debit, entry.total_credit = d, c
        entry.save()
        AuditService.log("journal.posted", entry, after={"debit": str(d), "credit": str(c)})
        return entry

    @staticmethod
    @transaction.atomic
    def reverse(entry, date_=None, memo=None):
        if entry.status != "posted":
            raise DomainError("Only posted journals can be reversed.")
        lines = [Line(l.account, l.credit, l.debit, f"Reversal: {l.description}", l.partner_type, l.partner_id, l.cost_centre, l.tax_type) for l in entry.lines.select_related("account")]
        rev = PostingService.post(lines, date_=date_ or timezone.localdate(), memo=memo or f"Reversal of {entry.number}", source=entry, type_=entry.type)
        entry.status = "reversed"
        entry.reversed_by_entry = rev
        entry.save(update_fields=["status", "reversed_by_entry", "updated_at"])
        AuditService.log("journal.reversed", entry, after={"reversed_by": rev.number})
        return rev

    # --------------------------------------------------------------- purchasing
    @staticmethod
    def post_grn(grn):
        inv, grni = account_for("inventory", "grn"), account_for("grni", "grn")
        total = sum((q4(l.qty_accepted * l.unit_cost) + q4(l.landed_cost_share) for l in grn.lines.all()), Decimal("0"))
        if total == 0:
            return None
        lines = [Line(inv, debit=total, description=f"GRN {grn.number}"), Line(grni, credit=total, description=f"GRN {grn.number}", partner_type="supplier", partner_id=grn.supplier_id)]
        return PostingService.post(lines, date_=grn.date, memo=f"Goods receipt {grn.number} from {grn.supplier.name}", source=grn)

    @staticmethod
    def post_purchase_bill(bill):
        grni, ap, ppv = account_for("grni", "purchase_bill"), bill.supplier.account or account_for("ap", "purchase_bill"), account_for("ppv", "purchase_bill")
        lines = []
        grn_value = sum((q4(l.qty_accepted * l.unit_cost) + q4(l.landed_cost_share) for g in bill.grns.all() for l in g.lines.all()), Decimal("0")) if bill.grns.exists() else bill.subtotal
        lines.append(Line(grni, debit=grn_value, description=f"Bill {bill.number}", partner_type="supplier", partner_id=bill.supplier_id))
        variance = q4(bill.subtotal - grn_value)
        if variance > 0:
            lines.append(Line(ppv, debit=variance, description="Purchase price variance"))
        elif variance < 0:
            lines.append(Line(ppv, credit=-variance, description="Purchase price variance"))
        for idx, (amt, tt) in enumerate([(bill.tax1_total, bill.tax1_type), (bill.tax2_total, bill.tax2_type)]):
            if amt:
                acct = (tt.input_account if tt and tt.input_account_id else None) or account_for(f"tax{idx + 1}_in", "purchase_bill")
                lines.append(Line(acct, debit=amt, description=f"Input tax {tt.name if tt else idx + 1}", tax_type=tt))
        if bill.freight:
            lines.append(Line(account_for("freight_in", "purchase_bill"), debit=bill.freight, description="Freight"))
        lines.append(Line(ap, credit=bill.total, description=f"Bill {bill.number} {bill.supplier_invoice_no}", partner_type="supplier", partner_id=bill.supplier_id))
        return PostingService.post(lines, date_=bill.bill_date, memo=f"Purchase bill {bill.number} – {bill.supplier.name}", source=bill)

    @staticmethod
    def post_purchase_return(ret):
        ap, inv = ret.supplier.account or account_for("ap", "purchase_return"), account_for("inventory", "purchase_return")
        total = sum((q4(l.qty * l.unit_cost) for l in ret.lines.all()), Decimal("0"))
        lines = [Line(ap, debit=total, description=f"Return {ret.number}", partner_type="supplier", partner_id=ret.supplier_id), Line(inv, credit=total, description=f"Return {ret.number}")]
        return PostingService.post(lines, date_=ret.date, memo=f"Purchase return {ret.number} to {ret.supplier.name}", source=ret)

    @staticmethod
    def post_supplier_debit_note(supplier, amount, reason, on_date, source=None):
        lines = [Line(supplier.account or account_for("ap"), debit=amount, description=reason, partner_type="supplier", partner_id=supplier.pk), Line(account_for("damaged_goods"), credit=amount, description=reason)]
        return PostingService.post(lines, date_=on_date, memo=f"Supplier debit note – {supplier.name}: {reason}", source=source)

    # -------------------------------------------------------------------- sales
    @staticmethod
    def post_sales_invoice(inv):
        ar = inv.customer.account or account_for("ar", "sales_invoice")
        lines = [Line(ar, debit=inv.total, description=f"Invoice {inv.number}", partner_type="customer", partner_id=inv.customer_id)]
        gross = inv.subtotal
        lines.append(Line(account_for("sales", "sales_invoice"), credit=gross, description=f"Sales {inv.number}", cost_centre=inv.cost_centre))
        if inv.discount:
            # the owner's rule (21 Oct 2026): Discounts Allowed carries ONLY the distributor's own share of
            # the discount (total% − company% from the discount table). The manufacturer-funded remainder
            # debits the Distribution Margin head, which the claim journal credits right back — so that head
            # shows the funded flow, the distributor's P&L bears only its own points, and the claim
            # receivable carries the manufacturer's debt until the payable is netted or a receipt arrives.
            from apps.sales.services import InvoiceService

            funded = sum((amt for items in InvoiceService.company_shares(inv).values() for _, _, amt in items), Decimal("0"))
            funded = min(funded, inv.discount)
            own = inv.discount - funded
            if own > 0:
                lines.append(Line(account_for("discount", "sales_invoice"), debit=own, description=f"Discount {inv.number} — own share"))
            if funded > 0:
                lines.append(Line(account_for("manufacturer_incentive", "company_claim"), debit=funded, description=f"Discount {inv.number} — manufacturer share"))
        for idx, (amt, tt) in enumerate([(inv.tax1_total, inv.tax1_type), (inv.tax2_total, inv.tax2_type)]):
            if amt:
                acct = (tt.output_account if tt and tt.output_account_id else None) or account_for(f"tax{idx + 1}_out", "sales_invoice")
                lines.append(Line(acct, credit=amt, description=f"Output tax {tt.name if tt else idx + 1}", tax_type=tt))
        je = PostingService.post(lines, date_=inv.date, memo=f"Sales invoice {inv.number} – {inv.customer.name}", source=inv)
        # COGS at the movement's average cost
        cogs_total = sum((q4((l.qty + l.free_qty) * l.unit_cost) for l in inv.lines.all()), Decimal("0"))
        if cogs_total:
            src = account_for("in_transit", "sales_invoice") if inv.from_transit else account_for("inventory", "sales_invoice")
            PostingService.post([Line(account_for("cogs", "sales_invoice"), debit=cogs_total, description=f"COGS {inv.number}", cost_centre=inv.cost_centre), Line(src, credit=cogs_total, description=f"COGS {inv.number}")], date_=inv.date, memo=f"COGS for {inv.number}", source=inv)
        return je

    @staticmethod
    def post_credit_note(cn):
        ar = cn.customer.account or account_for("ar", "credit_note")
        lines = [Line(account_for("sales_return", "credit_note"), debit=cn.subtotal, description=f"Credit note {cn.number}")]
        for idx, (amt, tt) in enumerate([(cn.tax1_total, cn.tax1_type), (cn.tax2_total, cn.tax2_type)]):
            if amt:
                acct = (tt.output_account if tt and tt.output_account_id else None) or account_for(f"tax{idx + 1}_out", "credit_note")
                lines.append(Line(acct, debit=amt, description=f"Output tax reversal {tt.name if tt else idx + 1}", tax_type=tt))
        lines.append(Line(ar, credit=cn.total, description=f"Credit note {cn.number}", partner_type="customer", partner_id=cn.customer_id))
        je = PostingService.post(lines, date_=cn.date, memo=f"Credit note {cn.number} – {cn.customer.name}", source=cn)
        # COGS reversal: good stock the user restocked goes back into Inventory; broken goods are expensed to
        # Damaged Goods and expired goods to Expiry Loss. The restock test matches the stock routing in
        # CreditNoteService.post exactly, so the ledger and the warehouse never disagree.
        restock_value = expired_value = damaged_value = Decimal("0")
        for l in cn.lines.all():
            val = q4(l.qty * l.unit_cost)
            if l.restock and l.condition == "good":
                restock_value += val
            elif l.condition == "expired":
                expired_value += val
            else:
                damaged_value += val
        cl = []
        if restock_value:
            cl += [Line(account_for("inventory", "credit_note"), debit=restock_value, description=f"Restock {cn.number}")]
        if expired_value:
            cl += [Line(account_for("expiry_loss", "credit_note"), debit=expired_value, description=f"Expired returns {cn.number}")]
        if damaged_value:
            cl += [Line(account_for("damaged_goods", "credit_note"), debit=damaged_value, description=f"Damaged returns {cn.number}")]
        if cl:
            cl.append(Line(account_for("cogs", "credit_note"), credit=restock_value + expired_value + damaged_value, description=f"COGS reversal {cn.number}"))
            PostingService.post(cl, date_=cn.date, memo=f"COGS reversal for {cn.number}", source=cn)
        return je

    @staticmethod
    def post_company_claim(claim):
        """The manufacturer owes us its share of the discount: Dr Manufacturer Claims Receivable, Cr incentive income."""
        recv = account_for("manufacturer_claim", "company_claim")
        income = account_for("manufacturer_incentive", "company_claim")
        lines = [
            Line(recv, debit=claim.total, description=f"Claim {claim.number} – {claim.supplier.name}", partner_type="supplier", partner_id=claim.supplier_id),
            Line(income, credit=claim.total, description=f"Manufacturer incentive {claim.invoice.number}"),
        ]
        return PostingService.post(lines, date_=claim.date, memo=f"Discount claim {claim.number} – {claim.supplier.name}", source=claim)

    @staticmethod
    def post_claim_netoff(claim, amount, on_date):
        """Settle a claim by netting it against what we owe the manufacturer: Dr Trade Payables, Cr Claims Receivable."""
        ap = (claim.supplier.account if claim.supplier.account_id else None) or account_for("ap", "company_claim")
        recv = account_for("manufacturer_claim", "company_claim")
        lines = [
            Line(ap, debit=amount, description=f"Claim {claim.number} netted against purchases", partner_type="supplier", partner_id=claim.supplier_id),
            Line(recv, credit=amount, description=f"Claim {claim.number} settled by net-off", partner_type="supplier", partner_id=claim.supplier_id),
        ]
        return PostingService.post(lines, date_=on_date, memo=f"Claim {claim.number} net-off – {claim.supplier.name}", source=claim)

    # ----------------------------------------------------------------- payments
    @staticmethod
    def _money_account(payment):
        if payment.method == "cheque" and payment.direction == "in":
            return account_for("pdc_received", "payment")
        if payment.method == "cheque" and payment.direction == "out":
            return account_for("cheques_payable", "payment")
        if payment.bank_account_id:
            return payment.bank_account.account
        if payment.cash_account_id:
            return payment.cash_account.account
        return account_for("cash", "payment")

    @staticmethod
    def resolve_partner_account(direction, partner_type, partner_id=None, other_account=None):
        """The partner leg of a payment — the account the money is booked against. One resolver, used both to
        post the journal and to show the leg on the form before saving, so the two can never disagree."""
        if partner_type == "customer":
            from apps.partners.models import Customer

            c = Customer.objects.filter(pk=partner_id).first() if partner_id else None
            return c.account if c and c.account_id else account_for("ar", "payment")
        if partner_type == "supplier":
            from apps.partners.models import Supplier

            s = Supplier.objects.filter(pk=partner_id).first() if partner_id else None
            return s.account if s and s.account_id else account_for("ap", "payment")
        if partner_type == "rep":
            return account_for("rep_clearing", "payment")
        if partner_type == "company":
            # manufacturer reimbursing its discount share — settles the claims receivable, not AP
            return account_for("manufacturer_claim", "payment")
        if other_account is not None:
            # "other" with a chosen head: the user says what the money is (rent income, a loan, capital …)
            return other_account
        return account_for("other_income", "payment") if direction == "in" else account_for("bank_charges", "payment")

    @staticmethod
    def post_payment(payment):
        money = PostingService._money_account(payment)
        partner_acct = PostingService.resolve_partner_account(payment.direction, payment.partner_type, payment.partner_id, payment.other_account if payment.other_account_id else None)
        net = payment.amount - payment.withholding_amount
        # the manufacturer is a supplier on the ledger; "other" carries no partner tag
        jp = "supplier" if payment.partner_type == "company" else (payment.partner_type if payment.partner_type != "other" else "")
        if payment.direction == "in":
            lines = [Line(money, debit=net, description=f"Receipt {payment.number}")]
            if payment.withholding_amount:
                lines.append(Line(account_for("withholding_receivable", "payment"), debit=payment.withholding_amount, description="Withholding tax deducted by customer", tax_type=payment.withholding_tax))
            lines.append(Line(partner_acct, credit=payment.amount, description=f"Receipt {payment.number} {payment.reference}", partner_type=jp, partner_id=payment.partner_id))
        else:
            # paying a supplier may deduct manufacturer discount claims: the payable is settled for cash +
            # deductions, and the deducted part clears the claims receivable instead of the money account
            claim_ded = Decimal("0")
            if payment.partner_type == "supplier":
                claim_ded = sum((a.amount for a in payment.allocations.filter(document_type="company_claim")), Decimal("0"))
            lines = [Line(partner_acct, debit=payment.amount + claim_ded, description=f"Payment {payment.number} {payment.reference}", partner_type=jp, partner_id=payment.partner_id), Line(money, credit=net, description=f"Payment {payment.number}")]
            if claim_ded:
                lines.append(Line(account_for("manufacturer_claim", "payment"), credit=claim_ded, description=f"Discount claims deducted {payment.number}"))
            if payment.withholding_amount:
                lines.append(Line(account_for("withholding_payable", "payment"), credit=payment.withholding_amount, description="Withholding tax withheld", tax_type=payment.withholding_tax))
        return PostingService.post(lines, date_=payment.date, memo=f"{'Receipt' if payment.direction == 'in' else 'Payment'} {payment.number} – {payment.partner_name}", source=payment)

    @staticmethod
    def post_cheque_event(payment, event, bank_account=None, charges=Decimal("0")):
        pdc = account_for("pdc_received", "payment") if payment.direction == "in" else account_for("cheques_payable", "payment")
        bank = (bank_account.account if bank_account else None) or (payment.bank_account.account if payment.bank_account_id else account_for("bank", "payment"))
        net = payment.amount - payment.withholding_amount
        if event == "cleared":
            if payment.direction == "in":
                lines = [Line(bank, debit=net, description=f"Cheque {payment.cheque_no} cleared"), Line(pdc, credit=net, description=f"Cheque {payment.cheque_no} cleared")]
            else:
                lines = [Line(pdc, debit=net, description=f"Cheque {payment.cheque_no} presented"), Line(bank, credit=net, description=f"Cheque {payment.cheque_no} presented")]
        elif event == "bounced":
            partner_acct = account_for("ar", "payment") if payment.partner_type == "customer" else account_for("ap", "payment")
            if payment.direction == "in":
                lines = [Line(partner_acct, debit=payment.amount, description=f"Cheque {payment.cheque_no} bounced", partner_type=payment.partner_type, partner_id=payment.partner_id), Line(pdc, credit=net, description=f"Cheque {payment.cheque_no} bounced")]
                if payment.withholding_amount:
                    lines.append(Line(account_for("withholding_receivable", "payment"), credit=payment.withholding_amount, description="Withholding reversal"))
                if charges:
                    lines += [Line(account_for("bank_charges", "payment"), debit=charges, description="Bounce charges"), Line(bank, credit=charges, description="Bounce charges")]
            else:
                lines = [Line(pdc, debit=net, description=f"Cheque {payment.cheque_no} bounced"), Line(partner_acct, credit=payment.amount, description=f"Cheque {payment.cheque_no} bounced", partner_type=payment.partner_type, partner_id=payment.partner_id)]
        else:
            return None
        return PostingService.post(lines, date_=timezone.localdate(), memo=f"Cheque {payment.cheque_no} {event} – {payment.number}", source=payment)

    # -------------------------------------------------------------- rep cash
    @staticmethod
    def post_rep_collection(col):
        ar = col.customer.account or account_for("ar", "rep_collection")
        lines = [Line(account_for("rep_clearing", "rep_collection"), debit=col.amount, description=f"Collected by rep {col.rep.name}", partner_type="rep", partner_id=col.rep_id), Line(ar, credit=col.amount, description=f"Rep collection {col.number}", partner_type="customer", partner_id=col.customer_id)]
        return PostingService.post(lines, date_=col.date, memo=f"Rep collection {col.number} – {col.customer.name} by {col.rep.name}", source=col)

    @staticmethod
    def post_rep_settlement(st):
        money = st.cash_account.account if st.cash_account_id else (st.bank_account.account if st.bank_account_id else account_for("cash", "rep_settlement"))
        lines = [Line(money, debit=st.amount, description=f"Settlement {st.number} from {st.rep.name}"), Line(account_for("rep_clearing", "rep_settlement"), credit=st.amount, description=f"Settlement {st.number}", partner_type="rep", partner_id=st.rep_id)]
        return PostingService.post(lines, date_=st.date, memo=f"Rep settlement {st.number} – {st.rep.name}", source=st)

    # ---------------------------------------------------------------- inventory
    @staticmethod
    def post_stock_adjustment(adj):
        if adj.total_value == 0:
            return None
        inv = account_for("inventory", "stock_adjustment")
        expense = adj.reason.expense_account or account_for("shrinkage", "stock_adjustment")
        if adj.type == "decrease":
            lines = [Line(expense, debit=adj.total_value, description=f"{adj.reason.name} {adj.number}"), Line(inv, credit=adj.total_value, description=f"Adjustment {adj.number}")]
        else:
            lines = [Line(inv, debit=adj.total_value, description=f"Adjustment {adj.number}"), Line(expense, credit=adj.total_value, description=f"{adj.reason.name} {adj.number}")]
        je = PostingService.post(lines, date_=adj.date, memo=f"Stock adjustment {adj.number} ({adj.reason.name})", source=adj)
        adj.journal = je
        adj.save(update_fields=["journal"])
        return je

    @staticmethod
    def post_transfer_dispatch(tr):
        from apps.inventory.models import Warehouse

        if not Warehouse.objects.filter(type="van", vehicle__isnull=True, code="TRANSIT").exists():
            return None
        total = sum((q4(l.qty_sent * l.unit_cost) for l in tr.lines.all()), Decimal("0"))
        if not total:
            return None
        return PostingService.post([Line(account_for("in_transit"), debit=total, description=f"Transfer {tr.number} dispatched"), Line(account_for("inventory"), credit=total, description=f"Transfer {tr.number} dispatched")], date_=tr.date, memo=f"Stock transfer {tr.number} dispatched", source=tr)

    @staticmethod
    def post_transfer_receive(tr):
        from apps.inventory.models import Warehouse

        if not Warehouse.objects.filter(type="van", vehicle__isnull=True, code="TRANSIT").exists():
            return None
        total = sum((q4(l.qty_sent * l.unit_cost) for l in tr.lines.all()), Decimal("0"))
        if not total:
            return None
        return PostingService.post([Line(account_for("inventory"), debit=total, description=f"Transfer {tr.number} received"), Line(account_for("in_transit"), credit=total, description=f"Transfer {tr.number} received")], date_=timezone.localdate(), memo=f"Stock transfer {tr.number} received", source=tr)

    @staticmethod
    def post_trip_dispatch(trip, value):
        """Two-step dispatch setting: Stock in Transit / Stock in Hand."""
        if not value:
            return None
        return PostingService.post([Line(account_for("in_transit", "trip"), debit=value, description=f"Trip {trip.number} loaded"), Line(account_for("inventory", "trip"), credit=value, description=f"Trip {trip.number} loaded")], date_=trip.date, memo=f"Trip {trip.number} dispatched (stock to van)", source=trip)

    @staticmethod
    def post_trip_return(trip, value):
        if not value:
            return None
        return PostingService.post([Line(account_for("inventory", "trip"), debit=value, description=f"Trip {trip.number} returns"), Line(account_for("in_transit", "trip"), credit=value, description=f"Trip {trip.number} returns")], date_=timezone.localdate(), memo=f"Trip {trip.number} closed – returns to warehouse", source=trip)

    @staticmethod
    def post_opening_stock(warehouse, movement_ids):
        from apps.inventory.models import StockMovement

        total = sum((q4(m.qty * m.unit_cost) for m in StockMovement.objects.filter(id__in=movement_ids)), Decimal("0"))
        if not total:
            return None
        return PostingService.post([Line(account_for("inventory"), debit=total, description=f"Opening stock {warehouse.code}"), Line(account_for("opening_equity"), credit=total, description="Opening stock")], date_=timezone.localdate(), memo=f"Opening stock – {warehouse.name}", type_="opening")

    # ------------------------------------------------------------------ expenses
    @staticmethod
    def post_expense(exp):
        money = exp.paid_from_bank.account if exp.paid_from_bank_id else (exp.paid_from_cash.account if exp.paid_from_cash_id else None)
        credit_acct = money or Account.objects.filter(code="2-01-003-0001").first() or account_for("ap")
        lines = [Line(exp.category, debit=exp.amount, description=f"{exp.number} {exp.vendor}".strip(), cost_centre=exp.cost_centre)]
        if exp.tax_amount:
            acct = (exp.tax_type.input_account if exp.tax_type and exp.tax_type.input_account_id else None) or account_for("tax1_in")
            lines.append(Line(acct, debit=exp.tax_amount, description="Input tax on expense", tax_type=exp.tax_type))
        lines.append(Line(credit_acct, credit=exp.total, description=f"Expense {exp.number}"))
        je = PostingService.post(lines, date_=exp.date, memo=f"Expense {exp.number} – {exp.category.name}", source=exp)
        exp.journal = je
        exp.save(update_fields=["journal"])
        return je

    # ------------------------------------------------------------- opening bal
    @staticmethod
    @transaction.atomic
    def post_opening_balances(batch: OpeningBalanceBatch):
        if batch.posted:
            raise DomainError("Batch already posted.")
        lines = []
        for row in batch.lines:
            acct = Account.objects.get(pk=row["account"])
            lines.append(Line(acct, debit=row.get("debit", 0), credit=row.get("credit", 0), description="Opening balance", partner_type=row.get("partner_type", ""), partner_id=row.get("partner_id", "")))
        d = sum((l.debit for l in lines), Decimal("0"))
        c = sum((l.credit for l in lines), Decimal("0"))
        if d != c:
            eq = account_for("opening_equity")
            lines.append(Line(eq, debit=max(c - d, 0), credit=max(d - c, 0), description="Opening balance equity (balancing)"))
        je = PostingService.post(lines, date_=batch.date, memo=f"Opening balances {batch.number}", source=batch, type_="opening")
        batch.posted = True
        batch.journal = je
        batch.save(update_fields=["posted", "journal"])
        return je

    # ---------------------------------------------------------------- year end
    @staticmethod
    @transaction.atomic
    def close_fiscal_year(fy: FiscalYear):
        if fy.status == "closed":
            raise DomainError("Fiscal year already closed.")
        open_periods = fy.periods.filter(status="open")
        if open_periods.exists():
            raise DomainError("Close all periods before closing the year.")
        bal = JournalLine.objects.filter(entry__status="posted", entry__date__gte=fy.start, entry__date__lte=fy.end, account__type__in=["income", "expense"]).values("account").annotate(d=Sum("debit"), c=Sum("credit"))
        lines, net = [], Decimal("0")
        for row in bal:
            acct = Account.objects.get(pk=row["account"])
            balance = (row["d"] or 0) - (row["c"] or 0)  # debit-positive
            if balance == 0:
                continue
            lines.append(Line(acct, debit=max(-balance, 0), credit=max(balance, 0), description=f"Year-end close {fy.name}"))
            net += -balance  # income (credit) positive
        re = account_for("retained_earnings")
        lines.append(Line(re, debit=max(-net, 0), credit=max(net, 0), description=f"Profit/(loss) {fy.name}"))
        je = PostingService.post(lines, date_=fy.end, memo=f"Year-end closing {fy.name}", type_="closing")
        fy.status = "closed"
        fy.closed_by = get_current_user()
        fy.closing_entry = je
        fy.save()
        AuditService.log("fiscal_year.closed", fy, after={"net_profit": str(net), "journal": je.number if je else None})
        return je


class ChequeBookService:
    """
    The cheque books the business writes from, and which leaf a payment used.

    The point of keeping leaves as rows rather than just a running number is that paper does not behave like a
    counter: a leaf gets spoiled, one is written out of order, and the stub has to be accounted for when the
    bank asks. Every leaf here can be pointed at — issued, void, or still in the book.
    """

    MAX_LEAVES = 500  # a chequebook is 25, 50 or 100 leaves; this is a guard against a typo in the range

    @staticmethod
    @transaction.atomic
    def create(*, bank_account, first_no, last_no, prefix="", issued_on=None, notes=""):
        from .models import ChequeBook, ChequeLeaf

        first_no, last_no = int(first_no), int(last_no)
        if last_no < first_no:
            raise DomainError("The last cheque number cannot be before the first one.")
        count = last_no - first_no + 1
        if count > ChequeBookService.MAX_LEAVES:
            raise DomainError(f"That range is {count} cheques. A book holds at most {ChequeBookService.MAX_LEAVES} — check the numbers.")
        book = ChequeBook.objects.create(bank_account=bank_account, prefix=(prefix or "").strip(),
                                         first_no=first_no, last_no=last_no, issued_on=issued_on, notes=notes)
        width = len(str(last_no))
        ChequeLeaf.objects.bulk_create([
            ChequeLeaf(book=book, tenant_id=book.tenant_id, serial=n, number=f"{book.prefix}{str(n).zfill(width)}")
            for n in range(first_no, last_no + 1)
        ])
        AuditService.log("cheque_book.added", book, after={"bank": bank_account.name, "range": str(book), "leaves": count})
        return book

    @staticmethod
    def available(bank_account=None):
        """Leaves that can still be written, nearest number first — the next one to use is simply the first."""
        from .models import ChequeLeaf

        qs = ChequeLeaf.objects.filter(status="available", book__status="active").select_related("book", "book__bank_account")
        if bank_account is not None:
            qs = qs.filter(book__bank_account=bank_account)
        return qs.order_by("book__first_no", "serial")

    @staticmethod
    @transaction.atomic
    def take(payment):
        """
        Attach the leaf a payment was written on, and fill in the number when the user did not pick one.

        Only for money going **out**: a cheque received from a customer is their paper, with their numbering, and
        has nothing to do with our books. Silence is the right answer when no book exists — plenty of businesses
        write cheques without recording the book, and refusing the payment over it would be absurd.
        """
        from .models import ChequeLeaf

        if payment.direction != "out" or payment.method != "cheque" or payment.bank_account_id is None:
            return None
        leaf = None
        if payment.cheque_no:
            leaf = ChequeLeaf.objects.filter(number=payment.cheque_no, book__bank_account_id=payment.bank_account_id).first()
            if leaf is None:
                return None  # a number typed by hand that belongs to no book we hold
            if leaf.status == "issued" and leaf.payment_id and leaf.payment_id != payment.pk:
                raise DomainError(f"Cheque {leaf.number} was already written on {leaf.payment.number}.", code="cheque_used", status_code=400)
            if leaf.status == "void":
                raise DomainError(f"Cheque {leaf.number} is marked spoiled and cannot be written.", code="cheque_void", status_code=400)
        else:
            leaf = ChequeBookService.available(payment.bank_account).first()
            if leaf is None:
                return None
            payment.cheque_no = leaf.number
            payment.save(update_fields=["cheque_no"])
        leaf.status, leaf.payment = "issued", payment
        leaf.save(update_fields=["status", "payment"])
        ChequeBookService._close_if_done(leaf.book)
        return leaf

    @staticmethod
    @transaction.atomic
    def void(leaf, reason=""):
        if leaf.status == "issued":
            raise DomainError(f"Cheque {leaf.number} was written on {leaf.payment.number if leaf.payment_id else 'a payment'} — cancel that payment first.",
                              code="cheque_issued", status_code=400)
        leaf.status, leaf.void_reason = "void", (reason or "")[:200]
        leaf.save(update_fields=["status", "void_reason"])
        AuditService.log("cheque.voided", leaf.book, after={"cheque": leaf.number, "reason": leaf.void_reason})
        ChequeBookService._close_if_done(leaf.book)
        return leaf

    @staticmethod
    def _close_if_done(book):
        """A book with nothing left in it stops offering itself, without anybody having to remember."""
        if book.status == "active" and not book.leaves.filter(status="available").exists():
            book.status = "exhausted"
            book.save(update_fields=["status"])


class PaymentService:
    @staticmethod
    def _open_documents(partner_type, partner_id):
        if partner_type == "customer":
            from apps.sales.models import SalesInvoice

            return [(d, d.total - d.paid_amount, "sales_invoice") for d in SalesInvoice.objects.filter(customer_id=partner_id, status__in=["posted", "partially_paid", "overdue"]).order_by("date")]
        if partner_type == "supplier":
            from apps.purchasing.models import PurchaseBill

            return [(d, d.total - d.paid_amount, "purchase_bill") for d in PurchaseBill.objects.filter(supplier_id=partner_id, status__in=["posted", "partially_paid"]).order_by("due_date", "bill_date")]
        if partner_type == "company":
            from apps.sales.models import CompanyClaim

            return [(d, d.total - d.paid_amount, "company_claim") for d in CompanyClaim.objects.filter(supplier_id=partner_id, status__in=["posted", "partially_paid"]).order_by("date")]
        return []

    @staticmethod
    def _apply(document, doc_type, amount):
        document.paid_amount = (document.paid_amount or Decimal("0")) + amount
        if document.paid_amount >= document.total:
            document.status = "paid"
        elif document.paid_amount > 0:
            document.status = "partially_paid"
        else:
            document.status = "posted"
        document.save(update_fields=["paid_amount", "status", "updated_at"])

    @staticmethod
    @transaction.atomic
    def record(*, direction, partner_type, partner_id="", partner_name="", amount, method="cash", date_=None, bank_account=None, cash_account=None, allocations=None, cheque_no="", cheque_date=None, cheque_bank="", reference="", notes="", withholding_tax=None, withholding_amount=Decimal("0"), trip=None, delivery_note=None, collected_by=None, claim_deductions=None, other_account=None):
        amount = q4(amount)
        if amount <= 0:
            raise DomainError("Amount must be positive.")
        # Paying a manufacturer for purchases may deduct their outstanding discount-share claims: the bills are
        # settled for cash + deductions, the claims receivable is cleared for the deducted part (post_payment).
        deduction_total = Decimal("0")
        claim_allocs = []
        if claim_deductions:
            if direction != "out" or partner_type != "supplier":
                raise DomainError("Discount claims can only be deducted when paying a supplier.")
            if method == "cheque":
                raise DomainError("Claims cannot be deducted on a cheque payment — the bounce path cannot restore them. Pay by bank/cash, or settle the claim from its own page.")
            from apps.sales.models import CompanyClaim

            for cd in claim_deductions:
                claim = CompanyClaim.objects.filter(pk=cd.get("claim_id") or cd.get("claim")).first()
                if claim is None:
                    raise DomainError("Unknown discount claim.")
                if str(claim.supplier_id) != str(partner_id):
                    raise DomainError(f"Claim {claim.number} belongs to another company.")
                if claim.status not in ("posted", "partially_paid"):
                    raise DomainError(f"Claim {claim.number} is not open.")
                amt = q4(cd.get("amount") or (claim.total - claim.paid_amount))
                if amt <= 0:
                    continue
                deduction_total += amt
                claim_allocs.append({"document_type": "company_claim", "document_id": str(claim.pk), "amount": amt})
        if method in ("bank_transfer", "online") and bank_account is None:
            raise DomainError("Select a bank account for bank/online payments.")
        if method == "cash" and cash_account is None:
            from .models import CashAccount

            cash_account = CashAccount.objects.filter(is_petty=False).first()
        number = next_number("payment_in" if direction == "in" else "payment_out")
        if partner_type in ("customer", "supplier", "company") and partner_id and not partner_name:
            from apps.partners.models import Customer, Supplier

            obj = (Customer if partner_type == "customer" else Supplier).objects.filter(pk=partner_id).first()
            partner_name = obj.name if obj else ""
        pay = Payment.objects.create(
            number=number, direction=direction, partner_type=partner_type, partner_id=str(partner_id or ""), partner_name=partner_name, date=date_ or timezone.localdate(), amount=amount, method=method,
            bank_account=bank_account, cash_account=cash_account if method == "cash" else None, collected_by=collected_by or (get_current_user() if getattr(get_current_user(), "is_authenticated", False) else None),
            cheque_no=cheque_no, cheque_date=cheque_date, cheque_bank=cheque_bank, cheque_status="pending" if method == "cheque" else "", reference=reference, notes=notes,
            withholding_tax=withholding_tax, withholding_amount=q4(withholding_amount), trip=trip, delivery_note=delivery_note, status="posted", other_account=other_account if partner_type == "other" else None,
        )
        if method == "cheque":
            ChequeBookService.take(pay)
        remaining = amount + deduction_total  # deducted claims settle bills just like cash does
        allocations = allocations or []
        if not allocations and partner_type in ("customer", "supplier") and partner_id:
            # FIFO by default (§6.1)
            for doc, balance, dtype in PaymentService._open_documents(partner_type, partner_id):
                if remaining <= 0:
                    break
                take = min(balance, remaining)
                if take <= 0:
                    continue
                allocations.append({"document_type": dtype, "document_id": str(doc.pk), "amount": take})
                remaining -= take
        allocations = allocations + claim_allocs
        remaining = amount + deduction_total
        for a in allocations:
            amt = q4(a["amount"])
            if amt <= 0:
                continue
            doc = PaymentService._load(a["document_type"], a["document_id"])
            balance = doc.total - doc.paid_amount
            if amt > balance + Decimal("0.01"):
                raise DomainError(f"Allocation {amt} exceeds balance {balance} of {doc.number}.")
            PaymentAllocation.objects.create(payment=pay, document_type=a["document_type"], document_id=str(doc.pk), document_number=doc.number, amount=amt)
            PaymentService._apply(doc, a["document_type"], amt)
            remaining -= amt
        pay.unallocated = max(remaining, Decimal("0"))
        pay.journal = PostingService.post_payment(pay)
        pay.save(update_fields=["unallocated", "journal"])
        AuditService.log("payment.recorded", pay, after={"amount": str(amount), "method": method, "partner": partner_name, "allocations": len(allocations)})
        from apps.core.events import emit

        emit("payment.received" if direction == "in" else "payment.made", payment=pay)
        return pay

    @staticmethod
    def _load(doc_type, doc_id):
        if doc_type == "sales_invoice":
            from apps.sales.models import SalesInvoice

            return SalesInvoice.objects.get(pk=doc_id)
        if doc_type == "purchase_bill":
            from apps.purchasing.models import PurchaseBill

            return PurchaseBill.objects.get(pk=doc_id)
        if doc_type == "company_claim":
            from apps.sales.models import CompanyClaim

            return CompanyClaim.objects.get(pk=doc_id)
        raise DomainError(f"Unknown document type {doc_type}")

    @staticmethod
    @transaction.atomic
    def allocate(payment, allocations):
        remaining = payment.unallocated
        for a in allocations:
            amt = q4(a["amount"])
            if amt > remaining:
                raise DomainError("Allocation exceeds unallocated amount.")
            doc = PaymentService._load(a["document_type"], a["document_id"])
            if amt > doc.total - doc.paid_amount + Decimal("0.01"):
                raise DomainError(f"Allocation exceeds balance of {doc.number}.")
            PaymentAllocation.objects.create(payment=payment, document_type=a["document_type"], document_id=str(doc.pk), document_number=doc.number, amount=amt)
            PaymentService._apply(doc, a["document_type"], amt)
            remaining -= amt
        payment.unallocated = remaining
        payment.save(update_fields=["unallocated"])
        return payment

    @staticmethod
    @transaction.atomic
    def cancel(payment, reason=""):
        if payment.status == "cancelled":
            raise DomainError("Already cancelled.")
        for a in payment.allocations.all():
            doc = PaymentService._load(a.document_type, a.document_id)
            PaymentService._apply(doc, a.document_type, -a.amount)
        if payment.journal_id:
            PostingService.reverse(payment.journal, memo=f"Cancelled {payment.number}: {reason}")
        payment.status = "cancelled"
        payment.notes = (payment.notes + f"\nCancelled: {reason}").strip()
        payment.save(update_fields=["status", "notes", "updated_at"])
        AuditService.log("payment.cancelled", payment, after={"reason": reason})
        return payment

    @staticmethod
    @transaction.atomic
    def cheque_event(payment, event, bank_account=None, charges=Decimal("0")):
        if payment.method != "cheque":
            raise DomainError("Not a cheque payment.")
        order = ["pending", "deposited", "cleared"]
        if event == "deposited" and payment.cheque_status != "pending":
            raise DomainError("Cheque is not pending.")
        if event == "cleared" and payment.cheque_status not in ("deposited", "pending"):
            raise DomainError("Cheque must be deposited first.")
        if event == "bounced" and payment.cheque_status in ("cleared", "bounced"):
            raise DomainError("Cheque already cleared/bounced.")
        je = PostingService.post_cheque_event(payment, event, bank_account, charges)
        if event == "deposited" and bank_account is not None:
            payment.bank_account = bank_account
        payment.cheque_status = event
        payment.save(update_fields=["cheque_status", "bank_account", "updated_at"])
        if event == "bounced":
            for a in payment.allocations.all():
                doc = PaymentService._load(a.document_type, a.document_id)
                PaymentService._apply(doc, a.document_type, -a.amount)
            payment.allocations.all().delete()
            payment.unallocated = Decimal("0")
            payment.save(update_fields=["unallocated"])
        AuditService.log(f"cheque.{event}", payment, after={"journal": je.number if je else None})
        return payment


class ExpenseService:
    @staticmethod
    @transaction.atomic
    def submit(exp):
        if exp.status != "draft":
            raise DomainError("Only drafts can be submitted.")
        from apps.accounts.models import TenantMembership

        user = get_current_user()
        m = TenantMembership.objects.filter(user=user, tenant=exp.tenant).first() if getattr(user, "is_authenticated", False) else None
        if m and m.has_perm("cash.approve_expense"):
            limit = m.approval_limit("expense")
            if limit is None or exp.total <= Decimal(str(limit)):
                return ExpenseService.approve(exp, force=True)
        exp.status = "submitted"
        exp.save(update_fields=["status", "updated_at"])
        from apps.core.notifications import notify_permission

        notify_permission("cash.approve_expense", "approval.pending", f"Expense {exp.number} needs approval", f"{exp.category.name} · {exp.total:.2f}", {"expense_id": str(exp.id)})
        return exp

    @staticmethod
    @transaction.atomic
    def approve(exp, force=False):
        if exp.status not in ("draft", "submitted"):
            raise DomainError("Expense cannot be approved from this status.")
        exp.status = "approved"
        exp.approved_by = get_current_user() if getattr(get_current_user(), "is_authenticated", False) else None
        exp.save(update_fields=["status", "approved_by", "updated_at"])
        if exp.paid_from_bank_id or exp.paid_from_cash_id:
            exp.status = "paid"
            exp.save(update_fields=["status"])
        PostingService.post_expense(exp)
        AuditService.log("status.approved", exp, after={"status": exp.status, "total": str(exp.total)})
        return exp

    @staticmethod
    @transaction.atomic
    def pay(exp, bank_account=None, cash_account=None):
        if exp.status != "approved":
            raise DomainError("Expense must be approved first.")
        if exp.journal_id:
            PostingService.reverse(exp.journal, memo=f"Re-post {exp.number} on payment")
        exp.paid_from_bank, exp.paid_from_cash = bank_account, cash_account if bank_account is None else None
        exp.status = "paid"
        exp.save()
        PostingService.post_expense(exp)
        return exp

    @staticmethod
    def reject(exp, reason=""):
        exp.status = "rejected"
        exp.notes = (exp.notes + f"\nRejected: {reason}").strip()
        exp.save(update_fields=["status", "notes", "updated_at"])
        return exp


class PeriodService:
    @staticmethod
    def checks(period):
        """Pre-close checks (§6.8)."""
        from apps.inventory.models import StockLevel
        from apps.purchasing.models import GoodsReceipt
        from apps.sales.models import RepCollection, SalesInvoice

        issues = []
        n = GoodsReceipt.objects.filter(status="draft", date__lte=period.end).count()
        if n:
            issues.append({"level": "error", "message": f"{n} unposted goods receipt(s) dated on/before {period.end}."})
        n = SalesInvoice.objects.filter(status="draft", date__lte=period.end).count()
        if n:
            issues.append({"level": "error", "message": f"{n} draft invoice(s) dated on/before {period.end}."})
        n = JournalEntry.objects.filter(status="draft", date__lte=period.end).count()
        if n:
            issues.append({"level": "warning", "message": f"{n} draft journal(s) not posted."})
        from .models import BankStatementLine

        n = BankStatementLine.objects.filter(reconciled=False, date__lte=period.end).count()
        if n:
            issues.append({"level": "warning", "message": f"{n} bank statement line(s) unreconciled."})
        inv_value = sum((lv.qty_on_hand * (lv.variant.avg_cost or lv.variant.cost_price) for lv in StockLevel.objects.filter(qty_on_hand__gt=0).select_related("variant")), Decimal("0"))
        gl_inv = ReportQueries.account_balance(account_for("inventory"), period.end)
        if abs(inv_value - gl_inv) > Decimal("1"):
            issues.append({"level": "warning", "message": f"Inventory value {inv_value:.2f} ≠ GL stock account {gl_inv:.2f}."})
        days = get_current_tenant().setting("rep_unsettled_alert_days")
        n = RepCollection.objects.filter(status="held_by_rep", date__lte=period.end - timezone.timedelta(days=days or 3)).count()
        if n:
            issues.append({"level": "warning", "message": f"{n} rep collection(s) unsettled for more than {days} days."})
        return issues

    @staticmethod
    @transaction.atomic
    def close(period, force=False):
        if period.status == "closed":
            raise DomainError("Period already closed.")
        issues = PeriodService.checks(period)
        if any(i["level"] == "error" for i in issues) and not force:
            raise DomainError("Period cannot be closed.", errors={"issues": issues})
        period.status = "closed"
        period.closed_by = get_current_user()
        period.closed_at = timezone.now()
        period.save()
        AuditService.log("period.closed", period, after={"issues": issues})
        return period

    @staticmethod
    def reopen(period, reason=""):
        if period.fiscal_year.status == "closed":
            raise DomainError("Fiscal year is closed.")
        period.status = "open"
        period.save(update_fields=["status"])
        AuditService.log("period.reopened", period, after={"reason": reason})
        return period


class ReconciliationService:
    @staticmethod
    @transaction.atomic
    def import_statement(bank_account, rows):
        from .models import BankStatementLine

        created = []
        for r in rows:
            created.append(BankStatementLine.objects.create(bank_account=bank_account, date=r["date"], description=r.get("description", ""), reference=r.get("reference", ""), amount=q4(r["amount"])))
        return created

    @staticmethod
    def auto_match(bank_account, days_tolerance=3):
        from .models import BankStatementLine

        matched = 0
        for sl in BankStatementLine.objects.filter(bank_account=bank_account, reconciled=False):
            cand = JournalLine.objects.filter(account=bank_account.account, entry__status="posted", entry__date__gte=sl.date - timezone.timedelta(days=days_tolerance), entry__date__lte=sl.date + timezone.timedelta(days=days_tolerance)).exclude(id__in=BankStatementLine.objects.filter(matched_line__isnull=False).values_list("matched_line_id", flat=True))
            amt = sl.amount
            cand = cand.filter(debit=amt) if amt > 0 else cand.filter(credit=-amt)
            if sl.reference:
                pref = cand.filter(entry__memo__icontains=sl.reference) | cand.filter(description__icontains=sl.reference)
                if pref.exists():
                    cand = pref
            line = cand.first()
            if line:
                sl.matched_line, sl.reconciled, sl.reconciled_at = line, True, timezone.now()
                sl.save()
                matched += 1
        return matched

    @staticmethod
    def report(bank_account, as_of):
        from .models import BankStatementLine

        gl = ReportQueries.account_balance(bank_account.account, as_of) + bank_account.opening_balance
        unrec = BankStatementLine.objects.filter(bank_account=bank_account, reconciled=False, date__lte=as_of)
        unrec_total = unrec.aggregate(s=Sum("amount"))["s"] or Decimal("0")
        return {"gl_balance": str(gl), "unreconciled_statement_total": str(unrec_total), "unreconciled_count": unrec.count(), "statement_balance_estimate": str(gl - unrec_total)}


class ReportQueries:
    @staticmethod
    def account_balance(account, as_of=None, from_date=None):
        qs = JournalLine.objects.filter(account=account, entry__status="posted")
        if as_of:
            qs = qs.filter(entry__date__lte=as_of)
        if from_date:
            qs = qs.filter(entry__date__gte=from_date)
        agg = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
        return (agg["d"] or Decimal("0")) - (agg["c"] or Decimal("0")) + (account.opening_balance if not from_date else Decimal("0"))
