from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.audit import AuditService
from apps.core.events import emit
from apps.core.exceptions import DomainError
from apps.core.numbering import derived_number, next_number
from apps.core.tenancy import get_current_tenant, get_current_user
from apps.inventory.services import StockService
from apps.sales.models import SalesOrder, StockReservation

from .models import DeliveryNote, DeliveryNoteLine, DeliveryTrip, PickList, PickListLine, VehicleLog

Q4 = Decimal("0.0001")


class PickListService:
    @staticmethod
    @transaction.atomic
    def create(*, warehouse, orders, notes=""):
        orders = [o for o in orders if o.status == "confirmed" and o.warehouse_id == warehouse.pk]
        if not orders:
            raise DomainError("Select confirmed orders from this warehouse.")
        pl = PickList.objects.create(number=derived_number(orders[0].number, "pick_list", PickList), warehouse=warehouse, notes=notes)
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
        AuditService.log("created", pl, after={"orders": [o.number for o in orders]})
        return pl

    @staticmethod
    @transaction.atomic
    def record_picks(pl, picks):
        """picks: [{line_id, qty_picked, batch?, scanned?}]"""
        if pl.status == "picked":
            raise DomainError("Pick list already completed.")
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
        if issues:
            PickListService.record_picks(pl, [{"line_id": i["line_id"], "qty_picked": i.get("qty_issued", i.get("qty_picked", 0)), "batch": i.get("batch")} for i in issues])
            pl.refresh_from_db()
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
            dn = pl.delivery_notes.filter(order=order).first()
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
    @transaction.atomic
    def complete(pl):
        """Kept for the older "picked" flow (scanner mode): marks the form packed without issuing stock."""
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
        dn = DeliveryNote.objects.create(number=derived_number(order.number, "delivery_note", DeliveryNote), trip=trip, order=order, customer=order.customer, warehouse=trip.warehouse if trip else (picklist.warehouse if picklist else order.warehouse), sequence=sequence, picklist=picklist)
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
        value = Decimal("0")
        for line in dn.lines.select_related("variant", "variant__product", "batch", "order_line"):
            if line.qty_loaded <= 0:
                continue
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
        Creates the stock movement if the order was never dispatched (mark-delivered path) and the invoice.
        """
        if dn.status in ("delivered", "returned"):
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
            if line.qty_delivered + line.qty_returned > line.qty_loaded + Decimal("0.001"):
                raise DomainError(f"Delivered + returned exceeds loaded for {line.variant.name}.")
            line.save()
            ol = line.order_line
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

        OrderService.refresh_delivery_status(dn.order)
        invoice = None
        if total_delivered > 0:
            invoice = InvoiceService.from_delivery_note(dn)
        if dn.cash_collected > 0 and invoice is not None:
            from apps.accounting.services import PaymentService

            PaymentService.record(direction="in", partner_type="customer", partner_id=str(dn.customer_id), partner_name=dn.customer.name, amount=dn.cash_collected, method="cash", allocations=[{"document_type": "sales_invoice", "document_id": str(invoice.pk), "amount": min(dn.cash_collected, invoice.total)}], reference=f"COD {dn.number}", trip=dn.trip, delivery_note=dn)
            if dn.trip_id:
                dn.trip.cash_collected = (dn.trip.cash_collected or 0) + dn.cash_collected
                dn.trip.save(update_fields=["cash_collected"])
        AuditService.log("delivery.completed", dn, after={"status": dn.status, "delivered": str(total_delivered), "invoice": invoice.number if invoice else None})
        emit("delivery.completed", delivery_note=dn, invoice=invoice)
        if dn.trip_id:
            TripService.refresh_status(dn.trip)
        return dn


class TripService:
    @staticmethod
    @transaction.atomic
    def create(*, warehouse, date=None, driver=None, vehicle=None, rep=None, delivery_rep=None, loader=None, helper=None, route=None, orders=(), notes="", picklist=None):
        trip = DeliveryTrip.objects.create(number=derived_number(orders[0].number if orders else None, "trip", DeliveryTrip), warehouse=warehouse, date=date or timezone.localdate(), driver=driver, vehicle=vehicle or (driver.default_vehicle if driver else None), rep=rep, delivery_rep=delivery_rep, loader=loader, helper=helper, route=route, notes=notes, picklist=picklist)
        if orders:
            TripService.assign_orders(trip, orders)
        AuditService.log("created", trip, after={"driver": driver.name if driver else None, "vehicle": vehicle.reg_no if vehicle else None})
        emit("delivery.assigned", trip=trip)
        return trip

    @staticmethod
    @transaction.atomic
    def from_picklist(pl, *, driver=None, vehicle=None, rep=None, delivery_rep=None, loader=None, helper=None, route=None, date=None, notes=""):
        """Warehouse finished packing a loader form → open the dispatch (trip) for its orders."""
        if pl.status not in ("picked", "issued", "partially_issued"):
            raise DomainError("The warehouse has to issue the load form before it can be dispatched.")
        # `invoiced` belongs here too: the invoice-before-dispatch flow invoices the issued goods and then
        # still has to put them on a vehicle. Leaving it out made an invoiced order impossible to dispatch.
        orders = [o for o in pl.orders.all() if o.status in ("confirmed", "issued", "partially_delivered", "invoiced")]
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
            if o.status not in ("confirmed", "issued", "invoiced", "partially_delivered"):
                raise DomainError(f"Order {o.number} is not ready to dispatch (status {o.get_status_display()}).")
            if o.warehouse_id != trip.warehouse_id:
                raise DomainError(f"Order {o.number} belongs to another warehouse.")
            if o.trip_id and o.trip_id != trip.pk:
                raise DomainError(f"Order {o.number} is already on trip {o.trip.number}.")
            if trip.delivery_notes.filter(order=o).exists():
                continue
            seq += 1
            # Already issued by the warehouse? Put that delivery note on the vehicle instead of issuing twice.
            issued = DeliveryNote.objects.filter(order=o, trip__isnull=True, status__in=["pending", "loaded"]).order_by("created_at").first()
            if issued is not None:
                issued.trip = trip
                issued.sequence = seq
                issued.save(update_fields=["trip", "sequence", "updated_at"])
            else:
                DeliveryService.create_note(o, trip=trip, sequence=seq)
            o.trip = trip
            o.save(update_fields=["trip"])
        trip.stops_count = trip.delivery_notes.count()
        trip.save(update_fields=["stops_count", "updated_at"])
        AuditService.log("trip.orders_assigned", trip, after={"orders": [o.number for o in orders]})
        return trip

    @staticmethod
    @transaction.atomic
    def remove_order(trip, order):
        if trip.status not in ("planned", "loading"):
            raise DomainError("Cannot remove orders after the trip started.")
        trip.delivery_notes.filter(order=order).delete()
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
        """Trip start = dispatch = stock out for every delivery note (§6.3 point 3)."""
        if trip.status not in ("planned", "loading"):
            raise DomainError("Trip already started.")
        if not trip.delivery_notes.exists():
            raise DomainError("Assign at least one order before starting the trip.")
        if trip.driver_id is None:
            raise DomainError("Assign a driver before starting the trip.")
        tenant = get_current_tenant()
        van = None
        if tenant.setting("two_step_dispatch") and trip.vehicle_id:
            from apps.inventory.models import Warehouse

            van = getattr(trip.vehicle, "van_warehouse", None)
            if van is None:
                van = Warehouse.objects.create(code=f"VAN-{trip.vehicle.reg_no}"[:20], name=f"Van {trip.vehicle.reg_no}", type="van", vehicle=trip.vehicle)
        value = Decimal("0")
        for dn in trip.delivery_notes.select_related("order", "warehouse").order_by("sequence"):
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
        trip.loaded_value = value
        trip.save(update_fields=["status", "started_at", "odometer_start", "loaded_value", "updated_at"])
        if van is not None:
            from apps.accounting.services import PostingService

            PostingService.post_trip_dispatch(trip, value)
        AuditService.log("status.started", trip, after={"status": "out_for_delivery", "loaded_value": str(value), "stops": trip.stops_count})
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
        damaged_lines, returned_value = [], Decimal("0")
        for l in DeliveryNoteLine.objects.filter(delivery_note__trip=trip).select_related("variant", "variant__product", "batch", "delivery_note"):
            if l.qty_returned <= 0:
                continue
            if l.return_condition == "damaged":
                damaged_lines.append({"variant": l.variant, "batch": l.batch, "qty": l.qty_returned, "unit_cost": l.unit_cost, "note": f"{l.delivery_note.number} {l.return_reason}"})
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
            from apps.inventory.models import ReasonCode
            from apps.inventory.services import AdjustmentService

            reason = damage_reason or ReasonCode.objects.filter(category="transit_damage").first() or ReasonCode.objects.first()
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
        trip.delivery_notes.all().delete()
        trip.status = "cancelled"
        trip.notes = (trip.notes + f"\nCancelled: {reason}").strip()
        trip.save(update_fields=["status", "notes", "updated_at"])
        AuditService.log("status.cancelled", trip, after={"reason": reason})
        return trip
