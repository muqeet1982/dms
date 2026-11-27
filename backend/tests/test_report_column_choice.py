"""
Sales by Sale Person (and the daily sheet a name on it opens) print and download without the columns unticked for that
file, and no report figure reads 0E-7 (owner, 25 Nov 2026).
"""
import io
from datetime import date
from decimal import Decimal as D

import pytest
from django.utils import timezone

from apps.core.tenancy import tenant_context
from apps.reports.exporters import plain_figures, without_columns

from .test_sales_by_rep import _billed, _order, excel_on, world  # noqa: F401 — fixtures

pytestmark = pytest.mark.django_db


def _params(on, **more):
    return {"date_from": on.isoformat(), "date_to": on.isoformat(), **more}


def _book(api, key, **params):
    from openpyxl import load_workbook

    r = api.get(f"/api/v1/reports/{key}", {**params, "format": "xlsx"})
    assert r.status_code == 200, r.content
    return load_workbook(io.BytesIO(r.content)).active


def test_excel_and_csv_leave_out_the_unticked_columns_and_their_totals(api, excel_on, world):
    w = world
    _billed(api, excel_on, w, w["rep"], "4", "cash")
    on = timezone.localdate()

    every = [c.value for c in _book(api, "sales_by_rep", **_params(on))[1]]
    assert "COGS" in every and "Gross Margin" in every and not [h for h in every if "Billed" in h or "Credited" in h]

    sheet = _book(api, "sales_by_rep", **_params(on, hide_columns="cogs,gross_margin,margin_pct"))
    heads = [c.value for c in sheet[1]]
    assert heads == [h for h in every if h not in ("COGS", "Gross Margin", "Margin %")]
    assert [c.value for c in sheet[2]][0] == "Talha" and len([c for c in sheet[2] if c.value is not None]) == len(heads)
    first_column = [row[0].value for row in sheet.iter_rows()]
    assert "sales" in first_column and not {"cogs", "gross_margin"} & set(first_column), "no total of a column left out"

    text = api.get("/api/v1/reports/sales_by_rep", _params(on, format="csv", hide_columns="cogs,gross_margin")).content.decode("utf-8-sig")
    assert "COGS" not in text.splitlines()[0] and "Net Sales" in text.splitlines()[0]
    assert "\r\ncogs," not in text and "\r\nsales," in text


def test_print_and_pdf_leave_them_out_too(api, excel_on, world, monkeypatch):
    from django.template import loader

    w = world
    _billed(api, excel_on, w, w["rep"], "4", "cash")
    seen = {}
    real = loader.render_to_string

    def spy(name, context=None, *a, **k):
        seen.update(context or {})
        return real(name, context, *a, **k)

    monkeypatch.setattr(loader, "render_to_string", spy)
    r = api.get("/api/v1/reports/sales_by_rep", _params(timezone.localdate(), format="pdf", hide_columns="cogs,gross_margin,margin_pct,invoices"))
    assert r.status_code == 200 and r.content.startswith(b"%PDF-")
    assert seen["cols"] == ["label", "qty", "sales", "returns", "net_sales", "cash_sales", "credit_sales", "received", "outstanding", "company_share"]
    assert len(seen["rows"][0]["cells"]) == 10 and not {"cogs", "gross_margin", "margin_pct"} & set(seen["totals"])
    assert "hide_columns" not in seen["params"], "not printed as a filter"


def test_the_page_itself_always_shows_every_column(api, tenant, world):
    w = world
    _billed(api, tenant, w, w["rep"], "4", "cash")
    on = timezone.localdate()
    assert api.get("/api/v1/reports/sales_by_rep", _params(on, hide_columns="cogs")).data["columns"] == api.get("/api/v1/reports/sales_by_rep", _params(on)).data["columns"]


def test_the_daily_sheets_headings_span_what_is_left_and_the_total_keeps_its_name(api, excel_on, world):
    w = world
    on = date(2026, 11, 3)
    _order(excel_on, w, on, w["rep"], [(w["tinta"], "4"), (w["developer"], "2")])

    sheet = _book(api, "rep_daily_sheet", **_params(on, hide_columns="name,tinta_lifting,developer_60,developer_1000"))
    top, sub = [c.value for c in sheet[1]], [c.value for c in sheet[2]]
    assert sub[:6] == ["Date", "Area", "No Of Shops Visited", "Productive Shops", "New Shops", "Regular Color"]
    assert top[5] == "Tinta" and "Developer" not in top and sub[6] == "Lifting Powder 500 gms"
    assert top[sub.index("Shampoo")] == "Care", "a later heading still sits over its own columns"
    assert str(sheet.merged_cells).count(":") == 3, "Care, Keratin Straight and Total; Tinta is one column now"
    assert sheet.cell(row=4, column=1).value == "Total", "the name column went: Total moves to the first one left"

    plain = _book(api, "rep_daily_sheet", **_params(on, hide_columns=",".join(
        c for c in api.get("/api/v1/reports/rep_daily_sheet", _params(on)).data["columns"] if c not in ("name", "date", "area"))))
    assert [c.value for c in plain[1]] == ["Sale Person", "Date", "Area"], "no heading left over them: one row of names"


def test_at_least_one_column_stays(api, excel_on, world):
    w = world
    _billed(api, excel_on, w, w["rep"], "4", "cash")
    on = timezone.localdate()
    every = ",".join(api.get("/api/v1/reports/sales_by_rep", _params(on)).data["columns"])
    assert api.get("/api/v1/reports/sales_by_rep", _params(on, format="xlsx", hide_columns=every)).status_code == 400
    assert api.get("/api/v1/reports/sales_by_rep", _params(on, format="xlsx", hide_columns="nothing_by_this_name")).status_code == 200


def test_every_report_has_the_switch(api, tenant):
    """Owner, 27 Nov 2026: "add column selection for all reports"."""
    assert all(r["pick_columns"] for r in api.get("/api/v1/reports").data)


def test_a_column_only_the_file_carries_can_be_left_out(api, excel_on, world):
    """Sales by Sale Person's Excel carries the sale person's name beside the label; the switch offers it too."""
    w = world
    _billed(api, excel_on, w, w["rep"], "4", "cash")
    on = timezone.localdate()
    data = api.get("/api/v1/reports/product_by_rep", _params(on)).data
    extra = [c for c in data.get("export_columns") or [] if c not in data["columns"]]
    assert extra, "this report's file has a column the table does not"
    r = api.get("/api/v1/reports/product_by_rep", _params(on, format="csv", hide_columns=",".join(extra)))
    assert r.status_code == 200
    head = r.content.decode("utf-8-sig").splitlines()[0]
    assert all((data.get("column_labels") or {}).get(c, c.replace("_", " ").title()) not in head for c in extra), head


def test_a_view_link_alone_is_not_a_file(api, excel_on, world):
    from apps.reports.exporters import without_columns
    from apps.core.exceptions import DomainError

    result = {"columns": ["number", "view"], "rows": [{"number": "1", "view": "View"}], "links": {"view": {"to": "/x/", "id": "id"}}}
    with pytest.raises(DomainError):
        without_columns(result, ["number"])


def test_a_row_whose_cost_nets_to_nothing_reads_zero(api, excel_on, world):
    """3 decimals of quantity × 4 of cost is a zero Python writes as 0E-7."""
    from apps.delivery.models import DeliveryNote
    from apps.sales.models import CreditNote, CreditNoteLine, SalesInvoice, SalesInvoiceLine

    w = world
    order = _billed(api, excel_on, w, w["rep"], "4", "cash")
    on = timezone.localdate()
    with tenant_context(excel_on, excel_on.owner):
        inv = SalesInvoice.objects.get(order_id=order.pk)
        line = SalesInvoiceLine.objects.get(invoice=inv)
        assert str((line.qty + line.free_qty) * line.unit_cost - line.qty * line.unit_cost) == "0E-7", "what the report adds up"
        DeliveryNote.objects.filter(order_id=order.pk).update(status="delivered")
        # all of it back: the report reads the posted credit note and its lines
        cn = CreditNote.objects.create(number="CN-T1", customer=w["cust"], invoice=inv, date=on, reason="came back", warehouse=w["wh"], status="posted", total=inv.total)
        CreditNoteLine.objects.create(credit_note=cn, variant=line.variant, qty=line.qty, unit_price=line.unit_price, unit_cost=line.unit_cost, line_total=line.line_total)
    data = api.get("/api/v1/reports/sales_by_rep", _params(on)).data
    row = next(r for r in data["rows"] if r["label"] == "Talha")
    assert D(row["net_sales"]) == 0 and row["cogs"] == "0" and row["gross_margin"] == "0", row
    book = _book(api, "sales_by_rep", **_params(on))
    heads = [c.value for c in book[1]]
    assert book[2][heads.index("COGS")].value == 0, "a figure in Excel, not the text 0E-7"


def test_plain_figures_leaves_everything_else_as_it_was():
    got = plain_figures({"rows": [{"cogs": "0E-7", "x": "1.5E-7", "big": "1E+1", "code": "0E-7", "sku": "1E5", "name": "NO. 1E5", "n": 3, "none": None, "d": D("0E-7"), "ok": "12.5000"}]})["rows"][0]
    assert got == {"cogs": "0", "x": "0.00000015", "big": "10", "code": "0E-7", "sku": "1E5", "name": "NO. 1E5", "n": 3, "none": None, "d": D("0"), "ok": "12.5000"}
    assert str(got["d"]) == "0"


def test_without_columns_leaves_the_report_it_was_given_alone():
    result = {"columns": ["a", "b"], "rows": [{"a": "1", "b": "2"}], "totals": {"b": "2"}, "export_columns": ["a", "b"]}
    out = without_columns(result, ["b"])
    assert out["columns"] == ["a"] and out["export_columns"] == ["a"] and out["totals"] == {}
    assert result["columns"] == ["a", "b"] and result["totals"] == {"b": "2"}


@pytest.mark.parametrize("key,params,hide,label", [
    ("trial_balance", {"date_from": "2026-11-01", "date_to": "2026-11-30"}, "opening_debit", "Opening Debit"),
    ("profit_and_loss", {"date_from": "2026-11-01", "date_to": "2026-11-30"}, "code", "Code"),
    ("balance_sheet", {"as_of": "2026-11-30"}, "code", "Code"),
    ("general_ledger", {"date_from": "2026-11-01", "date_to": "2026-11-30"}, "type", "Type"),
])
def test_the_statements_leave_out_a_column_switched_off(api, excel_on, key, params, hide, label):
    """The 4 Accounting statement pages got the switch too (owner, 27 Nov 2026, "do optional")."""
    full = api.get(f"/api/v1/reports/{key}", {**params, "format": "csv"})
    assert full.status_code == 200 and label in full.content.decode("utf-8-sig").splitlines()[0]
    less = api.get(f"/api/v1/reports/{key}", {**params, "format": "csv", "hide_columns": hide})
    assert less.status_code == 200, less.content
    assert label not in less.content.decode("utf-8-sig").splitlines()[0].split(",")
    assert api.get(f"/api/v1/reports/{key}", {**params, "format": "pdf", "hide_columns": hide}).content.startswith(b"%PDF-")
