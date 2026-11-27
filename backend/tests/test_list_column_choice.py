"""
Every list has the Columns switch (owner, 27 Nov 2026: "add column selection for all reports and for all table and pdf or
print or csv excel"). The page asks the list which columns its files carry, and a column switched off is left out of the
Print, the PDF, the Excel and the CSV.
"""
import csv
import io

import pytest

from .test_list_exports import excel_on  # noqa: F401

pytestmark = pytest.mark.django_db


def _order(api, tenant):
    from .conftest import seed_basic

    wh, _sup, variant, customer = seed_basic(tenant)
    r = api.post("/api/v1/sales-orders", {"customer": str(customer.id), "warehouse": str(wh.id), "lines": [{"variant": str(variant.id), "qty": "2"}]}, format="json")
    assert r.status_code == 201, r.content
    return r.data


def _header(resp):
    return next(csv.reader(io.StringIO(resp.content.decode("utf-8-sig"))))


def test_a_list_names_its_file_columns(api, tenant):
    _order(api, tenant)
    r = api.get("/api/v1/sales-orders/export", {"format": "columns"})
    assert r.status_code == 200, r.content
    cols = r.json()["columns"]
    assert cols[0] == {"key": "number", "label": "Number"}
    assert {"key": "gst_pct", "label": "GST %"} in cols, "a column keeps its own heading"


def test_a_column_switched_off_is_left_out_of_every_file(api, tenant, excel_on):  # noqa: F811
    from openpyxl import load_workbook

    _order(api, tenant)
    full = _header(api.get("/api/v1/sales-orders/export", {"format": "csv"}))
    assert "Customer Code" in full and "Subtotal" in full
    less = api.get("/api/v1/sales-orders/export", {"format": "csv", "hide_columns": "customer_code,subtotal"})
    assert less.status_code == 200
    head = _header(less)
    assert "Customer Code" not in head and "Subtotal" not in head and "Customer Name" in head and len(head) == len(full) - 2

    x = api.get("/api/v1/sales-orders/export", {"format": "xlsx", "hide_columns": "customer_code,subtotal"})
    ws = load_workbook(io.BytesIO(x.content)).active
    xhead = [c.value for c in ws[1]]
    assert "Customer Code" not in xhead and "Subtotal" not in xhead, xhead

    pdf = api.get("/api/v1/sales-orders/export", {"format": "pdf", "hide_columns": "customer_code,subtotal"})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")


def test_one_column_stays(api, tenant):
    _order(api, tenant)
    every = ",".join(c["key"] for c in api.get("/api/v1/sales-orders/export", {"format": "columns"}).json()["columns"])
    r = api.get("/api/v1/sales-orders/export", {"format": "csv", "hide_columns": every})
    assert r.status_code == 400 and r.json()["type"] == "no_columns"


def test_a_list_of_names_reads_as_one_cell(api, tenant):
    """The screen's Sale persons on a customer is a list of names; the file had dropped it as if it were ids."""
    from apps.core.viewsets import _printable_keys

    rows = [{"id": "x", "name": "A", "rep_names": ["Ali", "Babar"], "warehouses": ["0190a0b1-0000-7000-8000-000000000001"], "tags": []}]
    assert _printable_keys(rows) == ["name", "rep_names"]


@pytest.mark.parametrize("endpoint", ["/api/v1/users", "/api/v1/audit-log", "/api/v1/sales-orders/inbox", "/api/v1/inventory/stock",
                                      "/api/v1/inventory/alerts", "/api/v1/delivery-notes"])
def test_lists_whose_buttons_had_nothing_behind_them_now_export(api, tenant, excel_on, endpoint):  # noqa: F811
    """These lists showed Print, PDF and Excel that answered "not found"."""
    _order(api, tenant)
    assert api.get(f"{endpoint}/export", {"format": "columns"}).status_code == 200
    for fmt, starts in (("csv", None), ("xlsx", b"PK"), ("pdf", b"%PDF-")):
        r = api.get(f"{endpoint}/export", {"format": fmt})
        assert r.status_code == 200, (endpoint, fmt, r.content[:300])
        if starts:
            assert r.content.startswith(starts)


def test_the_inbox_file_holds_the_inbox(api, tenant):
    from apps.core.tenancy import tenant_context
    from apps.sales.models import SalesOrder

    o = _order(api, tenant)
    with tenant_context(tenant, tenant.owner):
        SalesOrder.objects.filter(id=o["id"]).update(status="submitted")
    text = api.get("/api/v1/sales-orders/inbox/export", {"format": "csv"}).content.decode("utf-8-sig")
    assert o["number"] in text


def test_the_user_list_reads_roles_by_name(api, tenant):
    head = _header(api.get("/api/v1/users/export", {"format": "csv"}))
    assert "Full Name" in head and "Role Names" in head and "Warehouses" not in head, head


@pytest.fixture
def platform_api(admin_user):
    from rest_framework.test import APIClient

    c = APIClient()
    r = c.post("/api/v1/auth/login", {"identifier": "admin-test", "password": "pak1stan"}, format="json")
    assert r.status_code == 200, r.content
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
    return c


@pytest.mark.parametrize("endpoint", ["tenants", "subscriptions", "invoices", "audit", "notification-log", "payments"])
def test_the_platform_lists_export(platform_api, tenant, endpoint):
    for fmt in ("columns", "csv", "xlsx", "pdf"):
        r = platform_api.get(f"/api/v1/platform/{endpoint}/export", {"format": fmt})
        assert r.status_code == 200, (endpoint, fmt, r.content[:300])


def test_a_company_export_is_still_its_data(platform_api, tenant):
    r = platform_api.get(f"/api/v1/platform/tenants/{tenant.id}/export")
    assert r.status_code == 200 and r["Content-Type"] == "application/zip"


def test_the_payments_csv_button_still_gets_its_csv(platform_api, tenant):
    r = platform_api.get("/api/v1/platform/payments/export")
    assert r.status_code == 200 and r.content.decode().startswith("date,tenant,invoice")
