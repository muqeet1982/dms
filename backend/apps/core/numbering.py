import re

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
    # tax invoices are their own series (owner, 1 Nov 2026: "TDN-STI-00001 then next TDN-STI-00002")
    "tax_invoice": "STI-",
    "credit_note": "CN-",
    "company_claim": "CLM-",
    "rep_collection": "RC-",
    "rep_settlement": "RS-",
    "stock_transfer": "ST-",
    "stock_adjustment": "ADJ-",
    "stock_count": "SC-",
    "journal": "JV-",
    # manual voucher families — each its own series, as the owner's old ERP numbered them
    "journal_cpv": "CPV-", "journal_crv": "CRV-", "journal_bpv": "BPV-", "journal_brv": "BRV-",
    "journal_jrv": "JRV-", "journal_jpv": "JPV-", "journal_jcv": "JCV-", "journal_sjv": "SJV-",
    "payment_in": "RCPT-",
    "payment_out": "PAY-",
    "expense": "EXP-",
    "customer": "C-",
    "supplier": "S-",
    "supplier_rep": "REP-",
    "product": "P-",
    "opening_balance": "OB-",
}


def _company_code(tenant_id) -> str:
    """The tenant's short code stamped before every document number (TDN-SO-00001), '' when unset."""
    from apps.platform.models import Tenant

    code = Tenant.objects.filter(pk=tenant_id).values_list("doc_code", flat=True).first() or ""
    return f"{code}-" if code else ""


def _prefix_for(document_type: str, tenant_id) -> str:
    seq = NumberSequence.all_objects.filter(tenant_id=tenant_id, document_type=document_type).first()
    prefix = seq.prefix if seq is not None else DEFAULT_PREFIXES.get(document_type, document_type.upper()[:3] + "-")
    return f"{_company_code(tenant_id)}{prefix}"


def numeric_core(number: str) -> str:
    """The counter part of a document number, prefix stripped: 'SO-00001' -> '00001', 'SO-2026-7' -> '2026-7'."""
    return re.sub(r"^[^0-9]*", "", number or "")


def renumber(doc, number: str) -> None:
    """Give a numbered document a new number, and the stock ledger lines that quote it the same one."""
    from apps.inventory.models import StockMovement

    type(doc).all_objects.filter(pk=doc.pk).update(number=number)
    StockMovement.all_objects.filter(reference_id=str(doc.pk)).update(reference_number=number)
    doc.number = number


def give_back(document_type: str, number: str, tenant_id=None) -> bool:
    """A number the series gave last returns to it, so the next document takes it and the series has no gap. Any
    other number is not reused: two papers must never carry the same one. True if it went back."""
    tenant_id = tenant_id or get_current_tenant_id()
    with transaction.atomic():
        seq = NumberSequence.all_objects.select_for_update().filter(tenant_id=tenant_id, document_type=document_type).first()
        if seq is None or seq.reset == "yearly" or seq.next_number <= 1:
            return False
        last = f"{_company_code(tenant_id)}{seq.prefix}{str(seq.next_number - 1).zfill(seq.padding)}"
        if number != last:
            return False
        seq.next_number -= 1
        seq.save(update_fields=["next_number", "updated_at"])
        return True


def derived_base(parent_number: str, document_type: str, tenant_id=None) -> str:
    """The number a child of this parent is meant to carry: SO-00001 -> DN-00001."""
    return f"{_prefix_for(document_type, tenant_id or get_current_tenant_id())}{numeric_core(parent_number)}"


def derived_number(parent_number: str, document_type: str, model, tenant_id=None, reclaim=False) -> str:
    """
    Give a child document the parent's counter under its own prefix, so a whole chain shares one number:
    SO-00001 -> INV-00001, DN-00001; PO-00001 -> GRN-00001, PB-00001. A second child for the same parent
    (a partial second delivery or invoice) gets a '-2', '-3' … suffix so it stays tied to the parent and unique.
    Falls back to a fresh sequence number when there is no parent to inherit from.

    `reclaim`: a CANCELLED holder hands its number to the document replacing it and stands aside as '-C1' — only a
    second live document is a real '-2' (owner, 5 Nov 2026).
    """
    tenant_id = tenant_id or get_current_tenant_id()
    if not parent_number:
        return next_number(document_type, tenant_id, unique_for=(model, "number"))
    base = derived_base(parent_number, document_type, tenant_id)
    taken = set(model.all_objects.filter(tenant_id=tenant_id, number__startswith=base).values_list("number", flat=True))
    if base not in taken:
        return base
    if reclaim:
        holder = model.all_objects.filter(tenant_id=tenant_id, number=base, status="cancelled").first()
        if holder is not None:
            n = 1
            while f"{base}-C{n}" in taken:
                n += 1
            renumber(holder, f"{base}-C{n}")
            return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


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
        company = _company_code(tenant_id)
        number = f"{company}{seq.prefix}{str(seq.next_number).zfill(seq.padding)}"
        if seq.reset == "yearly":
            number = f"{company}{seq.prefix}{year}-{str(seq.next_number).zfill(seq.padding)}"
        seq.next_number += 1
        seq.save(update_fields=["next_number", "last_reset_year", "updated_at"])
        return number
