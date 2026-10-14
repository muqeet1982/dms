from rest_framework import serializers

from .models import Batch, Bin, InventoryAlert, ReasonCode, StockAdjustment, StockAdjustmentLine, StockCount, StockCountLine, StockLevel, StockMovement, StockTransfer, StockTransferLine, Warehouse


class BinSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    label = serializers.SerializerMethodField()
    products_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Bin
        fields = ["id", "warehouse", "warehouse_name", "warehouse_code", "code", "name", "label", "products_count"]
        extra_kwargs = {"warehouse": {"required": False}}

    def get_label(self, obj):
        return f"{obj.warehouse.code} / {obj.code}" + (f" — {obj.name}" if obj.name else "")


class WarehouseSerializer(serializers.ModelSerializer):
    bins = BinSerializer(many=True, read_only=True)
    manager_name = serializers.CharField(source="manager.full_name", read_only=True, default="")
    vehicle_reg = serializers.CharField(source="vehicle.reg_no", read_only=True, default="")

    class Meta:
        model = Warehouse
        fields = ["id", "code", "name", "type", "address", "vehicle", "vehicle_reg", "manager", "manager_name", "allow_negative_stock", "is_default", "is_active", "bins", "created_at"]


class BatchSerializer(serializers.ModelSerializer):
    variant_name = serializers.CharField(source="variant.name", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    qty_on_hand = serializers.DecimalField(max_digits=18, decimal_places=3, read_only=True, default=None)
    # the screen marks the expiry field required off this, and the rule below enforces it
    is_expiry_tracked = serializers.BooleanField(source="variant.product.is_expiry_tracked", read_only=True)

    class Meta:
        model = Batch
        fields = ["id", "variant", "variant_name", "variant_sku", "batch_no", "mfg_date", "expiry_date", "supplier", "is_expired", "is_expiry_tracked", "qty_on_hand", "created_at"]

    def validate(self, attrs):
        """
        An expiry date, once a batch is carrying one, cannot be taken away — and a product marked expiry-tracked
        must have one.

        Picking is first-expiry-first-out and the near-expiry alerts and report read this date, so a batch
        without one is invisible to every check meant to catch it. The rule is deliberately not "always
        required": a batch number on a hairbrush or an apron is the supplier's challan number, and those cartons
        carry no expiry to type.
        """
        attrs = super().validate(attrs)
        instance = self.instance
        tracked = attrs.get("variant").product.is_expiry_tracked if attrs.get("variant") else (
            instance.variant.product.is_expiry_tracked if instance else False)
        has_now = attrs["expiry_date"] if "expiry_date" in attrs else (instance.expiry_date if instance else None)
        if not has_now:
            if tracked:
                raise serializers.ValidationError({"expiry_date": [
                    "This product is expiry-tracked, so its batches need an expiry date — read it off the carton."
                ]})
            if instance is not None and instance.expiry_date:
                raise serializers.ValidationError({"expiry_date": [
                    "This batch already has an expiry date and it is what decides when the stock is picked and "
                    "reported. Correct it rather than clearing it."
                ]})
        return attrs


class StockLevelSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    bin_code = serializers.CharField(source="bin.code", read_only=True, default="")
    variant_name = serializers.CharField(source="variant.name", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)
    product_id = serializers.UUIDField(source="variant.product_id", read_only=True)
    brand = serializers.CharField(source="variant.product.brand.name", read_only=True, default="")
    category = serializers.CharField(source="variant.product.category.name", read_only=True, default="")
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True, default="")
    expiry_date = serializers.DateField(source="batch.expiry_date", read_only=True, default=None)
    qty_available = serializers.DecimalField(max_digits=18, decimal_places=3, read_only=True)
    min_stock = serializers.DecimalField(source="variant.product.min_stock", max_digits=18, decimal_places=3, read_only=True)
    uom = serializers.CharField(source="variant.product.base_uom.code", read_only=True)
    # a warehouse counts boxes: `packing` units make one box, so the UI can show "12 box + 5"
    packing = serializers.IntegerField(source="variant.product.packing", read_only=True, default=1)
    item_code = serializers.CharField(source="variant.product.item_code", read_only=True, default="")
    upc = serializers.CharField(source="variant.product.upc", read_only=True, default="")
    value = serializers.SerializerMethodField()

    class Meta:
        model = StockLevel
        fields = ["id", "warehouse", "warehouse_name", "warehouse_code", "bin", "bin_code", "variant", "variant_name", "variant_sku", "product_id", "brand", "category", "batch", "batch_no", "expiry_date", "qty_on_hand", "qty_reserved", "qty_available", "avg_cost", "min_stock", "uom", "packing", "item_code", "upc", "value", "updated_at"]

    def get_value(self, obj):
        return str((obj.qty_on_hand * (obj.variant.avg_cost or obj.variant.cost_price)).quantize(__import__("decimal").Decimal("0.0001")))


class StockMovementSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    variant_name = serializers.CharField(source="variant.name", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True, default="")
    expiry_date = serializers.DateField(source="batch.expiry_date", read_only=True, default=None)
    upc = serializers.CharField(source="variant.product.upc", read_only=True, default="")
    item_code = serializers.CharField(source="variant.product.item_code", read_only=True, default="")
    performed_by_name = serializers.CharField(source="performed_by.full_name", read_only=True, default="")
    value = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)

    class Meta:
        model = StockMovement
        fields = ["id", "warehouse", "warehouse_name", "bin", "variant", "variant_name", "variant_sku", "upc", "item_code", "batch", "batch_no", "expiry_date", "qty", "unit_cost", "value", "movement_type", "reference_type", "reference_id", "reference_number", "occurred_at", "performed_by", "performed_by_name", "note"]


class ReasonCodeSerializer(serializers.ModelSerializer):
    expense_account_name = serializers.CharField(source="expense_account.name", read_only=True, default="")

    class Meta:
        model = ReasonCode
        fields = ["id", "code", "name", "category", "expense_account", "expense_account_name", "requires_approval", "requires_photo", "used_for", "is_active"]


class StockTransferLineSerializer(serializers.ModelSerializer):
    variant_name = serializers.CharField(source="variant.name", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True, default="")

    class Meta:
        model = StockTransferLine
        fields = ["id", "variant", "variant_name", "variant_sku", "batch", "batch_no", "qty_requested", "qty_sent", "qty_received", "qty_lost", "unit_cost"]
        read_only_fields = ["id", "qty_received", "qty_lost", "unit_cost"]


class StockTransferSerializer(serializers.ModelSerializer):
    lines = StockTransferLineSerializer(many=True)
    from_warehouse_name = serializers.CharField(source="from_warehouse.name", read_only=True)
    to_warehouse_name = serializers.CharField(source="to_warehouse.name", read_only=True)
    requested_by_name = serializers.CharField(source="requested_by.full_name", read_only=True, default="")
    approved_by_name = serializers.CharField(source="approved_by.full_name", read_only=True, default="")

    class Meta:
        model = StockTransfer
        fields = ["id", "number", "version", "from_warehouse", "from_warehouse_name", "to_warehouse", "to_warehouse_name", "date", "status", "requested_by", "requested_by_name", "approved_by", "approved_by_name", "dispatched_at", "received_at", "notes", "lines", "created_at", "updated_at"]
        read_only_fields = ["id", "number", "version", "status", "requested_by", "approved_by", "dispatched_at", "received_at", "created_at", "updated_at"]


class StockAdjustmentLineSerializer(serializers.ModelSerializer):
    variant_name = serializers.CharField(source="variant.name", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True, default="")

    class Meta:
        model = StockAdjustmentLine
        fields = ["id", "variant", "variant_name", "variant_sku", "batch", "batch_no", "bin", "qty", "unit_cost", "value", "note"]
        read_only_fields = ["id", "value"]
        extra_kwargs = {"unit_cost": {"required": False}}


class StockAdjustmentSerializer(serializers.ModelSerializer):
    lines = StockAdjustmentLineSerializer(many=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    reason_name = serializers.CharField(source="reason.name", read_only=True)
    reason_category = serializers.CharField(source="reason.category", read_only=True)
    approved_by_name = serializers.CharField(source="approved_by.full_name", read_only=True, default="")
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default="")
    journal_number = serializers.CharField(source="journal.number", read_only=True, default="")

    class Meta:
        model = StockAdjustment
        fields = ["id", "number", "version", "warehouse", "warehouse_name", "date", "type", "reason", "reason_name", "reason_category", "status", "total_value", "approved_by", "approved_by_name", "approved_at", "rejection_reason", "journal", "journal_number", "source_type", "source_id", "notes", "lines", "created_by_name", "created_at", "updated_at"]
        read_only_fields = ["id", "number", "version", "status", "total_value", "approved_by", "approved_at", "rejection_reason", "journal", "source_type", "source_id", "created_at", "updated_at"]


class StockCountLineSerializer(serializers.ModelSerializer):
    variant_name = serializers.CharField(source="variant.name", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True, default="")
    bin_code = serializers.CharField(source="bin.code", read_only=True, default="")
    variance = serializers.DecimalField(max_digits=18, decimal_places=3, read_only=True)
    counted_by_name = serializers.CharField(source="counted_by.full_name", read_only=True, default="")

    class Meta:
        model = StockCountLine
        fields = ["id", "variant", "variant_name", "variant_sku", "batch", "batch_no", "bin", "bin_code", "system_qty", "counted_qty", "variance", "counted_by_name", "counted_at", "scanned"]


class StockCountSerializer(serializers.ModelSerializer):
    lines = StockCountLineSerializer(many=True, read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    started_by_name = serializers.CharField(source="started_by.full_name", read_only=True, default="")
    adjustment_number = serializers.CharField(source="adjustment.number", read_only=True, default="")
    adjustment_increase_number = serializers.CharField(source="adjustment_increase.number", read_only=True, default="")
    progress = serializers.SerializerMethodField()

    class Meta:
        model = StockCount
        fields = ["id", "number", "warehouse", "warehouse_name", "type", "status", "scope", "started_by_name", "posted_by", "posted_at", "tolerance_pct", "adjustment", "adjustment_number", "adjustment_increase", "adjustment_increase_number", "notes", "lines", "progress", "created_at"]
        read_only_fields = ["id", "number", "status", "posted_by", "posted_at", "adjustment", "adjustment_increase", "created_at"]

    def get_progress(self, obj):
        total = obj.lines.count()
        done = obj.lines.filter(counted_qty__isnull=False).count()
        return {"total": total, "counted": done}


class InventoryAlertSerializer(serializers.ModelSerializer):
    variant_name = serializers.CharField(source="variant.name", read_only=True, default="")
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True, default="")

    class Meta:
        model = InventoryAlert
        fields = ["id", "type", "variant", "variant_name", "warehouse", "warehouse_name", "batch", "message", "data", "resolved_at", "created_at"]
