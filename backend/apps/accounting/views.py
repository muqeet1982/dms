from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import AuditService
from apps.accounts.page_permissions import extra_perm, page_perms
from apps.core.exceptions import DomainError
from apps.core.numbering import next_number
from apps.core.permissions import IsTenantUser, require_perm
from apps.core.viewsets import DocumentViewSet, TenantModelViewSet

from .coa_template import ensure_fiscal_year
from .models import Account, AccountingPeriod, BankAccount, BankStatementLine, CashAccount, ChequeBook, ChequeLeaf, CostCentre, Expense, FiscalYear, JournalEntry, JournalLine, OpeningBalanceBatch, Payment, PostingRule
from .serializers import (
    AccountSerializer, AccountingPeriodSerializer, BankAccountSerializer, BankStatementLineSerializer, CashAccountSerializer,
    ChequeBookSerializer, ChequeLeafSerializer, CostCentreSerializer, ExpenseSerializer,
    FiscalYearSerializer, JournalEntrySerializer, OpeningBalanceBatchSerializer, PaymentSerializer, PostingRuleSerializer,
)
from .services import ChequeBookService, ExpenseService, PaymentService, PeriodService, PostingService, ReconciliationService, ReportQueries


class AccountViewSet(TenantModelViewSet):
    serializer_class = AccountSerializer
    perm_map = page_perms("accounts", {"tree": "accounting.view_account", "ledger": "accounting.view_account"})
    filterset_fields = ["type", "level", "is_group", "is_active", "parent", "subtype"]
    search_fields = ["code", "name"]
    pagination_class = None
    queryset = Account.objects.select_related("parent")

    def perform_destroy(self, instance):
        if instance.is_system:
            raise DomainError("System accounts cannot be deleted.")
        if instance.lines.exists() or instance.children.exists():
            raise DomainError("Account has postings or children; deactivate it instead.")
        instance.delete()

    @action(detail=False, methods=["get"])
    def tree(self, request):
        accounts = list(Account.objects.all().order_by("code"))
        by_parent = {}
        for a in accounts:
            by_parent.setdefault(a.parent_id, []).append(a)

        def build(parent_id):
            return [{**AccountSerializer(a, context={"view": self}).data, "children": build(a.id)} for a in by_parent.get(parent_id, [])]

        return Response(build(None))

    @action(detail=True, methods=["get"])
    def ledger(self, request, pk=None):
        from .reports import general_ledger

        return Response(general_ledger(account=pk, date_from=request.query_params.get("date_from"), date_to=request.query_params.get("date_to")))


class JournalEntryViewSet(DocumentViewSet):
    serializer_class = JournalEntrySerializer
    perm_map = page_perms("journals", {
        "post": extra_perm("journals", "post"),
        "reverse": extra_perm("journals", "reverse"),
        "print": "accounting.view_journal",
        "pdf": "accounting.view_journal",
    })
    filterset_fields = ["status", "type", "voucher_type", "source_type", "period", "date"]
    search_fields = ["number", "memo", "source_number", "lines__description"]
    ordering_fields = ["date", "number", "created_at", "total_debit"]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = JournalEntry.objects.select_related("posted_by", "created_by", "reversed_by_entry").prefetch_related("lines__account", "lines__cost_centre")
        p = self.request.query_params
        if p.get("date_from"):
            qs = qs.filter(date__gte=p["date_from"])
        if p.get("date_to"):
            qs = qs.filter(date__lte=p["date_to"])
        if p.get("account"):
            qs = qs.filter(lines__account_id=p["account"])
        return qs.distinct()

    def perform_create(self, serializer):
        d = serializer.validated_data
        lines = d.pop("lines")
        vt = str(self.request.data.get("voucher_type") or d.get("voucher_type") or "jv").lower()
        if vt not in dict(JournalEntry.VOUCHER_TYPES):
            raise DomainError(f"Unknown voucher type {vt!r}.")
        # each voucher family numbers its own series (CPV-00001 …); plain JV shares the journal series
        entry = JournalEntry.objects.create(number=next_number("journal" if vt == "jv" else f"journal_{vt}"), date=d.get("date", timezone.localdate()), type="manual", voucher_type=vt, memo=d.get("memo", ""), notes=d.get("notes", ""), status="draft")
        self._write_lines(entry, lines)
        serializer.instance = entry
        AuditService.log("journal.created", entry, request=self.request)
        if self.request.data.get("post") and self.request.membership.has_perm("accounting.post_journal"):
            PostingService.post_draft(entry)

    def _write_lines(self, entry, lines):
        from .models import ChequeLeaf

        # a draft's cheques are released first and re-claimed from the new lines, so a removed cheque
        # goes back to Available (the guide's §7.5 behaviour)
        held = set(entry.lines.exclude(cheque_leaf=None).values_list("cheque_leaf_id", flat=True))
        if held:
            ChequeLeaf.objects.filter(pk__in=held, status="issued", payment=None).update(status="available")
        entry.lines.all().delete()
        d = c = Decimal("0")
        seen_leaves = set()
        for i, l in enumerate(lines):
            leaf = l.get("cheque_leaf")
            if leaf is not None:
                leaf = ChequeLeaf.objects.select_for_update().get(pk=leaf.pk if hasattr(leaf, "pk") else leaf)
                account = l.get("account")
                if leaf.book.bank_account.account_id != (account.pk if hasattr(account, "pk") else account):
                    raise DomainError(f"Cheque {leaf.number} belongs to {leaf.book.bank_account.name}, not this account.")
                if leaf.pk in seen_leaves:
                    raise DomainError(f"Cheque {leaf.number} is on two lines.")
                if leaf.status != "available" and leaf.pk not in held:
                    raise DomainError(f"Cheque {leaf.number} is no longer available — pick another.")
                seen_leaves.add(leaf.pk)
                leaf.status = "issued"
                leaf.save(update_fields=["status"])
                l = {**l, "cheque_leaf": leaf}
            JournalLine.objects.create(entry=entry, sequence=i, **{k: v for k, v in l.items() if k != "sequence"})
            d += l.get("debit", 0) or 0
            c += l.get("credit", 0) or 0
        entry.total_debit, entry.total_credit = d, c
        entry.save(update_fields=["total_debit", "total_credit"])

    def perform_update(self, serializer):
        entry = serializer.instance
        if entry.status != "draft":
            raise DomainError("Posted journals cannot be edited; reverse them instead.")
        lines = serializer.validated_data.pop("lines", None)
        super().perform_update(serializer)
        if lines is not None:
            self._write_lines(entry, lines)

    def perform_destroy(self, instance):
        if instance.status != "draft":
            raise DomainError("Only draft journals can be deleted.")
        from .models import ChequeLeaf

        held = set(instance.lines.exclude(cheque_leaf=None).values_list("cheque_leaf_id", flat=True))
        if held:
            ChequeLeaf.objects.filter(pk__in=held, status="issued", payment=None).update(status="available")
        super().perform_destroy(instance)

    @action(detail=True, methods=["post"])
    @require_perm("accounting.post_journal")
    def post(self, request, pk=None):
        return Response(JournalEntrySerializer(PostingService.post_draft(self.get_object())).data)

    @action(detail=True, methods=["post"])
    @require_perm("accounting.reverse_journal")
    def reverse(self, request, pk=None):
        rev = PostingService.reverse(self.get_object(), request.data.get("date"), request.data.get("memo"))
        return Response(JournalEntrySerializer(rev).data, status=201)

    @action(detail=True, methods=["get"])
    def print(self, request, pk=None):
        from apps.core.documents import render_doc

        from .vouchers import signature_boxes, voucher_code, voucher_title

        e = self.get_object()
        lines = list(e.lines.select_related("account", "cheque_leaf"))
        code = voucher_code(e, lines)
        return render_doc(request, "accounting/voucher.html",
                          {"e": e, "lines": lines, "voucher_code": code, "voucher_title": voucher_title(code), "signatures": signature_boxes(code)},
                          f"{e.number}.pdf", title=voucher_title(code), ref=e.number)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        # every other document answers /pdf; the voucher print is the same document
        return self.print(request, pk=pk)


class FiscalYearViewSet(TenantModelViewSet):
    serializer_class = FiscalYearSerializer
    perm_map = page_perms("periods", {"close": "accounting.change_period", "checks": "accounting.view_period"})
    pagination_class = None
    queryset = FiscalYear.objects.all()
    http_method_names = ["get", "post", "head", "options"]

    def list(self, request, *args, **kwargs):
        ensure_fiscal_year(request.tenant, timezone.localdate())
        return super().list(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        je = PostingService.close_fiscal_year(self.get_object())
        return Response({"closing_entry": je.number if je else None})


class PeriodViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsTenantUser]
    serializer_class = AccountingPeriodSerializer
    perm_map = page_perms("periods", {"close": "accounting.change_period", "reopen": "accounting.change_period"})
    pagination_class = None
    filterset_fields = ["fiscal_year", "status"]

    def get_queryset(self):
        ensure_fiscal_year(self.request.tenant, timezone.localdate())
        return AccountingPeriod.objects.select_related("fiscal_year")

    @action(detail=True, methods=["get"])
    def checks(self, request, pk=None):
        return Response(PeriodService.checks(self.get_object()))

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        return Response(AccountingPeriodSerializer(PeriodService.close(self.get_object(), force=bool(request.data.get("force")))).data)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        return Response(AccountingPeriodSerializer(PeriodService.reopen(self.get_object(), request.data.get("reason", ""))).data)


class CostCentreViewSet(TenantModelViewSet):
    serializer_class = CostCentreSerializer
    perm_map = page_perms("accounts")
    pagination_class = None
    queryset = CostCentre.objects.all()


class BankAccountViewSet(TenantModelViewSet):
    serializer_class = BankAccountSerializer
    perm_map = page_perms("bank_cash", {
        "import_statement": "accounting.add_reconciliation",
        "statement": "accounting.view_reconciliation",
        "reconciliation": "accounting.view_reconciliation",
        "match": "accounting.add_reconciliation",
    })
    pagination_class = None
    queryset = BankAccount.objects.select_related("account")

    def perform_create(self, serializer):
        if not serializer.validated_data.get("account"):
            group = Account.objects.filter(code="1-01-002").first()
            n = Account.objects.filter(parent=group).count() + 1
            acct = Account.objects.create(code=f"1-01-002-{n:04d}", name=serializer.validated_data["name"], level=4, parent=group, type="asset", subtype="bank", is_group=False, currency=self.request.tenant.base_currency)
            serializer.validated_data["account"] = acct
        super().perform_create(serializer)

    @action(detail=True, methods=["post"], url_path="statement/import")
    def import_statement(self, request, pk=None):
        import csv
        import io

        ba = self.get_object()
        rows = request.data.get("rows")
        if rows is None and request.FILES.get("file"):
            text = request.FILES["file"].read().decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
            rows = []
            for r in reader:
                amt = Decimal(str(r.get("amount") or 0) or 0)
                if not amt and (r.get("credit") or r.get("debit")):
                    amt = Decimal(str(r.get("credit") or 0) or 0) - Decimal(str(r.get("debit") or 0) or 0)
                rows.append({"date": r.get("date"), "description": r.get("description", ""), "reference": r.get("reference", ""), "amount": amt})
        created = ReconciliationService.import_statement(ba, rows or [])
        matched = ReconciliationService.auto_match(ba)
        return Response({"imported": len(created), "auto_matched": matched})

    @action(detail=True, methods=["get"])
    def statement(self, request, pk=None):
        qs = BankStatementLine.objects.filter(bank_account_id=pk)
        if request.query_params.get("unreconciled") == "1":
            qs = qs.filter(reconciled=False)
        return Response(BankStatementLineSerializer(qs[:1000], many=True).data)

    @action(detail=True, methods=["get"])
    def reconciliation(self, request, pk=None):
        as_of = request.query_params.get("as_of") or timezone.localdate()
        ba = self.get_object()
        return Response({**ReconciliationService.report(ba, as_of), "unmatched_gl_lines": [{"id": str(l.id), "date": l.entry.date, "journal": l.entry.number, "description": l.description or l.entry.memo, "debit": str(l.debit), "credit": str(l.credit)} for l in JournalLine.objects.filter(account=ba.account, entry__status="posted").exclude(id__in=BankStatementLine.objects.filter(matched_line__isnull=False).values_list("matched_line_id", flat=True)).select_related("entry")[:500]]})

    @action(detail=True, methods=["post"], url_path=r"statement/(?P<line_id>[^/.]+)/match")
    def match(self, request, pk=None, line_id=None):
        sl = BankStatementLine.objects.get(pk=line_id, bank_account_id=pk)
        jl = JournalLine.objects.filter(pk=request.data.get("journal_line")).first()
        sl.matched_line = jl
        sl.reconciled = jl is not None
        sl.reconciled_at = timezone.now() if jl else None
        sl.save()
        return Response(BankStatementLineSerializer(sl).data)


class CashAccountViewSet(TenantModelViewSet):
    serializer_class = CashAccountSerializer
    perm_map = page_perms("bank_cash", {"create": extra_perm("bank_cash", "petty_cash"), "update": extra_perm("bank_cash", "petty_cash"), "partial_update": extra_perm("bank_cash", "petty_cash"), "destroy": extra_perm("bank_cash", "petty_cash")})
    pagination_class = None
    queryset = CashAccount.objects.select_related("account", "custodian")

    def perform_create(self, serializer):
        if not serializer.validated_data.get("account"):
            group = Account.objects.filter(code="1-01-001").first()
            n = Account.objects.filter(parent=group).count() + 1
            acct = Account.objects.create(code=f"1-01-001-{n:04d}", name=serializer.validated_data["name"], level=4, parent=group, type="asset", subtype="cash", is_group=False, currency=self.request.tenant.base_currency)
            serializer.validated_data["account"] = acct
        super().perform_create(serializer)


class PaymentViewSet(DocumentViewSet):
    """Receipts (direction=in) and payments (direction=out) for customers, suppliers, reps, other."""

    serializer_class = PaymentSerializer
    perm_map = page_perms("payments", {
        "cancel": "accounting.delete_payment",
        "allocate": "accounting.change_payment",
        "cheque": "accounting.change_cheque",
        "cheques": "accounting.view_cheque",
        "open_documents": "accounting.view_payment",
        "print": "accounting.view_payment",
    }, also={"create": [extra_perm("payments", "receive"), extra_perm("payments", "pay")]})
    filterset_fields = ["direction", "partner_type", "partner_id", "method", "status", "cheque_status", "bank_account", "cash_account", "collected_by", "trip"]
    search_fields = ["number", "partner_name", "reference", "cheque_no", "notes"]
    ordering_fields = ["date", "amount", "number", "created_at"]
    http_method_names = ["get", "post", "patch", "head", "options"]
    queryset = Payment.objects.select_related("bank_account", "cash_account", "collected_by", "journal").prefetch_related("allocations")

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get("date_from"):
            qs = qs.filter(date__gte=p["date_from"])
        if p.get("date_to"):
            qs = qs.filter(date__lte=p["date_to"])
        return qs

    def perform_create(self, serializer):
        d = dict(serializer.validated_data)
        m = self.request.membership
        # Add covers both directions; the "Receive money" / "Pay out" extras let a role do only one of them
        if not m.has_perm("accounting.add_payment"):
            needed = extra_perm("payments", "receive") if d["direction"] == "in" else extra_perm("payments", "pay")
            if not m.has_perm(needed):
                raise DomainError("You cannot receive payments." if d["direction"] == "in" else "You cannot make payments.", code="forbidden", status_code=403)
        allocations = [{"document_type": a["document_type"], "document_id": a["document_id"], "amount": a["amount"]} for a in d.pop("allocations", [])]
        claim_deductions = self.request.data.get("claim_deductions") or []
        if claim_deductions and not (m.has_perm("sales.settle_company_claim") or m.is_owner):
            raise DomainError("You cannot settle company discount claims.", code="forbidden", status_code=403)
        serializer.instance = PaymentService.record(
            direction=d["direction"], partner_type=d["partner_type"], partner_id=d.get("partner_id", ""), amount=d["amount"], method=d.get("method", "cash"), date_=d.get("date"),
            bank_account=d.get("bank_account"), cash_account=d.get("cash_account"), allocations=allocations or None, cheque_no=d.get("cheque_no", ""), cheque_date=d.get("cheque_date"), cheque_bank=d.get("cheque_bank", ""),
            reference=d.get("reference", ""), notes=d.get("notes", ""), withholding_tax=d.get("withholding_tax"), withholding_amount=d.get("withholding_amount", 0), trip=d.get("trip"), delivery_note=d.get("delivery_note"), collected_by=d.get("collected_by"),
            claim_deductions=claim_deductions or None, other_account=d.get("other_account"),
        )

    def perform_update(self, serializer):
        raise DomainError("Payments are immutable; cancel and re-enter.")

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return Response(PaymentSerializer(PaymentService.cancel(self.get_object(), request.data.get("reason", ""))).data)

    @action(detail=True, methods=["post"])
    def allocate(self, request, pk=None):
        return Response(PaymentSerializer(PaymentService.allocate(self.get_object(), request.data.get("allocations", []))).data)

    @action(detail=True, methods=["post"], url_path=r"cheque/(?P<event>deposited|cleared|bounced)")
    def cheque(self, request, pk=None, event=None):
        ba = BankAccount.objects.filter(pk=request.data.get("bank_account")).first() if request.data.get("bank_account") else None
        return Response(PaymentSerializer(PaymentService.cheque_event(self.get_object(), event, ba, Decimal(str(request.data.get("charges", 0) or 0)))).data)

    @action(detail=False, methods=["get"])
    def cheques(self, request):
        qs = self.get_queryset().filter(method="cheque").exclude(status="cancelled")
        if request.query_params.get("cheque_status"):
            qs = qs.filter(cheque_status=request.query_params["cheque_status"])
        return Response(PaymentSerializer(qs[:500], many=True).data)

    @action(detail=False, methods=["get"], url_path="open-documents")
    def open_documents(self, request):
        pt, pid = request.query_params.get("partner_type"), request.query_params.get("partner_id")
        docs = PaymentService._open_documents(pt, pid)
        return Response([{"document_type": t, "document_id": str(d.pk), "number": d.number, "date": getattr(d, "date", None) or getattr(d, "bill_date", None), "due_date": d.due_date, "total": str(d.total), "paid": str(d.paid_amount), "balance": str(b)} for d, b, t in docs])

    @action(detail=True, methods=["get"])
    def print(self, request, pk=None):
        from django.http import HttpResponse
        from django.template.loader import render_to_string
        from weasyprint import HTML

        p = self.get_object()
        html = render_to_string("accounting/receipt.html", {"p": p, "tenant": request.tenant})
        resp = HttpResponse(HTML(string=html).write_pdf(), content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="{p.number}.pdf"'
        return resp


class ExpenseViewSet(DocumentViewSet):
    serializer_class = ExpenseSerializer
    perm_map = page_perms("expenses", {
        "submit": "accounting.add_expense",
        "approve": extra_perm("expenses", "approve"),
        "reject": extra_perm("expenses", "approve"),
        "pay": "accounting.change_expense",
    })
    filterset_fields = ["status", "category", "cost_centre", "is_recurring"]
    search_fields = ["number", "vendor", "notes"]
    ordering_fields = ["date", "amount", "created_at"]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    queryset = Expense.objects.select_related("category", "cost_centre", "approved_by", "created_by", "journal")

    def perform_create(self, serializer):
        serializer.validated_data["number"] = next_number("expense")
        super().perform_create(serializer)
        if self.request.data.get("submit"):
            ExpenseService.submit(serializer.instance)

    def perform_update(self, serializer):
        if serializer.instance.status not in ("draft", "rejected"):
            raise DomainError("Only draft expenses can be edited.")
        serializer.instance.status = "draft"
        super().perform_update(serializer)

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        return Response(ExpenseSerializer(ExpenseService.submit(self.get_object())).data)

    @action(detail=True, methods=["post"])
    @require_perm("cash.approve_expense")
    def approve(self, request, pk=None):
        return Response(ExpenseSerializer(ExpenseService.approve(self.get_object())).data)

    @action(detail=True, methods=["post"])
    @require_perm("cash.approve_expense")
    def reject(self, request, pk=None):
        return Response(ExpenseSerializer(ExpenseService.reject(self.get_object(), request.data.get("reason", ""))).data)

    @action(detail=True, methods=["post"])
    @require_perm("cash.make_payment")
    def pay(self, request, pk=None):
        ba = BankAccount.objects.filter(pk=request.data.get("bank_account")).first() if request.data.get("bank_account") else None
        ca = CashAccount.objects.filter(pk=request.data.get("cash_account")).first() if request.data.get("cash_account") else None
        return Response(ExpenseSerializer(ExpenseService.pay(self.get_object(), ba, ca)).data)


class OpeningBalanceViewSet(DocumentViewSet):
    serializer_class = OpeningBalanceBatchSerializer
    perm_map = page_perms("opening_balances", {"post": "accounting.add_opening_balance"})
    queryset = OpeningBalanceBatch.objects.all()
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def perform_create(self, serializer):
        serializer.validated_data["number"] = next_number("opening_balance")
        super().perform_create(serializer)

    @action(detail=True, methods=["post"])
    @require_perm("accounting.post_journal")
    def post(self, request, pk=None):
        je = PostingService.post_opening_balances(self.get_object())
        return Response({"journal": je.number if je else None})


class PostingRuleViewSet(TenantModelViewSet):
    serializer_class = PostingRuleSerializer
    perm_map = page_perms("settings_posting_rules")
    pagination_class = None
    queryset = PostingRule.objects.select_related("account")
    http_method_names = ["get", "post", "patch", "head", "options"]


class AccountingDashboardView(APIView):
    permission_classes = [IsTenantUser]
    required_perm = "accounting.view_payment"  # the Accounting dashboard sits on the Payments page's View switch

    def get(self, request):
        from apps.purchasing.models import PurchaseBill
        from apps.sales.models import RepCollection, SalesInvoice

        today = timezone.localdate()
        week = today + timezone.timedelta(days=7)
        cash = [{"name": c.name, "balance": str(ReportQueries.account_balance(c.account))} for c in CashAccount.objects.select_related("account")]
        bank = [{"name": b.name, "balance": str(ReportQueries.account_balance(b.account) + b.opening_balance)} for b in BankAccount.objects.select_related("account")]
        rec = SalesInvoice.objects.filter(status__in=["posted", "partially_paid", "overdue"])
        pay = PurchaseBill.objects.filter(status__in=["posted", "partially_paid"])

        def bal(qs):
            a = qs.aggregate(t=Sum("total"), p=Sum("paid_amount"))
            return str((a["t"] or 0) - (a["p"] or 0))

        return Response({
            "cash": cash, "bank": bank,
            "receivables": {"total": bal(rec), "due_today": bal(rec.filter(due_date=today)), "due_week": bal(rec.filter(due_date__lte=week, due_date__gte=today)), "overdue": bal(rec.filter(due_date__lt=today))},
            "payables": {"total": bal(pay), "due_today": bal(pay.filter(due_date=today)), "due_week": bal(pay.filter(due_date__lte=week, due_date__gte=today)), "overdue": bal(pay.filter(due_date__lt=today))},
            "cash_with_reps": str(RepCollection.objects.filter(status="held_by_rep").aggregate(s=Sum("amount"))["s"] or 0),
            "unreconciled_bank_lines": BankStatementLine.objects.filter(reconciled=False).count(),
            "pending_cheques": Payment.objects.filter(method="cheque", cheque_status__in=["pending", "deposited"], status="posted").count(),
            "pending_expenses": Expense.objects.filter(status="submitted").count(),
            "draft_journals": JournalEntry.objects.filter(status="draft").count(),
        })


class ChequeBookViewSet(TenantModelViewSet):
    """
    The cheque books the business writes from. Creating one lays out its leaves, so every cheque in it can be
    pointed at from the day the bank hands it over.
    """

    serializer_class = ChequeBookSerializer
    perm_map = page_perms("cheque_books", {"leaves": "accounting.view_cheque_book", "void": "accounting.change_cheque_book"})
    filterset_fields = ["bank_account", "status"]
    search_fields = ["prefix", "notes", "bank_account__name"]
    pagination_class = None
    queryset = ChequeBook.objects.select_related("bank_account").prefetch_related("leaves")

    def perform_create(self, serializer):
        d = serializer.validated_data
        serializer.instance = ChequeBookService.create(
            bank_account=d["bank_account"], first_no=d["first_no"], last_no=d["last_no"],
            prefix=d.get("prefix", ""), issued_on=d.get("issued_on"), notes=d.get("notes", ""),
        )

    def perform_destroy(self, instance):
        if instance.leaves.filter(status="issued").exists():
            raise DomainError("Cheques from this book have been written, so it is part of the record. Mark it cancelled instead.",
                              code="in_use", status_code=400)
        instance.delete()

    @action(detail=True, methods=["get"])
    def leaves(self, request, pk=None):
        qs = self.get_object().leaves.select_related("payment").order_by("serial")
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        return Response(ChequeLeafSerializer(qs, many=True).data)

    @action(detail=True, methods=["post"], url_path="leaves/(?P<leaf_id>[^/.]+)/void")
    def void(self, request, pk=None, leaf_id=None):
        """Mark a leaf spoiled — torn, misprinted, or cancelled before it went out."""
        leaf = self.get_object().leaves.get(pk=leaf_id)
        return Response(ChequeLeafSerializer(ChequeBookService.void(leaf, request.data.get("reason", ""))).data)


class ChequeLeafViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Every cheque the business has, across its books — the register the bank asks about, and the picker the
    payment screen uses. `?available=1&bank_account=…` is the one a payment needs: nearest number first, so the
    first row is the one to write next.
    """

    permission_classes = [IsTenantUser]
    serializer_class = ChequeLeafSerializer
    perm_map = page_perms("cheque_books", {"export": "accounting.view_cheque_book"})
    filterset_fields = ["status", "book"]
    search_fields = ["number"]
    pagination_class = None

    @action(detail=False, methods=["get"])
    def export(self, request):
        """The register as a file — `?format=csv|xlsx`, through the same writers the reports use."""
        from django.http import HttpResponse

        from apps.reports.exporters import to_csv, to_xlsx

        rows = [{
            "Bank": l.book.bank_account.name,
            "Book": str(l.book),
            "Cheque": l.number,
            "Status": l.get_status_display(),
            "Payment": l.payment.number if l.payment_id else "",
            "Date": l.payment.date if l.payment_id else "",
            "Paid to": l.payment.partner_name if l.payment_id else "",
            "Amount": str(l.payment.amount) if l.payment_id else "",
            "Note": l.void_reason,
        } for l in self.filter_queryset(self.get_queryset())]
        result = {"columns": list(rows[0].keys()) if rows else ["Bank", "Book", "Cheque", "Status"], "rows": rows}
        fmt = request.query_params.get("format", "csv")
        if fmt == "xlsx":
            data, ct, ext = to_xlsx(result, "Cheque register"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
        else:
            data, ct, ext = to_csv(result, "Cheque register"), "text/csv", "csv"
        resp = HttpResponse(data, content_type=ct)
        resp["Content-Disposition"] = f'attachment; filename="cheque-register.{ext}"'
        return resp

    def get_queryset(self):
        qs = ChequeLeaf.objects.select_related("book", "book__bank_account", "payment")
        p = self.request.query_params
        if p.get("bank_account"):
            qs = qs.filter(book__bank_account_id=p["bank_account"])
        if p.get("available") == "1":
            qs = qs.filter(status="available", book__status="active")
        return qs.order_by("book__first_no", "serial")
