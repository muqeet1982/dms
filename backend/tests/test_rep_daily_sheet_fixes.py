"""
Owner, 24 Nov 2026 ("check and fix Sale by Sale Person and Product by Sale Person"): the daily sheet counted a fully
returned order as a sale, and an order taken in the office (no rep of its own) went to "(No Rep)" although its trip's sales
rep sold it — every other sales report gives it to that rep.
"""
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

from .conftest import seed_basic

pytestmark = pytest.mark.django_db


def test_the_daily_sheet_reads_the_trip_rep_and_leaves_returned_orders_out(api, tenant):
    from apps.delivery.models import DeliveryTrip
    from apps.partners.models import SupplierRep
    from apps.sales.models import SalesOrder

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        rep = SupplierRep.objects.create(supplier=sup, code="R-T", name="Trip Rep", phone="03003330000")
        trip = DeliveryTrip.objects.create(number="TRIP-T1", warehouse=wh, rep=rep)
    mk = lambda q: api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "lines": [{"variant": str(v.id), "qty": q}]}, format="json").data
    kept, back = mk("2"), mk("1")                                  # 300 and 150, office orders with no rep
    with tenant_context(tenant, tenant.owner):
        SalesOrder.objects.filter(pk__in=[kept["id"], back["id"]]).update(trip=trip)
        SalesOrder.objects.filter(pk=back["id"]).update(status="returned")
    rows = api.get("/api/v1/reports/rep_daily_sheet", {"rep": str(rep.id)}).data["rows"]
    day = [r for r in rows if r.get("kind") != "subtotal"]
    assert len(day) == 1 and D(day[0]["total_today"]) == D("300"), rows
    assert day[0]["productive_count"] == "1" and day[0]["visited_count"] == "1", "the returned order is a visit, not a sale"
    everyone = api.get("/api/v1/reports/rep_daily_sheet").data["rows"]
    assert {r["name"] for r in everyone if r.get("kind") != "subtotal"} == {"Trip Rep"}, "not under (No Rep)"
