"""
The two documents a customer actually receives: the sales order and the invoice.

Both are printed from `templates/_doc.html`, so these tests are mostly a guard against a template that renders
on a developer's one test row and then falls over on a real document — a missing field, an empty batch, a
customer with no tax registration. They render the real PDF rather than the HTML, because WeasyPrint is where a
broken style or an unresolvable image actually fails.
"""
import pytest
from decimal import Decimal

pytestmark = pytest.mark.django_db


@pytest.fixture
def invoiced(api, tenant):
    """An order taken all the way to a posted invoice, which is the only way an invoice exists."""
    from .conftest import seed_basic

    wh, _sup, variant, customer = seed_basic(tenant)
    r = api.post("/api/v1/sales-orders", {"customer": str(customer.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(variant.id), "qty": "5"}]}, format="json")
    assert r.status_code == 201, r.content
    order = r.data
    pl = api.post(f"/api/v1/sales-orders/{order['id']}/load-form", {}, format="json")
    assert pl.status_code in (200, 201), pl.content
    form = pl.data if isinstance(pl.data, dict) else pl.data[0]
    issues = [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in form["lines"]]
    assert api.post(f"/api/v1/pick-lists/{form['id']}/issue", {"issues": issues}, format="json").status_code == 200
    made = api.post(f"/api/v1/sales-orders/{order['id']}/invoice", {}, format="json")
    assert made.status_code == 201, made.content
    return order, made.data[0]


def _is_pdf(resp):
    assert resp.status_code == 200, resp.content
    assert resp["Content-Type"] == "application/pdf"
    body = b"".join(resp.streaming_content) if resp.streaming_content else resp.content if hasattr(resp, "content") else b""
    assert body.startswith(b"%PDF-"), "not a PDF"
    assert len(body) > 2000, "a PDF this small did not render the document"
    return body


def test_the_order_prints(api, invoiced):
    order, _inv = invoiced
    resp = api.get(f"/api/v1/sales-orders/{order['id']}/pdf")
    assert resp.status_code == 200, resp.content
    assert resp["Content-Type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF-") and len(resp.content) > 2000
    assert "inline" in resp["Content-Disposition"] and order["number"] in resp["Content-Disposition"]


def test_the_invoice_prints(api, invoiced):
    _order, inv = invoiced
    resp = api.get(f"/api/v1/invoices/{inv['id']}/pdf")
    assert resp.status_code == 200, resp.content
    assert resp.content.startswith(b"%PDF-") and len(resp.content) > 2000
    assert inv["number"] in resp["Content-Disposition"]


def test_a_document_prints_for_a_customer_with_nothing_filled_in(api, tenant, invoiced):
    """No address, no tax numbers, no contact — the template must not depend on any of it."""
    from apps.core.tenancy import tenant_context

    _order, inv = invoiced
    with tenant_context(tenant, tenant.owner):
        from apps.sales.models import SalesInvoice

        invoice = SalesInvoice.objects.get(pk=inv["id"])
        c = invoice.customer
        c.address = c.city = c.phone = c.contact_person = c.ntn = c.strn = c.cnic = ""
        c.tax_numbers = {}
        c.business_name = ""
        c.save()
    assert api.get(f"/api/v1/invoices/{inv['id']}/pdf").content.startswith(b"%PDF-")


def test_the_total_is_written_out_in_words():
    """The line a customer disputes. Lakh and crore, because that is how the amount is read here."""
    from apps.core.words import amount_in_words

    assert amount_in_words("4916.60") == "Rupees Four Thousand Nine Hundred and Sixteen and Paisa Sixty Only"
    assert amount_in_words(325000) == "Rupees Three Lakh Twenty Five Thousand Only"
    assert amount_in_words("12500000") == "Rupees One Crore Twenty Five Lakh Only"
    assert amount_in_words(None) == "" and amount_in_words(0) == "Rupees Zero Only"


def test_an_invoice_takes_a_new_due_date_and_notes_but_not_a_new_total(api, invoiced):
    """
    What "edit" means on a posted invoice. The figures are in the ledger and owed by the customer, so they are
    corrected with a credit note — but when it falls due and what is written on it are still the office's to fix.
    """
    _order, inv = invoiced
    before_total = Decimal(inv["total"])

    saved = api.patch(f"/api/v1/invoices/{inv['id']}", {"due_date": "2027-01-31", "notes": "Agreed 60 days with the owner."}, format="json")
    assert saved.status_code == 200, saved.content

    again = api.get(f"/api/v1/invoices/{inv['id']}")
    assert again.data["due_date"] == "2027-01-31"
    assert again.data["notes"] == "Agreed 60 days with the owner."
    assert Decimal(again.data["total"]) == before_total

    # the amounts are not editable, and saying so by ignoring them beats a silent change
    api.patch(f"/api/v1/invoices/{inv['id']}", {"total": "1.00", "subtotal": "1.00"}, format="json")
    assert Decimal(api.get(f"/api/v1/invoices/{inv['id']}").data["total"]) == before_total

    # a due date before the invoice date is refused with the reason
    bad = api.patch(f"/api/v1/invoices/{inv['id']}", {"due_date": "2000-01-01"}, format="json")
    assert bad.status_code == 400 and "due date" in str(bad.data).lower()

    # and a full replace is not the way in
    assert api.put(f"/api/v1/invoices/{inv['id']}", {"due_date": "2027-02-01"}, format="json").status_code in (405, 400)


def test_a_cancelled_invoice_is_not_editable(api, invoiced):
    _order, inv = invoiced
    assert api.post(f"/api/v1/invoices/{inv['id']}/cancel", {"reason": "duplicate"}, format="json").status_code == 200
    refused = api.patch(f"/api/v1/invoices/{inv['id']}", {"notes": "after the fact"}, format="json")
    assert refused.status_code == 400 and refused.data["type"] == "cancelled"


@pytest.fixture
def draft_invoice(api, tenant):
    """An invoice raised but deliberately not posted — the state in which its figures can still be corrected."""
    from .conftest import seed_basic

    wh, _sup, variant, customer = seed_basic(tenant)
    r = api.post("/api/v1/sales-orders", {"customer": str(customer.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(variant.id), "qty": "5"}]}, format="json")
    assert r.status_code == 201, r.content
    order = r.data
    form = api.post(f"/api/v1/sales-orders/{order['id']}/load-form", {}, format="json").data
    form = form if isinstance(form, dict) else form[0]
    issues = [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in form["lines"]]
    assert api.post(f"/api/v1/pick-lists/{form['id']}/issue", {"issues": issues}, format="json").status_code == 200
    made = api.post(f"/api/v1/sales-orders/{order['id']}/invoice", {"post": False}, format="json")
    assert made.status_code == 201, made.content
    assert made.data[0]["status"] == "draft", "asked for a draft and got something else"
    return made.data[0]


def test_a_draft_invoice_takes_a_corrected_price(api, draft_invoice):
    """
    The owner's case: the invoice is wrong and has not gone anywhere yet. Correcting the price recomputes the
    tax from the corrected figure rather than scaling the old one, because a price can cross a tax slab.
    """
    inv = draft_invoice
    line = inv["lines"][0]
    old_rate = Decimal(line["unit_price"])

    saved = api.patch(f"/api/v1/invoices/{inv['id']}", {
        "lines": [{"id": line["id"], "unit_price": str(old_rate / 2)}],
    }, format="json")
    assert saved.status_code == 200, saved.content

    after = api.get(f"/api/v1/invoices/{inv['id']}").data
    assert Decimal(after["lines"][0]["unit_price"]) == old_rate / 2
    assert Decimal(after["subtotal"]) == Decimal(inv["subtotal"]) / 2, "the invoice total followed the line"
    if Decimal(inv["tax1_total"]):
        assert Decimal(after["tax1_total"]) < Decimal(inv["tax1_total"]), "tax was recomputed, not carried over"
    assert Decimal(after["total"]) == Decimal(after["subtotal"]) - Decimal(after["discount"]) + Decimal(after["tax1_total"]) + Decimal(after["tax2_total"])


def test_a_draft_refuses_a_discount_bigger_than_the_line(api, draft_invoice):
    inv = draft_invoice
    line = inv["lines"][0]
    too_much = Decimal(line["unit_price"]) * Decimal(line["qty"]) * 2
    bad = api.patch(f"/api/v1/invoices/{inv['id']}", {"lines": [{"id": line["id"], "discount_amount": str(too_much)}]}, format="json")
    assert bad.status_code == 400 and "more than the line is worth" in str(bad.data)


def test_posting_a_draft_fixes_its_figures(api, draft_invoice):
    """After the ledger has it, the same correction is refused — and the refusal names the way out."""
    inv = draft_invoice
    posted = api.post(f"/api/v1/invoices/{inv['id']}/post", {}, format="json")
    assert posted.status_code == 200, posted.content
    assert posted.data["status"] in ("posted", "partially_paid") and posted.data["journal_number"]

    refused = api.patch(f"/api/v1/invoices/{inv['id']}", {
        "lines": [{"id": inv["lines"][0]["id"], "unit_price": "1"}],
    }, format="json")
    assert refused.status_code == 400, refused.content
    assert "credit note" in str(refused.data)

    # the due date and the notes are still the office's to fix, posted or not
    assert api.patch(f"/api/v1/invoices/{inv['id']}", {"notes": "Posted and noted."}, format="json").status_code == 200


def test_invoicing_still_posts_by_default(api, tenant):
    """Nobody asked for a new step in the daily flow: without `post: false` the invoice goes straight out."""
    from .conftest import seed_basic

    wh, _sup, variant, customer = seed_basic(tenant)
    order = api.post("/api/v1/sales-orders", {"customer": str(customer.id), "warehouse": str(wh.id), "confirm": True,
                                              "lines": [{"variant": str(variant.id), "qty": "2"}]}, format="json").data
    form = api.post(f"/api/v1/sales-orders/{order['id']}/load-form", {}, format="json").data
    form = form if isinstance(form, dict) else form[0]
    api.post(f"/api/v1/pick-lists/{form['id']}/issue",
             {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in form["lines"]]}, format="json")
    made = api.post(f"/api/v1/sales-orders/{order['id']}/invoice", {}, format="json")
    assert made.status_code == 201 and made.data[0]["status"] in ("posted", "partially_paid")


def test_the_invoice_shows_no_tax_columns_or_strn(api, tenant, invoiced):
    """The owner's instruction: no Sales Tax / Further Tax columns or rows, and no STRN. Each Amount is
    tax-inclusive, so the line column sums to the Total and the page checks out with a pencil."""
    from django.template.loader import render_to_string

    from apps.core.tenancy import tenant_context

    _order, inv = invoiced
    with tenant_context(tenant, tenant.owner):
        tenant.tax_numbers = {"NTN": "1234567-8", "STRN": "03-01-9999"}
        tenant.save(update_fields=["tax_numbers"])
        from apps.sales.models import SalesInvoice

        invoice = SalesInvoice.objects.get(pk=inv["id"])
        lines = list(invoice.lines.select_related("variant__product", "batch").order_by("sequence"))
        html = render_to_string("sales/invoice.html", {"inv": invoice, "lines": lines, "tenant": tenant}).lower()
    assert "strn" not in html, "STRN must not appear on the sales invoice"
    assert "sales tax" not in html and "further tax" not in html, "tax columns/rows must be gone"
    assert sum(l.line_total for l in lines) == invoice.total, "each Amount is tax-inclusive, so they sum to the Total"


def test_the_purchase_order_shows_no_tax_columns_or_strn(api, tenant):
    """Same instruction on the PO: no Tax 1 / Tax 2 columns, no STRN, and the Amount column sums to the Total."""
    from decimal import Decimal

    from django.template.loader import render_to_string

    from apps.core.tenancy import tenant_context

    from .conftest import seed_basic

    wh, supplier, variant, _customer = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        tenant.tax_numbers = {"NTN": "1234567-8", "STRN": "03-01-9999"}
        tenant.save(update_fields=["tax_numbers"])
    r = api.post("/api/v1/purchase-orders", {"supplier": str(supplier.id), "warehouse": str(wh.id),
                                             "lines": [{"variant": str(variant.id), "qty_ordered": "5", "unit_price": "100"}]}, format="json")
    assert r.status_code == 201, r.content

    with tenant_context(tenant, tenant.owner):
        from apps.purchasing.models import PurchaseOrder

        po = PurchaseOrder.objects.get(pk=r.data["id"])
        lines = list(po.lines.select_related("variant__product", "packaging").order_by("sequence"))
        html = render_to_string("purchasing/po.html", {"po": po, "lines": lines, "tenant": tenant})
        low = html.lower()
        assert "strn" not in low, "STRN must not appear on the PO even though the company has one"
        assert "tax 1" not in low and "tax 2" not in low, "tax columns/rows must be gone from the PO"
        assert "ntn" in low, "NTN still belongs on the company letterhead"
        assert sum(l.line_total for l in lines) == po.total, "each Amount is tax-inclusive, so they must sum to the Total"


def test_the_sales_order_shows_no_tax_rows_or_strn(api, tenant):
    """The 'remove from all' instruction reaches the sales order too."""
    from django.template.loader import render_to_string

    from apps.core.tenancy import tenant_context

    from .conftest import seed_basic

    wh, _sup, variant, customer = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        tenant.tax_numbers = {"NTN": "1234567-8", "STRN": "03-01-9999"}
        tenant.save(update_fields=["tax_numbers"])
    r = api.post("/api/v1/sales-orders", {"customer": str(customer.id), "warehouse": str(wh.id),
                                          "lines": [{"variant": str(variant.id), "qty": "4"}]}, format="json")
    assert r.status_code == 201, r.content
    with tenant_context(tenant, tenant.owner):
        from apps.sales.models import SalesOrder

        o = SalesOrder.objects.get(pk=r.data["id"])
        lines = list(o.lines.select_related("variant__product", "packaging").order_by("sequence"))
        html = render_to_string("sales/order.html", {"o": o, "lines": lines, "tenant": tenant, "show_progress": False}).lower()
    assert "strn" not in html and "further tax" not in html and "tax 1" not in html
    assert "ntn" in html
