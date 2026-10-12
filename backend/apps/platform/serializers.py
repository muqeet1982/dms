from rest_framework import serializers

from apps.accounts.serializers import MeSerializer

from .models import AddOn, Announcement, Coupon, FeatureFlag, LegalDocument, MessageTemplate, NotificationLog, Plan, PlatformInvoice, PlatformPayment, PlatformRefund, Subscription, SubscriptionAddOn, SubscriptionEvent, SupportTicket, Tenant, TicketMessage


class PlanSerializer(serializers.ModelSerializer):
    subscriptions_count = serializers.SerializerMethodField()

    class Meta:
        model = Plan
        fields = "__all__"
        read_only_fields = ["id", "version", "parent_plan", "is_current", "created_at", "updated_at"]

    def get_subscriptions_count(self, obj):
        return obj.subscriptions.exclude(status="cancelled").count()


class AddOnSerializer(serializers.ModelSerializer):
    plan_names = serializers.SerializerMethodField()

    def get_plan_names(self, obj):
        return [p.name for p in obj.plans.all()] or ["All plans"]

    class Meta:
        model = AddOn
        fields = "__all__"


class CouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = "__all__"


class TenantPublicSerializer(serializers.ModelSerializer):
    """What a tenant user sees about their own company."""

    plan = serializers.SerializerMethodField()

    class Meta:
        model = Tenant
        fields = ["id", "slug", "legal_name", "display_name", "status", "address", "city", "phone", "email", "website", "tax_numbers", "logo", "base_currency", "fiscal_year_start_month", "timezone", "settings", "branding", "plan"]

    def get_plan(self, obj):
        sub = obj.active_subscription
        if not sub:
            return None
        return {"code": sub.plan_snapshot.get("code"), "name": sub.plan_snapshot.get("name"), "status": sub.status, "end_date": sub.end_date, "days_left": sub.days_left}


class SubscriptionEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.full_name", read_only=True, default="")

    class Meta:
        model = SubscriptionEvent
        fields = "__all__"


class SubscriptionAddOnSerializer(serializers.ModelSerializer):
    addon_name = serializers.CharField(source="addon.name", read_only=True)
    addon_code = serializers.CharField(source="addon.code", read_only=True)

    class Meta:
        model = SubscriptionAddOn
        fields = ["id", "addon", "addon_name", "addon_code", "quantity"]


class SubscriptionSerializer(serializers.ModelSerializer):
    plan_name = serializers.CharField(source="plan.name", read_only=True)
    plan_code = serializers.CharField(source="plan.code", read_only=True)
    tenant_name = serializers.CharField(source="tenant.display_name", read_only=True)
    days_left = serializers.IntegerField(read_only=True)
    addons = SubscriptionAddOnSerializer(many=True, read_only=True)
    discount_label = serializers.CharField(read_only=True)
    discount_active = serializers.BooleanField(read_only=True)
    discount_set_by_name = serializers.CharField(source="discount_set_by.full_name", read_only=True, default="")
    price_breakdown = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = "__all__"
        read_only_fields = ["id", "plan_version", "plan_snapshot", "discount_used", "discount_set_by", "discount_set_at", "created_at", "updated_at"]

    def get_price_breakdown(self, obj):
        from .services import SubscriptionService

        return SubscriptionService.price_breakdown(obj)


class TenantSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.full_name", read_only=True, default="")
    effective_features = serializers.SerializerMethodField()
    white_label = serializers.SerializerMethodField()
    outstanding = serializers.SerializerMethodField()
    usage = serializers.SerializerMethodField()
    owner_email = serializers.CharField(source="owner.email", read_only=True, default="")
    owner_phone = serializers.CharField(source="owner.phone", read_only=True, default="")
    subscription = serializers.SerializerMethodField()

    class Meta:
        model = Tenant
        fields = "__all__"
        read_only_fields = ["id", "slug", "owner", "created_by", "suspended_at", "deleted_at", "created_at", "updated_at", "status"]

    def get_subscription(self, obj):
        sub = obj.active_subscription
        return SubscriptionSerializer(sub).data if sub else None


    def get_usage(self, obj):
        """Just the two counts the list shows — the full picture is on /tenants/{id}/usage."""
        from apps.accounts.models import TenantMembership
        from apps.catalog.models import ProductVariant

        return {
            "users": TenantMembership.objects.filter(tenant=obj, status="active").count(),
            "products": ProductVariant.all_objects.filter(tenant=obj, deleted_at__isnull=True).count(),
        }

    def get_outstanding(self, obj):
        from django.db.models import Sum

        rows = obj.platform_invoices.filter(status__in=["issued", "overdue", "partial"], is_credit_note=False).aggregate(t=Sum("total"), p=Sum("paid_amount"))
        return str((rows["t"] or 0) - (rows["p"] or 0))

    def get_effective_features(self, obj):
        """Plan features + per-tenant overrides + global flags — what the tenant actually gets."""
        from apps.core.plan_limits import effective_features

        plan = (obj.active_subscription.plan_snapshot.get("features", {}) if obj.active_subscription else {})
        eff = effective_features(obj)
        return {k: {"value": v, "from_plan": plan.get(k), "overridden": k in (obj.feature_overrides or {})} for k, v in eff.items()}

    def get_white_label(self, obj):
        from apps.core.plan_limits import effective_features

        return bool(effective_features(obj).get("branding"))


class TenantCreateSerializer(serializers.Serializer):
    legal_name = serializers.CharField(max_length=200)
    display_name = serializers.CharField(max_length=120)
    slug = serializers.SlugField(required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    city = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    tax_numbers = serializers.DictField(required=False)
    base_currency = serializers.CharField(default="PKR")
    fiscal_year_start_month = serializers.IntegerField(default=7, min_value=1, max_value=12)
    timezone = serializers.CharField(default="Asia/Karachi")
    plan = serializers.PrimaryKeyRelatedField(queryset=Plan.objects.filter(is_current=True), required=False, allow_null=True)
    owner_name = serializers.CharField()
    owner_email = serializers.EmailField(required=False, allow_null=True, allow_blank=True)
    owner_phone = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    owner_password = serializers.CharField(required=False, allow_blank=True)
    send_invite = serializers.BooleanField(default=True)

    def validate(self, attrs):
        if not attrs.get("owner_email") and not attrs.get("owner_phone"):
            raise serializers.ValidationError({"owner_email": ["Owner email or phone is required."]})
        return attrs


class PlatformInvoiceSerializer(serializers.ModelSerializer):
    tenant_name = serializers.CharField(source="tenant.display_name", read_only=True)
    balance = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)

    class Meta:
        model = PlatformInvoice
        fields = "__all__"


class PlatformRefundSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlatformRefund
        fields = "__all__"
        read_only_fields = ["id", "payment", "recorded_by", "at"]


class PlatformPaymentSerializer(serializers.ModelSerializer):
    tenant_name = serializers.CharField(source="tenant.display_name", read_only=True)
    invoice_number = serializers.CharField(source="invoice.number", read_only=True, default="")
    recorded_by_name = serializers.CharField(source="recorded_by.full_name", read_only=True, default="")
    refunds = PlatformRefundSerializer(many=True, read_only=True)

    class Meta:
        model = PlatformPayment
        fields = "__all__"
        read_only_fields = ["id", "recorded_by", "created_at", "currency"]


class AnnouncementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Announcement
        fields = "__all__"


class MessageTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageTemplate
        fields = "__all__"


class NotificationLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationLog
        fields = "__all__"


class TicketMessageSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source="author.full_name", read_only=True, default="")

    class Meta:
        model = TicketMessage
        fields = "__all__"
        read_only_fields = ["id", "ticket", "author", "is_staff_reply", "created_at"]


class SupportTicketSerializer(serializers.ModelSerializer):
    tenant_name = serializers.CharField(source="tenant.display_name", read_only=True)
    opened_by_name = serializers.CharField(source="opened_by.full_name", read_only=True, default="")
    messages = TicketMessageSerializer(many=True, read_only=True)

    class Meta:
        model = SupportTicket
        fields = "__all__"
        read_only_fields = ["id", "tenant", "opened_by", "created_at", "updated_at"]


class FeatureFlagSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeatureFlag
        fields = "__all__"


class LegalDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = LegalDocument
        fields = "__all__"


class PlatformUserSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
    role_ids = serializers.ListField(child=serializers.UUIDField(), write_only=True, required=False)
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = MeSerializer.Meta.model
        fields = ["id", "email", "phone", "full_name", "is_active", "is_superuser", "mfa_enabled", "last_login", "roles", "role_ids", "password"]
        read_only_fields = ["id", "last_login"]

    def get_roles(self, obj):
        return [{"id": str(g.profile.id), "name": g.profile.name} for g in obj.groups.select_related("profile") if hasattr(g, "profile")]
