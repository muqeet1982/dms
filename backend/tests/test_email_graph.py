"""
Sending mail through Microsoft 365, configured from the admin page rather than a file.

Nothing here talks to Microsoft — the point is the wiring and, above all, that the client secret never comes
back out of the API. The live connection was proved by sending a real message.
"""
import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def platform_api(admin_user):
    c = APIClient()
    r = c.post("/api/v1/auth/login", {"identifier": "admin-test", "password": "pak1stan"}, format="json")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
    return c

SECRET = "a-secret-that-must-never-come-back"


@pytest.fixture
def configured(platform_api):
    from apps.platform.models import PlatformSetting

    PlatformSetting.set("global", {
        "email_provider": "graph", "graph_tenant_id": "tenant-guid", "graph_client_id": "client-guid",
        "graph_client_secret": SECRET, "email_from_address": "info@example.com",
    })
    from django.core.cache import cache

    cache.delete("platform.settings")
    return platform_api


def test_the_secret_never_leaves_the_server(configured):
    r = configured.get("/api/v1/platform/settings")
    assert r.status_code == 200, r.content
    assert SECRET not in str(r.data), "the client secret was sent to the browser"
    assert r.data["graph_client_secret"].startswith("••••") and r.data["graph_client_secret"].endswith(SECRET[-4:])
    assert r.data["graph_client_secret_set"] is True


def test_saving_the_masked_secret_back_does_not_erase_it(configured):
    shown = configured.get("/api/v1/platform/settings").data
    saved = configured.patch("/api/v1/platform/settings", {"graph_client_secret": shown["graph_client_secret"],
                                                           "graph_tenant_id": "another-guid"}, format="json")
    assert saved.status_code == 200, saved.content
    from apps.platform.models import PlatformSetting

    stored = PlatformSetting.get("global", {})
    assert stored["graph_client_secret"] == SECRET, "editing another field wiped the secret"
    assert stored["graph_tenant_id"] == "another-guid"


def test_a_new_secret_replaces_the_old_one(configured):
    configured.patch("/api/v1/platform/settings", {"graph_client_secret": "a-new-one"}, format="json")
    from apps.platform.models import PlatformSetting

    assert PlatformSetting.get("global", {})["graph_client_secret"] == "a-new-one"


def test_testing_another_provider_says_so_rather_than_sending(platform_api):
    from django.core.cache import cache

    from apps.platform.models import PlatformSetting

    PlatformSetting.set("global", {"email_provider": "smtp"})
    cache.delete("platform.settings")
    r = platform_api.post("/api/v1/platform/settings/email-test", {"to": "someone@example.com"}, format="json")
    assert r.status_code == 400 and "Microsoft 365" in r.data["detail"]


def test_a_test_with_nobody_to_send_to_is_refused(configured):
    r = configured.post("/api/v1/platform/settings/email-test", {"to": ""}, format="json")
    assert r.status_code == 400


def test_missing_credentials_name_what_is_missing():
    from apps.core.mail_graph import GraphMailError, token

    with pytest.raises(GraphMailError) as e:
        token({"tenant_id": "t", "client_id": "", "client_secret": "s", "sender": "a@b.c"})
    assert "Application (client) ID" in str(e.value)


def test_a_failed_send_never_breaks_what_triggered_it(configured, monkeypatch):
    """An invitation that cannot be posted must still create the user."""
    from apps.core import mail_graph, notifications

    def boom(*a, **kw):
        raise mail_graph.GraphMailError("Microsoft is having a day")

    monkeypatch.setattr(mail_graph, "send", boom)
    assert notifications.send_email("someone@example.com", "Subject", "Body") is False
