from rest_framework import serializers

from apps.accounts.models import GroupProfile

from .geofence import GeoFenceService, area_km2
from .models import Customer, GeoFence, GeoFenceEvent, GeoFenceRep, Route, Supplier, SupplierRep, Territory


class TerritorySerializer(serializers.ModelSerializer):
    parent_name = serializers.CharField(source="parent.name", read_only=True, default="")
    customers_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Territory
        fields = ["id", "name", "parent", "parent_name", "customers_count"]


class RouteSerializer(serializers.ModelSerializer):
    territory_name = serializers.CharField(source="territory.name", read_only=True, default="")
    customers_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Route
        fields = ["id", "name", "territory", "territory_name", "day_of_week", "is_active", "customers_count"]


class SupplierRepSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    role_name = serializers.CharField(source="role.name", read_only=True, default="")
    has_login = serializers.SerializerMethodField()
    user_email = serializers.CharField(source="user.email", read_only=True, default="")
    create_login = serializers.BooleanField(write_only=True, required=False, default=False)
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    send_invite = serializers.BooleanField(write_only=True, required=False, default=True)

    class Meta:
        model = SupplierRep
        fields = ["id", "supplier", "supplier_name", "user", "user_email", "has_login", "code", "name", "phone", "email", "territory", "territories", "role", "role_name", "monthly_target", "is_active", "geofence_policy", "create_login", "password", "send_invite", "created_at"]
        read_only_fields = ["id", "user", "created_at"]
        extra_kwargs = {"supplier": {"required": False}, "code": {"required": False, "allow_blank": True}}

    def get_has_login(self, obj):
        return obj.user_id is not None

    def validate_role(self, role):
        if role is not None and role.role_type != "external":
            raise serializers.ValidationError("Rep roles must be external roles.")
        return role


class _UniqueEmailMixin:
    """
    One email per customer / per supplier inside a company. Two records on the same address make statements and
    reminders go to the wrong place, so a duplicate is refused by name (case and spacing are ignored).
    """

    def validate_email(self, value):
        email = (value or "").strip().lower()
        if not email:
            return ""
        model = self.Meta.model
        qs = model.objects.filter(email__iexact=email)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        clash = qs.first()
        if clash is not None:
            label = model._meta.verbose_name
            raise serializers.ValidationError(f"This email is already used by {label} {clash.code} — {clash.name}.")
        return email


class SupplierSerializer(_UniqueEmailMixin, serializers.ModelSerializer):
    reps_count = serializers.IntegerField(read_only=True, default=0)
    products_count = serializers.IntegerField(read_only=True, default=0)
    outstanding = serializers.SerializerMethodField()
    tax_class_override_name = serializers.CharField(source="tax_class_override.name", read_only=True, default="")

    class Meta:
        model = Supplier
        fields = ["id", "code", "name", "business_name", "ntn", "strn", "cnic", "filer_status", "contact_person", "phone", "alt_mobile", "landline", "email", "address", "city", "tax_no", "tax_numbers", "payment_terms_days", "tax_class_override", "tax_class_override_name", "withholding_tax", "account", "opening_balance", "opening_balance_date", "contacts", "is_active", "notes", "reps_count", "products_count", "outstanding", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {"code": {"required": False, "allow_blank": True}}

    def get_outstanding(self, obj):
        if self.context.get("view") and getattr(self.context["view"], "action", "") == "list":
            return None
        from .services import SupplierService

        return str(SupplierService.outstanding(obj))


class CustomerSerializer(_UniqueEmailMixin, serializers.ModelSerializer):
    territory_name = serializers.CharField(source="territory.name", read_only=True, default="")
    route_name = serializers.CharField(source="route.name", read_only=True, default="")
    price_list_name = serializers.CharField(source="price_list.name", read_only=True, default="")
    tax_class_override_name = serializers.CharField(source="tax_class_override.name", read_only=True, default="")
    outstanding = serializers.SerializerMethodField()
    rep_names = serializers.SerializerMethodField()

    class Meta:
        model = Customer
        fields = ["id", "code", "name", "business_name", "ntn", "strn", "cnic", "filer_status", "type", "contact_person", "phone", "alt_mobile", "landline", "email", "address", "city", "gps_lat", "gps_lng", "territory", "territory_name", "route", "route_name", "price_list", "price_list_name", "credit_limit", "credit_days", "tax_class_override", "tax_class_override_name", "tax_numbers", "default_discount_pct", "opening_balance_date", "account", "assigned_reps", "rep_names", "status", "is_blocked", "blocked_reason", "opening_balance", "contacts", "requested_by_rep", "min_shelf_life_days", "notes", "outstanding", "created_at", "updated_at"]
        read_only_fields = ["id", "requested_by_rep", "created_at", "updated_at"]
        extra_kwargs = {"code": {"required": False, "allow_blank": True}}

    def get_outstanding(self, obj):
        if hasattr(obj, "outstanding_amount"):
            return str(obj.outstanding_amount or 0)
        from .services import CustomerService

        return str(CustomerService.outstanding(obj))

    def get_rep_names(self, obj):
        return [r.name for r in obj.assigned_reps.all()]


class GeoFenceRepSerializer(serializers.ModelSerializer):
    rep_name = serializers.CharField(source="rep.name", read_only=True)
    rep_code = serializers.CharField(source="rep.code", read_only=True)
    supplier_name = serializers.CharField(source="rep.supplier.name", read_only=True)
    assigned_by_name = serializers.CharField(source="assigned_by.full_name", read_only=True, default="")

    class Meta:
        model = GeoFenceRep
        fields = ["id", "fence", "rep", "rep_name", "rep_code", "supplier_name", "paused_until", "assigned_by_name", "assigned_at"]
        read_only_fields = ["id", "assigned_by_name", "assigned_at"]


class GeoFenceSerializer(serializers.ModelSerializer):
    """A working area. `reps` is the list of rep ids covering it — assigning is a plain write to this field."""

    reps = serializers.PrimaryKeyRelatedField(many=True, queryset=SupplierRep.objects.all(), required=False)
    assignments = GeoFenceRepSerializer(many=True, read_only=True)
    area_km2 = serializers.SerializerMethodField()
    customers_count = serializers.SerializerMethodField()
    reps_count = serializers.IntegerField(source="reps.count", read_only=True)
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default="")

    class Meta:
        model = GeoFence
        fields = ["id", "name", "code", "type", "centre_lat", "centre_lng", "radius_m", "points", "source", "place_name",
                  "osm_ref", "colour", "min_lat", "max_lat", "min_lng", "max_lng", "reps", "assignments", "reps_count",
                  "area_km2", "customers_count", "is_active", "notes", "created_by_name", "created_at", "updated_at"]
        read_only_fields = ["id", "min_lat", "max_lat", "min_lng", "max_lng", "created_at", "updated_at"]

    def get_area_km2(self, obj):
        return area_km2(obj)

    def get_customers_count(self, obj):
        # the list page asks for this on every row, so it is only counted when the view asks for it
        if self.context.get("with_counts") is False:
            return None
        return len(GeoFenceService.customers_in(obj))

    def validate(self, attrs):
        kind = attrs.get("type", getattr(self.instance, "type", "circle"))
        if kind == "circle":
            lat = attrs.get("centre_lat", getattr(self.instance, "centre_lat", None))
            radius = attrs.get("radius_m", getattr(self.instance, "radius_m", 0))
            if lat is None:
                raise serializers.ValidationError({"centre_lat": ["Drop a pin on the map first."]})
            if not radius:
                raise serializers.ValidationError({"radius_m": ["Set how far the area reaches."]})
        else:
            pts = attrs.get("points", getattr(self.instance, "points", []))
            if len([p for p in pts or [] if p]) < 3:
                raise serializers.ValidationError({"points": ["An area needs at least three corners."]})
        return attrs


class GeoFenceEventSerializer(serializers.ModelSerializer):
    rep_name = serializers.CharField(source="rep.name", read_only=True, default="")
    fence_name = serializers.CharField(source="fence.name", read_only=True, default="")
    customer_name = serializers.CharField(source="customer.name", read_only=True, default="")

    class Meta:
        model = GeoFenceEvent
        fields = ["id", "rep", "rep_name", "action", "verdict", "blocked", "gps_lat", "gps_lng", "fence", "fence_name",
                  "distance_m", "customer", "customer_name", "document_type", "document_number", "occurred_at"]
