from rest_framework import serializers

from .models import DeliveryNote, DeliveryNoteLine, DeliveryTrip, Driver, PickList, PickListLine, Vehicle, VehicleLog


class VehicleSerializer(serializers.ModelSerializer):
    trips_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Vehicle
        fields = ["id", "reg_no", "make_model", "type", "capacity_cartons", "owner_type", "insurance_expiry", "fitness_expiry", "tracker_id", "is_active", "notes", "trips_count", "created_at"]


class DriverSerializer(serializers.ModelSerializer):
    default_vehicle_reg = serializers.CharField(source="default_vehicle.reg_no", read_only=True, default="")
    user_email = serializers.CharField(source="user.email", read_only=True, default="")
    trips_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Driver
        fields = ["id", "user", "user_email", "name", "phone", "cnic", "license_no", "license_expiry", "default_vehicle", "default_vehicle_reg", "is_active", "notes", "trips_count", "created_at"]


class VehicleLogSerializer(serializers.ModelSerializer):
    trip_number = serializers.CharField(source="trip.number", read_only=True, default="")

    class Meta:
        model = VehicleLog
        fields = ["id", "vehicle", "trip", "trip_number", "date", "odometer_start", "odometer_end", "fuel_amount", "expense", "note"]


class PickListLineSerializer(serializers.ModelSerializer):
    variant_name = serializers.CharField(source="variant.name", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True, default="")
    bin_code = serializers.CharField(source="bin.code", read_only=True, default="")
    order_number = serializers.CharField(source="order_line.order.number", read_only=True, default="")

    class Meta:
        model = PickListLine
        fields = ["id", "order_line", "order_number", "variant", "variant_name", "variant_sku", "batch", "batch_no", "bin", "bin_code", "qty_to_pick", "qty_picked", "scanned_at"]


class PickListSerializer(serializers.ModelSerializer):
    lines = PickListLineSerializer(many=True, read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    picker_name = serializers.CharField(source="picker.full_name", read_only=True, default="")
    sent_by_name = serializers.CharField(source="sent_by.full_name", read_only=True, default="")
    issued_by_name = serializers.CharField(source="issued_by.full_name", read_only=True, default="")
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    delivery_note_numbers = serializers.SerializerMethodField()
    order_numbers = serializers.SerializerMethodField()
    customer_names = serializers.SerializerMethodField()

    def get_customer_names(self, obj):
        seen = []
        for o in obj.orders.all():
            if o.customer_id and o.customer.name not in seen:
                seen.append(o.customer.name)
        return seen

    class Meta:
        model = PickList
        fields = ["id", "number", "warehouse", "warehouse_name", "orders", "order_numbers", "customer_names", "status", "status_label", "picker", "picker_name", "picked_at", "sent_at", "sent_by", "sent_by_name", "issued_at", "issued_by", "issued_by_name", "delivery_note_numbers", "notes", "lines", "created_at"]
        read_only_fields = ["id", "number", "status", "picker", "picked_at", "sent_at", "sent_by", "issued_at", "issued_by", "created_at"]

    def get_order_numbers(self, obj):
        return [o.number for o in obj.orders.all()]

    def get_delivery_note_numbers(self, obj):
        return [{"id": str(d.id), "number": d.number} for d in obj.delivery_notes.all()]


class DeliveryNoteLineSerializer(serializers.ModelSerializer):
    variant_name = serializers.CharField(source="variant.name", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True, default="")
    unit_price = serializers.DecimalField(source="order_line.unit_price", max_digits=18, decimal_places=4, read_only=True)

    class Meta:
        model = DeliveryNoteLine
        fields = ["id", "order_line", "variant", "variant_name", "variant_sku", "batch", "batch_no", "qty_loaded", "qty_delivered", "qty_returned", "return_reason", "return_condition", "unit_cost", "unit_price"]


class DeliveryNoteSerializer(serializers.ModelSerializer):
    lines = DeliveryNoteLineSerializer(many=True, read_only=True)
    order_number = serializers.CharField(source="order.number", read_only=True)
    order_total = serializers.DecimalField(source="order.total", max_digits=18, decimal_places=4, read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    customer_phone = serializers.CharField(source="customer.phone", read_only=True)
    customer_address = serializers.SerializerMethodField()

    def get_customer_address(self, obj):
        # a multi-shop account's goods go to the SHOP the order was taken at, not the head office
        loc = obj.order.location if obj.order_id else None
        if loc is not None:
            return f"{loc.name} — {loc.address}" if loc.address else loc.name
        return obj.customer.address
    customer_gps = serializers.SerializerMethodField()
    gps = serializers.SerializerMethodField()   # where the driver was when the stop was signed for
    trip_number = serializers.CharField(source="trip.number", read_only=True, default="")
    picklist_number = serializers.CharField(source="picklist.number", read_only=True, default="")
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    invoice_number = serializers.CharField(source="invoice.number", read_only=True, default="")
    invoice_id = serializers.UUIDField(source="invoice.id", read_only=True, default=None)
    # a delivery can bill a DRAFT for the office to review (27 Oct 2026) — the driver app words its message by this
    invoice_status = serializers.CharField(source="invoice.status", read_only=True, default="")
    invoice_total = serializers.DecimalField(source="invoice.total", max_digits=18, decimal_places=4, read_only=True, default=None)
    delivered_by_name = serializers.CharField(source="delivered_by.full_name", read_only=True, default="")
    signature_url = serializers.SerializerMethodField()
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = DeliveryNote
        fields = ["id", "number", "trip", "trip_number", "order", "picklist", "picklist_number", "status_label", "remarks", "order_number", "order_total", "customer", "customer_name", "customer_phone", "customer_address", "customer_gps", "warehouse", "sequence", "status", "delivered_at", "delivered_by_name", "receiver_name", "signature_url", "photo_url", "gps_lat", "gps_lng", "gps_accuracy_m", "gps", "failure_reason", "cash_collected", "collected_by", "stock_out_done", "invoice_id", "invoice_number", "invoice_status", "invoice_total", "notes", "lines", "created_at"]
        read_only_fields = fields

    def get_gps(self, obj):
        return {"lat": float(obj.gps_lat), "lng": float(obj.gps_lng)} if obj.gps_lat is not None else None

    def get_customer_gps(self, obj):
        return obj.customer.gps

    def _url(self, f):
        if not f:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(f.url) if request else f.url

    def get_signature_url(self, obj):
        return self._url(obj.signature_file)

    def get_photo_url(self, obj):
        return self._url(obj.photo_file)


class DeliveryTripSerializer(serializers.ModelSerializer):
    delivery_notes = DeliveryNoteSerializer(many=True, read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    driver_name = serializers.CharField(source="driver.name", read_only=True, default="")
    driver_phone = serializers.CharField(source="driver.phone", read_only=True, default="")
    vehicle_reg = serializers.CharField(source="vehicle.reg_no", read_only=True, default="")
    rep_name = serializers.CharField(source="rep.name", read_only=True, default="")
    delivery_rep_name = serializers.CharField(source="delivery_rep.full_name", read_only=True, default="")
    loader_name = serializers.CharField(source="loader.full_name", read_only=True, default="")
    helper_name = serializers.CharField(source="helper.full_name", read_only=True, default="")
    picklist_number = serializers.CharField(source="picklist.number", read_only=True, default="")
    route_name = serializers.CharField(source="route.name", read_only=True, default="")
    order_ids = serializers.ListField(child=serializers.UUIDField(), write_only=True, required=False)
    orders_value = serializers.SerializerMethodField()
    closed_by_name = serializers.CharField(source="closed_by.full_name", read_only=True, default="")
    customer_names = serializers.SerializerMethodField()

    def get_customer_names(self, obj):
        seen = []
        for dn in obj.delivery_notes.all():
            if dn.customer_id and dn.customer.name not in seen:
                seen.append(dn.customer.name)
        return seen

    class Meta:
        model = DeliveryTrip
        fields = ["id", "number", "version", "warehouse", "warehouse_name", "date", "driver", "driver_name", "driver_phone", "vehicle", "vehicle_reg", "rep", "rep_name", "delivery_rep", "delivery_rep_name", "loader", "loader_name", "helper", "helper_name", "picklist", "picklist_number", "route", "route_name", "customer_names", "status", "stops_count", "cash_collected", "started_at", "completed_at", "closed_at", "closed_by_name", "odometer_start", "odometer_end", "fuel_amount", "expense_amount", "loaded_value", "reconciliation", "notes", "delivery_notes", "order_ids", "orders_value", "created_at"]
        read_only_fields = ["id", "number", "version", "status", "stops_count", "cash_collected", "started_at", "completed_at", "closed_at", "odometer_start", "odometer_end", "fuel_amount", "expense_amount", "loaded_value", "reconciliation", "created_at"]

    def get_orders_value(self, obj):
        return str(sum((d.order.total for d in obj.delivery_notes.all()), 0))


class DeliveryTripListSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    driver_name = serializers.CharField(source="driver.name", read_only=True, default="")
    vehicle_reg = serializers.CharField(source="vehicle.reg_no", read_only=True, default="")
    rep_name = serializers.CharField(source="rep.name", read_only=True, default="")
    delivered_count = serializers.SerializerMethodField()

    class Meta:
        model = DeliveryTrip
        fields = ["id", "number", "warehouse", "warehouse_name", "date", "driver", "driver_name", "vehicle", "vehicle_reg", "rep", "rep_name", "status", "stops_count", "delivered_count", "cash_collected", "loaded_value", "started_at", "closed_at", "created_at"]

    def get_delivered_count(self, obj):
        return obj.delivery_notes.filter(status__in=["delivered", "partially_delivered"]).count()
