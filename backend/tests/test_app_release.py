"""APK distribution: deleting a build removes its file; purge-old keeps the current build and sweeps orphans."""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_api(admin_user):
    c = APIClient()
    r = c.post("/api/v1/auth/login", {"identifier": "admin-test", "password": "pak1stan"}, format="json")
    assert r.status_code == 200, r.content
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
    return c


@pytest.fixture
def media(settings, tmp_path):
    """Isolated MEDIA_ROOT so the orphan sweep can never touch real builds."""
    from apps.platform.models import MobileAppRelease

    settings.MEDIA_ROOT = str(tmp_path)
    storage = MobileAppRelease._meta.get_field("file").storage
    assert str(storage.location).startswith(str(tmp_path))
    return tmp_path


def upload(api, version, current):
    r = api.post("/api/v1/platform/app-releases", {"platform": "android", "version": version, "is_current": "true" if current else "false", "file": SimpleUploadedFile(f"dms-{version}.apk", b"PK" + version.encode() * 1000, content_type="application/vnd.android.package-archive")}, format="multipart")
    assert r.status_code == 201, r.content
    return r.data


def test_delete_removes_apk_file(admin_api, media):
    rel = upload(admin_api, "1.0", True)
    old = upload(admin_api, "0.9", False)
    apps_dir = media / "apps"
    assert len(list(apps_dir.iterdir())) == 2
    r = admin_api.delete(f"/api/v1/platform/app-releases/{old['id']}")
    assert r.status_code == 204
    assert [p.name for p in apps_dir.iterdir()] == [rel["file_name"]]
    assert admin_api.get("/api/v1/app/android").data["version"] == "1.0"


def test_purge_old_keeps_current_and_sweeps_orphans(admin_api, media):
    upload(admin_api, "0.8", False)
    upload(admin_api, "0.9", False)
    cur = upload(admin_api, "1.0", True)
    apps_dir = media / "apps"
    (apps_dir / "leftover.apk").write_bytes(b"x" * 10)  # a file nobody references
    st = admin_api.get("/api/v1/platform/app-releases/storage").data
    assert st["count"] == 4 and st["orphans"] == 1
    r = admin_api.post("/api/v1/platform/app-releases/purge-old", {"platform": "android"}, format="json")
    assert r.status_code == 200, r.content
    assert r.data["deleted"] == 2 and r.data["orphans_removed"] == 1 and r.data["count"] == 1
    assert [p.name for p in apps_dir.iterdir()] == [cur["file_name"]]
    assert admin_api.get("/api/v1/platform/app-releases").data["count"] == 1
