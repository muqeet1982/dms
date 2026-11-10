"""
The sales tax invoice series (owner, 8 Nov 2026: "tax invoice should be in sequance and only set invoice number for submit tax
invoice to fbr"; "if i change tax to non tax invoice, it should change it invoice number squance ... make sure it should update
everywhere").

A company that files with FBR for real gives a tax invoice its STI number when FBR accepts it; until then every invoice carries
its order's number, so the tax switch never touches the series and the series has no gap. One that does not (or is on the sandbox) numbers a
tax invoice when it is posted; switched back to no tax, the invoice returns to its order's number, and the STI number goes back
to the series when it was the last one given. The journals, the customer's ledger and the stock ledger follow every renumbering.
"""
from decimal import Decimal
from unittest import mock

import pytest

from apps.core.tenancy import tenant_context

from .test_fbr import ACCEPT, _resp, _taxed_invoice
from .test_invoice_tax import _issued_order, _product_fbr, taxed_world  # noqa: F401  (taxed_world is a fixture)

pytestmark = pytest.mark.django_db


def _texts(tenant):
    """Every journal memo and line description, which is what the ledgers print."""
    from apps.accounting.models import JournalEntry, JournalLine

    with tenant_context(tenant):
        return " | ".join(list(JournalEntry.objects.values_list("memo", flat=True)) + list(JournalLine.objects.values_list("description", flat=True))
                          + list(JournalEntry.objects.values_list("source_number", flat=True)))


def _ledger(tenant, customer_id):
    from apps.partners.services import PartnerLedgerService

    with tenant_context(tenant):
        return " | ".join(r["description"] for r in PartnerLedgerService.ledger("customer", customer_id)["rows"])


def _tax_invoice(api, wh, cust, v, post=True):
    r = api.post(f"/api/v1/sales-orders/{_issued_order(api, wh, cust, v)}/invoice", {"apply_tax": True, "post": post}, format="json").data
    return r[0] if isinstance(r, list) else r


def _another_filed_kind(api, tenant):
    """One more posted tax invoice in the world _taxed_invoice set up."""
    from apps.catalog.models import ProductVariant
    from apps.inventory.models import Warehouse
    from apps.partners.models import Customer

    with tenant_context(tenant):
        wh, cust, v = Warehouse.objects.first(), Customer.objects.first(), ProductVariant.objects.first()
    return _tax_invoice(api, wh, cust, v)


def _switch(api, inv, on):
    """The invoice page's Tax on / Tax off on a posted invoice: reopen and switch, then post again."""
    r = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": on}, format="json")
    assert r.status_code == 200, r.content
    p = api.post(f"/api/v1/invoices/{inv['id']}/post", {}, format="json")
    assert p.status_code == 200, p.content
    return p.data


# ---------------------------------------------------------------- a company that does not file with FBR

def test_without_fbr_a_tax_invoice_is_numbered_when_posted_and_leaves_the_series_when_switched_off(api, tenant, taxed_world):
    wh, cust, v, _tt = taxed_world
    _product_fbr(tenant, v, "18%")
    inv = _tax_invoice(api, wh, cust, v)
    sti = inv["number"]
    assert "-STI-" in sti and sti.endswith("00001")
    assert sti in _texts(tenant) and sti in _ledger(tenant, cust.id)

    off = _switch(api, inv, False)
    assert "-INV-" in off["number"] and Decimal(off["tax1_total"]) == 0, "back to its order's number"
    assert off["number"].endswith(inv["order_number"].rsplit("-", 1)[-1])
    texts = _texts(tenant)
    assert sti not in texts and off["number"] in texts, "every journal quotes the new number"
    assert sti not in _ledger(tenant, cust.id) and off["number"] in _ledger(tenant, cust.id), "and so does the customer's ledger"

    nxt = _tax_invoice(api, wh, cust, v)
    assert nxt["number"] == sti, "the number went back to the series: no gap"

    on = _switch(api, off, True)
    assert "-STI-" in on["number"] and on["number"].endswith("00002"), "and the other way round, into the series"


def test_an_older_sti_number_is_not_given_out_again(api, tenant, taxed_world):
    wh, cust, v, _tt = taxed_world
    _product_fbr(tenant, v, "18%")
    first, second = _tax_invoice(api, wh, cust, v), _tax_invoice(api, wh, cust, v)
    assert first["number"].endswith("00001") and second["number"].endswith("00002")
    _switch(api, first, False)
    third = _tax_invoice(api, wh, cust, v)
    assert third["number"].endswith("00003"), "00001 was printed for someone: two papers must not share it"


def test_a_dash_two_invoice_is_never_mistaken_for_its_parent(api, tenant, taxed_world):
    from apps.accounting.models import JournalLine
    from apps.sales.models import SalesInvoice
    from apps.sales.services import InvoiceService

    wh, cust, v, _tt = taxed_world
    _product_fbr(tenant, v, "18%")
    inv = _tax_invoice(api, wh, cust, v)
    with tenant_context(tenant, tenant.owner):
        line = JournalLine.objects.filter(description__contains=inv["number"]).first()
        line.description = f"{inv['number']} and {inv['number']}-2"
        line.save(update_fields=["description"])
        InvoiceService.renumber_everywhere(SalesInvoice.objects.get(pk=inv["id"]), "NEW-1", "test")
        line.refresh_from_db()
        assert line.description == f"NEW-1 and {inv['number']}-2"


# ---------------------------------------------------------------- a company that files with FBR

def _go_live(tenant):
    from apps.fbr.models import FBRSettings

    with tenant_context(tenant, tenant.owner):
        FBRSettings.objects.update(use_sandbox=False, production_token="tok-live")


def test_on_the_sandbox_a_tax_invoice_is_numbered_when_posted(api, tenant):
    """A sandbox filing is a test and never gives an STI number (owner, 10 Nov 2026: TDN, on the sandbox, printed its tax
    invoices as TDN-INV)."""
    inv = _taxed_invoice(api, tenant)   # FBR set up on the sandbox, tax on, posted
    assert inv["number"].endswith("-STI-00001"), inv["number"]
    with mock.patch("apps.fbr.services.requests.post", return_value=_resp(ACCEPT)):
        r = api.post(f"/api/v1/invoices/{inv['id']}/fbr-post", {}, format="json")
        assert r.status_code == 200 and r.data["sandbox"] is True, r.content
    assert api.get(f"/api/v1/invoices/{inv['id']}").data["number"] == inv["number"], "the sandbox filing keeps it"


def test_filing_for_real_the_sti_number_comes_only_when_fbr_accepts_it(api, tenant):
    first = _taxed_invoice(api, tenant)            # posted on the sandbox: STI-00001
    _go_live(tenant)
    inv = _another_filed_kind(api, tenant)
    assert inv["status"] != "draft" and "-INV-" in inv["number"], "posted, but not filed yet: its order's number"
    with mock.patch("apps.fbr.services.requests.post", return_value=_resp(ACCEPT)):
        r = api.post(f"/api/v1/invoices/{inv['id']}/fbr-post", {}, format="json")
        assert r.status_code == 200 and r.data["status"] == "submitted" and r.data["sandbox"] is False, r.content
    filed = api.get(f"/api/v1/invoices/{inv['id']}").data
    assert "-STI-" in filed["number"] and filed["number"].endswith("00002"), "FBR accepted it: the next STI number after " + first["number"]
    texts = _texts(tenant)
    assert inv["number"] not in texts and filed["number"] in texts, "its journals follow"
    assert filed["fbr"]["status"] == "submitted" and filed["fbr"]["fbr_invoice_number"].endswith("857820")


def test_filing_for_real_the_tax_switch_never_touches_the_series(api, tenant):
    _taxed_invoice(api, tenant)                    # STI-00001, on the sandbox
    _go_live(tenant)
    inv = _another_filed_kind(api, tenant)
    off = _switch(api, inv, False)
    assert off["number"] == inv["number"], "it never had an STI number to give up"
    on = _switch(api, off, True)
    assert on["number"] == inv["number"] and Decimal(on["tax1_total"]) > 0
    with mock.patch("apps.fbr.services.requests.post", return_value=_resp(ACCEPT)):
        assert api.post(f"/api/v1/invoices/{inv['id']}/fbr-post", {}, format="json").status_code == 200
    assert api.get(f"/api/v1/invoices/{inv['id']}").data["number"].endswith("STI-00002"), "the next in the series: no gap"
