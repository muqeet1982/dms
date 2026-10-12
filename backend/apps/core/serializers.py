from rest_framework import serializers

from .models import Attachment, AuditLog, City, DeviceToken, Notification, NotificationPreference, NumberSequence


class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.full_name", read_only=True, default="")
    tenant_name = serializers.CharField(source="tenant.display_name", read_only=True, default="")

    class Meta:
        model = AuditLog
        fields = ["id", "tenant", "tenant_name", "actor", "actor_name", "action", "model", "object_id", "object_repr", "before", "after", "ip", "user_agent", "at"]


class AttachmentSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()
    uploaded_by_name = serializers.CharField(source="uploaded_by.full_name", read_only=True, default="")

    class Meta:
        model = Attachment
        fields = ["id", "model", "object_id", "file", "url", "name", "size", "content_type", "kind", "uploaded_by", "uploaded_by_name", "created_at"]
        read_only_fields = ["id", "name", "size", "content_type", "uploaded_by", "created_at"]
        extra_kwargs = {"file": {"write_only": True}}

    def get_url(self, obj):
        try:
            url = obj.file.url
        except Exception:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request and url.startswith("/") else url


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "type", "title", "body", "data", "read_at", "created_at"]


class DeviceTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceToken
        fields = ["id", "platform", "token", "device_name", "app_version", "last_seen", "created_at"]
        read_only_fields = ["id", "last_seen", "created_at"]


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = ["type", "push", "email", "sms"]


class NumberSequenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NumberSequence
        fields = ["id", "document_type", "prefix", "next_number", "padding", "reset"]
        read_only_fields = ["id"]


class CitySerializer(serializers.ModelSerializer):
    label = serializers.SerializerMethodField()

    class Meta:
        model = City
        fields = ["id", "name", "province", "country", "label"]

    def get_label(self, obj):
        return f"{obj.name} — {obj.province}" if obj.province else obj.name
