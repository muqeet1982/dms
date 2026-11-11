from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.audit import AuditService
from apps.core.events import emit
from apps.core.exceptions import DomainError
from apps.core.numbering import derived_base, derived_number, next_number, renumber
from apps.core.tenancy import get_current_tenant, get_current_user
from apps.inventory.services import StockService
from apps.sales.models import SalesOrder, StockReservation

from .models import DeliveryNote, DeliveryNoteLine, DeliveryTrip, PickList, PickListLine, VehicleLog

Q4 = Decimal("0.0001")


class PickListService:
    @staticmethod
    @transaction.atomic
    def create(*, warehouse, orders, notes=""):
        orders = list(orders)
        # one load form, one order, numbered after it (owner, 5 Nov 2026: "only limit one its own order that is it")
        if len(orders) > 1:
            raise DomainError("A load form is for one order — make one for each.", code="one_order_per_load_form")
        # a partly delivered order's balance goes out on a form of its own (owner, 5 Nov 2026: no load form, no delivery)
        orders = [o for o in orders if o.status in ("confirmed", "partially_delivered") and o.warehouse_id == warehouse.pk]
        if not orders:
            raise DomainError("Select a confirmed order from this warehouse.")
        # one live load form per order. An order stays "confirmed" until its stock is issued, so it could be picked again
        # on Load forms → New, or twice by a double click: TDN-PL-00132, 00155 and 00158 each came out twice (4 Nov 2026).
        # The lock makes a second click wait for the first to finish, then find its form.
        list(SalesOrder.objects.select_for_update().filter(pk__in=[o.pk for o in orders]).order_by("pk").values_list("pk", flat=True))
        held = PickListService.forms_holding(orders)
        if held:
            words = "; ".join(f"{', '.join(sorted(nums))} {'is' if len(nums) == 1 else 'are'} already on load form {form.number}" for form, nums in held.items())
            raise DomainError(f"{words}.", code="duplicate", errors={"picklist": [str(form.pk) for form in held]})
        # an order whose form was cancelled (edited, or cancelled by hand) gets that same form back, so its number never
        # grows a "-2"; the audit log keeps what happened to it
        pl = next((f for f in PickList.objects.filter(orders=orders[0], status="cancelled").order_by("created_at") if f.orders.count() == 1), None)
        if pl is None:
            pl = PickList.objects.create(number=derived_number(orders[0].number, "pick_list", PickList), warehouse=warehouse, notes=notes)
        else:
            pl.lines.all().delete()
            pl.warehouse, pl.notes, pl.status = warehouse, notes, "open"
            pl.picker = pl.sent_by = pl.issued_by = pl.sent_at = pl.issued_at = pl.picked_at = None
            pl.save()
        pl.orders.set(orders)
        tenant = get_current_tenant()
        for o in orders:
            for line in o.lines.select_related("variant"):
                qty = line.qty_ordered + line.free_qty - line.qty_dispatched
                if qty <= 0:
                    continue
                plan, short = StockService.fefo_batches(line.variant, warehouse, qty, o.customer.min_shelf_life_days or tenant.setting("min_shelf_life_days"))
                for batch, q, bin in plan:
                    PickListLine.objects.create(picklist=pl, order_line=line, variant=line.variant, batch=batch, bin=bin, qty_to_pick=q)
                if short > 0:
                    PickListLine.objects.create(picklist=pl, order_line=line, variant=line.variant, qty_to_pick=short)
        if not pl.lines.exists():
            raise DomainError(f"{orders[0].number} has nothing left to issue.", code="nothing_to_issue")
        AuditService.log("created", pl, after={"orders": [o.number for o in orders]})
        return pl

    @staticmethod
    def forms_holding(orders):
        """{live load form: order numbers} — the forms still carrying goods for these orders."""
        held = {}
        for line in PickListLine.objects.filter(order_line__order__in=orders).exclude(picklist__status="cancelled").select_related("picklist", "order_line__order"):
            # what an issued form carried has been delivered or came back, so it does not hold a partly delivered order's balance
            if line.order_line.order.status == "partially_delivered" and line.picklist.status in ("issued", "partially_issued"):
                continue
            held.setdefault(line.picklist, set()).add(line.order_line.order.number)
        return held

    @staticmethod
    @transaction.atomic
    def cancel(pl, reason=""):
        """
        Take back a load form nothing has left the warehouse on (owner, 5 Nov 2026). No stock moves — stock only leaves at
        issue — so its orders are simply free for another load form. Its lines and orders stay on it as the record.
        """
        if pl.status == "cancelled":
            raise DomainError("This load form is already cancelled.")
        if pl.status in ("issued", "partially_issued") or pl.delivery_notes.filter(stock_out_done=True).exists():
            raise DomainError("Stock has already left on this load form, so it cannot be cancelled.", code="already_issued")
        if pl.trips.exclude(status="cancelled").exists():
            raise DomainError("This load form is on a dispatch — cancel the dispatch first.", code="on_trip")
        before = pl.status
        pl.status = "cancelled"
        pl.save(update_fields=["status", "updated_at"])
        AuditService.log("status.cancelled", pl, before={"status": before}, after={"reason": reason})
        return pl

    @staticmethod
    @transaction.atomic
    def update_lines(pl, lines, notes=None):
        """
        Correct a load form BEFORE it goes to the warehouse (owner, 27 Oct 2026: "i need to create load form and
        save it" — created, seen, edited, saved, and only then sent). lines: [{line_id, qty_to_pick}]; 0 takes the
        line off. A line can ask for at most what its order line still has pending, counting the form's other
        lines for the same order line (two batches of one item).
        """
        if pl.status != "open":
            raise DomainError("This load form has already gone to the warehouse — it can no longer be edited.", code="not_open")
        by_id = {str(l.pk): l for l in pl.lines.select_related("order_line", "variant")}
        for row in lines or []:
            line = by_id.get(str(row.get("line_id")))
            if line is None:
                raise DomainError("That line is not on this load form.")
            try:
                qty = Decimal(str(row.get("qty_to_pick", line.qty_to_pick) or 0))
            except ArithmeticError:
                raise DomainError(f"{line.variant.name}: the quantity must be a number.")
            if qty < 0:
                raise DomainError(f"{line.variant.name}: the quantity cannot be negative.")
            line.qty_to_pick = qty
        for line in by_id.values():
            ol = line.order_line
            if ol is None:
                continue
            pending = ol.qty_ordered + ol.free_qty - ol.qty_dispatched
            asked = sum((x.qty_to_pick for x in by_id.values() if x.order_line_id == ol.pk), Decimal("0"))
            if asked > pending:
                raise DomainError(f"{line.variant.name}: {asked:g} asked but the order has only {pending:g} pending.")
        kept = [l for l in by_id.values() if l.qty_to_pick > 0]
        if not kept:
            raise DomainError("A load form needs at least one line — cancel the order's load form instead of emptying it.")
        for line in by_id.values():
            if line.qty_to_pick > 0:
                line.save(update_fields=["qty_to_pick"])
            else:
                line.delete()
        if notes is not None:
            pl.notes = notes
        pl.save(update_fields=["notes", "updated_at"])
        AuditService.log("load_form.edited", pl, after={"lines": [{"item": l.variant.name, "qty": str(l.qty_to_pick)} for l in kept]})
        return pl

    @staticmethod
    @transaction.atomic
    def record_picks(pl, picks):
        """picks: [{line_id, qty_picked, batch?, scanned?}]"""
        if pl.status == "picked":
            raise DomainError("Pick list already completed.")
        if pl.status == "cancelled":
            raise DomainError("This load form was cancelled.")
        if pl.status == "open":
            PickListService.send(pl)   # picking it is the warehouse having it
        from apps.inventory.models import Batch

        pl.status = "picking"
        pl.picker = pl.picker or get_current_user()
        for p in picks:
            line = pl.lines.get(pk=p["line_id"])
            line.qty_picked = Decimal(p["qty_picked"])
            if p.get("batch"):
                line.batch = Batch.objects.filter(pk=p["batch"]).first()
            if p.get("scanned"):
                line.scanned_at = timezone.now()
            line.save()
        pl.save(update_fields=["status", "picker", "updated_at"])
        return pl

    @staticmethod
    @transaction.atomic
    def scans(pl, scans):
        """Batch endpoint for scanner mode: [{code, qty}] → increments matching lines."""
        from apps.catalog.services import BarcodeParser

        if pl.status == "cancelled":
            raise DomainError("This load form was cancelled.")
        if pl.status == "open":
            PickListService.send(pl)

        for s in scans:
            hit = BarcodeParser.parse(s["code"])
            variant, batch = hit["variant"], hit["batch"]
            # one scan of a carton label picks the whole carton, as it does on receiving
            qty = Decimal(str(s.get("qty", 1))) * hit["qty_per_scan"]
            line = pl.lines.filter(variant=variant, batch=batch).first() if batch else None
            if line is None:
                line = pl.lines.filter(variant=variant).order_by("qty_picked").first()
            if line is None:
                raise DomainError(f"{variant.name} is not on this pick list.")
            line.qty_picked += qty
            if batch and line.batch_id is None:
                line.batch = batch
            line.scanned_at = timezone.now()
            line.save()
        pl.status = "picking"
        pl.picker = pl.picker or get_current_user()
        pl.save(update_fields=["status", "picker", "updated_at"])
        return pl

    @staticmethod
    @transaction.atomic
    def send(pl):
        """Hand the load form to the warehouse: it shows up in their queue and they get a notification."""
        if pl.status not in ("open", "sent"):
            raise DomainError("This load form has already gone to the warehouse.")
        pl.status = "sent"
        pl.sent_at = timezone.now()
        pl.sent_by = get_current_user()
        pl.save(update_fields=["status", "sent_at", "sent_by", "updated_at"])
        from apps.core.notifications import notify_permission

        tenant = get_current_tenant()
        notify_permission(
            # whoever may issue the stock is who needs telling — the same switch the issue endpoint checks
            "delivery.change_load_form", "load_form.sent", f"Load form {pl.number} to issue",
            f"{pl.orders.count()} order(s) for {pl.warehouse.name} — issue the stock when ready.",
            {"picklist": str(pl.pk)}, tenant_id=tenant.id if tenant else None,
        )
        AuditService.log("status.sent", pl, after={"warehouse": pl.warehouse.name})
        emit("load_form.sent", picklist=pl)
        return pl

    @staticmethod
    @transaction.atomic
    def issue(pl, issues=None, *, allow_short=True):
        """
        Warehouse issue: record what actually went out, take it off stock and open a delivery note per order
        (the document the invoice and the dispatch then hang off). `issues`: [{line_id, qty_issued, batch?}].
        """
        if pl.status in ("issued", "cancelled"):
            raise DomainError("This load form is already issued." if pl.status == "issued" else "This load form was cancelled.")
        if pl.status == "open":
            PickListService.send(pl)   # issued straight from an unsent form: it went to the warehouse now
        if issues:
            if pl.status == "picked":
                pl.status = "picking"   # packed is not issued: this is the step that follows it, and the only way out
            PickListService.record_picks(pl, [{"line_id": i["line_id"], "qty_picked": i.get("qty_issued", i.get("qty_picked", 0)), "batch": i.get("batch")} for i in issues])
            pl.refresh_from_db()
        PickListService._fit_batches(pl)
        lines = list(pl.lines.select_related("order_line", "order_line__order", "variant", "batch"))
        issued_total = sum((l.qty_picked for l in lines), Decimal("0"))
        if issued_total <= 0:
            raise DomainError("Nothing is marked as issued yet — enter the quantities the warehouse handed over.")
        short = [l for l in lines if l.qty_picked < l.qty_to_pick]
        if short and not allow_short:
            raise DomainError(f"{len(short)} item(s) are short of the requested quantity.")
        notes = []
        for order in pl.orders.all():
            order_lines = [l for l in lines if l.order_line and l.order_line.order_id == order.pk and l.qty_picked > 0]
            if not order_lines:
                continue
            dn = pl.delivery_notes.filter(order=order).exclude(status="cancelled").first()
            if dn is None:
                # a trip opened BEFORE the issue already holds a delivery note for this order, still unissued —
                # that note is the one that travels, so it carries the issue. A second note here took the goods
                # out of stock twice and billed them twice (TDN-SO-00044, 23–26 Oct 2026).
                dn = DeliveryNote.objects.filter(order=order, stock_out_done=False, status="pending").exclude(trip=None).first()
                if dn is not None:
                    dn.lines.all().delete()
                    for l in order_lines:
                        DeliveryNoteLine.objects.create(delivery_note=dn, order_line=l.order_line, variant=l.variant, batch=l.batch, qty_loaded=l.qty_picked,
                                                        unit_cost=l.variant.avg_cost or l.variant.cost_price)
                    dn.picklist = pl
                    dn.save(update_fields=["picklist", "updated_at"])
            if dn is None:
                dn = DeliveryService.create_note(
                    order, lines=[{"order_line": l.order_line, "qty": l.qty_picked, "batch": l.batch} for l in order_lines], picklist=pl
                )
            DeliveryService._stock_out(dn)
            notes.append(dn)
        pl.status = "issued" if not short else "partially_issued"
        pl.picked_at = pl.picked_at or timezone.now()
        pl.issued_at = timezone.now()
        pl.issued_by = get_current_user()
        pl.picker = pl.picker or get_current_user()
        pl.save(update_fields=["status", "picked_at", "issued_at", "issued_by", "picker", "updated_at"])
        AuditService.log("status.issued", pl, after={"issued": str(issued_total), "short_lines": len(short), "notes": [n.number for n in notes]})
        emit("load_form.issued", picklist=pl)
        return pl

    @staticmethod
    def _fit_batches(pl):
        """What goes out comes from the batches holding it now (StockService.fit_batches): a line's batch emptied since the
        form was made takes the rest from the others, and the form says which batches went."""
        tenant = get_current_tenant()
        lines = list(pl.lines.filter(qty_picked__gt=0).select_related("variant__product", "batch", "order_line__order__customer").order_by("pk"))
        by_order = {}
        for l in lines:
            cust = l.order_line.order.customer if l.order_line_id else None
            by_order.setdefault((cust.min_shelf_life_days if cust else None) or tenant.setting("min_shelf_life_days"), []).append(l)
        for shelf, group in by_order.items():
            for line, pieces in StockService.fit_batches(pl.warehouse, group, lambda l: l.qty_picked, shelf).items():
                moved = line.qty_picked - pieces[0][1]
                old = line.batch.batch_no if line.batch else "none"
                line.batch, line.qty_picked = pieces[0]
                line.qty_to_pick = max(line.qty_to_pick - moved, line.qty_picked)
                line.bin = None
                line.save(update_fields=["batch", "qty_picked", "qty_to_pick", "bin", "updated_at"])
                for b, q in pieces[1:]:
                    PickListLine.objects.create(picklist=pl, order_line=line.order_line, variant=line.variant, batch=b, qty_to_pick=q, qty_picked=q)
                AuditService.log("batches_refitted", pl, after={"item": line.variant.name, "from_batch": old,
                                                                "to": [f"{b.batch_no if b else 'none'} x {q:g}" for b, q in pieces]})

    @staticmethod
    @transaction.atomic
    def complete(pl):
        """Kept for the older "picked" flow (scanner mode): marks the form packed without issuing stock."""
        if pl.status == "cancelled":
            raise DomainError("This load form was cancelled.")
        pl.status = "picked"
        pl.picked_at = timezone.now()
        pl.save(update_fields=["status", "picked_at", "updated_at"])
        AuditService.log("status.picked", pl)
        return pl


class DeliveryService:
    @staticmethod
    @transaction.atomic
    def create_note(order, trip=None, lines=None, sequence=0, picklist=None):
        """Delivery note for an order (all pending qty by default). lines: [{order_line, qty, batch?}]"""
        dn = DeliveryNote.objects.create(number=derived_number(order.number, "delivery_note", DeliveryNote, reclaim=True), trip=trip, order=order, customer=order.customer, warehouse=trip.warehouse if trip else (picklist.warehouse if picklist else order.warehouse), sequence=sequence, picklist=picklist)
        tenant = get_current_tenant()
        if lines:
            for l in lines:
                ol = order.lines.get(pk=l["order_line"] if not hasattr(l["order_line"], "pk") else l["order_line"].pk)
                DeliveryNoteLine.objects.create(delivery_note=dn, order_line=ol, variant=ol.variant, batch=l.get("batch"), qty_loaded=Decimal(l["qty"]), unit_cost=ol.variant.avg_cost or ol.variant.cost_price)
        else:
            for ol in order.lines.select_related("variant"):
                pending = ol.qty_ordered + ol.free_qty - ol.qty_dispatched
                if pending <= 0:
                    continue
                plan, short = StockService.fefo_batches(ol.variant, dn.warehouse, pending, order.customer.min_shelf_life_days or tenant.setting("min_shelf_life_days"))
                for batch, q, bin in plan:
                    DeliveryNoteLine.objects.create(delivery_note=dn, order_line=ol, variant=ol.variant, batch=batch, qty_loaded=q, unit_cost=ol.variant.avg_cost or ol.variant.cost_price)
                if short > 0:
                    if ol.variant.product.is_batch_tracked:
                        raise DomainError(f"Not enough stock of {ol.variant.name} in {dn.warehouse.name} (short {short}).")
                    DeliveryNoteLine.objects.create(delivery_note=dn, order_line=ol, variant=ol.variant, qty_loaded=short, unit_cost=ol.variant.avg_cost or ol.variant.cost_price)
        if not dn.lines.exists():
            dn.delete()
            raise DomainError(f"Order {order.number} has nothing left to deliver.")
        return dn

    @staticmethod
    def _stock_out(dn, van_warehouse=None):
        """Dispatch = stock out (§6.3 point 3). With two-step setting, move to the van warehouse instead."""
        if dn.stock_out_done:
            return
        shelf = dn.order.customer.min_shelf_life_days or get_current_tenant().setting("min_shelf_life_days")
        notes_lines = list(dn.lines.filter(qty_loaded__gt=0).select_related("variant__product", "batch").order_by("pk"))
        for line, pieces in StockService.fit_batches(dn.warehouse, notes_lines, lambda l: l.qty_loaded, shelf).items():
            line.batch, line.qty_loaded = pieces[0]
            line.save(update_fields=["batch", "qty_loaded", "updated_at"])
            for b, q in pieces[1:]:
                DeliveryNoteLine.objects.create(delivery_note=dn, order_line=line.order_line, variant=line.variant, batch=b, qty_loaded=q, unit_cost=line.unit_cost)
        # an order line never leaves the warehouse beyond what was ordered — the guard that would have stopped
        # TDN-SO-00044 going out twice through two delivery notes (27 Oct 2026)
        taking = {}
        for line in dn.lines.select_related("order_line", "variant"):
            ol = line.order_line
            if ol is None or line.qty_loaded <= 0:
                continue
            taking[ol.pk] = taking.get(ol.pk, Decimal("0")) + line.qty_loaded   # two batches of one item are two lines
            if ol.qty_dispatched + taking[ol.pk] > ol.qty_ordered + ol.free_qty:
                raise DomainError(
                    f"{line.variant.name} on {dn.order.number} is already out of the warehouse ({ol.qty_dispatched:g} of "
                    f"{ol.qty_ordered + ol.free_qty:g}) — taking {taking[ol.pk]:g} more would issue it twice.", code="already_issued")
        value = Decimal("0")
        order_lines = {}
        for line in dn.lines.select_related("variant", "variant__product", "batch", "order_line"):
            if line.qty_loaded <= 0:
                continue
            # one copy per order line: each batch's line brought its own, and the last one's count overwrote the others
            # (TDN-SO-00133: 1 + 2 issued, dispatched 2)
            line.order_line = order_lines.setdefault(line.order_line_id, line.order_line)
            # release reservation for this order line first
            for r in StockReservation.objects.filter(order_line=line.order_line, released=False):
                StockService.unreserve(line.variant, r.warehouse, r.qty, r.batch)
                r.released = True
                r.save(update_fields=["released"])
            line.order_line.qty_allocated = Decimal("0")
            mv = StockService.move(warehouse=dn.warehouse, variant=line.variant, qty=-line.qty_loaded, movement_type="sale_dispatch", batch=line.batch, unit_cost=line.unit_cost, reference=dn.trip or dn, note=f"Order {dn.order.number}")
            line.unit_cost = mv.unit_cost
            line.save(update_fields=["unit_cost"])
            value += (line.qty_loaded * mv.unit_cost).quantize(Q4)
            if van_warehouse is not None:
                StockService.move(warehouse=van_warehouse, variant=line.variant, qty=line.qty_loaded, movement_type="transfer_in", batch=line.batch, unit_cost=mv.unit_cost, reference=dn.trip, note=f"Loaded for {dn.order.number}")
            line.order_line.qty_dispatched += line.qty_loaded
            line.order_line.save(update_fields=["qty_dispatched", "qty_allocated"])
        # the warehouse has now taken what it is taking: a line issued SHORT (or skipped) must not keep its
        # reservation — that was the leak behind permanently "spoken for" stock the audit found (21 Oct 2026)
        from apps.sales.models import StockReservation as _SR

        for r in _SR.objects.filter(order_line__order=dn.order, released=False).select_related("order_line__variant"):
            StockService.unreserve(r.order_line.variant, r.warehouse, r.qty, r.batch)
            r.released = True
            r.save(update_fields=["released"])
        dn.order.lines.update(qty_allocated=Decimal("0"))
        dn.stock_out_done = True
        dn.status = "loaded"
        dn.save(update_fields=["stock_out_done", "status", "updated_at"])
        order = dn.order
        if order.status in ("confirmed", "on_hold_stock", "issued"):
            before = order.status
            # Stock has left the warehouse. Until it is loaded on a vehicle that is "issued", not "dispatched".
            order.status = "dispatched" if dn.trip_id else "issued"
            order.dispatched_at = timezone.now()
            order.save(update_fields=["status", "dispatched_at", "updated_at"])
            AuditService.log(f"status.{order.status}", order, before={"status": before}, after={"status": order.status, "trip": dn.trip.number if dn.trip else None})
            emit("order.dispatched" if dn.trip_id else "order.issued", order=order)
        return value

    @staticmethod
    @transaction.atomic
    def deliver(dn, delivered=None, receiver_name="", signature_file=None, photo_file=None, gps=None, cash_collected=Decimal("0"), delivered_at=None, failure_reason="", failed=False, remarks=""):
        """
        delivered: [{line_id, qty_delivered, qty_returned, return_reason, return_condition}] or None = all loaded qty delivered.
        Creates the invoice, and the stock movement if a stop made before 5 Nov 2026 was never issued.
        """
        if dn.status in ("delivered", "partially_delivered", "returned"):
            raise DomainError("Delivery note already completed.")
        tenant = get_current_tenant()
        two_step = tenant.setting("two_step_dispatch") and dn.trip_id
        if not dn.stock_out_done:
            van = dn.trip.vehicle.van_warehouse if (two_step and dn.trip and dn.trip.vehicle_id and hasattr(dn.trip.vehicle, "van_warehouse")) else None
            DeliveryService._stock_out(dn, van)
        if failed:
            dn.status = "failed"
            dn.failure_reason = failure_reason
            dn.remarks = remarks or dn.remarks
            for line in dn.lines.all():
                line.qty_returned = line.qty_loaded
                line.qty_delivered = Decimal("0")
                line.return_reason = line.return_reason or "rejected"
                line.save()
            dn.delivered_at = delivered_at or timezone.now()
            dn.delivered_by = get_current_user()
            dn.save()
            AuditService.log("delivery.failed", dn, after={"reason": failure_reason})
            emit("delivery.failed", delivery_note=dn, reason=failure_reason)
            return dn
        by_id = {str(d["line_id"]): d for d in (delivered or [])}
        total_loaded = total_delivered = Decimal("0")
        order_lines = {}
        for line in dn.lines.select_related("order_line"):
            d = by_id.get(str(line.id))
            if d is None:
                line.qty_delivered = line.qty_loaded
                line.qty_returned = Decimal("0")
            else:
                line.qty_delivered = Decimal(d.get("qty_delivered", line.qty_loaded))
                line.qty_returned = Decimal(d.get("qty_returned", line.qty_loaded - line.qty_delivered))
                line.return_reason = d.get("return_reason", "") or ("rejected" if line.qty_returned else "")
                line.return_condition = d.get("return_condition", "good")
                # a broken or missing unit cannot go back on the shelf, whatever the screen or the phone sent
                if line.return_reason in ("damaged", "shortage"):
                    line.return_condition = "damaged"
            if line.qty_delivered < 0 or line.qty_returned < 0:
                raise DomainError(f"{line.variant.name}: a quantity cannot be negative.")
            if line.qty_delivered + line.qty_returned > line.qty_loaded + Decimal("0.001"):
                raise DomainError(f"Delivered + returned exceeds loaded for {line.variant.name}.")
            if line.qty_delivered + line.qty_returned < line.qty_loaded - Decimal("0.001"):
                # a unit neither delivered nor returned could not be reported as back in stock or wasted
                went_out = format(line.qty_loaded.normalize(), "f")
                raise DomainError(f"{line.variant.name}: {went_out} went out, so delivered and returned must add up to {went_out}.", code="qty_mismatch")
            line.save()
            # one copy per order line: each batch's line brought its own, and the last one's count overwrote the others
            # (TDN-SO-00149: NO. 4.19 delivered 6 on two batches, the order said 2, and it stayed Partially delivered)
            ol = order_lines.setdefault(line.order_line_id, line.order_line)
            ol.qty_delivered += line.qty_delivered
            ol.save(update_fields=["qty_delivered"])
            total_loaded += line.qty_loaded
            total_delivered += line.qty_delivered
        dn.status = "delivered" if total_delivered >= total_loaded else ("partially_delivered" if total_delivered > 0 else "returned")
        dn.delivered_at = delivered_at or timezone.now()
        dn.delivered_by = get_current_user()
        dn.receiver_name = receiver_name
        dn.remarks = remarks or dn.remarks
        if signature_file is not None:
            dn.signature_file = signature_file
        if photo_file is not None:
            dn.photo_file = photo_file
        if gps:
            dn.gps_lat, dn.gps_lng = gps.get("lat"), gps.get("lng")
            accuracy = gps.get("accuracy_m")
            dn.gps_accuracy_m = float(accuracy) if accuracy not in (None, "") else None
        dn.cash_collected = Decimal(cash_collected or 0)
        dn.collected_by = get_current_user() if dn.cash_collected else None
        dn.save()
        from apps.sales.services import InvoiceService, OrderService

        invoice = None
        if total_delivered > 0:
            had_invoice = getattr(dn, "invoice", None) is not None
            # goods delivered with no invoice yet are billed as a DRAFT for review, never posted behind anyone's
            # back (owner, 27 Oct 2026: "i want to see or edit before create invoice … same for other steps")
            invoice = InvoiceService.from_delivery_note(dn, post=False)
            if not had_invoice:
                from apps.core.notifications import notify_permission

                notify_permission("sales.change_invoice", "invoice.draft_at_delivery",
                                  f"Invoice {invoice.number} drafted at delivery — review and post",
                                  f"{dn.customer.name} · {dn.number} was delivered without an invoice. Check the draft, then Post to ledger.",
                                  {"invoice_id": str(invoice.id)})
            if not two_step:   # with van stock the goods are on the van's own books until its trip closes
                invoice = DeliveryService.settle_returns(dn, invoice)
        OrderService.refresh_delivery_status(dn.order)
        if dn.cash_collected > 0 and invoice is not None:
            from apps.accounting.services import PaymentService

            # cash against a POSTED invoice settles it now; against a draft it waits on the customer's account and
            # is applied the moment the invoice is posted (InvoiceService.post) — a draft is not on the books
            allocs = ([{"document_type": "sales_invoice", "document_id": str(invoice.pk), "amount": min(dn.cash_collected, invoice.balance)}]
                      if invoice.status != "draft" and invoice.balance > 0 else [])
            PaymentService.record(direction="in", partner_type="customer", partner_id=str(dn.customer_id), partner_name=dn.customer.name, amount=dn.cash_collected, method="cash", allocations=allocs, auto_allocate=invoice.status != "draft", reference=f"COD {dn.number}", trip=dn.trip, delivery_note=dn)
            if dn.trip_id:
                dn.trip.cash_collected = (dn.trip.cash_collected or 0) + dn.cash_collected
                dn.trip.save(update_fields=["cash_collected"])
        AuditService.log("delivery.completed", dn, after={"status": dn.status, "delivered": str(total_delivered), "invoice": invoice.number if invoice else None})
        emit("delivery.completed", delivery_note=dn, invoice=invoice)
        if dn.trip_id:
            TripService.refresh_status(dn.trip)
        return dn

    @staticmethod
    def settle_returns(dn, invoice=None):
        """
        Goods that came back from a part-delivered stop (owner, 5 Nov 2026: "how to handle when return during delivery? …
        update customer ledger and update account and chart of account … report if item going back to stock or wastage").

        All of it happens as the stop is saved, because a trip's close cannot be relied on to do it: TDN had closed 3 of
        its 37 trips. A good unit goes back on the shelf. The customer is credited for what a posted invoice billed and
        they did not keep (TDN-SO-00149 sat Partially delivered until someone remembered, and billed the unit again). A
        draft comes down to what was delivered. A damaged unit is expensed once: by the credit note if an invoice billed
        it, written off here if none did. Returns the invoice as it now stands.
        """
        from apps.sales.services import CreditNoteService, InvoiceService

        lines = [l for l in dn.lines.select_related("order_line", "variant", "variant__product", "batch") if l.qty_returned > 0 and not l.return_settled]
        if not lines:
            return invoice
        dn.refresh_from_db(fields=["delivered_at"])   # it may have been given as text
        on = timezone.localtime(dn.delivered_at).date()
        if invoice is not None and invoice.status == "draft":
            InvoiceService.fit_draft_to_stop(invoice, dn)
        to_credit = CreditNoteService.billed_not_kept(invoice, dn)
        credit, write_off, order_lines = [], [], {}
        for l in lines:
            credited = min(l.qty_returned, max(to_credit.get(l.variant_id, Decimal("0")), Decimal("0")))
            if credited > 0:
                to_credit[l.variant_id] -= credited
                credit.append({"variant": l.variant, "batch": l.batch, "qty": credited, "reason": l.get_return_reason_display(),
                               "condition": "damaged" if l.return_condition == "damaged" else "good",
                               "restock": l.return_condition != "damaged", "stock_back": l.return_condition != "damaged"})
            if l.return_condition != "damaged":
                StockService.move(warehouse=dn.warehouse, variant=l.variant, qty=l.qty_returned, movement_type="delivery_return", batch=l.batch,
                                  unit_cost=l.unit_cost, reference=dn, note=f"Returned at delivery {dn.number}", occurred_at=dn.delivered_at)
            elif l.qty_returned > credited:
                write_off.append({"variant": l.variant, "batch": l.batch, "qty": l.qty_returned - credited, "unit_cost": l.unit_cost, "note": f"{dn.number} {l.return_reason}"})
            l.return_settled = True
            l.save(update_fields=["return_settled"])
            ol = order_lines.setdefault(l.order_line_id, l.order_line)
            ol.qty_returned += l.qty_returned
            ol.save(update_fields=["qty_returned"])
        if write_off:
            dn.return_adjustment = DeliveryService.write_off_damaged(dn, write_off, on)
            dn.save(update_fields=["return_adjustment", "updated_at"])
        if credit:
            cn = CreditNoteService.create(customer=dn.customer, invoice=invoice, warehouse=dn.warehouse, date=on, reason=f"Returned at delivery {dn.number}",
                                          lines=credit, delivery_note=dn)
            CreditNoteService.post(cn)
            invoice.refresh_from_db()
            from apps.core.notifications import notify_permission

            notify_permission("sales.change_invoice", "credit_note.at_delivery", f"Credit note {cn.number} posted at delivery",
                              f"{dn.customer.name} · {dn.number} · {cn.total:,.2f}", {"invoice_id": str(invoice.id)})
        AuditService.log("delivery.returns_settled", dn, after={"credit_note": cn.number if credit else None, "units": str(sum((l.qty_returned for l in lines), Decimal("0")))})
        return invoice

    @staticmethod
    def write_off_damaged(dn, lines, on=None):
        """Damaged units no invoice billed: Dr the loss, Cr Stock in Hand. They left the shelf when issued and are not stock
        anywhere, not even a quarantine warehouse (owner, 8 Nov 2026), so no stock moves here."""
        from apps.inventory.services import AdjustmentService

        reason = TripService.damage_reason()
        adj = AdjustmentService.create(warehouse=dn.warehouse, type="decrease", reason=reason, lines=lines, date=on, notes=f"Returned damaged at delivery {dn.number}",
                                       source=dn, skip_stock=True, auto_approve=True)
        return adj

    @staticmethod
    def return_preview(dn, delivered):
        """What saving the stop with these quantities would credit, by the sums the credit note itself will make."""
        from apps.sales.services import CreditNoteService

        def num(v):
            try:
                return Decimal(str(v))
            except ArithmeticError:
                return Decimal("0")

        by_id = {str(d.get("line_id")): d for d in delivered or []}
        kept, back = {}, {}
        for l in dn.lines.all():
            d = by_id.get(str(l.id), {})
            returned = max(num(d.get("qty_returned") or 0), Decimal("0"))
            kept[l.variant_id] = kept.get(l.variant_id, Decimal("0")) + max(l.qty_loaded - returned, Decimal("0"))
            back[l.variant_id] = back.get(l.variant_id, Decimal("0")) + returned
        invoice = getattr(dn, "invoice", None)
        credit = Decimal("0")
        if invoice is not None and any(back.values()) and any(kept.values()):
            left = CreditNoteService.billed_not_kept(invoice, dn, kept=kept)
            credit = CreditNoteService.credit_for(invoice, {vid: min(q, max(left.get(vid, Decimal("0")), Decimal("0"))) for vid, q in back.items()})
        billed = invoice is not None and invoice.status not in ("draft", "cancelled")
        return {"credit": str(credit), "invoice_number": invoice.number if invoice is not None else "", "invoice_status": invoice.status if invoice is not None else "",
                "balance_after": str(max(invoice.balance - credit, Decimal("0"))) if billed else None}

    @staticmethod
    @transaction.atomic
    def undo_return(dn, reason=""):
        """
        A return saved on a stop by mistake: the customer kept those units after all. The stop reads Delivered, the credit
        note is cancelled and what it did comes off the books, the goods leave the shelf again. Refused once the credit
        has been used or refunded. A wrong quantity or condition is put right by undoing, then taking a return.
        """
        from apps.accounting.services import PostingService
        from apps.inventory.models import StockMovement
        from apps.sales.services import CreditNoteService, InvoiceService, OrderService

        dn = DeliveryNote.objects.select_for_update(of=("self",)).get(pk=dn.pk)
        lines = [l for l in dn.lines.select_related("order_line", "variant", "batch") if l.return_settled and l.qty_returned > 0]
        if dn.status != "partially_delivered" or not lines:
            raise DomainError(f"{dn.number} has no return to undo.", code="no_return")
        invoice = getattr(dn, "invoice", None)
        cn = dn.return_credit_notes.filter(status="posted").first()
        if cn is None and invoice is not None and invoice.status not in ("draft", "cancelled"):
            raise DomainError(f"{invoice.number} went to the books without these units — reopen it first, then undo the return.", code="invoice_posted")
        if cn is not None:
            CreditNoteService.undo_stop_credit(cn, reason)
        for mv in StockMovement.objects.filter(reference_type="DeliveryNote", reference_id=str(dn.pk), movement_type="delivery_return").select_related("warehouse", "variant", "batch"):
            StockService.move(warehouse=mv.warehouse, variant=mv.variant, qty=-mv.qty, movement_type="return_cancelled", batch=mv.batch, unit_cost=mv.unit_cost,
                              reference=dn, note=f"Return undone{': ' + reason if reason else ''}")
        adj = dn.return_adjustment
        if adj is not None:
            if adj.journal_id:
                PostingService.reverse(adj.journal, memo=f"Return undone on {dn.number}")
            adj.status = "cancelled"
            adj.save(update_fields=["status", "updated_at"])
            AuditService.log("status.cancelled", adj, after={"reason": f"Return undone on {dn.number}"})
        order_lines, units = {}, Decimal("0")
        for l in lines:
            units += l.qty_returned
            ol = order_lines.setdefault(l.order_line_id, l.order_line)
            ol.qty_delivered += l.qty_returned
            ol.qty_returned = max(ol.qty_returned - l.qty_returned, Decimal("0"))
            ol.save(update_fields=["qty_delivered", "qty_returned"])
            l.qty_delivered += l.qty_returned
            l.qty_returned = Decimal("0")
            l.return_reason, l.return_condition, l.return_settled = "", "good", False
            l.save(update_fields=["qty_delivered", "qty_returned", "return_reason", "return_condition", "return_settled"])
        dn.status = "delivered"
        dn.return_adjustment = None
        dn.save(update_fields=["status", "return_adjustment", "updated_at"])
        if invoice is not None and invoice.status == "draft":
            InvoiceService.fit_draft_to_stop(invoice, dn)
        OrderService.refresh_delivery_status(dn.order)
        AuditService.log("delivery.return_undone", dn, after={"units": str(units), "credit_note": cn.number if cn is not None else None, "reason": reason})
        return dn

    @staticmethod
    def bring_home(dn):
        """A refused stop credited by a return while its trip is open: the goods are on the shelf now, not when (or if) the
        trip closes. Its close passes over them."""
        trip = dn.trip
        if trip is None or trip.status == "closed" or get_current_tenant().setting("two_step_dispatch"):
            return
        for l in dn.lines.select_related("order_line", "variant", "variant__product", "batch"):
            if l.qty_returned <= 0 or l.return_settled or l.return_condition == "damaged":
                continue
            StockService.move(warehouse=trip.warehouse, variant=l.variant, qty=l.qty_returned, movement_type="delivery_return", batch=l.batch, unit_cost=l.unit_cost,
                              reference=trip, note=f"Returned from {dn.number}")
            l.order_line.qty_dispatched -= l.qty_returned
            l.order_line.save(update_fields=["qty_dispatched"])
            l.return_settled = True
            l.save(update_fields=["return_settled"])

    @staticmethod
    def settled_returns(**order_filters):
        """Units per variant settled at the stop. They stay in an order line's dispatched count, so what is left to issue
        does not offer them again: anything reading "out and not yet delivered" takes them off."""
        return {r["variant_id"]: r["q"] or Decimal("0") for r in DeliveryNoteLine.objects.filter(return_settled=True, **{f"order_line__order__{k}": v for k, v in order_filters.items()})
                .values("variant_id").annotate(q=Sum("qty_returned"))}

    @staticmethod
    def failed_note(order):
        """The order's delivery that did not reach the customer and is not decided yet: sent again, or the order closed."""
        if order.status not in ("issued", "dispatched", "confirmed"):
            return None
        return order.delivery_notes.filter(status__in=("failed", "returned")).order_by("-created_at").first()

    @staticmethod
    def refuse_second_delivery(order):
        """A failed delivery is sent again as it is — its goods, its invoice. A second delivery note beside it would
        bill the order twice (TDN-SO-00044 had two invoices, 28 Oct 2026)."""
        dn = DeliveryService.failed_note(order)
        if dn is not None:
            raise DomainError(f"{dn.number} did not reach the customer — press Deliver again on {order.number} to send the same goods "
                              "and invoice out again, or Customer rejected if the customer does not want them.", code="failed_delivery")

    @staticmethod
    @transaction.atomic
    def deliver_again(dn, reason=""):
        """
        The stop failed but the customer still wants the goods — shop closed, nobody to receive them (owner, 30 Oct
        2026: "what if out for delivery and not received by customer?"). The SAME delivery note goes out again with
        the same invoice, paid or not: one order, one invoice. Goods the closed trip put back on the shelf leave it
        again; goods still with the trip stay issued, and its close must not restock them.
        """
        from apps.sales.services import OrderService

        order = dn.order
        if dn.status not in ("failed", "returned"):
            raise DomainError(f"{dn.number} is {dn.get_status_display().lower()} — only a delivery that did not reach the customer goes out again.", code="not_failed")
        if order.status not in ("issued", "dispatched", "confirmed"):
            raise DomainError(f"{order.number} is {order.get_status_display().lower()} — it does not go out again.", code="closed")
        if order.delivery_notes.exclude(pk=dn.pk).exclude(status__in=("cancelled", "failed", "returned")).exists():
            raise DomainError(f"{order.number} already has another delivery on its way.", code="duplicate")
        trip = dn.trip
        home = trip is not None and trip.status == "closed"
        if home and get_current_tenant().setting("two_step_dispatch"):
            raise DomainError("Sending a delivery out again is not supported with van stock (two-step dispatch).")
        lines = list(dn.lines.select_related("variant", "batch", "order_line"))
        if any(l.qty_returned > 0 and l.return_condition == "damaged" for l in lines):
            raise DomainError(f"Part of {dn.number} came back damaged and cannot go out again as it was — use Customer rejected, "
                              "and a new order for the replacement.", code="damaged")
        for l in lines:
            if (home or l.return_settled) and l.qty_returned > 0:
                # the trip's close, or a return, put these back on the shelf: they leave it again, at the cost they went out at
                StockService.move(warehouse=trip.warehouse, variant=l.variant, qty=-l.qty_returned, movement_type="sale_dispatch", batch=l.batch,
                                  unit_cost=l.unit_cost, reference=dn, note=f"{dn.number} out again" + (f": {reason}" if reason else ""))
                l.order_line.qty_dispatched += l.qty_returned
                l.order_line.save(update_fields=["qty_dispatched"])
            l.qty_returned = l.qty_delivered = Decimal("0")
            l.return_reason, l.return_condition, l.return_settled = "", "good", False
            l.save(update_fields=["qty_returned", "qty_delivered", "return_reason", "return_condition", "return_settled"])
        out = trip is not None and not home and trip.status in ("out_for_delivery", "completed")
        dn.status = "dispatched" if out else "loaded"
        dn.failure_reason = ""
        dn.delivered_at = dn.delivered_by = None
        if home:
            dn.trip = None   # the closed trip let it go; the next trip (Add to trip) takes this same note
        dn.save()
        if out and trip.status == "completed":
            trip.status = "out_for_delivery"   # a stop is open again
            trip.save(update_fields=["status", "updated_at"])
        if order.status == "confirmed":   # the trip's close had sent it back to the warehouse
            OrderService._set(order, "dispatched" if out else "issued", "deliver_again", {"delivery_note": dn.number, "reason": reason})
        AuditService.log("delivery.deliver_again", dn, after={"reason": reason, "out_of_stock_again": home})
        return dn


class TripService:
    DISPATCHABLE = ("confirmed", "issued", "invoiced", "partially_delivered")

    @staticmethod
    def _number_for(order):
        """
        A trip is numbered after its first order. A trip still waiting to leave that was named after this order but no
        longer carries it (the order was edited off it) gives the number back and takes that of the order it does
        carry, so neither reads -2: TDN-TRIP-00149-2, 5 Nov 2026.
        """
        holder = DeliveryTrip.objects.filter(number=derived_base(order.number, "trip"), status__in=("planned", "loading")).first()
        if holder is not None and not holder.delivery_notes.filter(order=order).exclude(status="cancelled").exists():
            first = holder.delivery_notes.exclude(status="cancelled").select_related("order").order_by("sequence", "created_at").first()
            if first is not None and not DeliveryTrip.all_objects.filter(tenant_id=holder.tenant_id, number=derived_base(first.order.number, "trip")).exists():
                renumber(holder, derived_base(first.order.number, "trip"))
        return derived_number(order.number, "trip", DeliveryTrip, reclaim=True)

    @staticmethod
    @transaction.atomic
    def create(*, warehouse, date=None, driver=None, vehicle=None, rep=None, delivery_rep=None, loader=None, helper=None, route=None, orders=(), notes="", picklist=None):
        trip = DeliveryTrip.objects.create(number=TripService._number_for(orders[0]) if orders else derived_number(None, "trip", DeliveryTrip), warehouse=warehouse, date=date or timezone.localdate(), driver=driver, vehicle=vehicle or (driver.default_vehicle if driver else None), rep=rep, delivery_rep=delivery_rep, loader=loader, helper=helper, route=route, notes=notes, picklist=picklist)
        if orders:
            TripService.assign_orders(trip, orders)
        AuditService.log("created", trip, after={"driver": driver.name if driver else None, "vehicle": vehicle.reg_no if vehicle else None})
        emit("delivery.assigned", trip=trip)
        return trip

    @staticmethod
    @transaction.atomic
    def from_picklist(pl, *, driver=None, vehicle=None, rep=None, delivery_rep=None, loader=None, helper=None, route=None, date=None, notes=""):
        """Warehouse finished packing a loader form → open the dispatch (trip) for its orders."""
        if pl.status not in ("issued", "partially_issued"):
            raise DomainError("The warehouse has to issue the load form before it can be dispatched.")
        # `invoiced` belongs here too: the invoice-before-dispatch flow invoices the issued goods and then
        # still has to put them on a vehicle. Leaving it out made an invoiced order impossible to dispatch.
        orders = [o for o in pl.orders.all() if o.status in TripService.DISPATCHABLE]
        if not orders:
            raise DomainError("The orders on this loader form are already dispatched or cancelled.")
        trip = TripService.create(warehouse=pl.warehouse, date=date, driver=driver, vehicle=vehicle, rep=rep, delivery_rep=delivery_rep, loader=loader or pl.picker, helper=helper, route=route, orders=orders, notes=notes or f"Packed on {pl.number}", picklist=pl)
        AuditService.log("dispatch.created", trip, after={"picklist": pl.number, "orders": [o.number for o in orders]})
        return trip

    @staticmethod
    @transaction.atomic
    def assign_orders(trip, orders):
        if trip.status not in ("planned", "loading"):
            raise DomainError("Orders can only be assigned before the trip starts.")
        seq = trip.delivery_notes.count()
        for o in orders:
            if o.status not in TripService.DISPATCHABLE:
                raise DomainError(f"Order {o.number} is not ready to dispatch (status {o.get_status_display()}).")
            if o.warehouse_id != trip.warehouse_id:
                raise DomainError(f"Order {o.number} belongs to another warehouse.")
            if o.trip_id and o.trip_id != trip.pk:
                raise DomainError(f"Order {o.number} is already on trip {o.trip.number}.")
            DeliveryService.refuse_second_delivery(o)
            if trip.delivery_notes.filter(order=o).exists():
                continue
            # no load form, no issue, no dispatch (owner, 5 Nov 2026): a trip only takes the delivery note the warehouse
            # issued. It used to make its own, and starting the trip took the stock with no load form at all.
            issued = DeliveryNote.awaiting_trip().filter(order=o).order_by("created_at").first()
            if issued is None:
                raise DomainError(f"{o.number} has not been issued yet — issue its load form first.", code="not_issued")
            seq += 1
            issued.trip = trip
            issued.sequence = seq
            issued.save(update_fields=["trip", "sequence", "updated_at"])
            o.trip = trip
            o.save(update_fields=["trip"])
        trip.stops_count = trip.delivery_notes.count()
        trip.save(update_fields=["stops_count", "updated_at"])
        AuditService.log("trip.orders_assigned", trip, after={"orders": [o.number for o in orders]})
        return trip

    @staticmethod
    @transaction.atomic
    def _let_go(notes):
        """
        A trip releasing its stops. A note the warehouse already issued (its stock is out, maybe an invoice hangs
        off it) stays — off the trip, back at "issued" — so the next trip picks it up and nothing is issued twice
        (review, 27 Oct 2026). Only a note that never took stock out is deleted.
        """
        from apps.sales.services import OrderService

        for dn in list(notes.select_related("order")):
            if not dn.stock_out_done:
                dn.delete()
                continue
            dn.trip, dn.sequence = None, 0
            dn.save(update_fields=["trip", "sequence", "updated_at"])
            if dn.order.status == "dispatched":
                OrderService._set(dn.order, "issued", "taken_off_trip")

    @staticmethod
    @transaction.atomic
    def remove_order(trip, order):
        if trip.status not in ("planned", "loading"):
            raise DomainError("Cannot remove orders after the trip started.")
        TripService._let_go(trip.delivery_notes.filter(order=order))
        order.trip = None
        order.save(update_fields=["trip"])
        trip.stops_count = trip.delivery_notes.count()
        trip.save(update_fields=["stops_count"])
        return trip

    @staticmethod
    @transaction.atomic
    def reorder_stops(trip, ordered_note_ids):
        for i, nid in enumerate(ordered_note_ids, start=1):
            trip.delivery_notes.filter(pk=nid).update(sequence=i)
        return trip

    @staticmethod
    @transaction.atomic
    def load(trip, loaded=None):
        """Loading step: confirm/adjust loaded quantities. loaded: [{line_id, qty_loaded, batch?}]"""
        if trip.status not in ("planned", "loading"):
            raise DomainError("Trip is not in loading state.")
        from apps.inventory.models import Batch

        for l in loaded or []:
            line = DeliveryNoteLine.objects.get(pk=l["line_id"], delivery_note__trip=trip)
            line.qty_loaded = Decimal(l["qty_loaded"])
            if l.get("batch"):
                line.batch = Batch.objects.filter(pk=l["batch"]).first()
            line.save()
        trip.status = "loading"
        trip.save(update_fields=["status", "updated_at"])
        return trip

    @staticmethod
    @transaction.atomic
    def start(trip, odometer_start=None):
        """Trip start = the vehicle leaves with what the warehouse issued. It never takes stock of its own (owner, 5 Nov 2026)."""
        if trip.status not in ("planned", "loading"):
            raise DomainError("Trip already started.")
        if not trip.delivery_notes.exists():
            raise DomainError("Assign at least one order before starting the trip.")
        if trip.driver_id is None:
            raise DomainError("Assign a driver before starting the trip.")
        notes = list(trip.delivery_notes.select_related("order", "warehouse").prefetch_related("lines").exclude(status="cancelled").order_by("sequence"))
        unissued = [dn.order.number for dn in notes if not dn.stock_out_done]
        if unissued:
            raise DomainError(f"{', '.join(unissued)} not issued yet — issue the load form first.", code="not_issued")
        tenant = get_current_tenant()
        van = None
        if tenant.setting("two_step_dispatch") and trip.vehicle_id:
            from apps.inventory.models import Warehouse

            van = getattr(trip.vehicle, "van_warehouse", None)
            if van is None:
                van = Warehouse.objects.create(code=f"VAN-{trip.vehicle.reg_no}"[:20], name=f"Van {trip.vehicle.reg_no}", type="van", vehicle=trip.vehicle)
        value = Decimal("0")
        for dn in notes:
            value += DeliveryService._stock_out(dn, van) or Decimal("0")
            if dn.status in ("pending", "loaded"):
                dn.status = "dispatched"
                dn.save(update_fields=["status", "updated_at"])
            if dn.order.status == "issued":
                dn.order.status = "dispatched"
                dn.order.save(update_fields=["status", "updated_at"])
        trip.status = "out_for_delivery"
        trip.started_at = timezone.now()
        trip.odometer_start = odometer_start
        # what is on the vehicle, at cost. Start used to count only the stock it took itself, so issued goods read 0.
        trip.loaded_value = sum(((l.qty_loaded * l.unit_cost).quantize(Q4) for dn in notes for l in dn.lines.all()), Decimal("0"))
        trip.save(update_fields=["status", "started_at", "odometer_start", "loaded_value", "updated_at"])
        if van is not None:
            from apps.accounting.services import PostingService

            PostingService.post_trip_dispatch(trip, value)
        AuditService.log("status.started", trip, after={"status": "out_for_delivery", "loaded_value": str(trip.loaded_value), "stops": trip.stops_count})
        emit("trip.started", trip=trip)
        return trip

    @staticmethod
    def refresh_status(trip):
        notes = trip.delivery_notes.all()
        if notes.exists() and not notes.filter(status__in=["pending", "loaded", "dispatched"]).exists() and trip.status == "out_for_delivery":
            trip.status = "completed"
            trip.completed_at = timezone.now()
            trip.save(update_fields=["status", "completed_at", "updated_at"])

    @staticmethod
    def reconciliation(trip):
        loaded = delivered = returned = damaged = Decimal("0")
        for l in DeliveryNoteLine.objects.filter(delivery_note__trip=trip):
            loaded += l.qty_loaded
            delivered += l.qty_delivered
            if l.return_condition == "damaged":
                damaged += l.qty_returned
            else:
                returned += l.qty_returned
        from apps.accounting.models import Payment

        receipts = Payment.objects.filter(trip=trip, status="posted", direction="in").aggregate(s=__import__("django.db.models", fromlist=["Sum"]).Sum("amount"))["s"] or Decimal("0")
        undelivered = loaded - delivered - returned - damaged
        return {"loaded": str(loaded), "delivered": str(delivered), "returned_good": str(returned), "returned_damaged": str(damaged), "unaccounted": str(undelivered), "cash_collected": str(trip.cash_collected), "receipts": str(receipts), "cash_mismatch": str(trip.cash_collected - receipts), "ok": undelivered == 0 and abs(trip.cash_collected - receipts) < Decimal("0.01")}

    @staticmethod
    def damage_reason():
        """Goods that come back damaged post to Damaged Goods, the account a credit note uses for them (owner, 6 Nov 2026).
        TDN's Transit damage reason posts to an account it has renamed Packing Material."""
        from apps.inventory.models import ReasonCode

        return (ReasonCode.objects.filter(category="damage", is_active=True).first() or ReasonCode.objects.filter(category="transit_damage").first()
                or ReasonCode.objects.first())

    @staticmethod
    def _expensed_by_returns(dn):
        from apps.sales.models import CreditNoteLine

        out = {}
        for l in CreditNoteLine.objects.filter(credit_note__invoice__delivery_note=dn, credit_note__status="posted", stock_back=False).exclude(restock=True, condition="good"):
            out[l.variant_id] = out.get(l.variant_id, Decimal("0")) + l.qty
        return out

    @staticmethod
    @transaction.atomic
    def close(trip, odometer_end=None, fuel_amount=None, expense_amount=None, override=False, damage_reason=None):
        """Close: returns go back to the warehouse, damaged via adjustment, cash reconciled (§6.3 points 6-7)."""
        if trip.status not in ("out_for_delivery", "completed"):
            raise DomainError("Trip must be out for delivery or completed to close.")
        pending = trip.delivery_notes.filter(status__in=["pending", "loaded", "dispatched"])
        if pending.exists():
            if not override:
                raise DomainError(f"{pending.count()} stop(s) not yet marked delivered/failed.", errors={"pending": [d.number for d in pending]})
            for dn in pending:
                DeliveryService.deliver(dn, failed=True, failure_reason="Not delivered (trip closed)")
        rec = TripService.reconciliation(trip)
        if not rec["ok"] and not override:
            raise DomainError("Trip does not reconcile.", errors={"reconciliation": rec})
        tenant = get_current_tenant()
        two_step = tenant.setting("two_step_dispatch") and trip.vehicle_id and getattr(trip.vehicle, "van_warehouse", None)
        source_wh = trip.vehicle.van_warehouse if two_step else None
        damaged_lines, returned_value, expensed = [], Decimal("0"), {}
        for l in DeliveryNoteLine.objects.filter(delivery_note__trip=trip).select_related("variant", "variant__product", "batch", "delivery_note"):
            if l.qty_returned <= 0 or l.return_settled:
                continue   # the stop settled these when it was saved
            if l.return_condition == "damaged":
                # a return already taken on them has expensed them; a second write-off would count one lost unit twice
                by_return = expensed.setdefault(l.delivery_note_id, TripService._expensed_by_returns(l.delivery_note))
                taken = min(l.qty_returned, by_return.get(l.variant_id, Decimal("0")))
                by_return[l.variant_id] = by_return.get(l.variant_id, Decimal("0")) - taken
                if l.qty_returned > taken:
                    damaged_lines.append({"variant": l.variant, "batch": l.batch, "qty": l.qty_returned - taken, "unit_cost": l.unit_cost, "note": f"{l.delivery_note.number} {l.return_reason}"})
                if source_wh is not None:
                    StockService.move(warehouse=source_wh, variant=l.variant, qty=-l.qty_returned, movement_type="transfer_out", batch=l.batch, unit_cost=l.unit_cost, reference=trip, note="Damaged in transit")
                continue
            if source_wh is not None:
                StockService.move(warehouse=source_wh, variant=l.variant, qty=-l.qty_returned, movement_type="transfer_out", batch=l.batch, unit_cost=l.unit_cost, reference=trip, note="Return to warehouse")
            StockService.move(warehouse=trip.warehouse, variant=l.variant, qty=l.qty_returned, movement_type="delivery_return", batch=l.batch, unit_cost=l.unit_cost, reference=trip, note=f"Returned from {l.delivery_note.number}")
            l.order_line.qty_dispatched -= l.qty_returned
            l.order_line.save(update_fields=["qty_dispatched"])
            returned_value += (l.qty_returned * l.unit_cost).quantize(Q4)
        if two_step:
            # whatever was delivered leaves the van (COGS already posted from in-transit account on invoice)
            for l in DeliveryNoteLine.objects.filter(delivery_note__trip=trip, qty_delivered__gt=0).select_related("variant", "batch"):
                StockService.move(warehouse=source_wh, variant=l.variant, qty=-l.qty_delivered, movement_type="sale_dispatch", batch=l.batch, unit_cost=l.unit_cost, reference=trip, note="Delivered")
            from apps.accounting.services import PostingService

            PostingService.post_trip_return(trip, returned_value)
        if damaged_lines:
            from apps.inventory.services import AdjustmentService

            reason = damage_reason or TripService.damage_reason()
            adj = AdjustmentService.create(warehouse=trip.warehouse, type="decrease", reason=reason, lines=damaged_lines, notes=f"Transit damage on trip {trip.number}", source=trip, skip_stock=True, auto_approve=True)
            rec["damage_adjustment"] = adj.number
        for dn in trip.delivery_notes.select_related("order"):
            from apps.sales.services import OrderService

            OrderService.refresh_delivery_status(dn.order)
            o = dn.order
            if o.status == "dispatched" and o.lines.filter(qty_dispatched__gt=0).count() == 0:
                # everything came back
                OrderService._set(o, "confirmed", "returned_to_warehouse")
                o.trip = None
                o.save(update_fields=["trip"])
        trip.status = "closed"
        trip.closed_at = timezone.now()
        trip.closed_by = get_current_user()
        trip.odometer_end = odometer_end
        trip.fuel_amount = fuel_amount or 0
        trip.expense_amount = expense_amount or 0
        trip.reconciliation = rec
        trip.save()
        if trip.vehicle_id:
            VehicleLog.objects.create(vehicle=trip.vehicle, trip=trip, date=trip.date, odometer_start=trip.odometer_start, odometer_end=odometer_end, fuel_amount=trip.fuel_amount, expense=trip.expense_amount)
        AuditService.log("status.closed", trip, after=rec)
        emit("trip.closed", trip=trip)
        return trip

    @staticmethod
    @transaction.atomic
    def cancel(trip, reason=""):
        if trip.status not in ("planned", "loading"):
            raise DomainError("Started trips cannot be cancelled; close them instead.")
        for dn in trip.delivery_notes.select_related("order"):
            dn.order.trip = None
            dn.order.save(update_fields=["trip"])
        TripService._let_go(trip.delivery_notes.all())
        trip.status = "cancelled"
        trip.notes = (trip.notes + f"\nCancelled: {reason}").strip()
        trip.save(update_fields=["status", "notes", "updated_at"])
        AuditService.log("status.cancelled", trip, after={"reason": reason})
        return trip
