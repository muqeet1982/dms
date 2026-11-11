"""
A product's page shows its stock (TDN, 12 Nov 2026): FREESTYLER 500ML and COLOR BOWL (IMP) read 4 in the product list
and 0 on their own pages, because the page's Stock on hand card added up a figure the product endpoint never sent.
"""
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

from .conftest import seed_basic

pytestmark = pytest.mark.django_db


def test_the_product_page_shows_what_the_list_shows(api, tenant):
    from apps.inventory.models import Warehouse
    from apps.inventory.services import StockService

    wh, _s, v, _c = seed_basic(tenant)                          # 100 pcs in Main
    with tenant_context(tenant, tenant.owner):
        other = Warehouse.objects.create(code="W2", name="Second", type="main")
        StockService.move(warehouse=other, variant=v, qty=D("4"), movement_type="opening", unit_cost=D("100"))
    page = api.get(f"/api/v1/products/{v.product_id}")
    assert page.status_code == 200, page.content
    assert D(page.data["stock_on_hand"]) == D("104"), "both warehouses"
    listed = api.get("/api/v1/products", {"search": "Colour 7.1", "with_stock": 1}).data["results"]
    assert [D(x["stock_on_hand"]) for x in listed] == [D("104")], "the list and the page agree"


def test_a_product_with_no_stock_reads_none_not_an_error(api, tenant):
    from apps.catalog.models import UnitOfMeasure
    from apps.catalog.services import ProductService

    with tenant_context(tenant, tenant.owner):
        p = ProductService.create_product(tenant, {"sku": "27493", "name": "VELVET CLOUD", "base_uom": UnitOfMeasure.objects.get(code="PC"), "cost_price": D("100"), "sale_price": D("150")})
    r = api.get(f"/api/v1/products/{p.pk}")
    assert r.status_code == 200 and r.data["stock_on_hand"] is None, r.content
