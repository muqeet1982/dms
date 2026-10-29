"""
Owner, 29 Oct 2026: "product need delete button — soft delete so its related records should not delete — and an
option to undelete from a table somewhere". Delete archives the product (gone from lists, pickers and the rep app;
every document that names it keeps it); Products → Deleted products lists them and Restore brings one back.
"""
import pytest

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db


def test_deleting_a_product_hides_it_keeps_its_history_and_restore_brings_it_back(api, tenant):
    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    o = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "lines": [{"variant": str(v.id), "qty": "2"}]}, format="json")
    assert o.status_code == 201, o.content
    pid = str(v.product_id)

    assert api.delete(f"/api/v1/products/{pid}").status_code == 204
    listed = [p["id"] for p in api.get("/api/v1/products").data["results"]]
    assert pid not in listed, "a deleted product leaves the product list"
    deleted = [p["id"] for p in api.get("/api/v1/products", {"status": "archived"}).data["results"]]
    assert pid in deleted, "…and is in Deleted products"
    assert str(v.id) not in [x["id"] for x in api.get("/api/v1/variants", {"is_active": "true"}).data["results"]], "gone from the pickers"
    order = api.get(f"/api/v1/sales-orders/{o.data['id']}").data
    assert str(order["lines"][0]["variant"]) == str(v.id), "the order still names its product"
    assert api.get("/api/v1/products/stats").data["total"] == 0

    from apps.catalog.models import UnitOfMeasure

    with tenant_context(tenant):
        pc = str(UnitOfMeasure.objects.get(code="PC").id)
    again = api.post("/api/v1/products", {"name": "Same code", "upc": "P-1", "base_uom": pc, "sale_price": "10", "cost_price": "5"}, format="json")
    assert again.status_code == 400 and "deleted product" in str(again.content), "a re-add says where the code went"

    back = api.post(f"/api/v1/products/{pid}/restore", {}, format="json")
    assert back.status_code == 200, back.content
    assert back.data["status"] == "active"
    assert pid in [p["id"] for p in api.get("/api/v1/products").data["results"]]
    assert str(v.id) in [x["id"] for x in api.get("/api/v1/variants", {"is_active": "true"}).data["results"]]
    assert api.post(f"/api/v1/products/{pid}/restore", {}, format="json").status_code == 400, "only a deleted one restores"
