import uuid
from decimal import Decimal

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone

from .managers import AllObjectsManager, TenantManager
from .tenancy import get_current_tenant_id, get_current_user
from .uuid7 import uuid7

MONEY = dict(max_digits=18, decimal_places=4, default=Decimal("0"))
QTY = dict(max_digits=18, decimal_places=3, default=Decimal("0"))
ZERO = Decimal("0")


class TimeStampedModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        user = get_current_user()
        if user is not None and getattr(user, "is_authenticated", False):
            if self._state.adding and self.created_by_id is None:
                self.created_by = user
            self.updated_by = user
        super().save(*args, **kwargs)


class TenantModel(TimeStampedModel):
    """Every tenant-owned row. Default manager is tenant-scoped."""

    tenant = models.ForeignKey("platform.Tenant", on_delete=models.CASCADE, related_name="+", db_index=True, editable=False)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = TenantManager()
    all_objects = AllObjectsManager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.tenant_id is None:
            tid = get_current_tenant_id()
            if tid is None:
                raise ValueError(f"{self.__class__.__name__}: no tenant in context")
            self.tenant_id = tid
        super().save(*args, **kwargs)

    def soft_delete(self):
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at", "updated_at"])


class DocumentModel(TenantModel):
    """Numbered business document with optimistic locking."""

    number = models.CharField(max_length=40, db_index=True)
    version = models.PositiveIntegerField(default=1)
    notes = models.TextField(blank=True)

    class Meta:
        abstract = True

    def bump_version(self):
        self.version = (self.version or 1) + 1


class NumberSequence(TenantModel):
    RESET_CHOICES = [("never", "Never"), ("yearly", "Yearly")]
    document_type = models.CharField(max_length=40)
    prefix = models.CharField(max_length=20, default="")
    next_number = models.PositiveIntegerField(default=1)
    padding = models.PositiveSmallIntegerField(default=5)
    reset = models.CharField(max_length=10, choices=RESET_CHOICES, default="never")
    last_reset_year = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        unique_together = [("tenant", "document_type")]

    def __str__(self):
        return f"{self.document_type}: {self.prefix}{self.next_number}"


class AuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    tenant = models.ForeignKey("platform.Tenant", null=True, blank=True, on_delete=models.CASCADE, related_name="audit_logs")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=60, db_index=True)
    model = models.CharField(max_length=80, db_index=True)
    object_id = models.CharField(max_length=64, blank=True, db_index=True)
    object_repr = models.CharField(max_length=200, blank=True)
    before = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    after = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-at"]


class Attachment(TenantModel):
    model = models.CharField(max_length=80, db_index=True)
    object_id = models.CharField(max_length=64, db_index=True)
    file = models.FileField(upload_to="attachments/%Y/%m/")
    name = models.CharField(max_length=255)
    size = models.PositiveBigIntegerField(default=0)
    content_type = models.CharField(max_length=120, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=40, blank=True)  # receipt, pod_photo, signature, cheque, document

    class Meta:
        ordering = ["-created_at"]


class Notification(TenantModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    type = models.CharField(max_length=60, db_index=True)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class DeviceToken(models.Model):
    PLATFORMS = [("ios", "iOS"), ("android", "Android"), ("web", "Web")]
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="devices")
    platform = models.CharField(max_length=10, choices=PLATFORMS)
    token = models.CharField(max_length=512)
    device_name = models.CharField(max_length=120, blank=True)
    app_version = models.CharField(max_length=40, blank=True)
    last_seen = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "token")]


class NotificationPreference(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_preferences")
    type = models.CharField(max_length=60)
    push = models.BooleanField(default=True)
    email = models.BooleanField(default=False)
    sms = models.BooleanField(default=False)

    class Meta:
        unique_together = [("user", "type")]


class IdempotencyKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    tenant = models.ForeignKey("platform.Tenant", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    key = models.CharField(max_length=120)
    path = models.CharField(max_length=200)
    response_status = models.PositiveSmallIntegerField()
    response_body = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("tenant", "user", "key", "path")]


class Module(models.Model):
    """Anchor model for the module-level permission catalogue (see accounts.permissions_catalog)."""

    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=80)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return self.name


class City(models.Model):
    """
    Reference list of cities used by the customer/supplier forms — global, not per tenant, so every company
    searches the same spellings. Seeded with Pakistan from `manage.py seed_cities`; add others as needed.
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    name = models.CharField(max_length=80, db_index=True)
    province = models.CharField(max_length=60, blank=True)
    country = models.CharField(max_length=60, default="Pakistan")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        unique_together = [("name", "province", "country")]
        verbose_name_plural = "cities"

    def __str__(self):
        return f"{self.name}, {self.province}" if self.province else self.name
