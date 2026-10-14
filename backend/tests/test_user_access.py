"""
Giving a user a role, and what that role then lets them reach.

The permission engine was already right — a user's pages are resolved from the roles they hold. What was
missing was any way to *grant* a field role from the user screen, and a guard on the one combination that
grants permissions a login cannot use: rep permissions with no rep record.
"""
import pytest

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def roles(tenant):
    """One office role and one field role, as the Roles screen creates them."""
    from apps.accounts.services import RoleService

    with tenant_context(tenant, tenant.owner):
        office = RoleService.create_role(tenant, "Office Probe", ["sales.view_sales_order", "customers.view_customer"])
        field = RoleService.create_role(tenant, "Field Probe", ["external.submit_order", "external.view_assigned_customers"],
                                        role_type="external")
    return {"office": office, "field": field}


def _supplier(tenant):
    from apps.partners.models import Supplier

    with tenant_context(tenant, tenant.owner):
        return Supplier.objects.create(code="S-PROBE", name="Probe Supplier")


def test_a_field_role_can_be_given_from_the_user_screen(api, tenant, roles):
    """
    It could not before: the user endpoint refused outright with "External roles are assigned through
    Suppliers → Reps", which is why the owner could create a field role and then not find anywhere to use it.
    """
    supplier = _supplier(tenant)
    r = api.post("/api/v1/users", {
        "full_name": "Field Hire", "email": "field.hire@probe.pk", "password": "Abcd-1234!",
        "roles": [str(roles["field"].id)], "rep_for_supplier": str(supplier.id),
    }, format="json")
    assert r.status_code == 201, r.content
    assert r.data["role_names"] == ["Field Probe"]
    assert r.data["supplier_rep"], "a field role without a rep record cannot work, so one was created"
    assert "Probe Supplier" in r.data["rep_name"]


def test_a_field_role_with_no_rep_is_refused_with_a_reason(api, tenant, roles):
    """The refusal is the point: rep permissions and no rep identity is an unscoped login, not a narrow one."""
    r = api.post("/api/v1/users", {
        "full_name": "Repless", "email": "repless@probe.pk", "password": "Abcd-1234!",
        "roles": [str(roles["field"].id)],
    }, format="json")
    assert r.status_code == 400 and r.data["type"] == "rep_required", r.content
    assert "rep record" in r.data["detail"]


def test_a_user_can_hold_both_an_office_role_and_a_field_role(api, tenant, roles):
    """A supervisor who also sells: the office pages come from one role, the rep app from the other."""
    supplier = _supplier(tenant)
    r = api.post("/api/v1/users", {
        "full_name": "Both Hats", "email": "both.hats@probe.pk", "password": "Abcd-1234!",
        "roles": [str(roles["office"].id), str(roles["field"].id)], "rep_for_supplier": str(supplier.id),
    }, format="json")
    assert r.status_code == 201, r.content
    assert sorted(r.data["role_names"]) == ["Field Probe", "Office Probe"]

    access = api.get(f"/api/v1/users/{r.data['id']}/access")
    assert access.status_code == 200, access.content
    assert {p["key"] for p in access.data["pages"]} >= {"sales_orders", "customers"}, access.data["pages"]
    assert access.data["field_permissions"], "and the rep app side is there too"
    assert access.data["field_without_rep"] is False


def test_taking_the_last_field_role_away_unlinks_the_rep(api, tenant, roles):
    """The login is office staff again, and a rep record nobody holds should not keep pointing at it."""
    supplier = _supplier(tenant)
    made = api.post("/api/v1/users", {
        "full_name": "Promoted", "email": "promoted@probe.pk", "password": "Abcd-1234!",
        "roles": [str(roles["field"].id)], "rep_for_supplier": str(supplier.id),
    }, format="json")
    assert made.status_code == 201 and made.data["supplier_rep"]

    moved = api.patch(f"/api/v1/users/{made.data['id']}", {"roles": [str(roles["office"].id)]}, format="json")
    assert moved.status_code == 200, moved.content
    assert not moved.data["supplier_rep"], "no field role left, so no rep link"


def test_the_access_view_answers_what_this_user_can_see(api, tenant, roles):
    """
    The owner's actual request: a role should visibly decide which pages a user gets. This is that, resolved
    the same way the user's own session resolves it, so an administrator never has to log in as somebody to
    find out what they just granted.
    """
    made = api.post("/api/v1/users", {
        "full_name": "Office Only", "email": "office.only@probe.pk", "password": "Abcd-1234!",
        "roles": [str(roles["office"].id)],
    }, format="json")
    assert made.status_code == 201, made.content

    access = api.get(f"/api/v1/users/{made.data['id']}/access")
    keys = {p["key"] for p in access.data["pages"]}
    assert "sales_orders" in keys and "customers" in keys
    assert "users" not in keys, "a role that does not grant it must not show the page"
    assert 0 < len(access.data["pages"]) < access.data["pages_total"]
    orders = next(p for p in access.data["pages"] if p["key"] == "sales_orders")
    assert orders["actions"]["view"] is True and orders["actions"]["delete"] is False
    assert orders["group"] and orders["label"], "grouped and labelled, so it reads as a menu"


def test_a_field_login_with_no_rep_cannot_see_every_customer(api, tenant, roles):
    """
    The hole this redesign would otherwise have opened. The rep endpoints scope customers by the caller's rep
    record; with none, the fallback was the company's entire customer list. A login holding rep permissions
    must be refused, while office staff reaching the same endpoint keep the company view they are meant to
    have.
    """
    from apps.accounts.models import TenantMembership, User
    from apps.accounts.services import UserService
    from rest_framework.test import APIClient

    from .conftest import seed_basic

    seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        UserService.create_tenant_user(tenant, "Repless Field", email="repless.field@probe.pk",
                                       password="Abcd-1234", roles=[roles["field"]], warehouses=[])
        user = User.objects.get(email="repless.field@probe.pk")
        User.objects.filter(pk=user.pk).update(must_change_password=False)
        # deliberately no rep link, which is the state the guard exists for
        assert TenantMembership.objects.get(user=user, tenant=tenant).supplier_rep_id is None

    phone = APIClient()
    login = phone.post("/api/v1/auth/login", {"identifier": "repless.field@probe.pk", "password": "Abcd-1234"}, format="json")
    assert login.status_code == 200, login.content
    phone.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}", HTTP_X_TENANT_ID=str(tenant.id))

    r = phone.get("/ext/v1/customers")
    assert r.status_code == 403 and r.data["type"] == "rep_required", r.content
    assert "not linked to a rep" in r.data["detail"]


def test_a_user_is_not_created_or_left_with_no_role(api, tenant, roles):
    """
    The owner's words: "until role assign, user should not create or save". A login with no role signs in fine
    and then opens nothing, which the person reads as a broken account rather than as a missing right — and it
    is the state an interrupted "add user" used to leave behind.
    """
    bare = api.post("/api/v1/users", {
        "full_name": "Roleless", "email": "roleless@probe.pk", "password": "Abcd-1234!",
    }, format="json")
    assert bare.status_code == 400, bare.content
    assert "roles" in bare.data["errors"], bare.data
    assert "at least one role" in " ".join(bare.data["errors"]["roles"])

    empty = api.post("/api/v1/users", {
        "full_name": "Roleless", "email": "roleless@probe.pk", "password": "Abcd-1234!", "roles": [],
    }, format="json")
    assert empty.status_code == 400, "an empty list is the same as none"

    made = api.post("/api/v1/users", {
        "full_name": "Has A Role", "email": "has.role@probe.pk", "password": "Abcd-1234!",
        "roles": [str(roles["office"].id)],
    }, format="json")
    assert made.status_code == 201, made.content

    # and the last one cannot be taken away afterwards — suspending the login is how access is removed
    stripped = api.patch(f"/api/v1/users/{made.data['id']}", {"roles": []}, format="json")
    assert stripped.status_code == 400, stripped.content
    assert "roles" in stripped.data["errors"]

    # the owner holds everything by ownership, not by a role, so the rule does not apply to them
    owner_row = next(u for u in api.get("/api/v1/users").data["results"] if u["is_owner"])
    assert api.patch(f"/api/v1/users/{owner_row['id']}", {"roles": []}, format="json").status_code == 200


def test_owner_can_log_in_as_a_user_and_come_back(api, tenant, roles):
    """
    The owner's ask: see the app as one of their users (that user's pages and rights), then return to themselves.
    Reuses the impersonation session the platform-admin flow uses, so the same expiry and audit apply.
    """
    from apps.core.tenancy import tenant_context

    made = api.post("/api/v1/users", {"full_name": "Office Staff", "email": "office.staff@probe.pk",
                                      "password": "Abcd-1234!", "roles": [str(roles["office"].id)]}, format="json")
    assert made.status_code == 201, made.content
    membership_id = made.data["id"]

    # as the owner, pages come back as everything (owner bypass)
    before = api.get("/api/v1/me").data
    assert before["is_owner"] is True and before["impersonating"] is False

    started = api.post(f"/api/v1/users/{membership_id}/login-as", {}, format="json")
    assert started.status_code == 200, started.content

    now = api.get("/api/v1/me").data
    assert now["impersonating"] is True, now
    assert now["impersonating_as"] == "Office Staff"
    assert now["is_owner"] is False, "while acting as a user the owner is not treated as owner"
    # the office role does not grant the users page, so it is gone while acting as that user
    assert now["pages"].get("users", {}).get("view") in (False, None)

    exited = api.post("/api/v1/users/exit-impersonation", {}, format="json")
    assert exited.status_code == 200

    back = api.get("/api/v1/me").data
    assert back["is_owner"] is True and back["impersonating"] is False


def test_owner_cannot_log_in_as_another_owner(api, tenant):
    """Impersonating another owner would be a lateral move with no point and real risk — refused."""
    owner_membership = next(u for u in api.get("/api/v1/users").data["results"] if u["is_owner"])
    r = api.post(f"/api/v1/users/{owner_membership['id']}/login-as", {}, format="json")
    assert r.status_code == 400, r.content


def test_a_platform_admin_viewing_a_tenant_can_also_log_in_as_a_user(api, tenant, roles, admin_user):
    """
    The owner's report: login-as failed. The real owner reaches their tenant through platform-admin
    impersonation, and login-as used to refuse because a session was already active. Now it re-points that
    session at the chosen user, and "back to owner" reverts to the owner without leaving the company.
    """
    from rest_framework.test import APIClient

    from apps.accounts.models import TenantMembership, User
    from apps.core.tenancy import tenant_context
    from apps.platform.services import TenantService

    # a staff user to act as
    made = api.post("/api/v1/users", {"full_name": "Floor Staff", "email": "floor.staff@probe.pk",
                                      "password": "Abcd-1234!", "roles": [str(roles["office"].id)]}, format="json")
    assert made.status_code == 201, made.content
    membership_id = made.data["id"]

    # the platform admin starts impersonating the tenant (as its owner)
    with tenant_context(tenant, tenant.owner):
        TenantService.start_impersonation(admin_user, tenant)

    admin = APIClient()
    r = admin.post("/api/v1/auth/login", {"identifier": "admin-test", "password": "pak1stan"}, format="json")
    admin.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}", HTTP_X_TENANT_ID=str(tenant.id))

    as_owner = admin.get("/api/v1/me").data
    assert as_owner["impersonating"] is True and as_owner["is_owner"] is True
    assert as_owner["impersonating_as"] is None, "viewing as the owner is not a 'logged in as user' state"

    started = admin.post(f"/api/v1/users/{membership_id}/login-as", {}, format="json")
    assert started.status_code == 200, started.content

    now = admin.get("/api/v1/me").data
    assert now["impersonating_as"] == "Floor Staff" and now["is_owner"] is False

    # back to owner: stays in the tenant, reverts to the owner view
    assert admin.post("/api/v1/users/exit-impersonation", {}, format="json").status_code == 200
    back = admin.get("/api/v1/me").data
    assert back["is_owner"] is True and back["impersonating_as"] is None and back["impersonating"] is True
