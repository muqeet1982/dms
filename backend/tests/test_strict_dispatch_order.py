"""
The owner's order of work (11 Nov 2026, "do recommanded"): a trip starts with its first delivery and completes with its
last, no order goes on a trip before its invoice is posted, and no delivery is recorded without a trip: one on no trip
names its driver and delivery person and the trip is made with it. TDN never pressed Start Trip, and that afternoon 20
deliveries were recorded with no vehicle on record.
"""
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

from .conftest import issue_order, seed_basic

pytestmark = pytest.mark.django_db


def _strict(tenant, on=True):
    tenant.settings = {**(tenant.settings or {}), "trip_needs_posted_invoice": on, "delivery_needs_trip": on}
    tenant.save(update_fields=["settings"])


def _order(api, wh, v, cust, qty="2"):
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": qty}]}, format="json")
    assert r.status_code == 201, r.content
    return r.data


def _invoice(api, order):
    r = api.post(f"/api/v1/sales-orders/{order['id']}/invoice", {"post": True}, format="json")
    assert r.status_code in (200, 201), r.content
    return r.data[0]


def _driver(tenant):
    from apps.delivery.models import Driver

    with tenant_context(tenant, tenant.owner):
        return Driver.objects.create(name="Akram", phone="03001234567")


def _note(tenant, order):
    from apps.delivery.models import DeliveryNote

    with tenant_context(tenant):
        return DeliveryNote.objects.get(order_id=order["id"])


def _trip(api, wh, driver, tenant, orders):
    return api.post("/api/v1/trips", {"delivery_rep": str(tenant.owner.id), "warehouse": str(wh.pk), "driver": str(driver.pk),
                                      "order_ids": [o["id"] for o in orders]}, format="json")


def test_a_trip_starts_with_its_first_delivery_and_completes_with_its_last(api, tenant):
    wh, _s, v, cust = seed_basic(tenant)
    a, b = _order(api, wh, v, cust), _order(api, wh, v, cust)
    issue_order(api, a["id"]), issue_order(api, b["id"])
    t = _trip(api, wh, _driver(tenant), tenant, [a, b])
    assert t.status_code == 201, t.content
    first = _note(tenant, a)
    assert api.post(f"/api/v1/delivery-notes/{first.pk}/deliver", {"receiver_name": "Shop"}, format="json").status_code == 200
    trip = api.get(f"/api/v1/trips/{t.data['id']}").data
    assert trip["status"] == "out_for_delivery" and trip["started_at"], "Start Trip was never pressed"
    assert D(trip["loaded_value"]) == D("400"), "4 pcs at cost 100 went out"
    assert api.get(f"/api/v1/sales-orders/{b['id']}").data["status"] == "dispatched", "the other stop is on the road"

    second = _note(tenant, b)                                  # the last stop fails: still the end of the trip
    assert api.post(f"/api/v1/delivery-notes/{second.pk}/deliver", {"failed": True, "failure_reason": "Shop closed"}, format="json").status_code == 200
    trip = api.get(f"/api/v1/trips/{t.data['id']}").data
    assert trip["status"] == "completed" and trip["completed_at"], trip["status"]


def test_no_trip_before_the_invoice_is_posted(api, tenant):
    _strict(tenant)
    wh, _s, v, cust = seed_basic(tenant)
    o = _order(api, wh, v, cust)
    issue_order(api, o["id"])
    driver = _driver(tenant)
    offered = lambda: [x["id"] for x in api.get("/api/v1/sales-orders", {"ready_for_trip": 1}).data["results"]]
    assert o["id"] not in offered(), "New Trip does not offer it"
    steps = {s["key"]: s for s in api.get(f"/api/v1/sales-orders/{o['id']}/workflow").data["steps"]}
    assert steps["dispatch"]["state"] == "todo" and steps["dispatch"]["detail"] == "After the invoice is posted."

    r = _trip(api, wh, driver, tenant, [o])
    assert r.status_code == 422 and r.data["type"] == "invoice_first", r.content
    assert r.data["detail"] == f"{o['number']}: post its invoice first. An order goes on a trip once its invoice is posted."

    draft = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": False}, format="json")
    assert draft.status_code in (200, 201) and draft.data[0]["status"] == "draft"
    assert _trip(api, wh, driver, tenant, [o]).status_code == 422, "a draft is not posted"
    assert api.post(f"/api/v1/invoices/{draft.data[0]['id']}/post", {}, format="json").status_code == 200
    assert o["id"] in offered()
    assert _trip(api, wh, driver, tenant, [o]).status_code == 201


def test_a_delivery_on_no_trip_makes_one_with_its_driver(api, tenant):
    _strict(tenant)
    wh, _s, v, cust = seed_basic(tenant)
    o = _order(api, wh, v, cust)
    issue_order(api, o["id"])
    _invoice(api, o)
    note = _note(tenant, o)
    r = api.post(f"/api/v1/delivery-notes/{note.pk}/deliver", {"receiver_name": "Shop"}, format="json")
    assert r.status_code == 422 and r.data["type"] == "trip_required", r.content
    assert set(r.data["errors"]) == {"driver", "delivery_rep"}

    driver = _driver(tenant)
    r = api.post(f"/api/v1/delivery-notes/{note.pk}/deliver", {"receiver_name": "Shop", "driver": str(driver.pk), "delivery_rep": str(tenant.owner.id)}, format="json")
    assert r.status_code == 200 and r.data["status"] == "delivered", r.content
    trip = api.get(f"/api/v1/trips/{r.data['trip']}").data
    assert trip["status"] == "completed" and trip["driver_name"] == "Akram", trip
    steps = {s["key"]: s for s in api.get(f"/api/v1/sales-orders/{o['id']}/workflow").data["steps"]}
    assert all(s["state"] == "done" for s in steps.values()), {k: s["state"] for k, s in steps.items()}
    assert "Akram" in steps["dispatch"]["detail"]


def test_a_refused_delivery_rolls_back_the_trip_made_for_it(api, tenant):
    _strict(tenant)
    wh, _s, v, cust = seed_basic(tenant)
    o = _order(api, wh, v, cust)
    issue_order(api, o["id"])                                   # issued, never invoiced
    note = _note(tenant, o)
    r = api.post(f"/api/v1/delivery-notes/{note.pk}/deliver", {"receiver_name": "Shop", "driver": str(_driver(tenant).pk), "delivery_rep": str(tenant.owner.id)}, format="json")
    assert r.status_code == 422 and r.data["type"] == "invoice_first", r.content
    assert api.get("/api/v1/trips").data["count"] == 0, "no trip is left behind"
    assert _note(tenant, o).status in ("pending", "loaded")


def test_mark_delivered_on_the_order_makes_the_trip_too(api, tenant):
    _strict(tenant)
    wh, _s, v, cust = seed_basic(tenant)
    o = _order(api, wh, v, cust)
    issue_order(api, o["id"])
    _invoice(api, o)
    assert api.post(f"/api/v1/sales-orders/{o['id']}/mark-delivered", {"receiver_name": "Shop"}, format="json").status_code == 422
    r = api.post(f"/api/v1/sales-orders/{o['id']}/mark-delivered", {"receiver_name": "Shop", "driver": str(_driver(tenant).pk), "delivery_rep": str(tenant.owner.id)}, format="json")
    assert r.status_code == 200 and r.data["status"] in ("delivered", "invoiced"), r.content
    assert _note(tenant, o).trip_id is not None


def test_a_clerk_without_trip_rights_is_told_to_ask(api, tenant):
    """Recording a delivery is one right, making a trip another: the trip on the spot needs the trips page's Add."""
    from apps.accounts.models import TenantMembership

    _strict(tenant)
    wh, _s, v, cust = seed_basic(tenant)
    o = _order(api, wh, v, cust)
    issue_order(api, o["id"])
    _invoice(api, o)
    note = _note(tenant, o)
    from unittest import mock

    real = TenantMembership.has_perm
    with mock.patch.object(TenantMembership, "has_perm", lambda self, c: False if c == "delivery.add_trip" else real(self, c)):
        r = api.post(f"/api/v1/delivery-notes/{note.pk}/deliver", {"receiver_name": "Shop", "driver": str(_driver(tenant).pk), "delivery_rep": str(tenant.owner.id)}, format="json")
    assert r.status_code == 403 and "put " + o["number"] + " on a trip" in r.data["detail"], r.content
