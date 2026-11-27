"""
Owner, 27 Nov 2026 — the settlement discount in the rep app, "same words, same Rs 50 limit, never over the limit for a rep":

- the rep can read a shop's open invoices (GET /ext/v1/customers/{id}/invoices?open=1): Collect Cash said "No open invoices
  — this will sit as an advance" for a shop that owed, because both lists it used answered 403 to a rep
- POST /ext/v1/collections with settle_short: the last invoice the money reaches, left short by no more than the limit,
  is closed to Settlement Discount (4-01-001-0004); never for a cheque, never above the limit
"""
from decimal import Decimal as D

import pytest
from rest_framework.test import APIClient

from apps.core.tenancy import tenant_context

from .test_returns import _issued_invoice

pytestmark = pytest.mark.django_db
PIN = (31.5204, 74.3587)


@pytest.fixture
def rep_shop(api, tenant):
    from apps.accounts.models import TenantMembership, User
    from apps.accounts.services import RoleService, UserService
    from apps.partners.models import SupplierRep

    from .conftest import seed_basic

    wh, supplier, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        rep = SupplierRep.objects.create(supplier=supplier, code="R-SET", name="Settling Rep")
        cust.gps_lat, cust.gps_lng = D(str(PIN[0])), D(str(PIN[1]))
        cust.save(update_fields=["gps_lat", "gps_lng"])
        cust.assigned_reps.add(rep)
        role = RoleService.create_role(tenant, "Collector", ["external.view_assigned_customers", "external.view_customer_outstanding",
                                                             "external.record_collection", "external.view_own_collections"], role_type="external")
        UserService.create_tenant_user(tenant, "Settling Rep", email="settle@probe.pk", password="Abcd-1234", roles=[role], warehouses=[])
        user = User.objects.get(email="settle@probe.pk")
        User.objects.filter(pk=user.pk).update(must_change_password=False)
        rep.user, rep.role = user, role
        rep.save(update_fields=["user", "role"])
        TenantMembership.objects.filter(user=user, tenant=tenant).update(supplier_rep=rep)
    phone = APIClient()
    tok = phone.post("/api/v1/auth/login", {"identifier": "settle@probe.pk", "password": "Abcd-1234"}, format="json").data["access"]
    phone.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}", HTTP_X_TENANT_ID=str(tenant.id))
    _o1, inv1 = _issued_invoice(api, wh, cust, v, qty="2")
    _o2, inv2 = _issued_invoice(api, wh, cust, v, qty="3")
    return {"phone": phone, "cust": cust, "inv1": inv1, "inv2": inv2}


def _collect(s, amount, **more):
    return s["phone"].post("/ext/v1/collections", {"customer": str(s["cust"].id), "amount": str(amount), "method": more.pop("method", "cash"),
                                                   "gps": {"lat": PIN[0], "lng": PIN[1], "accuracy_m": 8}, **more}, format="json")


def _invoice(api, inv):
    return api.get(f"/api/v1/invoices/{inv['id']}").data


def test_a_rep_reads_the_shops_open_invoices_oldest_first(rep_shop):
    s = rep_shop
    r = s["phone"].get(f"/ext/v1/customers/{s['cust'].id}/invoices", {"open": 1})
    assert r.status_code == 200, r.content
    rows = r.data["results"]
    assert [x["number"] for x in rows] == [s["inv1"]["number"], s["inv2"]["number"]]
    assert D(rows[0]["balance"]) == D(s["inv1"]["total"]) and rows[0]["settlement_limit"] == "50" and r.data["settlement_limit"] == "50"
    assert {"id", "number", "date", "due_date", "total", "balance", "days_overdue", "status"} <= set(rows[0])


def test_a_shop_that_is_not_the_reps_is_not_found(rep_shop, tenant):
    from apps.partners.models import Customer

    with tenant_context(tenant, tenant.owner):
        other = Customer.objects.create(code="C-X", name="Not Mine", credit_limit=D("1000"))
    assert rep_shop["phone"].get(f"/ext/v1/customers/{other.id}/invoices").status_code == 404


def test_close_invoice_short_with_a_settlement_discount(api, tenant, rep_shop):
    from apps.accounting.models import JournalLine

    s = rep_shop
    owed = D(s["inv1"]["total"]) + D(s["inv2"]["total"])
    r = _collect(s, owed - 4, settle_short=True)
    assert r.status_code == 201, r.content
    assert D(r.data["settled_amount"]) == 4 and r.data["settled_invoice"] == s["inv2"]["number"]
    second = _invoice(api, s["inv2"])
    assert second["status"] == "paid" and D(second["settled_amount"]) == 4 and D(second["balance"]) == 0
    with tenant_context(tenant, tenant.owner):
        line = JournalLine.objects.get(account__code="4-01-001-0004", entry__source_id=r.data["id"])
    assert line.debit == 4
    rep = api.get("/api/v1/reports/settlement_discounts", {"date_from": r.data["date"], "date_to": r.data["date"]}).data
    assert [(x["receipt"], x["invoice"], D(x["settlement"])) for x in rep["rows"]] == [(r.data["number"], s["inv2"]["number"], D("4"))]


def test_keep_due_leaves_it_owed(api, rep_shop):
    s = rep_shop
    r = _collect(s, D(s["inv1"]["total"]) + D(s["inv2"]["total"]) - 4)
    assert r.status_code == 201 and D(r.data["settled_amount"]) == 0 and r.data["settled_invoice"] is None
    assert D(_invoice(api, s["inv2"])["balance"]) == 4


def test_never_for_a_cheque_and_never_above_the_limit(api, rep_shop):
    s = rep_shop
    owed = D(s["inv1"]["total"]) + D(s["inv2"]["total"])
    cheque = _collect(s, owed - 4, settle_short=True, method="cheque", reference="CHQ-1")
    assert cheque.status_code == 422 and cheque.json()["type"] == "settlement_not_allowed"
    over = _collect(s, owed - 60, settle_short=True)
    assert over.status_code == 422 and over.json()["type"] == "settlement_not_allowed" and "Keep it due" in over.json()["detail"]
    assert D(_invoice(api, s["inv2"])["balance"]) == D(s["inv2"]["total"]), "a refused collection changed nothing"
