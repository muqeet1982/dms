"""
Stock levels searched by the product's name (demo, 12 Nov 2026): "ColorMax Cream Colour 1.0 Black 60m" found nothing,
because the item is named "ColorMax 1.0 Black 60ml" and the search read only the item's name, SKU, batch and barcode.
"""
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

from .conftest import seed_basic

pytestmark = pytest.mark.django_db


def test_the_product_name_finds_its_stock_movements_and_batches(api, tenant):
    wh, _s, v, _c = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        p = v.product
        p.name = "ColorMax Cream Colour 1.0 Black 60ml"
        p.save(update_fields=["name"])
        v.name = "ColorMax 1.0 Black 60ml"                    # the item keeps its shorter name
        v.save(update_fields=["name"])
    words = "ColorMax Cream Colour 1.0 Black 60m"
    levels = api.get("/api/v1/inventory/stock", {"search": words}).data["results"]
    assert [l["variant"] for l in levels] and {str(l["variant"]) for l in levels} == {str(v.id)}, levels
    assert sum(D(l["qty_on_hand"]) for l in levels) == 100
    moves = api.get("/api/v1/inventory/movements", {"search": words}).data
    assert (moves["results"] if isinstance(moves, dict) else moves), "the movements page finds it too"
    assert api.get("/api/v1/inventory/stock", {"search": "ColorMax 1.0 Black"}).data["results"], "the item's own name still works"
