"""
14 Nov 2026: TDN's October renumbering moved the documents but not the text that quotes them — INV-00013 (now
TDN-INV-00015) stayed in its journals and read as another invoice. rewrite_quoted_numbers carries a rename into every
journal memo, source number, line description and stock-ledger note and reference, in one pass and whole tokens only.
"""
import pytest

from apps.core.tenancy import tenant_context

from .conftest import issue_order, seed_basic

pytestmark = pytest.mark.django_db


def test_a_rename_reaches_the_journals_and_the_stock_ledger(api, tenant):
    from apps.accounting.models import JournalEntry, JournalLine
    from apps.core.numbering import rewrite_quoted_numbers
    from apps.inventory.models import StockMovement

    wh, _s, v, cust = seed_basic(tenant)
    o = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": "2"}]}, format="json").data
    issue_order(api, o["id"])
    inv = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": True}, format="json").data[0]
    with tenant_context(tenant):
        je = JournalEntry.objects.filter(source_id=inv["id"], memo__startswith="Sales invoice").get()
        other = JournalEntry.objects.filter(source_id=inv["id"], memo__startswith="COGS for").get()
        # as the October renumbering left them: old numbers in the text, and a look-alike that must not change
        JournalEntry.objects.filter(pk=je.pk).update(memo="Sales invoice INV-00013 – Shop", source_number="INV-00013")
        JournalEntry.objects.filter(pk=other.pk).update(memo="COGS for TDN-INV-00013 and INV-00013-2 and INV-00015")
        JournalLine.objects.filter(entry=je).update(description="Invoice INV-00013")
        mv = StockMovement.objects.filter(variant=v, movement_type="sale_dispatch").first()
        StockMovement.objects.filter(pk=mv.pk).update(note="Order SO-00004", reference_number="DN-00002")
        done = rewrite_quoted_numbers(tenant.id, [(inv["id"], "INV-00013", "TDN-INV-00015"), ("x", "INV-00015", "TDN-INV-00017"),
                                                 ("y", "SO-00004", "TDN-SO-00005"), (mv.reference_id, "DN-00002", "TDN-DN-00005")])
        je.refresh_from_db(); other.refresh_from_db(); mv.refresh_from_db()
        assert (je.memo, je.source_number) == ("Sales invoice TDN-INV-00015 – Shop", "TDN-INV-00015"), "not chained on to 00017"
        assert set(JournalLine.objects.filter(entry=je).values_list("description", flat=True)) == {"Invoice TDN-INV-00015"}
        assert other.memo == "COGS for TDN-INV-00013 and INV-00013-2 and TDN-INV-00017", "whole tokens only"
        assert (mv.note, mv.reference_number) == ("Order TDN-SO-00005", "TDN-DN-00005")
        assert done["journals"] == 2 and done["stock_notes"] == 1 and done["stock_references"] >= 1, dict(done)
