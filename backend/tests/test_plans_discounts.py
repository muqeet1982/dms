"""Plan pricing (monthly/yearly, setup fee), add-ons and the per-tenant negotiated discount."""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_api(admin_user):
    c = APIClient()
    r = c.post("/api/v1/auth/login", {"identifier": "admin-test", "password": "pak1stan"}, format="json")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
    return c


def subscription_of(tenant):
    return tenant.subscriptions.order_by("-created_at").first()


def test_price_breakdown_covers_plan_addons_and_discount(admin_api, tenant):
    from apps.platform.models import AddOn, SubscriptionAddOn
    from apps.platform.services import SubscriptionService

    sub = subscription_of(tenant)
    sub.plan_snapshot = {**sub.plan_snapshot, "price": "10000", "price_annual": "100000", "currency": "PKR", "name": "Growth"}
    sub.save(update_fields=["plan_snapshot"])
    seats = AddOn.objects.create(code="extra-users", name="Extra users", unit_price=Decimal("500"), unit_label="user", limit_key="max_users", kind="recurring")
    setup = AddOn.objects.create(code="setup", name="Onboarding", unit_price=Decimal("20000"), unit_label="session", kind="one_time")
    SubscriptionAddOn.objects.create(subscription=sub, addon=seats, quantity=4)
    SubscriptionAddOn.objects.create(subscription=sub, addon=setup, quantity=1)

    b = SubscriptionService.price_breakdown(sub)
    assert [Decimal(b[k]) for k in ("plan_total", "addons_total", "total")] == [Decimal("10000"), Decimal("22000"), Decimal("32000")]

    # a yearly subscription uses the yearly price, and one-off add-ons are not multiplied by the periods
    sub.billing_cycle = "yearly"
    sub.save(update_fields=["billing_cycle"])
    b = SubscriptionService.price_breakdown(sub, periods=2)
    assert [Decimal(b[k]) for k in ("plan_total", "addons_total")] == [Decimal("200000"), Decimal("24000")]  # 500×4×2 + 20000×1


def test_percent_and_fixed_discounts_apply_to_invoices(admin_api, tenant):
    from apps.platform.models import PlatformInvoice
    from apps.platform.services import BillingService, SubscriptionService

    sub = subscription_of(tenant)
    sub.plan_snapshot = {**sub.plan_snapshot, "price": "10000", "currency": "PKR", "name": "Growth"}
    sub.save(update_fields=["plan_snapshot"])

    r = admin_api.post(f"/api/v1/platform/subscriptions/{sub.id}/discount", {"type": "percent", "value": 20, "cycles": 2, "reason": "Launch offer"}, format="json")
    assert r.status_code == 200 and r.data["discount_active"] is True and Decimal(r.data["price_breakdown"]["total"]) == Decimal("8000")
    assert r.data["discount_label"] == "20% off · 2 invoice(s)"

    sub.refresh_from_db()
    inv1 = BillingService.create_invoice(tenant, sub, date.today(), date.today() + timedelta(days=30))
    assert inv1.discount == Decimal("2000") and inv1.total == Decimal("8000")
    sub.refresh_from_db()
    inv2 = BillingService.create_invoice(tenant, sub, date.today(), date.today() + timedelta(days=30))
    assert inv2.total == Decimal("8000")
    sub.refresh_from_db()
    assert sub.discount_active is False  # both allowed invoices used
    inv3 = BillingService.create_invoice(tenant, sub, date.today(), date.today() + timedelta(days=30))
    assert inv3.discount == Decimal("0") and inv3.total == Decimal("10000")
    assert PlatformInvoice.objects.count() >= 3

    # fixed amount, and the guard rails
    SubscriptionService.set_discount(sub, type="fixed", value=1500, reason="Goodwill")
    sub.refresh_from_db()
    assert Decimal(SubscriptionService.price_breakdown(sub)["total"]) == Decimal("8500")
    with pytest.raises(Exception):
        SubscriptionService.set_discount(sub, type="percent", value=120)
    with pytest.raises(Exception):
        SubscriptionService.set_discount(sub, type="fixed", value=99999)

    # removing it
    assert admin_api.delete(f"/api/v1/platform/subscriptions/{sub.id}/discount").data["discount_active"] is False


def test_discount_respects_an_end_date(admin_api, tenant):
    from apps.platform.services import SubscriptionService

    sub = subscription_of(tenant)
    SubscriptionService.set_discount(sub, type="percent", value=10, until=date.today() - timedelta(days=1))
    sub.refresh_from_db()
    assert sub.discount_active is False and sub.discount_for(Decimal("1000")) == Decimal("0")
    SubscriptionService.set_discount(sub, type="percent", value=10, until=date.today() + timedelta(days=30))
    sub.refresh_from_db()
    assert sub.discount_active is True and sub.discount_for(Decimal("1000")) == Decimal("100")


def test_tenant_list_carries_what_the_admin_list_shows(admin_api, tenant):
    r = admin_api.get("/api/v1/platform/tenants")
    row = next(t for t in r.data["results"] if t["id"] == str(tenant.id))
    assert set(row["usage"]) == {"users", "products"} and "outstanding" in row and "white_label" in row
    assert row["subscription"]["price_breakdown"]["total"]
    assert admin_api.get("/api/v1/platform/tenants", {"expiring": 1}).status_code == 200
    assert admin_api.get("/api/v1/platform/tenants", {"white_label": 1}).data["count"] == 0
