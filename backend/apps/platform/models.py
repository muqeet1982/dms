from decimal import Decimal

from django.conf import settings as django_settings
from django.db import models
from django.utils import timezone

from apps.core.uuid7 import uuid7

MONEY = dict(max_digits=18, decimal_places=4, default=Decimal("0"))


class Plan(models.Model):
    CYCLES = [("monthly", "Monthly"), ("quarterly", "Quarterly"), ("yearly", "Yearly"), ("custom", "Custom days")]
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    code = models.SlugField(max_length=40)
    name = models.CharField(max_length=80)
    description = models.TextField(blank=True)
    tagline = models.CharField(max_length=120, blank=True, help_text="One line shown on the plan card, e.g. 'For a single warehouse'")
    price = models.DecimalField(**MONEY, help_text="Price per billing cycle (the monthly price for monthly plans)")
    price_annual = models.DecimalField(**MONEY, help_text="Price when billed yearly — 0 means yearly billing is not offered")
    setup_fee = models.DecimalField(**MONEY, help_text="One-off charge on the first invoice")
    is_featured = models.BooleanField(default=False, help_text="Marks the 'most popular' plan on pricing pages")
    currency = models.CharField(max_length=3, default="PKR")
    billing_cycle = models.CharField(max_length=10, choices=CYCLES, default="monthly")
    cycle_days = models.PositiveIntegerField(default=30)
    trial_days = models.PositiveIntegerField(default=14)
    grace_days = models.PositiveIntegerField(default=7)
    limits = models.JSONField(default=dict, blank=True)
    features = models.JSONField(default=dict, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_public = models.BooleanField(default=True)
    is_archived = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    parent_plan = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="versions")
    is_current = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "code", "-version"]
        unique_together = [("code", "version")]

    def __str__(self):
        return f"{self.name} v{self.version}"

    @property
    def period_days(self):
        return {"monthly": 30, "quarterly": 91, "yearly": 365}.get(self.billing_cycle, self.cycle_days or 30)

    def snapshot(self):
        return {"plan_id": str(self.id), "code": self.code, "name": self.name, "version": self.version, "price": str(self.price), "price_annual": str(self.price_annual), "setup_fee": str(self.setup_fee), "currency": self.currency, "billing_cycle": self.billing_cycle, "period_days": self.period_days, "limits": self.limits, "features": self.features, "grace_days": self.grace_days}


class AddOn(models.Model):
    KINDS = [("recurring", "Recurring — charged every cycle"), ("one_time", "One-off — charged once")]
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=200, blank=True)
    kind = models.CharField(max_length=10, choices=KINDS, default="recurring")
    unit_price = models.DecimalField(**MONEY)
    unit_label = models.CharField(max_length=40, default="unit")
    limit_key = models.CharField(max_length=40, blank=True, help_text="Plan limit key this add-on raises, e.g. max_users")
    units_per_qty = models.PositiveIntegerField(default=1)
    max_quantity = models.PositiveIntegerField(null=True, blank=True, help_text="Cap on how many a tenant may buy")
    plans = models.ManyToManyField("Plan", blank=True, related_name="addons", help_text="Leave empty to offer it on every plan")
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Coupon(models.Model):
    TYPES = [("percent", "Percent"), ("fixed", "Fixed")]
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    code = models.CharField(max_length=40, unique=True)
    type = models.CharField(max_length=10, choices=TYPES)
    value = models.DecimalField(**MONEY)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    used_count = models.PositiveIntegerField(default=0)
    plans = models.ManyToManyField(Plan, blank=True, related_name="coupons")
    is_active = models.BooleanField(default=True)

    def is_valid(self, plan=None):
        today = timezone.localdate()
        if not self.is_active:
            return False
        if self.valid_from and today < self.valid_from:
            return False
        if self.valid_to and today > self.valid_to:
            return False
        if self.max_uses and self.used_count >= self.max_uses:
            return False
        if plan is not None and self.plans.exists() and not self.plans.filter(code=plan.code).exists():
            return False
        return True

    def discount_for(self, amount):
        if self.type == "percent":
            return (Decimal(amount) * self.value / Decimal("100")).quantize(Decimal("0.0001"))
        return min(Decimal(amount), self.value)


class Tenant(models.Model):
    STATUS = [("trial", "Trial"), ("active", "Active"), ("past_due", "Past due"), ("suspended", "Suspended"), ("cancelled", "Cancelled"), ("archived", "Archived")]
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    slug = models.SlugField(max_length=60, unique=True)
    legal_name = models.CharField(max_length=200)
    display_name = models.CharField(max_length=120)
    # Short company code stamped onto every document number (TDN-SO-00001), so each company's count is
    # unmistakably its own. Derived from the name at creation; editable in Settings → Document numbering.
    doc_code = models.CharField(max_length=8, blank=True, default="")
    status = models.CharField(max_length=12, choices=STATUS, default="trial", db_index=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=80, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    website = models.CharField(max_length=120, blank=True)
    tax_numbers = models.JSONField(default=dict, blank=True)  # {"NTN": "...", "STRN": "..."}
    logo = models.ImageField(upload_to="tenants/logos/", null=True, blank=True)
    base_currency = models.CharField(max_length=3, default="PKR")
    fiscal_year_start_month = models.PositiveSmallIntegerField(default=7)
    timezone = models.CharField(max_length=50, default="Asia/Karachi")
    settings = models.JSONField(default=dict, blank=True)
    limit_overrides = models.JSONField(default=dict, blank=True)
    feature_overrides = models.JSONField(default=dict, blank=True)
    branding = models.JSONField(default=dict, blank=True)
    owner = models.ForeignKey(django_settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="owned_tenants")
    created_by = models.ForeignKey(django_settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspended_reason = models.CharField(max_length=200, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    impersonation_allowed = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name"]

    def __str__(self):
        return self.display_name

    @property
    def active_subscription(self):
        if hasattr(self, "_active_sub"):
            return self._active_sub
        self._active_sub = self.subscriptions.exclude(status="cancelled").order_by("-start_date", "-created_at").first()
        return self._active_sub

    DEFAULT_SETTINGS = {
        "allow_negative_stock": False,
        "reserve_stock_on_confirm": True,
        "two_step_dispatch": False,
        "credit_policy": "warn",  # hard | warn
        "backorder_auto_cancel_days": 30,
        "accept_external_prices": "flag",  # accept | price_list | flag
        "customer_requests_need_approval": True,
        "min_shelf_life_days": 30,
        "block_stock_moves_during_count": True,
        "count_tolerance_pct": "2",
        "transfer_requires_approval": True,
        "rep_unsettled_alert_days": 3,
        # working areas: off | warn (record it) | block (refuse it outside the area)
        "geofence_policy": "block",
        # A rep standing outside their area is refused; a rep whose app sends no location is not. Turn this on
        # once every app in the field sends `gps` on orders and collections — until then it would lock out the
        # ones that do not, and a missing fix would be the easy way around the rule.
        "geofence_require_location": True,
        # a photo of the shop taken at the visit: off | optional | required
        "rep_order_photo": "optional",
        # checking in at a shop with no order — the photo is the evidence, so it is required by default and
        # the app opens the camera rather than the gallery (apps/partners/visits.py)
        "rep_visit_photo": "required",
        # where the field force has been. On by default at the owner's instruction — it is still employee location
        # data, so the app shows `tracking_notice` before asking for background permission and a rep can always
        # read their own history (apps/partners/tracking.py).
        "tracking_enabled": True,
        "tracking_mode": "work_hours",
        "tracking_interval_s": 120,
        "tracking_min_distance_m": 50,
        "tracking_from": "09:00",
        "tracking_to": "19:00",
        "tracking_days": [1, 2, 3, 4, 5, 6],
        "tracking_retention_days": 90,
        "tracking_notice": "Your location is recorded during working hours so the office can plan routes and confirm visits.",
        "invoice_footer": "",
    }

    def setting(self, key):
        return {**self.DEFAULT_SETTINGS, **(self.settings or {})}.get(key)


class Subscription(models.Model):
    STATUS = [("trial", "Trial"), ("active", "Active"), ("past_due", "Past due"), ("suspended", "Suspended"), ("cancelled", "Cancelled"), ("expired", "Expired")]
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="subscriptions")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    plan_version = models.PositiveIntegerField(default=1)
    plan_snapshot = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default="trial")
    start_date = models.DateField()
    end_date = models.DateField()
    trial_end = models.DateField(null=True, blank=True)
    grace_days = models.PositiveIntegerField(default=7)
    auto_status_change = models.BooleanField(default=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    coupon = models.ForeignKey(Coupon, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    billing_cycle = models.CharField(max_length=10, choices=Plan.CYCLES, default="monthly", help_text="How this tenant is billed — monthly or yearly")
    # Negotiated discount for this tenant only (a coupon is the public/shared version of the same thing)
    discount_type = models.CharField(max_length=10, choices=[("", "None"), ("percent", "Percent"), ("fixed", "Fixed amount")], blank=True)
    discount_value = models.DecimalField(**MONEY)
    discount_cycles = models.PositiveIntegerField(default=0, help_text="How many invoices it applies to; 0 = every invoice while the subscription lives")
    discount_used = models.PositiveIntegerField(default=0)
    discount_until = models.DateField(null=True, blank=True, help_text="Optional end date for the discount")
    discount_reason = models.CharField(max_length=200, blank=True)
    discount_set_by = models.ForeignKey(django_settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    discount_set_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return f"{self.tenant} – {self.plan}"

    @property
    def discount_active(self):
        """A discount runs until its end date passes or it has been used on `discount_cycles` invoices."""
        if not self.discount_type or self.discount_value <= 0:
            return False
        if self.discount_until and self.discount_until < timezone.localdate():
            return False
        return not (self.discount_cycles and self.discount_used >= self.discount_cycles)

    def discount_for(self, amount):
        from decimal import Decimal

        if not self.discount_active or amount <= 0:
            return Decimal("0")
        value = Decimal(self.discount_value)
        cut = (amount * value / 100) if self.discount_type == "percent" else value
        return min(cut.quantize(Decimal("0.0001")), amount)

    @property
    def discount_label(self):
        if not self.discount_type or self.discount_value <= 0:
            return ""
        base = f"{self.discount_value:.0f}%" if self.discount_type == "percent" else f"{self.plan_snapshot.get('currency', 'PKR')} {self.discount_value:,.0f}"
        span = "every invoice" if not self.discount_cycles else f"{self.discount_cycles} invoice(s)"
        return f"{base} off · {span}"

    @property
    def days_left(self):
        return (self.end_date - timezone.localdate()).days


class SubscriptionAddOn(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="addons")
    addon = models.ForeignKey(AddOn, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)


class SubscriptionEvent(models.Model):
    TYPES = [("created", "Created"), ("discount", "Discount changed"), ("renewed", "Renewed"), ("upgraded", "Upgraded"), ("downgraded", "Downgraded"), ("suspended", "Suspended"), ("reactivated", "Reactivated"), ("cancelled", "Cancelled"), ("trial_extended", "Trial extended"), ("trial_converted", "Trial converted"), ("limit_override", "Limit override"), ("status_changed", "Status changed"), ("addon_changed", "Add-on changed")]
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=20, choices=TYPES)
    from_data = models.JSONField(null=True, blank=True)
    to_data = models.JSONField(null=True, blank=True)
    actor = models.ForeignKey(django_settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reason = models.CharField(max_length=300, blank=True)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-at"]


class PlatformInvoice(models.Model):
    STATUS = [("draft", "Draft"), ("issued", "Issued"), ("paid", "Paid"), ("partial", "Partially paid"), ("void", "Void"), ("overdue", "Overdue")]
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="platform_invoices")
    subscription = models.ForeignKey(Subscription, null=True, blank=True, on_delete=models.SET_NULL, related_name="invoices")
    number = models.CharField(max_length=30, unique=True)
    period_start = models.DateField()
    period_end = models.DateField()
    description = models.CharField(max_length=200, blank=True)
    subtotal = models.DecimalField(**MONEY)
    discount = models.DecimalField(**MONEY)
    tax = models.DecimalField(**MONEY)
    total = models.DecimalField(**MONEY)
    paid_amount = models.DecimalField(**MONEY)
    currency = models.CharField(max_length=3, default="PKR")
    status = models.CharField(max_length=10, choices=STATUS, default="issued")
    due_date = models.DateField(null=True, blank=True)
    lines = models.JSONField(default=list, blank=True)
    pdf_file = models.FileField(upload_to="platform/invoices/", null=True, blank=True)
    is_credit_note = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def balance(self):
        return self.total - self.paid_amount


class PlatformPayment(models.Model):
    METHODS = [("bank_transfer", "Bank transfer"), ("cash", "Cash"), ("cheque", "Cheque"), ("jazzcash", "JazzCash"), ("easypaisa", "Easypaisa"), ("card", "Card"), ("other", "Other")]
    STATUS = [("paid", "Paid"), ("partial", "Partial"), ("pending", "Pending"), ("refunded", "Refunded"), ("failed", "Failed")]
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="platform_payments")
    invoice = models.ForeignKey(PlatformInvoice, null=True, blank=True, on_delete=models.SET_NULL, related_name="payments")
    amount = models.DecimalField(**MONEY)
    currency = models.CharField(max_length=3, default="PKR")
    method = models.CharField(max_length=20, choices=METHODS, default="bank_transfer")
    reference = models.CharField(max_length=100, blank=True)
    paid_at = models.DateField(default=timezone.now)
    recorded_by = models.ForeignKey(django_settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    receipt_file = models.FileField(upload_to="platform/receipts/", null=True, blank=True)
    note = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="paid")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-paid_at", "-created_at"]


class PlatformRefund(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    payment = models.ForeignKey(PlatformPayment, on_delete=models.CASCADE, related_name="refunds")
    amount = models.DecimalField(**MONEY)
    reason = models.CharField(max_length=300)
    recorded_by = models.ForeignKey(django_settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    at = models.DateTimeField(default=timezone.now)


class Announcement(models.Model):
    AUDIENCE = [("all", "All tenants"), ("selected", "Selected tenants"), ("plan", "Tenants on plan")]
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    title = models.CharField(max_length=150)
    body = models.TextField()
    level = models.CharField(max_length=10, default="info")  # info | warning | danger
    audience = models.CharField(max_length=10, choices=AUDIENCE, default="all")
    tenants = models.ManyToManyField(Tenant, blank=True, related_name="announcements")
    plan_code = models.CharField(max_length=40, blank=True)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    dismissible = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-starts_at"]


class MessageTemplate(models.Model):
    CHANNELS = [("email", "Email"), ("sms", "SMS"), ("push", "Push"), ("whatsapp", "WhatsApp")]
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    key = models.CharField(max_length=60)
    channel = models.CharField(max_length=10, choices=CHANNELS)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    variables = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("key", "channel")]

    def render(self, ctx):
        subject, body = self.subject, self.body
        for k, v in ctx.items():
            subject = subject.replace("{{" + k + "}}", str(v))
            body = body.replace("{{" + k + "}}", str(v))
        return subject, body


class NotificationLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    user = models.ForeignKey(django_settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    channel = models.CharField(max_length=10)
    template_key = models.CharField(max_length=60, blank=True)
    to = models.CharField(max_length=200)
    status = models.CharField(max_length=20, default="sent")
    provider_response = models.TextField(blank=True)
    sent_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-sent_at"]


class SupportTicket(models.Model):
    STATUS = [("open", "Open"), ("pending", "Pending"), ("resolved", "Resolved"), ("closed", "Closed")]
    PRIORITY = [("low", "Low"), ("normal", "Normal"), ("high", "High"), ("urgent", "Urgent")]
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="tickets")
    opened_by = models.ForeignKey(django_settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    subject = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=STATUS, default="open")
    priority = models.CharField(max_length=10, choices=PRIORITY, default="normal")
    assigned_to = models.ForeignKey(django_settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_tickets")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]


class TicketMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(django_settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    body = models.TextField()
    attachment = models.FileField(upload_to="tickets/", null=True, blank=True)
    is_staff_reply = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class FeatureFlag(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    key = models.CharField(max_length=60, unique=True)
    description = models.CharField(max_length=200, blank=True)
    enabled = models.BooleanField(default=False)
    all_tenants = models.BooleanField(default=False)
    tenants = models.ManyToManyField(Tenant, blank=True, related_name="feature_flags")


class PlatformSetting(models.Model):
    """Single-row global settings (key/value JSON)."""

    key = models.CharField(max_length=60, primary_key=True)
    value = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get(cls, key, default=None):
        row = cls.objects.filter(pk=key).first()
        return row.value if row else default

    @classmethod
    def set(cls, key, value):
        row, _ = cls.objects.update_or_create(pk=key, defaults={"value": value})
        return row


class LegalDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    kind = models.CharField(max_length=20)  # terms | privacy
    version = models.CharField(max_length=20)
    body = models.TextField()
    published_at = models.DateTimeField(default=timezone.now)
    is_current = models.BooleanField(default=True)

    class Meta:
        ordering = ["-published_at"]


class MobileAppRelease(models.Model):
    """Uploaded mobile app builds (APK). The current release is what the QR code / download link serves."""

    PLATFORMS = [("android", "Android"), ("ios", "iOS")]
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    platform = models.CharField(max_length=10, choices=PLATFORMS, default="android")
    version = models.CharField(max_length=40)
    build_number = models.PositiveIntegerField(null=True, blank=True)
    file = models.FileField(upload_to="apps/")
    file_name = models.CharField(max_length=200, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)
    min_supported_version = models.CharField(max_length=40, blank=True)
    is_current = models.BooleanField(default=True)
    downloads = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(django_settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.platform} {self.version}"


class AgentToken(models.Model):
    """
    A credential for one of the teams building this system — the mobile app's AI, the server's, or a human.

    It is not a tenant API key: the mailbox below carries no business data, only messages between the people
    and agents doing the work. Stored as a SHA-256 hash with a readable prefix, the same way `ApiKey` is, so a
    leaked token can be identified in a log and rotated without reading it back.
    """

    ROLES = [("mobile", "Mobile app team"), ("server", "Server team"), ("owner", "Business owner"), ("design", "Design team")]

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    name = models.CharField(max_length=80)
    role = models.CharField(max_length=12, choices=ROLES, db_index=True)
    key_prefix = models.CharField(max_length=14, db_index=True)
    hashed_key = models.CharField(max_length=64, db_index=True)
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["role", "name"]

    def __str__(self):
        return f"{self.name} ({self.role})"

    @staticmethod
    def hash(raw):
        import hashlib

        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def issue(cls, name, role):
        import secrets

        raw = f"agent_{role}_" + secrets.token_urlsafe(24)
        return cls.objects.create(name=name, role=role, key_prefix=raw[:14], hashed_key=cls.hash(raw)), raw

    @classmethod
    def resolve(cls, raw):
        """The token on the wire → the token row, or None. Updates `last_used_at` so a dead key is visible."""
        if not raw:
            return None
        token = cls.objects.filter(key_prefix=raw[:14], hashed_key=cls.hash(raw), is_active=True).first()
        if token is not None:
            cls.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
        return token


class AgentMessage(models.Model):
    """
    One message in the build conversation: a request, a reply, or a note.

    Two AIs and a business owner are building this system from different sides, and until now every message
    between them was copied by hand through the owner. A message here belongs to a `thread`, names who it is
    `for`, and carries a `status` the sender does not own — the recipient closes a request, which is what makes
    "anything pending?" answerable without reading the whole history.
    """

    KINDS = [("request", "Request"), ("question", "Question"), ("reply", "Reply"), ("note", "Note")]
    STATUSES = [("open", "Open"), ("answered", "Answered"), ("closed", "Closed")]

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    thread = models.SlugField(max_length=80, db_index=True, help_text="Slug grouping a request with its replies")
    subject = models.CharField(max_length=200)
    sender = models.CharField(max_length=12, choices=AgentToken.ROLES, db_index=True)
    recipient = models.CharField(max_length=12, choices=AgentToken.ROLES, db_index=True)
    kind = models.CharField(max_length=10, choices=KINDS, default="note")
    body = models.TextField(help_text="Markdown — the whole message, as it would be pasted")
    in_reply_to = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="replies")
    # only meaningful on a request or a question; a reply inherits its parent's thread
    status = models.CharField(max_length=10, choices=STATUSES, default="open", db_index=True)
    client_id = models.CharField(max_length=64, blank=True, help_text="The sender's own id — so a retry cannot post twice")
    delivered_at = models.DateTimeField(null=True, blank=True, help_text="When the recipient first read it")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["recipient", "delivered_at"]), models.Index(fields=["thread", "created_at"])]
        constraints = [models.UniqueConstraint(fields=["sender", "client_id"], condition=models.Q(client_id__gt=""),
                                               name="agent_message_unique_client_id")]

    def __str__(self):
        return f"{self.sender}→{self.recipient} {self.subject[:40]}"

    @property
    def needs_an_answer(self):
        return self.kind in ("request", "question") and self.status == "open"
