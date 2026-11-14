"""
14 Nov 2026: TDN-CN-00006's and TDN-RCPT-00045's journals were reversed on the journal page while the credit note kept
crediting its invoice and the receipt kept paying one. A journal a document posted is undone with that document; a
journal typed by hand can still be reversed.
"""
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

from .conftest import issue_order, seed_basic

pytestmark = pytest.mark.django_db


def test_a_documents_journal_is_not_reversed_on_its_own(api, tenant):
    from apps.accounting.models import JournalEntry

    wh, _s, v, cust = seed_basic(tenant)
    o = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": "2"}]}, format="json").data
    issue_order(api, o["id"])
    inv = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": True}, format="json").data[0]
    with tenant_context(tenant):
        je = JournalEntry.objects.filter(source_id=inv["id"], memo__startswith="Sales invoice").get()
    r = api.post(f"/api/v1/accounting/journals/{je.pk}/reverse", {}, format="json")
    assert r.status_code == 422 and r.data["type"] == "document_journal" and inv["number"] in r.data["detail"], r.content
    with tenant_context(tenant):
        assert JournalEntry.objects.get(pk=je.pk).status == "posted"


def test_a_manual_journal_can_still_be_reversed(api, tenant):
    from apps.accounting.services import account_for

    with tenant_context(tenant, tenant.owner):
        cash, sales = str(account_for("cash").id), str(account_for("sales").id)
    r = api.post("/api/v1/accounting/journals", {"date": "2026-11-14", "voucher_type": "jv", "memo": "Typed by hand",
                                                "lines": [{"account": cash, "debit": "100", "credit": 0}, {"account": sales, "debit": 0, "credit": "100"}]}, format="json")
    assert api.post(f"/api/v1/accounting/journals/{r.data['id']}/post", {}, format="json").status_code == 200
    assert api.post(f"/api/v1/accounting/journals/{r.data['id']}/reverse", {}, format="json").status_code == 201
