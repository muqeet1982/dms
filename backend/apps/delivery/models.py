from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import MONEY, QTY, DocumentModel, TenantModel


class Vehicle(TenantModel):
    OWNER = [("company", "Company"), ("rented", "Rented")]
    reg_no = models.CharField(max_length=30)
    make_model = models.CharField(max_length=80, blank=True)
    type = models.CharField(max_length=40, blank=True)  # van, pickup, bike, truck
    capacity_cartons = models.PositiveIntegerField(null=True, blank=True)
    owner_type = models.CharField(max_length=10, choices=OWNER, default="company")
    insurance_expiry = models.DateField(null=True, blank=True)
    fitness_expiry = models.DateField(null=True, blank=True)
    tracker_id = models.CharField(max_length=60, blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [("tenant", "reg_no")]
        ordering = ["reg_no"]

    def __str__(self):
        return self.reg_no


class Driver(TenantModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="driver_profiles")
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=30, blank=True)
    cnic = models.CharField(max_length=20, blank=True)
    license_no = models.CharField(max_length=40, blank=True)
    license_expiry = models.DateField(null=True, blank=True)
    default_vehicle = models.ForeignKey(Vehicle, null=True, blank=True, on_delete=models.SET_NULL, related_name="default_drivers")
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PickList(DocumentModel):
    """
    The **load form**: what a confirmed order needs, handed to the warehouse to issue.
    open → sent (to the warehouse) → picking → issued / partially_issued.
    """

    STATUS = [
        ("open", "Pending"), ("sent", "Sent to warehouse"), ("picking", "Picking"),
        ("issued", "Issued"), ("partially_issued", "Partially issued"), ("picked", "Picked"), ("cancelled", "Cancelled"),
    ]
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="pick_lists")
    orders = models.ManyToManyField("sales.SalesOrder", related_name="pick_lists", blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default="open", db_index=True)
    picker = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    picked_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    issued_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("tenant", "number")]

    def __str__(self):
        return self.number


class PickListLine(TenantModel):
    picklist = models.ForeignKey(PickList, on_delete=models.CASCADE, related_name="lines")
    order_line = models.ForeignKey("sales.SalesOrderLine", null=True, blank=True, on_delete=models.SET_NULL, related_name="pick_lines")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey("inventory.Batch", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    bin = models.ForeignKey("inventory.Bin", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    qty_to_pick = models.DecimalField(**QTY)
    qty_picked = models.DecimalField(**QTY)
    scanned_at = models.DateTimeField(null=True, blank=True)


class DeliveryTrip(DocumentModel):
    STATUS = [("planned", "Planned"), ("loading", "Loading"), ("out_for_delivery", "Out for delivery"), ("completed", "Completed"), ("closed", "Closed"), ("cancelled", "Cancelled")]
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="trips")
    date = models.DateField(default=timezone.localdate, db_index=True)
    driver = models.ForeignKey(Driver, null=True, blank=True, on_delete=models.PROTECT, related_name="trips")
    vehicle = models.ForeignKey(Vehicle, null=True, blank=True, on_delete=models.PROTECT, related_name="trips")
    rep = models.ForeignKey("partners.SupplierRep", null=True, blank=True, on_delete=models.SET_NULL, related_name="trips", help_text="Sales rep travelling with the delivery")
    delivery_rep = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="delivery_trips", help_text="Delivery rep / supervisor responsible for this dispatch")
    loader = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="loaded_trips", help_text="Warehouse staff who loaded the vehicle")
    helper = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="helper_trips", help_text="Helper / delivery person riding with the driver")
    picklist = models.ForeignKey("delivery.PickList", null=True, blank=True, on_delete=models.SET_NULL, related_name="trips", help_text="Loader form this dispatch was packed from")
    route = models.ForeignKey("partners.Route", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=20, choices=STATUS, default="planned", db_index=True)
    stops_count = models.PositiveIntegerField(default=0)
    cash_collected = models.DecimalField(**MONEY)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    odometer_start = models.PositiveIntegerField(null=True, blank=True)
    odometer_end = models.PositiveIntegerField(null=True, blank=True)
    fuel_amount = models.DecimalField(**MONEY)
    expense_amount = models.DecimalField(**MONEY)
    loaded_value = models.DecimalField(**MONEY)
    reconciliation = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        unique_together = [("tenant", "number")]

    def __str__(self):
        return self.number


class DeliveryNote(DocumentModel):
    STATUS = [
        ("pending", "Pending"), ("loaded", "Issued"), ("dispatched", "Out for delivery"), ("delivered", "Delivered"),
        ("partially_delivered", "Partially delivered"), ("failed", "Failed"), ("returned", "Returned"), ("cancelled", "Cancelled"),
    ]
    trip = models.ForeignKey(DeliveryTrip, null=True, blank=True, on_delete=models.SET_NULL, related_name="delivery_notes")
    picklist = models.ForeignKey("delivery.PickList", null=True, blank=True, on_delete=models.SET_NULL, related_name="delivery_notes", help_text="Load form this stock was issued on")
    order = models.ForeignKey("sales.SalesOrder", on_delete=models.PROTECT, related_name="delivery_notes")
    customer = models.ForeignKey("partners.Customer", on_delete=models.PROTECT, related_name="delivery_notes")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+")
    sequence = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS, default="pending", db_index=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    receiver_name = models.CharField(max_length=120, blank=True)
    signature_file = models.FileField(upload_to="pod/signatures/", null=True, blank=True)
    photo_file = models.FileField(upload_to="pod/photos/", null=True, blank=True)
    gps_lat = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    gps_lng = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    gps_accuracy_m = models.FloatField(null=True, blank=True)
    failure_reason = models.CharField(max_length=200, blank=True)
    remarks = models.CharField(max_length=300, blank=True, help_text="What the customer or the driver said on delivery")
    cash_collected = models.DecimalField(**MONEY)
    collected_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    stock_out_done = models.BooleanField(default=False)

    class Meta:
        ordering = ["trip", "sequence", "-created_at"]
        unique_together = [("tenant", "number")]

    def __str__(self):
        return self.number


class DeliveryNoteLine(TenantModel):
    RETURN_REASONS = [("rejected", "Rejected by customer"), ("damaged", "Damaged"), ("shortage", "Shortage"), ("not_ordered", "Not ordered"), ("other", "Other")]
    delivery_note = models.ForeignKey(DeliveryNote, on_delete=models.CASCADE, related_name="lines")
    order_line = models.ForeignKey("sales.SalesOrderLine", on_delete=models.PROTECT, related_name="delivery_lines")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey("inventory.Batch", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    qty_loaded = models.DecimalField(**QTY)
    qty_delivered = models.DecimalField(**QTY)
    qty_returned = models.DecimalField(**QTY)
    return_reason = models.CharField(max_length=20, choices=RETURN_REASONS, blank=True)
    return_condition = models.CharField(max_length=10, default="good")  # good | damaged
    unit_cost = models.DecimalField(**MONEY)


class VehicleLog(TenantModel):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="logs")
    trip = models.ForeignKey(DeliveryTrip, null=True, blank=True, on_delete=models.SET_NULL, related_name="vehicle_logs")
    date = models.DateField(default=timezone.localdate)
    odometer_start = models.PositiveIntegerField(null=True, blank=True)
    odometer_end = models.PositiveIntegerField(null=True, blank=True)
    fuel_amount = models.DecimalField(**MONEY)
    expense = models.DecimalField(**MONEY)
    note = models.CharField(max_length=200, blank=True)
