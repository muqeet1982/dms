from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import MONEY, QTY, DocumentModel, TenantModel
from apps.purchasing.models import TaxLineMixin


class SalesOrder(DocumentModel):
    SOURCES = [("rep_app", "Rep app"), ("api", "API"), ("staff", "Staff"), ("portal", "Portal")]
    STATUS = [
        ("draft", "Draft"), ("submitted", "Submitted"), ("under_review", "Under review"), ("confirmed", "Confirmed"), ("on_hold_credit", "On hold (credit)"), ("on_hold_stock", "On hold (stock)"),
        ("issued", "Issued from warehouse"), ("dispatched", "Dispatched"), ("partially_delivered", "Partially delivered"), ("delivered", "Delivered"), ("invoiced", "Invoiced"), ("cancelled", "Cancelled"), ("rejected", "Rejected"),
    ]
    OPEN_STATUSES = ["submitted", "under_review", "confirmed", "on_hold_credit", "on_hold_stock", "issued", "dispatched", "partially_delivered"]
    customer = models.ForeignKey("partners.Customer", on_delete=models.PROTECT, related_name="orders")
    supplier = models.ForeignKey("partners.Supplier", null=True, blank=True, on_delete=models.SET_NULL, related_name="orders", help_text="Manufacturer whose rep raised the order")
    rep = models.ForeignKey("partners.SupplierRep", null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    source = models.CharField(max_length=10, choices=SOURCES, default="staff")
    warehouse = models.ForeignKey("inventory.Warehouse", null=True, blank=True, on_delete=models.PROTECT, related_name="orders")
    order_date = models.DateField(default=timezone.localdate, db_index=True)
    requested_delivery_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default="submitted", db_index=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    trip = models.ForeignKey("delivery.DeliveryTrip", null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    price_list = models.ForeignKey("catalog.PriceList", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    currency = models.CharField(max_length=3, default="PKR")
    apply_customer_discount = models.BooleanField(default=True, help_text="Apply the customer's standing discount to lines that have none; switch off for a one-off order")
    subtotal = models.DecimalField(**MONEY)
    discount = models.DecimalField(**MONEY)
    scheme_discount = models.DecimalField(**MONEY)
    tax1_total = models.DecimalField(**MONEY)
    tax2_total = models.DecimalField(**MONEY)
    total = models.DecimalField(**MONEY)
    external_ref = models.CharField(max_length=80, blank=True, db_index=True)
    # where the rep was standing when the order was taken — the audit trail behind "I was at the shop"
    gps_lat = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    gps_lng = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    gps_accuracy_m = models.FloatField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=300, blank=True)
    hold_reason = models.CharField(max_length=300, blank=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    price_flags = models.JSONField(default=list, blank=True)  # differences between caller prices and price list
    cost_centre = models.ForeignKey("accounting.CostCentre", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-order_date", "-created_at"]
        unique_together = [("tenant", "number")]
        indexes = [models.Index(fields=["tenant", "status"]), models.Index(fields=["tenant", "supplier", "external_ref"])]

    def __str__(self):
        return self.number

    @property
    def is_editable(self):
        return self.status in ("draft", "submitted", "under_review", "on_hold_credit", "on_hold_stock")


class SalesOrderLine(TaxLineMixin, TenantModel):
    order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name="lines")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="order_lines")
    packaging = models.ForeignKey("catalog.Packaging", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    qty_ordered = models.DecimalField(**QTY)  # base units
    qty_allocated = models.DecimalField(**QTY)
    qty_dispatched = models.DecimalField(**QTY)
    qty_delivered = models.DecimalField(**QTY)
    qty_returned = models.DecimalField(**QTY)
    free_qty = models.DecimalField(**QTY)
    unit_price = models.DecimalField(**MONEY)  # per base unit
    requested_price = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    discount_pct = models.DecimalField(max_digits=7, decimal_places=3, default=Decimal("0"))
    discount_amount = models.DecimalField(**MONEY)
    scheme_discount = models.DecimalField(**MONEY)
    line_total = models.DecimalField(**MONEY)
    scheme_names = models.JSONField(default=list, blank=True)
    sequence = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sequence", "id"]

    @property
    def qty_pending(self):
        return self.qty_ordered - self.qty_delivered


class StockReservation(TenantModel):
    order_line = models.ForeignKey(SalesOrderLine, on_delete=models.CASCADE, related_name="reservations")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.CASCADE, related_name="+")
    batch = models.ForeignKey("inventory.Batch", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    qty = models.DecimalField(**QTY)
    expires_at = models.DateTimeField(null=True, blank=True)
    released = models.BooleanField(default=False)


class SalesInvoice(DocumentModel):
    STATUS = [("draft", "Draft"), ("posted", "Posted"), ("partially_paid", "Partially paid"), ("paid", "Paid"), ("overdue", "Overdue"), ("cancelled", "Cancelled")]
    customer = models.ForeignKey("partners.Customer", on_delete=models.PROTECT, related_name="invoices")
    order = models.ForeignKey(SalesOrder, null=True, blank=True, on_delete=models.SET_NULL, related_name="invoices")
    delivery_note = models.OneToOneField("delivery.DeliveryNote", null=True, blank=True, on_delete=models.SET_NULL, related_name="invoice")
    rep = models.ForeignKey("partners.SupplierRep", null=True, blank=True, on_delete=models.SET_NULL, related_name="invoices")
    date = models.DateField(default=timezone.localdate, db_index=True)
    due_date = models.DateField(null=True, blank=True)
    subtotal = models.DecimalField(**MONEY)
    discount = models.DecimalField(**MONEY)
    tax1_type = models.ForeignKey("catalog.TaxType", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    tax1_total = models.DecimalField(**MONEY)
    tax2_type = models.ForeignKey("catalog.TaxType", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    tax2_total = models.DecimalField(**MONEY)
    total = models.DecimalField(**MONEY)
    paid_amount = models.DecimalField(**MONEY)
    status = models.CharField(max_length=20, choices=STATUS, default="draft", db_index=True)
    journal = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    cost_centre = models.ForeignKey("accounting.CostCentre", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    from_transit = models.BooleanField(default=False)

    class Meta:
        ordering = ["-date", "-created_at"]
        unique_together = [("tenant", "number")]

    def __str__(self):
        return self.number

    @property
    def balance(self):
        return self.total - self.paid_amount


class SalesInvoiceLine(TaxLineMixin, TenantModel):
    invoice = models.ForeignKey(SalesInvoice, on_delete=models.CASCADE, related_name="lines")
    order_line = models.ForeignKey(SalesOrderLine, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey("inventory.Batch", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    description = models.CharField(max_length=200, blank=True)
    qty = models.DecimalField(**QTY)
    free_qty = models.DecimalField(**QTY)
    unit_price = models.DecimalField(**MONEY)
    unit_cost = models.DecimalField(**MONEY)
    discount_amount = models.DecimalField(**MONEY)
    line_total = models.DecimalField(**MONEY)
    sequence = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sequence", "id"]


class CreditNote(DocumentModel):
    STATUS = [("draft", "Draft"), ("posted", "Posted"), ("cancelled", "Cancelled")]
    customer = models.ForeignKey("partners.Customer", on_delete=models.PROTECT, related_name="credit_notes")
    invoice = models.ForeignKey(SalesInvoice, null=True, blank=True, on_delete=models.SET_NULL, related_name="credit_notes")
    warehouse = models.ForeignKey("inventory.Warehouse", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    date = models.DateField(default=timezone.localdate)
    reason = models.CharField(max_length=200, blank=True)
    subtotal = models.DecimalField(**MONEY)
    tax1_type = models.ForeignKey("catalog.TaxType", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    tax1_total = models.DecimalField(**MONEY)
    tax2_type = models.ForeignKey("catalog.TaxType", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    tax2_total = models.DecimalField(**MONEY)
    total = models.DecimalField(**MONEY)
    status = models.CharField(max_length=10, choices=STATUS, default="draft", db_index=True)
    journal = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    applied_to_invoice = models.BooleanField(default=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        unique_together = [("tenant", "number")]

    def __str__(self):
        return self.number


class CreditNoteLine(TaxLineMixin, TenantModel):
    CONDITIONS = [("good", "Good"), ("damaged", "Damaged"), ("expired", "Expired")]
    credit_note = models.ForeignKey(CreditNote, on_delete=models.CASCADE, related_name="lines")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey("inventory.Batch", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    qty = models.DecimalField(**QTY)
    unit_price = models.DecimalField(**MONEY)
    unit_cost = models.DecimalField(**MONEY)
    reason = models.CharField(max_length=120, blank=True)
    restock = models.BooleanField(default=True)
    condition = models.CharField(max_length=10, choices=CONDITIONS, default="good")
    line_total = models.DecimalField(**MONEY)


class RepCollection(DocumentModel):
    STATUS = [("held_by_rep", "Held by rep"), ("settled", "Settled"), ("cancelled", "Cancelled")]
    METHODS = [("cash", "Cash"), ("cheque", "Cheque"), ("online", "Online")]
    rep = models.ForeignKey("partners.SupplierRep", on_delete=models.PROTECT, related_name="collections")
    customer = models.ForeignKey("partners.Customer", on_delete=models.PROTECT, related_name="rep_collections")
    date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(**MONEY)
    method = models.CharField(max_length=10, choices=METHODS, default="cash")
    reference = models.CharField(max_length=100, blank=True)
    allocations = models.JSONField(default=list, blank=True)  # [{invoice, number, amount}]
    status = models.CharField(max_length=15, choices=STATUS, default="held_by_rep", db_index=True)
    journal = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    settlement = models.ForeignKey("RepSettlement", null=True, blank=True, on_delete=models.SET_NULL, related_name="collections")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    gps_lat = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    gps_lng = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    gps_accuracy_m = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        unique_together = [("tenant", "number")]

    def __str__(self):
        return self.number


class RepSettlement(DocumentModel):
    rep = models.ForeignKey("partners.SupplierRep", on_delete=models.PROTECT, related_name="settlements")
    date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(**MONEY)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    cash_account = models.ForeignKey("accounting.CashAccount", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    bank_account = models.ForeignKey("accounting.BankAccount", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    journal = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date", "-created_at"]
        unique_together = [("tenant", "number")]

    def __str__(self):
        return self.number
