"""
The sheet a rep sends the office each day (owner, 5 Nov 2026): name, date, area, shops visited, productive and new, the
units of each product family, and the day's and the month's money. A rep's name on Sales By Rep, the original invoice
summary, opens it for the same dates.
"""
import io
from datetime import date, datetime
from decimal import Decimal as D

import pytest
from django.utils import timezone

from apps.core.tenancy import tenant_context
from apps.reports.rep_daily import FAMILIES, family_of

from .conftest import seed_basic

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("category,name,column", [
    ("Tinta Color", "NO. 1 BLACK - 60 ML", "tinta_regular"),
    ("Tinta Color", "NO. 6.13 - DARK ASH G/BLONDE UC - 60 ML", "tinta_regular"),
    ("Tinta Color", "TINTA COLOR METALLIC COOL", "tinta_regular"),
    ("Tinta Color", "NO. 1000 - NATURAL BLONDE - 60 ML", "tinta_lifting"),
    ("Tinta Color", "NO. 3017 - ULTRA ASH VIOLET BLONDE - 60 G", "tinta_lifting"),
    ("Tinta Color", "RED BOOSTER - 10*3 ML", "others"),
    ("Tinta Developers & Accessories", "DEVELOPER 20 VOL - 60 ML", "developer_60"),
    ("Tinta Developers & Accessories", "DEVELOPER 40 VOL - 1000 ML", "developer_1000"),
    ("Semi Color Activator", "SEMI COLOR ACTIVATOR - 60 ML", "others"),
    ("Bleach", "UB MAGIC BLONDE - 500 G", "lifting_powder"),
    ("Bleach", "UB POWER BLONDE RE-FILL", "lifting_powder"),
    ("Bleach", "CREAM BLEACH 50 G", "cream_blonde"),
    ("Bleach", "ULTIMATE BLONDE CREAM - 500 G", "others"),
    ("Derma Regulate", "CARE DERMA REGULATE SHAMPOO - 300 ML", "care_shampoo"),
    ("Derma Sensitive", "CARE DERMA SENSITIVE CONDITION - 250 ML", "care_conditioner"),
    ("Satin Oil", "CARE SATIN OIL MASK - 200 ML", "care_mask"),
    ("So Pure Treatments", "SP Restore Mask", "care_mask"),
    ("Satin Oil", "CARE SATIN OIL TREATMENT - 95 ML", "care_others"),
    ("Velvet Smooth", "VELVET SMOOTH 2-PHASE SPRAY - 200 ML", "care_others"),
    ("Fix", "STYLE FREESTYLE SPRAY", "spray"),
    ("Fix & Finish", "FREESTYLER 300ML", "spray"),
    ("Texture", "STYLE ULTRA GEL", "gels"),
    ("Smooth & Shine", "SMOOTH OPERATOR", "smooth_operator"),
    ("Semi Color", "SEMI COLOR NO. 1", "others"),
    ("Perms", "HAIR STRAIGHTENER PACK (85+85 ML) - 170 ML", "hair_straightener"),
    ("Perms", "S& S REBONDING E/FORT PACK (85+85 M) - 170 ML", "hair_straightener"),
    ("Keratin Straight Rebonding", "KERATIN STRAIGHTENING PACK NORMAL - 2*100 ML", "keratin_pack"),
    ("Keratin Straight Rebonding", "KERATIN STRAIGHTENING CREAM STRONG - 400 ML", "keratin_400"),
    ("Keratin Straight Rebonding", "KERATIN STRAIGHT NEUTRALIZATION - 1000 ML", "others"),
    ("Accessories", "CARE MASSAGE TOWEL (Imp)", "accessories"),
    ("Color Bowl", "COLOR BOWL (IMP)", "accessories"),
    ("", "Something new nobody has placed", "others"),
])
def test_each_product_counts_under_its_column(category, name, column):
    assert family_of(category, name) == column


def _variant(tenant, category, name, price="100"):
    from apps.catalog.models import Category, UnitOfMeasure
    from apps.catalog.services import ProductService

    with tenant_context(tenant, tenant.owner):
        uom = UnitOfMeasure.objects.get(code="PC")
        cat, _ = Category.objects.get_or_create(name=category)
        product = ProductService.create_product(tenant, {"sku": name[:30], "name": name, "base_uom": uom, "category": cat, "sale_price": D(price), "cost_price": D("10")})
        return product.variants.first()


@pytest.fixture
def world(tenant):
    from apps.partners.models import SupplierRep

    wh, sup, v, cust = seed_basic(tenant)
    tinta = _variant(tenant, "Tinta Color", "NO. 5 - LIGHT BROWN - 60 ML", "100")
    developer = _variant(tenant, "Tinta Developers & Accessories", "DEVELOPER 20 VOL - 60 ML", "50")
    with tenant_context(tenant, tenant.owner):
        from apps.inventory.services import StockService

        for stocked in (tinta, developer):
            StockService.move(warehouse=wh, variant=stocked, qty=D("1000"), movement_type="opening", unit_cost=D("10"))
        rep = SupplierRep.objects.create(supplier=sup, code="R-1", name="Talha")
        other = SupplierRep.objects.create(supplier=sup, code="R-2", name="Anza")
        cust.city = "Lahore"
        cust.save(update_fields=["city"])
    return {"wh": wh, "sup": sup, "cust": cust, "tinta": tinta, "developer": developer, "rep": rep, "other": other}


def _order(tenant, w, on, rep, lines, customer=None, status=None):
    from apps.sales.models import SalesOrder
    from apps.sales.services import OrderService

    with tenant_context(tenant, tenant.owner):
        o = OrderService.create(customer=customer or w["cust"], warehouse=w["wh"], rep=rep, source="rep_app", order_date=on,
                                lines=[{"variant": v, "qty": D(q), "unit_price": None} for v, q in lines])
        if status:
            SalesOrder.objects.filter(pk=o.pk).update(status=status)
        return o


def _shop(tenant, code, rep=None, on=None, **fields):
    from apps.partners.models import Customer

    with tenant_context(tenant, tenant.owner):
        c = Customer.objects.create(code=code, name=f"Shop {code}", requested_by_rep=rep, **fields)
        if on:
            Customer.objects.filter(pk=c.pk).update(created_at=timezone.make_aware(datetime(on.year, on.month, on.day, 12)))
        return c


def _visit(tenant, customer, rep, on, hour=11):
    from apps.partners.models import CustomerVisit

    with tenant_context(tenant, tenant.owner):
        return CustomerVisit.objects.create(customer=customer, rep=rep, at=timezone.make_aware(datetime(on.year, on.month, on.day, hour)))


def _report(api, **params):
    r = api.get("/api/v1/reports/rep_daily_sheet", params)
    assert r.status_code == 200, r.content
    return r.data


def _row(data, name, on):
    return next(r for r in data["rows"] if r["name"] == name and r["date"] == on.isoformat())


def test_it_has_every_field_the_owner_listed_in_order(api, tenant, world):
    data = _report(api)
    flat = []
    at = 0
    for g in data["header_groups"]:
        for c in data["columns"][at:at + g["span"]]:
            flat.append(f"{g['label']} {data['column_labels'][c]}".strip())
        at += g["span"]
    assert flat == [
        "Name", "Date", "Area", "No Of Shops Visited", "Productive Shops", "New Shops",
        "Tinta Regular Color", "Tinta Lifting Colors", "Developer 60 ml", "Developer 1000 ml", "Lifting Powder 500 gms", "Cream Blonde 50 g",
        "Care Shampoo", "Care Conditioner", "Care Mask", "Care Others", "Spray", "Gels", "Smooth Operator", "Others",
        "Hair Straightener Normal & Rebonding Extra Forte", "Keratin Straight Pack", "Keratin Straight 400 ml", "Accessories",
        "Total Today", "Total Till Date",
    ]


def test_a_reps_day(api, tenant, world):
    w = world
    on = date(2026, 11, 3)
    second = _shop(tenant, "C-2", city="Lahore")
    quiet = _shop(tenant, "C-3", city="Lahore")
    new = _shop(tenant, "C-4", rep=w["rep"], on=on, city="Lahore")
    _order(tenant, w, on, w["rep"], [(w["tinta"], "4"), (w["developer"], "2")])
    _order(tenant, w, on, w["rep"], [(w["tinta"], "1")])   # a second order at the same shop: still one shop
    _order(tenant, w, on, w["rep"], [(w["developer"], "6")], customer=second)
    _order(tenant, w, on, w["rep"], [(w["tinta"], "9")], customer=quiet, status="cancelled")   # visited, but no sale
    _visit(tenant, w["cust"], w["rep"], on)               # checked in where an order was taken: not counted twice

    row = _row(_report(api, date_from=on, date_to=on), "Talha", on)
    assert row["visited_count"] == "4", "the two with orders, the cancelled one and the new one"
    assert row["productive_count"] == "2"
    assert row["new_count"] == "1"
    assert D(row["tinta_regular"]) == 5 and D(row["developer_60"]) == 8, "units; the cancelled order's 9 are not in them"
    assert D(row["total_today"]) == D("900"), "5 × 100 + 8 × 50"
    assert row["area"] == "Lahore"
    assert all(D(row[k]) == 0 for k, _h, _l in FAMILIES if k not in ("tinta_regular", "developer_60"))


def test_a_visit_with_no_order_is_a_row_of_its_own(api, tenant, world):
    w = world
    on = date(2026, 11, 4)
    _visit(tenant, w["cust"], w["other"], on)
    row = _row(_report(api, date_from=on, date_to=on), "Anza", on)
    assert (row["visited_count"], row["productive_count"], row["new_count"]) == ("1", "0", "0")
    assert D(row["total_today"]) == 0


def test_a_visit_late_at_night_counts_on_the_local_day(api, tenant, world):
    from apps.partners.models import CustomerVisit

    w = world
    with tenant_context(tenant, tenant.owner):
        CustomerVisit.objects.create(customer=w["cust"], rep=w["rep"], at=timezone.make_aware(datetime(2026, 11, 4, 23, 30)))
    assert _report(api, date_from=date(2026, 11, 4), date_to=date(2026, 11, 4))["rows"][0]["date"] == "2026-11-04"
    assert _report(api, date_from=date(2026, 11, 5), date_to=date(2026, 11, 5))["rows"] == []


def test_till_date_runs_from_the_first_of_the_month_even_when_the_range_starts_later(api, tenant, world):
    w = world
    _order(tenant, w, date(2026, 10, 30), w["rep"], [(w["tinta"], "7")])    # last month: not in November's till date
    _order(tenant, w, date(2026, 11, 2), w["rep"], [(w["tinta"], "1")])
    _order(tenant, w, date(2026, 11, 3), w["rep"], [(w["tinta"], "2")])
    _order(tenant, w, date(2026, 11, 5), w["rep"], [(w["tinta"], "4")])

    data = _report(api, date_from=date(2026, 11, 3), date_to=date(2026, 11, 3))
    assert [r["date"] for r in data["rows"] if r["name"] == "Talha"] == ["2026-11-03"], "only the day asked for is listed"
    row = _row(data, "Talha", date(2026, 11, 3))
    assert D(row["total_today"]) == 200 and D(row["total_till_date"]) == 300, "1st to the 3rd, the 2nd included"

    month = _report(api, date_from=date(2026, 10, 1), date_to=date(2026, 11, 30))
    assert [D(_row(month, "Talha", d)["total_till_date"]) for d in (date(2026, 10, 30), date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 5))] == [700, 100, 300, 700]


def test_rows_read_rep_by_rep_with_a_total_at_the_bottom(api, tenant, world):
    w = world
    _order(tenant, w, date(2026, 11, 3), w["rep"], [(w["tinta"], "1")])
    _order(tenant, w, date(2026, 11, 2), w["rep"], [(w["tinta"], "2")])
    _order(tenant, w, date(2026, 11, 3), w["other"], [(w["developer"], "2")])
    _order(tenant, w, date(2026, 11, 3), None, [(w["developer"], "1")])

    rows = _report(api, date_from=date(2026, 11, 1), date_to=date(2026, 11, 30))["rows"]
    assert [(r["name"], r["date"]) for r in rows] == [
        ("Anza", "2026-11-03"), ("Talha", "2026-11-02"), ("Talha", "2026-11-03"), ("(No Rep)", "2026-11-03"), ("Total", "")]
    total = rows[-1]
    assert total["kind"] == "subtotal" and total["total_till_date"] == "", "a running figure does not add up"
    assert D(total["tinta_regular"]) == 3 and D(total["developer_60"]) == 3
    assert D(total["total_today"]) == D("450") and total["productive_count"] == "4"


def test_the_rep_filter_shows_one_rep(api, tenant, world):
    w = world
    on = date(2026, 11, 3)
    _order(tenant, w, on, w["rep"], [(w["tinta"], "1")])
    _order(tenant, w, on, w["other"], [(w["tinta"], "1")])
    _visit(tenant, w["cust"], w["other"], on)
    rows = _report(api, date_from=on, date_to=on, rep=str(w["rep"].id))["rows"]
    assert [r["name"] for r in rows] == ["Talha", "Total"]


def test_area_is_the_shops_territory_then_route_then_city(api, tenant, world):
    from apps.partners.models import Route, Territory

    w = world
    on = date(2026, 11, 3)
    with tenant_context(tenant, tenant.owner):
        territory = Territory.objects.create(name="Cantt")
        route = Route.objects.create(name="Walton Road")
    a = _shop(tenant, "A-1", territory=territory, route=route, city="Lahore")
    b = _shop(tenant, "A-2", route=route, city="Lahore")
    c = _shop(tenant, "A-3", city="Multan")
    for shop in (a, b, c):
        _visit(tenant, shop, w["rep"], on)
    assert _row(_report(api, date_from=on, date_to=on), "Talha", on)["area"] == "Cantt, Multan, Walton Road"


def test_with_nothing_in_the_range_there_is_no_total_row(api, tenant, world):
    assert _report(api, date_from=date(2000, 1, 1), date_to=date(2000, 1, 2))["rows"] == []


@pytest.fixture
def excel_on(tenant):
    with tenant_context(tenant, tenant.owner):
        sub = getattr(tenant, "subscription", None)
        if sub is not None:
            sub.plan_snapshot = {**sub.plan_snapshot, "features": {**(sub.plan_snapshot.get("features") or {}), "excel_export": True}}
            sub.save(update_fields=["plan_snapshot"])
    return tenant


def test_print_pdf_and_excel_carry_the_same_sheet(api, excel_on, world):
    from openpyxl import load_workbook

    w = world
    on = date(2026, 11, 3)
    _order(excel_on, w, on, w["rep"], [(w["tinta"], "4"), (w["developer"], "2")])
    params = {"date_from": on.isoformat(), "date_to": on.isoformat()}

    text = api.get("/api/v1/reports/rep_daily_sheet", {**params, "format": "csv"}).content.decode("utf-8-sig")
    head = text.splitlines()[0]
    assert head.startswith("Name,Date,Area,No Of Shops Visited,Productive Shops,New Shops,Tinta Regular Color,Tinta Lifting Colors,Developer 60 ml")
    assert head.endswith("Accessories,Total Today,Total Till Date")
    assert "03/11/2026" in text and "2026-11-03" not in text
    assert "\nsales," not in text and "\r\nsales," not in text, "the totals are the bottom row, not repeated underneath"

    book = load_workbook(io.BytesIO(api.get("/api/v1/reports/rep_daily_sheet", {**params, "format": "xlsx"}).content)).active
    assert [c.value for c in book[1]][6:8] == ["Tinta", ""] or book[1][6].value == "Tinta", "the two-row heading"
    assert book[2][6].value == "Regular Color" and book[2][3].value == "No Of Shops Visited"
    assert [c.value for c in book[3]][:2] == ["Talha", "03/11/2026"]
    assert book[3][6].value == 4 and book[3][8].value == 2
    assert book.cell(row=4, column=1).value == "Total" and book.cell(row=4, column=1).font.bold

    pdf = api.get("/api/v1/reports/rep_daily_sheet", {**params, "format": "pdf"})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")


def test_the_print_fits_the_page_and_names_the_rep_and_the_dates_properly(api, excel_on, world, monkeypatch):
    """26 columns: the first print ran off the right edge of an A4 landscape page, losing both totals."""
    from django.template import loader

    w = world
    on = date(2026, 11, 3)
    _order(excel_on, w, on, w["rep"], [(w["tinta"], "4")])
    seen = {}
    real = loader.render_to_string

    def spy(name, context=None, *a, **k):
        seen.update(context or {})
        html = real(name, context, *a, **k)
        seen["html"] = html
        return html

    monkeypatch.setattr(loader, "render_to_string", spy)
    r = api.get("/api/v1/reports/rep_daily_sheet", {"date_from": on.isoformat(), "date_to": on.isoformat(), "rep": str(w["rep"].id), "format": "pdf"})
    assert r.status_code == 200 and r.content.startswith(b"%PDF-")
    assert seen["landscape"] and seen["fit"] == "fit-tiny"
    assert seen["params"] == {"date_from": "03/11/2026", "date_to": "03/11/2026", "rep": "Talha"}
    assert 'class="st-subtotal"' in seen["html"] and 'class="r z"' in seen["html"], "the Total row stands out and the zeros fade"
    assert not seen["dark_header"] and 'class="lines fit-tiny"' in seen["html"], "the header is the colour of every other report"
    assert not {0, 1, 2} & seen["num_cols"] and {3, 4} <= seen["num_cols"], "Name, Date and Area are words: left; the figures after them right"


def test_the_original_report_is_back_and_its_names_open_the_sheet(api, tenant, world):
    """Owner, 5 Nov 2026: "wanted the original report as well, when click on the name on that report then show this"."""
    from .conftest import issue_order

    w = world
    on = timezone.localdate()
    order = _order(tenant, w, on, w["rep"], [(w["tinta"], "4")])
    with tenant_context(tenant, tenant.owner):
        from apps.sales.services import OrderService

        OrderService.confirm(order)
    issue_order(api, order.pk)
    assert api.post(f"/api/v1/sales-orders/{order.pk}/invoice", {"post": True}, format="json").status_code == 201

    summary = api.get("/api/v1/reports/sales_by_rep", {"date_from": on.isoformat(), "date_to": on.isoformat()}).data
    assert summary["columns"] == ["label", "qty", "sales", "cash_sales", "credit_sales", "returns", "received", "outstanding", "cogs", "gross_margin", "margin_pct", "invoices"], "the original columns, with the additions"
    assert summary["totals"] and not summary.get("totals_in_rows") and not summary.get("header_groups")
    row = next(r for r in summary["rows"] if r["label"] == "Talha")
    assert D(row["qty"]) == 4 and D(row["sales"]) == 400 and row["invoices"] == 1
    assert summary["drill"] == {"column": "label", "report": "rep_daily_sheet", "param": "rep"}
    assert row["key"] == str(w["rep"].id), "what the link carries"

    sheet = api.get(f"/api/v1/reports/{summary['drill']['report']}", {"date_from": on.isoformat(), "date_to": on.isoformat(), summary["drill"]["param"]: row["key"]}).data
    assert [r["name"] for r in sheet["rows"]] == ["Talha", "Total"] and D(sheet["rows"][0]["tinta_regular"]) == 4
    assert sheet["plain_header"] and sheet["header_groups"]


def test_both_reports_are_in_the_catalogue(api, tenant):
    keys = {r["key"]: r["title"] for r in api.get("/api/v1/reports").data}
    assert keys["sales_by_rep"] == "Sales by Sale Person" and keys["rep_daily_sheet"] == "Sales by Sale Person — daily sheet"


def _billed(api, tenant, w, rep, qty, mode, customer=None):
    """An order taken as cash or credit, issued and invoiced today."""
    from apps.sales.services import OrderService

    from .conftest import issue_order

    with tenant_context(tenant, tenant.owner):
        order = OrderService.create(customer=customer or w["cust"], warehouse=w["wh"], rep=rep, source="rep_app", payment_mode=mode,
                                    lines=[{"variant": w["tinta"], "qty": D(qty), "unit_price": None}])
        OrderService.confirm(order)
    issue_order(api, order.pk)
    made = api.post(f"/api/v1/sales-orders/{order.pk}/invoice", {"post": True}, format="json")
    assert made.status_code == 201, made.content
    return order


def _summary(api):
    on = timezone.localdate().isoformat()
    r = api.get("/api/v1/reports/sales_by_rep", {"date_from": on, "date_to": on})
    assert r.status_code == 200, r.content
    return r.data


def test_the_main_report_shows_how_much_on_cash_and_how_much_on_credit(api, tenant, world):
    """Owner, 5 Nov 2026: "also show in main rep report how much on cash and how much on credit"."""
    w = world
    _billed(api, tenant, w, w["rep"], "4", "cash")
    _billed(api, tenant, w, w["rep"], "1", "cash")
    _billed(api, tenant, w, w["rep"], "2", "credit")
    _billed(api, tenant, w, w["other"], "3", "credit")

    data = _summary(api)
    talha = next(r for r in data["rows"] if r["label"] == "Talha")
    anza = next(r for r in data["rows"] if r["label"] == "Anza")
    assert (D(talha["sales"]), D(talha["cash_sales"]), D(talha["credit_sales"])) == (D("700"), D("500"), D("200"))
    assert (D(anza["sales"]), D(anza["cash_sales"]), D(anza["credit_sales"])) == (D("300"), D("0"), D("300"))
    t = data["totals"]
    assert (D(t["sales"]), D(t["cash_sales"]), D(t["credit_sales"])) == (D("1000"), D("500"), D("500"))
    assert list(t)[:3] == ["sales", "cash_sales", "credit_sales"]
    assert D(t["cogs"]) > 0 and "gross_margin" in t, "the original totals are all still there"


def test_cash_and_credit_add_up_to_sales_without_tax_as_sales_does(api, tenant, world):
    from apps.catalog.models import TaxType

    w = world
    with tenant_context(tenant, tenant.owner):
        TaxType.objects.create(code="ST", name="Sales Tax", kind="sales_tax", applies_to="both")
    from apps.sales.services import OrderService

    from .conftest import issue_order

    with tenant_context(tenant, tenant.owner):
        order = OrderService.create(customer=w["cust"], warehouse=w["wh"], rep=w["rep"], source="rep_app", payment_mode="credit", charge_tax=True,
                                    lines=[{"variant": w["tinta"], "qty": D("5"), "unit_price": None}])
        OrderService.confirm(order)
    issue_order(api, order.pk)
    assert api.post(f"/api/v1/sales-orders/{order.pk}/invoice", {"post": True, "apply_tax": True}, format="json").status_code == 201
    _billed(api, tenant, w, w["rep"], "1", "cash")

    row = next(r for r in _summary(api)["rows"] if r["label"] == "Talha")
    assert D(row["cash_sales"]) + D(row["credit_sales"]) == D(row["sales"])
    assert D(row["credit_sales"]) == D("400"), "T.P 500 includes 25% tax: 400 before it"
    assert D(row["cash_sales"]) == D("100")


def test_an_older_order_that_never_said_goes_by_the_customers_terms(api, tenant, world):
    from apps.sales.models import SalesOrder

    w = world
    on_terms = _shop(tenant, "T-1", credit_days=30, credit_limit=D("100000"))
    a = _billed(api, tenant, w, w["rep"], "2", "cash")
    b = _billed(api, tenant, w, w["rep"], "3", "credit", customer=on_terms)
    with tenant_context(tenant, tenant.owner):
        SalesOrder.objects.filter(pk__in=[a.pk, b.pk]).update(payment_mode="")
    row = next(r for r in _summary(api)["rows"] if r["label"] == "Talha")
    assert (D(row["cash_sales"]), D(row["credit_sales"])) == (D("200"), D("300"))


def test_the_other_sales_reports_do_not_grow_the_columns(api, tenant):
    on = timezone.localdate().isoformat()
    assert "cash_sales" not in api.get("/api/v1/reports/sales_by_customer", {"date_from": on, "date_to": on}).data["columns"]


def test_the_main_report_shows_what_came_in_and_what_is_still_owed(api, tenant, world):
    """The owner said yes to these on 5 Nov 2026: Cash Sales is how an order was taken, not money received."""
    from apps.accounting.services import PaymentService
    from apps.sales.models import SalesInvoice

    w = world
    first = _billed(api, tenant, w, w["rep"], "4", "cash")      # 400
    _billed(api, tenant, w, w["rep"], "2", "credit")            # 200
    third = _billed(api, tenant, w, w["rep"], "1", "cash")      # 100
    _billed(api, tenant, w, w["other"], "3", "cash")            # 300, nothing paid
    with tenant_context(tenant, tenant.owner):
        inv = SalesInvoice.objects.get(order_id=first.pk)
        PaymentService.record(direction="in", partner_type="customer", partner_id=str(w["cust"].id), amount=D("150"), method="cash",
                              allocations=[{"document_type": "sales_invoice", "document_id": str(inv.pk), "amount": D("150")}], auto_allocate=False)
        # 100 settled by a credit note: the goods came back, no money did
        SalesInvoice.objects.filter(order_id=third.pk).update(paid_amount=D("100"), credited_amount=D("100"), status="returned")

    data = _summary(api)
    talha = next(r for r in data["rows"] if r["label"] == "Talha")
    anza = next(r for r in data["rows"] if r["label"] == "Anza")
    assert D(talha["received"]) == D("150") and D(talha["outstanding"]) == D("450"), "250 + 200 still owed, the returned 100 is neither"
    assert D(anza["received"]) == 0 and D(anza["outstanding"]) == D("300")
    assert D(talha["invoices"]) == 3, "each invoice counted once, however many lines it has"
    t = data["totals"]
    assert D(t["received"]) == D("150") and D(t["outstanding"]) == D("750")
    assert list(t)[:6] == ["sales", "cash_sales", "credit_sales", "returns", "received", "outstanding"]


def test_an_invoice_with_many_lines_is_counted_once(api, tenant, world):
    from apps.sales.services import OrderService

    from .conftest import issue_order

    w = world
    with tenant_context(tenant, tenant.owner):
        order = OrderService.create(customer=w["cust"], warehouse=w["wh"], rep=w["rep"], source="rep_app", payment_mode="cash",
                                    lines=[{"variant": w["tinta"], "qty": D("2"), "unit_price": None}, {"variant": w["developer"], "qty": D("2"), "unit_price": None}])
        OrderService.confirm(order)
    issue_order(api, order.pk)
    assert api.post(f"/api/v1/sales-orders/{order.pk}/invoice", {"post": True}, format="json").status_code == 201
    row = next(r for r in _summary(api)["rows"] if r["label"] == "Talha")
    assert D(row["sales"]) == D("300") and D(row["outstanding"]) == D("300"), "not 600"
