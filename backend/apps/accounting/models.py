from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import CheckConstraint, Q
from django.utils import timezone

from apps.core.models import MONEY, DocumentModel, TenantModel


class Account(TenantModel):
    TYPES = [("asset", "Asset"), ("liability", "Liability"), ("equity", "Equity"), ("income", "Income"), ("expense", "Expense")]
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    level = models.PositiveSmallIntegerField(default=4)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    type = models.CharField(max_length=10, choices=TYPES)
    subtype = models.CharField(max_length=40, blank=True)  # cash, bank, receivable, payable, inventory, tax, cogs, ...
    is_group = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False)
    currency = models.CharField(max_length=3, default="PKR")
    opening_balance = models.DecimalField(**MONEY)
    allow_manual_posting = models.BooleanField(default=True)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = [("tenant", "code")]
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def normal_side(self):
        return "debit" if self.type in ("asset", "expense") else "credit"


class FiscalYear(TenantModel):
    STATUS = [("open", "Open"), ("closed", "Closed")]
    name = models.CharField(max_length=20)
    start = models.DateField()
    end = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS, default="open")
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    closing_entry = models.ForeignKey("JournalEntry", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-start"]
        unique_together = [("tenant", "name")]

    def __str__(self):
        return self.name


class AccountingPeriod(TenantModel):
    STATUS = [("open", "Open"), ("closed", "Closed")]
    fiscal_year = models.ForeignKey(FiscalYear, on_delete=models.CASCADE, related_name="periods")
    start = models.DateField()
    end = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS, default="open")
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["start"]

    def __str__(self):
        return f"{self.start:%b %Y}"


class CostCentre(TenantModel):
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=20, blank=True)  # warehouse, route, vehicle, department
    ref_id = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("tenant", "code")]
        ordering = ["code"]

    def __str__(self):
        return self.name


# A reversed original stays on the books — its reversing entry nets it to zero. Excluding it while the
# reversing entry counts would subtract the money twice, so every balance reads these two statuses.
GL_LIVE = ("posted", "reversed")


class JournalEntry(DocumentModel):
    TYPES = [("manual", "Manual"), ("auto", "Automatic"), ("opening", "Opening"), ("closing", "Closing")]
    STATUS = [("draft", "Draft"), ("posted", "Posted"), ("reversed", "Reversed")]
    # The voucher family a MANUAL entry belongs to — a label and a numbering series (CPV-00001 …), exactly as
    # the owner's old ERP had it. Automatic entries leave it blank and derive a report code instead.
    VOUCHER_TYPES = [
        ("cpv", "Cash Payment Voucher"), ("crv", "Cash Receipt Voucher"), ("bpv", "Bank Payment Voucher"),
        ("brv", "Bank Receipt Voucher"), ("jv", "Journal Voucher"), ("jrv", "Journal Receipt Voucher"),
        ("jpv", "Journal Payment Voucher"), ("jcv", "Journal Contra Voucher"), ("sjv", "Standing Journal Voucher"),
    ]
    date = models.DateField(default=timezone.localdate, db_index=True)
    type = models.CharField(max_length=10, choices=TYPES, default="manual")
    voucher_type = models.CharField(max_length=4, choices=VOUCHER_TYPES, blank=True, default="", db_index=True)
    source_type = models.CharField(max_length=40, blank=True, db_index=True)
    source_id = models.CharField(max_length=64, blank=True, db_index=True)
    source_number = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="draft", db_index=True)
    memo = models.CharField(max_length=300, blank=True)
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    posted_at = models.DateTimeField(null=True, blank=True)
    reversed_by_entry = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reverses")
    period = models.ForeignKey(AccountingPeriod, null=True, blank=True, on_delete=models.PROTECT, related_name="entries")
    total_debit = models.DecimalField(**MONEY)
    total_credit = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["-date", "-created_at"]
        unique_together = [("tenant", "number")]
        constraints = [CheckConstraint(condition=Q(total_debit=models.F("total_credit")) | Q(status="draft"), name="journal_balanced_when_posted")]

    def __str__(self):
        return self.number


class JournalLine(TenantModel):
    PARTNERS = [("customer", "Customer"), ("supplier", "Supplier"), ("rep", "Rep"), ("employee", "Employee")]
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    debit = models.DecimalField(**MONEY)
    credit = models.DecimalField(**MONEY)
    description = models.CharField(max_length=200, blank=True)
    partner_type = models.CharField(max_length=10, choices=PARTNERS, blank=True, db_index=True)
    partner_id = models.CharField(max_length=64, blank=True, db_index=True)
    cost_centre = models.ForeignKey(CostCentre, null=True, blank=True, on_delete=models.SET_NULL, related_name="lines")
    tax_type = models.ForeignKey("catalog.TaxType", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    # a bank-account line on a manual voucher may carry one of our own cheques (claimed when the draft saves)
    cheque_leaf = models.ForeignKey("ChequeLeaf", null=True, blank=True, on_delete=models.SET_NULL, related_name="je_lines")
    sequence = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sequence", "id"]
        indexes = [models.Index(fields=["tenant", "account"]), models.Index(fields=["tenant", "partner_type", "partner_id"])]
        constraints = [CheckConstraint(condition=Q(debit__gte=0) & Q(credit__gte=0), name="journal_line_non_negative")]


class BankAccount(TenantModel):
    name = models.CharField(max_length=80)
    bank = models.CharField(max_length=80, blank=True)
    branch = models.CharField(max_length=80, blank=True)
    account_no = models.CharField(max_length=40, blank=True)
    iban = models.CharField(max_length=40, blank=True)
    account = models.OneToOneField(Account, on_delete=models.PROTECT, related_name="bank_account")
    opening_balance = models.DecimalField(**MONEY)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class CashAccount(TenantModel):
    name = models.CharField(max_length=80)
    account = models.OneToOneField(Account, on_delete=models.PROTECT, related_name="cash_account")
    custodian = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    is_petty = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ChequeBook(TenantModel):
    """
    A book of cheque leaves belonging to one bank account.

    Only for cheques the business **writes**. A cheque taken *from* a customer is their paper and their
    numbering; it is recorded on the payment and belongs to no book of ours.
    """

    STATUS = [("active", "Active"), ("exhausted", "Used up"), ("cancelled", "Cancelled")]
    bank_account = models.ForeignKey(BankAccount, on_delete=models.PROTECT, related_name="cheque_books")
    prefix = models.CharField(max_length=10, blank=True, help_text="Letters printed before the number, if any")
    first_no = models.PositiveIntegerField()
    last_no = models.PositiveIntegerField()
    issued_on = models.DateField(null=True, blank=True, help_text="When the bank gave you the book")
    status = models.CharField(max_length=10, choices=STATUS, default="active", db_index=True)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["bank_account__name", "first_no"]
        constraints = [models.UniqueConstraint(fields=["tenant", "bank_account", "first_no"], name="one_book_per_start_no")]

    def __str__(self):
        return f"{self.prefix}{self.first_no}–{self.prefix}{self.last_no}"

    @property
    def label(self):
        return str(self)


class ChequeLeaf(TenantModel):
    """One printed cheque. `issued` once it is written out; `void` when it is spoiled or cancelled."""

    STATUS = [("available", "Available"), ("issued", "Issued"), ("void", "Void")]
    book = models.ForeignKey(ChequeBook, on_delete=models.CASCADE, related_name="leaves")
    serial = models.PositiveIntegerField()
    number = models.CharField(max_length=30, db_index=True, help_text="As printed, prefix and all")
    status = models.CharField(max_length=10, choices=STATUS, default="available", db_index=True)
    payment = models.OneToOneField("Payment", null=True, blank=True, on_delete=models.SET_NULL, related_name="cheque_leaf")
    void_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["serial"]
        constraints = [models.UniqueConstraint(fields=["tenant", "book", "serial"], name="one_leaf_per_serial")]

    def __str__(self):
        return self.number


class Payment(DocumentModel):
    DIRECTIONS = [("in", "Receipt"), ("out", "Payment")]
    PARTNERS = [("customer", "Customer"), ("supplier", "Supplier"), ("rep", "Rep"), ("company", "Manufacturer/Company"), ("other", "Other")]
    METHODS = [("cash", "Cash"), ("bank_transfer", "Bank transfer"), ("cheque", "Cheque"), ("online", "Online"), ("adjustment", "Adjustment")]
    CHEQUE_STATUS = [("pending", "Pending"), ("deposited", "Deposited"), ("cleared", "Cleared"), ("bounced", "Bounced")]
    STATUS = [("draft", "Draft"), ("posted", "Posted"), ("cancelled", "Cancelled")]
    direction = models.CharField(max_length=3, choices=DIRECTIONS)
    partner_type = models.CharField(max_length=10, choices=PARTNERS)
    partner_id = models.CharField(max_length=64, blank=True)
    partner_name = models.CharField(max_length=200, blank=True)
    date = models.DateField(default=timezone.localdate, db_index=True)
    amount = models.DecimalField(**MONEY)
    method = models.CharField(max_length=15, choices=METHODS, default="cash")
    bank_account = models.ForeignKey(BankAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    cash_account = models.ForeignKey(CashAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    # partner type "other": the account head the money is booked against (rent income, a loan, owner capital …);
    # left empty it falls back to Misc income (in) / Bank charges (out)
    other_account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT, related_name="other_payments")
    collected_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="collected_payments")
    cheque_no = models.CharField(max_length=40, blank=True)
    cheque_date = models.DateField(null=True, blank=True)
    cheque_bank = models.CharField(max_length=80, blank=True)
    cheque_status = models.CharField(max_length=10, choices=CHEQUE_STATUS, blank=True)
    reference = models.CharField(max_length=100, blank=True)
    withholding_tax = models.ForeignKey("catalog.TaxType", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    withholding_amount = models.DecimalField(**MONEY)
    status = models.CharField(max_length=10, choices=STATUS, default="posted", db_index=True)
    journal = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    trip = models.ForeignKey("delivery.DeliveryTrip", null=True, blank=True, on_delete=models.SET_NULL, related_name="payments")
    delivery_note = models.ForeignKey("delivery.DeliveryNote", null=True, blank=True, on_delete=models.SET_NULL, related_name="payments")
    unallocated = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["-date", "-created_at"]
        unique_together = [("tenant", "number")]

    def __str__(self):
        return self.number


class PaymentAllocation(TenantModel):
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="allocations")
    document_type = models.CharField(max_length=30)  # sales_invoice | purchase_bill | credit_note
    document_id = models.CharField(max_length=64)
    document_number = models.CharField(max_length=40, blank=True)
    amount = models.DecimalField(**MONEY)


class Expense(DocumentModel):
    STATUS = [("draft", "Draft"), ("submitted", "Submitted"), ("approved", "Approved"), ("paid", "Paid"), ("rejected", "Rejected")]
    date = models.DateField(default=timezone.localdate)
    category = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="expenses", limit_choices_to={"type": "expense", "is_group": False})
    amount = models.DecimalField(**MONEY)
    tax_type = models.ForeignKey("catalog.TaxType", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    tax_amount = models.DecimalField(**MONEY)
    paid_from_bank = models.ForeignKey(BankAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    paid_from_cash = models.ForeignKey(CashAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    vendor = models.CharField(max_length=120, blank=True)
    cost_centre = models.ForeignKey(CostCentre, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=10, choices=STATUS, default="draft", db_index=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    receipt_file = models.FileField(upload_to="expenses/", null=True, blank=True)
    journal = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    is_recurring = models.BooleanField(default=False)
    recurrence_day = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        unique_together = [("tenant", "number")]

    @property
    def total(self):
        return self.amount + self.tax_amount


class BankStatementLine(TenantModel):
    bank_account = models.ForeignKey(BankAccount, on_delete=models.CASCADE, related_name="statement_lines")
    date = models.DateField()
    description = models.CharField(max_length=200, blank=True)
    reference = models.CharField(max_length=100, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=4)  # + credit to bank / − debit
    matched_line = models.ForeignKey(JournalLine, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reconciled = models.BooleanField(default=False)
    reconciled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date"]


class OpeningBalanceBatch(DocumentModel):
    date = models.DateField(default=timezone.localdate)
    lines = models.JSONField(default=list, blank=True)  # [{account, debit, credit, partner_type, partner_id}]
    posted = models.BooleanField(default=False)
    journal = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date"]


class PostingRule(TenantModel):
    ROLES = [
        ("inventory", "Stock in hand"), ("in_transit", "Stock in transit"), ("quarantine", "Damaged/quarantine stock"), ("cogs", "Cost of goods sold"), ("ar", "Trade receivables"), ("ap", "Trade payables"),
        ("sales", "Sales"), ("sales_return", "Sales returns"), ("discount", "Discounts allowed"), ("tax1_out", "Output tax 1"), ("tax2_out", "Output tax 2"), ("tax1_in", "Input tax 1"), ("tax2_in", "Input tax 2"),
        ("grni", "Goods received not invoiced"), ("ppv", "Purchase price variance"), ("freight_in", "Freight inward"), ("rep_clearing", "Rep cash clearing"), ("cash", "Cash in hand"), ("petty_cash", "Petty cash"), ("bank", "Default bank"),
        ("pdc_received", "Post-dated cheques received"), ("manufacturer_claim", "Manufacturer claims receivable"), ("cheques_payable", "Cheques payable"), ("shrinkage", "Inventory shrinkage"), ("expiry_loss", "Expiry loss"), ("damaged_goods", "Damaged goods"), ("transit_damage", "Transit damage"),
        ("samples", "Samples & promotion"), ("withholding_payable", "Withholding tax payable"), ("withholding_receivable", "Withholding tax receivable"), ("advance_customer", "Advances from customers"), ("advance_supplier", "Advances to suppliers"),
        ("opening_equity", "Opening balance equity"), ("retained_earnings", "Retained earnings"), ("current_year_profit", "Current year profit"), ("bank_charges", "Bank charges"), ("other_income", "Misc income"), ("bad_debts", "Bad debts"),
        ("manufacturer_incentive", "Manufacturer incentive / scheme income"),
    ]
    role = models.CharField(max_length=30, choices=ROLES)
    document_type = models.CharField(max_length=30, default="*")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="posting_rules")

    class Meta:
        unique_together = [("tenant", "role", "document_type")]
        ordering = ["role"]
