"""
Owner, 14 Nov 2026: "when edit product and then create sale order it should always pick r.p". A salon's order takes the
product's R.P as it stands after the product is edited, on a new order and on an order being edited; the order form now
sends only a price somebody typed (no unit_price key otherwise), so every other line is priced here. A carton line edited keeps its cartons.
"""
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

from .conftest import seed_basic

pytestmark = pytest.mark.django_db


def _salon(tenant):
    from apps.partners.models import Customer

    with tenant_context(tenant, tenant.owner):
        return Customer.objects.create(code="C-SAL", name="Shine Salon", type="salon", credit_limit=D("100000"))


def _order(api, wh, cust, lines):
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "lines": lines}, format="json")
    assert r.status_code == 201, r.content
    return r.data


def test_a_product_edit_moves_the_rp_a_salon_order_takes(api, tenant):
    wh, _s, v, retailer = seed_basic(tenant)                    # T.P 150
    salon = _salon(tenant)
    r = api.patch(f"/api/v1/products/{v.product_id}", {"retail_price": "200"}, format="json")
    assert r.status_code == 200, r.content
    assert D(api.get(f"/api/v1/variants/{v.id}/price", {"customer": str(salon.id)}).data["unit_price"]) == D("200")

    first = _order(api, wh, salon, [{"variant": str(v.id), "qty": "2"}])
    assert D(first["lines"][0]["unit_price"]) == D("200") and first["lines"][0]["requested_price"] is None
    assert D(_order(api, wh, retailer, [{"variant": str(v.id), "qty": "1"}])["lines"][0]["unit_price"]) == D("150"), "a retailer keeps the T.P"

    # the R.P edited again: the next order takes the new one, and an order being edited follows it too
    assert api.patch(f"/api/v1/products/{v.product_id}", {"retail_price": "220"}, format="json").status_code == 200
    assert D(_order(api, wh, salon, [{"variant": str(v.id), "qty": "1"}])["lines"][0]["unit_price"]) == D("220")
    r = api.patch(f"/api/v1/sales-orders/{first['id']}", {"lines": [{"variant": str(v.id), "qty": "3"}]}, format="json")
    assert r.status_code == 200, r.content
    assert D(r.data["lines"][0]["unit_price"]) == D("220") and D(r.data["total"]) == D("660")


def test_a_typed_price_is_still_kept(api, tenant):
    wh, _s, v, _c = seed_basic(tenant)
    salon = _salon(tenant)
    assert api.patch(f"/api/v1/products/{v.product_id}", {"retail_price": "200"}, format="json").status_code == 200
    o = _order(api, wh, salon, [{"variant": str(v.id), "qty": "1", "unit_price": "190"}])
    assert D(o["lines"][0]["unit_price"]) == D("190") and D(o["lines"][0]["requested_price"]) == D("190")


def test_editing_a_carton_line_keeps_its_cartons(api, tenant):
    from apps.catalog.models import Packaging, UnitOfMeasure

    wh, _s, v, _c = seed_basic(tenant)
    salon = _salon(tenant)
    assert api.patch(f"/api/v1/products/{v.product_id}", {"retail_price": "200"}, format="json").status_code == 200
    with tenant_context(tenant, tenant.owner):
        ctn, _ = UnitOfMeasure.objects.get_or_create(code="CTN", defaults={"name": "Carton"})
        box = Packaging.objects.create(variant=v, name="carton", uom=ctn, qty_in_base=D("12"))
    line = {"variant": str(v.id), "packaging": str(box.id), "qty": "2", "price_per_packaging": True}
    o = _order(api, wh, salon, [line])
    assert D(o["lines"][0]["qty_ordered"]) == D("24") and D(o["lines"][0]["unit_price"]) == D("200")
    # the form sends the line back as it shows it: 2 cartons
    r = api.patch(f"/api/v1/sales-orders/{o['id']}", {"lines": [line]}, format="json")
    assert r.status_code == 200, r.content
    assert D(r.data["lines"][0]["qty_ordered"]) == D("24") and D(r.data["total"]) == D("4800")
