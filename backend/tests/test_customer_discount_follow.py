"""
Owner, 27 Oct 2026 (TDN-SO-00055: "fix it and fix for the project as well").

1. A customer's NEW standing discount reaches their documents that are not on the books yet — open orders still on
   the old rate and their draft invoices — while a % set by hand stays, and posted invoices are only listed.
2. An office edit that merely echoes a line's stored price is not a "price request": the line takes today's list
   price without a false price-difference flag. A price somebody types is still a request.
"""
from decimal import Decimal

import pytest

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db
D = Decimal


def _order(api, wh, cust, v, qty="10", **extra):
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(v.id), "qty": qty}], **extra}, format="json")
    assert r.status_code == 201, r.content
    return r.data


def _invoiced(api, wh, cust, v, post):
    o = _order(api, wh, cust, v, qty="2")
    pl = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json").data
    api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl["lines"]]}, format="json")
    inv = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": post}, format="json").data[0]
    return o, inv


def test_a_new_standing_discount_follows_business_in_flight_books_included(api, tenant):
    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    following = _order(api, wh, cust, v)                         # 10 × 150 at the customer's 0%
    by_hand = _order(api, wh, cust, v, discount_pct="5")         # a % somebody chose for this order
    _drafted_order, draft = _invoiced(api, wh, cust, v, post=False)
    in_flight_order, in_flight = _invoiced(api, wh, cust, v, post=True)   # posted, goods not yet delivered
    delivered = _order(api, wh, cust, v, qty="2")
    api.post(f"/api/v1/sales-orders/{delivered['id']}/mark-delivered", {"receiver_name": "Shop"}, format="json")
    settled = api.get("/api/v1/invoices", {"order": delivered["id"]}).data["results"][0]
    assert api.post(f"/api/v1/invoices/{settled['id']}/post", {}, format="json").status_code == 200
    assert D(following["total"]) == D("1500") and D(by_hand["total"]) == D("1425")

    r = api.patch(f"/api/v1/customers/{cust.id}", {"default_discount_pct": "10"}, format="json")
    assert r.status_code == 200, r.content

    o = api.get(f"/api/v1/sales-orders/{following['id']}").data
    assert D(o["discount_pct"]) == D("10") and D(o["total"]) == D("1350"), "the open order follows the new rate"
    assert D(api.get(f"/api/v1/sales-orders/{by_hand['id']}").data["total"]) == D("1425"), "a hand-set % stays"
    d = api.get(f"/api/v1/invoices/{draft['id']}").data
    assert d["status"] == "draft" and D(d["discount_pct"]) == D("10") and D(d["total"]) == D("270"), "the draft invoice follows"

    # in flight: reopened, re-discounted and posted again — the ledger follows
    f = api.get(f"/api/v1/invoices/{in_flight['id']}").data
    assert f["status"] == "posted" and D(f["discount_pct"]) == D("10") and D(f["total"]) == D("270"), f
    assert f["journal"] == in_flight["journal"], "the invoice's own entry is rewritten, not a new one (27 Oct 2026)"
    assert D(api.get(f"/api/v1/sales-orders/{in_flight_order['id']}").data["total"]) == D("270")
    with tenant_context(tenant, tenant.owner):
        from apps.accounting.models import GL_LIVE, JournalLine
        from apps.accounting.services import account_for
        from django.db.models import Sum

        ar = JournalLine.objects.filter(entry__source_id=in_flight["id"], entry__status__in=GL_LIVE, account=account_for("ar")).aggregate(dr=Sum("debit"), cr=Sum("credit"))
        rev = JournalLine.objects.filter(entry__source_type="JournalEntry", entry__memo__contains=in_flight["number"], account=account_for("ar")).aggregate(dr=Sum("debit"), cr=Sum("credit"))
        net = (ar["dr"] or 0) - (ar["cr"] or 0) + (rev["dr"] or 0) - (rev["cr"] or 0)
        assert net == D("270"), f"the receivable on the books is the re-discounted figure, got {net}"

    # delivered and posted: settled business stays at the old % and is listed
    s2 = api.get(f"/api/v1/invoices/{settled['id']}").data
    assert s2["status"] == "posted" and D(s2["total"]) == D("300")
    with tenant_context(tenant, tenant.owner):
        from apps.core.models import Notification

        n = Notification.objects.filter(type="customer.discount_changed").first()
        assert n is not None and settled["number"] in n.body and in_flight["number"] in n.body and following["number"] in n.body, n and n.body


def test_an_echoed_stored_price_is_not_a_price_request(api, tenant):
    from apps.catalog.models import ProductVariant
    from apps.sales.services import OrderService

    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        tenant.settings = {**(tenant.settings or {}), "accept_external_prices": "flag"}
        tenant.save(update_fields=["settings"])
        # the rep app sends no price: the server prices the line from the list
        order = OrderService.create(customer=cust, lines=[{"variant": v, "qty": D("4"), "unit_price": None}], source="rep_app", warehouse=wh)
        assert order.lines.get().unit_price == D("150") and order.lines.get().requested_price is None
        ProductVariant.objects.filter(pk=v.pk).update(sale_price=D("180"))   # the list moves after the order was taken

    # the office edits and the form sends the stored 150 back — an echo, not a request
    r = api.patch(f"/api/v1/sales-orders/{order.id}", {"lines": [{"variant": str(v.id), "qty": "4", "unit_price": "150"}]}, format="json")
    assert r.status_code == 200, r.content
    assert r.data["price_flags"] == [] and D(r.data["lines"][0]["unit_price"]) == D("180"), r.data["price_flags"]

    # a price somebody actually types is still a request, and the difference is flagged
    r = api.patch(f"/api/v1/sales-orders/{order.id}", {"lines": [{"variant": str(v.id), "qty": "4", "unit_price": "140"}]}, format="json")
    assert r.status_code == 200, r.content
    assert len(r.data["price_flags"]) == 1 and r.data["price_flags"][0]["requested"].startswith("140")


def test_who_funds_a_discount_shows_only_where_the_splits_are_set(api, tenant):
    """Owner, 29 Oct 2026: the Keune / Trafco split "not needed in customer add and edit form also in app — do not
    want to show anywhere other then the form where they are created". The customer and the rep app carry the
    total % only; Settings → Discount contributions keeps the split."""
    from apps.sales.models import DiscountContribution
    from .conftest import seed_basic

    _wh, _sup, _v, cust = seed_basic(tenant)
    with tenant_context(tenant):
        DiscountContribution.objects.get_or_create(total_pct=D("15"), defaults={"distributor_pct": D("3"), "company_pct": D("12")})
    r = api.patch(f"/api/v1/customers/{cust.id}", {"default_discount_pct": "15"}, format="json")
    assert r.status_code == 200, r.content
    assert D(r.data["default_discount_pct"]) == D("15") and "discount_split" not in r.data
    listed = next(c for c in api.get("/api/v1/customers").data["results"] if c["id"] == str(cust.id))
    assert "discount_split" not in listed

    app = api.get("/ext/v1/customers")
    assert app.status_code == 200, app.content
    row = next(x for x in app.data["results"] if x["id"] == str(cust.id))
    assert row["discount_pct"] == "15" and "discount_split" not in row

    rows = api.get("/api/v1/settings/discount-contributions").data
    rows = rows["results"] if isinstance(rows, dict) else rows
    split = next(x for x in rows if D(x["total_pct"]) == D("15"))
    assert (D(split["company_pct"]), D(split["distributor_pct"])) == (D("12"), D("3")), "the split lives where it is set"


def test_whoever_edits_a_customer_gets_the_discount_choices_without_the_settings_page(api, tenant):
    """TDN, 29 Oct 2026: supervisor@… (Manager: customers yes, Settings no) saw an empty Discount dropdown on the
    customer form — its % list was the Settings page's. The totals are now readable by whoever fills one in."""
    from rest_framework.test import APIClient

    from apps.accounts.models import User
    from apps.sales.models import DiscountContribution

    with tenant_context(tenant):
        DiscountContribution.objects.get_or_create(total_pct=D("15"), defaults={"distributor_pct": D("3"), "company_pct": D("12")})
    role = api.post("/api/v1/roles", {"name": "Customer desk", "role_type": "internal",
                                      "permissions": ["customers.view_customer", "customers.change_customer"]}, format="json")
    assert role.status_code == 201, role.content
    made = api.post("/api/v1/users", {"full_name": "Desk", "email": "desk@probe.pk", "password": "Abcd-1234!", "roles": [role.data["id"]]}, format="json")
    assert made.status_code == 201, made.content
    User.objects.filter(email="desk@probe.pk").update(must_change_password=False)
    c = APIClient()
    tok = c.post("/api/v1/auth/login", {"identifier": "desk@probe.pk", "password": "Abcd-1234!"}, format="json").data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}", HTTP_X_TENANT_ID=str(tenant.id))

    r = c.get("/api/v1/settings/discount-contributions/rates")
    assert r.status_code == 200, r.content
    assert "15" in {str(Decimal(x["total_pct"]).normalize()) for x in r.data}
    assert all(set(x) == {"id", "total_pct"} for x in r.data), "totals only — who funds them stays in Settings"
    assert c.get("/api/v1/settings/discount-contributions").status_code == 403, "the Settings list stays the Settings page's"
