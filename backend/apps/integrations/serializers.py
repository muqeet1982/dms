from rest_framework import serializers

from .models import SCOPES, ApiKey, ImportJob, WebhookDelivery, WebhookEndpoint


class ApiKeySerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True, default="")
    usage_month = serializers.SerializerMethodField()

    class Meta:
        model = ApiKey
        fields = ["id", "name", "key_prefix", "scopes", "supplier", "supplier_name", "rep", "rate_limit", "ip_allowlist", "expires_at", "last_used_at", "is_active", "usage_month", "created_at"]
        read_only_fields = ["id", "key_prefix", "last_used_at", "created_at"]

    def validate_scopes(self, v):
        valid = {s for s, _ in SCOPES}
        bad = [s for s in v if s not in valid]
        if bad:
            raise serializers.ValidationError(f"Unknown scopes: {bad}")
        return v

    def get_usage_month(self, obj):
        from django.utils import timezone

        return obj.usage.filter(at__date__gte=timezone.localdate().replace(day=1)).count()


class WebhookEndpointSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True, default="")
    recent_failures = serializers.SerializerMethodField()

    class Meta:
        model = WebhookEndpoint
        fields = ["id", "url", "secret", "events", "supplier", "supplier_name", "api_key", "is_active", "description", "recent_failures", "created_at"]
        read_only_fields = ["id", "secret", "created_at"]

    def get_recent_failures(self, obj):
        return obj.deliveries.filter(status="failed").count()


class WebhookDeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookDelivery
        fields = ["id", "endpoint", "event", "payload", "status", "attempts", "last_status_code", "last_error", "next_attempt_at", "delivered_at", "created_at"]


class ImportJobSerializer(serializers.ModelSerializer):
    started_by_name = serializers.CharField(source="started_by.full_name", read_only=True, default="")

    class Meta:
        model = ImportJob
        fields = ["id", "type", "file", "status", "rows_total", "rows_ok", "rows_failed", "errors", "options", "summary", "started_by_name", "created_at"]
        read_only_fields = ["id", "status", "rows_total", "rows_ok", "rows_failed", "errors", "summary", "created_at"]
