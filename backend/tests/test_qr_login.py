"""Web → phone QR login: create (signed-in web user) → redeem on the phone (no password) → single use, short-lived."""
import pytest
from rest_framework.test import APIClient

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def owner_password_settled(tenant):
    """New tenant owners must change their password first; QR login is blocked until then (tested separately)."""
    tenant.owner.must_change_password = False
    tenant.owner.save(update_fields=["must_change_password"])


def test_qr_login_round_trip(api, tenant):
    r = api.post("/api/v1/auth/qr-login/create")
    assert r.status_code == 200, r.content
    code = r.data
    assert code["qr_png"].startswith("data:image/png;base64,") and code["url"].startswith("http") and "app-login?t=" in code["url"]
    assert code["deep_link"].startswith("dms://login?t=") and code["tenant_id"] == str(tenant.id) and code["expires_in"] == 300
    assert api.get(f"/api/v1/auth/qr-login/status?id={code['id']}").data["status"] == "pending"

    phone = APIClient()  # no credentials at all
    r = phone.post("/api/v1/auth/qr-login/redeem", {"token": code["url"], "device_name": "Pixel 7", "platform": "android"}, format="json")
    assert r.status_code == 200, r.content
    assert r.data["user"]["id"] and r.data["access"] and r.data["refresh"] and r.data["tenant_id"] == str(tenant.id) and r.data["login_method"] == "qr"
    assert [m["tenant_id"] for m in r.data["memberships"]] == [str(tenant.id)]

    # the phone's tokens work like a password login
    phone.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}", HTTP_X_TENANT_ID=str(tenant.id))
    me = phone.get("/api/v1/me")
    assert me.status_code == 200 and me.data["is_owner"] is True
    # the web modal sees it was used, and on which device
    st = api.get(f"/api/v1/auth/qr-login/status?id={code['id']}").data
    assert st["status"] == "used" and st["redeemed_device"] == "Pixel 7"
    # a session row exists for the phone
    sessions = api.get("/api/v1/me/sessions").data
    rows = sessions["results"] if isinstance(sessions, dict) and "results" in sessions else sessions
    assert any(s["device"] == "Pixel 7" and s["platform"] == "android" for s in rows)


def test_qr_code_is_single_use_and_expires(api, tenant):
    from django.utils import timezone

    from apps.accounts.models import QrLoginToken

    code = api.post("/api/v1/auth/qr-login/create").data
    phone = APIClient()
    assert phone.post("/api/v1/auth/qr-login/redeem", {"token": code["deep_link"]}, format="json").status_code == 200
    again = phone.post("/api/v1/auth/qr-login/redeem", {"token": code["deep_link"]}, format="json")
    assert again.status_code == 410 and again.data["type"] == "used"

    code2 = api.post("/api/v1/auth/qr-login/create").data
    QrLoginToken.objects.filter(pk=code2["id"]).update(expires_at=timezone.now())
    r = phone.post("/api/v1/auth/qr-login/redeem", {"token": code2["url"]}, format="json")
    assert r.status_code == 410 and r.data["type"] == "expired"
    assert phone.get(f"/api/v1/auth/qr-login/status?t={code2['url'].split('t=')[1]}").data["status"] == "expired"
    assert phone.post("/api/v1/auth/qr-login/redeem", {"token": "garbage"}, format="json").status_code == 400
    assert phone.get("/api/v1/auth/qr-login/status?t=garbage").data["status"] == "invalid"


def test_new_code_invalidates_previous_and_admins_cannot_create(api, tenant, admin_user):
    first = api.post("/api/v1/auth/qr-login/create").data
    second = api.post("/api/v1/auth/qr-login/create").data
    assert api.get(f"/api/v1/auth/qr-login/status?id={first['id']}").data["status"] == "expired"
    assert api.get(f"/api/v1/auth/qr-login/status?id={second['id']}").data["status"] == "pending"

    admin = APIClient()
    r = admin.post("/api/v1/auth/login", {"identifier": "admin-test", "password": "pak1stan"}, format="json")
    admin.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
    assert admin.post("/api/v1/auth/qr-login/create").status_code == 403
    assert APIClient().post("/api/v1/auth/qr-login/create").status_code == 401
    tenant.owner.must_change_password = True
    tenant.owner.save(update_fields=["must_change_password"])
    assert api.post("/api/v1/auth/qr-login/create").status_code == 403


def test_assetlinks_and_app_identity(admin_user, tenant):
    admin = APIClient()
    r = admin.post("/api/v1/auth/login", {"identifier": "admin-test", "password": "pak1stan"}, format="json")
    admin.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
    assert admin.get("/.well-known/assetlinks.json").json() == []
    r = admin.patch("/api/v1/platform/app-releases/config", {"android_package": "pk.invoiceflow.dms", "sha256_fingerprints": "AA:BB\ncc:dd"}, format="json")
    assert r.status_code == 200 and r.data["assetlinks_ready"] is True and r.data["sha256_fingerprints"] == ["AA:BB", "CC:DD"]
    links = APIClient().get("/.well-known/assetlinks.json").json()
    assert links[0]["target"] == {"namespace": "android_app", "package_name": "pk.invoiceflow.dms", "sha256_cert_fingerprints": ["AA:BB", "CC:DD"]}
    info = APIClient().get("/api/v1/app/android").data
    assert info["package_name"] == "pk.invoiceflow.dms" and info["scheme"] == "dms" and info["app_links_ready"] is True and info["app_login_url"].endswith("/app-login")
