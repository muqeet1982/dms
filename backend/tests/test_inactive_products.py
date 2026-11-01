"""
Inactive products (owner, 1 Nov 2026: "by default active products should show in product list", "inactive products
should not show in sale order search item, PO etc", "message mobile ai"): the product list opens on active ones, the
item search on orders and purchases offers only active products, stock work can still reach an inactive product's
shelf, and the rep app is refused one and told it has gone.
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.tenancy import tenant_context

from .test_rep_order_edit import _order, rep_app  # noqa: F401  (rep_app is a fixture)

pytestmark = pytest.mark.django_db


def _set_status(tenant, variant, status):
    from apps.catalog.models import Product

    with tenant_context(tenant, tenant.owner):
        p = Product.objects.get(pk=variant.product_id)
        p.status = status
        p.save()   # updated_at moves, as an edit on the product page does


def test_the_list_opens_on_active_and_the_item_search_hides_inactive(api, tenant):
    from .conftest import seed_basic

    _wh, _sup, v, _c = seed_basic(tenant)
    _set_status(tenant, v, "inactive")
    names = lambda r: [p["id"] for p in r.data["results"]]  # noqa: E731
    assert str(v.product_id) not in names(api.get("/api/v1/products")), "active only by default"
    assert str(v.product_id) in names(api.get("/api/v1/products?status_view=inactive"))
    assert str(v.product_id) in names(api.get("/api/v1/products?status_view=all"))
    search = api.get("/api/v1/variants?is_active=true").data["results"]
    assert str(v.id) not in [x["id"] for x in search], "orders, POs, invoices and returns do not offer it"
    stock = api.get("/api/v1/variants?is_active=true&include_inactive=1&with_stock=1").data["results"]
    assert str(v.id) in [x["id"] for x in stock], "an adjustment or transfer can still reach its shelf"
    _set_status(tenant, v, "active")
    assert str(v.id) in [x["id"] for x in api.get("/api/v1/variants?is_active=true").data["results"]]


def test_the_rep_app_is_refused_an_inactive_product_and_told_it_has_gone(rep_app):
    tenant, v, phone = rep_app["tenant"], rep_app["variant"], rep_app["phone"]
    since = (timezone.now() - timedelta(minutes=1)).isoformat()
    made = _order(rep_app, qty=2)
    assert made.status_code == 201, made.content
    _set_status(tenant, v, "inactive")
    r = _order(rep_app, qty=1)
    assert r.status_code == 400 and r.data["type"] == "product_inactive" and "no longer sold" in r.data["detail"]
    fixed = phone.patch(f"/ext/v1/orders/{made.data['id']}", {"lines": [{"variant_id": str(v.id), "qty": 3}]}, format="json")
    assert fixed.status_code == 200, "an order that already has it can still be corrected"
    sync = phone.get("/ext/v1/products", {"since": since})
    assert sync.status_code == 200, sync.content
    sync = sync.data
    assert str(v.id) not in [x["id"] for x in sync["results"]] and str(v.id) in sync["removed"]
