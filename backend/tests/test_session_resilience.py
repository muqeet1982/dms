"""
Staying signed in: a refresh token presented twice within the grace window (two tabs, a retried request) must not
log the user out, real reuse still must, and a platform admin working inside a tenant must not be thrown out while
the impersonation window slides forward.
"""
import pytest
from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


def _login(tenant):
    c = APIClient()
    r = c.post("/api/v1/auth/login", {"identifier": f"owner@{tenant.slug}.pk", "password": "owner123"}, format="json")
    assert r.status_code == 200, r.content
    return c, r.data


def test_racing_refresh_keeps_the_session(tenant):
    c, tokens = _login(tenant)
    first = c.post("/api/v1/auth/refresh", {"refresh": tokens["refresh"]}, format="json")
    assert first.status_code == 200, first.content

    # The second tab was still holding the previous token: it gets a working pair, not a logout.
    second = c.post("/api/v1/auth/refresh", {"refresh": tokens["refresh"]}, format="json")
    assert second.status_code == 200, second.content
    assert second.data["access"] != first.data["access"]

    for pair in (first.data, second.data):
        probe = APIClient()
        probe.credentials(HTTP_AUTHORIZATION=f"Bearer {pair['access']}", HTTP_X_TENANT_ID=str(tenant.id))
        assert probe.get("/api/v1/me").status_code == 200


def test_reuse_outside_the_grace_window_is_still_refused(tenant, settings):
    c, tokens = _login(tenant)
    assert c.post("/api/v1/auth/refresh", {"refresh": tokens["refresh"]}, format="json").status_code == 200
    from apps.accounts.models import UserSession

    UserSession.objects.update(rotated_at=timezone.now() - timedelta(seconds=settings.DMS["REFRESH_GRACE_SECONDS"] + 60))
    r = c.post("/api/v1/auth/refresh", {"refresh": tokens["refresh"]}, format="json")
    assert r.status_code == 401 and r.data["type"] == "token_reuse"
    assert UserSession.objects.filter(revoked_at__isnull=False).exists()


def test_refresh_rejects_a_forged_token(tenant):
    c, tokens = _login(tenant)
    forged = tokens["refresh"][:-4] + "abcd"
    r = c.post("/api/v1/auth/refresh", {"refresh": forged}, format="json")
    assert r.status_code == 401


def test_impersonation_slides_forward_while_the_admin_works(tenant, admin_user):
    from apps.accounts.models import ImpersonationSession
    from apps.platform.services import TenantService

    session = TenantService.start_impersonation(admin_user, tenant, reason="support")
    c = APIClient()
    r = c.post("/api/v1/auth/login", {"identifier": "admin-test", "password": "pak1stan"}, format="json")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}", HTTP_X_TENANT_ID=str(tenant.id))
    assert c.get("/api/v1/me").data["impersonating"] is True

    # Almost out of time, but still at the keyboard → the window moves, no interruption.
    near = timezone.now() + timedelta(minutes=2)
    ImpersonationSession.objects.filter(pk=session.pk).update(expires_at=near)
    me = c.get("/api/v1/me")
    assert me.status_code == 200 and me.data["impersonating"] is True
    assert ImpersonationSession.objects.get(pk=session.pk).expires_at > near + timedelta(minutes=30)


def test_expired_impersonation_answers_with_a_code_the_app_can_act_on(tenant, admin_user):
    from apps.accounts.models import ImpersonationSession
    from apps.platform.services import TenantService

    session = TenantService.start_impersonation(admin_user, tenant, reason="support")
    ImpersonationSession.objects.filter(pk=session.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
    c = APIClient()
    r = c.post("/api/v1/auth/login", {"identifier": "admin-test", "password": "pak1stan"}, format="json")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}", HTTP_X_TENANT_ID=str(tenant.id))
    out = c.get("/api/v1/me")
    assert out.status_code == 403 and out.data["type"] == "impersonation_expired"

    # Without the stale company header the admin is still perfectly signed in.
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
    assert c.get("/api/v1/me").status_code == 200
