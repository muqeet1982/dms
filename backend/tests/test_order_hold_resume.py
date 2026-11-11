"""
The office's Hold (owner, 12 Nov 2026): DD-SO-00052 was held with the Hold button and read "On hold (credit)". A Hold is
its own status, "On hold", the button turns into Resume, and Resume puts the order back where Hold found it. The credit
check's own hold keeps "On hold (credit)".
"""
import pytest

from apps.core.tenancy import tenant_context

from .conftest import seed_basic

pytestmark = pytest.mark.django_db


def _open_order(api, wh, v, cust):
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "lines": [{"variant": str(v.id), "qty": "2"}]}, format="json")
    assert r.status_code == 201, r.content
    if r.data["status"] == "draft":
        assert api.post(f"/api/v1/sales-orders/{r.data['id']}/submit", {}, format="json").status_code == 200
    return api.get(f"/api/v1/sales-orders/{r.data['id']}").data


def test_hold_then_resume_puts_the_order_back(api, tenant):
    from apps.sales.models import SalesOrder

    wh, _s, v, cust = seed_basic(tenant)
    o = _open_order(api, wh, v, cust)
    was = o["status"]
    r = api.post(f"/api/v1/sales-orders/{o['id']}/hold", {"reason": "customer asked to wait"}, format="json")
    assert r.status_code == 200 and r.data["status"] == "on_hold", r.content
    with tenant_context(tenant):
        held = SalesOrder.objects.get(pk=o["id"])
        assert held.get_status_display() == "On hold", "not (credit)"
        assert held.held_from == was and held.hold_reason == "customer asked to wait"

    assert o["id"] in [x["id"] for x in api.get("/api/v1/sales-orders/inbox").data["results"]], "still in the inbox"
    steps = {s["key"]: s for s in api.get(f"/api/v1/sales-orders/{o['id']}/workflow").data["steps"]}
    assert steps["order"]["action"]["label"] == "Resume"

    r = api.post(f"/api/v1/sales-orders/{o['id']}/confirm", {}, format="json")
    assert r.status_code == 422 and r.data["type"] == "on_hold", "resume it first"

    r = api.post(f"/api/v1/sales-orders/{o['id']}/resume", {}, format="json")
    assert r.status_code == 200 and r.data["status"] == was, r.content
    with tenant_context(tenant):
        back = SalesOrder.objects.get(pk=o["id"])
        assert back.held_from == "" and back.hold_reason == ""
    assert api.post(f"/api/v1/sales-orders/{o['id']}/confirm", {}, format="json").data["status"] == "confirmed"


def test_under_review_comes_back_under_review(api, tenant):
    wh, _s, v, cust = seed_basic(tenant)
    o = _open_order(api, wh, v, cust)
    assert api.post(f"/api/v1/sales-orders/{o['id']}/review", {}, format="json").data["status"] == "under_review"
    api.post(f"/api/v1/sales-orders/{o['id']}/hold", {"reason": "x"}, format="json")
    assert api.post(f"/api/v1/sales-orders/{o['id']}/resume", {}, format="json").data["status"] == "under_review"


def test_a_confirmed_order_is_not_held_and_a_held_one_cancels(api, tenant):
    wh, _s, v, cust = seed_basic(tenant)
    c = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": "1"}]}, format="json").data
    r = api.post(f"/api/v1/sales-orders/{c['id']}/hold", {"reason": "x"}, format="json")
    assert r.status_code == 422 and r.data["type"] == "not_holdable", r.content
    assert api.post(f"/api/v1/sales-orders/{c['id']}/resume", {}, format="json").status_code == 422

    o = _open_order(api, wh, v, cust)
    api.post(f"/api/v1/sales-orders/{o['id']}/hold", {"reason": "x"}, format="json")
    r = api.post(f"/api/v1/sales-orders/{o['id']}/cancel", {"reason": "customer asked"}, format="json")
    assert r.status_code == 200 and r.data["status"] == "cancelled", r.content


def test_the_credit_check_still_holds_as_credit(api, tenant):
    from decimal import Decimal as D

    from apps.partners.models import Customer

    wh, _s, v, _c = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        tight = Customer.objects.create(code="C-9", name="Tight Shop", credit_limit=D("1"))
    r = api.post("/api/v1/sales-orders", {"customer": str(tight.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": "2"}]}, format="json")
    assert r.status_code == 201 and r.data["status"] == "on_hold_credit", r.content
