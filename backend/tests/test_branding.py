"""Company profile, white-label branding and the platform's own branding settings."""
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture
def admin_api(admin_user):
    c = APIClient()
    r = c.post("/api/v1/auth/login", {"identifier": "admin-test", "password": "pak1stan"}, format="json")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
    return c


def test_company_profile_round_trip(api, tenant):
    r = api.get("/api/v1/settings/company")
    assert r.status_code == 200 and r.data["display_name"] == tenant.display_name
    assert "invoice_footer" in r.data["settings"] and "registration_no" in r.data["settings"]

    r = api.patch("/api/v1/settings/company", {"display_name": "Alpha Traders", "city": "Lahore", "phone": "+92421111111",
                                               "tax_numbers": {"NTN": "1234567-8"}, "settings": {"invoice_footer": "Thank you", "bank_details": "Meezan 0123"}}, format="json")
    assert r.status_code == 200 and r.data["display_name"] == "Alpha Traders" and r.data["tax_numbers"] == {"NTN": "1234567-8"}
    assert r.data["settings"]["invoice_footer"] == "Thank you" and r.data["settings"]["bank_details"] == "Meezan 0123"

    logo = api.post("/api/v1/settings/company/logo", {"file": SimpleUploadedFile("logo.png", PNG, content_type="image/png")}, format="multipart")
    assert logo.status_code == 200 and logo.data["logo"], logo.content


def test_white_label_is_off_until_the_platform_enables_it(api, admin_api, tenant, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    state = api.get("/api/v1/settings/branding").data
    assert state["enabled"] is False and "not part of your plan" in state["upsell"]
    assert api.patch("/api/v1/settings/branding", {"app_name": "Alpha Suite"}, format="json").status_code == 403
    assert api.post("/api/v1/settings/branding/asset", {"kind": "logo", "file": SimpleUploadedFile("l.png", PNG)}, format="multipart").status_code == 403
    # /me hides branding while it is off
    with tenant_context(tenant, tenant.owner):
        tenant.branding = {"app_name": "Sneaky"}
        tenant.save(update_fields=["branding"])
    me = api.get("/api/v1/me").data
    assert me["tenant"]["white_label"] is False and me["tenant"]["branding"] == {}

    # the platform admin switches it on for this tenant only
    r = admin_api.post(f"/api/v1/platform/tenants/{tenant.id}/set-feature", {"key": "branding", "value": True}, format="json")
    assert r.status_code == 200 and r.data["white_label"] is True
    assert r.data["effective_features"]["branding"] == {"value": True, "from_plan": False, "overridden": True}

    state = api.get("/api/v1/settings/branding").data
    assert state["enabled"] is True and state["upsell"] == ""
    r = api.patch("/api/v1/settings/branding", {"app_name": "Alpha Suite", "primary_color": "#123456", "hide_powered_by": True}, format="json")
    assert r.status_code == 200 and r.data["branding"]["app_name"] == "Alpha Suite" and r.data["branding"]["hide_powered_by"] is True
    up = api.post("/api/v1/settings/branding/asset", {"kind": "logo", "file": SimpleUploadedFile("logo.png", PNG, content_type="image/png")}, format="multipart")
    assert up.status_code == 200 and up.data["branding"]["logo"].startswith("/media/")
    me = api.get("/api/v1/me").data
    assert me["tenant"]["white_label"] is True and me["tenant"]["branding"]["app_name"] == "Alpha Suite"

    # and back to the plan default
    assert admin_api.post(f"/api/v1/platform/tenants/{tenant.id}/set-feature", {"key": "branding", "value": None}, format="json").data["white_label"] is False
    assert api.get("/api/v1/me").data["tenant"]["branding"] == {}


def test_platform_branding_is_public_and_admin_only_to_edit(admin_api, api, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    assert APIClient().get("/api/v1/branding").status_code == 200
    assert api.patch("/api/v1/platform/settings", {"site_name": "Nope"}, format="json").status_code in (403, 404)

    r = admin_api.patch("/api/v1/platform/settings", {"site_name": "InvoiceFlow", "support_email": "help@x.pk", "currencies": "PKR, USD"}, format="json")
    assert r.status_code == 200 and r.data["site_name"] == "InvoiceFlow" and r.data["currencies"] == ["PKR", "USD"]
    up = admin_api.post("/api/v1/platform/settings/asset", {"kind": "favicon", "file": SimpleUploadedFile("f.png", PNG, content_type="image/png")}, format="multipart")
    assert up.status_code == 200 and up.data["favicon_url"].startswith("/media/branding/")
    public = APIClient().get("/api/v1/branding").data
    assert public["site_name"] == "InvoiceFlow" and public["favicon_url"] == up.data["favicon_url"] and "login_headline" in public


def test_me_serves_effective_settings_not_just_the_stored_overrides(api, tenant):
    """Irfan Aleem's missing photo buttons (26 Oct 2026): /me sent only the tenant's stored settings
    column, so a company that never touched Business rules served no `rep_order_photo` at all and
    the mobile app fell back to its own "off" — hiding the shop/shelf photo tiles from every rep.
    /me must serve the same effective merge Tenant.setting() reads."""
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        (tenant.settings or {}).pop("rep_order_photo", None)
        tenant.save(update_fields=["settings"])

    got = api.get("/api/v1/me").data["tenant"]["settings"]
    assert got.get("rep_order_photo") == "optional", "the code default must reach the client"
    assert got.get("reserve_stock_on_confirm") is True

    # a stored override still wins over the default
    with tenant_context(tenant, tenant.owner):
        tenant.settings = {**(tenant.settings or {}), "rep_order_photo": "required"}
        tenant.save(update_fields=["settings"])
    assert api.get("/api/v1/me").data["tenant"]["settings"]["rep_order_photo"] == "required"


def test_google_maps_is_a_map_choice_and_falls_back_without_a_key(admin_api, settings):
    """Owner, 28 Oct 2026: "use google map and google search now instead of other map" — the browser key comes
    from the server's settings (never stored in the repository); without one the map stays on English tiles."""
    from django.core.cache import cache

    settings.GOOGLE_MAPS_API_KEY = "test-browser-key"
    assert admin_api.patch("/api/v1/platform/settings", {"map_tiles": "google"}, format="json").status_code == 200
    cache.clear()
    m = APIClient().get("/api/v1/branding").data["map"]
    assert m["source"] == "google" and m["google_key"] == "test-browser-key" and m["url"].startswith("https://")
    settings.GOOGLE_MAPS_API_KEY = ""
    cache.clear()
    m = APIClient().get("/api/v1/branding").data["map"]
    assert m["source"] == "esri_en" and not m["google_key"], "no key: a working map beats a blank one"
