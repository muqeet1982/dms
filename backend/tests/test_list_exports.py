"""
Every list in the app can be printed, saved as a PDF or opened in a spreadsheet, and what comes out is what was
on screen — same filters, same search, same order.
"""
import pytest

pytestmark = pytest.mark.django_db

SALES_LISTS = ["/api/v1/sales-orders", "/api/v1/invoices", "/api/v1/credit-notes",
               "/api/v1/rep-collections", "/api/v1/rep-settlements"]


@pytest.fixture
def excel_on(tenant):
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        sub = getattr(tenant, "subscription", None)
        if sub is not None:
            features = dict(sub.plan_snapshot.get("features") or {})
            features["excel_export"] = True
            sub.plan_snapshot = {**sub.plan_snapshot, "features": features}
            sub.save(update_fields=["plan_snapshot"])
    return tenant


@pytest.mark.parametrize("endpoint", SALES_LISTS)
def test_every_sales_list_exports(api, excel_on, endpoint):
    csv = api.get(f"{endpoint}/export", {"format": "csv"})
    assert csv.status_code == 200, csv.content[:300]
    assert csv["Content-Type"].startswith("text/csv")

    xlsx = api.get(f"{endpoint}/export", {"format": "xlsx"})
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"

    pdf = api.get(f"{endpoint}/export", {"format": "pdf"})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")


def test_the_file_holds_what_the_screen_held(api, tenant, excel_on):
    """A filtered list exports filtered. A download that quietly differs from the page is worse than none."""
    from .conftest import seed_basic

    wh, _sup, variant, customer = seed_basic(tenant)
    made = api.post("/api/v1/sales-orders", {"customer": str(customer.id), "warehouse": str(wh.id),
                                             "lines": [{"variant": str(variant.id), "qty": "3"}]}, format="json")
    assert made.status_code == 201, made.content
    number = made.data["number"]

    everything = api.get("/api/v1/sales-orders/export", {"format": "csv"}).content.decode("utf-8-sig")
    assert number in everything

    # a filter the screen could have set, carried into the file
    filtered = api.get("/api/v1/sales-orders/export", {"format": "csv", "status": "cancelled"}).content.decode("utf-8-sig")
    assert number not in filtered, "the export ignored the filter the page was showing"


def test_an_export_is_readable_rather_than_a_wall_of_ids(api, tenant, excel_on):
    from .conftest import seed_basic

    wh, _sup, variant, customer = seed_basic(tenant)
    api.post("/api/v1/sales-orders", {"customer": str(customer.id), "warehouse": str(wh.id),
                                      "lines": [{"variant": str(variant.id), "qty": "1"}]}, format="json")
    text = api.get("/api/v1/sales-orders/export", {"format": "csv"}).content.decode("utf-8-sig")
    header = text.splitlines()[0]
    assert "Number" in header and "Customer Name" in header, header
    assert "Id," not in header and "_id" not in header, "ids are not something anybody wants in a spreadsheet"
    assert "Lines," not in header and not header.endswith("Lines"), "nested rows do not belong in a flat file"
    assert "Gps" not in header and "Cost Centre" not in header, "a spreadsheet of orders is not the place for plumbing"
    import re

    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", text), "a UUID leaked into the export"


def test_exporting_needs_the_same_right_as_reading(api, tenant):
    """Nobody can download a list they are not allowed to open."""
    from apps.core.tenancy import tenant_context

    from .test_permission_matrix import _client

    with tenant_context(tenant, tenant.owner):
        blind = _client(tenant, [])
    assert blind.get("/api/v1/sales-orders/export", {"format": "csv"}).status_code == 403
