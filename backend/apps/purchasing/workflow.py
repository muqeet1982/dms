"""
The buying chain, as one answer the UI can render:

    Purchase order → Approved → Receiving → Received & put away → Billed → Paid

Every step says where it stands, which document carries it and what the next action is. Quantities come back in
units *and* in boxes, because a warehouse counts boxes: `packing` units make one box.
"""
from decimal import Decimal

from .models import GoodsReceiptLine, PurchaseBill


def _boxes(units, packing):
    """(units, boxes, loose) — 29 units at 12 per box is 2 boxes and 5 loose."""
    units = Decimal(units or 0)
    per = int(packing or 0)
    if per <= 1:
        return {"units": str(units), "packing": per, "boxes": None, "loose": str(units)}
    whole = int(units // per)
    return {"units": str(units), "packing": per, "boxes": whole, "loose": str(units - whole * per)}


def _person(user):
    return getattr(user, "full_name", "") or getattr(user, "username", "") or ""


def _step(key, title, state, **kw):
    return {"key": key, "title": title, "state": state, "at": None, "by": "", "detail": "", "docs": [], "action": None, **kw}


def po_lines_progress(po):
    """Per line: ordered · received · rejected · not received · still pending, and where the stock was put."""
    rows = []
    received_lines = list(
        GoodsReceiptLine.objects.filter(po_line__po=po, grn__status="posted").select_related("grn", "bin", "bin__warehouse", "po_line")
    )
    for line in po.lines.select_related("variant", "variant__product").all():
        mine = [g for g in received_lines if g.po_line_id == line.pk]
        packing = line.variant.product.packing
        places = []
        for g in mine:
            where = f"{g.bin.warehouse.code} / {g.bin.code}" if g.bin_id else po.warehouse.code
            qty = next((p for p in places if p["where"] == where), None)
            if qty is None:
                places.append({"where": where, "qty": str(g.qty_accepted), "grn": g.grn.number})
            else:
                qty["qty"] = str(Decimal(qty["qty"]) + g.qty_accepted)
        rows.append({
            "line_id": str(line.pk),
            "variant": str(line.variant_id),
            "sku": line.variant.sku,
            "item_code": line.variant.product.item_code,
            "upc": line.variant.product.upc,
            "name": line.variant.name,
            "packing": packing,
            "ordered": _boxes(line.qty_ordered, packing),
            "received": _boxes(line.qty_received, packing),
            "rejected": _boxes(sum((g.qty_damaged for g in mine), Decimal("0")), packing),
            "not_received": _boxes(sum((g.qty_short for g in mine), Decimal("0")), packing),
            "pending": _boxes(line.qty_pending, packing),
            # the receive screen marks the expiry field required off this, so the dock is asked at the keyboard
            # rather than refused at the end (StockService.get_or_create_batch is the rule behind it)
            "is_expiry_tracked": line.variant.product.is_expiry_tracked,
            "is_batch_tracked": line.variant.product.is_batch_tracked,
            "closed": line.closed,
            "closed_reason": line.closed_reason,
            "reject_reasons": [g.reject_reason for g in mine if g.reject_reason],
            "placed": places,
        })
    return rows


def po_receiving(po):
    """
    One record of receiving for the order: what is owed per line, and a dated log of each delivery.

    This is the view the owner asked for — "how much received and how much pending", in one place, instead of
    a count of receipt documents that says nothing. The deliveries themselves stay separate underneath,
    because stock moves and the books are written when each one is posted.
    """
    grns = list(po.grns.select_related("received_by").order_by("created_at"))
    lines = po_lines_progress(po)
    ordered = sum(Decimal(l["ordered"]["units"]) for l in lines)
    received = sum(Decimal(l["received"]["units"]) for l in lines)
    deliveries = []
    for g in grns:
        rows = list(g.lines.all())
        deliveries.append({
            "id": str(g.pk), "number": g.number, "status": g.status,
            "date": g.date, "created_at": g.created_at,
            "challan": g.supplier_challan_no,
            "items": len(rows),
            "units": str(sum((r.qty_accepted for r in rows), Decimal("0"))),
            "rejected": str(sum((r.qty_damaged for r in rows), Decimal("0"))),
            "received_by": (g.received_by.full_name or g.received_by.username) if g.received_by_id else "",
            "is_open": g.status == "draft",
        })
    open_one = next((d for d in deliveries if d["is_open"]), None)
    return {
        "ordered_units": str(ordered), "received_units": str(received),
        "pending_units": str(ordered - received),
        "lines_total": len(lines),
        "lines_complete": sum(1 for l in lines if l["closed"] or Decimal(l["received"]["units"]) >= Decimal(l["ordered"]["units"])),
        "open_receipt": open_one,
        "deliveries": deliveries,
        "lines": lines,
    }


def po_workflow(po):
    grns = list(po.grns.select_related("received_by").order_by("created_at"))
    posted = [g for g in grns if g.status == "posted"]
    open_draft = next((g for g in grns if g.status == "draft"), None)
    bills = list(PurchaseBill.objects.filter(grns__in=grns).distinct())
    paid = [b for b in bills if b.status == "paid"]
    lines = po_lines_progress(po)
    pending_units = sum(Decimal(r["pending"]["units"]) for r in lines)
    cancelled = po.status == "cancelled"
    steps = []

    # 1 · the order itself
    steps.append(_step(
        "order", "Purchase order", "done" if po.status != "draft" else ("blocked" if cancelled else "current"),
        at=po.created_at, by=_person(po.created_by) if getattr(po, "created_by", None) else "",
        detail=f"{len(lines)} item(s) · {po.get_status_display()}",
        docs=[{"label": po.number, "to": f"/app/purchasing/orders/{po.pk}"}],
        action=None if po.status != "draft" or cancelled else {"label": "Edit order", "to": f"/app/purchasing/orders/{po.pk}/edit", "perm": "purchasing.change_purchase_order"},
    ))

    # 2 · approved (and sent to the supplier)
    steps.append(_step(
        "approved", "Approved", "done" if po.approved_at or po.status != "draft" else ("current" if not cancelled else "skipped"),
        at=po.approved_at, by=_person(po.approved_by),
        detail="Approved and sent to the supplier." if po.approved_at else "Check the prices and quantities, then approve.",
        action=None if po.status != "draft" else {"label": "Approve & send", "post": f"/api/v1/purchase-orders/{po.pk}/send", "perm": "purchasing.approve_purchase_order"},
    ))

    # 3 · receiving
    steps.append(_step(
        "receiving", "Receiving", "done" if posted and pending_units <= 0 else ("current" if po.status in ("sent", "partially_received") else "todo"),
        at=posted[0].posted_at if posted else None, by=_person(posted[0].received_by) if posted else "",
        detail=(f"{len(posted)} receipt(s) · {pending_units} unit(s) still to come" if pending_units > 0 else f"{len(posted)} receipt(s), nothing outstanding") if posted else "Record what the supplier actually delivered.",
        docs=[{"label": g.number, "to": f"/app/purchasing/grns/{g.pk}"} for g in grns if g.status != "cancelled"],
        action=(
            # with a receipt already open against this order the next step is to finish that one, not to start a
            # second: pointing at it is what keeps one order to one receipt in front of the person receiving
            ({"label": f"Continue {open_draft.number}", "to": f"/app/purchasing/grns/{open_draft.pk}", "perm": "purchasing.add_grn"}
             if open_draft is not None else
             {"label": "Receive goods", "to": f"/app/purchasing/grns/new?po={po.pk}", "perm": "purchasing.add_grn"})
            if po.status in ("sent", "partially_received") and pending_units > 0 else None
        ),
    ))

    # 4 · put away — what came in and where it went
    placed = [p for r in lines for p in r["placed"]]
    steps.append(_step(
        "put_away", "Received & put away", "done" if placed else ("current" if posted else "todo"),
        detail=(" · ".join(sorted({p["where"] for p in placed})) if placed else "Where the goods were placed shows here once a receipt is posted."),
        at=posted[-1].posted_at if posted else None,
    ))

    # 5 · billed
    steps.append(_step(
        "billed", "Supplier bill", "done" if bills else ("current" if posted else "todo"),
        at=bills[0].created_at if bills else None,
        detail=" · ".join(f"{b.number} {b.get_status_display()}" for b in bills) or "Enter the supplier's invoice against the receipt.",
        docs=[{"label": b.number, "to": f"/app/purchasing/bills/{b.pk}"} for b in bills],
        action=(
            {"label": "Enter bill", "to": f"/app/purchasing/bills/new?supplier={po.supplier_id}&grn={posted[0].pk}", "perm": "purchasing.add_bill"}
            if posted and not bills else None
        ),
    ))

    # 6 · paid
    steps.append(_step(
        "paid", "Paid", "done" if bills and len(paid) == len(bills) else ("current" if bills else "todo"),
        detail=(f"{len(paid)} of {len(bills)} bill(s) paid" if bills else "After the bill is entered."),
        action=(
            {"label": "Pay supplier", "to": f"/app/accounting/payments/new?direction=out&partner_type=supplier&partner_id={po.supplier_id}", "perm": "cash.make_payment"}
            if bills and len(paid) < len(bills) else None
        ),
    ))

    for s in steps:
        if cancelled and s["state"] in ("current", "todo"):
            s["state"] = "skipped"
    return {"steps": steps, "lines": lines, "pending_units": str(pending_units)}
