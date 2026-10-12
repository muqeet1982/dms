from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import MONEY, QTY, DocumentModel, TenantModel


class TaxLineMixin(models.Model):
    tax1_type = models.ForeignKey("catalog.TaxType", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    tax1_rate = models.DecimalField(max_digits=7, decimal_places=3, default=Decimal("0"))
    tax1_amount = models.DecimalField(**MONEY)
    tax2_type = models.ForeignKey("catalog.TaxType", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    tax2_rate = models.DecimalField(max_digits=7, decimal_places=3, default=Decimal("0"))
    tax2_amount = models.DecimalField(**MONEY)

    class Meta:
        abstract = True


class PurchaseOrder(DocumentModel):
    STATUS = [("draft", "Draft"), ("sent", "Sent"), ("partially_received", "Partially received"), ("received", "Received"), ("cancelled", "Cancelled")]
    supplier = models.ForeignKey("partners.Supplier", on_delete=models.PROTECT, related_name="purchase_orders")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="purchase_orders")
    date = models.DateField(default=timezone.localdate)
    expected_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default="draft", db_index=True)
    currency = models.CharField(max_length=3, default="PKR")
    subtotal = models.DecimalField(**MONEY)
    discount = models.DecimalField(**MONEY)
    tax1_total = models.DecimalField(**MONEY)
    tax2_total = models.DecimalField(**MONEY)
    freight = models.DecimalField(**MONEY)
    total = models.DecimalField(**MONEY)
    supplier_ref = models.CharField(max_length=60, blank=True)
    reference = models.CharField(max_length=120, blank=True, help_text="Our title / reference for this order, e.g. 'Lahore Distribution Order 20-9-26'")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        unique_together = [("tenant", "number")]

    def __str__(self):
        return self.number


class PurchaseOrderLine(TaxLineMixin, TenantModel):
    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    packaging = models.ForeignKey("catalog.Packaging", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    qty_ordered = models.DecimalField(**QTY)  # base units
    qty_received = models.DecimalField(**QTY)
    unit_price = models.DecimalField(**MONEY)  # per base unit
    discount_pct = models.DecimalField(max_digits=7, decimal_places=3, default=Decimal("0"))
    line_total = models.DecimalField(**MONEY)
    sequence = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sequence", "id"]

    closed = models.BooleanField(default=False, help_text="The rest is not coming: this line counts as finished even if short")
    closed_reason = models.CharField(max_length=200, blank=True)

    @property
    def qty_pending(self):
        """What we are still waiting for — nothing, once the line has been closed short."""
        if self.closed:
            return Decimal("0")
        return self.qty_ordered - self.qty_received


class GoodsReceipt(DocumentModel):
    STATUS = [("draft", "Draft"), ("posted", "Posted"), ("cancelled", "Cancelled")]
    po = models.ForeignKey(PurchaseOrder, null=True, blank=True, on_delete=models.SET_NULL, related_name="grns")
    supplier = models.ForeignKey("partners.Supplier", on_delete=models.PROTECT, related_name="grns")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="grns")
    date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=10, choices=STATUS, default="draft", db_index=True)
    supplier_challan_no = models.CharField(max_length=60, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    posted_at = models.DateTimeField(null=True, blank=True)
    journal = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    total_value = models.DecimalField(**MONEY)
    billed = models.BooleanField(default=False)

    class Meta:
        ordering = ["-date", "-created_at"]
        unique_together = [("tenant", "number")]
        constraints = [
            # An order collects several receipts over time — partial deliveries are normal in this trade — but only
            # one of them is ever being keyed. A second *draft* against the same order is the same delivery entered
            # twice, so the database refuses it even if some future code path forgets to reuse the open one.
            models.UniqueConstraint(
                fields=["tenant", "po"],
                condition=models.Q(status="draft", po__isnull=False, deleted_at__isnull=True),
                name="one_open_grn_per_po",
            ),
        ]

    def __str__(self):
        return self.number


class GoodsReceiptLine(TenantModel):
    grn = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="lines")
    po_line = models.ForeignKey(PurchaseOrderLine, null=True, blank=True, on_delete=models.SET_NULL, related_name="grn_lines")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    packaging = models.ForeignKey("catalog.Packaging", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    qty_accepted = models.DecimalField(**QTY)
    qty_damaged = models.DecimalField(**QTY, help_text="Arrived but rejected — goes to quarantine, never into sellable stock")
    qty_short = models.DecimalField(**QTY, help_text="Did not arrive at all")
    reject_reason = models.CharField(max_length=200, blank=True, help_text="Why it was rejected or missing")
    note = models.CharField(max_length=200, blank=True, help_text="Anything worth remembering about this line")
    batch_no = models.CharField(max_length=60, blank=True)
    mfg_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    batch = models.ForeignKey("inventory.Batch", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    bin = models.ForeignKey("inventory.Bin", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    unit_cost = models.DecimalField(**MONEY)
    landed_cost_share = models.DecimalField(**MONEY)
    sequence = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sequence", "id"]


class LandedCost(TenantModel):
    TYPES = [("freight", "Freight"), ("customs", "Customs"), ("other", "Other")]
    ALLOC = [("by_qty", "By quantity"), ("by_value", "By value")]
    grn = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="landed_costs")
    type = models.CharField(max_length=10, choices=TYPES, default="freight")
    description = models.CharField(max_length=120, blank=True)
    amount = models.DecimalField(**MONEY)
    allocation = models.CharField(max_length=10, choices=ALLOC, default="by_value")
    vendor = models.CharField(max_length=120, blank=True)


class PurchaseBill(DocumentModel):
    STATUS = [("draft", "Draft"), ("posted", "Posted"), ("partially_paid", "Partially paid"), ("paid", "Paid"), ("cancelled", "Cancelled")]
    supplier = models.ForeignKey("partners.Supplier", on_delete=models.PROTECT, related_name="bills")
    grns = models.ManyToManyField(GoodsReceipt, blank=True, related_name="bills")
    bill_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)
    supplier_invoice_no = models.CharField(max_length=60, blank=True)
    subtotal = models.DecimalField(**MONEY)
    discount = models.DecimalField(**MONEY)
    tax1_type = models.ForeignKey("catalog.TaxType", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    tax1_total = models.DecimalField(**MONEY)
    tax2_type = models.ForeignKey("catalog.TaxType", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    tax2_total = models.DecimalField(**MONEY)
    freight = models.DecimalField(**MONEY)
    total = models.DecimalField(**MONEY)
    paid_amount = models.DecimalField(**MONEY)
    status = models.CharField(max_length=20, choices=STATUS, default="draft", db_index=True)
    journal = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    lines = models.JSONField(default=list, blank=True)  # snapshot of billed lines

    class Meta:
        ordering = ["-bill_date", "-created_at"]
        unique_together = [("tenant", "number")]

    def __str__(self):
        return self.number

    @property
    def balance(self):
        return self.total - self.paid_amount


class PurchaseReturn(DocumentModel):
    STATUS = [("draft", "Draft"), ("posted", "Posted"), ("cancelled", "Cancelled")]
    supplier = models.ForeignKey("partners.Supplier", on_delete=models.PROTECT, related_name="purchase_returns")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+")
    grn = models.ForeignKey(GoodsReceipt, null=True, blank=True, on_delete=models.SET_NULL, related_name="returns")
    date = models.DateField(default=timezone.localdate)
    reason_code = models.ForeignKey("inventory.ReasonCode", null=True, blank=True, on_delete=models.PROTECT, related_name="purchase_returns")
    reason = models.CharField(max_length=200, blank=True, help_text="Free-text detail on top of the reason code")
    status = models.CharField(max_length=10, choices=STATUS, default="draft", db_index=True)
    total = models.DecimalField(**MONEY)
    journal = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    is_debit_note_only = models.BooleanField(default=False, help_text="Claim without stock movement (e.g. damaged goods claim)")

    class Meta:
        ordering = ["-date", "-created_at"]
        unique_together = [("tenant", "number")]

    def __str__(self):
        return self.number


class PurchaseReturnLine(TenantModel):
    ret = models.ForeignKey(PurchaseReturn, on_delete=models.CASCADE, related_name="lines")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey("inventory.Batch", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    qty = models.DecimalField(**QTY)
    unit_cost = models.DecimalField(**MONEY)
    reason_code = models.ForeignKey("inventory.ReasonCode", null=True, blank=True, on_delete=models.PROTECT, related_name="purchase_return_lines")
    reason = models.CharField(max_length=120, blank=True)
