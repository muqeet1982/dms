"""
Owner, 27 Nov 2026: a Delivery Rider role ("delivery person but better wording ... with best permission") who "will collect
all the order he need to deliver only". The role opens the phone app's delivery mode and nothing else, and a rider sees
only the trips they are on — as delivery person, helper or driver. Office users keep seeing every trip.
"""
import pytest
from rest_framework.test import APIClient

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

RIDER = ["delivery.view_trip", "delivery.start_trip", "delivery.view_delivery_note", "delivery.complete_delivery",
         "delivery.record_delivery_return", "tracking.view_own"]


def _login(tenant, email):
    from apps.accounts.models import User

    User.objects.filter(email=email).update(must_change_password=False)
    c = APIClient()
    r = c.post("/api/v1/auth/login", {"identifier": email, "password": "Rider-9981-x"}, format="json")
    assert r.status_code == 200, r.content
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}", HTTP_X_TENANT_ID=str(tenant.id))
    return c


def _person(tenant, name, email, role):
    from apps.accounts.services import UserService

    with tenant_context(tenant, tenant.owner):
        return UserService.create_tenant_user(tenant, name, email=email, password="Rider-9981-x", roles=[role], warehouses=[]).user


def _trips(tenant, rider):
    from apps.delivery.models import DeliveryTrip
    from .conftest import seed_basic

    wh, *_ = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        mine = DeliveryTrip.objects.create(number="TRP-R1", warehouse=wh, delivery_rep=rider)
        helping = DeliveryTrip.objects.create(number="TRP-R2", warehouse=wh, delivery_rep=tenant.owner, helper=rider)
        other = DeliveryTrip.objects.create(number="TRP-R3", warehouse=wh, delivery_rep=tenant.owner)
    return mine, helping, other


def test_every_company_gets_the_rider_role_with_only_what_the_delivery_app_needs(tenant):
    from apps.accounts.models import GroupProfile

    role = GroupProfile.objects.get(tenant=tenant, name="Delivery Rider")
    assert set(role.permission_codenames) == set(RIDER), role.permission_codenames


def test_a_rider_opens_the_delivery_app_and_sees_only_their_own_trips(tenant):
    from apps.accounts.models import GroupProfile

    role = GroupProfile.objects.get(tenant=tenant, name="Delivery Rider")
    rider = _person(tenant, "Rider One", "rider@alpha.pk", role)
    mine, helping, other = _trips(tenant, rider)
    c = _login(tenant, "rider@alpha.pk")

    pages = c.get("/api/v1/me").data["pages"]
    assert pages["delivery_notes"]["extras"]["complete"] and not pages["sales_orders"]["edit"], "the app's delivery mode"
    assert not pages.get("customers", {}).get("view") and not pages.get("invoices", {}).get("view")

    seen = {t["number"] for t in c.get("/api/v1/trips", {"page_size": 50}).data["results"]}
    assert seen == {"TRP-R1", "TRP-R2"}, seen
    assert c.get(f"/api/v1/trips/{other.id}").status_code == 404
    assert c.get("/api/v1/delivery-notes").status_code == 200
    assert c.get("/api/v1/customers").status_code == 403


def test_an_office_user_without_see_all_still_sees_every_trip(tenant):
    """TDN's Manager holds the trips page without its "see every trip" switch: nothing changes for them."""
    from apps.accounts.services import RoleService

    with tenant_context(tenant, tenant.owner):
        role = RoleService.create_role(tenant, "Office probe", ["delivery.view_trip", "sales.view_sales_order", "sales.change_sales_order", "delivery.complete_delivery"])
    clerk = _person(tenant, "Clerk", "clerk@alpha.pk", role)
    _trips(tenant, clerk)
    c = _login(tenant, "clerk@alpha.pk")
    assert {t["number"] for t in c.get("/api/v1/trips", {"page_size": 50}).data["results"]} >= {"TRP-R1", "TRP-R2", "TRP-R3"}
