"""
A product renamed renames its item (TDN, 11 Nov 2026): VELVET CLOUD became VELVET CLOUD 200ML / 500ML / 75ML, and the
order and purchase order searches, which list items, still read VELVET CLOUD for all three. A product with several items
(shades) keeps their own names.
"""
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db


def _product(tenant, sku, name):
    from apps.catalog.models import UnitOfMeasure
    from apps.catalog.services import ProductService

    with tenant_context(tenant, tenant.owner):
        return ProductService.create_product(tenant, {"sku": sku, "name": name, "base_uom": UnitOfMeasure.objects.get(code="PC"), "cost_price": D("100"), "sale_price": D("150"), "unit_size": "200ml"})


def test_renaming_a_product_renames_its_item(api, tenant):
    p = _product(tenant, "27493", "VELVET CLOUD")
    r = api.patch(f"/api/v1/products/{p.pk}", {"name": "VELVET CLOUD 200ML"}, format="json")
    assert r.status_code == 200, r.content
    found = api.get("/api/v1/variants", {"search": "VELVET", "is_active": "true", "with_stock": 1}).data["results"]
    assert [v["name"] for v in found] == ["VELVET CLOUD 200ML"]
    assert found[0]["stock_on_hand"] in (None, "0", "0.000"), "no stock row: the search shows stock 0"


def test_a_product_with_shades_keeps_their_names(api, tenant):
    from apps.catalog.services import ProductService

    p = _product(tenant, "C-1", "Colour")
    with tenant_context(tenant, tenant.owner):
        first = p.variants.first()
        first.name = "Colour 6.1"
        first.save(update_fields=["name"])
        ProductService.add_variant(p, {"sku": "C-1-7", "name": "Colour 7.1", "cost_price": D("100"), "sale_price": D("150")})
    assert api.patch(f"/api/v1/products/{p.pk}", {"name": "Cream Colour"}, format="json").status_code == 200
    with tenant_context(tenant):
        assert sorted(p.variants.values_list("name", flat=True)) == ["Colour 6.1", "Colour 7.1"]
