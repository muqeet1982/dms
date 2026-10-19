"""
Rep tracking: the policy the office sets, the fixes a phone sends, and the day those fixes add up to.

The contract these tests hold the server to is the one the mobile app was written against — see `tracking.md`.
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

GULBERG = (31.5180, 74.3480)
NEXT_STREET = (31.5168, 74.3430)
KARACHI = (24.8607, 67.0011)


@pytest.fixture
def field(api, tenant):
    """A rep with a login, a shop with a pin, and tracking switched on."""
    from apps.accounts.models import TenantMembership, User
    from apps.accounts.services import RoleService, UserService

    from .conftest import seed_basic

    wh, supplier, variant, customer = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        from decimal import Decimal

        from apps.partners.models import SupplierRep

        rep = SupplierRep.objects.create(supplier=supplier, code="R-TRK", name="Tracked Rep")
        customer.gps_lat, customer.gps_lng = Decimal(str(GULBERG[0])), Decimal(str(GULBERG[1]))
        customer.save(update_fields=["gps_lat", "gps_lng"])
        customer.assigned_reps.add(rep)
        role = RoleService.create_role(tenant, "Tracked", ["external.view_own_orders", "external.submit_order",
                                                           "external.view_assigned_customers", "tracking.view_own"], role_type="external")
        UserService.create_tenant_user(tenant, "Tracked Rep", email="tracked@probe.pk", password="Abcd-1234", roles=[role], warehouses=[])
        user = User.objects.get(email="tracked@probe.pk")
        User.objects.filter(pk=user.pk).update(must_change_password=False)
        rep.user, rep.role = user, role
        rep.save(update_fields=["user", "role"])
        TenantMembership.objects.filter(user=user, tenant=tenant).update(supplier_rep=rep)

    tenant.settings = {**(tenant.settings or {}), "tracking_enabled": True}
    tenant.save(update_fields=["settings"])

    from rest_framework.test import APIClient

    phone = APIClient()
    login = phone.post("/api/v1/auth/login", {"identifier": "tracked@probe.pk", "password": "Abcd-1234"}, format="json")
    assert login.status_code == 200, login.content
    phone.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}", HTTP_X_TENANT_ID=str(tenant.id))
    return {"tenant": tenant, "rep": rep, "user": user, "customer": customer, "variant": variant,
            "warehouse": wh, "phone": phone}


def _pings(points, start=None, step_minutes=4):
    # Anchor inside today, on the company's clock. "Two hours ago" is yesterday when the suite runs between
    # midnight and 02:00, and the endpoints under test default to *today* — so the fixture, not the code, was
    # what failed at half past midnight.
    at = start or max(timezone.now() - timedelta(hours=2),
                      timezone.localtime().replace(hour=0, minute=1, second=0, microsecond=0))
    return [{"client_id": f"p{i}", "at": (at + timedelta(minutes=i * step_minutes)).isoformat(),
             "lat": lat, "lng": lng, "accuracy_m": 10.0, "battery_pct": 80, "source": "background"}
            for i, (lat, lng) in enumerate(points)]


def test_the_policy_ships_on_with_a_notice_and_a_window(api, tenant):
    """
    Tracking is on out of the box, but only during working hours and never silently: the app shows `notice` and
    links `privacy_url` before it asks for background location, and a rep can always read their own history.
    """
    from apps.partners.tracking import policy

    tenant.settings = {k: v for k, v in (tenant.settings or {}).items() if k != "tracking_enabled"}
    tenant.save(update_fields=["settings"])
    p = policy(tenant)
    assert p["enabled"] is True and p["mode"] == "work_hours"
    assert p["window"]["from"] and p["window"]["to"] and p["window"]["days"]
    assert p["notice"] and p["privacy_url"], "the disclosure screen needs both"
    assert p["retention_days"] > 0, "history has to expire"


def test_a_company_can_switch_tracking_off(api, tenant):
    from apps.partners.tracking import policy

    tenant.settings = {**(tenant.settings or {}), "tracking_enabled": False}
    tenant.save(update_fields=["settings"])
    p = policy(tenant)
    assert p["enabled"] is False and p["mode"] == "off", "mode follows the switch, so the app reads one field"


def test_pings_are_refused_until_the_office_turns_tracking_on(field):
    t = field["tenant"]
    t.settings = {**t.settings, "tracking_enabled": False}
    t.save(update_fields=["settings"])
    r = field["phone"].post("/ext/v1/tracking/pings", {"pings": _pings([GULBERG])}, format="json")
    assert r.status_code == 403 and r.data["type"] == "tracking_off", r.content


def test_a_batch_is_taken_once_however_often_it_is_sent(field):
    """The phone retries batches it is unsure about; the same point must not land twice."""
    batch = {"pings": _pings([NEXT_STREET, GULBERG])}
    first = field["phone"].post("/ext/v1/tracking/pings", batch, format="json")
    assert first.status_code == 202 and first.data == {"accepted": 2, "duplicates": 0, "rejected": 0, "dropped": 0}, first.content
    again = field["phone"].post("/ext/v1/tracking/pings", batch, format="json")
    assert again.data == {"accepted": 0, "duplicates": 2, "rejected": 0, "dropped": 0}


def test_a_bad_point_is_a_400_that_names_it_and_a_big_batch_is_a_413(field):
    """The app's queue drops a 4xx batch and retries a 5xx one, so the status has to be the right one."""
    bad = field["phone"].post("/ext/v1/tracking/pings", {"pings": [
        {"client_id": "a", "at": timezone.now().isoformat(), "lat": 31.5, "lng": 74.3},
        {"client_id": "b", "at": timezone.now().isoformat(), "lat": 999, "lng": 74.3},
    ]}, format="json")
    assert bad.status_code == 400 and "pings[1]" in bad.data["detail"], bad.content

    nothing = field["phone"].post("/ext/v1/tracking/pings", {"pings": []}, format="json")
    assert nothing.status_code == 400

    huge = field["phone"].post("/ext/v1/tracking/pings", {"pings": _pings([GULBERG] * 201, step_minutes=0)}, format="json")
    assert huge.status_code == 413, huge.content


def test_a_point_outside_every_working_area_is_still_kept(field):
    """A rep driving home is outside their area — those points are the route, not a violation."""
    r = field["phone"].post("/ext/v1/tracking/pings", {"pings": _pings([KARACHI])}, format="json")
    assert r.status_code == 202 and r.data["accepted"] == 1


def test_the_day_adds_up_to_a_route_a_stop_and_the_shop_it_belongs_to(field):
    """Twenty minutes in one place next to a customer's pin is a stop, and it says whose shop it was."""
    start = timezone.now() - timedelta(hours=3)
    route = _pings([NEXT_STREET, (31.5174, 74.3462)], start=start)
    stay = [{"client_id": f"s{i}", "at": (start + timedelta(minutes=10 + i * 5)).isoformat(),
             "lat": GULBERG[0] + i * 0.00002, "lng": GULBERG[1], "accuracy_m": 8.0} for i in range(5)]
    assert field["phone"].post("/ext/v1/tracking/pings", {"pings": route + stay}, format="json").status_code == 202

    with tenant_context(field["tenant"], field["tenant"].owner):
        from apps.partners.tracking import day_for

        # the day the route belongs to, not "today": three hours ago is yesterday if this runs after midnight
        day = day_for(field["user"], timezone.localdate(start), rep=field["rep"])
    assert day["points"] and day["first_at"] and day["last_at"]
    assert 0 < day["distance_m"] < 5000, day["distance_m"]
    stop = next((s for s in day["stops"] if s["customer_name"]), None)
    assert stop is not None, day["stops"]
    assert stop["customer_name"] == field["customer"].name and stop["minutes"] >= 20
    assert stop["customer_distance_m"] < 100


def test_one_bad_fix_does_not_turn_a_morning_into_a_thousand_kilometres(field):
    from apps.partners.tracking import TrackingService

    start = timezone.now() - timedelta(hours=2)
    good = _pings([NEXT_STREET, (31.5174, 74.3462), GULBERG], start=start)
    glitch = [{"client_id": "glitch", "at": (start + timedelta(minutes=6)).isoformat(),
               "lat": KARACHI[0], "lng": KARACHI[1], "accuracy_m": 20.0}]
    assert field["phone"].post("/ext/v1/tracking/pings", {"pings": good + glitch}, format="json").status_code == 202
    with tenant_context(field["tenant"], field["tenant"].owner):
        points = TrackingService.points_for(field["user"], timezone.localdate(start))
        assert len(points) == 4, "the odd fix is kept — it still shows on the map"
        assert TrackingService.distance_of(points) < 5000, "but it is not counted as travel"


def test_a_rep_sees_their_own_day_and_nobody_elses(field, api):
    field["phone"].post("/ext/v1/tracking/pings", {"pings": _pings([GULBERG, NEXT_STREET])}, format="json")
    mine = field["phone"].get("/ext/v1/tracking/me")
    assert mine.status_code == 200 and mine.data["points"], mine.content
    assert "user_name" not in mine.data

    assert field["phone"].get("/api/v1/tracking/live").status_code == 403
    assert field["phone"].get("/api/v1/tracking/users").status_code == 403
    own = field["phone"].get(f"/api/v1/tracking/users/{field['user'].pk}")
    assert own.status_code == 200, "their own track is theirs to see"


def test_the_office_sees_the_team_the_live_map_and_who_looked(api, field):
    from apps.core.models import AuditLog

    field["phone"].post("/ext/v1/tracking/pings", {"pings": _pings([GULBERG, NEXT_STREET])}, format="json")

    day = api.get(f"/api/v1/tracking/users/{field['user'].pk}")
    assert day.status_code == 200 and day.data["user_name"] == "Tracked Rep"

    team = api.get("/api/v1/tracking/users")
    assert team.data["results"] and "points" not in team.data["results"][0]

    live = api.get("/api/v1/tracking/live")
    row = live.data["users"][0]
    assert {"lat", "lng", "at", "stale_s", "moving"} <= set(row) and row["stale_s"] >= 0

    with tenant_context(field["tenant"], field["tenant"].owner):
        assert AuditLog.objects.filter(action="tracking.viewed").exists(), "reading someone's track is logged"


def test_an_order_keeps_the_position_it_was_taken_at(field, api):
    """§4 of the app's contract: the GPS was being validated and thrown away."""
    r = field["phone"].post("/ext/v1/orders", {
        "customer": {"id": str(field["customer"].id)},
        "gps": {"lat": GULBERG[0], "lng": GULBERG[1], "accuracy_m": 9.5},
        "lines": [{"variant_id": str(field["variant"].id), "qty": 2}],
    }, format="json")
    assert r.status_code == 201, r.content
    order = api.get(f"/api/v1/sales-orders/{r.data['id']}")
    assert order.data["gps"] == {"lat": GULBERG[0], "lng": GULBERG[1]}
    assert order.data["gps_accuracy_m"] == 9.5

    with tenant_context(field["tenant"], field["tenant"].owner):
        from apps.partners.tracking import TrackingService

        events = TrackingService.events_for(field["user"], field["rep"], timezone.localdate())
    assert [(e["type"], e["ref"]) for e in events] == [("order", r.data["number"])]


def test_a_collection_and_a_delivery_keep_the_accuracy_the_phone_sent(field):
    """
    The app sends `accuracy_m` on collections and on delivery completion too. Both rows have the column; both
    write paths used to drop it, so a vague fix looked exactly like a precise one.
    """
    from decimal import Decimal

    from apps.delivery.services import DeliveryService
    from apps.sales.services import RepCollectionService

    with tenant_context(field["tenant"], field["tenant"].owner):
        col = RepCollectionService.record(rep=field["rep"], customer=field["customer"], amount="500",
                                          gps={"lat": GULBERG[0], "lng": GULBERG[1], "accuracy_m": 12.5})
        assert col.gps_accuracy_m == 12.5
        assert float(col.gps_lat) == GULBERG[0]

        # and the same on a stop a driver signs for
        from apps.delivery.models import Driver, Vehicle
        from apps.delivery.services import TripService
        from apps.sales.services import OrderService

        order = OrderService.create(customer=field["customer"], warehouse=field["warehouse"],
                                    lines=[{"variant": field["variant"], "qty": Decimal("2"), "unit_price": None}],
                                    source="staff")
        OrderService.confirm(order)
        trip = TripService.create(warehouse=field["warehouse"], driver=Driver.objects.create(name="D", phone="1"),
                                  vehicle=Vehicle.objects.create(reg_no="V-TRK"), orders=[order])
        TripService.start(trip)
        note = trip.delivery_notes.first()
        DeliveryService.deliver(note, receiver_name="R",
                                gps={"lat": GULBERG[0], "lng": GULBERG[1], "accuracy_m": 7.0})
        note.refresh_from_db()
        assert note.gps_accuracy_m == 7.0, "a delivered stop keeps the accuracy of the fix it was signed at"
        assert float(note.gps_lat) == GULBERG[0]


def test_history_is_purged_to_the_retention_the_office_set(field):
    from apps.partners.models import LocationPing
    from apps.partners.tracking import purge

    with tenant_context(field["tenant"], field["tenant"].owner):
        old = timezone.now() - timedelta(days=120)
        LocationPing.objects.create(user=field["user"], client_id="ancient", at=old, lat="31.5", lng="74.3")
        LocationPing.objects.create(user=field["user"], client_id="recent", at=timezone.now(), lat="31.5", lng="74.3")
        out = purge(field["tenant"], days=90)
        assert out["deleted"] == 1
        assert list(LocationPing.objects.values_list("client_id", flat=True)) == ["recent"]
