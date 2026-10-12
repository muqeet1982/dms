"""
No leaks: for every page that is switched to `enforced`, a role holding exactly one of its switches must be able to
use that action and be refused (403) on all the others.

The test only asks "was it refused?" — a 400/404/422 counts as *reached* (the permission let it through and the
payload or the id was wrong), which is precisely what leak detection needs.
"""
import pytest
from rest_framework.test import APIClient

from apps.accounts.page_permissions import PAGES

pytestmark = pytest.mark.django_db

ENFORCED = [p for p in PAGES if p["enforced"]]


def _client(tenant, codenames, label=""):
    """
    One probe user for the whole module, whose role is re-pointed at `codenames` for each case. A JWT only carries
    the user id, so permissions are read from the role on every request — no second login needed, and the suite
    stays quick enough to run over every page.
    """
    from apps.accounts.models import User
    from apps.accounts.services import RoleService, UserService, _perm_objects

    state = _PROBE.setdefault(str(tenant.id), {})
    if "client" not in state:
        role = RoleService.create_role(tenant, "Matrix probe", [], role_type="internal", description="permission matrix test")
        UserService.create_tenant_user(tenant, "Matrix Probe", email="matrix@probe.pk", password="Probe-9981-x", roles=[role], warehouses=[])
        User.objects.filter(email="matrix@probe.pk").update(must_change_password=False)
        c = APIClient()
        r = c.post("/api/v1/auth/login", {"identifier": "matrix@probe.pk", "password": "Probe-9981-x"}, format="json")
        assert r.status_code == 200, r.content
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}", HTTP_X_TENANT_ID=str(tenant.id))
        state["client"], state["role"] = c, role
    state["role"].group.permissions.set(_perm_objects(codenames))
    return state["client"]


_PROBE: dict = {}


def _call(client, action, endpoint):
    if action == "view":
        return client.get(endpoint)
    if action == "add":
        return client.post(endpoint, {}, format="json")
    if action == "edit":
        return client.patch(f"{endpoint}/00000000-0000-0000-0000-000000000000", {}, format="json")
    return client.delete(f"{endpoint}/00000000-0000-0000-0000-000000000000")


@pytest.mark.parametrize("pg", ENFORCED, ids=[p["key"] for p in ENFORCED])
def test_one_switch_at_a_time(pg, tenant, settings):
    """Granting one switch must not hand over any of the others."""
    from apps.core.tenancy import tenant_context

    for action, codename in pg["actions"].items():
        if action in pg["skip_probe"]:
            continue
        with tenant_context(tenant, tenant.owner):
            client = _client(tenant, [codename])
        granted = _call(client, action, pg["endpoint"])
        assert granted.status_code != 403, f"{pg['key']}: {action} was granted but {pg['endpoint']} still refused it"
        for other, _ in pg["actions"].items():
            if other == action or other in pg["skip_probe"]:
                continue
            blocked = _call(client, other, pg["endpoint"])
            assert blocked.status_code == 403, (
                f"LEAK on {pg['key']}: a role with only '{action}' could {other} "
                f"({pg['endpoint']} answered {blocked.status_code})"
            )


@pytest.mark.parametrize("pg", ENFORCED, ids=[p["key"] for p in ENFORCED])
def test_no_switch_means_no_access(pg, tenant):
    """A role with nothing on cannot even read the page's data."""
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        client = _client(tenant, [])
    for action in pg["actions"]:
        if action in pg["skip_probe"]:
            continue
        r = _call(client, action, pg["endpoint"])
        assert r.status_code == 403, f"LEAK on {pg['key']}: {action} worked with no permissions ({r.status_code})"


def test_me_reports_each_page(api, tenant):
    """The owner sees every switch on; the UI gates on exactly this."""
    me = api.get("/api/v1/me")
    assert me.status_code == 200
    pages = me.data["pages"]
    assert len(pages) == len(PAGES)
    assert pages["users"] == {"view": True, "add": True, "edit": True, "delete": True, "extras": {"reset_password": True, "manage_access": True}}


def test_me_reports_a_view_only_role(tenant):
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        client = _client(tenant, ["users.view_user"])
    pages = client.get("/api/v1/me").data["pages"]
    assert pages["users"] == {"view": True, "add": False, "edit": False, "delete": False, "extras": {"reset_password": False, "manage_access": False}}
    assert pages["customers"]["view"] is False


def test_an_old_role_keeps_working(tenant):
    """A role saved before the matrix holds `users.manage_roles`; it must still cover the Roles page."""
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        client = _client(tenant, ["users.manage_roles"])
    pages = client.get("/api/v1/me").data["pages"]
    assert pages["roles"] == {"view": True, "add": True, "edit": True, "delete": True, "extras": {}}
    assert client.get("/api/v1/roles").status_code == 200


def test_roles_and_access_is_its_own_switch(tenant):
    """Being allowed to add or edit users must not let someone hand out roles — that is privilege escalation."""
    from apps.core.tenancy import tenant_context

    def as_role(codenames):
        with tenant_context(tenant, tenant.owner):
            return _client(tenant, codenames)

    with tenant_context(tenant, tenant.owner):
        from apps.accounts.models import GroupProfile

        picks = list(GroupProfile.objects.filter(tenant=tenant, is_system=False).exclude(name="Matrix probe").values_list("pk", flat=True)[:2])
        role_id, other_role_id = picks[0], picks[1]
    client = as_role(["users.view_user", "users.add_user", "users.change_user"])

    # A login has to have a role (see TenantUserSerializer.validate), so somebody who may add users but not
    # hand out access cannot create one at all — they would be making an account that opens nothing.
    no_roles = client.post("/api/v1/users", {"full_name": "Plain", "email": "plain@probe.pk", "password": "Abcd-1234"}, format="json")
    assert no_roles.status_code == 400 and "roles" in no_roles.data["errors"], no_roles.content
    with_roles = client.post("/api/v1/users", {"full_name": "Boosted", "email": "boosted@probe.pk", "password": "Abcd-1234", "roles": [role_id]}, format="json")
    assert with_roles.status_code == 403 and with_roles.data["type"] == "forbidden", "and handing out a role is still refused"

    # Somebody who may hand out access makes the user. (`as_role` re-points one probe role, so the client above
    # and this one are the same login — the order matters: whatever was granted last is what it holds.)
    full = as_role(["users.view_user", "users.add_user", "users.change_user", "users.change_user_access"])
    plain = full.post("/api/v1/users", {"full_name": "Plain", "email": "plain@probe.pk", "password": "Abcd-1234", "roles": [role_id]}, format="json")
    assert plain.status_code == 201, plain.content
    assert full.patch(f"/api/v1/users/{plain.data['id']}", {"roles": [other_role_id]}, format="json").status_code == 200

    # the escalation this test is really about: with the access switch taken away, an existing user's roles are
    # no longer theirs to change, though the rest of the user still is
    client = as_role(["users.view_user", "users.add_user", "users.change_user"])
    patched = client.patch(f"/api/v1/users/{plain.data['id']}", {"roles": [role_id]}, format="json")
    assert patched.status_code == 403, patched.content
    assert client.patch(f"/api/v1/users/{plain.data['id']}", {"full_name": "Plain Renamed"}, format="json").status_code == 200


def test_a_rep_role_can_be_saved_unchanged(api, tenant):
    """
    Open the rep ("external") role, change nothing, press Save: it has to save. A rep's role is not only
    `external.*` — checking in at a shop and seeing their own route are the shared visits/tracking permissions and
    the seeded Manufacturer Rep is created holding them — so a validator that demanded the prefix refused the very
    role the product had made, and the rep role page could not be saved at all.
    """
    rep_role = {r["name"]: r for r in api.get("/api/v1/roles", {"role_type": "external"}).data}["Manufacturer Rep"]
    held = rep_role["permissions"]
    assert {"tracking.view_own", "visits.record", "visits.view_own"} <= set(held)

    body = {"name": rep_role["name"], "description": rep_role["description"], "role_type": "external",
            "permissions": rep_role["permissions_effective"], "approval_limits": {}}
    saved = api.patch(f"/api/v1/roles/{rep_role['id']}", body, format="json")
    assert saved.status_code == 200, saved.content
    assert sorted(saved.data["permissions"]) == sorted(held), "an unchanged save must not move the switches"

    # the editor draws its switches from the catalogue, so one the role holds but the catalogue leaves out is a
    # permission the owner can neither see nor keep
    offered = {p["codename"] for mod in api.get("/api/v1/permissions-catalog", {"role_type": "external"}).data for p in mod["permissions"]}
    assert set(held) <= offered

    # an office switch on a rep role would let one rep watch all the others, so it is still refused — by name
    refused = api.patch(f"/api/v1/roles/{rep_role['id']}", dict(body, permissions=[*held, "tracking.view_team"]), format="json")
    assert refused.status_code == 400
    assert "tracking.view_team" in str(refused.data["errors"]["permissions"])
    assert not {"visits.view_team", "tracking.manage_policy", "customers.view_customer"} & offered


def test_saving_a_rep_role_unchanged_keeps_the_reps_signed_in(api, tenant):
    """
    A permission change forces a re-login (§2.5); pressing Save on a role nobody edited must not, or every rep
    holding that role is thrown out of the mobile app in the middle of their round.
    """
    from rest_framework.test import APIClient

    from apps.accounts.models import User, UserSession

    supplier = api.post("/api/v1/suppliers", {"name": "Colormax"}, format="json")
    assert supplier.status_code == 201, supplier.content
    rep_role = {r["name"]: r for r in api.get("/api/v1/roles", {"role_type": "external"}).data}["Manufacturer Rep"]
    rep = api.post(f"/api/v1/suppliers/{supplier.data['id']}/reps",
                   {"name": "Imran Rep", "email": "imran@colormax.pk", "role": rep_role["id"], "create_login": True, "password": "Rep-9981-x"}, format="json")
    assert rep.status_code == 201 and rep.data["has_login"], rep.content
    User.objects.filter(email="imran@colormax.pk").update(must_change_password=False)

    phone = APIClient()
    assert phone.post("/api/v1/auth/login", {"identifier": "imran@colormax.pk", "password": "Rep-9981-x"}, format="json").status_code == 200
    signed_in = UserSession.objects.filter(user__email="imran@colormax.pk")
    assert signed_in.filter(revoked_at__isnull=True).exists()

    body = {"name": rep_role["name"], "description": rep_role["description"], "role_type": "external",
            "permissions": rep_role["permissions"], "approval_limits": {}}
    assert api.patch(f"/api/v1/roles/{rep_role['id']}", body, format="json").status_code == 200
    assert not signed_in.filter(revoked_at__isnull=False).exists(), "nothing changed, so nobody should be logged out"

    # taking the check-in switch away is a real change, and then the rep does have to sign in again
    trimmed = [p for p in rep_role["permissions"] if p != "visits.record"]
    assert api.patch(f"/api/v1/roles/{rep_role['id']}", dict(body, permissions=trimmed), format="json").status_code == 200
    assert signed_in.filter(revoked_at__isnull=False).exists()


def test_invoicing_and_cancelling_live_on_the_order(api, tenant, order_with_stock_for_perms):
    """The Invoices page's Add and Delete are reached through the order/invoice actions, so probe them there."""
    from apps.core.tenancy import tenant_context

    order, _wh, _variant = order_with_stock_for_perms

    def as_role(codenames):
        with tenant_context(tenant, tenant.owner):
            return _client(tenant, codenames)

    view_only = ["sales.view_sales_order", "sales.view_invoice"]
    assert as_role(view_only).post(f"/api/v1/sales-orders/{order.pk}/invoice", {}, format="json").status_code == 403
    # allowed through the switch; 422 because nothing has been issued yet, which means it reached the service
    assert as_role([*view_only, "sales.add_invoice"]).post(f"/api/v1/sales-orders/{order.pk}/invoice", {}, format="json").status_code == 422

    cancel_url = "/api/v1/invoices/00000000-0000-0000-0000-000000000000/cancel"
    assert as_role(view_only).post(cancel_url, {}, format="json").status_code == 403
    assert as_role(["sales.view_invoice", "sales.delete_invoice"]).post(cancel_url, {}, format="json").status_code == 404


@pytest.fixture
def order_with_stock_for_perms(api, tenant):
    from apps.core.tenancy import tenant_context

    from .conftest import seed_basic

    wh, _sup, variant, customer = seed_basic(tenant)
    r = api.post("/api/v1/sales-orders", {"customer": str(customer.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(variant.id), "qty": "2"}]}, format="json")
    assert r.status_code == 201, r.content
    with tenant_context(tenant, tenant.owner):
        from apps.sales.models import SalesOrder

        return SalesOrder.objects.get(pk=r.data["id"]), wh, variant


def test_an_email_or_mobile_belongs_to_one_person(api, tenant):
    """Logins are by email or mobile, so a second account on the same one is refused — case and spacing ignored."""
    from apps.accounts.models import GroupProfile
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        role = [GroupProfile.objects.filter(tenant=tenant, role_type="internal").first().pk]  # a login needs one
    first = api.post("/api/v1/users", {"full_name": "Asim", "email": "asim@probe.pk", "phone": "+923009998877", "password": "Abcd-1234", "roles": role}, format="json")
    assert first.status_code == 201, first.content

    same = api.post("/api/v1/users", {"full_name": "Asim Again", "email": "asim@probe.pk", "password": "Abcd-1234", "roles": role}, format="json")
    assert same.status_code == 400 and same.data["type"] == "duplicate" and "Asim" in same.data["detail"]

    other_case = api.post("/api/v1/users", {"full_name": "Asim Caps", "email": " ASIM@Probe.pk ", "password": "Abcd-1234", "roles": role}, format="json")
    assert other_case.status_code == 400 and other_case.data["type"] == "duplicate"

    same_mobile = api.post("/api/v1/users", {"full_name": "Asim Phone", "phone": "+923009998877", "password": "Abcd-1234", "roles": role}, format="json")
    assert same_mobile.status_code == 400 and "mobile" in same_mobile.data["detail"]

    # and editing someone must not take an address that is already in use
    second = api.post("/api/v1/users", {"full_name": "Bilal", "email": "bilal@probe.pk", "password": "Abcd-1234", "roles": role}, format="json")
    assert second.status_code == 201, second.content
    steal = api.patch(f"/api/v1/users/{second.data['id']}", {"email": "asim@probe.pk"}, format="json")
    assert steal.status_code == 400 and steal.data["type"] == "duplicate"


def test_one_email_per_customer_and_supplier(api, tenant):
    a = api.post("/api/v1/customers", {"name": "Shop A", "email": "shop@probe.pk"}, format="json")
    assert a.status_code == 201, a.content
    b = api.post("/api/v1/customers", {"name": "Shop B", "email": "SHOP@probe.pk"}, format="json")
    assert b.status_code == 400 and "already used by" in str(b.data["errors"]["email"])
    # the same address on a supplier is a different book, so it is allowed
    assert api.post("/api/v1/suppliers", {"name": "Maker Two", "email": "shop@probe.pk"}, format="json").status_code == 201
    # editing a customer onto a taken address is refused too
    c = api.post("/api/v1/customers", {"name": "Shop C", "email": "shopc@probe.pk"}, format="json")
    assert c.status_code == 201
    assert api.patch(f"/api/v1/customers/{c.data['id']}", {"email": "shop@probe.pk"}, format="json").status_code == 400


def test_a_documents_files_follow_the_document(api, tenant):
    """Attachments and the history tab are part of a document, so they need that page's switches."""
    from apps.core.tenancy import tenant_context

    def as_role(codenames):
        with tenant_context(tenant, tenant.owner):
            return _client(tenant, codenames)

    stranger = as_role(["customers.view_customer"])
    assert stranger.get("/api/v1/attachments", {"model": "SalesOrder", "object_id": "x"}).status_code == 403
    assert stranger.get("/api/v1/history/SalesOrder/00000000-0000-0000-0000-000000000000").status_code == 403

    reader = as_role(["sales.view_sales_order"])
    assert reader.get("/api/v1/attachments", {"model": "SalesOrder", "object_id": "x"}).status_code == 200
    assert reader.get("/api/v1/history/SalesOrder/00000000-0000-0000-0000-000000000000").status_code == 200
    # reading a document does not mean you may attach to it
    assert reader.post("/api/v1/attachments", {"model": "SalesOrder", "object_id": "x"}, format="multipart").status_code == 403

    editor = as_role(["sales.view_sales_order", "sales.change_sales_order"])
    # 400 = the permission let it through and the upload itself was empty
    assert editor.post("/api/v1/attachments", {"model": "SalesOrder", "object_id": "x"}, format="multipart").status_code == 400
    assert as_role([]).get("/api/v1/attachments", {"model": "Nonsense", "object_id": "x"}).status_code == 400


def test_saving_a_role_that_still_holds_the_old_codenames_signs_nobody_out(api, tenant):
    """
    The editor shows a role's *expanded* rights, so pressing Save sends the expansion back. Comparing that
    against the coarse codenames still stored made every untouched save look like a change — and every
    internal role on a real company differs that way, so one harmless click signed every holder out of every
    device, the mobile app included. What counts is whether the role grants anything different.
    """
    from apps.accounts.models import GroupProfile, UserSession
    from apps.accounts.page_permissions import expand
    from apps.accounts.services import RoleService, UserService
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        # a coarse codename that expands to several page switches
        role = RoleService.create_role(tenant, "Legacy Shape", ["customers.manage_territories"])
        raw = set(role.permission_codenames)
        effective = set(expand(raw))
        assert effective > raw, "this test needs a role whose stored set is smaller than what it grants"

        UserService.create_tenant_user(tenant, "Holder", email="holder@probe.pk", password="Abcd-1234",
                                       roles=[role], warehouses=[])
        from apps.accounts.models import User

        holder = User.objects.get(email="holder@probe.pk")
        UserSession.objects.create(user=holder, family="probe-family", refresh_jti="probe-jti", device="probe")
        live = lambda: UserSession.objects.filter(user=holder, revoked_at__isnull=True).count()
        assert live() == 1

        # the editor hands back the expansion — the shape changes, the rights do not
        RoleService.update_role(role, permissions=sorted(effective))
        assert live() == 1, "nothing was granted or taken away, so nobody should have been signed out"

        # and a real change still signs them out
        RoleService.update_role(role, permissions=sorted(effective | {"catalog.view_product"}))
        assert live() == 0, "a genuine change must still force a fresh sign-in"


def test_turning_one_right_off_stays_off(api, tenant):
    """
    Unticking Delete and saving has to mean Delete is gone — the thing the owner reported three times.

    The editor is seeded from what a role *effectively* grants, so a role still holding an old coarse codename
    (`customers.manage_territories`, nine page switches in one) sent that codename back along with the eight
    switches left ticked. The ninth was then handed straight back by the expansion, and every attempt to take a
    right away looked like a save that did nothing.
    """
    from apps.accounts.models import GroupProfile
    from apps.accounts.services import RoleService, UserService
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        role = RoleService.create_role(tenant, "Territory clerk", ["customers.manage_territories"])

    shown = api.get(f"/api/v1/roles/{role.id}")
    assert shown.status_code == 200, shown.content
    effective = shown.data["permissions_effective"]
    assert "customers.delete_territory" in effective and "customers.view_territory" in effective

    kept = [p for p in effective if p != "customers.delete_territory"]
    saved = api.patch(f"/api/v1/roles/{role.id}", {"permissions": kept}, format="json")
    assert saved.status_code == 200, saved.content

    again = api.get(f"/api/v1/roles/{role.id}")
    assert "customers.delete_territory" not in again.data["permissions_effective"], "the untick was undone again"
    assert "customers.view_territory" in again.data["permissions_effective"], "the rest of the page was kept"
    assert "customers.manage_territories" not in again.data["permissions"], "the coarse codename had to go"

    # and it is gone for the person holding the role, which is the only place it matters
    with tenant_context(tenant, tenant.owner):
        UserService.create_tenant_user(tenant, "Clerk", email="clerk@probe.pk", password="Abcd-1234",
                                       roles=[GroupProfile.objects.get(pk=role.pk)], warehouses=[])
        from apps.accounts.models import TenantMembership, User

        m = TenantMembership.objects.get(user=User.objects.get(email="clerk@probe.pk"), tenant=tenant)
        rights = m.permission_codenames()
        assert "customers.delete_territory" not in rights and "customers.view_territory" in rights


def test_a_role_given_only_the_old_codename_keeps_working(api, tenant):
    """
    Dropping a coarse codename is only right when the sender enumerated the switches and left some out. A caller
    that sends the coarse codename alone — role seeding, an older API client — means all of it, and must not be
    quietly stripped down to nothing.
    """
    from apps.accounts.page_permissions import expand
    from apps.accounts.services import RoleService
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        role = RoleService.create_role(tenant, "Old client role", ["customers.manage_territories"])
        assert "customers.delete_territory" in expand(role.permission_codenames)
        RoleService.update_role(role, permissions=["customers.manage_territories", "catalog.view_product"])
        rights = expand(role.permission_codenames)
        assert "customers.delete_territory" in rights and "customers.view_territory" in rights
