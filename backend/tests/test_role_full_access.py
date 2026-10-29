"""
Owner, 29 Oct 2026: "another user created as user is not seeing same thing as owner … do complete audit and debug
permission and role". The audit: the server gives the owner every page; TDN's hand-ticked "superadmin" role was 16
pages and 34 pages' buttons short of it, and no page added later ever reached it. A role can now be Full access —
the owner's rights, page for page, including pages added later — and an owner's forgotten "log in as" session no
longer follows them into a fresh login.
"""
import pytest
from rest_framework.test import APIClient

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db


def _user(api, tenant, email, role_id):
    from apps.accounts.models import User

    r = api.post("/api/v1/users", {"full_name": email.split("@")[0], "email": email, "password": "Abcd-1234!", "roles": [str(role_id)]}, format="json")
    assert r.status_code == 201, r.content
    User.objects.filter(email=email).update(must_change_password=False)
    c = APIClient()
    login = c.post("/api/v1/auth/login", {"identifier": email, "password": "Abcd-1234!"}, format="json")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}", HTTP_X_TENANT_ID=str(tenant.id))
    return r.data["id"], c


def test_a_full_access_role_sees_and_does_everything_the_owner_does(api, tenant):
    owner_me = api.get("/api/v1/me").data
    r = api.post("/api/v1/roles", {"name": "Superadmin", "role_type": "internal", "permissions": ["sales.view_sales_order"], "full_access": True}, format="json")
    assert r.status_code == 201, r.content
    assert r.data["full_access"] is True
    _mid, c = _user(api, tenant, "super@probe.pk", r.data["id"])

    me = c.get("/api/v1/me").data
    assert me["is_owner"] is False, "full access is not ownership"
    assert me["pages"] == owner_me["pages"], "every page, every button, every extra — the owner's map exactly"
    assert set(me["permissions"]) == set(owner_me["permissions"])
    assert c.get("/api/v1/settings/company").status_code == 200, "an owner-level page the old role could not open"
    with tenant_context(tenant):
        from apps.accounts.models import TenantMembership

        m = TenantMembership.objects.get(user__email="super@probe.pk", tenant=tenant)
        assert m.has_perm("sales.edit_posted_invoice") and not m.has_perm("external.submit_order") and m.approval_limit("expense") is None


def test_only_someone_with_full_access_can_hand_it_out(api, tenant):
    r = api.post("/api/v1/roles", {"name": "Role admin", "role_type": "internal",
                                   "permissions": ["users.view_role", "users.add_role", "users.change_role", "users.view_user"]}, format="json")
    assert r.status_code == 201, r.content
    _mid, c = _user(api, tenant, "roleadmin@probe.pk", r.data["id"])
    refused = c.post("/api/v1/roles", {"name": "Back door", "role_type": "internal", "permissions": [], "full_access": True}, format="json")
    assert refused.status_code == 403, refused.content
    flip = c.patch(f"/api/v1/roles/{r.data['id']}", {"full_access": True}, format="json")
    assert flip.status_code == 403, "not even on their own role"
    ok = c.post("/api/v1/roles", {"name": "Plain", "role_type": "internal", "permissions": ["sales.view_sales_order"]}, format="json")
    assert ok.status_code == 201, "an ordinary role is still theirs to make"


def test_a_fresh_owner_login_is_the_owner_again_not_the_user_they_were_viewing_as(api, tenant):
    from apps.accounts.services import RoleService

    with tenant_context(tenant, tenant.owner):
        office = RoleService.create_role(tenant, "Office Only", ["sales.view_sales_order"])
    mid, _c = _user(api, tenant, "staff@probe.pk", office.id)
    assert api.post(f"/api/v1/users/{mid}/login-as", {}, format="json").status_code == 200
    assert api.get("/api/v1/me").data["impersonating"] is True

    fresh = APIClient()
    login = fresh.post("/api/v1/auth/login", {"identifier": f"owner@{tenant.slug}.pk", "password": "owner123"}, format="json")
    fresh.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}", HTTP_X_TENANT_ID=str(tenant.id))
    me = fresh.get("/api/v1/me").data
    assert me["is_owner"] is True and me["impersonating"] is False, "the forgotten 'log in as' no longer follows the owner"
