from rest_framework import serializers

from .models import CreditNote, CreditNoteLine, RepCollection, RepSettlement, SalesInvoice, SalesInvoiceLine, SalesOrder, SalesOrderLine


class OrderLineSerializer(serializers.ModelSerializer):
    variant_name = serializers.CharField(source="variant.name", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)
    packaging_name = serializers.CharField(source="packaging.name", read_only=True, default="")
    uom = serializers.CharField(source="variant.product.base_uom.code", read_only=True)
    qty = serializers.DecimalField(max_digits=18, decimal_places=3, write_only=True, required=False)
    price_per_packaging = serializers.BooleanField(write_only=True, required=False, default=False)
    qty_pending = serializers.DecimalField(max_digits=18, decimal_places=3, read_only=True)
    # `qty_ordered` has always been in base units — `_base_qty()` converts on the way in — but a client that
    # read it next to `packaging_name` could reasonably render "36 box" for 36 pieces. `qty_base` says what it
    # is in its name, and `packaging_qty_in_base` is the pack factor, so a carton figure can be shown too.
    qty_base = serializers.DecimalField(source="qty_ordered", max_digits=18, decimal_places=3, read_only=True)
    packaging_qty_in_base = serializers.DecimalField(source="packaging.qty_in_base", max_digits=18, decimal_places=3, read_only=True, default=None)
    tax1_name = serializers.CharField(source="tax1_type.name", read_only=True, default="")
    tax2_name = serializers.CharField(source="tax2_type.name", read_only=True, default="")

    class Meta:
        model = SalesOrderLine
        fields = ["id", "variant", "variant_name", "variant_sku", "packaging", "packaging_name", "packaging_qty_in_base", "uom", "qty", "qty_ordered", "qty_base", "qty_allocated", "qty_dispatched", "qty_delivered", "qty_returned", "qty_pending", "free_qty", "unit_price", "requested_price", "price_per_packaging", "discount_pct", "discount_amount", "scheme_discount", "scheme_names", "tax1_type", "tax1_name", "tax1_rate", "tax1_amount", "tax2_type", "tax2_name", "tax2_rate", "tax2_amount", "line_total", "sequence"]
        read_only_fields = ["id", "qty_ordered", "qty_allocated", "qty_dispatched", "qty_delivered", "qty_returned", "free_qty", "discount_amount", "scheme_discount", "scheme_names", "tax1_type", "tax1_rate", "tax1_amount", "tax2_type", "tax2_rate", "tax2_amount", "line_total", "sequence"]
        extra_kwargs = {"unit_price": {"required": False}}


class SalesOrderListSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    customer_code = serializers.CharField(source="customer.code", read_only=True)
    rep_name = serializers.CharField(source="rep.name", read_only=True, default="")
    supplier_name = serializers.CharField(source="supplier.name", read_only=True, default="")
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True, default="")
    trip_number = serializers.CharField(source="trip.number", read_only=True, default="")
    lines_count = serializers.IntegerField(source="lines.count", read_only=True)
    sales_person = serializers.SerializerMethodField()

    def get_sales_person(self, obj):
        # the rep who took it, or the sales rep on its trip — never created_by (whoever imported it)
        if obj.rep_id:
            return obj.rep.name
        if obj.trip_id and obj.trip.rep_id:
            return obj.trip.rep.name
        return ""

    class Meta:
        model = SalesOrder
        fields = ["id", "number", "customer", "customer_name", "customer_code", "rep", "rep_name", "sales_person", "supplier", "supplier_name", "source", "warehouse", "warehouse_name", "order_date", "requested_delivery_date", "status", "total", "trip", "trip_number", "external_ref", "lines_count", "hold_reason", "created_at"]


class SalesOrderSerializer(SalesOrderListSerializer):
    lines = OrderLineSerializer(many=True)
    gps = serializers.SerializerMethodField()   # where the rep was standing when they took it
    confirmed_by_name = serializers.CharField(source="confirmed_by.full_name", read_only=True, default="")
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default="")
    customer_phone = serializers.CharField(source="customer.phone", read_only=True)
    customer_address = serializers.CharField(source="customer.address", read_only=True)
    driver_name = serializers.CharField(source="trip.driver.name", read_only=True, default="")
    vehicle_reg = serializers.CharField(source="trip.vehicle.reg_no", read_only=True, default="")
    delivery_person = serializers.CharField(source="trip.delivery_rep.full_name", read_only=True, default="")
    payment_terms = serializers.SerializerMethodField()
    invoices = serializers.SerializerMethodField()
    delivery_notes = serializers.SerializerMethodField()

    def get_payment_terms(self, obj):
        days = getattr(obj.customer, "credit_days", 0) or 0
        return f"{days} days credit" if days else "Cash on delivery"

    class Meta(SalesOrderListSerializer.Meta):
        fields = SalesOrderListSerializer.Meta.fields + ["payment_terms", "delivery_person", "gps", "gps_accuracy_m", "version", "price_list", "currency", "subtotal", "discount", "scheme_discount", "tax1_total", "tax2_total", "rejection_reason", "confirmed_by", "confirmed_by_name", "confirmed_at", "dispatched_at", "delivered_at", "price_flags", "cost_centre", "apply_customer_discount", "notes", "lines", "created_by_name", "customer_phone", "customer_address", "driver_name", "vehicle_reg", "invoices", "delivery_notes", "updated_at"]
        read_only_fields = ["id", "number", "version", "status", "currency", "subtotal", "discount", "scheme_discount", "tax1_total", "tax2_total", "total", "trip", "rejection_reason", "confirmed_by", "confirmed_at", "dispatched_at", "delivered_at", "price_flags", "hold_reason", "gps", "gps_accuracy_m", "created_at", "updated_at"]

    def get_gps(self, obj):
        return {"lat": float(obj.gps_lat), "lng": float(obj.gps_lng)} if obj.gps_lat is not None else None

    def get_invoices(self, obj):
        return [{"id": str(i.id), "number": i.number, "status": i.status, "total": str(i.total), "paid_amount": str(i.paid_amount)} for i in obj.invoices.all()]

    def get_delivery_notes(self, obj):
        return [{"id": str(d.id), "number": d.number, "status": d.status, "trip": d.trip.number if d.trip_id else None, "delivered_at": d.delivered_at} for d in obj.delivery_notes.all()]


class InvoiceLineSerializer(serializers.ModelSerializer):
    variant_name = serializers.CharField(source="variant.name", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True, default="")
    tax1_name = serializers.CharField(source="tax1_type.name", read_only=True, default="")
    tax2_name = serializers.CharField(source="tax2_type.name", read_only=True, default="")

    class Meta:
        model = SalesInvoiceLine
        fields = ["id", "variant", "variant_name", "variant_sku", "batch", "batch_no", "description", "qty", "free_qty", "unit_price", "unit_cost", "discount_amount", "tax1_name", "tax1_rate", "tax1_amount", "tax2_name", "tax2_rate", "tax2_amount", "line_total"]


class SalesInvoiceEditSerializer(serializers.ModelSerializer):
    """
    The parts of a posted invoice that may still be corrected: when it falls due, and what is written on it.

    Not the amounts. An invoice is posted to the ledger the moment it is raised, and its figures are owed by the
    customer and sitting in receivables — changing them behind the journal would put the two out of step with
    nothing to show why. A wrong amount is corrected with a credit note, which is a document the customer also
    holds. Not the date either: that is the journal's date and moves the entry between accounting periods.
    """

    # a draft may also have its date and its prices corrected; a posted one may not, and says why
    date = serializers.DateField(required=False)
    lines = serializers.ListField(child=serializers.DictField(), required=False, write_only=True)

    class Meta:
        model = SalesInvoice
        fields = ["due_date", "notes", "date", "lines"]

    def validate(self, attrs):
        draft_only = [f for f in ("date", "lines") if f in attrs]
        if draft_only and self.instance and self.instance.status != "draft":
            raise serializers.ValidationError({
                draft_only[0]: [
                    f"{self.instance.number} is already posted, so its {'date' if draft_only[0] == 'date' else 'figures'} "
                    "cannot change — the entry is in the ledger and the amount is owed by the customer. Raise a credit "
                    "note instead; the due date and the notes can still be corrected here."
                ]
            })
        return attrs

    def validate_due_date(self, value):
        inv = self.instance
        if value and inv and value < inv.date:
            raise serializers.ValidationError(f"The due date cannot be before the invoice date ({inv.date:%d/%m/%Y}).")
        return value


class SalesInvoiceSerializer(serializers.ModelSerializer):
    lines = InvoiceLineSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    customer_code = serializers.CharField(source="customer.code", read_only=True)
    order_number = serializers.CharField(source="order.number", read_only=True, default="")
    delivery_note_number = serializers.CharField(source="delivery_note.number", read_only=True, default="")
    # the load form the stock was issued on — keeps the chain order → load form → issue → invoice readable
    picklist = serializers.UUIDField(source="delivery_note.picklist_id", read_only=True, default=None)
    picklist_number = serializers.CharField(source="delivery_note.picklist.number", read_only=True, default="")
    trip = serializers.UUIDField(source="delivery_note.trip_id", read_only=True, default=None)
    trip_number = serializers.CharField(source="delivery_note.trip.number", read_only=True, default="")
    rep_name = serializers.CharField(source="rep.name", read_only=True, default="")
    sales_person = serializers.SerializerMethodField()
    balance = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)
    journal_number = serializers.CharField(source="journal.number", read_only=True, default="")
    tax1_name = serializers.CharField(source="tax1_type.name", read_only=True, default="")
    tax2_name = serializers.CharField(source="tax2_type.name", read_only=True, default="")

    
    def get_sales_person(self, obj):
        if obj.rep_id:
            return obj.rep.name
        order = obj.order
        if order:
            if order.rep_id:
                return order.rep.name
            if order.trip_id and order.trip.rep_id:
                return order.trip.rep.name
        dn = obj.delivery_note
        if dn and dn.trip_id and dn.trip.rep_id:
            return dn.trip.rep.name
        return ""

    class Meta:
        model = SalesInvoice
        fields = ["id", "number", "version", "customer", "customer_name", "customer_code", "order", "order_number", "delivery_note", "delivery_note_number", "picklist", "picklist_number", "trip", "trip_number", "rep", "rep_name", "sales_person", "date", "due_date", "subtotal", "discount", "tax1_type", "tax1_name", "tax1_total", "tax2_type", "tax2_name", "tax2_total", "total", "paid_amount", "balance", "status", "journal", "journal_number", "notes", "lines", "created_at"]
        read_only_fields = fields


class CreditNoteLineSerializer(serializers.ModelSerializer):
    variant_name = serializers.CharField(source="variant.name", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)

    class Meta:
        model = CreditNoteLine
        fields = ["id", "variant", "variant_name", "variant_sku", "batch", "qty", "unit_price", "unit_cost", "reason", "restock", "condition", "tax1_amount", "tax2_amount", "line_total"]
        read_only_fields = ["id", "tax1_amount", "tax2_amount", "line_total"]
        extra_kwargs = {"unit_cost": {"required": False}}


class CreditNoteSerializer(serializers.ModelSerializer):
    lines = CreditNoteLineSerializer(many=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    invoice_number = serializers.CharField(source="invoice.number", read_only=True, default="")
    journal_number = serializers.CharField(source="journal.number", read_only=True, default="")

    class Meta:
        model = CreditNote
        fields = ["id", "number", "version", "customer", "customer_name", "invoice", "invoice_number", "warehouse", "date", "reason", "subtotal", "tax1_total", "tax2_total", "total", "status", "journal", "journal_number", "applied_to_invoice", "notes", "lines", "created_at"]
        read_only_fields = ["id", "number", "version", "subtotal", "tax1_total", "tax2_total", "total", "status", "journal", "created_at"]


class RepCollectionSerializer(serializers.ModelSerializer):
    rep_name = serializers.CharField(source="rep.name", read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    settlement_number = serializers.CharField(source="settlement.number", read_only=True, default="")
    recorded_by_name = serializers.CharField(source="recorded_by.full_name", read_only=True, default="")
    gps = serializers.DictField(write_only=True, required=False)

    class Meta:
        model = RepCollection
        fields = ["id", "number", "rep", "rep_name", "customer", "customer_name", "date", "amount", "method", "reference", "allocations", "status", "journal", "settlement", "settlement_number", "recorded_by_name", "gps_lat", "gps_lng", "gps_accuracy_m", "gps", "notes", "created_at"]
        read_only_fields = ["id", "number", "status", "journal", "settlement", "gps_lat", "gps_lng", "gps_accuracy_m", "created_at"]
        extra_kwargs = {"rep": {"required": False}, "allocations": {"required": False}}


class RepSettlementSerializer(serializers.ModelSerializer):
    rep_name = serializers.CharField(source="rep.name", read_only=True)
    received_by_name = serializers.CharField(source="received_by.full_name", read_only=True, default="")
    collections = serializers.PrimaryKeyRelatedField(queryset=RepCollection.objects.all(), many=True, write_only=True)
    collection_numbers = serializers.SerializerMethodField()
    journal_number = serializers.CharField(source="journal.number", read_only=True, default="")

    class Meta:
        model = RepSettlement
        fields = ["id", "number", "rep", "rep_name", "date", "amount", "received_by", "received_by_name", "cash_account", "bank_account", "journal", "journal_number", "collections", "collection_numbers", "notes", "created_at"]
        read_only_fields = ["id", "number", "amount", "received_by", "journal", "created_at"]

    def get_collection_numbers(self, obj):
        return [{"id": str(c.id), "number": c.number, "customer": c.customer.name, "amount": str(c.amount), "date": c.date} for c in obj.collections.select_related("customer")]
