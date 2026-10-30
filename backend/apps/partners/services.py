from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.exceptions import DomainError
from apps.core.audit import AuditService
from apps.core.numbering import next_number
from apps.core.plan_limits import PlanLimitService

from .models import Customer, Supplier, SupplierRep


class PartnerLedgerService:
    """Customer / supplier / rep ledgers are views over JournalLine filtered by partner (§8.2)."""

    @staticmethod
    def ledger(partner_type, partner_id, date_from=None, date_to=None):
        from apps.accounting.models import GL_LIVE, JournalLine

        qs = JournalLine.objects.filter(partner_type=partner_type, partner_id=str(partner_id), entry__status__in=GL_LIVE).select_related("entry", "account").order_by("entry__date", "entry__created_at", "id")
        opening = Decimal("0")
        if date_from:
            opening_qs = qs.filter(entry__date__lt=date_from)
            agg = opening_qs.aggregate(d=Sum("debit"), c=Sum("credit"))
            opening = (agg["d"] or 0) - (agg["c"] or 0)
            qs = qs.filter(entry__date__gte=date_from)
        if date_to:
            qs = qs.filter(entry__date__lte=date_to)
        rows, balance = [], opening
        for line in qs:
            balance += line.debit - line.credit
            rows.append({
                "date": line.entry.date, "journal": line.entry.number, "journal_id": str(line.entry_id), "source_type": line.entry.source_type, "source_id": line.entry.source_id,
                "description": line.description or line.entry.memo, "account": line.account.name, "debit": str(line.debit), "credit": str(line.credit), "balance": str(balance),
            })
        return {"opening_balance": str(opening), "rows": rows, "closing_balance": str(balance)}

    @staticmethod
    def balance(partner_type, partner_id):
        from apps.accounting.models import GL_LIVE, JournalLine

        agg = JournalLine.objects.filter(partner_type=partner_type, partner_id=str(partner_id), entry__status__in=GL_LIVE).aggregate(d=Sum("debit"), c=Sum("credit"))
        return (agg["d"] or Decimal("0")) - (agg["c"] or Decimal("0"))


class CustomerService:
    @staticmethod
    def outstanding(customer):
        """Unpaid invoices plus whatever was still owed when the company started using DMS (the opening balance)."""
        from apps.sales.models import SalesInvoice

        inv = SalesInvoice.objects.filter(customer=customer, status__in=["posted", "partially_paid", "overdue"]).aggregate(t=Sum("total"), p=Sum("paid_amount"))
        invoiced = (inv["t"] or Decimal("0")) - (inv["p"] or Decimal("0"))
        opening = Decimal(customer.opening_balance or 0)
        if opening:  # payments against the opening balance land on the ledger, so take what is left of it
            from apps.accounting.models import GL_LIVE, JournalLine

            paid = JournalLine.objects.filter(partner_type="customer", partner_id=str(customer.id), entry__status__in=GL_LIVE, entry__type="payment").aggregate(c=Sum("credit"))["c"] or Decimal("0")
            opening = max(Decimal("0"), opening - max(Decimal("0"), paid - invoiced))
        # credit kept from returns is theirs to use: it brings what they owe down, below zero when we owe them
        return invoiced + opening - CustomerService.credit(customer)

    @staticmethod
    def credit(customer):
        """Credit the customer holds from returns (owner, 30 Oct 2026) — posted returns not yet applied or refunded."""
        from django.db.models import F

        from apps.sales.models import CreditNote

        left = CreditNote.objects.filter(customer=customer, status="posted").aggregate(
            c=Sum(F("total") - F("applied_amount") - F("refunded_amount")))["c"] or Decimal("0")
        return max(left, Decimal("0"))

    @staticmethod
    def overdue(customer):
        from apps.sales.models import SalesInvoice

        today = timezone.localdate()
        inv = SalesInvoice.objects.filter(customer=customer, status__in=["posted", "partially_paid", "overdue"], due_date__lt=today).aggregate(t=Sum("total"), p=Sum("paid_amount"))
        return (inv["t"] or Decimal("0")) - (inv["p"] or Decimal("0"))

    @staticmethod
    def credit_check(customer, new_amount=Decimal("0")):
        """Returns (ok, reason). Policy: setting credit_policy hard|warn."""
        if customer.is_blocked or customer.status == "blocked":
            return False, f"Customer is blocked: {customer.blocked_reason or 'no reason given'}"
        outstanding = CustomerService.outstanding(customer)
        if customer.credit_limit and outstanding + new_amount > customer.credit_limit:
            return False, f"Credit limit exceeded: outstanding {outstanding:.2f} + order {new_amount:.2f} > limit {customer.credit_limit:.2f}"
        if customer.credit_days:
            overdue = CustomerService.overdue(customer)
            if overdue > 0:
                return False, f"Customer has overdue invoices ({overdue:.2f})"
        return True, ""

    @staticmethod
    def ageing(customer=None, as_of=None):
        from apps.sales.models import SalesInvoice

        as_of = as_of or timezone.localdate()
        buckets = {"current": Decimal("0"), "1-30": Decimal("0"), "31-60": Decimal("0"), "61-90": Decimal("0"), "90+": Decimal("0")}
        qs = SalesInvoice.objects.filter(status__in=["posted", "partially_paid", "overdue"])
        if customer is not None:
            qs = qs.filter(customer=customer)
        for inv in qs:
            bal = inv.total - inv.paid_amount
            days = (as_of - (inv.due_date or inv.date)).days
            key = "current" if days <= 0 else "1-30" if days <= 30 else "31-60" if days <= 60 else "61-90" if days <= 90 else "90+"
            buckets[key] += bal
        buckets["total"] = sum(buckets.values(), Decimal("0"))
        return {k: str(v) for k, v in buckets.items()}

    @staticmethod
    @transaction.atomic
    def create(data, rep=None):
        if not data.get("code"):
            data["code"] = next_number("customer", unique_for=(Customer, "code"))
        if rep is not None:
            data["requested_by_rep"] = rep
            data["status"] = "pending" if rep.tenant.setting("customer_requests_need_approval") else "active"
        opening = Decimal(str(data.pop("opening_balance", 0) or 0))
        opening_date = data.pop("opening_balance_date", None)
        c = Customer.objects.create(**data)
        if rep is not None:
            c.assigned_reps.add(rep)
        if opening:
            PartnerOpeningBalance.set(c, opening, as_of=opening_date)
        return c


class PartnerOpeningBalance:
    """
    Opening balances posted properly: one journal per partner (Dr receivable / Cr opening equity for a customer,
    the mirror for a supplier) so the amount shows in their statement, in ageing and in the trial balance.
    Re-saving a different amount replaces the previous entry instead of stacking a second one.
    """

    @staticmethod
    @transaction.atomic
    def set(partner, amount, *, as_of=None, actor=None):
        from apps.accounting.services import Line, PostingService, account_for

        kind = "customer" if partner.__class__.__name__ == "Customer" else "supplier"
        amount = Decimal(str(amount or 0))
        as_of = as_of or partner.opening_balance_date or timezone.localdate()
        old_entry = partner.opening_journal
        if old_entry is not None and old_entry.status == "posted":  # replace: reverse the previous opening entry
            from apps.accounting.services import PostingService as _PS

            _PS.reverse(old_entry, memo=f"Opening balance corrected — {partner.name}")
            partner.opening_journal = None
        entry = None
        if amount:
            control = partner.account or account_for("ar" if kind == "customer" else "ap")
            equity = account_for("opening_equity")
            memo = f"Opening balance — {partner.name}"
            if kind == "customer":
                lines = [Line(control, debit=amount, description=memo, partner_type="customer", partner_id=partner.id), Line(equity, credit=amount, description=memo)]
            else:
                lines = [Line(equity, debit=amount, description=memo), Line(control, credit=amount, description=memo, partner_type="supplier", partner_id=partner.id)]
            entry = PostingService.post(lines, date_=as_of, memo=memo, type_="opening")
        partner.opening_balance = amount
        partner.opening_balance_date = as_of if amount else None
        partner.opening_journal = entry
        partner.save(update_fields=["opening_balance", "opening_balance_date", "opening_journal"])
        AuditService.log("partner.opening_balance", partner, after={"amount": str(amount), "as_of": str(as_of), "journal": entry.number if entry else ""}, actor=actor)
        return partner


class SupplierService:
    @staticmethod
    def outstanding(supplier):
        """Unpaid bills plus whatever was still owed to them when the company started using DMS."""
        from apps.purchasing.models import PurchaseBill

        agg = PurchaseBill.objects.filter(supplier=supplier, status__in=["posted", "partially_paid"]).aggregate(t=Sum("total"), p=Sum("paid_amount"))
        billed = (agg["t"] or Decimal("0")) - (agg["p"] or Decimal("0"))
        opening = Decimal(supplier.opening_balance or 0)
        if opening:
            from apps.accounting.models import GL_LIVE, JournalLine

            paid = JournalLine.objects.filter(partner_type="supplier", partner_id=str(supplier.id), entry__status__in=GL_LIVE, entry__type="payment").aggregate(d=Sum("debit"))["d"] or Decimal("0")
            opening = max(Decimal("0"), opening - max(Decimal("0"), paid - billed))
        return billed + opening

    @staticmethod
    @transaction.atomic
    def create_rep(supplier, *, name, code=None, phone="", email="", territory="", role=None, create_login=False, password=None, monthly_target=0, territories=(), send_invite=True):
        from apps.accounts.services import UserService

        tenant = supplier.tenant
        PlanLimitService.check(tenant, "max_reps")
        rep = SupplierRep.objects.create(supplier=supplier, code=code or next_number("supplier_rep", unique_for=(SupplierRep, "code")), name=name, phone=phone, email=email, territory=territory, role=role, monthly_target=monthly_target)
        if territories:
            rep.territories.set(territories)
        if create_login:
            if role is None or role.role_type != "external":
                raise DomainError("Select an external role for the rep login.")
            membership = UserService.create_tenant_user(tenant, name, email=email or None, phone=phone or None, password=password, roles=[role], user_type="EXTERNAL", send_invite=send_invite and not password, supplier_rep=rep)
            rep.user = membership.user
            rep.save(update_fields=["user"])
        return rep

    @staticmethod
    @transaction.atomic
    def update_rep(rep, **data):
        from apps.accounts.models import TenantMembership

        role = data.pop("role", "__unset__")
        territories = data.pop("territories", None)
        for k, v in data.items():
            setattr(rep, k, v)
        if role != "__unset__":
            rep.role = role
            if rep.user_id:
                m = TenantMembership.objects.filter(user=rep.user, tenant=rep.tenant).first()
                if m is not None:
                    m.groups.set([role.group] if role else [])
        rep.save()
        if territories is not None:
            rep.territories.set(territories)
        return rep

    @staticmethod
    def performance(rep, date_from=None, date_to=None):
        from apps.sales.models import RepCollection, SalesOrder

        qs = SalesOrder.objects.filter(rep=rep)
        if date_from:
            qs = qs.filter(order_date__gte=date_from)
        if date_to:
            qs = qs.filter(order_date__lte=date_to)
        # (the count is taken below; summing the uuid primary key was a 500 waiting to happen)
        agg = qs.exclude(status__in=["cancelled", "rejected"]).aggregate(total=Sum("total"))
        delivered = qs.filter(status__in=["delivered", "invoiced"]).aggregate(total=Sum("total"))["total"] or 0
        held = RepCollection.objects.filter(rep=rep, status="held_by_rep").aggregate(t=Sum("amount"))["t"] or 0
        return {"orders": qs.exclude(status__in=["cancelled", "rejected"]).count(), "order_value": str(agg["total"] or 0), "delivered_value": str(delivered), "rejected": qs.filter(status="rejected").count(), "cash_held": str(held), "target": str(rep.monthly_target)}
