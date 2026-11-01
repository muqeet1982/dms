"""
Owner, 1 Nov 2026: a purchase order's due date and a supplier bill's due date default to 45 days; a product is due for
reorder below 30% of its max stock; an order on stock hold can be confirmed with the stock there is; and the company's
rules (Settings → Rules) are actually saved.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db


def test_po_due_date_defaults_to_45_days(api, tenant):
    from .conftest import seed_basic

    wh, sup, v, _c = seed_basic(tenant)
    r = api.post("/api/v1/purchase-orders", {"supplier": str(sup.id), "warehouse": str(wh.id), "lines": [{"variant": str(v.id), "qty": "5", "unit_price": "100"}]}, format="json")
    assert r.status_code == 201, r.content
    assert r.data["expected_date"] == str(timezone.localdate() + timedelta(days=45))


def test_reorder_at_30_percent_of_max_stock(api, tenant):
    from apps.catalog.models import Product
    from apps.inventory.services import AlertService

    from .conftest import seed_basic

    _wh, _sup, v, _c = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        on_hand = sum((s.qty_on_hand for s in v.stock_levels.all()), Decimal("0"))
        Product.objects.filter(pk=v.product_id).update(max_stock=on_hand * 4, min_stock=0)   # 30% of 4× = 1.2× on hand: due
        rows = AlertService.reorder_suggestions()
    assert [r["sku"] for r in rows] == [v.sku] and Decimal(rows[0]["suggested_qty"]) == on_hand * 3
    assert api.get("/api/v1/products/stats").data["low_stock"] == 1
    with tenant_context(tenant, tenant.owner):
        Product.objects.filter(pk=v.product_id).update(max_stock=on_hand * 3)               # 30% of 3× = 0.9×: not due
        assert AlertService.reorder_suggestions() == []


def test_the_rules_tab_saves_its_rules(api, tenant):
    r = api.patch("/api/v1/settings/company", {"settings": {"credit_policy": "hard", "count_tolerance_pct": "5", "reorder_point_pct": 25, "purchase_due_days": 60}}, format="json")
    assert r.status_code == 200, r.content
    tenant.refresh_from_db()
    assert tenant.settings["credit_policy"] == "hard" and tenant.settings["reorder_point_pct"] == 25 and tenant.settings["purchase_due_days"] == 60


def test_a_stock_hold_is_confirmed_with_the_stock_there_is(api, tenant):
    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        have = sum((s.qty_on_hand for s in v.stock_levels.all()), Decimal("0"))
    o = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": str(have + 5)}]}, format="json").data
    assert o["status"] == "on_hold_stock" and "short 5" in o["hold_reason"]
    again = api.post(f"/api/v1/sales-orders/{o['id']}/confirm", {}, format="json").data
    assert again["status"] == "on_hold_stock", "plain Confirm puts it back on hold"
    ok = api.post(f"/api/v1/sales-orders/{o['id']}/confirm", {"allow_partial": True}, format="json")
    assert ok.status_code == 200 and ok.data["status"] == "confirmed"
    assert ok.data["lines"][0]["qty_allocated"] == f"{have:.3f}", "what is there is held for it; the 5 short stay on the order"
