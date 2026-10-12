from rest_framework import serializers

from .models import GoodsReceipt, GoodsReceiptLine, LandedCost, PurchaseBill, PurchaseOrder, PurchaseOrderLine, PurchaseReturn, PurchaseReturnLine


class POLineSerializer(serializers.ModelSerializer):
    """A line as the buying screens need it: codes, the box size, where it is normally put away."""

    variant_name = serializers.CharField(source="variant.name", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)
    packaging_name = serializers.CharField(source="packaging.name", read_only=True, default="")
    qty_pending = serializers.DecimalField(max_digits=18, decimal_places=3, read_only=True)
    qty = serializers.DecimalField(max_digits=18, decimal_places=3, write_only=True, required=False)
    price_per_packaging = serializers.BooleanField(write_only=True, required=False, default=False)
    tax1_name = serializers.CharField(source="tax1_type.name", read_only=True, default="")
    tax2_name = serializers.CharField(source="tax2_type.name", read_only=True, default="")
    unit_size = serializers.CharField(source="variant.product.unit_size", read_only=True, default="")
    packing = serializers.IntegerField(source="variant.product.packing", read_only=True, default=1)
    item_code = serializers.CharField(source="variant.product.item_code", read_only=True, default="")
    upc = serializers.CharField(source="variant.product.upc", read_only=True, default="")
    default_bin = serializers.PrimaryKeyRelatedField(source="variant.product.location", read_only=True)
    default_bin_name = serializers.SerializerMethodField()

    def get_default_bin_name(self, obj):
        bin_ = obj.variant.product.location
        return f"{bin_.warehouse.code} / {bin_.code}" if bin_ else ""

    class Meta:
        model = PurchaseOrderLine
        fields = ["id", "variant", "variant_name", "variant_sku", "item_code", "upc", "unit_size", "packing", "default_bin", "default_bin_name", "closed", "closed_reason", "packaging", "packaging_name", "qty", "qty_ordered", "qty_received", "qty_pending", "unit_price", "price_per_packaging", "discount_pct", "tax1_type", "tax1_name", "tax1_rate", "tax1_amount", "tax2_type", "tax2_name", "tax2_rate", "tax2_amount", "line_total", "sequence"]
        read_only_fields = ["id", "qty_ordered", "qty_received", "tax1_type", "tax1_rate", "tax1_amount", "tax2_type", "tax2_rate", "tax2_amount", "line_total", "sequence"]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    lines = POLineSerializer(many=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    grns_count = serializers.SerializerMethodField()
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default="")
    approved_by_name = serializers.CharField(source="approved_by.full_name", read_only=True, default="")

    def get_grns_count(self, obj):
        """How many receipts this order carries. A cancelled one was undone — counting it reads as a duplicate on the order page."""
        return obj.grns.exclude(status="cancelled").count()

    class Meta:
        model = PurchaseOrder
        fields = ["id", "number", "version", "supplier", "supplier_name", "warehouse", "warehouse_name", "date", "expected_date", "status", "currency", "subtotal", "discount", "tax1_total", "tax2_total", "freight", "total", "supplier_ref", "reference", "notes", "lines", "grns_count", "created_by_name", "approved_by", "approved_by_name", "approved_at", "created_at", "updated_at"]
        read_only_fields = ["id", "number", "version", "status", "currency", "subtotal", "discount", "tax1_total", "tax2_total", "total", "approved_by", "approved_at", "created_at", "updated_at"]


class POListSerializer(serializers.ModelSerializer):
    """
    The list and the order pickers. A purchase order can carry hundreds of lines, so the list leaves them out and
    sends the counts instead: how many lines, how many receipts, and how many units are still due.
    """

    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default="")
    approved_by_name = serializers.CharField(source="approved_by.full_name", read_only=True, default="")
    lines_count = serializers.IntegerField(read_only=True)
    grns_count = serializers.IntegerField(read_only=True)
    units_due = serializers.DecimalField(max_digits=18, decimal_places=3, read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = ["id", "number", "version", "supplier", "supplier_name", "warehouse", "warehouse_name", "date", "expected_date", "status", "currency", "subtotal", "discount", "tax1_total", "tax2_total", "freight", "total", "supplier_ref", "reference", "notes", "lines_count", "grns_count", "units_due", "created_by_name", "approved_by", "approved_by_name", "approved_at", "created_at", "updated_at"]


class GRNLineSerializer(serializers.ModelSerializer):
    variant_name = serializers.CharField(source="variant.name", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)
    packaging_name = serializers.CharField(source="packaging.name", read_only=True, default="")
    is_batch_tracked = serializers.BooleanField(source="variant.product.is_batch_tracked", read_only=True)
    is_expiry_tracked = serializers.BooleanField(source="variant.product.is_expiry_tracked", read_only=True)
    price_per_packaging = serializers.BooleanField(write_only=True, required=False, default=False)
    packing = serializers.IntegerField(source="variant.product.packing", read_only=True, default=1)
    item_code = serializers.CharField(source="variant.product.item_code", read_only=True, default="")
    bin_name = serializers.SerializerMethodField()
    # "the rest is not coming" — closes the order line this receipt came from
    close_line = serializers.BooleanField(write_only=True, required=False, default=False)

    def get_bin_name(self, obj):
        return f"{obj.bin.warehouse.code} / {obj.bin.code}" if obj.bin_id else ""

    class Meta:
        model = GoodsReceiptLine
        fields = ["id", "po_line", "variant", "variant_name", "variant_sku", "item_code", "packing", "packaging", "packaging_name", "qty_accepted", "qty_damaged", "qty_short", "reject_reason", "note", "close_line", "batch_no", "mfg_date", "expiry_date", "batch", "bin", "bin_name", "unit_cost", "price_per_packaging", "landed_cost_share", "is_batch_tracked", "is_expiry_tracked", "sequence"]
        read_only_fields = ["id", "batch", "landed_cost_share", "sequence"]
        extra_kwargs = {"unit_cost": {"required": False}}


class LandedCostSerializer(serializers.ModelSerializer):
    class Meta:
        model = LandedCost
        fields = ["id", "type", "description", "amount", "allocation", "vendor"]
        read_only_fields = ["id"]


class GoodsReceiptSerializer(serializers.ModelSerializer):
    lines = GRNLineSerializer(many=True)
    landed_costs = LandedCostSerializer(many=True, required=False)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    po_number = serializers.CharField(source="po.number", read_only=True, default="")
    received_by_name = serializers.CharField(source="received_by.full_name", read_only=True, default="")
    journal_number = serializers.CharField(source="journal.number", read_only=True, default="")
    reused = serializers.SerializerMethodField()
    bills = serializers.SerializerMethodField()

    def get_reused(self, obj):
        """True when this receipt was already open against the order and was filled in, rather than a second one being created."""
        return bool(getattr(obj, "reused", False))

    def get_bills(self, obj):
        """
        The supplier bills this receipt is on — it knew it was `billed` but not by what.

        Without this the screen fetched a hundred of the supplier's bills and matched on their receipt numbers
        to draw one link. A billed receipt also cannot be edited, and "which bill?" is the first thing anyone
        asks when an edit is refused.
        """
        return [{"id": str(b.pk), "number": b.number, "status": b.status, "date": b.bill_date, "total": str(b.total)}
                for b in obj.bills.all()]

    class Meta:
        model = GoodsReceipt
        fields = ["id", "number", "version", "po", "po_number", "supplier", "supplier_name", "warehouse", "warehouse_name", "date", "status", "supplier_challan_no", "received_by", "received_by_name", "posted_at", "journal", "journal_number", "total_value", "billed", "reused", "notes", "lines", "landed_costs", "created_at", "updated_at", "bills"]
        read_only_fields = ["id", "number", "version", "status", "received_by", "posted_at", "journal", "total_value", "billed", "created_at", "updated_at"]
        # goods are received against a purchase order, which says who they came from
        extra_kwargs = {"supplier": {"required": False}}


class PurchaseBillSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    grn_numbers = serializers.SerializerMethodField()
    balance = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)
    journal_number = serializers.CharField(source="journal.number", read_only=True, default="")
    tax1_name = serializers.CharField(source="tax1_type.name", read_only=True, default="")
    tax2_name = serializers.CharField(source="tax2_type.name", read_only=True, default="")

    class Meta:
        model = PurchaseBill
        fields = ["id", "number", "version", "supplier", "supplier_name", "grns", "grn_numbers", "bill_date", "due_date", "supplier_invoice_no", "subtotal", "discount", "tax1_type", "tax1_name", "tax1_total", "tax2_type", "tax2_name", "tax2_total", "freight", "total", "paid_amount", "balance", "status", "journal", "journal_number", "lines", "notes", "created_at", "updated_at"]
        read_only_fields = ["id", "number", "version", "subtotal", "total", "paid_amount", "status", "journal", "lines", "created_at", "updated_at"]
        extra_kwargs = {"grns": {"required": False}}

    def get_grn_numbers(self, obj):
        return [g.number for g in obj.grns.all()]


class PurchaseReturnLineSerializer(serializers.ModelSerializer):
    reason_name = serializers.CharField(source="reason_code.name", read_only=True, default="")
    variant_name = serializers.CharField(source="variant.name", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True, default="")

    class Meta:
        model = PurchaseReturnLine
        fields = ["id", "variant", "variant_name", "variant_sku", "batch", "batch_no", "qty", "unit_cost", "reason_code", "reason_name", "reason"]
        read_only_fields = ["id"]
        extra_kwargs = {"unit_cost": {"required": False}}


class PurchaseReturnSerializer(serializers.ModelSerializer):
    reason_name = serializers.CharField(source="reason_code.name", read_only=True, default="")
    lines = PurchaseReturnLineSerializer(many=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    grn_number = serializers.CharField(source="grn.number", read_only=True, default="")
    journal_number = serializers.CharField(source="journal.number", read_only=True, default="")

    class Meta:
        model = PurchaseReturn
        fields = ["id", "number", "version", "supplier", "supplier_name", "warehouse", "warehouse_name", "grn", "grn_number", "date", "reason_code", "reason_name", "reason", "status", "total", "journal", "journal_number", "is_debit_note_only", "notes", "lines", "created_at"]
        read_only_fields = ["id", "number", "version", "status", "total", "journal", "created_at"]
