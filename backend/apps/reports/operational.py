"""Operational reports (§12): sales, purchasing, inventory, delivery, receivables."""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDay, TruncMonth
from django.utils import timezone

Z = Decimal("0")

def boxes_loose(units, packing):
    """A quantity as a warehouse counts it: (boxes, loose) where one box holds `packing` units."""
    units = Decimal(str(units or 0))
    per = int(packing or 0)
    if per <= 1:
        return "", str(units)
    whole = int(units // per)
    return str(whole), str(units - whole * per)


def _window(qs, field, date_from, date_to):
    if date_from:
        qs = qs.filter(**{f"{field}__gte": date_from})
    if date_to:
        qs = qs.filter(**{f"{field}__lte": date_to})
    return qs


def _inv_lines(date_from, date_to, **filters):
    from apps.sales.models import SalesInvoiceLine

    qs = SalesInvoiceLine.objects.filter(invoice__status__in=["posted", "partially_paid", "paid", "overdue"]).select_related("invoice", "variant", "variant__product", "variant__product__brand", "variant__product__supplier", "invoice__customer", "invoice__rep")
    qs = _window(qs, "invoice__date", date_from, date_to)
    return qs.filter(**filters)


def _group(rows, key_fn, label_fn):
    out = defaultdict(lambda: {"qty": Z, "sales": Z, "cogs": Z, "invoices": set()})
    for l in rows:
        k = key_fn(l)
        r = out[k]
        r["label"] = label_fn(l)
        r["qty"] += l.qty
        r["sales"] += l.line_total - l.tax1_amount - l.tax2_amount
        r["cogs"] += l.qty * l.unit_cost
        r["invoices"].add(l.invoice_id)
    result = []
    for k, r in out.items():
        margin = r["sales"] - r["cogs"]
        result.append({"key": str(k), "label": r["label"], "qty": str(r["qty"]), "sales": str(r["sales"]), "cogs": str(r["cogs"]), "gross_margin": str(margin), "margin_pct": str((margin / r["sales"] * 100).quantize(Decimal("0.01")) if r["sales"] else Z), "invoices": len(r["invoices"])})
    result.sort(key=lambda r: -Decimal(r["sales"]))
    totals = {"sales": str(sum((Decimal(r["sales"]) for r in result), Z)), "cogs": str(sum((Decimal(r["cogs"]) for r in result), Z)), "gross_margin": str(sum((Decimal(r["gross_margin"]) for r in result), Z))}
    return {"columns": ["label", "qty", "sales", "cogs", "gross_margin", "margin_pct", "invoices"], "rows": result, "totals": totals}


def sales_by_product(date_from=None, date_to=None, **_):
    return _group(_inv_lines(date_from, date_to), lambda l: l.variant_id, lambda l: f"{l.variant.sku} {l.variant.name}")


def sales_by_brand(date_from=None, date_to=None, **_):
    return _group(_inv_lines(date_from, date_to), lambda l: l.variant.product.brand_id, lambda l: l.variant.product.brand.name if l.variant.product.brand_id else "(no brand)")


def sales_by_supplier(date_from=None, date_to=None, **_):
    return _group(_inv_lines(date_from, date_to), lambda l: l.variant.product.supplier_id, lambda l: l.variant.product.supplier.name if l.variant.product.supplier_id else "(no supplier)")


def sales_by_customer(date_from=None, date_to=None, **_):
    return _group(_inv_lines(date_from, date_to), lambda l: l.invoice.customer_id, lambda l: f"{l.invoice.customer.code} {l.invoice.customer.name}")


def sales_by_rep(date_from=None, date_to=None, **_):
    return _group(_inv_lines(date_from, date_to), lambda l: l.invoice.rep_id, lambda l: l.invoice.rep.name if l.invoice.rep_id else "(staff)")


def sales_by_territory(date_from=None, date_to=None, **_):
    return _group(_inv_lines(date_from, date_to).select_related("invoice__customer__territory", "invoice__customer__route"), lambda l: l.invoice.customer.territory_id, lambda l: l.invoice.customer.territory.name if l.invoice.customer.territory_id else "(no territory)")


def sales_by_route(date_from=None, date_to=None, **_):
    return _group(_inv_lines(date_from, date_to).select_related("invoice__customer__route"), lambda l: l.invoice.customer.route_id, lambda l: l.invoice.customer.route.name if l.invoice.customer.route_id else "(no route)")


def sales_by_period(date_from=None, date_to=None, granularity="day", **_):
    from apps.sales.models import SalesInvoice

    qs = _window(SalesInvoice.objects.filter(status__in=["posted", "partially_paid", "paid", "overdue"]), "date", date_from, date_to)
    trunc = TruncMonth("date") if granularity == "month" else TruncDay("date")
    rows = [{"period": r["p"].isoformat() if hasattr(r["p"], "isoformat") else str(r["p"]), "invoices": r["n"], "sales": str(r["s"] or 0), "tax": str((r["t1"] or 0) + (r["t2"] or 0))} for r in qs.annotate(p=trunc).values("p").annotate(n=Count("id"), s=Sum("subtotal") - Sum("discount"), t1=Sum("tax1_total"), t2=Sum("tax2_total")).order_by("p")]
    return {"columns": ["period", "invoices", "sales", "tax"], "rows": rows, "totals": {"sales": str(sum((Decimal(r["sales"]) for r in rows), Z))}}


def order_fulfilment(date_from=None, date_to=None, **_):
    from apps.sales.models import SalesOrder

    qs = _window(SalesOrder.objects.all(), "order_date", date_from, date_to)
    counts = dict(qs.values_list("status").annotate(c=Count("id")).values_list("status", "c"))
    total = sum(counts.values())
    fulfilled = counts.get("delivered", 0) + counts.get("invoiced", 0)
    rows = [{"status": k, "count": v, "pct": str((Decimal(v) / total * 100).quantize(Decimal("0.1")) if total else Z)} for k, v in sorted(counts.items())]
    return {"columns": ["status", "count", "pct"], "rows": rows, "totals": {"orders": total, "fulfilled": fulfilled, "fulfilment_rate": str((Decimal(fulfilled) / total * 100).quantize(Decimal("0.1")) if total else Z), "rejected": counts.get("rejected", 0), "cancelled": counts.get("cancelled", 0)}}


def pending_orders(**_):
    from apps.sales.models import SalesOrder

    rows = []
    for o in SalesOrder.objects.filter(status__in=["submitted", "under_review", "confirmed", "on_hold_credit", "on_hold_stock", "dispatched", "partially_delivered"]).select_related("customer", "rep"):
        pend = sum((l.qty_ordered + l.free_qty - l.qty_delivered for l in o.lines.all()), Z)
        rows.append({"number": o.number, "date": o.order_date, "customer": o.customer.name, "rep": o.rep.name if o.rep_id else "", "status": o.status, "total": str(o.total), "qty_pending": str(pend), "age_days": (timezone.localdate() - o.order_date).days, "hold_reason": o.hold_reason})
    return {"columns": ["number", "date", "customer", "rep", "status", "total", "qty_pending", "age_days"], "rows": rows, "totals": {"count": len(rows)}}


def dispatched_not_delivered(**_):
    from apps.sales.models import SalesOrder

    rows = [{"number": o.number, "customer": o.customer.name, "trip": o.trip.number if o.trip_id else "", "driver": o.trip.driver.name if o.trip_id and o.trip.driver_id else "", "dispatched_at": o.dispatched_at, "total": str(o.total)} for o in SalesOrder.objects.filter(status__in=["dispatched", "partially_delivered"]).select_related("customer", "trip", "trip__driver")]
    return {"columns": ["number", "customer", "trip", "driver", "dispatched_at", "total"], "rows": rows, "totals": {"count": len(rows), "value": str(sum((Decimal(r["total"]) for r in rows), Z))}}


def rejected_orders(date_from=None, date_to=None, **_):
    from apps.sales.models import SalesOrder

    qs = _window(SalesOrder.objects.filter(status__in=["rejected", "cancelled"]), "order_date", date_from, date_to).select_related("customer", "rep")
    rows = [{"number": o.number, "date": o.order_date, "customer": o.customer.name, "rep": o.rep.name if o.rep_id else "", "status": o.status, "reason": o.rejection_reason, "total": str(o.total)} for o in qs]
    return {"columns": ["number", "date", "customer", "rep", "status", "reason", "total"], "rows": rows, "totals": {"count": len(rows)}}


def scheme_cost(date_from=None, date_to=None, **_):
    from apps.sales.models import SalesOrderLine

    qs = _window(SalesOrderLine.objects.exclude(order__status__in=["cancelled", "rejected"]), "order__order_date", date_from, date_to).exclude(scheme_names=[])
    out = defaultdict(lambda: {"discount": Z, "free_qty": Z, "lines": 0})
    for l in qs:
        for n in l.scheme_names or ["(scheme)"]:
            out[n]["discount"] += l.scheme_discount
            out[n]["free_qty"] += l.free_qty
            out[n]["lines"] += 1
    rows = [{"scheme": k, **{kk: str(vv) for kk, vv in v.items()}} for k, v in out.items()]
    return {"columns": ["scheme", "discount", "free_qty", "lines"], "rows": rows, "totals": {"discount": str(sum((v["discount"] for v in out.values()), Z))}}


# --------------------------------------------------------------------- purchasing
def purchase_register(date_from=None, date_to=None, supplier=None, **_):
    from apps.purchasing.models import PurchaseBill

    qs = _window(PurchaseBill.objects.exclude(status="cancelled"), "bill_date", date_from, date_to).select_related("supplier")
    if supplier:
        qs = qs.filter(supplier_id=supplier)
    rows = [{"number": b.number, "date": b.bill_date, "supplier": b.supplier.name, "supplier_invoice": b.supplier_invoice_no, "subtotal": str(b.subtotal), "tax1": str(b.tax1_total), "tax2": str(b.tax2_total), "total": str(b.total), "paid": str(b.paid_amount), "status": b.status} for b in qs]
    return {"columns": ["number", "date", "supplier", "supplier_invoice", "subtotal", "tax1", "tax2", "total", "paid", "status"], "rows": rows, "totals": {"total": str(sum((Decimal(r["total"]) for r in rows), Z))}}


def grn_register(date_from=None, date_to=None, **_):
    from apps.purchasing.models import GoodsReceipt

    qs = _window(GoodsReceipt.objects.exclude(status="cancelled"), "date", date_from, date_to).select_related("supplier", "warehouse", "po")
    rows = [{"number": g.number, "date": g.date, "supplier": g.supplier.name, "warehouse": g.warehouse.name, "po": g.po.number if g.po_id else "", "value": str(g.total_value), "status": g.status, "billed": g.billed} for g in qs]
    return {"columns": ["number", "date", "supplier", "warehouse", "po", "value", "status", "billed"], "rows": rows, "totals": {"value": str(sum((Decimal(r["value"]) for r in rows), Z))}}


def receipts_by_item(date_from=None, date_to=None, supplier=None, **_):
    """
    What we bought and what actually arrived, item by item: ordered, received, rejected, still due — in boxes and
    in units — and where the goods were put away. This is the report that answers "how many boxes came in".
    """
    from apps.purchasing.models import GoodsReceiptLine, PurchaseOrderLine

    lines = _window(PurchaseOrderLine.objects.exclude(po__status__in=["draft", "cancelled"]), "po__date", date_from, date_to).select_related(
        "po", "po__supplier", "po__warehouse", "variant", "variant__product"
    )
    if supplier:
        lines = lines.filter(po__supplier_id=supplier)
    received = {}
    for g in GoodsReceiptLine.objects.filter(po_line__in=lines, grn__status="posted").select_related("grn", "bin", "bin__warehouse"):
        agg = received.setdefault(g.po_line_id, {"damaged": Z, "short": Z, "where": set(), "reasons": set()})
        agg["damaged"] += g.qty_damaged
        agg["short"] += g.qty_short
        if g.bin_id:
            agg["where"].add(f"{g.bin.warehouse.code} / {g.bin.code}")
        if g.reject_reason:
            agg["reasons"].add(g.reject_reason)
    rows = []
    for l in lines:
        product = l.variant.product
        per = product.packing or 1
        agg = received.get(l.pk, {"damaged": Z, "short": Z, "where": set(), "reasons": set()})
        ordered_box, ordered_loose = boxes_loose(l.qty_ordered, per)
        got_box, got_loose = boxes_loose(l.qty_received, per)
        rows.append({
            "po": l.po.number, "date": l.po.date, "supplier": l.po.supplier.name, "warehouse": l.po.warehouse.name,
            "item_code": product.item_code, "upc": product.upc, "sku": l.variant.sku, "name": l.variant.name,
            "per_box": str(per),
            "ordered_boxes": ordered_box, "ordered_loose": ordered_loose, "ordered_units": str(l.qty_ordered),
            "received_boxes": got_box, "received_loose": got_loose, "received_units": str(l.qty_received),
            "rejected": str(agg["damaged"]), "not_received": str(agg["short"]),
            "pending_units": str(l.qty_pending), "closed": l.closed,
            "placed_in": ", ".join(sorted(agg["where"])) or l.po.warehouse.code,
            "reason": "; ".join(sorted(agg["reasons"])),
        })
    return {
        "columns": ["po", "date", "supplier", "warehouse", "item_code", "upc", "sku", "name", "per_box",
                    "ordered_boxes", "ordered_loose", "ordered_units", "received_boxes", "received_loose", "received_units",
                    "rejected", "not_received", "pending_units", "closed", "placed_in", "reason"],
        "rows": rows,
        "totals": {
            "ordered_units": str(sum((Decimal(r["ordered_units"]) for r in rows), Z)),
            "received_units": str(sum((Decimal(r["received_units"]) for r in rows), Z)),
            "rejected": str(sum((Decimal(r["rejected"]) for r in rows), Z)),
            "not_received": str(sum((Decimal(r["not_received"]) for r in rows), Z)),
        },
    }


def purchases_by_supplier(date_from=None, date_to=None, **_):
    from apps.purchasing.models import PurchaseBill

    qs = _window(PurchaseBill.objects.filter(status__in=["posted", "partially_paid", "paid"]), "bill_date", date_from, date_to)
    rows = [{"supplier": r["supplier__name"], "bills": r["n"], "total": str(r["t"] or 0), "paid": str(r["p"] or 0), "outstanding": str((r["t"] or 0) - (r["p"] or 0))} for r in qs.values("supplier__name").annotate(n=Count("id"), t=Sum("total"), p=Sum("paid_amount")).order_by("-t")]
    return {"columns": ["supplier", "bills", "total", "paid", "outstanding"], "rows": rows, "totals": {"total": str(sum((Decimal(r["total"]) for r in rows), Z))}}


def pending_pos(**_):
    from apps.purchasing.models import PurchaseOrder

    rows = []
    for po in PurchaseOrder.objects.filter(status__in=["sent", "partially_received"]).select_related("supplier"):
        pend = sum((l.qty_ordered - l.qty_received for l in po.lines.all()), Z)
        rows.append({"number": po.number, "date": po.date, "expected": po.expected_date, "supplier": po.supplier.name, "status": po.status, "total": str(po.total), "qty_pending": str(pend), "age_days": (timezone.localdate() - po.date).days})
    return {"columns": ["number", "date", "expected", "supplier", "status", "total", "qty_pending", "age_days"], "rows": rows, "totals": {"count": len(rows)}}


def price_variance(date_from=None, date_to=None, **_):
    from apps.accounting.models import JournalLine

    qs = _window(JournalLine.objects.filter(account__code="5-01-002-0001", entry__status="posted"), "entry__date", date_from, date_to).select_related("entry")
    rows = [{"date": l.entry.date, "journal": l.entry.number, "source": l.entry.source_number, "variance": str(l.debit - l.credit)} for l in qs]
    return {"columns": ["date", "journal", "source", "variance"], "rows": rows, "totals": {"variance": str(sum((Decimal(r["variance"]) for r in rows), Z))}}


def returns_to_supplier(date_from=None, date_to=None, **_):
    from apps.purchasing.models import PurchaseReturn

    qs = _window(PurchaseReturn.objects.filter(status="posted"), "date", date_from, date_to).select_related("supplier")
    rows = [{"number": r.number, "date": r.date, "supplier": r.supplier.name, "reason": r.reason, "total": str(r.total), "debit_note_only": r.is_debit_note_only} for r in qs]
    return {"columns": ["number", "date", "supplier", "reason", "total"], "rows": rows, "totals": {"total": str(sum((Decimal(r["total"]) for r in rows), Z))}}


# ---------------------------------------------------------------------- inventory
def stock_summary(warehouse=None, **_):
    from apps.inventory.models import StockLevel
    from apps.sales.models import SalesOrderLine

    qs = StockLevel.objects.select_related("variant", "variant__product", "warehouse", "batch").filter(Q(qty_on_hand__gt=0) | Q(qty_reserved__gt=0))
    if warehouse:
        qs = qs.filter(warehouse_id=warehouse)
    disp = {r["variant_id"]: ((r["d"] or 0) - (r["dl"] or 0), r["dl"] or 0) for r in SalesOrderLine.objects.filter(order__status__in=["dispatched", "partially_delivered", "delivered", "invoiced"]).values("variant_id").annotate(d=Sum("qty_dispatched"), dl=Sum("qty_delivered"))}
    rows = []
    for lv in qs:
        d = disp.get(lv.variant_id, (0, 0))
        product = lv.variant.product
        box, loose = boxes_loose(lv.qty_on_hand, product.packing)
        rows.append({"warehouse": lv.warehouse.name, "item_code": product.item_code, "upc": product.upc, "sku": lv.variant.sku, "name": lv.variant.name, "batch": lv.batch.batch_no if lv.batch else "", "expiry": lv.batch.expiry_date if lv.batch else None, "per_box": str(product.packing or 1), "boxes": box, "loose": loose, "in_stock": str(lv.qty_on_hand), "reserved": str(lv.qty_reserved), "available": str(lv.qty_on_hand - lv.qty_reserved), "dispatched": str(d[0]), "delivered": str(d[1]), "avg_cost": str(lv.variant.avg_cost), "value": str((lv.qty_on_hand * (lv.variant.avg_cost or lv.variant.cost_price)).quantize(Decimal("0.0001")))})
    return {"columns": ["warehouse", "item_code", "upc", "sku", "name", "batch", "expiry", "per_box", "boxes", "loose", "in_stock", "reserved", "available", "dispatched", "delivered", "avg_cost", "value"], "rows": rows, "totals": {"value": str(sum((Decimal(r["value"]) for r in rows), Z))}}


def stock_ledger(variant=None, warehouse=None, date_from=None, date_to=None, **_):
    from apps.inventory.models import StockMovement

    qs = StockMovement.objects.select_related("variant", "warehouse", "batch", "performed_by").order_by("occurred_at", "created_at")
    if variant:
        qs = qs.filter(variant_id=variant)
    if warehouse:
        qs = qs.filter(warehouse_id=warehouse)
    opening = Z
    if date_from:
        opening = qs.filter(occurred_at__date__lt=date_from).aggregate(s=Sum("qty"))["s"] or Z
        qs = qs.filter(occurred_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(occurred_at__date__lte=date_to)
    rows, bal = [], opening
    for m in qs[:5000]:
        bal += m.qty
        rows.append({"date": m.occurred_at, "warehouse": m.warehouse.name, "sku": m.variant.sku, "name": m.variant.name, "batch": m.batch.batch_no if m.batch else "", "type": m.movement_type, "reference": m.reference_number, "qty_in": str(m.qty if m.qty > 0 else Z), "qty_out": str(-m.qty if m.qty < 0 else Z), "unit_cost": str(m.unit_cost), "balance": str(bal), "by": m.performed_by.full_name if m.performed_by else ""})
    return {"columns": ["date", "warehouse", "sku", "name", "batch", "type", "reference", "qty_in", "qty_out", "unit_cost", "balance", "by"], "rows": rows, "totals": {"opening": str(opening), "closing": str(bal)}}


def movement_summary(date_from=None, date_to=None, warehouse=None, **_):
    from apps.inventory.models import StockMovement

    qs = _window(StockMovement.objects.all(), "occurred_at__date", date_from, date_to)
    if warehouse:
        qs = qs.filter(warehouse_id=warehouse)
    rows = [{"type": r["movement_type"], "count": r["n"], "qty": str(r["q"] or 0), "value": str(r["v"] or 0)} for r in qs.values("movement_type").annotate(n=Count("id"), q=Sum("qty"), v=Sum(F("qty") * F("unit_cost"))).order_by("movement_type")]
    return {"columns": ["type", "count", "qty", "value"], "rows": rows, "totals": {}}


def stock_valuation(warehouse=None, **_):
    from apps.inventory.services import StockService

    from apps.inventory.models import Warehouse

    wh = Warehouse.objects.filter(pk=warehouse).first() if warehouse else None
    v = StockService.valuation(wh)
    return {"columns": ["warehouse", "sku", "name", "batch", "qty", "avg_cost", "value"], "rows": v["rows"], "totals": {"value": v["total"]}}


def stock_ageing(warehouse=None, **_):
    from apps.inventory.models import StockLevel, StockMovement

    today = timezone.localdate()
    rows = []
    qs = StockLevel.objects.filter(qty_on_hand__gt=0).select_related("variant", "warehouse", "batch")
    if warehouse:
        qs = qs.filter(warehouse_id=warehouse)
    for lv in qs:
        last_in = StockMovement.objects.filter(variant=lv.variant, warehouse=lv.warehouse, qty__gt=0).order_by("-occurred_at").values_list("occurred_at", flat=True).first()
        days = (today - last_in.date()).days if last_in else None
        rows.append({"warehouse": lv.warehouse.name, "sku": lv.variant.sku, "name": lv.variant.name, "batch": lv.batch.batch_no if lv.batch else "", "qty": str(lv.qty_on_hand), "days_in_stock": days, "bucket": "0-30" if days is not None and days <= 30 else "31-60" if days is not None and days <= 60 else "61-90" if days is not None and days <= 90 else "90+", "value": str((lv.qty_on_hand * (lv.variant.avg_cost or lv.variant.cost_price)).quantize(Decimal("0.0001")))})
    return {"columns": ["warehouse", "sku", "name", "batch", "qty", "days_in_stock", "bucket", "value"], "rows": rows, "totals": {}}


def expiry_report(days=90, warehouse=None, **_):
    from apps.inventory.models import StockLevel

    today = timezone.localdate()
    qs = StockLevel.objects.filter(qty_on_hand__gt=0, batch__expiry_date__isnull=False, batch__expiry_date__lte=today + timezone.timedelta(days=int(days))).select_related("variant", "warehouse", "batch").order_by("batch__expiry_date")
    if warehouse:
        qs = qs.filter(warehouse_id=warehouse)
    rows = [{"warehouse": lv.warehouse.name, "sku": lv.variant.sku, "name": lv.variant.name, "batch": lv.batch.batch_no, "expiry": lv.batch.expiry_date, "days_left": (lv.batch.expiry_date - today).days, "qty": str(lv.qty_on_hand), "value": str((lv.qty_on_hand * (lv.variant.avg_cost or lv.variant.cost_price)).quantize(Decimal("0.0001")))} for lv in qs]
    return {"columns": ["warehouse", "sku", "name", "batch", "expiry", "days_left", "qty", "value"], "rows": rows, "totals": {"value": str(sum((Decimal(r["value"]) for r in rows), Z))}}


def low_stock(**_):
    from apps.inventory.services import AlertService

    rows = AlertService.reorder_suggestions()
    return {"columns": ["sku", "name", "supplier", "on_hand", "min_stock", "max_stock", "suggested_qty"], "rows": rows, "totals": {"count": len(rows)}}


def dead_stock(days=90, **_):
    from apps.inventory.models import InventoryAlert

    rows = [{"sku": a.variant.sku, "name": a.variant.name, "days": a.data.get("days"), "on_hand": a.data.get("on_hand"), "message": a.message} for a in InventoryAlert.objects.filter(type="dead_stock", resolved_at__isnull=True).select_related("variant")]
    return {"columns": ["sku", "name", "days", "on_hand"], "rows": rows, "totals": {"count": len(rows)}}


def wastage_by_reason(date_from=None, date_to=None, **_):
    from apps.inventory.models import StockAdjustment

    qs = _window(StockAdjustment.objects.filter(status="approved", type="decrease"), "date", date_from, date_to)
    rows = [{"reason": r["reason__name"], "category": r["reason__category"], "adjustments": r["n"], "value": str(r["v"] or 0)} for r in qs.values("reason__name", "reason__category").annotate(n=Count("id"), v=Sum("total_value")).order_by("-v")]
    return {"columns": ["reason", "category", "adjustments", "value"], "rows": rows, "totals": {"value": str(sum((Decimal(r["value"]) for r in rows), Z))}}


def transfer_register(date_from=None, date_to=None, **_):
    from apps.inventory.models import StockTransfer

    qs = _window(StockTransfer.objects.all(), "date", date_from, date_to).select_related("from_warehouse", "to_warehouse")
    rows = [{"number": t.number, "date": t.date, "from": t.from_warehouse.name, "to": t.to_warehouse.name, "status": t.status, "lines": t.lines.count(), "lost": str(sum((l.qty_lost for l in t.lines.all()), Z))} for t in qs]
    return {"columns": ["number", "date", "from", "to", "status", "lines", "lost"], "rows": rows, "totals": {}}


def count_variance_history(date_from=None, date_to=None, **_):
    from apps.inventory.models import StockCount

    qs = _window(StockCount.objects.filter(status="posted"), "posted_at__date", date_from, date_to).select_related("warehouse", "adjustment", "adjustment_increase")
    rows = [{"number": c.number, "warehouse": c.warehouse.name, "posted_at": c.posted_at, "lines": c.lines.count(), "variances": c.lines.exclude(counted_qty=F("system_qty")).exclude(counted_qty__isnull=True).count(), "decrease": str(c.adjustment.total_value if c.adjustment_id else Z), "increase": str(c.adjustment_increase.total_value if c.adjustment_increase_id else Z)} for c in qs]
    return {"columns": ["number", "warehouse", "posted_at", "lines", "variances", "decrease", "increase"], "rows": rows, "totals": {}}


# ---------------------------------------------------------------------- delivery
def trip_register(date_from=None, date_to=None, **_):
    from apps.delivery.models import DeliveryTrip

    qs = _window(DeliveryTrip.objects.all(), "date", date_from, date_to).select_related("driver", "vehicle", "rep", "warehouse")
    rows = [{"number": t.number, "date": t.date, "driver": t.driver.name if t.driver_id else "", "vehicle": t.vehicle.reg_no if t.vehicle_id else "", "rep": t.rep.name if t.rep_id else "", "status": t.status, "stops": t.stops_count, "delivered": t.delivery_notes.filter(status__in=["delivered", "partially_delivered"]).count(), "cash": str(t.cash_collected), "loaded_value": str(t.loaded_value)} for t in qs]
    return {"columns": ["number", "date", "driver", "vehicle", "rep", "status", "stops", "delivered", "cash", "loaded_value"], "rows": rows, "totals": {"trips": len(rows), "cash": str(sum((Decimal(r["cash"]) for r in rows), Z))}}


def driver_performance(date_from=None, date_to=None, **_):
    from apps.delivery.models import DeliveryNote

    qs = _window(DeliveryNote.objects.filter(trip__isnull=False), "trip__date", date_from, date_to).select_related("trip__driver")
    out = defaultdict(lambda: {"stops": 0, "delivered": 0, "failed": 0, "returns": Z})
    for dn in qs:
        d = dn.trip.driver.name if dn.trip.driver_id else "(none)"
        out[d]["stops"] += 1
        if dn.status in ("delivered", "partially_delivered"):
            out[d]["delivered"] += 1
        if dn.status in ("failed", "returned"):
            out[d]["failed"] += 1
        out[d]["returns"] += sum((l.qty_returned for l in dn.lines.all()), Z)
    rows = [{"driver": k, "stops": v["stops"], "delivered": v["delivered"], "failed": v["failed"], "success_rate": str((Decimal(v["delivered"]) / v["stops"] * 100).quantize(Decimal("0.1")) if v["stops"] else Z), "returned_qty": str(v["returns"])} for k, v in out.items()]
    return {"columns": ["driver", "stops", "delivered", "failed", "success_rate", "returned_qty"], "rows": rows, "totals": {}}


def vehicle_usage(date_from=None, date_to=None, **_):
    from apps.delivery.models import VehicleLog

    qs = _window(VehicleLog.objects.all(), "date", date_from, date_to).select_related("vehicle")
    out = defaultdict(lambda: {"trips": 0, "km": 0, "fuel": Z, "expense": Z})
    for l in qs:
        v = out[l.vehicle.reg_no]
        v["trips"] += 1
        if l.odometer_start is not None and l.odometer_end is not None:
            v["km"] += max(l.odometer_end - l.odometer_start, 0)
        v["fuel"] += l.fuel_amount
        v["expense"] += l.expense
    rows = [{"vehicle": k, "trips": v["trips"], "km": v["km"], "fuel": str(v["fuel"]), "expense": str(v["expense"]), "cost_per_km": str(((v["fuel"] + v["expense"]) / v["km"]).quantize(Decimal("0.01")) if v["km"] else Z)} for k, v in out.items()]
    return {"columns": ["vehicle", "trips", "km", "fuel", "expense", "cost_per_km"], "rows": rows, "totals": {}}


def delivery_success(date_from=None, date_to=None, **_):
    from apps.delivery.models import DeliveryNote

    qs = _window(DeliveryNote.objects.filter(delivered_at__isnull=False), "delivered_at__date", date_from, date_to)
    counts = dict(qs.values_list("status").annotate(c=Count("id")).values_list("status", "c"))
    total = sum(counts.values())
    reasons = dict(qs.filter(status__in=["failed", "returned"]).values_list("failure_reason").annotate(c=Count("id")).values_list("failure_reason", "c"))
    rows = [{"status": k, "count": v} for k, v in counts.items()] + [{"status": f"reason: {k or '(none)'}", "count": v} for k, v in reasons.items()]
    return {"columns": ["status", "count"], "rows": rows, "totals": {"total": total, "success_rate": str((Decimal(counts.get("delivered", 0)) / total * 100).quantize(Decimal("0.1")) if total else Z)}}


def cash_per_trip(date_from=None, date_to=None, **_):
    from apps.accounting.models import Payment

    qs = _window(Payment.objects.filter(trip__isnull=False, status="posted"), "date", date_from, date_to).select_related("trip", "collected_by")
    rows = [{"trip": r["trip__number"], "date": r["trip__date"], "receipts": r["n"], "amount": str(r["s"] or 0)} for r in qs.values("trip__number", "trip__date").annotate(n=Count("id"), s=Sum("amount")).order_by("-trip__date")]
    return {"columns": ["trip", "date", "receipts", "amount"], "rows": rows, "totals": {"amount": str(sum((Decimal(r["amount"]) for r in rows), Z))}}


# --------------------------------------------------------------------- receivables
def collection_report(date_from=None, date_to=None, **_):
    from apps.accounting.models import Payment
    from apps.sales.models import RepCollection

    qs = _window(Payment.objects.filter(direction="in", status="posted", partner_type="customer"), "date", date_from, date_to).select_related("collected_by")
    rows = [{"collector": r["collected_by__full_name"] or "(unknown)", "kind": "staff", "receipts": r["n"], "amount": str(r["s"] or 0)} for r in qs.values("collected_by__full_name").annotate(n=Count("id"), s=Sum("amount"))]
    rc = _window(RepCollection.objects.exclude(status="cancelled"), "date", date_from, date_to)
    rows += [{"collector": r["rep__name"], "kind": "rep", "receipts": r["n"], "amount": str(r["s"] or 0)} for r in rc.values("rep__name").annotate(n=Count("id"), s=Sum("amount"))]
    return {"columns": ["collector", "kind", "receipts", "amount"], "rows": rows, "totals": {"amount": str(sum((Decimal(r["amount"]) for r in rows), Z))}}


def overdue_list(**_):
    from apps.sales.models import SalesInvoice

    today = timezone.localdate()
    rows = [{"invoice": i.number, "date": i.date, "due_date": i.due_date, "days_overdue": (today - i.due_date).days if i.due_date else 0, "customer": i.customer.name, "phone": i.customer.phone, "balance": str(i.total - i.paid_amount)} for i in SalesInvoice.objects.filter(status__in=["posted", "partially_paid", "overdue"], due_date__lt=today).select_related("customer").order_by("due_date")]
    return {"columns": ["invoice", "date", "due_date", "days_overdue", "customer", "phone", "balance"], "rows": rows, "totals": {"balance": str(sum((Decimal(r["balance"]) for r in rows), Z))}}


def cheque_register(cheque_status=None, **_):
    from apps.accounting.models import Payment

    qs = Payment.objects.filter(method="cheque").exclude(status="cancelled")
    if cheque_status:
        qs = qs.filter(cheque_status=cheque_status)
    rows = [{"number": p.number, "direction": p.direction, "partner": p.partner_name, "cheque_no": p.cheque_no, "cheque_date": p.cheque_date, "bank": p.cheque_bank, "amount": str(p.amount), "status": p.cheque_status} for p in qs.order_by("cheque_date")]
    return {"columns": ["number", "direction", "partner", "cheque_no", "cheque_date", "bank", "amount", "status"], "rows": rows, "totals": {}}


def credit_utilisation(**_):
    from apps.partners.models import Customer
    from apps.partners.services import CustomerService

    rows = []
    for c in Customer.objects.filter(credit_limit__gt=0):
        out = CustomerService.outstanding(c)
        rows.append({"code": c.code, "customer": c.name, "credit_limit": str(c.credit_limit), "outstanding": str(out), "utilisation_pct": str((out / c.credit_limit * 100).quantize(Decimal("0.1"))), "available": str(c.credit_limit - out)})
    rows.sort(key=lambda r: -Decimal(r["utilisation_pct"]))
    return {"columns": ["code", "customer", "credit_limit", "outstanding", "utilisation_pct", "available"], "rows": rows, "totals": {}}


def payment_register(date_from=None, date_to=None, direction=None, **_):
    from apps.accounting.models import Payment

    qs = _window(Payment.objects.exclude(status="cancelled"), "date", date_from, date_to).select_related("collected_by")
    if direction:
        qs = qs.filter(direction=direction)
    rows = [{"number": p.number, "date": p.date, "direction": p.direction, "partner": p.partner_name, "method": p.method, "reference": p.reference, "amount": str(p.amount), "by": p.collected_by.full_name if p.collected_by else ""} for p in qs]
    return {"columns": ["number", "date", "direction", "partner", "method", "reference", "amount", "by"], "rows": rows, "totals": {"in": str(sum((Decimal(r["amount"]) for r in rows if r["direction"] == "in"), Z)), "out": str(sum((Decimal(r["amount"]) for r in rows if r["direction"] == "out"), Z))}}


def due_payments_calendar(**_):
    from apps.purchasing.models import PurchaseBill

    rows = [{"due_date": r["due_date"], "bills": r["n"], "amount": str((r["t"] or 0) - (r["p"] or 0))} for r in PurchaseBill.objects.filter(status__in=["posted", "partially_paid"]).values("due_date").annotate(n=Count("id"), t=Sum("total"), p=Sum("paid_amount")).order_by("due_date")]
    return {"columns": ["due_date", "bills", "amount"], "rows": rows, "totals": {"amount": str(sum((Decimal(r["amount"]) for r in rows), Z))}}


def gross_margin_by_supplier(date_from=None, date_to=None, **_):
    return sales_by_supplier(date_from, date_to)


def out_of_area_activity(date_from=None, date_to=None, rep=None, **_):
    """
    Every time a rep's location was checked and fell outside the area they cover — what they were doing, how far
    out they were, and whether it was refused. This is the report the sales manager reads on Monday morning.
    """
    from apps.partners.models import GeoFenceEvent

    qs = _window(GeoFenceEvent.objects.exclude(verdict="inside"), "occurred_at__date", date_from, date_to).select_related("rep", "fence", "customer")
    if rep:
        qs = qs.filter(rep_id=rep)
    rows = [{
        "when": e.occurred_at, "rep": e.rep.name if e.rep_id else "", "action": e.get_action_display(),
        "outcome": "Refused" if e.blocked else ("No location" if e.verdict == "no_fix" else "Allowed"),
        "nearest_area": e.fence.name if e.fence_id else "", "distance_km": str(round((e.distance_m or 0) / 1000, 2)) if e.distance_m else "",
        "customer": e.customer.name if e.customer_id else "", "document": e.document_number,
        "lat": str(e.gps_lat or ""), "lng": str(e.gps_lng or ""),
    } for e in qs[:5000]]
    return {"columns": ["when", "rep", "action", "outcome", "nearest_area", "distance_km", "customer", "document", "lat", "lng"],
            "rows": rows, "totals": {"count": str(len(rows)), "refused": str(sum(1 for r in rows if r["outcome"] == "Refused"))}}


def area_coverage(**_):
    """Each working area: who covers it, how big it is, and how many customers fall inside."""
    from apps.partners.geofence import GeoFenceService, area_km2
    from apps.partners.models import GeoFence

    rows = []
    for f in GeoFence.objects.prefetch_related("reps"):
        inside = GeoFenceService.customers_in(f)
        rows.append({"area": f.name, "type": f.get_type_display(), "size_km2": str(area_km2(f)),
                     "reps": ", ".join(r.name for r in f.reps.all()) or "— not assigned —",
                     "customers": str(len(inside)), "active": f.is_active})
    rows.sort(key=lambda r: -int(r["customers"]))
    return {"columns": ["area", "type", "size_km2", "reps", "customers", "active"], "rows": rows,
            "totals": {"areas": str(len(rows)), "customers": str(sum(int(r["customers"]) for r in rows))}}


def customers_without_area(**_):
    """Customers no area covers — either the pin is missing, or nobody's area reaches them."""
    from apps.partners.geofence import GeoFenceService
    from apps.partners.models import Customer, GeoFence

    covered = set()
    for f in GeoFence.objects.filter(is_active=True):
        covered.update(c.pk for c in GeoFenceService.customers_in(f))
    rows = []
    for c in Customer.objects.filter(status="active").select_related("route", "territory")[:5000]:
        if c.pk in covered:
            continue
        rows.append({"customer": c.name, "code": c.code, "city": c.city, "route": c.route.name if c.route_id else "",
                     "territory": c.territory.name if c.territory_id else "",
                     "why": "No pin on the map" if c.gps_lat is None else "Outside every area",
                     "reps": ", ".join(r.name for r in c.assigned_reps.all())})
    return {"columns": ["customer", "code", "city", "route", "territory", "why", "reps"], "rows": rows,
            "totals": {"count": str(len(rows)), "no_pin": str(sum(1 for r in rows if r["why"] == "No pin on the map"))}}
