"""
Working areas: the geometry, who falls inside, and the rule that a rep's work has to happen inside the area
they cover.
"""
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

# Lahore, around Gulberg — the distances below are real ones on the ground
GULBERG = (31.5204, 74.3587)
JOHAR_TOWN = (31.4697, 74.2728)  # ~11 km away
KARACHI = (24.8607, 67.0011)  # ~1,000 km away


@pytest.fixture
def field(api, tenant):
    """A rep, a customer in Gulberg and another in Johar Town, and a 5 km circle over Gulberg."""
    from .conftest import seed_basic

    wh, supplier, variant, customer = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        from apps.partners.models import Customer, GeoFence, GeoFenceRep, SupplierRep

        rep = SupplierRep.objects.create(supplier=supplier, code="R-1", name="Area Rep")
        customer.gps_lat, customer.gps_lng = Decimal(str(GULBERG[0])), Decimal(str(GULBERG[1]))
        customer.save(update_fields=["gps_lat", "gps_lng"])
        customer.assigned_reps.add(rep)
        far = Customer.objects.create(code="C-FAR", name="Far Shop", gps_lat=Decimal(str(JOHAR_TOWN[0])), gps_lng=Decimal(str(JOHAR_TOWN[1])))
        far.assigned_reps.add(rep)
        fence = GeoFence.objects.create(name="Gulberg 5 km", type="circle", centre_lat=Decimal(str(GULBERG[0])), centre_lng=Decimal(str(GULBERG[1])), radius_m=5000)
        from apps.partners.geofence import GeoFenceService

        GeoFenceService.normalise(fence)
        fence.save()
        GeoFenceRep.objects.create(fence=fence, rep=rep)
    return {"rep": rep, "customer": customer, "far": far, "fence": fence, "tenant": tenant, "supplier": supplier,
            "variant": variant, "warehouse": wh}


def test_a_circle_knows_what_is_inside_it(field):
    from apps.partners.geofence import contains, distance_to

    fence = field["fence"]
    assert contains(fence, GULBERG) and distance_to(fence, GULBERG) == 0
    assert not contains(fence, JOHAR_TOWN)
    out = distance_to(fence, JOHAR_TOWN)
    assert 4_500 < out < 5_500, f"Johar Town is ~9.9 km away, so ~4.9 km outside a 5 km circle (got {out})"


def test_a_drawn_area_knows_what_is_inside_it(tenant):
    """A square over Gulberg: the middle is in, a corner-adjacent point is out, and the edge distance is honest."""
    from apps.partners.geofence import GeoFenceService, area_km2, contains, distance_to

    with tenant_context(tenant, tenant.owner):
        from apps.partners.models import GeoFence

        box = [{"lat": 31.50, "lng": 74.34}, {"lat": 31.54, "lng": 74.34}, {"lat": 31.54, "lng": 74.38}, {"lat": 31.50, "lng": 74.38}]
        fence = GeoFence.objects.create(name="Drawn box", type="polygon", points=box)
        GeoFenceService.normalise(fence)
        fence.save()
        assert contains(fence, (31.52, 74.36))
        assert not contains(fence, (31.56, 74.36))
        assert 2_000 < distance_to(fence, (31.56, 74.36)) < 2_600, "2.2 km north of the top edge"
        assert 15 < area_km2(fence) < 22, f"roughly 4.4 km x 3.8 km (got {area_km2(fence)})"
        assert fence.min_lat is not None and float(fence.max_lat) == 31.54, "the bounding box is kept for pre-filtering"
        assert float(fence.centre_lat) == pytest.approx(31.52, abs=0.01), "and a centre, so the map can fly to it"


def test_a_traced_outline_is_thinned(tenant):
    """An OSM district can carry thousands of points; a delivery area is just as correct with a couple of hundred."""
    from apps.partners.geofence import GeoFenceService, contains

    with tenant_context(tenant, tenant.owner):
        from apps.partners.models import GeoFence

        ring = [{"lat": 31.52 + 0.05 * __import__("math").cos(i / 500 * 6.283), "lng": 74.35 + 0.05 * __import__("math").sin(i / 500 * 6.283)} for i in range(500)]
        fence = GeoFence.objects.create(name="Traced", type="polygon", points=ring)
        GeoFenceService.normalise(fence)
        fence.save()
        assert len(fence.points) <= 200
        assert contains(fence, (31.52, 74.35)), "and the shape still covers what it covered"


def test_customers_inside_an_area(field):
    from apps.partners.geofence import GeoFenceService

    with tenant_context(field["tenant"], field["tenant"].owner):
        inside = GeoFenceService.customers_in(field["fence"])
        assert [c.name for c in inside] == [field["customer"].name], "only the Gulberg shop is in the 5 km circle"


def _collect(api, field, gps, amount="500"):
    body = {"rep": str(field["rep"].id), "customer": str(field["customer"].id), "amount": amount, "method": "cash"}
    if gps is not None:
        body["gps"] = {"lat": gps[0], "lng": gps[1]}
    return api.post("/api/v1/rep-collections", body, format="json")


def test_a_rep_outside_the_area_is_refused(api, field):
    """
    The policy is `block`: cash cannot be recorded a thousand kilometres from the area the rep covers — and the
    refusal is kept, which is the whole point of blocking.
    """
    from apps.partners.models import GeoFenceEvent

    assert _collect(api, field, GULBERG).status_code == 201, "inside the area it goes through"
    refused = _collect(api, field, KARACHI)
    assert refused.status_code == 403 and refused.data["type"] == "outside_area", refused.content
    assert "outside Gulberg 5 km" in refused.data["detail"]
    with tenant_context(field["tenant"], field["tenant"].owner):
        assert GeoFenceEvent.objects.filter(verdict="inside", blocked=False).count() == 1, "the one inside is recorded"
        event = GeoFenceEvent.objects.filter(verdict="outside").first()
        assert event is not None and event.blocked, "and so is the refusal"
        assert event.distance_m > 900_000 and event.fence.name == "Gulberg 5 km"
        from apps.sales.models import RepCollection

        assert RepCollection.objects.count() == 1, "and no cash was recorded for the refused one"


def test_no_location_is_refused_by_default(api, field):
    """
    Shipped behaviour: a fenced rep has to say where they are. Otherwise turning the location off would be the
    easy way around the area rule.
    """
    refused = _collect(api, field, None)
    assert refused.status_code == 403 and refused.data["type"] == "location_required", refused.content


def test_a_company_can_allow_work_without_a_fix(api, field):
    """`geofence_require_location: false` is the transition setting for apps that do not send GPS yet — the gap
    is still recorded, so it cannot be used quietly."""
    from apps.partners.models import GeoFenceEvent

    t = field["tenant"]
    t.settings = {**(t.settings or {}), "geofence_require_location": False}
    t.save(update_fields=["settings"])
    assert _collect(api, field, None).status_code == 201
    with tenant_context(field["tenant"], field["tenant"].owner):
        assert GeoFenceEvent.objects.filter(verdict="no_fix", blocked=False).exists()


def test_a_rep_with_no_area_works_anywhere(api, field):
    """Handing someone an area is what switches checks on — nobody is restricted by default."""
    from apps.partners.models import GeoFenceRep

    with tenant_context(field["tenant"], field["tenant"].owner):
        GeoFenceRep.objects.all().delete()
    assert _collect(api, field, None).status_code == 201
    assert _collect(api, field, KARACHI).status_code == 201


def test_a_paused_assignment_lets_a_rep_travel(api, field):
    from django.utils import timezone

    from apps.partners.models import GeoFenceRep

    with tenant_context(field["tenant"], field["tenant"].owner):
        GeoFenceRep.objects.update(paused_until=timezone.localdate() + timezone.timedelta(days=2))
    assert _collect(api, field, KARACHI).status_code == 201


def test_warn_records_without_refusing(api, field):
    from apps.partners.models import GeoFenceEvent

    t = field["tenant"]
    t.settings = {**(t.settings or {}), "geofence_policy": "warn"}
    t.save(update_fields=["settings"])
    assert _collect(api, field, KARACHI).status_code == 201
    with tenant_context(t, t.owner):
        assert GeoFenceEvent.objects.filter(verdict="outside", blocked=False).exists(), "allowed, but on the report"


def test_the_check_itself_can_be_called_from_anywhere(field):
    """Code that is not an endpoint still gets the rule, as an exception it has to handle."""
    from apps.core.exceptions import DomainError
    from apps.partners.geofence import GeoFenceService

    with tenant_context(field["tenant"], field["tenant"].owner):
        assert GeoFenceService.check(rep=field["rep"], gps={"lat": GULBERG[0], "lng": GULBERG[1]}, action="check").verdict == "inside"
        with pytest.raises(DomainError) as refused:
            GeoFenceService.check(rep=field["rep"], gps={"lat": KARACHI[0], "lng": KARACHI[1]}, action="check")
        assert refused.value.get_codes() == "outside_area"


def test_the_api_draws_assigns_and_lists_who_is_inside(api, field):
    """The screens' round trip: create an area, hand it to a rep, ask who is inside, and locate a point."""
    body = {"name": "Johar Town 3 km", "type": "circle", "centre_lat": str(JOHAR_TOWN[0]), "centre_lng": str(JOHAR_TOWN[1]),
            "radius_m": 3000, "reps": [str(field["rep"].id)]}
    r = api.post("/api/v1/geofences", body, format="json")
    assert r.status_code == 201, r.content
    assert r.data["area_km2"] == pytest.approx(28.27, abs=0.1) and r.data["customers_count"] == 1
    assert [a["rep_name"] for a in r.data["assignments"]] == ["Area Rep"]

    inside = api.get(f"/api/v1/geofences/{r.data['id']}/customers")
    assert [c["name"] for c in inside.data["results"]] == ["Far Shop"]

    where = api.get("/api/v1/geofences/locate", {"rep": str(field["rep"].id), "lat": JOHAR_TOWN[0], "lng": JOHAR_TOWN[1]})
    assert where.data["inside"] is True and where.data["areas"] == 2

    out = api.get("/api/v1/geofences/locate", {"rep": str(field["rep"].id), "lat": KARACHI[0], "lng": KARACHI[1]})
    assert out.data["inside"] is False and out.data["distance_m"] > 900_000 and out.data["fence"]["name"]

    # the coverage map answers in one call
    m = api.get("/api/v1/geofences/map")
    assert m.status_code == 200 and len(m.data["fences"]) == 2 and m.data["uncovered"] == 0

    # and unassigning is a write to the same field
    api.post(f"/api/v1/geofences/{r.data['id']}/assign", {"reps": []}, format="json")
    assert api.get(f"/api/v1/geofences/{r.data['id']}").data["reps_count"] == 0


def test_an_area_needs_a_usable_shape(api, tenant):
    bad_circle = api.post("/api/v1/geofences", {"name": "No pin", "type": "circle"}, format="json")
    assert bad_circle.status_code == 400 and "centre_lat" in bad_circle.data["errors"]
    bad_poly = api.post("/api/v1/geofences", {"name": "Two corners", "type": "polygon", "points": [{"lat": 31.5, "lng": 74.3}, {"lat": 31.6, "lng": 74.4}]}, format="json")
    assert bad_poly.status_code == 400 and "points" in bad_poly.data["errors"]


def test_the_rep_app_can_read_its_own_areas(api, field):
    """`/ext/v1/my-areas` is what a phone draws and checks against when it has no signal."""
    from apps.accounts.models import User
    from apps.accounts.services import RoleService, UserService

    t = field["tenant"]
    with tenant_context(t, t.owner):
        role = RoleService.create_role(t, "Field rep", ["external.view_own_orders", "external.submit_order"], role_type="external")
        UserService.create_tenant_user(t, "Area Rep", email="arearep@probe.pk", password="Abcd-1234", roles=[role], warehouses=[])
        user = User.objects.get(email="arearep@probe.pk")
        User.objects.filter(pk=user.pk).update(must_change_password=False)
        field["rep"].user = user
        field["rep"].role = role
        field["rep"].save(update_fields=["user", "role"])
        # the rep portal resolves the rep from the membership, the way a real rep login is wired
        from apps.accounts.models import TenantMembership

        TenantMembership.objects.filter(user=user, tenant=t).update(supplier_rep=field["rep"])

    from rest_framework.test import APIClient

    c = APIClient()
    login = c.post("/api/v1/auth/login", {"identifier": "arearep@probe.pk", "password": "Abcd-1234"}, format="json")
    assert login.status_code == 200, login.content
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}", HTTP_X_TENANT_ID=str(t.id))
    r = c.get("/ext/v1/my-areas")
    assert r.status_code == 200, r.content
    assert r.data["policy"] == "block" and r.data["location_required"] is True
    assert [a["name"] for a in r.data["areas"]] == ["Gulberg 5 km"]
    assert r.data["areas"][0]["radius_m"] == 5000 and r.data["areas"][0]["centre"]["lat"] == pytest.approx(31.5204)


def test_stores_on_the_map_and_moving_a_pin(api, field):
    """The store-locations screen: every shop as a pin, the ones with no pin listed separately, and a pin move."""
    from apps.partners.models import Customer

    m = api.get("/api/v1/customers/map")
    assert m.status_code == 200, m.content
    names = {s["name"] for s in m.data["stores"]}
    assert {field["customer"].name, field["far"].name} <= names
    gulberg = next(s for s in m.data["stores"] if s["name"] == field["customer"].name)
    assert gulberg["fences"] == [str(field["fence"].id)], "it says which areas cover each store"
    assert gulberg["reps"][0]["name"] == "Area Rep"

    with tenant_context(field["tenant"], field["tenant"].owner):
        blind = Customer.objects.create(code="C-NOPIN", name="Shop With No Pin")
    m = api.get("/api/v1/customers/map")
    assert [s["name"] for s in m.data["without_pin"]] == ["Shop With No Pin"], "a shop with no pin cannot fall in an area"

    placed = api.post(f"/api/v1/customers/{blind.id}/pin", {"lat": GULBERG[0], "lng": GULBERG[1]}, format="json")
    assert placed.status_code == 200 and placed.data["gps"]["lat"] == pytest.approx(GULBERG[0])
    m = api.get("/api/v1/customers/map")
    assert not m.data["without_pin"]
    assert next(s for s in m.data["stores"] if s["name"] == "Shop With No Pin")["fences"] == [str(field["fence"].id)]


def test_only_the_stores_inside_the_area_are_offered_and_assigned(api, field):
    """
    The flow the business asked for: give a rep an area, see only the stores inside it, and hand over one, some
    or all of them.
    """
    from apps.partners.models import Customer, SupplierRep

    with tenant_context(field["tenant"], field["tenant"].owner):
        other = SupplierRep.objects.create(supplier=field["supplier"], code="R-2", name="Second Rep")
        Customer.objects.create(code="C-GUL2", name="Second Gulberg Shop", gps_lat=Decimal("31.5215"), gps_lng=Decimal("74.3590"))

    offered = api.get(f"/api/v1/geofences/{field['fence'].id}/customers")
    inside = {c["name"] for c in offered.data["results"]}
    assert inside == {field["customer"].name, "Second Gulberg Shop"}, "only what is inside the area is offered"
    assert field["far"].name not in inside, "Johar Town is outside the 5 km circle"

    one = next(c for c in offered.data["results"] if c["name"] == "Second Gulberg Shop")
    r = api.post(f"/api/v1/geofences/{field['fence'].id}/assign-customers", {"rep": str(other.id), "customers": [one["id"]]}, format="json")
    assert r.status_code == 200 and r.data["stores"] == 1
    with tenant_context(field["tenant"], field["tenant"].owner):
        assert list(Customer.objects.get(code="C-GUL2").assigned_reps.values_list("name", flat=True)) == ["Second Rep"]

    # …and all of them in one go
    r = api.post(f"/api/v1/geofences/{field['fence'].id}/assign-customers", {"rep": str(other.id), "all": True}, format="json")
    assert r.data["stores"] == 2
    with tenant_context(field["tenant"], field["tenant"].owner):
        assert other.customers.count() == 2
        assert field["far"].assigned_reps.filter(pk=other.pk).count() == 0, "the store outside the area was left alone"

    # taking them off again
    r = api.post(f"/api/v1/geofences/{field['fence'].id}/assign-customers", {"rep": str(other.id), "all": True, "remove": True}, format="json")
    assert r.data["removed"] is True
    with tenant_context(field["tenant"], field["tenant"].owner):
        assert other.customers.count() == 0

    assert api.post(f"/api/v1/geofences/{field['fence'].id}/assign-customers", {"customers": []}, format="json").status_code == 400


def test_a_rep_only_sees_the_stores_assigned_to_them(api, field, tenant):
    """Assigning is what a rep sees — the area decides who is offered, the assignment decides who is theirs."""
    from apps.accounts.models import TenantMembership, User
    from apps.accounts.services import RoleService, UserService
    from apps.partners.models import Customer

    t = field["tenant"]
    with tenant_context(t, t.owner):
        Customer.objects.create(code="C-GUL3", name="Third Gulberg Shop", gps_lat=Decimal("31.5220"), gps_lng=Decimal("74.3600"))
        role = RoleService.create_role(t, "Store rep", ["external.view_own_orders", "external.view_assigned_customers"], role_type="external")
        UserService.create_tenant_user(t, "Store Rep", email="storerep@probe.pk", password="Abcd-1234", roles=[role], warehouses=[])
        user = User.objects.get(email="storerep@probe.pk")
        User.objects.filter(pk=user.pk).update(must_change_password=False)
        field["rep"].user, field["rep"].role = user, role
        field["rep"].save(update_fields=["user", "role"])
        TenantMembership.objects.filter(user=user, tenant=t).update(supplier_rep=field["rep"])

    from rest_framework.test import APIClient

    c = APIClient()
    login = c.post("/api/v1/auth/login", {"identifier": "storerep@probe.pk", "password": "Abcd-1234"}, format="json")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}", HTTP_X_TENANT_ID=str(t.id))
    seen = {s["name"] for s in c.get("/ext/v1/customers").data["results"]}
    assert "Third Gulberg Shop" not in seen, "inside the area, but not handed over — so not theirs"

    api.post(f"/api/v1/geofences/{field['fence'].id}/assign-customers", {"rep": str(field["rep"].id), "all": True}, format="json")
    seen = {s["name"] for s in c.get("/ext/v1/customers").data["results"]}
    assert "Third Gulberg Shop" in seen, "once assigned, the rep sees it"


@pytest.fixture
def rep_client(api, field):
    """A logged-in rep app for the field fixture's rep, wired the way a real rep login is."""
    from apps.accounts.models import TenantMembership, User
    from apps.accounts.services import RoleService, UserService

    t = field["tenant"]
    with tenant_context(t, t.owner):
        role = RoleService.create_role(t, "Field app", ["external.view_own_orders", "external.submit_order",
                                                        "external.view_assigned_customers", "external.record_collection"], role_type="external")
        UserService.create_tenant_user(t, "Field Rep", email="fieldrep@probe.pk", password="Abcd-1234", roles=[role], warehouses=[])
        user = User.objects.get(email="fieldrep@probe.pk")
        User.objects.filter(pk=user.pk).update(must_change_password=False)
        field["rep"].user, field["rep"].role = user, role
        field["rep"].save(update_fields=["user", "role"])
        TenantMembership.objects.filter(user=user, tenant=t).update(supplier_rep=field["rep"])

    from rest_framework.test import APIClient

    c = APIClient()
    login = c.post("/api/v1/auth/login", {"identifier": "fieldrep@probe.pk", "password": "Abcd-1234"}, format="json")
    assert login.status_code == 200, login.content
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}", HTTP_X_TENANT_ID=str(t.id))
    return c


# a real 1x1 png — the smallest honest stand-in for a photo taken at the shop
PIXEL = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def _order_body(field, variant, **extra):
    return {"customer": {"id": str(field["customer"].id)}, "lines": [{"variant_id": str(variant.id), "qty": 2}], **extra}


def test_a_rep_order_carries_a_photo_of_the_shop(api, field, rep_client):
    """The photo is evidence: it is filed against the order and says when and where it was taken."""
    from apps.core.models import Attachment

    body = _order_body(field, field["variant"], gps={"lat": GULBERG[0], "lng": GULBERG[1]}, attachments=[PIXEL])
    r = rep_client.post("/ext/v1/orders", body, format="json")
    assert r.status_code == 201, r.content
    with tenant_context(field["tenant"], field["tenant"].owner):
        shot = Attachment.objects.filter(model="SalesOrder", object_id=str(r.data["id"]), kind="visit_photo").first()
        assert shot is not None, "the photo is filed against the order"
        assert "31.520" in shot.name and str(timezone.localdate().year) in shot.name, shot.name


def test_the_photo_can_be_made_compulsory(api, field, rep_client):
    t = field["tenant"]
    t.settings = {**(t.settings or {}), "rep_order_photo": "required"}
    t.save(update_fields=["settings"])
    bare = rep_client.post("/ext/v1/orders", _order_body(field, field["variant"], gps={"lat": GULBERG[0], "lng": GULBERG[1]}), format="json")
    assert bare.status_code == 400 and bare.data["type"] == "photo_required", bare.content
    with_photo = rep_client.post("/ext/v1/orders", _order_body(field, field["variant"], gps={"lat": GULBERG[0], "lng": GULBERG[1]}, attachments=[PIXEL]), format="json")
    assert with_photo.status_code == 201, with_photo.content


def test_an_order_from_outside_the_area_is_refused_and_recorded(api, field, rep_client):
    from apps.partners.models import GeoFenceEvent

    r = rep_client.post("/ext/v1/orders", _order_body(field, field["variant"], gps={"lat": KARACHI[0], "lng": KARACHI[1]}, attachments=[PIXEL]), format="json")
    assert r.status_code == 403 and r.data["type"] == "outside_area", r.content
    with tenant_context(field["tenant"], field["tenant"].owner):
        event = GeoFenceEvent.objects.filter(action="order", verdict="outside").first()
        assert event is not None and event.blocked and event.distance_m > 900_000


def test_the_check_that_allowed_an_order_points_at_it(api, field, rep_client):
    """A refusal has no document to point at; the check that let an order through does."""
    from apps.partners.models import GeoFenceEvent

    r = rep_client.post("/ext/v1/orders", _order_body(field, field["variant"], gps={"lat": GULBERG[0], "lng": GULBERG[1]}), format="json")
    assert r.status_code == 201, r.content
    with tenant_context(field["tenant"], field["tenant"].owner):
        event = GeoFenceEvent.objects.filter(action="order", verdict="inside").first()
        assert event.document_number == r.data["number"] and event.document_type == "SalesOrder"
