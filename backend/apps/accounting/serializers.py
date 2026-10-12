from rest_framework import serializers

from .models import Account, AccountingPeriod, BankAccount, BankStatementLine, CashAccount, ChequeBook, ChequeLeaf, CostCentre, Expense, FiscalYear, JournalEntry, JournalLine, OpeningBalanceBatch, Payment, PaymentAllocation, PostingRule


class AccountSerializer(serializers.ModelSerializer):
    parent_name = serializers.CharField(source="parent.name", read_only=True, default="")
    balance = serializers.SerializerMethodField()

    class Meta:
        model = Account
        fields = ["id", "code", "name", "level", "parent", "parent_name", "type", "subtype", "is_group", "is_active", "is_system", "currency", "opening_balance", "allow_manual_posting", "description", "balance"]
        read_only_fields = ["id", "level", "is_system"]

    # The class and group levels are the standard chart of accounts, and the reports and the automatic journals
    # resolve them by code: the balance sheet and P&L print the level 1–2 *names* as their headings, and the
    # posting rules look accounts up with `code__startswith`. So renaming or renumbering one quietly rewrites a
    # report or breaks a posting. The screen already locks these fields; this is the half that cannot be
    # bypassed by a direct call.
    FIXED_ABOVE_LEVEL = ("code", "name", "parent", "type", "is_group")

    def get_balance(self, obj):
        if getattr(self.context.get("view"), "action", "") not in ("retrieve", "tree"):
            return None
        from .services import ReportQueries

        return str(ReportQueries.account_balance(obj)) if not obj.is_group else None

    def validate(self, attrs):
        parent = attrs.get("parent", getattr(self.instance, "parent", None))
        if self.instance is not None:
            # the class and group levels are fixed — `level` is set on create, so fall back to the depth of the
            # code ("1-01" is level 2) for any row seeded before that field was trustworthy
            level = self.instance.level or len(str(self.instance.code or "").split("-"))
            if level <= 2:
                # the class and group levels are the standard chart itself — fully locked, not just their
                # identifying fields: a report heading, a posting rule or a balance hangs off every one of them.
                changing = [f for f in self.fields if not self.fields[f].read_only
                            and f in attrs and attrs[f] != getattr(self.instance, f, None)]
                if changing:
                    raise serializers.ValidationError({changing[0]: [
                        f"{self.instance.code} {self.instance.name} is part of the standard chart of accounts and "
                        f"cannot be edited. Add an account beneath it instead."
                    ]})
        if self.instance is None:
            attrs["level"] = (parent.level + 1) if parent else 1
            if attrs["level"] > 4:
                raise serializers.ValidationError({"parent": ["Maximum depth is 4 levels."]})
            attrs.setdefault("is_group", attrs["level"] < 4)
            if parent and not parent.is_group:
                raise serializers.ValidationError({"parent": ["Parent must be a group account."]})
            if parent:
                attrs["type"] = parent.type
        return attrs


class JournalLineSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)
    cost_centre_name = serializers.CharField(source="cost_centre.name", read_only=True, default="")
    partner_name = serializers.SerializerMethodField()

    class Meta:
        model = JournalLine
        fields = ["id", "account", "account_code", "account_name", "debit", "credit", "description", "partner_type", "partner_id", "partner_name", "cost_centre", "cost_centre_name", "tax_type", "sequence"]
        read_only_fields = ["id"]

    def get_partner_name(self, obj):
        if not obj.partner_type or not obj.partner_id:
            return ""
        from apps.partners.models import Customer, Supplier, SupplierRep

        model = {"customer": Customer, "supplier": Supplier, "rep": SupplierRep}.get(obj.partner_type)
        if model is None:
            return ""
        o = model.objects.filter(pk=obj.partner_id).first()
        return o.name if o else ""


class JournalEntrySerializer(serializers.ModelSerializer):
    lines = JournalLineSerializer(many=True)
    posted_by_name = serializers.CharField(source="posted_by.full_name", read_only=True, default="")
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default="")
    reversed_by_number = serializers.CharField(source="reversed_by_entry.number", read_only=True, default="")

    class Meta:
        model = JournalEntry
        fields = ["id", "number", "version", "date", "type", "source_type", "source_id", "source_number", "status", "memo", "posted_by", "posted_by_name", "posted_at", "reversed_by_entry", "reversed_by_number", "period", "total_debit", "total_credit", "notes", "lines", "created_by_name", "created_at", "updated_at"]
        read_only_fields = ["id", "number", "version", "type", "source_type", "source_id", "source_number", "status", "posted_by", "posted_at", "reversed_by_entry", "period", "total_debit", "total_credit", "created_at", "updated_at"]


class FiscalYearSerializer(serializers.ModelSerializer):
    class Meta:
        model = FiscalYear
        fields = ["id", "name", "start", "end", "status", "closed_by", "closing_entry"]
        read_only_fields = ["id", "status", "closed_by", "closing_entry"]


class AccountingPeriodSerializer(serializers.ModelSerializer):
    fiscal_year_name = serializers.CharField(source="fiscal_year.name", read_only=True)
    label = serializers.CharField(source="__str__", read_only=True)

    class Meta:
        model = AccountingPeriod
        fields = ["id", "fiscal_year", "fiscal_year_name", "label", "start", "end", "status", "closed_by", "closed_at"]
        read_only_fields = fields


class CostCentreSerializer(serializers.ModelSerializer):
    class Meta:
        model = CostCentre
        fields = ["id", "code", "name", "kind", "ref_id", "is_active"]


class BankAccountSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source="account.code", read_only=True)
    balance = serializers.SerializerMethodField()

    class Meta:
        model = BankAccount
        fields = ["id", "name", "bank", "branch", "account_no", "iban", "account", "account_code", "opening_balance", "is_active", "balance"]
        read_only_fields = ["id"]
        extra_kwargs = {"account": {"required": False}}

    def get_balance(self, obj):
        from .services import ReportQueries

        return str(ReportQueries.account_balance(obj.account) + obj.opening_balance)


class CashAccountSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source="account.code", read_only=True)
    custodian_name = serializers.CharField(source="custodian.full_name", read_only=True, default="")
    balance = serializers.SerializerMethodField()

    class Meta:
        model = CashAccount
        fields = ["id", "name", "account", "account_code", "custodian", "custodian_name", "is_petty", "is_active", "balance"]
        read_only_fields = ["id"]
        extra_kwargs = {"account": {"required": False}}

    def get_balance(self, obj):
        from .services import ReportQueries

        return str(ReportQueries.account_balance(obj.account))


class PaymentAllocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentAllocation
        fields = ["id", "document_type", "document_id", "document_number", "amount"]
        read_only_fields = ["id", "document_number"]


class PaymentSerializer(serializers.ModelSerializer):
    allocations = PaymentAllocationSerializer(many=True, required=False)
    bank_account_name = serializers.CharField(source="bank_account.name", read_only=True, default="")
    cash_account_name = serializers.CharField(source="cash_account.name", read_only=True, default="")
    collected_by_name = serializers.CharField(source="collected_by.full_name", read_only=True, default="")
    journal_number = serializers.CharField(source="journal.number", read_only=True, default="")

    class Meta:
        model = Payment
        fields = ["id", "number", "direction", "partner_type", "partner_id", "partner_name", "date", "amount", "method", "bank_account", "bank_account_name", "cash_account", "cash_account_name", "collected_by", "collected_by_name", "cheque_no", "cheque_date", "cheque_bank", "cheque_status", "reference", "withholding_tax", "withholding_amount", "status", "journal", "journal_number", "trip", "delivery_note", "unallocated", "notes", "allocations", "created_at"]
        read_only_fields = ["id", "number", "partner_name", "cheque_status", "status", "journal", "unallocated", "created_at"]


class ExpenseSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    category_code = serializers.CharField(source="category.code", read_only=True)
    cost_centre_name = serializers.CharField(source="cost_centre.name", read_only=True, default="")
    approved_by_name = serializers.CharField(source="approved_by.full_name", read_only=True, default="")
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default="")
    total = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)
    journal_number = serializers.CharField(source="journal.number", read_only=True, default="")

    class Meta:
        model = Expense
        fields = ["id", "number", "version", "date", "category", "category_name", "category_code", "amount", "tax_type", "tax_amount", "total", "paid_from_bank", "paid_from_cash", "vendor", "cost_centre", "cost_centre_name", "status", "approved_by", "approved_by_name", "receipt_file", "journal", "journal_number", "is_recurring", "recurrence_day", "notes", "created_by_name", "created_at"]
        read_only_fields = ["id", "number", "version", "status", "approved_by", "journal", "created_at"]


class BankStatementLineSerializer(serializers.ModelSerializer):
    matched_journal = serializers.CharField(source="matched_line.entry.number", read_only=True, default="")

    class Meta:
        model = BankStatementLine
        fields = ["id", "bank_account", "date", "description", "reference", "amount", "matched_line", "matched_journal", "reconciled", "reconciled_at"]
        read_only_fields = ["id", "reconciled_at"]


class OpeningBalanceBatchSerializer(serializers.ModelSerializer):
    journal_number = serializers.CharField(source="journal.number", read_only=True, default="")

    class Meta:
        model = OpeningBalanceBatch
        fields = ["id", "number", "date", "lines", "posted", "journal", "journal_number", "notes", "created_at"]
        read_only_fields = ["id", "number", "posted", "journal", "created_at"]


class PostingRuleSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = PostingRule
        fields = ["id", "role", "role_label", "document_type", "account", "account_code", "account_name"]


class ChequeLeafSerializer(serializers.ModelSerializer):
    payment_number = serializers.CharField(source="payment.number", read_only=True, default="")
    payment_date = serializers.DateField(source="payment.date", read_only=True, default=None)
    payee = serializers.CharField(source="payment.partner_name", read_only=True, default="")
    amount = serializers.DecimalField(source="payment.amount", max_digits=18, decimal_places=4, read_only=True, default=None)
    bank_account = serializers.UUIDField(source="book.bank_account_id", read_only=True)
    bank_name = serializers.CharField(source="book.bank_account.name", read_only=True)

    class Meta:
        model = ChequeLeaf
        fields = ["id", "book", "serial", "number", "status", "payment", "payment_number", "payment_date",
                  "payee", "amount", "void_reason", "bank_account", "bank_name"]
        read_only_fields = ["id", "book", "serial", "number", "payment"]


class ChequeBookSerializer(serializers.ModelSerializer):
    bank_name = serializers.CharField(source="bank_account.name", read_only=True)
    bank_label = serializers.SerializerMethodField()
    label = serializers.CharField(read_only=True)
    leaves_total = serializers.SerializerMethodField()
    leaves_available = serializers.SerializerMethodField()
    leaves_issued = serializers.SerializerMethodField()
    leaves_void = serializers.SerializerMethodField()
    next_number = serializers.SerializerMethodField()

    class Meta:
        model = ChequeBook
        fields = ["id", "bank_account", "bank_name", "bank_label", "prefix", "first_no", "last_no", "issued_on",
                  "status", "notes", "label", "leaves_total", "leaves_available", "leaves_issued", "leaves_void",
                  "next_number", "created_at"]
        read_only_fields = ["id", "status", "created_at"]

    def get_bank_label(self, obj):
        b = obj.bank_account
        return f"{b.name} · {b.bank}" + (f" · {b.account_no}" if b.account_no else "")

    def _count(self, obj, status):
        return obj.leaves.filter(status=status).count()

    def get_leaves_total(self, obj):
        return obj.leaves.count()

    def get_leaves_available(self, obj):
        return self._count(obj, "available")

    def get_leaves_issued(self, obj):
        return self._count(obj, "issued")

    def get_leaves_void(self, obj):
        return self._count(obj, "void")

    def get_next_number(self, obj):
        leaf = obj.leaves.filter(status="available").order_by("serial").first()
        return leaf.number if leaf else ""
