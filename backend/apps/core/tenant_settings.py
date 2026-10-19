"""
Tenant-side settings the company edits about itself:

* `/settings/company` — the legal identity that appears on invoices, POs and emails (name, address, tax numbers,
  logo, currency, fiscal year, document footers). Always available: it is the tenant's own data.
* `/settings/branding` — **white-label**: replacing the product's name/logo/colours inside the app. Only the
  platform admin can grant this (plan feature `branding`, or a per-tenant override); without it the endpoint
  answers with `enabled: false` and refuses writes, and `/me` hides the stored branding so the UI falls back to
  the platform's own logo and name.
"""

from django.conf import settings as django_settings
from django.core.files.storage import default_storage
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import AuditService
from apps.core.exceptions import DomainError
from apps.core.permissions import IsTenantUser, require_perm
from apps.core.plan_limits import effective_features
from apps.core.uuid7 import uuid7

IMAGE_EXT = ("png", "jpg", "jpeg", "svg", "webp", "ico", "gif")
MAX_IMAGE_MB = 5

COMPANY_FIELDS = ["legal_name", "display_name", "address", "city", "phone", "email", "website", "tax_numbers", "base_currency", "fiscal_year_start_month", "timezone"]
# Free-form company/document options kept in Tenant.settings
SETTINGS_KEYS = {
    "invoice_footer": "", "quote_footer": "", "po_footer": "", "bank_details": "", "contact_person": "", "support_phone": "",
    "registration_no": "", "license_no": "", "default_credit_days": 0, "min_shelf_life_days": 0, "allow_negative_stock": False,
    "price_change_policy": "flag", "require_delivery_otp": False, "whatsapp": "", "facebook": "", "instagram": "", "linkedin": "",
    # field work: the rules the rep app is held to (see apps/partners/geofence.py)
    "geofence_policy": "block", "geofence_require_location": True, "order_location_rule": "shop", "order_proximity_m": 50, "rep_order_photo": "optional", "rep_visit_photo": "required",
    "tracking_enabled": True, "tracking_mode": "work_hours",
    "require_shift_for_orders": False, "shift_gap_minutes": 15, "shift_lost_end_minutes": 30, "shift_auto_end_at": "23:59", "tracking_interval_s": 120, "tracking_min_distance_m": 50,
    "tracking_from": "09:00", "tracking_to": "19:00", "tracking_retention_days": 90, "tracking_notice": "",
}
BRANDING_KEYS = {"app_name": "", "logo": "", "logo_dark": "", "favicon": "", "primary_color": "", "accent_color": "", "footer_text": "", "email_from_name": "", "hide_powered_by": False}
BRANDING_ASSETS = ("logo", "logo_dark", "favicon")


def save_image(f, folder, kind):
    if f is None:
        raise DomainError("Choose an image file to upload.", status_code=400)
    if f.size > MAX_IMAGE_MB * 1024 * 1024:
        raise DomainError(f"Image too large — keep it under {MAX_IMAGE_MB} MB.", status_code=400)
    ext = (f.name.rsplit(".", 1)[-1] or "png").lower()[:5]
    if ext not in IMAGE_EXT:
        raise DomainError("Use a PNG, JPG, SVG, WEBP or ICO file.", status_code=400)
    path = default_storage.save(f"{folder}/{kind}-{uuid7().hex[:8]}.{ext}", f)
    return f"{django_settings.MEDIA_URL}{path}"


def drop_old(url):
    if url and str(url).startswith(django_settings.MEDIA_URL):
        try:
            default_storage.delete(str(url)[len(django_settings.MEDIA_URL):])
        except Exception:  # pragma: no cover - storage hiccup, the file simply stays
            pass


class CompanyProfileView(APIView):
    """GET / PATCH the company's own details, and POST a company logo (multipart `file`) for documents."""

    permission_classes = [IsTenantUser]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def payload(self, tenant):
        from apps.platform.serializers import TenantPublicSerializer

        data = TenantPublicSerializer(tenant).data
        data["settings"] = {**SETTINGS_KEYS, **(tenant.settings or {})}
        data["settings_schema"] = SETTINGS_KEYS
        return data

    @require_perm("settings.view_company")
    def get(self, request):
        return Response(self.payload(request.tenant))

    @require_perm("settings.change_company")
    def patch(self, request):
        tenant = request.tenant
        before = {f: getattr(tenant, f) for f in COMPANY_FIELDS}
        changed = []
        for field in COMPANY_FIELDS:
            if field in request.data:
                value = request.data[field]
                if field == "tax_numbers" and isinstance(value, str):
                    import json

                    value = json.loads(value or "{}")
                if field == "fiscal_year_start_month":
                    value = int(value or 1)
                    if not 1 <= value <= 12:
                        raise DomainError("Fiscal year start month must be between 1 and 12.", status_code=400)
                setattr(tenant, field, value)
                changed.append(field)
        if "settings" in request.data and isinstance(request.data["settings"], dict):
            tenant.settings = {**(tenant.settings or {}), **{k: v for k, v in request.data["settings"].items() if k in SETTINGS_KEYS}}
            changed.append("settings")
        if changed:
            tenant.save(update_fields=[*{f for f in changed if f != "settings"}, *(["settings"] if "settings" in changed else []), "updated_at"] if hasattr(tenant, "updated_at") else None)
            AuditService.log("tenant.company.updated", tenant, before=before, after={f: getattr(tenant, f) for f in COMPANY_FIELDS}, request=request)
        return Response(self.payload(tenant))

    @require_perm("settings.change_company")
    def post(self, request):
        """Upload the company logo used on invoices, POs and delivery notes."""
        tenant = request.tenant
        clearing = request.data.get("clear") in ("1", "true", True)
        f = request.FILES.get("file")
        if clearing:
            tenant.logo.delete(save=True)
        else:
            if f is None:
                raise DomainError("Send the logo as `file`.", status_code=400)
            if f.size > MAX_IMAGE_MB * 1024 * 1024:
                raise DomainError(f"Image too large — keep it under {MAX_IMAGE_MB} MB.", status_code=400)
            tenant.logo.save(f.name, f, save=True)
        AuditService.log("tenant.logo.updated", tenant, after={"logo": tenant.logo.name}, request=request)
        return Response(self.payload(tenant))


class BrandingView(APIView):
    """
    GET / PATCH white-label branding, POST a branding image (`kind` = logo | logo_dark | favicon).

    Reads are always allowed so the page can show what is stored and whether it is active; writes need the
    `branding` feature (plan or per-tenant override) plus `settings.manage_branding`.
    """

    permission_classes = [IsTenantUser]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def state(self, tenant):
        enabled = bool(effective_features(tenant).get("branding"))
        from apps.platform.views import PlatformSettingsView

        platform = PlatformSettingsView.current()
        return {
            "enabled": enabled,
            "branding": {**BRANDING_KEYS, **(tenant.branding or {})},
            "company_logo": tenant.logo.url if tenant.logo else "",
            "max_image_mb": MAX_IMAGE_MB,
            # what the app shows while white-label is off, so the page can preview the difference
            "platform": {k: platform.get(k) for k in ("site_name", "site_tagline", "logo_url", "favicon_url", "primary_color", "footer_text")},
            "upsell": "" if enabled else "White-label branding is not part of your plan. Ask the platform administrator to enable it for your company.",
        }

    @require_perm("settings.view_branding")
    def get(self, request):
        return Response(self.state(request.tenant))

    def require_enabled(self, tenant):
        if not effective_features(tenant).get("branding"):
            raise DomainError("White-label branding is not enabled for your company — ask the platform administrator.", code="feature_off", status_code=403)

    @require_perm("settings.change_branding")
    def patch(self, request):
        tenant = request.tenant
        self.require_enabled(tenant)
        branding = {**BRANDING_KEYS, **(tenant.branding or {})}
        for key in BRANDING_KEYS:
            if key in request.data:
                value = request.data[key]
                branding[key] = bool(value) if isinstance(BRANDING_KEYS[key], bool) else str(value or "").strip()[:200]
        tenant.branding = branding
        tenant.save(update_fields=["branding"])
        AuditService.log("tenant.branding.updated", tenant, after=branding, request=request)
        return Response(self.state(tenant))

    @require_perm("settings.change_branding")
    def post(self, request):
        tenant = request.tenant
        self.require_enabled(tenant)
        kind = request.data.get("kind")
        if kind not in BRANDING_ASSETS:
            raise DomainError(f"kind must be one of: {', '.join(BRANDING_ASSETS)}.", status_code=400)
        url = save_image(request.FILES.get("file"), f"tenants/{tenant.slug}/branding", kind)
        branding = {**BRANDING_KEYS, **(tenant.branding or {})}
        drop_old(branding.get(kind))
        branding[kind] = url
        tenant.branding = branding
        tenant.save(update_fields=["branding"])
        AuditService.log("tenant.branding.asset", tenant, after={"kind": kind, "url": url}, request=request)
        return Response(self.state(tenant))
