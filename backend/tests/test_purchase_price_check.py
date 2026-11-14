"""
14 Nov 2026: TDN-PO-00003 put CREAM BLEACH 50 G (D.P 684) in stock at 14.25 a piece — 684 entered on a carton line of 48
is the carton's price — and every sale of it was costed at that. A purchase order price a piece under half or over
twice the product's D.P is refused until the person confirms it (`confirm_prices: true`).
"""
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

from .conftest import seed_basic

pytestmark = pytest.mark.django_db


def _po(api, sup, wh, lines, **extra):
    return api.post("/api/v1/purchase-orders", {"supplier": str(sup.id), "warehouse": str(wh.id), "lines": lines, **extra}, format="json")


def test_a_price_far_from_the_dp_waits_for_a_confirmation(api, tenant):
    wh, sup, v, _c = seed_basic(tenant)                          # D.P (cost) 100
    for price in ("40", "250"):
        r = _po(api, sup, wh, [{"variant": str(v.id), "qty": "5", "unit_price": price}])
        assert r.status_code == 422 and r.data["type"] == "price_check", r.content
        assert "D.P 100.00" in r.data["detail"], r.data["detail"]
    r = _po(api, sup, wh, [{"variant": str(v.id), "qty": "5", "unit_price": "40"}], confirm_prices=True)
    assert r.status_code == 201 and D(r.data["lines"][0]["unit_price"]) == D("40")
    assert _po(api, sup, wh, [{"variant": str(v.id), "qty": "5", "unit_price": "95"}]).status_code == 201, "a normal price goes straight through"
    assert _po(api, sup, wh, [{"variant": str(v.id), "qty": "5", "unit_price": "0"}]).status_code == 201, "a free line is not checked"


def test_the_carton_mistake_is_caught(api, tenant):
    from apps.catalog.models import Packaging, UnitOfMeasure

    wh, sup, v, _c = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        ctn, _ = UnitOfMeasure.objects.get_or_create(code="CTN", defaults={"name": "Carton"})
        box = Packaging.objects.create(variant=v, name="carton", uom=ctn, qty_in_base=D("48"))
    line = {"variant": str(v.id), "packaging": str(box.id), "qty": "10", "price_per_packaging": True}
    r = _po(api, sup, wh, [{**line, "unit_price": "100"}])           # the piece price typed on a carton line
    assert r.status_code == 422 and r.data["type"] == "price_check" and "2.08 a piece" in r.data["detail"], r.content
    r = _po(api, sup, wh, [{**line, "unit_price": "4800"}])          # the carton's price
    assert r.status_code == 201 and D(r.data["lines"][0]["unit_price"]) == D("100") and D(r.data["lines"][0]["qty_ordered"]) == D("480")


def test_an_edit_is_checked_too(api, tenant):
    wh, sup, v, _c = seed_basic(tenant)
    po = _po(api, sup, wh, [{"variant": str(v.id), "qty": "5", "unit_price": "100"}]).data
    r = api.patch(f"/api/v1/purchase-orders/{po['id']}", {"lines": [{"variant": str(v.id), "qty": "5", "unit_price": "10"}]}, format="json")
    assert r.status_code == 422 and r.data["type"] == "price_check", r.content
    assert D(api.get(f"/api/v1/purchase-orders/{po['id']}").data["lines"][0]["unit_price"]) == D("100"), "nothing saved"
    r = api.patch(f"/api/v1/purchase-orders/{po['id']}", {"lines": [{"variant": str(v.id), "qty": "5", "unit_price": "10"}], "confirm_prices": True}, format="json")
    assert r.status_code == 200 and D(r.data["lines"][0]["unit_price"]) == D("10")
