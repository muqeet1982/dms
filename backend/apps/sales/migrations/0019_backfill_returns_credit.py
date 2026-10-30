"""
Returns already on the books counted as payment (owner, 30 Oct 2026). Their share of an invoice's paid_amount
becomes credited_amount — the part that is not money — with an application record, and an invoice settled by
returns alone reads Returned instead of Paid. No amount changes: paid_amount (the settled total) stays.
"""
from decimal import Decimal

from django.db import migrations
from django.utils import timezone


def forward(apps, schema_editor):
    CreditNote = apps.get_model("sales", "CreditNote")
    CreditNoteApplication = apps.get_model("sales", "CreditNoteApplication")
    PaymentAllocation = apps.get_model("accounting", "PaymentAllocation")
    RepCollection = apps.get_model("sales", "RepCollection")
    Z = Decimal("0")
    SalesInvoice = apps.get_model("sales", "SalesInvoice")
    for cn in CreditNote.objects.filter(status="posted", applied_to_invoice=True, invoice__isnull=False).order_by("date", "created_at"):
        # read fresh every time: two returns on one invoice must see each other's credit (review, 30 Oct 2026)
        inv = SalesInvoice.objects.get(pk=cn.invoice_id)
        # money is what receipts that still stand put on it — a cancelled receipt keeps its allocation rows
        money = sum((a.amount for a in PaymentAllocation.objects.filter(document_type="sales_invoice", document_id=str(inv.pk))
                     .exclude(payment__status="cancelled")), Z)
        for col in RepCollection.objects.filter(customer_id=inv.customer_id).exclude(status="cancelled"):
            money += sum((Decimal(str(a.get("amount") or 0)) for a in (col.allocations or []) if str(a.get("invoice")) == str(inv.pk)), Z)
        room = inv.paid_amount - money - inv.credited_amount        # what this invoice's settled total owes to returns
        applied = max(min(cn.total, room), Z)
        if applied <= 0:
            continue
        CreditNoteApplication.objects.create(tenant_id=cn.tenant_id, credit_note=cn, invoice=inv, amount=applied, date=cn.date)
        cn.applied_amount = applied
        cn.save(update_fields=["applied_amount"])
        inv.credited_amount = inv.credited_amount + applied
        cash = inv.paid_amount - inv.credited_amount
        if inv.paid_amount >= inv.total and cash <= Z and inv.status == "paid":
            inv.status = "returned"
        inv.save(update_fields=["credited_amount", "status"])


class Migration(migrations.Migration):
    dependencies = [("sales", "0018_returns_credit"), ("accounting", "0013_returns_credit")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
