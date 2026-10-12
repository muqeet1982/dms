import pytest
from decimal import Decimal

from apps.core.tenancy import tenant_context


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        from apps.accounts.services import ensure_permission_catalog
        from apps.platform.services import seed_platform_defaults

        ensure_permission_catalog()
        seed_platform_defaults()


@pytest.fixture(autouse=True)
def _reset_throttles():
    """DRF throttle counters live in the (in-memory, see settings_test) cache; the suite logs in far more than 20×/min from one IP."""
    from django.core.cache import cache

    cache.clear()
    yield


@pytest.fixture
def admin_user(db):
    from apps.accounts.models import User

    u = User(username="admin-test", email="admin-test@x.pk", full_name="Admin", user_type="PLATFORM_ADMIN", is_superuser=True)
    u.set_password("pak1stan")
    u.save()
    return u


def make_tenant(slug, admin):
    from apps.platform.services import TenantService

    return TenantService.create_tenant(legal_name=f"{slug} Ltd", display_name=slug.title(), slug=slug, owner_name=f"{slug} owner", owner_email=f"owner@{slug}.pk", owner_password="owner123", created_by=admin, send_invite=False)


@pytest.fixture
def tenant(db, admin_user):
    return make_tenant("alpha", admin_user)


@pytest.fixture
def tenant_b(db, admin_user):
    return make_tenant("beta", admin_user)


@pytest.fixture
def api(tenant):
    """Authenticated API client for the tenant owner."""
    from rest_framework.test import APIClient

    c = APIClient()
    r = c.post("/api/v1/auth/login", {"identifier": f"owner@{tenant.slug}.pk", "password": "owner123"}, format="json")
    assert r.status_code == 200, r.content
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}", HTTP_X_TENANT_ID=str(tenant.id))
    return c


def seed_basic(tenant):
    """Warehouse, supplier, product with stock, customer — inside tenant context."""
    from apps.catalog.models import Barcode, UnitOfMeasure
    from apps.catalog.services import ProductService
    from apps.inventory.models import Warehouse
    from apps.inventory.services import StockService
    from apps.partners.models import Customer, Supplier

    with tenant_context(tenant, tenant.owner):
        wh = Warehouse.objects.create(code="MAIN", name="Main", type="main")
        sup = Supplier.objects.create(code="S-1", name="Maker")
        pc = UnitOfMeasure.objects.get(code="PC")
        p = ProductService.create_product(tenant, {"sku": "P-1", "name": "Colour 7.1", "supplier": sup, "base_uom": pc, "cost_price": Decimal("100"), "sale_price": Decimal("150")})
        v = p.variants.first()
        Barcode.objects.create(variant=v, code="1234567890123", type="EAN13", is_primary=True)
        StockService.move(warehouse=wh, variant=v, qty=Decimal("100"), movement_type="opening", unit_cost=Decimal("100"))
        cust = Customer.objects.create(code="C-1", name="Salon One", credit_limit=Decimal("100000"))
        return wh, sup, v, cust
