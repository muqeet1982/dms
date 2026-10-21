"""
The stock audit (21 Oct 2026): every unit that goes out must be accounted for, and every cancel or return
must bring it home. This walks the whole sales lifecycle with the counter in hand — reservation, issue,
partial delivery with a return, a credit-note restock, and the cancel paths — then has recompute_levels
re-derive the levels from movements to prove the running cache never drifted.
"""
from decimal import Decimal

import pytest

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db


def _on_hand(variant, warehouse):
    from django.db.models import Sum

    from apps.inventory.models import StockLevel

    return StockLevel.objects.filter(variant=variant, warehouse=warehouse).aggregate(s=Sum("qty_on_hand"))["s"] or Decimal("0")


def _reserved(variant, warehouse):
    from django.db.models import Sum

    from apps.inventory.models import StockLevel

    return StockLevel.objects.filter(variant=variant, warehouse=warehouse).aggregate(s=Sum("qty_reserved"))["s"] or Decimal("0")


def test_stock_is_conserved_through_the_whole_sales_lifecycle(api, tenant):
    from apps.inventory.services import StockService

    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        base_on = _on_hand(v, wh)
        base_av = StockService.available(v, wh)

    # ── confirm: a reservation, not a movement ────────────────────────────────────────────────
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(v.id), "qty": "10"}]}, format="json")
    assert r.status_code == 201 and r.data["status"] == "confirmed"
    with tenant_context(tenant, tenant.owner):
        assert _on_hand(v, wh) == base_on, "confirming moves nothing physical"
        assert StockService.available(v, wh) == base_av - 10, "but the 10 are spoken for"

    # ── issue: now it is physical ─────────────────────────────────────────────────────────────
    pl = api.post(f"/api/v1/sales-orders/{r.data['id']}/load-form", {}, format="json")
    issues = [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl.data["lines"]]
    assert api.post(f"/api/v1/pick-lists/{pl.data['id']}/issue", {"issues": issues}, format="json").status_code == 200
    with tenant_context(tenant, tenant.owner):
        assert _on_hand(v, wh) == base_on - 10, "issued stock has left the warehouse"
        assert _reserved(v, wh) == 0, "the reservation was consumed by the issue, not left dangling"

    # ── deliver 7, 3 come back good: the trip completion books the return ────────────────────
    def rows_of(d):
        return d.get("results", d) if isinstance(d, dict) else d

    drivers = rows_of(api.get("/api/v1/drivers", format="json").data) or [api.post("/api/v1/drivers", {"name": "D", "phone": "0300"}, format="json").data]
    vehicles = rows_of(api.get("/api/v1/vehicles", format="json").data) or [api.post("/api/v1/vehicles", {"reg_no": "AUD-1", "type": "van"}, format="json").data]
    trip = api.post("/api/v1/trips", {"warehouse": str(wh.id), "driver": str(drivers[0]["id"]), "vehicle": str(vehicles[0]["id"]), "order_ids": [r.data["id"]]}, format="json")
    assert trip.status_code == 201, trip.content
    assert api.post(f"/api/v1/trips/{trip.data['id']}/start", {"odometer_start": 100}, format="json").status_code == 200
    dn_rows = api.get("/api/v1/delivery-notes", {"order": r.data["id"]}, format="json").data
    dn = (dn_rows.get("results", dn_rows))[0]
    dn_full = api.get(f"/api/v1/delivery-notes/{dn['id']}", format="json").data
    delivered = [{"line_id": l["id"], "qty_delivered": 7, "qty_returned": 3, "return_reason": "rejected", "return_condition": "good"} for l in dn_full["lines"]]
    assert api.post(f"/api/v1/delivery-notes/{dn['id']}/deliver", {"delivered": delivered, "receiver_name": "Audit", "cash_collected": 0}, format="json").status_code == 200
    done = api.post(f"/api/v1/trips/{trip.data['id']}/close", {"odometer_end": 120}, format="json")
    assert done.status_code == 200, done.content
    with tenant_context(tenant, tenant.owner):
        assert _on_hand(v, wh) == base_on - 7, "7 stayed with the customer, the 3 good returns are home"

    # ── a later credit-note return restocks 2 more ───────────────────────────────────────────
    with tenant_context(tenant, tenant.owner):
        from apps.sales.services import CreditNoteService

        cn = CreditNoteService.create(customer=cust, lines=[{"variant": v, "qty": "2", "unit_price": "150", "unit_cost": "100", "condition": "good", "restock": True}], reason="audit return")
        CreditNoteService.post(cn)
        assert _on_hand(v, wh) == base_on - 5, "2 of the 7 came back by credit note"
        assert StockService.available(v, wh) == base_av - 5, "and nothing is still reserved anywhere"

    # ── the running levels never drifted from the movement ledger ────────────────────────────
    with tenant_context(tenant, tenant.owner):
        before = _on_hand(v, wh)
        StockService.recompute_levels(tenant)
        assert _on_hand(v, wh) == before, "recomputing from movements lands on the same number"


def test_cancel_before_issue_frees_the_reservation_only(api, tenant):
    from apps.inventory.services import StockService

    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        base_on, base_av = _on_hand(v, wh), StockService.available(v, wh)
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(v.id), "qty": "6"}]}, format="json")
    with tenant_context(tenant, tenant.owner):
        assert StockService.available(v, wh) == base_av - 6
    assert api.post(f"/api/v1/sales-orders/{r.data['id']}/cancel", {"reason": "audit"}, format="json").status_code == 200
    with tenant_context(tenant, tenant.owner):
        assert _on_hand(v, wh) == base_on and StockService.available(v, wh) == base_av, "nothing moved, nothing held"


def test_a_short_issue_releases_what_the_warehouse_did_not_take(api, tenant):
    """
    THE leak the 21 Oct audit found live (TDN-SO-00034): issue 4 of 10 and the other 6 stayed "spoken for"
    forever — the order left `confirmed`, so even the expiry janitor refused to touch them. Now the issue
    itself releases whatever the warehouse did not take, and the janitor sweeps stragglers on any order
    that has moved on.
    """
    from apps.inventory.services import StockService
    from apps.sales.services import OrderService

    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        base_av = StockService.available(v, wh)

    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(v.id), "qty": "10"}]}, format="json")
    pl = api.post(f"/api/v1/sales-orders/{r.data['id']}/load-form", {}, format="json")
    issues = [{"line_id": l["id"], "qty_issued": "4"} for l in pl.data["lines"]]
    assert api.post(f"/api/v1/pick-lists/{pl.data['id']}/issue", {"issues": issues}, format="json").status_code == 200

    with tenant_context(tenant, tenant.owner):
        assert _reserved(v, wh) == 0, "the 6 the warehouse did not take are free again"
        assert StockService.available(v, wh) == base_av - 4, "only what actually left is gone"

        # and the janitor heals any historical stragglers the same way
        from apps.sales.models import SalesOrder, StockReservation

        order = SalesOrder.objects.get(pk=r.data["id"])
        line = order.lines.first()
        StockService.reserve(v, wh, 5)
        StockReservation.objects.create(order_line=line, warehouse=wh, qty=5, expires_at=timezone_now_plus_day())
        assert _reserved(v, wh) == 5
        released = OrderService.expire_reservations()
        assert released == 1 and _reserved(v, wh) == 0, "a reservation on a moved-on order is released on sight"


def timezone_now_plus_day():
    from datetime import timedelta

    from django.utils import timezone

    return timezone.now() + timedelta(days=1)
