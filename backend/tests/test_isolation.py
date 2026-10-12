"""Cross-tenant isolation: tenant A must never see tenant B rows (§11)."""
import pytest

from apps.core.tenancy import tenant_context

from .conftest import seed_basic

pytestmark = pytest.mark.django_db

LIST_ENDPOINTS = ["/api/v1/products", "/api/v1/variants", "/api/v1/customers", "/api/v1/suppliers", "/api/v1/settings/warehouses", "/api/v1/inventory/stock", "/api/v1/inventory/movements", "/api/v1/sales-orders", "/api/v1/accounting/accounts", "/api/v1/accounting/journals"]


def test_lists_are_tenant_scoped(api, tenant, tenant_b):
    wh_a, sup_a, v_a, cust_a = seed_basic(tenant)
    wh_b, sup_b, v_b, cust_b = seed_basic(tenant_b)
    own = {"/api/v1/variants": v_a.id, "/api/v1/customers": cust_a.id, "/api/v1/suppliers": sup_a.id, "/api/v1/settings/warehouses": wh_a.id}
    for url in LIST_ENDPOINTS:
        r = api.get(url)
        assert r.status_code == 200, (url, r.content)
        rows = r.data["results"] if isinstance(r.data, dict) and "results" in r.data else r.data
        ids = {str(row.get("id")) for row in rows}
        assert str(v_b.id) not in ids and str(cust_b.id) not in ids and str(wh_b.id) not in ids, url
        if url in own:
            assert str(own[url]) in ids, f"{url} must list the tenant's own rows"
        if url in ("/api/v1/accounting/accounts", "/api/v1/inventory/stock", "/api/v1/inventory/movements"):
            assert len(rows) > 0, f"{url} should not be empty"


def test_detail_of_other_tenant_is_404(api, tenant, tenant_b):
    seed_basic(tenant)
    wh_b, sup_b, v_b, cust_b = seed_basic(tenant_b)
    for url in [f"/api/v1/customers/{cust_b.id}", f"/api/v1/variants/{v_b.id}", f"/api/v1/suppliers/{sup_b.id}", f"/api/v1/settings/warehouses/{wh_b.id}"]:
        assert api.get(url).status_code == 404, url


def test_wrong_tenant_header_is_forbidden(api, tenant, tenant_b):
    api.credentials(HTTP_AUTHORIZATION=api._credentials["HTTP_AUTHORIZATION"], HTTP_X_TENANT_ID=str(tenant_b.id))
    r = api.get("/api/v1/customers")
    assert r.status_code == 403


def test_tenant_manager_returns_nothing_without_context(tenant):
    from apps.partners.models import Customer

    seed_basic(tenant)
    assert Customer.objects.count() == 0
    with tenant_context(tenant):
        assert Customer.objects.count() == 1
