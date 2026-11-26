"""
The reports' rep filter reads Sale Person, on the page and in the printed heading, and Sales by Sale Person opens on the
month so far (owner, 25 Nov 2026). The heading used to print "Rep: 01a14e44-3831-…", the id instead of the name.
"""
import pytest
from django.utils import timezone

from .test_sales_by_rep import _billed, _order, excel_on, world  # noqa: F401 — fixtures

pytestmark = pytest.mark.django_db


@pytest.fixture
def printed(monkeypatch):
    from django.template import loader

    seen = {}
    real = loader.render_to_string

    def spy(name, context=None, *a, **k):
        seen.update(context or {})
        return real(name, context, *a, **k)

    monkeypatch.setattr(loader, "render_to_string", spy)
    return seen


def test_every_rep_filter_is_called_sale_person(api, tenant):
    reps = [(r["key"], p) for r in api.get("/api/v1/reports").data for p in r["params"] if p["type"] == "rep"]
    assert {k for k, _p in reps} >= {"daily_sales", "pending_payments", "rep_daily_sheet", "out_of_area_activity"}
    assert all(p["label"] == "Sale Person" for _k, p in reps)


def test_sales_by_sale_person_opens_on_the_month_and_its_daily_sheet_on_today(api, tenant):
    opens = {r["key"]: r["opens_on"] for r in api.get("/api/v1/reports").data}
    assert opens["sales_by_rep"] == "month" and opens["trial_balance"] == "month"
    assert opens["rep_daily_sheet"] == "today" and opens["sales_by_customer"] == "today" and opens["daily_sales"] == "today"


def test_the_printed_heading_names_the_sale_person_and_prints_no_link_column(api, excel_on, world, printed):
    w = world
    _billed(api, excel_on, w, w["rep"], "2", "cash")
    on = timezone.localdate()
    r = api.get("/api/v1/reports/daily_sales", {"date_from": on.isoformat(), "date_to": on.isoformat(), "rep": str(w["rep"].id), "format": "pdf"})
    assert r.status_code == 200
    assert printed["params"]["Sale Person"] == "Talha" and "rep" not in printed["params"]
    assert str(w["rep"].id) not in str(printed["params"])
    assert "view" not in printed["cols"] and printed["cols"][-1] == "grand_total"
    assert len(printed["rows"][0]["cells"]) == len(printed["cols"])


def test_another_filter_prints_its_name_too(api, excel_on, world, printed):
    w = world
    _billed(api, excel_on, w, w["rep"], "2", "credit")
    r = api.get("/api/v1/reports/pending_payments", {"due_within": "All pending", "customer": str(w["cust"].id), "format": "pdf"})
    assert r.status_code == 200
    assert printed["params"]["customer"] == w["cust"].name and printed["params"]["Show"] == "All pending" and "view" not in printed["cols"]


def test_the_daily_sheet_of_one_person_still_names_them_in_its_title_only(api, excel_on, world, printed):
    w = world
    on = timezone.localdate()
    _order(excel_on, w, on, w["rep"], [(w["tinta"], "4")])
    assert api.get("/api/v1/reports/rep_daily_sheet", {"date_from": on.isoformat(), "date_to": on.isoformat(), "rep": str(w["rep"].id), "format": "pdf"}).status_code == 200
    assert set(printed["params"]) == {"date_from", "date_to"} and printed["title"].endswith("Talha")


def test_the_dropdown_lists_sale_persons_only(api, tenant):
    """Owner, 27 Nov 2026: "only Sale Person will show" — TDN's list carried Muhammad Imran (a Manager, twice) and Irfan
    Aleem (a Supplier), who have rep records but do not sell."""
    from django.contrib.auth.models import Group

    from apps.accounts.models import TenantMembership, User
    from apps.core.tenancy import tenant_context
    from apps.partners.models import SupplierRep

    from .conftest import seed_basic

    _wh, sup, _v, _c = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        made = {}
        for name, role in (("Talha", "Sales"), ("Farhan", "Sales Coordinator"), ("Keune Man", "Manufacturer Rep"), ("Imran", "Manager"), ("Irfan", "SUPPLIER"), ("No Login", None)):
            rep = SupplierRep.objects.create(supplier=sup, code=f"R-{len(made)}", name=name, phone=f"0300111000{len(made)}")
            if role:
                user = User.objects.create_user(username=f"u{len(made)}", email=f"u{len(made)}@x.pk", password="x-12345678")
                rep.user = user
                rep.save(update_fields=["user"])
                m = TenantMembership.objects.create(user=user, tenant=tenant, supplier_rep=rep)
                m.groups.add(Group.objects.get_or_create(name=f"{tenant.slug}:{role}")[0])
            made[name] = rep
    names = {r["name"] for r in api.get("/api/v1/supplier-reps", {"sale_persons": 1, "page_size": 100}).data["results"]}
    assert {"Talha", "Farhan", "Keune Man", "No Login"} <= names and not {"Imran", "Irfan"} & names, names
    assert {"Imran", "Irfan"} <= {r["name"] for r in api.get("/api/v1/supplier-reps", {"page_size": 100}).data["results"]}, "the reps page still lists everyone"
