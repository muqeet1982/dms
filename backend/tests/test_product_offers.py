"""
The product's own discount, as the owner set it on 21 Oct 2026: the standard % waits for a minimum quantity,
and a dated offer (its own %, minimum and window) replaces the standard while it matches.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db


def test_the_discount_waits_for_its_minimum_and_a_dated_offer_overrides(tenant):
    from apps.catalog.models import ProductOffer
    from apps.sales.services import OrderService

    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        p = v.product
        p.discount_pct, p.discount_min_qty = Decimal("5"), Decimal("5")
        p.save(update_fields=["discount_pct", "discount_min_qty"])

        def line_pct(qty):
            o = OrderService.create(customer=cust, lines=[{"variant": v, "qty": Decimal(qty), "unit_price": None}], source="staff", warehouse=wh)
            return o.lines.first().product_discount_pct

        assert line_pct("2") == Decimal("0"), "below the minimum the standard % stays away"
        assert line_pct("5") == Decimal("5.000"), "at the minimum it applies"

        today = timezone.localdate()
        ProductOffer.objects.create(product=p, discount_pct=Decimal("8"), min_qty=Decimal("10"),
                                    start_date=today, end_date=today + timedelta(days=5))
        ProductOffer.objects.create(product=p, discount_pct=Decimal("50"), min_qty=Decimal("1"),
                                    start_date=today - timedelta(days=30), end_date=today - timedelta(days=10))

        assert line_pct("10") == Decimal("8.000"), "the live offer replaces the standard"
        assert line_pct("5") == Decimal("5.000"), "under the offer's minimum the standard still rules"
        assert line_pct("2") == Decimal("0"), "an expired offer is dead, whatever its minimum"


def test_the_product_form_add_saves_discount_and_offers(api, tenant):
    """The form posts discount_pct, discount_min_qty AND offers_write together on ADD — the create path
    bypasses serializer.save(), so the offers must be popped there (this 500'd on 24 Oct 2026)."""
    from apps.catalog.models import UnitOfMeasure
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        uom = str(UnitOfMeasure.objects.get(code="PC").id)
    r = api.post("/api/v1/products", {"sku": "OFR-ADD-1", "name": "Offer On Add", "base_uom": uom,
                                      "cost_price": "100", "sale_price": "150",
                                      "discount_pct": 5, "discount_min_qty": 12,
                                      "offers_write": [{"discount_pct": "9", "min_qty": "20",
                                                        "start_date": "2026-10-24", "end_date": "2026-11-30"}]},
                 format="json")
    assert r.status_code == 201, r.content
    assert str(r.data["discount_pct"]).startswith("5") and str(r.data["discount_min_qty"]).startswith("12")
    assert len(r.data["offers"]) == 1 and str(r.data["offers"][0]["discount_pct"]).startswith("9")


def test_the_datewise_discounts_page_lists_adds_and_edits_offers(api, tenant):
    """The separate Date-wise discounts page (25 Oct 2026): every product's offers in one list, with
    add / edit / delete there — the same rows the product form's offers editor writes."""
    from apps.core.tenancy import tenant_context

    from .conftest import seed_basic

    wh, _sup, v, _cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        pid = str(v.product_id)

    r = api.post("/api/v1/product-offers", {"product": pid, "discount_pct": "7.5", "min_qty": "10",
                                            "start_date": "2026-10-01", "end_date": "2026-12-31"}, format="json")
    assert r.status_code == 201, r.content
    offer_id = r.data["id"]
    assert r.data["product_name"], "the list shows the product, not a bare id"

    r = api.get("/api/v1/product-offers")
    rows = r.data["results"] if isinstance(r.data, dict) else r.data
    assert any(o["id"] == offer_id for o in rows)

    # the product form reads the same row
    r = api.get(f"/api/v1/products/{pid}")
    assert any(o["id"] == str(offer_id) for o in r.data["offers"])

    r = api.patch(f"/api/v1/product-offers/{offer_id}", {"discount_pct": "9"}, format="json")
    assert r.status_code == 200, r.content

    # bad rows are refused with a reason
    r = api.post("/api/v1/product-offers", {"product": pid, "discount_pct": "7", "min_qty": "0",
                                            "start_date": "2026-12-31", "end_date": "2026-10-01"}, format="json")
    assert r.status_code == 400 and "end" in str(r.data).lower()
    r = api.post("/api/v1/product-offers", {"product": pid, "discount_pct": "150", "min_qty": "0",
                                            "start_date": "2026-10-01", "end_date": "2026-12-31"}, format="json")
    assert r.status_code == 400

    r = api.delete(f"/api/v1/product-offers/{offer_id}")
    assert r.status_code in (200, 204)
    r = api.get(f"/api/v1/products/{pid}")
    assert r.data["offers"] == [], "a deleted offer leaves the product clean"
