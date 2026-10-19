"""
The owner's shift model: Start / Break / Back / End on the phone, tracking exactly while the shift is on
(never during a break), no shift = no order, and the server watching for the silence a killed tracker
cannot report about itself. Office corrections keep the phone's own record alongside.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

GULBERG = (31.5180, 74.3480)


@pytest.fixture
def field(api, tenant):
    from apps.accounts.models import TenantMembership, User
    from apps.accounts.services import RoleService, UserService
    from apps.partners.models import SupplierRep
    from rest_framework.test import APIClient

    from .conftest import seed_basic

    wh, supplier, variant, customer = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        tenant.settings = {**(tenant.settings or {}), "require_shift_for_orders": True, "tracking_mode": "shift"}
        tenant.save(update_fields=["settings"])
        rep = SupplierRep.objects.create(supplier=supplier, code="R-SHF", name="Shift Rep")
        customer.gps_lat, customer.gps_lng = Decimal(str(GULBERG[0])), Decimal(str(GULBERG[1]))
        customer.save(update_fields=["gps_lat", "gps_lng"])
        customer.assigned_reps.add(rep)
        role = RoleService.create_role(tenant, "Shifted", ["external.view_assigned_customers", "external.submit_order",
                                                           "external.view_own_orders", "tracking.view_own"], role_type="external")
        UserService.create_tenant_user(tenant, "Shift Rep", email="shift@probe.pk", password="Abcd-1234", roles=[role], warehouses=[])
        user = User.objects.get(email="shift@probe.pk")
        User.objects.filter(pk=user.pk).update(must_change_password=False)
        rep.user, rep.role = user, role
        rep.save(update_fields=["user", "role"])
        TenantMembership.objects.filter(user=user, tenant=tenant).update(supplier_rep=rep)

    phone = APIClient()
    login = phone.post("/api/v1/auth/login", {"identifier": "shift@probe.pk", "password": "Abcd-1234"}, format="json")
    assert login.status_code == 200, login.content
    phone.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}", HTTP_X_TENANT_ID=str(tenant.id))
    return {"tenant": tenant, "rep": rep, "user": user, "customer": customer, "variant": variant, "phone": phone}


def _start(field, client_id="day-1"):
    return field["phone"].post("/ext/v1/shifts", {
        "client_id": client_id, "at": timezone.now().isoformat(),
        "gps": {"lat": GULBERG[0], "lng": GULBERG[1], "accuracy_m": 8, "mocked": False},
        "device": {"model": "Pixel 6", "os": "Android 14", "app_version": "2.4.0"},
        "checks": {"location_services": True, "background_permission": True, "notifications": True, "battery_unrestricted": True},
    }, format="json")


def _order(field):
    return field["phone"].post("/ext/v1/orders", {
        "customer": {"id": str(field["customer"].id)},
        "gps": {"lat": GULBERG[0], "lng": GULBERG[1], "accuracy_m": 9.0},
        "lines": [{"variant_id": str(field["variant"].id), "qty": 1}],
    }, format="json")


def test_no_shift_no_order_and_a_break_pauses_it(field):
    """The owner's rule 4, and its break-time corollary — refused with words the app shows verbatim."""
    refused = _order(field)
    assert refused.status_code == 403 and refused.data["type"] == "no_shift", refused.content

    assert _start(field).status_code == 201
    assert _order(field).status_code == 201, "on shift, at the shop — the order goes through"

    cur = field["phone"].get("/ext/v1/shifts/current").data["shift"]
    assert field["phone"].post(f"/ext/v1/shifts/{cur['id']}/break", {"at": timezone.now().isoformat()}, format="json").status_code == 200
    on_break = _order(field)
    assert on_break.status_code == 403 and on_break.data["type"] == "on_break"

    assert field["phone"].post(f"/ext/v1/shifts/{cur['id']}/resume", {"at": timezone.now().isoformat()}, format="json").status_code == 200
    assert _order(field).status_code == 201


def test_the_app_names_the_shift_and_replays_are_harmless(field):
    """A shift started in a dead zone has no server id — the app's client_id works everywhere, and a queued
    retry of the start returns the same shift instead of opening a second one."""
    first = _start(field, client_id="dead-zone-1")
    again = _start(field, client_id="dead-zone-1")
    assert first.status_code == 201 and again.status_code == 201
    assert first.data["shift"]["id"] == again.data["shift"]["id"]
    assert again.data["shift"]["client_id"] == "dead-zone-1"

    # transitions by the app's own name for it
    r = field["phone"].post("/ext/v1/shifts/dead-zone-1/break", {"at": timezone.now().isoformat()}, format="json")
    assert r.status_code == 200 and r.data["shift"]["status"] == "break"
    r = field["phone"].post("/ext/v1/shifts/dead-zone-1/end", {"at": timezone.now().isoformat(), "reason": "rep"}, format="json")
    assert r.status_code == 200 and r.data["shift"]["status"] == "ended" and r.data["shift"]["end_reason"] == "rep"

    # current now answers with today's ended shift, so the app can say when and why it ended
    cur = field["phone"].get("/ext/v1/shifts/current").data["shift"]
    assert cur and cur["status"] == "ended" and cur["end_reason"] == "rep"


def test_a_mocked_fix_cannot_start_a_shift(field):
    r = field["phone"].post("/ext/v1/shifts", {
        "client_id": "fake-1", "gps": {"lat": GULBERG[0], "lng": GULBERG[1], "mocked": True},
    }, format="json")
    assert r.status_code == 400 and r.data["type"] == "mocked_fix"


def test_tracking_follows_the_shift_and_breaks_are_silence(field, tenant):
    """In shift mode a ping lands only while the shift is ON — a break is deliberate silence, not data."""
    from apps.partners.models import LocationPing, RepShift

    def ping(cid):
        return field["phone"].post("/ext/v1/tracking/pings", {"pings": [
            {"client_id": cid, "at": timezone.now().isoformat(), "lat": GULBERG[0], "lng": GULBERG[1], "mocked": cid == "p-mock"},
        ]}, format="json")

    off = ping("p-off")
    assert off.status_code == 202 and off.data["dropped"] == 1 and off.data["reason"] == "no_shift"

    assert _start(field).status_code == 201
    on = ping("p-on")
    assert on.status_code == 202 and on.data["accepted"] == 1
    assert ping("p-mock").data["accepted"] == 1

    with tenant_context(tenant, tenant.owner):
        shift = RepShift.objects.get(rep=field["rep"])
        assert shift.last_ping_at is not None
        assert LocationPing.objects.filter(shift=shift).count() == 2
        assert LocationPing.objects.filter(shift=shift, mocked=True).count() == 1

    cur = field["phone"].get("/ext/v1/shifts/current").data["shift"]
    field["phone"].post(f"/ext/v1/shifts/{cur['id']}/break", {}, format="json")
    paused = ping("p-break")
    assert paused.status_code == 202 and paused.data["dropped"] == 1 and paused.data["reason"] == "on_break"


def test_the_sweep_catches_dead_trackers_but_not_breaks(field, tenant):
    """Silence past the gap alerts; silence past the end threshold ends the shift; a quiet break is neither."""
    from apps.core.models import Notification
    from apps.partners.models import RepShift
    from apps.partners.shifts import ShiftService, sweep_tenant

    assert _start(field).status_code == 201
    with tenant_context(tenant, tenant.owner):
        shift = RepShift.objects.get(rep=field["rep"])
        RepShift.objects.filter(pk=shift.pk).update(last_ping_at=timezone.now() - timedelta(minutes=20))
        acted = sweep_tenant(tenant)
        assert acted["alerted"] == 1
        assert Notification.objects.filter(user=field["user"], type="shift.tracking_lost").exists()

        # a break makes the same silence legitimate
        shift.refresh_from_db()
        ShiftService.break_(shift)
        assert sweep_tenant(tenant)["alerted"] == 0

        ShiftService.resume(shift)
        RepShift.objects.filter(pk=shift.pk).update(last_ping_at=timezone.now() - timedelta(minutes=50), lost_alerted_at=timezone.now() - timedelta(minutes=35))
        acted = sweep_tenant(tenant)
        assert acted["ended_lost"] == 1
        shift.refresh_from_db()
        assert shift.status == "ended" and shift.end_reason == "tracking_lost"
        assert Notification.objects.filter(user=field["user"], type="shift.ended").exists()


def test_the_office_corrects_times_with_the_phone_record_kept(field, api, tenant):
    """Corrected attendance gets disputed later: reason required, originals kept, the rep told."""
    from apps.core.models import Notification

    assert _start(field).status_code == 201
    cur = field["phone"].get("/ext/v1/shifts/current").data["shift"]
    field["phone"].post(f"/ext/v1/shifts/{cur['id']}/end", {"reason": "rep"}, format="json")

    no_reason = api.patch(f"/api/v1/shifts/{cur['id']}", {"ended_at": timezone.now().isoformat()}, format="json")
    assert no_reason.status_code in (400, 422), no_reason.content

    new_end = (timezone.now() + timedelta(minutes=30)).isoformat()
    ok = api.patch(f"/api/v1/shifts/{cur['id']}", {"ended_at": new_end, "reason": "Forgot to end after the last call"}, format="json")
    assert ok.status_code == 200 and ok.data["corrected"] is True, ok.content

    with tenant_context(tenant, tenant.owner):
        from apps.partners.models import RepShift

        shift = RepShift.objects.get(pk=cur["id"])
        assert shift.recorded_ended_at != shift.ended_at, "the phone's own record is never touched"
        assert shift.correction_reason
        assert Notification.objects.filter(user=field["user"], type="shift.corrected").exists()

    row = field["phone"].get("/ext/v1/shifts/current").data["shift"]
    assert row["corrected"] is True, "the app shows the effective times with the corrected marker"

    sheet = api.get("/api/v1/shifts", {"date": str(timezone.localdate())})
    assert sheet.status_code == 200
    assert any(r["id"] == cur["id"] and r["corrected"] for r in sheet.data["results"])


def test_my_areas_carries_the_shift_block_the_app_switches_on(field):
    block = field["phone"].get("/ext/v1/my-areas").data["shift"]
    assert block == {"enabled": True, "require_for_orders": True, "auto_end_at": "23:59", "gap_minutes": 15}
