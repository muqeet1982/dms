"""A shop without a pin: saved with location_later, pinned by the rep at the first visit, and the office
sees how every pin was set."""
from decimal import Decimal

import pytest

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

GULBERG = (31.5204, 74.3487)


@pytest.fixture
def rep_app(api, tenant):
    from apps.accounts.models import TenantMembership, User
    from apps.accounts.services import RoleService, UserService
    from apps.partners.models import SupplierRep
    from rest_framework.test import APIClient

    from .conftest import seed_basic

    wh, supplier, variant, customer = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        rep = SupplierRep.objects.create(supplier=supplier, code="R-PIN", name="Pin Rep")
        customer.assigned_reps.add(rep)
        role = RoleService.create_role(tenant, "Pinner", ["external.view_assigned_customers", "external.submit_order",
                                                          "external.view_own_orders", "external.add_customer_request"],
                                       role_type="external")
        UserService.create_tenant_user(tenant, "Pin Rep", email="pin@probe.pk", password="Abcd-1234", roles=[role], warehouses=[])
        user = User.objects.get(email="pin@probe.pk")
        User.objects.filter(pk=user.pk).update(must_change_password=False)
        rep.user, rep.role = user, role
        rep.save(update_fields=["user", "role"])
        TenantMembership.objects.filter(user=user, tenant=tenant).update(supplier_rep=rep)

    phone = APIClient()
    login = phone.post("/api/v1/auth/login", {"identifier": "pin@probe.pk", "password": "Abcd-1234"}, format="json")
    assert login.status_code == 200, login.content
    phone.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}", HTTP_X_TENANT_ID=str(tenant.id))
    return {"tenant": tenant, "rep": rep, "phone": phone}


def test_a_shop_saves_without_a_location_and_gets_pinned_at_first_visit(rep_app, tenant):
    phone = rep_app["phone"]
    r = phone.post("/ext/v1/customers", {"name": "Pinless Pan Shop", "cnic": "35202-7654321-1",
                                         "location_later": True,
                                         "rep_gps": {"lat": GULBERG[0], "lng": GULBERG[1], "accuracy_m": 9}}, format="json")
    assert r.status_code == 201, r.content
    assert r.data["location_later"] is True
    cid = r.data["id"]

    # first visit: "Save where I am"
    pin = phone.post(f"/ext/v1/customers/{cid}/location",
                     {"gps": {"lat": GULBERG[0], "lng": GULBERG[1], "accuracy_m": 7}, "pin_source": "gps"},
                     format="json", headers={"Idempotency-Key": "pin-once"})
    assert pin.status_code == 201, pin.content
    assert pin.data["pin_source"] == "gps" and pin.data["gps"]["lat"] == pytest.approx(GULBERG[0])

    # an offline replay answers with the same body, not a refusal
    again = phone.post(f"/ext/v1/customers/{cid}/location",
                       {"gps": {"lat": 0, "lng": 0}, "pin_source": "gps"},
                       format="json", headers={"Idempotency-Key": "pin-once"})
    assert again.status_code == 200 and again.data["gps"]["lat"] == pytest.approx(GULBERG[0])

    # once a pin exists, moving it is the office's job
    moved = phone.post(f"/ext/v1/customers/{cid}/location",
                       {"gps": {"lat": 31.6, "lng": 74.4}}, format="json", headers={"Idempotency-Key": "pin-twice"})
    assert moved.status_code == 409 and moved.data["type"] == "pin_exists"


def test_pin_set_from_afar_is_kept_and_measured(rep_app, tenant, api):
    phone = rep_app["phone"]
    r = phone.post("/ext/v1/customers", {"name": "Far Pin Store", "cnic": "35202-1111111-1",
                                         "gps": {"lat": 31.55, "lng": 74.40}, "pin_source": "map",
                                         "rep_gps": {"lat": GULBERG[0], "lng": GULBERG[1], "accuracy_m": 12}}, format="json")
    assert r.status_code == 201, r.content
    assert r.data["pin_source"] == "map"
    office = api.get(f"/api/v1/customers/{r.data['id']}")
    assert office.data["pin_source"] == "map"
    assert float(office.data["pin_rep_distance_m"]) > 1000  # placed kilometres from where the rep stood
