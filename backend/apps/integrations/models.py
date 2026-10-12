import hashlib
import secrets

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone

from apps.core.models import TenantModel
from apps.core.uuid7 import uuid7

SCOPES = [
    ("orders:read", "Read orders"), ("orders:write", "Create/cancel orders"), ("customers:read", "Read customers"), ("customers:write", "Create customers"),
    ("catalog:read", "Read catalogue & prices"), ("stock:read", "Read stock"), ("deliveries:read", "Read deliveries"), ("collections:write", "Record collections"), ("webhooks:manage", "Manage webhooks"),
]

SCOPE_TO_PERMS = {
    "orders:read": ["external.view_own_orders", "sales.view_sales_order"], "orders:write": ["external.submit_order", "external.cancel_own_order", "sales.add_sales_order"],
    "customers:read": ["external.view_assigned_customers", "customers.view_customer", "external.view_customer_outstanding"], "customers:write": ["external.add_customer_request", "customers.add_customer"],
    "catalog:read": ["catalog.view_product", "external.view_prices"], "stock:read": ["inventory.view_stock", "external.view_stock_exact"], "deliveries:read": ["external.view_delivery_status", "delivery.view_all_trips"],
    "collections:write": ["external.record_collection", "external.view_own_collections", "cash.receive_payment"], "webhooks:manage": ["settings.manage_integrations"],
}


class ApiKey(TenantModel):
    name = models.CharField(max_length=80)
    key_prefix = models.CharField(max_length=12, db_index=True)
    hashed_key = models.CharField(max_length=64, db_index=True)
    scopes = models.JSONField(default=list, blank=True)
    supplier = models.ForeignKey("partners.Supplier", null=True, blank=True, on_delete=models.CASCADE, related_name="api_keys")
    rep = models.ForeignKey("partners.SupplierRep", null=True, blank=True, on_delete=models.SET_NULL, related_name="api_keys", help_text="Optional default rep for orders created by this key")
    rate_limit = models.PositiveIntegerField(default=60, help_text="requests per minute")
    ip_allowlist = models.JSONField(default=list, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.key_prefix}…)"

    @staticmethod
    def hash(raw):
        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def generate(cls, **fields):
        raw = "dms_" + secrets.token_urlsafe(32)
        obj = cls.objects.create(key_prefix=raw[:12], hashed_key=cls.hash(raw), **fields)
        return obj, raw

    def has_scope(self, perm_codename):
        for scope in self.scopes:
            if perm_codename in SCOPE_TO_PERMS.get(scope, []):
                return True
        return False

    @property
    def is_valid(self):
        return self.is_active and (self.expires_at is None or self.expires_at > timezone.now())


class ApiUsage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    api_key = models.ForeignKey(ApiKey, on_delete=models.CASCADE, related_name="usage")
    path = models.CharField(max_length=200)
    method = models.CharField(max_length=8)
    status = models.PositiveSmallIntegerField()
    ip = models.GenericIPAddressField(null=True, blank=True)
    at = models.DateTimeField(default=timezone.now, db_index=True)


class WebhookEndpoint(TenantModel):
    EVENTS = ["order.created", "order.confirmed", "order.rejected", "order.dispatched", "order.delivered", "delivery.completed", "invoice.created", "payment.received"]
    url = models.URLField(max_length=300)
    secret = models.CharField(max_length=64)
    events = models.JSONField(default=list, blank=True)
    supplier = models.ForeignKey("partners.Supplier", null=True, blank=True, on_delete=models.CASCADE, related_name="webhooks", help_text="Only send events for this supplier's orders")
    api_key = models.ForeignKey(ApiKey, null=True, blank=True, on_delete=models.SET_NULL, related_name="webhooks")
    is_active = models.BooleanField(default=True)
    description = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["url"]

    def save(self, *args, **kwargs):
        if not self.secret:
            self.secret = secrets.token_hex(24)
        super().save(*args, **kwargs)


class WebhookDelivery(TenantModel):
    STATUS = [("pending", "Pending"), ("delivered", "Delivered"), ("failed", "Failed")]
    endpoint = models.ForeignKey(WebhookEndpoint, on_delete=models.CASCADE, related_name="deliveries")
    event = models.CharField(max_length=40, db_index=True)
    payload = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    status = models.CharField(max_length=10, choices=STATUS, default="pending", db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class ImportJob(TenantModel):
    TYPES = [("products", "Products"), ("customers", "Customers"), ("suppliers", "Suppliers"), ("opening_stock", "Opening stock"), ("opening_balances", "Opening balances"), ("price_list", "Price list"), ("purchase_order", "Purchase order")]
    STATUS = [("pending", "Pending"), ("running", "Running"), ("done", "Done"), ("failed", "Failed")]
    type = models.CharField(max_length=20, choices=TYPES)
    file = models.FileField(upload_to="imports/")
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    rows_total = models.PositiveIntegerField(default=0)
    rows_ok = models.PositiveIntegerField(default=0)
    rows_failed = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)
    options = models.JSONField(default=dict, blank=True)
    summary = models.JSONField(default=dict, blank=True)  # {"created": n, "updated": n, "categories_created": n, ...}
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
