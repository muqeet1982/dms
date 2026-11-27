"""
Owner, 27 Nov 2026 — the Delivery Rider plan, answered "yes. and do recommanded":

- the system puts a trip's stops in the best order, from where the rider is when the trip starts; Google's road route
  when the key allows it, the nearest shop next (crossed legs swapped out) when it does not; a shop with no pin goes last
- Arrived (the phone near the shop) starts the minutes at the shop
- km planned and km actually driven, per stop and per trip; Delivery Rider Performance shows them per rider per day
- the rider starts trips, the office closes them (returns back on the shelf, cash counted)
"""
import io
import json
from decimal import Decimal as D

import pytest
from rest_framework.test import APIClient

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

WAREHOUSE = (31.5204, 74.3587)
FAR, NEAR, MIDDLE = (31.6000, 74.3587), (31.5300, 74.3587), (31.5600, 74.3587)   # due north, 9 / 1 / 4 km


def _setup(api, tenant, pins):
    """One issued, invoiced order per shop, all on one trip; `pins` gives each shop's pin (None = no pin)."""
    from apps.delivery.models import Driver, Vehicle
    from apps.partners.models import Customer

    from .conftest import seed_basic

    wh, _sup, v, _c = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        driver = Driver.objects.create(name="Akram", phone="03001234567")
        vehicle = Vehicle.objects.create(reg_no="LEB-1001")
        shops = [Customer.objects.create(code=f"R-{i}", name=f"Shop {i}", credit_limit=D("100000"),
                                         gps_lat=D(str(p[0])) if p else None, gps_lng=D(str(p[1])) if p else None) for i, p in enumerate(pins)]
    orders = []
    for c in shops:
        o = api.post("/api/v1/sales-orders", {"customer": str(c.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": "1"}]}, format="json").data
        pl = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json").data
        api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl["lines"]]}, format="json")
        orders.append(o)
    trip = api.post("/api/v1/trips", {"warehouse": str(wh.id), "driver": str(driver.id), "vehicle": str(vehicle.id), "delivery_rep": str(tenant.owner.id), "order_ids": [o["id"] for o in orders]}, format="json")
    assert trip.status_code == 201, trip.content
    for o in orders:
        api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": True}, format="json")
    return trip.data, shops


def _road(a, b):
    from apps.delivery.routing import ROAD_FACTOR, haversine_km

    return haversine_km(a, b) * ROAD_FACTOR


def _stops(api, trip_id):
    t = api.get(f"/api/v1/trips/{trip_id}").data
    return t, sorted(t["delivery_notes"], key=lambda n: n["sequence"])


@pytest.fixture
def no_google(settings):
    settings.GOOGLE_MAPS_API_KEY = ""


def test_encoded_polyline_matches_googles_own_example():
    from apps.delivery.routing import encode_polyline

    assert encode_polyline([(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"


def test_nearest_shop_next_and_crossed_legs_swapped():
    from apps.delivery.routing import straight_order

    assert straight_order(WAREHOUSE, [FAR, NEAR, MIDDLE]) == [1, 2, 0]


def test_the_trip_starts_from_the_rider_and_the_stops_go_in_the_best_order(api, tenant, no_google):
    trip, shops = _setup(api, tenant, [FAR, None, NEAR, MIDDLE])
    r = api.post(f"/api/v1/trips/{trip['id']}/start", {"gps": {"lat": WAREHOUSE[0], "lng": WAREHOUSE[1]}}, format="json")
    assert r.status_code == 200, r.content
    t, stops = _stops(api, trip["id"])
    assert [s["customer_name"] for s in stops] == ["Shop 2", "Shop 3", "Shop 0", "Shop 1"], "nearest first, the shop with no pin last"
    assert t["start_gps"] == {"lat": WAREHOUSE[0], "lng": WAREHOUSE[1]} and t["route_source"] == "straight"
    assert float(stops[0]["planned_km"]) == pytest.approx(_road(WAREHOUSE, NEAR), abs=0.01), "the straight line × 1.3"
    assert stops[3]["planned_km"] is None
    # out to the farthest shop and back, × 1.3
    assert float(t["planned_km"]) == pytest.approx(2 * _road(WAREHOUSE, FAR), abs=0.03), t["planned_km"]
    assert t["planned_minutes"] > 0 and t["route_polyline"]


def test_googles_road_route_is_used_when_the_key_allows_it(api, tenant, settings, monkeypatch):
    settings.GOOGLE_MAPS_API_KEY = "test-key"
    trip, _shops = _setup(api, tenant, [FAR, NEAR, MIDDLE])
    sent = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout=None):
        sent["body"] = json.loads(req.data)
        sent["key"] = req.get_header("X-goog-api-key")
        return Resp(json.dumps({"routes": [{"optimizedIntermediateWaypointIndex": [1, 2, 0], "polyline": {"encodedPolyline": "abc"},
                                            "legs": [{"distanceMeters": 1500, "duration": "300s"}, {"distanceMeters": 4000, "duration": "600s"},
                                                     {"distanceMeters": 6000, "duration": "900s"}, {"distanceMeters": 11000, "duration": "1500s"}]}]}).encode())

    monkeypatch.setattr("apps.delivery.routing.urllib.request.urlopen", fake)
    assert api.post(f"/api/v1/trips/{trip['id']}/start", {"gps": {"lat": WAREHOUSE[0], "lng": WAREHOUSE[1]}}, format="json").status_code == 200
    t, stops = _stops(api, trip["id"])
    assert sent["key"] == "test-key" and sent["body"]["optimizeWaypointOrder"] is True and len(sent["body"]["intermediates"]) == 3
    by_lat = {FAR[0]: "Shop 0", NEAR[0]: "Shop 1", MIDDLE[0]: "Shop 2"}
    sent_shops = [by_lat[round(p["location"]["latLng"]["latitude"], 4)] for p in sent["body"]["intermediates"]]
    assert [s["customer_name"] for s in stops] == [sent_shops[i] for i in (1, 2, 0)], "Google's order, by its own index"
    assert [D(s["planned_km"]) for s in stops] == [D("1.50"), D("4.00"), D("6.00")] and [s["planned_minutes"] for s in stops] == [5, 10, 15]
    assert t["route_source"] == "google" and D(t["planned_km"]) == D("22.50") and t["planned_minutes"] == 55 and t["route_polyline"] == "abc"


def test_google_refusing_never_stops_the_trip(api, tenant, settings, monkeypatch):
    import urllib.error

    settings.GOOGLE_MAPS_API_KEY = "test-key"
    trip, _shops = _setup(api, tenant, [FAR, NEAR])

    def refuse(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "Permission denied", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr("apps.delivery.routing.urllib.request.urlopen", refuse)
    assert api.post(f"/api/v1/trips/{trip['id']}/start", {"gps": {"lat": WAREHOUSE[0], "lng": WAREHOUSE[1]}}, format="json").status_code == 200
    t, stops = _stops(api, trip["id"])
    assert t["status"] == "out_for_delivery" and t["route_source"] == "straight"
    assert [s["customer_name"] for s in stops] == ["Shop 1", "Shop 0"]


def test_arrived_then_delivered_gives_minutes_at_the_shop_and_km_driven(api, tenant, no_google):
    from datetime import timedelta

    from django.utils import timezone

    trip, _shops = _setup(api, tenant, [NEAR, MIDDLE])
    api.post(f"/api/v1/trips/{trip['id']}/start", {"gps": {"lat": WAREHOUSE[0], "lng": WAREHOUSE[1]}}, format="json")
    _t, stops = _stops(api, trip["id"])
    first = stops[0]
    at = timezone.now() - timedelta(minutes=12)
    r = api.post(f"/api/v1/delivery-notes/{first['id']}/arrive", {"gps": {"lat": NEAR[0] + 0.0005, "lng": NEAR[1]}, "at": at.isoformat()}, format="json")
    assert r.status_code == 200, r.content
    assert r.data["arrived_at"] and 50 < r.data["arrived_distance_m"] < 60
    again = api.post(f"/api/v1/delivery-notes/{first['id']}/arrive", {"gps": {"lat": 0, "lng": 0}}, format="json").data
    assert again["arrived_at"] == r.data["arrived_at"], "the first Arrived wins"

    d = api.post(f"/api/v1/delivery-notes/{first['id']}/deliver", {"receiver_name": "Asad", "gps": {"lat": NEAR[0], "lng": NEAR[1]}}, format="json")
    assert d.status_code == 200, d.content
    assert d.data["minutes_at_stop"] == 12
    arrived = (NEAR[0] + 0.0005, NEAR[1])
    assert float(d.data["actual_km"]) == pytest.approx(_road(WAREHOUSE, arrived), abs=0.01), "start to where Arrived was pressed, × 1.3"

    # the second shop was closed: the stop still counts its distance
    f = api.post(f"/api/v1/delivery-notes/{stops[1]['id']}/deliver", {"failed": True, "failure_reason": "Shop closed", "gps": {"lat": MIDDLE[0], "lng": MIDDLE[1]}}, format="json")
    assert f.status_code == 200, f.content
    t, stops = _stops(api, trip["id"])
    assert t["status"] == "completed" and t["actual_km_source"] == "straight"
    assert float(stops[1]["actual_km"]) == pytest.approx(_road(arrived, MIDDLE), abs=0.01)
    whole = _road(WAREHOUSE, arrived) + _road(arrived, MIDDLE) + _road(MIDDLE, WAREHOUSE)
    assert float(t["actual_km"]) == pytest.approx(whole, abs=0.03), "both legs and the way back"

    rep = api.get("/api/v1/reports/rider_performance", {"date_from": t["date"], "date_to": t["date"]}).data
    row = rep["rows"][0]
    assert (row["trips"], row["stops"], row["delivered"], row["shop_closed"], row["refused"]) == (1, 2, 1, 1, 0)
    assert row["avg_minutes_per_stop"] == 12 and D(str(row["actual_km"])) == D(t["actual_km"]) and D(str(row["planned_km"])) == D(t["planned_km"])


def test_a_trip_that_started_with_its_first_delivery_is_planned_from_there(api, tenant, no_google):
    """TDN never presses Start: the trip starts with its first stop, and the rest are ordered from that shop."""
    trip, _shops = _setup(api, tenant, [MIDDLE, FAR, NEAR])
    _t, stops = _stops(api, trip["id"])
    near = next(s for s in stops if s["customer_name"] == "Shop 2")
    assert api.post(f"/api/v1/delivery-notes/{near['id']}/deliver", {"receiver_name": "x"}, format="json").status_code == 200
    t, stops = _stops(api, trip["id"])
    assert t["route_planned_at"] and [s["customer_name"] for s in stops] == ["Shop 2", "Shop 0", "Shop 1"]


def test_the_rider_starts_the_trip_and_the_office_closes_it(api, tenant, no_google):
    from apps.accounts.models import GroupProfile, User
    from apps.accounts.services import UserService

    role = GroupProfile.objects.get(tenant=tenant, name="Delivery Rider")
    with tenant_context(tenant, tenant.owner):
        rider = UserService.create_tenant_user(tenant, "Rider", email="rider@alpha.pk", password="Rider-9981-x", roles=[role], warehouses=[]).user
    User.objects.filter(pk=rider.pk).update(must_change_password=False)
    trip, _shops = _setup(api, tenant, [NEAR])
    with tenant_context(tenant, tenant.owner):
        from apps.delivery.models import DeliveryTrip

        DeliveryTrip.objects.filter(pk=trip["id"]).update(delivery_rep=rider)
    c = APIClient()
    tok = c.post("/api/v1/auth/login", {"identifier": "rider@alpha.pk", "password": "Rider-9981-x"}, format="json").data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}", HTTP_X_TENANT_ID=str(tenant.id))
    pages = c.get("/api/v1/me").data["pages"]["trips"]["extras"]
    assert pages["start"] and not pages["close"]
    assert c.post(f"/api/v1/trips/{trip['id']}/start", {"gps": {"lat": WAREHOUSE[0], "lng": WAREHOUSE[1]}}, format="json").status_code == 200
    assert c.post(f"/api/v1/trips/{trip['id']}/plan-route", {"gps": {"lat": NEAR[0], "lng": NEAR[1]}}, format="json").status_code == 200
    _t, stops = _stops(api, trip["id"])
    assert c.post(f"/api/v1/delivery-notes/{stops[0]['id']}/deliver", {"receiver_name": "x"}, format="json").status_code == 200
    assert c.post(f"/api/v1/trips/{trip['id']}/close", {}, format="json").status_code == 403
    assert api.post(f"/api/v1/trips/{trip['id']}/close", {"override": True}, format="json").status_code == 200


def test_every_role_that_could_close_a_trip_still_can():
    """The migration gives Close trips to each role holding Start trips, except the Delivery Rider."""
    import importlib

    from django.apps import apps as global_apps
    from django.contrib.auth.models import Group, Permission
    from django.contrib.contenttypes.models import ContentType

    from apps.accounts.models import GroupProfile
    from apps.core.models import Module

    ct = ContentType.objects.get_for_model(Module)
    start, _ = Permission.objects.get_or_create(content_type=ct, codename="delivery.start_trip", defaults={"name": "x"})
    Permission.objects.filter(codename="delivery.close_trip").delete()
    keep, rider = Group.objects.create(name="t:keep"), Group.objects.create(name="t:rider")
    for g, name in ((keep, "Dispatcher"), (rider, "Delivery Rider")):
        g.permissions.add(start)
        GroupProfile.objects.create(group=g, tenant=None, name=name)
    importlib.import_module("apps.accounts.migrations.0013_close_trip_switch").split(global_apps, None)
    assert keep.permissions.filter(codename="delivery.close_trip").exists()
    assert not rider.permissions.filter(codename="delivery.close_trip").exists()
