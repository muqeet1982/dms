from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from apps.catalog.services import TaxService
from apps.core.audit import AuditService
from apps.core.events import emit
from apps.core.exceptions import DomainError
from apps.core.numbering import next_number
from apps.core.tenancy import get_current_user
from apps.inventory.services import StockService

from .models import GoodsReceipt, GoodsReceiptLine, LandedCost, PurchaseBill, PurchaseOrder, PurchaseOrderLine, PurchaseReturn, PurchaseReturnLine

Q4 = Decimal("0.0001")


def q4(v):
    return Decimal(v or 0).quantize(Q4, ROUND_HALF_UP)


def _default_bin(variant, warehouse):
    """Where this product normally lives, when the receiver does not say otherwise."""
    bin_ = getattr(variant.product, "location", None)
    return bin_ if bin_ is not None and bin_.warehouse_id == warehouse.pk else None


def _base_qty(qty, packaging):
    return Decimal(qty) * (packaging.qty_in_base if packaging else Decimal("1"))


class PurchaseOrderService:
    @staticmethod
    def _compute_line(line, supplier, on_date):
        gross = q4(line.qty_ordered * line.unit_price)
        disc = q4(gross * line.discount_pct / 100)
        taxable = gross - disc
        tax_class = TaxService.resolve_class(line.variant, supplier)
        t = TaxService.compute(taxable, tax_class, on_date)
        line.tax1_type, line.tax1_rate, line.tax1_amount = t["tax1_type"], t["tax1_rate"], t["tax1_amount"]
        line.tax2_type, line.tax2_rate, line.tax2_amount = t["tax2_type"], t["tax2_rate"], t["tax2_amount"]
        line.line_total = taxable + line.tax1_amount + line.tax2_amount
        return gross, disc

    @staticmethod
    def recompute(po):
        subtotal = discount = t1 = t2 = Decimal("0")
        for line in po.lines.select_related("variant", "variant__product"):
            gross, disc = PurchaseOrderService._compute_line(line, po.supplier, po.date)
            line.save()
            subtotal += gross
            discount += disc
            t1 += line.tax1_amount
            t2 += line.tax2_amount
        po.subtotal, po.discount, po.tax1_total, po.tax2_total = subtotal, discount, t1, t2
        po.total = subtotal - discount + t1 + t2 + po.freight
        po.save(update_fields=["subtotal", "discount", "tax1_total", "tax2_total", "total", "updated_at"])
        return po

    @staticmethod
    @transaction.atomic
    def create(*, supplier, warehouse, lines, date=None, expected_date=None, notes="", freight=Decimal("0"), supplier_ref="", reference=""):
        po = PurchaseOrder.objects.create(number=next_number("purchase_order"), supplier=supplier, warehouse=warehouse, date=date or timezone.localdate(), expected_date=expected_date, notes=notes, freight=freight or 0, supplier_ref=supplier_ref, reference=reference, currency=supplier.tenant.base_currency)
        PurchaseOrderService.write_lines(po, lines)
        AuditService.log("created", po, after={"supplier": supplier.name, "total": str(po.total)})
        return po

    @staticmethod
    def write_lines(po, lines):
        po.lines.all().delete()
        for i, l in enumerate(lines):
            packaging = l.get("packaging")
            qty = _base_qty(l["qty"], packaging)
            unit_price = Decimal(l["unit_price"]) / (packaging.qty_in_base if packaging else 1) if l.get("price_per_packaging") else Decimal(l["unit_price"])
            PurchaseOrderLine.objects.create(po=po, variant=l["variant"], packaging=packaging, qty_ordered=qty, unit_price=q4(unit_price), discount_pct=l.get("discount_pct", 0) or 0, sequence=i)
        PurchaseOrderService.recompute(po)

    @staticmethod
    @transaction.atomic
    def send(po):
        if po.status != "draft":
            raise DomainError("Only draft POs can be sent/approved.")
        if not po.lines.exists():
            raise DomainError("PO has no lines.")
        po.status = "sent"
        po.approved_by = get_current_user()
        po.approved_at = timezone.now()
        po.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
        AuditService.log("status.sent", po, before={"status": "draft"}, after={"status": "sent"})
        return po

    @staticmethod
    @transaction.atomic
    def cancel(po, reason=""):
        if po.status in ("received", "cancelled"):
            raise DomainError("PO cannot be cancelled.")
        if po.grns.filter(status="posted").exists():
            raise DomainError("PO has posted receipts; close it instead.")
        po.status = "cancelled"
        po.notes = (po.notes + f"\nCancelled: {reason}").strip()
        po.save(update_fields=["status", "notes", "updated_at"])
        AuditService.log("status.cancelled", po, after={"reason": reason})
        return po

    @staticmethod
    def refresh_status(po):
        lines = list(PurchaseOrderLine.objects.filter(po=po))
        if not lines:
            return
        # a line closed short counts as finished, so the order can be completed with less than was ordered
        if all(l.closed or l.qty_received >= l.qty_ordered for l in lines):
            po.status = "received"
        elif any(l.qty_received > 0 for l in lines):
            po.status = "partially_received"
        elif po.status in ("received", "partially_received"):
            # Cancelling the only receipt used to leave the order reading "partially received" with nothing
            # received against it — the status only ever stepped forward. An order with no goods on it is
            # waiting for the supplier again.
            po.status = "sent" if po.approved_at else "draft"
        po.save(update_fields=["status", "updated_at"])

    @staticmethod
    @transaction.atomic
    def from_reorder(supplier, rows):
        """Draft PO from reorder suggestions (§7.4)."""
        from apps.catalog.models import ProductVariant
        from apps.inventory.models import Warehouse

        wh = Warehouse.objects.filter(type="main", is_active=True).first() or Warehouse.objects.first()
        lines = [{"variant": ProductVariant.objects.get(pk=r["variant_id"]), "qty": Decimal(r["suggested_qty"]), "unit_price": Decimal(r.get("cost") or 0)} for r in rows if Decimal(r["suggested_qty"]) > 0]
        if not lines:
            raise DomainError("Nothing to reorder.")
        return PurchaseOrderService.create(supplier=supplier, warehouse=wh, lines=lines, notes="Auto-generated from reorder suggestions")


class GoodsReceiptService:
    @staticmethod
    def check_order_open(po):
        """Goods are received against an *approved* order: create → approve → receive, never straight off a draft."""
        if po is None:
            return
        if po.status == "draft":
            raise DomainError(f"{po.number} has not been approved yet.", code="po_not_approved")
        if po.status == "cancelled":
            raise DomainError(f"{po.number} was cancelled.", code="po_cancelled")

    @staticmethod
    def open_draft_for(po):
        """
        The receipt already being keyed against an order, if there is one.

        A supplier delivers an order in pieces, so one order collects several receipts over time — but only ever
        one at a time is *being entered*. A second draft next to an open one is the same delivery keyed twice (a
        reload, the back button, a second tab), which is what "one order, one receipt" is about.
        """
        if po is None:
            return None
        return GoodsReceipt.objects.filter(po=po, status="draft").order_by("-created_at").first()

    @staticmethod
    def check_challan_unused(supplier, challan, *, exclude=None):
        """
        One delivery note, one receipt.

        The challan number is the supplier's own identifier for a delivery, so it is the fact that separates
        "they delivered again" from "we keyed the same delivery twice" — the one distinction this screen cannot
        work out for itself. A genuine second delivery arrives with a different number.
        """
        challan = (challan or "").strip()
        if not challan:
            return
        from .models import GoodsReceipt

        clash = GoodsReceipt.objects.filter(supplier=supplier, supplier_challan_no__iexact=challan).exclude(status="cancelled")
        if exclude is not None:
            clash = clash.exclude(pk=exclude.pk)
        other = clash.first()
        if other is not None:
            raise DomainError(
                f"Challan {challan} was already received on {other.number} ({other.date:%d %b %Y}). "
                f"If this is the same delivery, open {other.number} instead. If the supplier has reused the "
                f"number, send `confirm_duplicate_challan` to record it anyway.",
                code="duplicate_challan", status_code=409,
                errors={"grn": [str(other.pk)], "number": [other.number]},
            )

    @staticmethod
    def check_editable(grn):
        """
        A receipt can only be corrected while it is still a draft.

        Once it is posted the stock is in the warehouse, a journal entry stands behind it and a supplier bill may
        quote it, so changing the quantities afterwards would leave the inventory valuation wrong. Cancelling the
        receipt reverses both the stock and the journal, and the delivery is then keyed again.
        """
        if grn.status == "posted":
            if grn.billed or grn.bills.exists():
                raise DomainError(f"{grn.number} is posted and already billed, so it cannot be changed. Raise a purchase return for whatever is going back to the supplier.", code="grn_billed", status_code=400)
            raise DomainError(f"{grn.number} is posted, so its stock is already in {grn.warehouse.name}. Cancel it — that reverses the stock and the journal entry — then receive the delivery again.", code="grn_posted", status_code=400)
        if grn.status == "cancelled":
            raise DomainError(f"{grn.number} was cancelled. Receive this delivery on a new receipt.", code="grn_cancelled", status_code=400)

    @staticmethod
    def write_lines(grn, lines, *, replace=True, start=0):
        """
        The rows of a receipt, written the one way whether it is being created or corrected: box counts turned into
        base units, the cost taken from the order line when the receiver does not type one, and the put-away bin
        falling back to where the product is normally kept.

        `replace=False` appends instead of rewriting, which is how `add_lines` puts a new product on a receipt
        that is already part-keyed.
        """
        if replace:
            grn.lines.all().delete()
        for i, l in enumerate(lines, start=start):
            packaging = l.get("packaging")
            po_line = l.get("po_line")
            unit_cost = l.get("unit_cost")
            if unit_cost is None and po_line is not None:
                unit_cost = po_line.unit_price * (1 - po_line.discount_pct / 100)
            if unit_cost is None:
                unit_cost = l["variant"].cost_price
            if l.get("price_per_packaging") and packaging:
                unit_cost = Decimal(unit_cost) / packaging.qty_in_base
            GoodsReceiptLine.objects.create(
                grn=grn, po_line=po_line, variant=l["variant"], packaging=packaging, qty_accepted=_base_qty(l.get("qty_accepted") or 0, packaging), qty_damaged=_base_qty(l.get("qty_damaged") or 0, packaging),
                qty_short=_base_qty(l.get("qty_short") or 0, packaging), batch_no=l.get("batch_no", "") or "", mfg_date=l.get("mfg_date"), expiry_date=l.get("expiry_date"),
                bin=l.get("bin") or _default_bin(l["variant"], grn.warehouse), reject_reason=(l.get("reject_reason") or "")[:200],
                note=(l.get("note") or "")[:200], unit_cost=q4(unit_cost), sequence=i,
            )
            # "the rest is not coming": the order line stops waiting for the balance
            if l.get("close_line") and po_line is not None:
                po_line.closed = True
                po_line.closed_reason = (l.get("reject_reason") or "Closed short on receipt")[:200]
                po_line.save(update_fields=["closed", "closed_reason"])

    @staticmethod
    def add_lines(grn, lines):
        """
        Add what was just counted to the receipt already open, instead of replacing it.

        This is the interrupted count: a thousand items arrive, five hundred are keyed, the storeman breaks off
        and comes back. Until the receipt is posted it stays the one record for that delivery and everything
        keyed into it adds up — the same way the scanner has always worked. Replacing would have thrown the
        first five hundred away and posted half a delivery as a whole one.

        A row is matched on the order line (or the product), its packaging **and its batch**, so two batches of
        the same item stay two rows and keep their own expiry dates.
        """
        at = grn.lines.count()
        for i, l in enumerate(lines):
            packaging = l.get("packaging")
            po_line = l.get("po_line")
            batch = (l.get("batch_no") or "").strip()
            row = grn.lines.filter(variant=l["variant"], packaging=packaging, batch_no=batch)
            row = row.filter(po_line=po_line) if po_line is not None else row.filter(po_line__isnull=True)
            existing = row.first()
            if existing is None:
                GoodsReceiptService.write_lines(grn, lines[i:i + 1], replace=False, start=at + i)
                continue
            existing.qty_accepted += _base_qty(l.get("qty_accepted") or 0, packaging)
            existing.qty_damaged += _base_qty(l.get("qty_damaged") or 0, packaging)
            existing.qty_short += _base_qty(l.get("qty_short") or 0, packaging)
            # a later sitting fills in what the first one did not know
            if not existing.expiry_date and l.get("expiry_date"):
                existing.expiry_date = l["expiry_date"]
            if not existing.reject_reason and l.get("reject_reason"):
                existing.reject_reason = l["reject_reason"][:200]
            if not existing.note and l.get("note"):
                existing.note = l["note"][:200]
            existing.save(update_fields=["qty_accepted", "qty_damaged", "qty_short", "expiry_date", "reject_reason", "note"])
            if l.get("close_line") and po_line is not None:
                po_line.closed = True
                po_line.closed_reason = (l.get("reject_reason") or "Closed short on receipt")[:200]
                po_line.save(update_fields=["closed", "closed_reason"])
        GoodsReceiptService.allocate_landed_costs(grn)
        return grn

    @staticmethod
    def write_landed_costs(grn, landed_costs):
        """Freight and customs on this delivery, replaced as a set — they are shared out over the rows, so a half-changed set would misprice the stock."""
        LandedCost.objects.filter(grn=grn).delete()
        for lc in landed_costs:
            LandedCost.objects.create(grn=grn, **lc)

    @staticmethod
    @transaction.atomic
    def create(*, supplier, warehouse, lines, po=None, date=None, supplier_challan_no="", notes="", landed_costs=None,
               confirm_duplicate_challan=False):
        """
        Record what the supplier delivered.

        One order keeps one receipt open: when an *empty* draft is already being keyed against this order, that
        draft is filled instead of a second receipt appearing beside it. A draft that already holds rows is not
        written over — see `check_can_fill`. A *posted* receipt is never reused: its stock has moved, so next
        week's delivery against the same order rightly gets a receipt of its own.
        """
        GoodsReceiptService.check_order_open(po)
        open_draft = GoodsReceiptService.open_draft_for(po)
        if open_draft is not None:
            # the one record for this delivery: everything keyed adds into it until somebody posts it, so the
            # challan is not checked against the receipt it belongs to
            return GoodsReceiptService._refill(open_draft, warehouse=warehouse, lines=lines, date=date, supplier_challan_no=supplier_challan_no, notes=notes, landed_costs=landed_costs)
        if not confirm_duplicate_challan:
            GoodsReceiptService.check_challan_unused(supplier, supplier_challan_no)
        grn = GoodsReceipt.objects.create(number=next_number("goods_receipt"), po=po, supplier=supplier, warehouse=warehouse, date=date or timezone.localdate(), supplier_challan_no=supplier_challan_no, notes=notes, received_by=get_current_user())
        GoodsReceiptService.write_lines(grn, lines)
        GoodsReceiptService.write_landed_costs(grn, landed_costs or [])
        GoodsReceiptService.allocate_landed_costs(grn)
        AuditService.log("created", grn, after={"supplier": supplier.name, "lines": len(lines)})
        return grn

    @staticmethod
    def _refill(grn, *, warehouse, lines, date, supplier_challan_no, notes, landed_costs):
        """
        Fill the receipt already open against this order with what was just keyed.

        Until it is posted this is *the* record for the delivery, so whatever is keyed **adds** to what is
        already on it — a thousand items counted five hundred before the break and five hundred after end up
        as one receipt of a thousand. Posting is what moves the stock and closes the record; the next delivery
        against the same order then opens a fresh one.

        Keying nothing (the scanning screen opens an empty receipt and scans into it afterwards) leaves the
        rows alone.
        """
        fields = ["date", "supplier_challan_no", "notes", "updated_at"]
        grn.date = date or grn.date
        grn.supplier_challan_no = supplier_challan_no or grn.supplier_challan_no
        grn.notes = notes or grn.notes
        if lines:
            # the bins on the rewritten rows belong to this warehouse, so the two move together
            grn.warehouse = warehouse
            fields.append("warehouse")
        grn.save(update_fields=fields)
        if lines:
            GoodsReceiptService.add_lines(grn, lines)
        if landed_costs:
            GoodsReceiptService.write_landed_costs(grn, landed_costs)
        GoodsReceiptService.allocate_landed_costs(grn)
        AuditService.log("added_to_open_receipt", grn, after={"added_lines": len(lines or []), "lines_now": grn.lines.count()})
        # the screen says which receipt it filled instead of announcing one that was never created
        grn.reused = True
        return grn

    @staticmethod
    @transaction.atomic
    def update(grn, *, lines=None, landed_costs=None):
        """Correct a receipt that has not been posted yet: what arrived, the batch and expiry, where it was put."""
        GoodsReceiptService.check_editable(grn)
        if lines is not None:
            GoodsReceiptService.write_lines(grn, lines)
        if landed_costs is not None:
            GoodsReceiptService.write_landed_costs(grn, landed_costs)
        GoodsReceiptService.allocate_landed_costs(grn)
        return grn

    @staticmethod
    def allocate_landed_costs(grn):
        lines = list(GoodsReceiptLine.objects.filter(grn=grn))
        total_lc = sum((lc.amount for lc in grn.landed_costs.all()), Decimal("0"))
        for l in lines:
            l.landed_cost_share = Decimal("0")
        if total_lc and lines:
            for lc in grn.landed_costs.all():
                if lc.allocation == "by_qty":
                    base = sum((l.qty_accepted for l in lines), Decimal("0")) or Decimal("1")
                    for l in lines:
                        l.landed_cost_share += q4(lc.amount * l.qty_accepted / base)
                else:
                    base = sum((l.qty_accepted * l.unit_cost for l in lines), Decimal("0")) or Decimal("1")
                    for l in lines:
                        l.landed_cost_share += q4(lc.amount * (l.qty_accepted * l.unit_cost) / base)
        total = Decimal("0")
        for l in lines:
            l.save(update_fields=["landed_cost_share"])
            total += q4(l.qty_accepted * l.unit_cost) + l.landed_cost_share
        grn.total_value = total
        grn.save(update_fields=["total_value"])

    @staticmethod
    @transaction.atomic
    def post(grn):
        if grn.status != "draft":
            raise DomainError(f"{grn.number} is already {grn.get_status_display().lower()}.", code="grn_not_draft", status_code=400)
        if not grn.lines.exists():
            raise DomainError("GRN has no lines.")
        GoodsReceiptService.check_order_open(grn.po)
        for line in grn.lines.select_related("variant", "variant__product", "po_line"):
            product = line.variant.product
            # Batch is optional for the receiver: a product that *is* batch-tracked still has to stay traceable
            # and sellable (FEFO picks by batch), so when nothing is typed the receipt itself becomes the batch
            # — "GRN-00012". Nothing is asked of the person on the dock for that.
            #
            # An expiry date is different: it cannot be invented, and it is the whole point of the flag — the
            # near-expiry alerts, the expiry report and shelf-life picking all read it. So an expiry-tracked
            # product is refused at *posting* (the draft saves fine, so the dock can come back with the box in
            # hand), and it is given a batch to hang the date on even when it is not batch-tracked.
            if (product.is_batch_tracked or product.is_expiry_tracked) and not line.batch_no:
                line.batch_no = grn.supplier_challan_no.strip() or grn.number
                line.save(update_fields=["batch_no"])
            if product.is_expiry_tracked and not line.expiry_date:
                raise DomainError(
                    f"{line.variant.name} is expiry-tracked: type the expiry date from the box before posting "
                    f"{grn.number}.",
                    code="expiry_required", status_code=400,
                )
            batch = StockService.get_or_create_batch(line.variant, line.batch_no, line.expiry_date, line.mfg_date, grn.supplier, line) if line.batch_no else None
            line.batch = batch
            line.save(update_fields=["batch"])
            landed_unit = (line.landed_cost_share / line.qty_accepted) if line.qty_accepted else Decimal("0")
            if line.qty_accepted > 0:
                StockService.move(warehouse=grn.warehouse, variant=line.variant, qty=line.qty_accepted, movement_type="grn", unit_cost=q4(line.unit_cost + landed_unit), batch=batch, bin=line.bin, reference=grn, occurred_at=timezone.now())
            if line.qty_damaged > 0:
                # damaged goods go to quarantine warehouse if one exists (no cost to inventory), else recorded only
                from apps.inventory.models import Warehouse

                q = Warehouse.objects.filter(type="quarantine", is_active=True).first()
                if q is not None:
                    StockService.move(warehouse=q, variant=line.variant, qty=line.qty_damaged, movement_type="grn", unit_cost=Decimal("0"), batch=batch, reference=grn, note="Damaged on receipt")
            if line.po_line_id:
                pol = line.po_line
                pol.qty_received += line.qty_accepted
                pol.save(update_fields=["qty_received"])
        grn.status = "posted"
        grn.posted_at = timezone.now()
        from apps.accounting.services import PostingService

        grn.journal = PostingService.post_grn(grn)
        grn.save(update_fields=["status", "posted_at", "journal", "updated_at"])
        if grn.po_id:
            PurchaseOrderService.refresh_status(grn.po)
        AuditService.log("status.posted", grn, after={"value": str(grn.total_value), "journal": grn.journal.number if grn.journal else None})
        emit("grn.posted", grn=grn)
        return grn

    @staticmethod
    @transaction.atomic
    def cancel(grn, reason=""):
        if grn.status == "posted":
            if grn.billed or grn.bills.exists():
                raise DomainError("GRN is billed; use a purchase return instead.")
            from apps.accounting.services import PostingService

            for line in grn.lines.select_related("variant", "variant__product", "batch"):
                if line.qty_accepted > 0:
                    StockService.move(warehouse=grn.warehouse, variant=line.variant, qty=-line.qty_accepted, movement_type="grn_cancelled", batch=line.batch, bin=line.bin, unit_cost=line.unit_cost, reference=grn, note=f"GRN cancelled: {reason}")
                if line.po_line_id:
                    line.po_line.qty_received -= line.qty_accepted
                    line.po_line.save(update_fields=["qty_received"])
            if grn.journal_id:
                PostingService.reverse(grn.journal, memo=f"GRN {grn.number} cancelled")
            if grn.po_id:
                PurchaseOrderService.refresh_status(grn.po)
        grn.status = "cancelled"
        grn.notes = (grn.notes + f"\nCancelled: {reason}").strip()
        grn.save(update_fields=["status", "notes", "updated_at"])
        AuditService.log("status.cancelled", grn, after={"reason": reason})
        return grn


class PurchaseBillService:
    @staticmethod
    @transaction.atomic
    def create(*, supplier, grns=(), bill_date=None, due_date=None, supplier_invoice_no="", lines=None, discount=Decimal("0"), freight=Decimal("0"), notes="", tax1_type=None, tax2_type=None, tax1_total=None, tax2_total=None):
        bill_date = bill_date or timezone.localdate()
        bill = PurchaseBill.objects.create(number=next_number("purchase_bill"), supplier=supplier, bill_date=bill_date, due_date=due_date or (bill_date + timezone.timedelta(days=supplier.payment_terms_days or 0)), supplier_invoice_no=supplier_invoice_no, notes=notes, discount=discount or 0, freight=freight or 0)
        snapshot, subtotal, t1, t2 = [], Decimal("0"), Decimal("0"), Decimal("0")
        if grns:
            for g in grns:
                if g.status != "posted":
                    raise DomainError(f"GRN {g.number} is not posted.")
                if g.billed:
                    raise DomainError(f"GRN {g.number} is already billed.")
                bill.grns.add(g)
                for l in g.lines.select_related("variant", "variant__product"):
                    price = l.po_line.unit_price if l.po_line_id else l.unit_cost
                    disc = l.po_line.discount_pct if l.po_line_id else Decimal("0")
                    gross = q4(l.qty_accepted * price)
                    taxable = gross - q4(gross * disc / 100)
                    tax_class = TaxService.resolve_class(l.variant, supplier)
                    t = TaxService.compute(taxable, tax_class, bill_date)
                    snapshot.append({"grn": g.number, "variant_id": str(l.variant_id), "sku": l.variant.sku, "name": l.variant.name, "qty": str(l.qty_accepted), "unit_price": str(price), "discount_pct": str(disc), "taxable": str(taxable), "tax1": str(t["tax1_amount"]), "tax2": str(t["tax2_amount"]), "total": str(taxable + t["tax1_amount"] + t["tax2_amount"])})
                    subtotal += taxable
                    t1 += t["tax1_amount"]
                    t2 += t["tax2_amount"]
                    if t["tax1_type"] and tax1_type is None:
                        tax1_type = t["tax1_type"]
                    if t["tax2_type"] and tax2_type is None:
                        tax2_type = t["tax2_type"]
                g.billed = True
                g.save(update_fields=["billed"])
        else:
            for l in lines or []:
                taxable = q4(Decimal(l["qty"]) * Decimal(l["unit_price"]))
                snapshot.append({"sku": l.get("sku", ""), "name": l.get("name", ""), "variant_id": l.get("variant_id"), "qty": str(l["qty"]), "unit_price": str(l["unit_price"]), "taxable": str(taxable), "tax1": str(l.get("tax1", 0)), "tax2": str(l.get("tax2", 0)), "total": str(taxable + Decimal(str(l.get("tax1", 0))) + Decimal(str(l.get("tax2", 0))))})
                subtotal += taxable
                t1 += Decimal(str(l.get("tax1", 0)))
                t2 += Decimal(str(l.get("tax2", 0)))
        if tax1_total is not None:
            t1 = Decimal(tax1_total)
        if tax2_total is not None:
            t2 = Decimal(tax2_total)
        bill.lines = snapshot
        bill.subtotal = subtotal - (discount or 0)
        bill.tax1_type, bill.tax2_type = tax1_type, tax2_type
        bill.tax1_total, bill.tax2_total = t1, t2
        bill.total = bill.subtotal + t1 + t2 + (freight or 0)
        bill.save()
        AuditService.log("created", bill, after={"supplier": supplier.name, "total": str(bill.total)})
        return bill

    @staticmethod
    @transaction.atomic
    def post(bill):
        if bill.status != "draft":
            raise DomainError("Bill already posted.")
        from apps.accounting.services import PostingService

        bill.journal = PostingService.post_purchase_bill(bill)
        bill.status = "posted"
        bill.save(update_fields=["journal", "status", "updated_at"])
        AuditService.log("status.posted", bill, after={"total": str(bill.total), "journal": bill.journal.number if bill.journal else None})
        return bill

    @staticmethod
    @transaction.atomic
    def cancel(bill, reason=""):
        if bill.paid_amount > 0:
            raise DomainError("Bill has payments; cancel them first.")
        if bill.status == "posted" and bill.journal_id:
            from apps.accounting.services import PostingService

            PostingService.reverse(bill.journal, memo=f"Bill {bill.number} cancelled")
        for g in bill.grns.all():
            g.billed = False
            g.save(update_fields=["billed"])
        bill.status = "cancelled"
        bill.notes = (bill.notes + f"\nCancelled: {reason}").strip()
        bill.save(update_fields=["status", "notes", "updated_at"])
        AuditService.log("status.cancelled", bill, after={"reason": reason})
        return bill


class PurchaseReturnService:
    @staticmethod
    @transaction.atomic
    def create(*, supplier, warehouse, lines, grn=None, date=None, reason="", reason_code=None, notes="", is_debit_note_only=False):
        ret = PurchaseReturn.objects.create(number=next_number("purchase_return"), supplier=supplier, warehouse=warehouse, grn=grn, date=date or timezone.localdate(), reason=reason, reason_code=reason_code, notes=notes, is_debit_note_only=is_debit_note_only)
        total = Decimal("0")
        for l in lines:
            unit_cost = Decimal(l.get("unit_cost") or l["variant"].avg_cost or l["variant"].cost_price)
            PurchaseReturnLine.objects.create(ret=ret, variant=l["variant"], batch=l.get("batch"), qty=l["qty"], unit_cost=q4(unit_cost), reason_code=l.get("reason_code") or reason_code, reason=l.get("reason", ""))
            total += q4(Decimal(l["qty"]) * unit_cost)
        ret.total = total
        ret.save(update_fields=["total"])
        AuditService.log("created", ret, after={"supplier": supplier.name, "total": str(total)})
        return ret

    @staticmethod
    @transaction.atomic
    def post(ret):
        if ret.status != "draft":
            raise DomainError("Return already posted.")
        from apps.accounting.services import PostingService

        if ret.is_debit_note_only:
            ret.journal = PostingService.post_supplier_debit_note(ret.supplier, ret.total, ret.reason or "Damaged goods claim", ret.date, source=ret)
        else:
            total = Decimal("0")
            for line in ret.lines.select_related("variant", "variant__product", "batch"):
                mv = StockService.move(warehouse=ret.warehouse, variant=line.variant, qty=-line.qty, movement_type="purchase_return", batch=line.batch, reference=ret, note=line.reason or ret.reason)
                line.unit_cost = mv.unit_cost
                line.save(update_fields=["unit_cost"])
                total += q4(line.qty * mv.unit_cost)
            ret.total = total
            ret.journal = PostingService.post_purchase_return(ret)
        ret.status = "posted"
        ret.save(update_fields=["status", "journal", "total", "updated_at"])
        AuditService.log("status.posted", ret, after={"total": str(ret.total)})
        return ret
