"""
The sales reports read in the owner's words (6 and 8 Nov 2026): Sales By Product is headed Product Name, in bold, with the item
code and UPC under the name; Sales By Brand is headed Brand, Sales By Supplier Supplier Name; Sales By Customer Customer Name
with the sale person under it; Sales by rep is Sales by Sale Person. Words read left and figures right, on screen and in print.
"""
import io
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

from .conftest import issue_order, seed_basic

pytestmark = pytest.mark.django_db


@pytest.fixture
def sold(api, tenant):
    from apps.catalog.models import Brand

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        p = v.product
        p.item_code, p.upc, p.brand = "0062001", "8690606123456", Brand.objects.create(name="Keune")
        p.save(update_fields=["item_code", "upc", "brand"])
    o = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": "2"}]}, format="json").data
    issue_order(api, o["id"])
    r = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": True}, format="json")
    assert r.status_code in (200, 201), r.content
    return v, sup


def test_sales_by_product_is_headed_product_name_with_the_codes_under_it(api, sold):
    v, _s = sold
    data = api.get("/api/v1/reports/sales_by_product").data
    assert data["column_labels"]["label"] == "Product Name"
    row = data["rows"][0]
    assert row["label"] == v.name and v.sku not in row["label"], "the name alone"
    assert data["notes"] == {"label": "codes"} and row["codes"] == "Item Code: 0062001 · UPC: 8690606123456"
    assert "codes" not in data["columns"] and "upc" not in data["columns"], "under the name, not columns of their own, on screen"
    assert D(row["qty"]) == 2


@pytest.mark.parametrize("item_code,upc,expected", [("0062001", "", "Item Code: 0062001"), ("", "8690606123456", "UPC: 8690606123456"), ("", "", "")])
def test_a_code_the_product_does_not_have_is_left_out(api, tenant, sold, item_code, upc, expected):
    v, _s = sold
    with tenant_context(tenant, tenant.owner):
        p = v.product
        p.item_code, p.upc = item_code, upc
        p.save(update_fields=["item_code", "upc"])
    assert api.get("/api/v1/reports/sales_by_product").data["rows"][0]["codes"] == expected


def test_the_pdf_prints_the_codes_under_the_name(api, sold, monkeypatch):
    from django.template import loader

    v, _s = sold
    seen, real = {}, loader.render_to_string

    def capture(template, context=None, *a, **k):
        seen["html"] = real(template, context, *a, **k)
        return seen["html"]

    monkeypatch.setattr(loader, "render_to_string", capture)
    r = api.get("/api/v1/reports/sales_by_product", {"format": "pdf"})
    assert r.status_code == 200 and r["Content-Type"].startswith("application/pdf")
    assert ">Product Name<" in seen["html"]
    assert f'{v.name}<div class="cell-note">Item Code: 0062001 · UPC: 8690606123456</div>' in seen["html"]


def test_a_spreadsheet_takes_the_codes_as_columns_of_their_own_and_as_text(api, sold):
    from openpyxl import load_workbook

    v, _s = sold
    r = api.get("/api/v1/reports/sales_by_product", {"format": "xlsx"})
    assert r.status_code == 200
    ws = load_workbook(io.BytesIO(b"".join(r.streaming_content) if r.streaming else r.content)).active
    heads = [c.value for c in ws[1]]
    assert heads[:3] == ["Product Name", "Item Code", "UPC"]
    assert [c.value for c in ws[2]][:3] == [v.name, "0062001", "8690606123456"], "the leading zero and the long barcode survive"
    assert D(str(ws[2][3].value)) == 2, "and the figures are still figures"
    assert "Codes" not in heads
    csv = api.get("/api/v1/reports/sales_by_product", {"format": "csv"})
    first = (b"".join(csv.streaming_content) if csv.streaming else csv.content).decode("utf-8-sig").splitlines()
    assert first[0].startswith("Product Name,Item Code,UPC,") and "0062001,8690606123456" in first[1]


def test_brand_and_supplier_are_headed_in_their_own_words(api, sold):
    brand = api.get("/api/v1/reports/sales_by_brand").data
    assert brand["column_labels"]["label"] == "Brand" and brand["rows"][0]["label"] == "Keune"
    supplier = api.get("/api/v1/reports/sales_by_supplier").data
    assert supplier["column_labels"]["label"] == "Supplier Name" and supplier["rows"][0]["label"] == "Maker"
    for key, head in (("sales_by_brand", "Brand"), ("sales_by_supplier", "Supplier Name")):
        csv = api.get(f"/api/v1/reports/{key}", {"format": "csv"})
        assert (b"".join(csv.streaming_content) if csv.streaming else csv.content).decode("utf-8-sig").startswith(f"{head},")


def _pdf_html(api, monkeypatch, key):
    from django.template import loader

    seen, real = {}, loader.render_to_string

    def capture(template, context=None, *a, **k):
        seen["html"] = real(template, context, *a, **k)
        return seen["html"]

    monkeypatch.setattr(loader, "render_to_string", capture)
    assert api.get(f"/api/v1/reports/{key}", {"format": "pdf"}).status_code == 200
    return seen["html"]


def test_product_names_print_bold_and_every_figure_reads_right(api, sold, monkeypatch):
    v, _s = sold
    data = api.get("/api/v1/reports/sales_by_product").data
    assert data["column_styles"] == {"label": "bold"}
    html = _pdf_html(api, monkeypatch, "sales_by_product")
    assert f'<td class="b">{v.name}<div class="cell-note">' in html
    assert '<th>Product Name</th><th class="r">Qty</th>' in html, "the quantity is a figure: right, though it is the second column"
    assert '<th class="r">Invoices</th>' in html and '<td class="r">1</td>' in html


def test_codes_stay_left_however_they_look(api, tenant, sold, monkeypatch):
    html = _pdf_html(api, monkeypatch, "stock_summary")
    assert '<th>Upc</th>' in html or '<th>UPC</th>' in html
    assert '<th class="r">Upc</th>' not in html and '<th class="r">UPC</th>' not in html


def test_sales_by_customer_shows_the_name_and_who_sold_to_them(api, tenant, sold):
    from apps.partners.models import Customer, Supplier, SupplierRep

    data = api.get("/api/v1/reports/sales_by_customer").data
    row = data["rows"][0]
    assert data["column_labels"]["label"] == "Customer Name" and row["label"] == "Salon One", "the name, not the code"
    assert data["notes"] == {"label": "sold_by"} and data["export_columns"][:2] == ["label", "sale_person"]
    with tenant_context(tenant, tenant.owner):
        rep = SupplierRep.objects.create(supplier=Supplier.objects.first(), code="R-1", name="Imran", phone="03001110000")
        Customer.objects.get(name="Salon One").assigned_reps.add(rep)
    row = api.get("/api/v1/reports/sales_by_customer").data["rows"][0]
    assert row["sale_person"] == "Imran" and row["sold_by"] == "Sale Person: Imran", "no rep on the invoices: the customer's own"
    csv = api.get("/api/v1/reports/sales_by_customer", {"format": "csv"})
    assert (b"".join(csv.streaming_content) if csv.streaming else csv.content).decode("utf-8-sig").startswith("Customer Name,Sale Person,")


def test_sales_by_rep_reads_sales_by_sale_person_and_sales_by_product_has_no_saved_filters(api, sold):
    reports = {r["key"]: r for r in api.get("/api/v1/reports").data}
    assert reports["sales_by_rep"]["title"] == "Sales by Sale Person"
    assert api.get("/api/v1/reports/sales_by_rep").data["column_labels"]["label"] == "Sale Person"
    assert reports["sales_by_product"]["saved_filters"] is False and reports["sales_by_brand"]["saved_filters"] is True
    assert reports["sales_by_customer"]["saved_filters"] is False, "owner, 8 Nov 2026"
