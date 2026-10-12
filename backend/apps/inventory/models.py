from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import MONEY, QTY, DocumentModel, TenantModel


class Warehouse(TenantModel):
    TYPES = [("main", "Main"), ("sub", "Sub"), ("van", "Van / In-transit"), ("quarantine", "Quarantine")]
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=100)
    type = models.CharField(max_length=12, choices=TYPES, default="main")
    address = models.TextField(blank=True)
    vehicle = models.OneToOneField("delivery.Vehicle", null=True, blank=True, on_delete=models.SET_NULL, related_name="van_warehouse")
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    allow_negative_stock = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False, help_text="Pre-selected on new orders, purchase orders and receipts")
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("tenant", "code")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class Bin(TenantModel):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name="bins")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=80, blank=True)

    class Meta:
        unique_together = [("warehouse", "code")]
        ordering = ["code"]

    def __str__(self):
        return f"{self.warehouse.code}/{self.code}"


class Batch(TenantModel):
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.CASCADE, related_name="batches")
    batch_no = models.CharField(max_length=60)
    mfg_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    supplier = models.ForeignKey("partners.Supplier", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    grn_line = models.ForeignKey("purchasing.GoodsReceiptLine", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        unique_together = [("variant", "batch_no")]
        ordering = ["expiry_date", "batch_no"]

    def __str__(self):
        return self.batch_no

    @property
    def is_expired(self):
        return bool(self.expiry_date and self.expiry_date < timezone.localdate())


class StockLevel(TenantModel):
    """Materialised sum of StockMovement per (warehouse, bin, variant, batch)."""

    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name="stock_levels")
    bin = models.ForeignKey(Bin, null=True, blank=True, on_delete=models.SET_NULL, related_name="stock_levels")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.CASCADE, related_name="stock_levels")
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.CASCADE, related_name="stock_levels")
    qty_on_hand = models.DecimalField(**QTY)
    qty_reserved = models.DecimalField(**QTY)
    avg_cost = models.DecimalField(**MONEY)

    class Meta:
        unique_together = [("warehouse", "bin", "variant", "batch")]
        indexes = [models.Index(fields=["tenant", "variant"]), models.Index(fields=["tenant", "warehouse"])]

    @property
    def qty_available(self):
        return self.qty_on_hand - self.qty_reserved


class StockMovement(TenantModel):
    """Append-only ledger. Never edited; corrections are new movements."""

    TYPES = [
        ("opening", "Opening"), ("grn", "Goods receipt"), ("purchase_return", "Purchase return"), ("sale_dispatch", "Sale dispatch"),
        ("sales_return", "Sales return"), ("transfer_out", "Transfer out"), ("transfer_in", "Transfer in"), ("adjustment", "Adjustment"),
        ("wastage", "Wastage"), ("count_variance", "Count variance"), ("delivery_return", "Delivery return"),
        # Cancelling a posted receipt takes the stock back out, and that is not a return to the supplier: nothing
        # went back on a lorry. It used to be filed as "Purchase return", so the ledger showed a return that did
        # not exist and the owner quite reasonably asked where it had come from.
        ("grn_cancelled", "Receipt cancelled"),
    ]
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="movements")
    bin = models.ForeignKey(Bin, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="movements")
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.PROTECT, related_name="movements")
    qty = models.DecimalField(max_digits=18, decimal_places=3)  # +in / -out (base unit)
    unit_cost = models.DecimalField(**MONEY)
    movement_type = models.CharField(max_length=20, choices=TYPES, db_index=True)
    reference_type = models.CharField(max_length=40, blank=True, db_index=True)
    reference_id = models.CharField(max_length=64, blank=True, db_index=True)
    reference_number = models.CharField(max_length=40, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-occurred_at", "-created_at"]
        indexes = [models.Index(fields=["tenant", "variant", "occurred_at"]), models.Index(fields=["tenant", "warehouse", "occurred_at"])]

    @property
    def value(self):
        return self.qty * self.unit_cost


class ReasonCode(TenantModel):
    CATEGORIES = [
        ("damage", "Damage"), ("expiry", "Expiry"), ("near_expiry", "Near expiry / short shelf life"), ("theft", "Theft"), ("lost", "Lost"), ("sample", "Sample"), ("promotion", "Promotion"),
        ("count", "Count variance"), ("correction", "Correction"), ("transit_damage", "Transit damage"), ("quality", "Quality complaint"), ("wrong_item", "Wrong / unordered item"),
        ("short_supply", "Short supply"), ("excess_supply", "Excess supply / overstock"), ("recall", "Supplier recall"), ("price_difference", "Price difference"), ("customer_return", "Customer return"), ("other", "Other"),
    ]
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=80)
    category = models.CharField(max_length=20, choices=CATEGORIES, default="other")
    expense_account = models.ForeignKey("accounting.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    requires_approval = models.BooleanField(default=True)
    requires_photo = models.BooleanField(default=False)
    used_for = models.JSONField(default=list, blank=True, help_text='Where the reason can be picked: ["adjustment", "purchase_return", "sales_return"]')
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("tenant", "code")]
        ordering = ["code"]

    def __str__(self):
        return self.name


class StockTransfer(DocumentModel):
    STATUS = [("requested", "Requested"), ("approved", "Approved"), ("dispatched", "Dispatched"), ("in_transit", "In transit"), ("received", "Received"), ("cancelled", "Cancelled")]
    from_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="transfers_out")
    to_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="transfers_in")
    date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=12, choices=STATUS, default="requested", db_index=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    dispatched_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        unique_together = [("tenant", "number")]

    def __str__(self):
        return self.number


class StockTransferLine(TenantModel):
    transfer = models.ForeignKey(StockTransfer, on_delete=models.CASCADE, related_name="lines")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    qty_requested = models.DecimalField(**QTY)
    qty_sent = models.DecimalField(**QTY)
    qty_received = models.DecimalField(**QTY)
    qty_lost = models.DecimalField(**QTY)
    unit_cost = models.DecimalField(**MONEY)


class StockAdjustment(DocumentModel):
    TYPES = [("increase", "Increase"), ("decrease", "Decrease")]
    STATUS = [("draft", "Draft"), ("pending_approval", "Pending approval"), ("approved", "Approved"), ("rejected", "Rejected"), ("cancelled", "Cancelled")]
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="adjustments")
    date = models.DateField(default=timezone.localdate)
    type = models.CharField(max_length=10, choices=TYPES, default="decrease")
    reason = models.ForeignKey(ReasonCode, on_delete=models.PROTECT, related_name="adjustments")
    status = models.CharField(max_length=20, choices=STATUS, default="draft", db_index=True)
    total_value = models.DecimalField(**MONEY)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=200, blank=True)
    journal = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    source_type = models.CharField(max_length=40, blank=True)
    source_id = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        unique_together = [("tenant", "number")]

    def __str__(self):
        return self.number


class StockAdjustmentLine(TenantModel):
    adjustment = models.ForeignKey(StockAdjustment, on_delete=models.CASCADE, related_name="lines")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    bin = models.ForeignKey(Bin, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    qty = models.DecimalField(**QTY)
    unit_cost = models.DecimalField(**MONEY)
    value = models.DecimalField(**MONEY)
    note = models.CharField(max_length=200, blank=True)


class StockCount(DocumentModel):
    TYPES = [("full", "Full"), ("cycle", "Cycle")]
    STATUS = [("open", "Open"), ("counting", "Counting"), ("review", "Review"), ("posted", "Posted"), ("cancelled", "Cancelled")]
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="counts")
    type = models.CharField(max_length=10, choices=TYPES, default="full")
    status = models.CharField(max_length=10, choices=STATUS, default="open", db_index=True)
    scope = models.JSONField(default=dict, blank=True)  # {"categories": [...], "bins": [...]}
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    posted_at = models.DateTimeField(null=True, blank=True)
    tolerance_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("2"))
    adjustment = models.ForeignKey(StockAdjustment, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    adjustment_increase = models.ForeignKey(StockAdjustment, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("tenant", "number")]

    def __str__(self):
        return self.number


class StockCountLine(TenantModel):
    count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name="lines")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    bin = models.ForeignKey(Bin, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    system_qty = models.DecimalField(**QTY)
    counted_qty = models.DecimalField(max_digits=18, decimal_places=3, null=True, blank=True)
    counted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    counted_at = models.DateTimeField(null=True, blank=True)
    scanned = models.BooleanField(default=False)

    @property
    def variance(self):
        return (self.counted_qty - self.system_qty) if self.counted_qty is not None else None


class InventoryAlert(TenantModel):
    TYPES = [("low_stock", "Low stock"), ("reorder", "Reorder"), ("expiring", "Expiring"), ("expired", "Expired"), ("dead_stock", "Dead stock"), ("negative", "Negative stock"), ("drift", "Ledger drift")]
    type = models.CharField(max_length=15, choices=TYPES, db_index=True)
    variant = models.ForeignKey("catalog.ProductVariant", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    message = models.CharField(max_length=300)
    data = models.JSONField(default=dict, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
