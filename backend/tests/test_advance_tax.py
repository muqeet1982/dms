"""
Advance income tax, section 236G/236H (owner, 31 Oct 2026: "in order and invoice it should also show advance tax …
advance income tax on Grand Total"): the customer's own %, on the Grand Total (net + GST + FED + Extra + Further),
shown after it and added to Total Payable; on a tax invoice only (owner, 8 Nov 2026), none on a plain one. Booked to Advance Income Tax
Payable, reversed by returns to the paisa, never in the FBR sales-tax filing, and reported.
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

import pytest

from apps.core.tenancy import tenant_context

from .conftest import deliver_invoice
from .test_fbr_other_taxes import _issue, world  # noqa: F401  (world is a fixture)

pytestmark = pytest.mark.django_db
D = Decimal


def _rate(tenant, cust, pct):
    from apps.partners.models import Customer

    with tenant_context(tenant, tenant.owner):
        Customer.objects.filter(pk=cust.pk).update(advance_income_tax_rate=D(pct))


def _invoice(api, order_id, **body):
    made = api.post(f"/api/v1/sales-orders/{order_id}/invoice", body, format="json")
    assert made.status_code == 201, made.content
    return made.data[0] if isinstance(made.data, list) else made.data


def _journal(tenant, entry_id):
    from apps.accounting.models import JournalLine

    with tenant_context(tenant):
        lines = list(JournalLine.objects.filter(entry_id=entry_id).select_related("account"))
    assert sum(l.debit for l in lines) == sum(l.credit for l in lines), "the journal balances"
    return {l.account.code: l.debit - l.credit for l in lines}


ADV, AR = "2-01-002-0004", "1-01-003-0001"


def test_a_tax_invoice_adds_the_advance_tax_on_its_grand_total_and_books_it(api, tenant, world):
    wh, cust, v, tt = world
    _rate(tenant, cust, "0.5")
    # 600 net + GST 108 + FED 12 + Extra 6 + Further 18 = Grand Total 744; 0.5% = 3.72; less WHT 30 → 717.72
    order = _issue(api, wh, cust, v)
    assert (D(order["advance_tax_rate"]), D(order["advance_tax_total"]), D(order["total"])) == (D("0.5"), D("3.72"), D("717.72"))
    inv = _invoice(api, order["id"], apply_tax=True)
    assert (D(inv["advance_tax_total"]), D(inv["total"])) == (D("3.72"), D("717.72")), "the order and its invoice agree"
    gl = _journal(tenant, inv["journal"])
    assert gl[ADV] == D("-3.72") and gl[AR] == D("717.72")

    # FBR files the sales-tax figures only: Σ totalValues is what the buyer owes before the advance income tax
    from apps.fbr.services import build_payload
    from apps.sales.models import SalesInvoice

    filing = SimpleNamespace(seller_ntn_cnic="1234567", seller_business_name="Demo", seller_province="Punjab", seller_address="Lahore",
                             buyer_ntn_cnic="", buyer_business_name=cust.name, buyer_province="Punjab", buyer_address="Lahore",
                             buyer_registration_type="Unregistered")
    with tenant_context(tenant, tenant.owner):
        payload = build_payload(SalesInvoice.objects.get(pk=inv["id"]), filing, use_sandbox=False)
    assert sum(D(str(i["totalValues"])) for i in payload["items"]) == D("714")
    assert "advance" not in str(payload).lower()


def test_an_untaxed_invoice_carries_none(api, tenant, world):
    """Owner, 8 Nov 2026: "advance income tax will also only show when tax on on invoice"."""
    wh, cust, v, tt = world
    _rate(tenant, cust, "0.5")
    order = _issue(api, wh, cust, v, charge_tax=False)
    assert D(order["advance_tax_total"]) == 0, "nor does an order to be billed without tax"
    inv = _invoice(api, order["id"], post=True)
    assert (D(inv["advance_tax_total"]), D(inv["total"])) == (D("0"), D("600")), "no tax, no advance income tax"
    assert ADV not in _journal(tenant, inv["journal"])
    on = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": True}, format="json")
    assert on.status_code == 200 and D(on.data["advance_tax_total"]) > 0, "switched to a tax invoice, it carries it"
    off = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": False}, format="json")
    assert D(off.data["advance_tax_total"]) == 0 and D(off.data["total"]) == D("600"), "and back off, it drops it"


def test_returns_give_back_exactly_what_was_charged(api, tenant, world):
    wh, cust, v, tt = world
    _rate(tenant, cust, "0.5")
    order = _issue(api, wh, cust, v)
    inv = _invoice(api, order["id"], apply_tax=True)
    deliver_invoice(api, inv["id"])
    full = api.get(f"/api/v1/invoices/{inv['id']}").data
    line = {"variant": str(full["lines"][0]["variant"]), "unit_price": full["lines"][0]["unit_price"], "restock": True, "condition": "good"}
    one = api.post("/api/v1/credit-notes", {"customer": str(cust.id), "invoice": inv["id"], "reason": "x", "post": True, "lines": [{**line, "qty": "1"}]}, format="json")
    assert one.status_code == 201, one.content
    # a quarter of 744 = 186 → 0.93
    assert D(one.data["advance_tax_total"]) == D("0.93")
    rest = api.post("/api/v1/credit-notes", {"customer": str(cust.id), "invoice": inv["id"], "reason": "x", "post": True, "lines": [{**line, "qty": "3"}]}, format="json")
    assert rest.status_code == 201, rest.content
    assert D(one.data["advance_tax_total"]) + D(rest.data["advance_tax_total"]) == D("3.72"), "the last return takes what is left"
    assert D(one.data["total"]) + D(rest.data["total"]) == D("717.72")
    i = api.get(f"/api/v1/invoices/{inv['id']}").data
    assert i["status"] == "returned" and D(i["balance"]) == 0
    assert _journal(tenant, one.data["journal"])[ADV] == D("0.93")
    from apps.accounting.models import GL_LIVE, JournalLine

    with tenant_context(tenant):
        left = sum((l.credit - l.debit for l in JournalLine.objects.filter(account__code=ADV, entry__status__in=GL_LIVE)), D("0"))
    assert left == 0, "nothing left owed on goods that all came back"


def test_a_new_rate_reaches_what_is_not_on_the_books_and_posting_charges_the_current_one(api, tenant, world):
    wh, cust, v, tt = world
    # taxed throughout (owner, 8 Nov 2026: only a tax invoice carries it): 4 × 150 → Grand Total 744, Total Payable 714
    posted = _invoice(api, _issue(api, wh, cust, v)["id"], apply_tax=True, post=True)           # no rate yet
    draft = _invoice(api, _issue(api, wh, cust, v)["id"], apply_tax=True, post=False)
    open_order = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "charge_tax": True,
                                                   "lines": [{"variant": str(v.id), "qty": "4"}]}, format="json").data
    plain_order = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "charge_tax": False,
                                                    "lines": [{"variant": str(v.id), "qty": "2"}]}, format="json").data
    assert D(posted["total"]) == D("714") and D(draft["total"]) == D("714")
    r = api.patch(f"/api/v1/customers/{cust.id}", {"advance_income_tax_rate": "1.00"}, format="json")
    assert r.status_code == 200, r.content
    assert D(api.get(f"/api/v1/sales-orders/{open_order['id']}").data["total"]) == D("721.44"), "714 + 1% of 744"
    assert D(api.get(f"/api/v1/invoices/{draft['id']}").data["total"]) == D("721.44")
    assert D(api.get(f"/api/v1/invoices/{posted['id']}").data["total"]) == D("714"), "a posted invoice keeps what it was charged"
    assert D(api.get(f"/api/v1/sales-orders/{plain_order['id']}").data["advance_tax_total"]) == 0, "an order without tax takes none"
    # a draft goes to the books at the rate standing when it is posted, whatever it held
    _rate(tenant, cust, "2")
    p = api.post(f"/api/v1/invoices/{draft['id']}/post", {}, format="json")
    assert p.status_code == 200 and D(p.data["total"]) == D("728.88") and D(p.data["advance_tax_rate"]) == D("2")
    assert _journal(tenant, p.data["journal"])[ADV] == D("-14.88")


def test_print_excel_preview_and_reports_show_it(api, tenant, world):
    wh, cust, v, tt = world
    _rate(tenant, cust, "0.5")
    order = _issue(api, wh, cust, v)
    inv = _invoice(api, order["id"], apply_tax=True)
    seen = {}

    def capture(request, template, ctx, *a, **kw):
        from django.http import HttpResponse
        from django.template.loader import render_to_string

        seen["html"] = render_to_string(template, {**ctx, "tenant": tenant, "logo_url": None, "printed_at": "", "doc_title": "", "doc_ref": ""})
        seen.update(ctx)
        return HttpResponse(b"")

    with mock.patch("apps.sales.documents.render_doc", side_effect=capture):
        assert api.get(f"/api/v1/invoices/{inv['id']}/pdf").status_code == 200
    html = seen["html"]
    assert "Advance Income Tax (0.5%)" in html and "236G/H" in html and "3.72" in html and seen["grand_total"] == D("744")
    assert html.index("Grand Total") < html.index("Advance Income Tax") < html.index("Total Payable")
    with mock.patch("apps.sales.documents.render_doc", side_effect=capture):
        assert api.get(f"/api/v1/sales-orders/{order['id']}/pdf").status_code == 200
    assert "Advance Income Tax (0.5%)" in seen["html"]

    # the app's live price (orders/preview) prices it the same way
    from apps.partners.models import Customer
    from apps.sales.services import OrderService

    with tenant_context(tenant, tenant.owner):
        pv = OrderService.preview(Customer.objects.get(pk=cust.pk), [{"variant": v, "qty": "4"}])
    assert (D(pv["grand_total"]), D(pv["advance_tax_total"]), D(pv["total"])) == (D("600"), D("0"), D("600")), "the customer prices untaxed: no advance income tax"

    reg = api.get("/api/v1/reports/advance_tax_register").data
    assert [(r["number"], D(r["advance_tax"])) for r in reg["rows"]] == [(inv["number"], D("3.72"))] and D(reg["totals"]["advance_tax"]) == D("3.72")
    assert D(reg["rows"][0]["grand_total"]) == D("744")
    st = api.get("/api/v1/reports/sales_tax_register").data
    assert "advance_tax" in st["columns"] and D(st["totals"]["advance_tax"]) == D("3.72")
    summary = api.get("/api/v1/reports/tax_summary").data
    assert any(r["code"] == ADV and D(r["net_payable"]) == D("3.72") for r in summary["rows"])
