"""
Owner, 14 Nov 2026: "in customer when on tax invoice on then advance income tax should be 0.5". A customer whose Tax
invoice goes on with no advance income tax rate takes the company's rate (Settings → Rules, 0.5% by default); a rate typed
with it stays, and a 0 set afterwards, with the switch already on, stays 0.
"""
from decimal import Decimal as D

import pytest

pytestmark = pytest.mark.django_db


def _new(api, name, cnic, **extra):
    r = api.post("/api/v1/customers", {"name": name, "type": "retailer", "cnic": cnic, **extra}, format="json")
    assert r.status_code == 201, r.content
    return r.data


def test_a_new_tax_customer_takes_half_a_percent(api, tenant):
    assert D(_new(api, "Tax Mart", "35202-1111111-1", charge_tax=True)["advance_income_tax_rate"]) == D("0.5")
    assert D(_new(api, "Plain Mart", "35202-2222222-2")["advance_income_tax_rate"]) == D("0"), "tax off: no advance tax"
    assert D(_new(api, "Filer Mart", "35202-3333333-3", charge_tax=True, advance_income_tax_rate="1.00")["advance_income_tax_rate"]) == D("1")


def test_switching_tax_on_fills_the_rate_once(api, tenant):
    c = _new(api, "Corner Store", "35202-4444444-4")
    r = api.patch(f"/api/v1/customers/{c['id']}", {"charge_tax": True}, format="json")
    assert r.status_code == 200 and D(r.data["advance_income_tax_rate"]) == D("0.5"), r.content
    # with the switch already on, a 0 is somebody's choice
    r = api.patch(f"/api/v1/customers/{c['id']}", {"advance_income_tax_rate": "0", "charge_tax": True}, format="json")
    assert D(r.data["advance_income_tax_rate"]) == D("0")
    # off and on again: the company's rate comes back
    api.patch(f"/api/v1/customers/{c['id']}", {"charge_tax": False}, format="json")
    r = api.patch(f"/api/v1/customers/{c['id']}", {"charge_tax": True}, format="json")
    assert D(r.data["advance_income_tax_rate"]) == D("0.5")


def test_the_company_sets_the_rate(api, tenant):
    tenant.settings = {**(tenant.settings or {}), "tax_customer_advance_tax_pct": 1}
    tenant.save(update_fields=["settings"])
    assert D(_new(api, "Non Filer Mart", "35202-5555555-5", charge_tax=True)["advance_income_tax_rate"]) == D("1")
