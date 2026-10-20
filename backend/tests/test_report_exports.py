"""
Every financial report, in every format the office actually uses.

There was no test over the Excel path at all, which is how a one-word shadowing bug (`for cell in ws[1]`
shadowing the `cell()` formatter) shipped and broke every xlsx export. Hence this: the four statements an
accountant asks for, each as JSON, CSV, Excel and PDF.
"""
import pytest

pytestmark = pytest.mark.django_db

STATEMENTS = ["trial_balance", "general_ledger", "profit_and_loss", "balance_sheet"]


@pytest.fixture
def excel_on(tenant):
    """xlsx is a plan feature; these tests are about the writer, not about billing."""
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        sub = getattr(tenant, "subscription", None)
        if sub is not None:
            features = dict(sub.plan_snapshot.get("features") or {})
            features["excel_export"] = True
            sub.plan_snapshot = {**sub.plan_snapshot, "features": features}
            sub.save(update_fields=["plan_snapshot"])
    return tenant


@pytest.mark.parametrize("key", STATEMENTS)
def test_a_financial_statement_comes_back_in_every_format(api, excel_on, key):
    params = {"date_from": "2026-01-01", "date_to": "2026-12-31", "as_of": "2026-12-31"}

    js = api.get(f"/api/v1/reports/{key}", params)
    assert js.status_code == 200, js.content
    assert "rows" in js.data and "columns" in js.data

    csv = api.get(f"/api/v1/reports/{key}", {**params, "format": "csv"})
    assert csv.status_code == 200 and csv["Content-Type"].startswith("text/csv")
    assert csv.content.decode("utf-8-sig").strip(), "an empty file is not an export"

    xlsx = api.get(f"/api/v1/reports/{key}", {**params, "format": "xlsx"})
    assert xlsx.status_code == 200, xlsx.content[:400]
    assert xlsx.content[:2] == b"PK", "an xlsx is a zip"

    pdf = api.get(f"/api/v1/reports/{key}", {**params, "format": "pdf"})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")


def test_an_export_writes_dates_the_way_the_screen_does(api, excel_on):
    """A download that disagrees with the page it came from is worse than no download."""
    r = api.get("/api/v1/reports/journal_register", {"date_from": "2026-01-01", "date_to": "2026-12-31", "format": "csv"})
    assert r.status_code == 200
    text = r.content.decode("utf-8-sig")
    import re

    assert not re.search(r"\b\d{4}-\d{2}-\d{2}\b", text), f"an ISO date leaked into the export: {text[:300]}"


def test_the_stock_report_exports_to_every_format(api, tenant):
    """Its title once carried a slash, which openpyxl refuses as a sheet name — the export 500'd while the
    screen worked. Every format must answer."""
    for fmt, ctype in (("csv", "text/csv"), ("xlsx", "application/vnd"), ("pdf", "application/pdf")):
        r = api.get("/api/v1/reports/stock_report", {"format": fmt})
        assert r.status_code == 200, (fmt, r.status_code)
        assert r["Content-Type"].startswith(ctype), (fmt, r["Content-Type"])
