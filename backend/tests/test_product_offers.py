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
