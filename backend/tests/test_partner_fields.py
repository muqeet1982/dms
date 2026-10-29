"""Legal details, phone numbers, city list, posted opening balances and the customer's standing discount."""
from datetime import date
from decimal import Decimal

import pytest

from apps.core.tenancy import tenant_context

from .conftest import seed_basic

pytestmark = pytest.mark.django_db


def test_customer_carries_legal_details_and_three_numbers(api, tenant):
    body = {
        "name": "Al-Madina Store", "type": "retailer", "business_name": "Al-Madina Traders (Pvt) Ltd",
        "ntn": "1234567-8", "strn": "03-01-2345-678-90", "cnic": "35202-1234567-1", "filer_status": "filer",
        "phone": "+923001234567", "alt_mobile": "+923211234567", "landline": "+924235881234", "city": "Lahore",
    }
    r = api.post("/api/v1/customers", body, format="json")
    assert r.status_code == 201, r.content
    for key, value in body.items():
        assert r.data[key] == value, key
    # searchable by any of them
    assert api.get("/api/v1/customers", {"search": "1234567-8"}).data["count"] == 1
    assert api.get("/api/v1/customers", {"search": "+923211234567"}).data["count"] == 1


def test_supplier_carries_the_same_details(api, tenant):
    r = api.post("/api/v1/suppliers", {"name": "Keune Pakistan", "business_name": "Keune Pakistan (Pvt) Ltd", "ntn": "7654321-0", "strn": "03-09-8765-432-10", "cnic": "", "filer_status": "filer", "phone": "+923009999999", "alt_mobile": "+923339999999", "landline": "+922135881234", "city": "Karachi"}, format="json")
    assert r.status_code == 201, r.content
    assert r.data["ntn"] == "7654321-0" and r.data["landline"] == "+922135881234" and r.data["filer_status"] == "filer"


def test_city_list_is_searchable(api, db):
    from django.core.management import call_command

    call_command("seed_cities")
    rows = api.get("/api/v1/cities", {"search": "lah"}).data
    assert any(c["name"] == "Lahore" and c["province"] == "Punjab" for c in rows)
    assert api.get("/api/v1/cities", {"search": "kar"}).data[0]["name"] == "Karachi"  # prefix match ranks first
    assert len(api.get("/api/v1/cities", {"province": "Sindh"}).data) > 5


def test_opening_balance_is_posted_to_the_ledger(api, tenant):
    from apps.accounting.models import JournalEntry
    from apps.partners.models import Customer

    r = api.post("/api/v1/customers", {"cnic": "35202-9999999-9", "name": "Opening Co", "type": "retailer", "opening_balance": "25000", "opening_balance_date": "2026-07-01"}, format="json")
    assert r.status_code == 201, r.content
    with tenant_context(tenant, tenant.owner):
        c = Customer.objects.get(pk=r.data["id"])
        assert c.opening_balance == Decimal("25000") and c.opening_balance_date == date(2026, 7, 1)
        entry = c.opening_journal
        assert entry is not None and entry.status == "posted" and str(entry.date) == "2026-07-01"
        lines = list(entry.lines.all())
        assert sum(l.debit for l in lines) == Decimal("25000") == sum(l.credit for l in lines)
        assert any(l.partner_type == "customer" and l.partner_id == str(c.id) and l.debit == Decimal("25000") for l in lines)
    assert Decimal(api.get(f"/api/v1/customers/{r.data['id']}").data["outstanding"]) == Decimal("25000")
    assert len(api.get(f"/api/v1/customers/{r.data['id']}/statement").data["ledger"]["rows"]) == 1

    # correcting it replaces the entry instead of stacking a second one
    r2 = api.patch(f"/api/v1/customers/{r.data['id']}", {"opening_balance": "10000", "opening_balance_date": "2026-07-01"}, format="json")
    assert r2.status_code == 200
    with tenant_context(tenant, tenant.owner):
        c.refresh_from_db()
        assert c.opening_balance == Decimal("10000")
        assert JournalEntry.objects.filter(type="opening", status="posted").count() == 1
    assert Decimal(api.get(f"/api/v1/customers/{r.data['id']}").data["outstanding"]) == Decimal("10000")


def test_customer_standing_discount_applies_and_can_be_dropped(api, tenant):
    wh, sup, variant, customer = seed_basic(tenant)
    api.patch(f"/api/v1/customers/{customer.id}", {"default_discount_pct": "5"}, format="json")
    body = {"customer": str(customer.id), "warehouse": str(wh.id), "lines": [{"variant": str(variant.id), "qty": "10"}]}
    with_discount = api.post("/api/v1/sales-orders", body, format="json")
    assert with_discount.status_code == 201, with_discount.content
    assert Decimal(with_discount.data["lines"][0]["discount_pct"]) == Decimal("5")
    assert Decimal(with_discount.data["discount"]) > 0

    without = api.post("/api/v1/sales-orders", {**body, "apply_customer_discount": False}, format="json")
    assert Decimal(without.data["lines"][0]["discount_pct"]) == Decimal("0") and Decimal(without.data["discount"]) == Decimal("0")

    # a line that carries its own discount keeps it
    own = api.post("/api/v1/sales-orders", {**body, "lines": [{"variant": str(variant.id), "qty": "10", "discount_pct": "12"}]}, format="json")
    assert Decimal(own.data["lines"][0]["discount_pct"]) == Decimal("12")


def test_a_customer_with_no_order_radius_saves_and_a_bad_one_says_which_field(api, tenant):
    """TDN, 29 Oct 2026: "order radius m: a valid integer is required" on every save of a customer with no radius
    of its own — the form sends the empty value back as "". Empty now means none; a mistake names the field."""
    from .conftest import seed_basic

    _wh, _sup, _v, cust = seed_basic(tenant)
    r = api.patch(f"/api/v1/customers/{cust.id}", {"order_radius_m": "", "gps_lat": "", "gps_lng": ""}, format="json")
    assert r.status_code == 200, r.content
    assert r.data["order_radius_m"] is None and r.data["gps_lat"] is None, "no pin and no radius are both fine"
    assert api.patch(f"/api/v1/customers/{cust.id}", {"order_radius_m": 49.6}, format="json").data["order_radius_m"] == 50
    bad = api.patch(f"/api/v1/customers/{cust.id}", {"order_radius_m": "abc"}, format="json")
    assert bad.status_code == 400 and "Order radius must be a whole number" in str(bad.content)
