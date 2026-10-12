from django.db import transaction
from django.utils import timezone

from .models import NumberSequence
from .tenancy import get_current_tenant_id

DEFAULT_PREFIXES = {
    "purchase_order": "PO-",
    "goods_receipt": "GRN-",
    "purchase_bill": "PB-",
    "purchase_return": "PR-",
    "sales_order": "SO-",
    "pick_list": "PL-",
    "trip": "TRIP-",
    "delivery_note": "DN-",
    "sales_invoice": "INV-",
    "credit_note": "CN-",
    "rep_collection": "RC-",
    "rep_settlement": "RS-",
    "stock_transfer": "ST-",
    "stock_adjustment": "ADJ-",
    "stock_count": "SC-",
    "journal": "JV-",
    "payment_in": "RCPT-",
    "payment_out": "PAY-",
    "expense": "EXP-",
    "customer": "C-",
    "supplier": "S-",
    "supplier_rep": "REP-",
    "product": "P-",
    "opening_balance": "OB-",
}


def next_number(document_type: str, tenant_id=None, unique_for=None) -> str:
    """
    Allocate the next document number for the tenant inside the current transaction.
    `unique_for=(Model, "code")` skips numbers already taken (codes typed by hand or imported, e.g. S-00001).
    """
    tenant_id = tenant_id or get_current_tenant_id()
    if unique_for is not None:
        model, field = unique_for
        for _ in range(10_000):
            number = next_number(document_type, tenant_id)
            if not model.all_objects.filter(tenant_id=tenant_id, **{field: number}).exists():
                return number
        raise RuntimeError(f"Could not allocate a free {document_type} number")
    with transaction.atomic():
        seq = (
            NumberSequence.all_objects.select_for_update()
            .filter(tenant_id=tenant_id, document_type=document_type)
            .first()
        )
        if seq is None:
            seq = NumberSequence.all_objects.create(
                tenant_id=tenant_id, document_type=document_type, prefix=DEFAULT_PREFIXES.get(document_type, document_type.upper()[:3] + "-")
            )
            seq = NumberSequence.all_objects.select_for_update().get(pk=seq.pk)
        year = timezone.now().year
        if seq.reset == "yearly" and seq.last_reset_year != year:
            seq.next_number = 1
            seq.last_reset_year = year
        number = f"{seq.prefix}{str(seq.next_number).zfill(seq.padding)}"
        if seq.reset == "yearly":
            number = f"{seq.prefix}{year}-{str(seq.next_number).zfill(seq.padding)}"
        seq.next_number += 1
        seq.save(update_fields=["next_number", "last_reset_year", "updated_at"])
        return number
