from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.catalog.services import PricingService, TaxService
from apps.core.audit import AuditService
from apps.core.events import emit
from apps.core.exceptions import DomainError
from apps.core.numbering import derived_number, next_number
from apps.core.plan_limits import PlanLimitService
from apps.core.tenancy import get_current_tenant, get_current_user
from apps.inventory.services import StockService
from apps.partners.services import CustomerService

from .models import CompanyClaim, CreditNote, CreditNoteLine, RepCollection, RepSettlement, SalesInvoice, SalesInvoiceLine, SalesOrder, SalesOrderLine, StockReservation

Q4 = Decimal("0.0001")


def q4(v):
    return Decimal(v or 0).quantize(Q4, ROUND_HALF_UP)


def _base_qty(qty, packaging):
    return Decimal(qty) * (packaging.qty_in_base if packaging else Decimal("1"))


class OrderService:
    # ---------------------------------------------------------------- pricing
    @staticmethod
    def price_line(order, line, allow_override=False):
        customer = order.customer
        packaging = line.packaging
        list_price, pl = PricingService.price_for(line.variant, customer, order.price_list, None, line.qty_ordered, order.order_date)
        tenant = get_current_tenant()
        policy = tenant.setting("accept_external_prices")
        flags = []
        if line.requested_price is not None:
            req = q4(line.requested_price)
            if req != list_price:
                if policy == "accept" or (allow_override and order.source == "staff"):
                    line.unit_price = req
                else:
                    line.unit_price = list_price
                    if policy == "flag":
                        flags.append({"variant": line.variant.sku, "requested": str(req), "list": str(list_price)})
            else:
                line.unit_price = list_price
        elif not line.unit_price or not allow_override:
            line.unit_price = list_price
        gross = q4(line.qty_ordered * line.unit_price)
        line.discount_amount = q4(gross * (line.discount_pct + line.product_discount_pct) / 100)
        sd, free, names = PricingService.apply_schemes(line.variant, line.qty_ordered, line.unit_price, customer, order.order_date)
        line.scheme_discount, line.free_qty, line.scheme_names = sd, free, names
        taxable = gross - line.discount_amount - line.scheme_discount
        tax_class = TaxService.resolve_class(line.variant, customer)
        t = TaxService.compute(taxable, tax_class, order.order_date)
        line.tax1_type, line.tax1_rate, line.tax1_amount = t["tax1_type"], t["tax1_rate"], t["tax1_amount"]
        line.tax2_type, line.tax2_rate, line.tax2_amount = t["tax2_type"], t["tax2_rate"], t["tax2_amount"]
        line.line_total = taxable + line.tax1_amount + line.tax2_amount
        return gross, flags

    @staticmethod
    def recompute(order, allow_override=False):
        subtotal = disc = sd = t1 = t2 = Decimal("0")
        flags = []
        for line in order.lines.select_related("variant", "variant__product", "packaging"):
            gross, f = OrderService.price_line(order, line, allow_override)
            line.save()
            flags += f
            subtotal += gross
            disc += line.discount_amount
            sd += line.scheme_discount
            t1 += line.tax1_amount
            t2 += line.tax2_amount
        order.subtotal, order.discount, order.scheme_discount, order.tax1_total, order.tax2_total = subtotal, disc, sd, t1, t2
        order.total = subtotal - disc - sd + t1 + t2
        order.price_flags = flags
        order.save(update_fields=["subtotal", "discount", "scheme_discount", "tax1_total", "tax2_total", "total", "price_flags", "updated_at"])
        return order

    @staticmethod
    def effective_discount_pct(order):
        """The order's own % when one was set; the customer's standing rate otherwise (legacy orders)."""
        if order.discount_pct is not None:
            return Decimal(str(order.discount_pct))
        if not getattr(order, "apply_customer_discount", True):
            return Decimal("0")
        return Decimal(str(order.customer.default_discount_pct or 0))

    @staticmethod
    def write_lines(order, lines, allow_override=False):
        order.lines.all().delete()
        # the order's discount % applies to lines that carry no discount of their own
        default_pct = OrderService.effective_discount_pct(order)
        from apps.inventory.services import StockService

        for i, l in enumerate(lines):
            packaging = l.get("packaging")
            qty = _base_qty(l["qty"], packaging)
            if qty <= 0:
                continue
            # the owner's rule (21 Oct 2026): a product with nothing available cannot be put on an order at
            # all — a PARTIAL shortage still goes to on_hold_stock at confirmation, where the office decides
            if order.warehouse_id and StockService.available(l["variant"], order.warehouse) <= 0:
                raise DomainError(f"{l['variant'].name} is out of stock — it cannot be added to an order.", code="out_of_stock", status_code=400)
            req = l.get("unit_price")
            if req is not None and l.get("price_per_packaging") and packaging:
                req = Decimal(req) / packaging.qty_in_base
            prod_pct = l.get("product_discount_pct")
            if prod_pct is None:
                # the product's own rule decides: dated offer first, else the standard % from its minimum qty
                from apps.catalog.models import product_discount_for

                prod_pct = product_discount_for(l["variant"].product, qty, order.order_date)
            SalesOrderLine.objects.create(order=order, variant=l["variant"], packaging=packaging, qty_ordered=qty, requested_price=q4(req) if req is not None else None, unit_price=q4(req) if req is not None else Decimal("0"), discount_pct=l.get("discount_pct", 0) or 0, product_discount_pct=prod_pct or 0, sequence=i)
        if default_pct:
            for line in order.lines.all():
                if not line.discount_pct:
                    line.discount_pct = default_pct
                    line.save(update_fields=["discount_pct"])
        OrderService.recompute(order, allow_override)

    # ---------------------------------------------------------------- create
    @staticmethod
    @transaction.atomic
    def create(*, customer, lines, source="staff", rep=None, supplier=None, warehouse=None, order_date=None, requested_delivery_date=None, notes="", external_ref="", price_list=None, allow_override=False, auto_confirm=False, apply_customer_discount=True, discount_pct=None, location=None, payment_mode=None, charge_tax=False):
        tenant = get_current_tenant()
        PlanLimitService.check(tenant, "max_orders_month")
        if rep is None:
            # the sale person lives on the customer: orders inherit it automatically (change it on the customer)
            rep = customer.assigned_reps.filter(is_active=True).first() or customer.assigned_reps.first()
        if supplier is None and rep is not None:
            supplier = rep.supplier
        if external_ref and supplier is not None:
            existing = SalesOrder.objects.filter(supplier=supplier, external_ref=external_ref).first()
            if existing:
                return existing
        if customer.is_blocked:
            raise DomainError(f"Customer {customer.name} is blocked.")
        from apps.inventory.models import Warehouse

        warehouse = warehouse or Warehouse.objects.filter(type="main", is_active=True).first() or Warehouse.objects.filter(is_active=True).first()
        if discount_pct is None:
            # every new order records its % explicitly: the customer's standing rate, or 0 when switched off
            discount_pct = Decimal(str(customer.default_discount_pct or 0)) if apply_customer_discount else Decimal("0")
        if payment_mode and payment_mode not in ("cash", "credit"):
            raise DomainError("payment_mode must be cash or credit.", code="bad_payment_mode")
        payment_mode = payment_mode or ("cash" if not (customer.credit_days or 0) else "credit")
        order = SalesOrder.objects.create(number=next_number("sales_order"), customer=customer, location=location, supplier=supplier, rep=rep, source=source, warehouse=warehouse, order_date=order_date or timezone.localdate(), requested_delivery_date=requested_delivery_date, notes=notes, apply_customer_discount=apply_customer_discount, discount_pct=Decimal(str(discount_pct)), external_ref=external_ref, price_list=price_list or customer.price_list, currency=tenant.base_currency, payment_mode=payment_mode, charge_tax=bool(charge_tax), status="draft" if source == "staff" and not auto_confirm else "submitted")
        OrderService.write_lines(order, lines, allow_override)
        if not order.lines.exists():
            raise DomainError("Order has no valid lines.")
        AuditService.log("created", order, after={"customer": customer.name, "total": str(order.total), "source": source})
        if order.status == "submitted":
            emit("order.submitted", order=order)
        if auto_confirm:
            OrderService.confirm(order)
        elif source == "staff":
            OrderService.submit(order)
        return order

    @staticmethod
    @transaction.atomic
    def update_lines(order, lines, allow_override=False):
        if not order.is_editable:
            raise DomainError(f"Order in status {order.status} cannot be edited.")
        # an edit reflects through the WHOLE flow: any warehouse work already done is pulled back — stock
        # home, load form and delivery note cancelled, the trip lets go — and the office re-issues from the
        # edited lines, so no document downstream can disagree with the order
        if order.status in ("issued", "dispatched") or order.pick_lists.exclude(status="cancelled").exists():
            OrderService.unissue(order, reason="edited")
        OrderService.release_reservations(order)
        OrderService.write_lines(order, lines, allow_override)
        # a confirmed order holds stock against its lines; the new lines must hold it too, not ride the old grip
        if order.status == "confirmed" and get_current_tenant().setting("reserve_stock_on_confirm"):
            OrderService.reserve(order)
        AuditService.log("order.lines_changed", order, after={"total": str(order.total)})
        # the rep took this order at the counter — an office change behind their back must not be silent
        editor = get_current_user()
        if order.rep_id and order.rep.user_id and getattr(editor, "pk", None) != order.rep.user_id:
            from apps.core.notifications import notify

            notify(order.rep.user, "order.edited", f"Order {order.number} was changed by the office",
                   f"{order.customer.name} · new total {order.total:.2f}", {"order_id": str(order.id), "screen": "order"})
        return order

    # --------------------------------------------------------------- statuses
    @staticmethod
    def _set(order, status, action, extra=None):
        before = order.status
        order.status = status
        order.save(update_fields=["status", "updated_at"])
        AuditService.log(f"status.{action}", order, before={"status": before}, after={"status": status, **(extra or {})})
        # the sale person hears every turn their own order takes (owner, 22 Oct 2026) — the louder ones
        # (confirmed, rejected, cancelled, dispatched, delivered) already ring through their event handlers
        quiet = {"under_review": ("order.under_review", f"Order {order.number} is under review", ""),
                 "on_hold_credit": ("order.on_hold", f"Order {order.number} on hold — credit", (extra or {}).get("reason", "")),
                 "on_hold_stock": ("order.on_hold", f"Order {order.number} on hold — stock short", "The office will decide how to fill it")}
        if status != before and status in quiet and order.rep_id and order.rep.user_id:
            from apps.core.notifications import notify

            ntype, title, body = quiet[status]
            notify(order.rep.user, ntype, title, body, {"order_id": str(order.id), "screen": "order"})

    @staticmethod
    @transaction.atomic
    def submit(order):
        if order.status != "draft":
            raise DomainError("Only draft orders can be submitted.")
        OrderService._set(order, "submitted", "submitted")
        emit("order.submitted", order=order)
        return order

    @staticmethod
    @transaction.atomic
    def review(order):
        if order.status == "submitted":
            order.reviewed_by = get_current_user()
            order.save(update_fields=["reviewed_by"])
            OrderService._set(order, "under_review", "under_review")
        return order

    @staticmethod
    def availability(order):
        """Per-line availability in the order's warehouse."""
        rows, ok = [], True
        for line in order.lines.select_related("variant"):
            # stock this order already reserved for itself (qty_allocated) is not a shortfall —
            # without adding it back, a confirmed order reads short against its own hold
            avail = StockService.available(line.variant, order.warehouse) + line.qty_allocated
            short = max(line.qty_ordered + line.free_qty - avail, Decimal("0"))
            if short > 0:
                ok = False
            rows.append({"line_id": str(line.id), "sku": line.variant.sku, "name": line.variant.name, "ordered": str(line.qty_ordered + line.free_qty), "available": str(avail), "short": str(short)})
        return ok, rows

    @staticmethod
    @transaction.atomic
    def confirm(order, override_credit=False, allow_partial=False):
        if order.status not in ("submitted", "under_review", "on_hold_credit", "on_hold_stock", "draft"):
            raise DomainError(f"Order in status {order.status} cannot be confirmed.")
        tenant = get_current_tenant()
        OrderService.recompute(order, allow_override=True)
        ok, reason = CustomerService.credit_check(order.customer, order.total)
        if not ok and not override_credit:
            if tenant.setting("credit_policy") == "hard" or "blocked" in reason:
                raise DomainError(reason, code="credit_hold")
            order.hold_reason = reason
            order.save(update_fields=["hold_reason"])
            OrderService._set(order, "on_hold_credit", "on_hold_credit", {"reason": reason})
            from apps.core.notifications import notify_permission

            notify_permission("sales.approve_credit_hold", "approval.pending", f"Order {order.number} on credit hold", reason, {"order_id": str(order.id)})
            return order
        stock_ok, rows = OrderService.availability(order)
        if not stock_ok and not allow_partial:
            order.hold_reason = "Insufficient stock: " + ", ".join(f"{r['sku']} short {r['short']}" for r in rows if Decimal(r["short"]) > 0)
            order.save(update_fields=["hold_reason"])
            OrderService._set(order, "on_hold_stock", "on_hold_stock", {"rows": rows})
            return order
        if tenant.setting("reserve_stock_on_confirm"):
            OrderService.reserve(order)
        order.hold_reason = ""
        order.confirmed_by = get_current_user()
        order.confirmed_at = timezone.now()
        order.save(update_fields=["hold_reason", "confirmed_by", "confirmed_at"])
        OrderService._set(order, "confirmed", "confirmed")
        emit("order.confirmed", order=order)
        return order

    @staticmethod
    def reserve(order):
        ttl = settings.DMS["RESERVATION_TTL_HOURS"]
        for line in order.lines.select_related("variant"):
            need = line.qty_ordered + line.free_qty - line.qty_allocated
            if need <= 0:
                continue
            avail = StockService.available(line.variant, order.warehouse)
            take = min(need, max(avail, Decimal("0")))
            if take <= 0:
                continue
            StockService.reserve(line.variant, order.warehouse, take)
            StockReservation.objects.create(order_line=line, warehouse=order.warehouse, qty=take, expires_at=timezone.now() + timedelta(hours=ttl))
            line.qty_allocated += take
            line.save(update_fields=["qty_allocated"])

    @staticmethod
    def release_reservations(order):
        for r in StockReservation.objects.filter(order_line__order=order, released=False).select_related("order_line__variant"):
            StockService.unreserve(r.order_line.variant, r.warehouse, r.qty, r.batch)
            r.released = True
            r.save(update_fields=["released"])
        order.lines.update(qty_allocated=Decimal("0"))

    @staticmethod
    @transaction.atomic
    def hold(order, reason):
        OrderService._set(order, "on_hold_credit", "hold", {"reason": reason})
        order.hold_reason = reason
        order.save(update_fields=["hold_reason"])
        return order

    @staticmethod
    @transaction.atomic
    def reject(order, reason):
        if order.status not in ("submitted", "under_review", "on_hold_credit", "on_hold_stock"):
            raise DomainError("Only open (unconfirmed) orders can be rejected.")
        OrderService.release_reservations(order)
        order.rejection_reason = reason
        order.save(update_fields=["rejection_reason"])
        OrderService._set(order, "rejected", "rejected", {"reason": reason})
        emit("order.rejected", order=order)
        return order

    # A rep may correct their own order until midnight at the end of the day it was taken, and only while the
    # office has not acted on it. Both halves matter: after midnight the day is closed and the figures have
    # been read, and once the order is confirmed stock is reserved against it and the warehouse may be
    # picking — an edit behind that would change what someone is already acting on.
    REP_EDITABLE_STATUSES = ("submitted", "under_review")

    @staticmethod
    def edit_window_end(order):
        """
        Midnight at the end of the day the order was taken, read on the company's clock.

        It is computed from `created_at` rather than `order_date` because `created_at` is an *instant*: it
        gives the day the rep actually worked, whatever the server is set to and whoever typed the date. A
        back-dated order therefore does not hand the rep a window that closed last week, nor one that never
        opened.

        (This was found the hard way: Django ran in UTC while the business runs at UTC+5, so `order_date` was
        the UTC date and the window could close five hours before a rep typed the order. The project is on
        Asia/Karachi now, and this stays instant-based so it cannot depend on that again.)
        """
        from zoneinfo import ZoneInfo

        from apps.core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        try:
            zone = ZoneInfo((tenant.timezone if tenant else None) or "Asia/Karachi")
        except Exception:
            zone = ZoneInfo("Asia/Karachi")
        taken = timezone.localtime(order.created_at, zone)
        return taken.replace(hour=0, minute=0, second=0, microsecond=0) + timezone.timedelta(days=1)

    @staticmethod
    def rep_can_edit(order):
        # only an order the rep took on the app is theirs to change — a staff order that merely carries them
        # as the sale person (auto-assigned from the customer) is the office's document
        return order.source == "rep_app" and order.status in OrderService.REP_EDITABLE_STATUSES and timezone.now() < OrderService.edit_window_end(order)

    # cancelling from the app closes exactly when editing does (owner, 22 Oct 2026): midnight of the order's
    # day on the company clock, and only while the office has not acted on it
    rep_can_cancel = rep_can_edit

    @staticmethod
    @transaction.atomic
    def edit_by_rep(order, *, lines=None, notes=None, requested_delivery_date=None):
        """
        Replace a rep's own order lines after the fact — the whole set, as the app sends them.

        No location check: a rep notices the mistake in the next street or back at the office, which is the
        normal case rather than the suspicious one. Where they were when they *took* the order is already on
        the order and does not change.
        """
        if order.source != "rep_app":
            raise DomainError(
                f"{order.number} was entered by the office — only the office can change it.",
                code="order_not_editable", status_code=403,
            )
        if order.status not in OrderService.REP_EDITABLE_STATUSES:
            raise DomainError(
                f"{order.number} has moved on — it is {order.get_status_display().lower()}, so the office has to change it now.",
                code="order_not_editable", status_code=403,
            )
        closes = OrderService.edit_window_end(order)
        if timezone.now() >= closes:
            raise DomainError(
                f"{order.number} was taken on {order.order_date:%d %b %Y} and can no longer be changed — "
                f"the day it was taken has closed. Ask the office.",
                code="edit_window_closed", status_code=403,
            )
        if notes is not None:
            order.notes = notes
        if requested_delivery_date is not None:
            order.requested_delivery_date = requested_delivery_date
        order.save(update_fields=["notes", "requested_delivery_date", "updated_at"])
        if lines is not None:
            before = {"lines": order.lines.count(), "total": str(order.total)}
            # nothing is reserved or dispatched in these statuses, so the lines can simply be rewritten
            order.lines.all().delete()
            OrderService.write_lines(order, lines, allow_override=False)
            if not order.lines.exists():
                raise DomainError("An order needs at least one line.", code="no_lines", status_code=400)
            AuditService.log("edited_by_rep", order, before=before,
                             after={"lines": order.lines.count(), "total": str(order.total)})
        order.refresh_from_db()
        return order

    @staticmethod
    @transaction.atomic
    def unissue(order, *, reason=""):
        """
        Pull an order back from the warehouse before anything is delivered: every unit the warehouse issued
        returns to stock as its own movement ("Issue reversed"), the delivery note and the load form die,
        the trip lets go of the stop, and the order stands at confirmed again — ready to edit or re-issue.
        """
        from apps.inventory.services import StockService

        for dn in order.delivery_notes.exclude(status__in=("cancelled", "delivered", "partially_delivered")):
            if dn.stock_out_done:
                for line in dn.lines.select_related("variant", "batch"):
                    if line.qty_loaded:
                        StockService.move(warehouse=dn.warehouse, variant=line.variant, qty=line.qty_loaded,
                                          movement_type="issue_cancelled", batch=line.batch, unit_cost=line.unit_cost,
                                          reference=dn, note=f"Order {order.number}: {reason or 'pulled back'}")
            dn.status = "cancelled"
            dn.stock_out_done = False
            dn.save(update_fields=["status", "stock_out_done", "updated_at"])
        # a load form left with no live orders is dead paper
        for pl in order.pick_lists.exclude(status="cancelled"):
            if not pl.orders.exclude(pk=order.pk).exclude(status__in=("cancelled", "rejected")).exists():
                pl.status = "cancelled"
                pl.save(update_fields=["status", "updated_at"])
        # the dead paperwork's lines still point at the order lines — an edit replaces those lines, so the
        # references must go with the paper they belonged to (the movements keep the audit trail)
        from apps.delivery.models import DeliveryNoteLine, PickListLine

        DeliveryNoteLine.objects.filter(delivery_note__order=order, delivery_note__status="cancelled").delete()
        PickListLine.objects.filter(order_line__order=order).exclude(picklist__status__in=("issued", "partially_issued")).delete()
        order.lines.update(qty_dispatched=Decimal("0"))
        if order.trip_id:
            order.trip = None
            order.save(update_fields=["trip"])
        if order.status in ("issued", "dispatched"):
            OrderService._set(order, "confirmed", "unissued", {"reason": reason})
        return order

    @staticmethod
    @transaction.atomic
    def cancel(order, reason, by_rep=False):
        if by_rep and not OrderService.rep_can_cancel(order):
            raise DomainError("This order can no longer be cancelled from the app — ask the office.", code="cancel_window_closed", status_code=403)
        if order.status in ("partially_delivered", "delivered", "invoiced"):
            raise DomainError("Delivered or invoiced orders cannot be cancelled; use a return.")
        if order.status in ("cancelled", "rejected"):
            raise DomainError("Order already closed.")
        # anything the warehouse issued — even if it is on a vehicle but not yet delivered — comes home first
        OrderService.unissue(order, reason=f"cancelled: {reason}" if reason else "cancelled")
        OrderService.release_reservations(order)
        order.rejection_reason = reason
        order.save(update_fields=["rejection_reason"])
        OrderService._set(order, "cancelled", "cancelled", {"reason": reason})
        emit("order.cancelled", order=order)
        return order

    @staticmethod
    @transaction.atomic
    def allocate(order, allocations):
        """Manual allocation: [{line_id, qty, batch?}]"""
        if order.status not in ("confirmed", "on_hold_stock", "under_review"):
            raise DomainError("Order must be confirmed or on stock hold to allocate.")
        from apps.inventory.models import Batch

        for a in allocations:
            line = order.lines.get(pk=a["line_id"])
            qty = Decimal(a["qty"])
            batch = Batch.objects.filter(pk=a.get("batch")).first() if a.get("batch") else None
            StockService.reserve(line.variant, order.warehouse, qty, batch)
            StockReservation.objects.create(order_line=line, warehouse=order.warehouse, batch=batch, qty=qty, expires_at=timezone.now() + timedelta(hours=settings.DMS["RESERVATION_TTL_HOURS"]))
            line.qty_allocated += qty
            line.save(update_fields=["qty_allocated"])
        if order.status == "on_hold_stock":
            OrderService._set(order, "confirmed", "confirmed")
        return order

    @staticmethod
    @transaction.atomic
    def mark_delivered(order, lines=None, receiver_name="", cash_collected=Decimal("0"), delivered_at=None):
        """Mark delivered from the order without a trip (§6.3 point 5): DN + stock out (if needed) + invoice."""
        from apps.delivery.services import DeliveryService

        if order.status not in ("confirmed", "dispatched", "partially_delivered"):
            raise DomainError(f"Order in status {order.status} cannot be marked delivered.")
        dn = DeliveryService.create_note(order, trip=None, lines=lines)
        dn = DeliveryService.deliver(dn, delivered=None, receiver_name=receiver_name, cash_collected=cash_collected, delivered_at=delivered_at)
        return order

    @staticmethod
    def refresh_delivery_status(order):
        lines = list(SalesOrderLine.objects.filter(order=order))
        total_ord = sum((l.qty_ordered + l.free_qty for l in lines), Decimal("0"))
        total_del = sum((l.qty_delivered for l in lines), Decimal("0"))
        total_ret = sum((l.qty_returned for l in lines), Decimal("0"))
        before = order.status
        if total_del + total_ret >= total_ord and total_del > 0:
            order.status = "delivered"
            order.delivered_at = order.delivered_at or timezone.now()
        elif total_del > 0:
            order.status = "partially_delivered"
        elif order.status in ("delivered", "partially_delivered"):
            order.status = "dispatched"
        order.save(update_fields=["status", "delivered_at", "updated_at"])
        if before != order.status:
            AuditService.log("status.delivery", order, before={"status": before}, after={"status": order.status})
            if order.status == "delivered":
                emit("order.delivered", order=order)

    @staticmethod
    def expire_reservations():
        """TTL expiry for live holds — and a janitor: a reservation whose order has moved past confirmation
        (issued, delivered, cancelled…) is a leak, released on sight whatever its expiry says."""
        n = 0
        live = ("confirmed", "on_hold_stock", "on_hold_credit")
        for r in StockReservation.objects.filter(released=False).select_related("order_line__variant", "order_line__order"):
            status = r.order_line.order.status
            stale = status not in live and status not in ("draft", "submitted", "under_review")
            if stale or (status in ("confirmed", "on_hold_stock") and r.expires_at < timezone.now()):
                StockService.unreserve(r.order_line.variant, r.warehouse, r.qty, r.batch)
                r.released = True
                r.save(update_fields=["released"])
                r.order_line.qty_allocated = max(Decimal("0"), r.order_line.qty_allocated - r.qty)
                r.order_line.save(update_fields=["qty_allocated"])
                n += 1
        return n

    @staticmethod
    def auto_cancel_backorders():
        days = get_current_tenant().setting("backorder_auto_cancel_days")
        if not days:
            return 0
        cutoff = timezone.localdate() - timedelta(days=int(days))
        n = 0
        for o in SalesOrder.objects.filter(status__in=["on_hold_stock", "submitted"], order_date__lt=cutoff):
            OrderService.cancel(o, f"Auto-cancelled after {days} days")
            n += 1
        return n


class InvoiceService:
    @staticmethod
    @transaction.atomic
    def from_delivery_note(dn, basis="delivered", *, post=True, charge_tax=None, tax_type=None):
        """
        One invoice per delivery note (§6.3 point 8). `basis="loaded"` invoices what the warehouse issued —
        that is the invoice-before-dispatch flow; shortfalls on delivery are settled with a credit note.

        `post=False` leaves it a **draft**, which is the only state in which its figures can still be corrected
        (`update_draft`). Posting is the default because that is how the office has always worked here: the
        invoice goes out with the vehicle, and one that needs checking first is the exception, not the rule.

        `charge_tax`: None follows the ORDER's own switch (a tax invoice asked for when the order was taken,
        owner 26 Oct 2026) — and a refusal there must not block an automatic billing at delivery confirmation,
        so it is recorded as a notification instead. True/False is a person deciding right now, so a refusal
        (unclassified products, 3rd Schedule without MRP) is raised back at them.
        """
        if hasattr(dn, "invoice") and dn.invoice is not None:
            return dn.invoice
        order = dn.order
        customer = order.customer
        inv = SalesInvoice.objects.create(number=derived_number(order.number if order else (dn.order.number if dn and dn.order_id else None), "sales_invoice", SalesInvoice), customer=customer, order=order, delivery_note=dn, rep=order.rep, date=timezone.localdate(), due_date=timezone.localdate() + timedelta(days=customer.credit_days or 0), cost_centre=order.cost_centre, from_transit=bool(order.trip_id and get_current_tenant().setting("two_step_dispatch")))
        subtotal = disc = t1 = t2 = Decimal("0")
        tax1_type = tax2_type = None
        for i, dl in enumerate(dn.lines.select_related("order_line", "variant", "batch")):
            billed = dl.qty_loaded if basis == "loaded" else dl.qty_delivered
            if billed <= 0:
                continue
            ol = dl.order_line
            ratio = billed / (ol.qty_ordered + ol.free_qty) if (ol.qty_ordered + ol.free_qty) else Decimal("0")
            paid_qty = billed
            free_part = Decimal("0")
            if ol.free_qty:
                free_part = min(ol.free_qty * ratio, billed).quantize(Decimal("0.001"))
                paid_qty = billed - free_part
            gross = q4(paid_qty * ol.unit_price)
            line_disc = q4((ol.discount_amount + ol.scheme_discount) * ratio)
            taxable = gross - line_disc
            # tax rides ONLY on the invoice's tax switch (owner, 25 Oct 2026): no switch, no tax — the
            # tax classes no longer feed sales invoices; set_tax/recompute put the forced tax on later
            t = {"tax1_type": None, "tax1_rate": Decimal("0"), "tax1_amount": Decimal("0"),
                 "tax2_type": None, "tax2_rate": Decimal("0"), "tax2_amount": Decimal("0")}
            SalesInvoiceLine.objects.create(invoice=inv, order_line=ol, variant=ol.variant, batch=dl.batch, description=ol.variant.name, qty=paid_qty, free_qty=free_part, unit_price=ol.unit_price, unit_cost=dl.unit_cost or ol.variant.avg_cost, discount_amount=line_disc, product_discount_pct=ol.product_discount_pct, line_total=taxable + t["tax1_amount"] + t["tax2_amount"], tax1_type=t["tax1_type"], tax1_rate=t["tax1_rate"], tax1_amount=t["tax1_amount"], tax2_type=t["tax2_type"], tax2_rate=t["tax2_rate"], tax2_amount=t["tax2_amount"], sequence=i)
            subtotal += gross
            disc += line_disc
            t1 += t["tax1_amount"]
            t2 += t["tax2_amount"]
            tax1_type = tax1_type or t["tax1_type"]
            tax2_type = tax2_type or t["tax2_type"]
        inv.subtotal, inv.discount, inv.tax1_total, inv.tax2_total = subtotal, disc, t1, t2
        inv.tax1_type, inv.tax2_type = tax1_type, tax2_type
        inv.total = subtotal - disc + t1 + t2
        # the co-funded discount % comes from the order (which seeded it from the customer, changeable there);
        # it drives the manufacturer claim on posting
        inv.discount_pct = OrderService.effective_discount_pct(dn.order) if dn.order_id else Decimal(str(customer.default_discount_pct or 0))
        inv.save()
        want_tax = charge_tax if charge_tax is not None else bool(order and order.charge_tax)
        if want_tax:
            try:
                InvoiceService.set_tax(inv, apply=True, tax_type=tax_type, actor=get_current_user())
            except DomainError as e:
                if charge_tax is not None:
                    raise
                from apps.core.notifications import notify_permission

                # carry the switch's OWN refusal — "no FBR rate", "no MRP" and "no tax type" each send
                # the office to a different screen, and a generic text sent them to the wrong one
                notify_permission("sales.change_invoice", "invoice.tax_skipped",
                                  f"Invoice {inv.number} was billed WITHOUT the tax the order asked for",
                                  f"{e.detail} Fix that, then open the invoice and press Tax on.",
                                  {"invoice_id": str(inv.id)})
        if post:
            InvoiceService.post(inv)
        return inv

    @staticmethod
    @transaction.atomic
    def update_draft(inv, *, date=None, due_date=None, notes=None, lines=None):
        """
        Correct a **draft** invoice before it goes to the ledger: its date, and the price or discount on a line.

        Quantities are deliberately not among them. What is on an invoice is what the warehouse issued and the
        delivery note records; letting the two disagree would mean the stock that left and the stock that was
        billed are different numbers with nothing to say which is right. A quantity that is genuinely wrong is a
        delivery-note matter, or a credit note once this has been posted.

        Tax is recomputed from the corrected figures rather than scaled, because a price change can cross a slab
        and the slab is the thing a tax authority checks.
        """
        if inv.status != "draft":
            raise DomainError("Only a draft invoice can be corrected. A posted one is corrected with a credit note.",
                              code="not_draft", status_code=400)
        if date is not None:
            inv.date = date
        if due_date is not None:
            inv.due_date = due_date
        if notes is not None:
            inv.notes = notes

        by_id = {str(l.id): l for l in inv.lines.all()}
        for row in lines or []:
            line = by_id.get(str(row.get("id")))
            if line is None:
                raise DomainError("That line is not on this invoice.", status_code=400)
            if row.get("unit_price") is not None:
                line.unit_price = Decimal(str(row["unit_price"]))
            if row.get("discount_amount") is not None:
                line.discount_amount = Decimal(str(row["discount_amount"]))
            if row.get("product_discount_pct") is not None:
                new_pct = Decimal(str(row["product_discount_pct"]))
                gross = q4(line.qty * line.unit_price)
                line.discount_amount = max(Decimal("0"), q4(line.discount_amount + gross * (new_pct - line.product_discount_pct) / 100))
                line.product_discount_pct = new_pct
            if "tax_pct" in row:
                # typed per-line %: beats the product's and the invoice's rate while the tax switch is on
                v = row["tax_pct"]
                line.tax_pct_override = None if v in (None, "") else Decimal(str(v))
                if line.tax_pct_override is not None and not (0 <= line.tax_pct_override <= 100):
                    raise DomainError("A line's tax % must be between 0 and 100.", status_code=400)
            if line.unit_price < 0 or line.discount_amount < 0:
                raise DomainError("A price or a discount cannot be negative.", status_code=400)
            gross = q4(line.qty * line.unit_price)
            if line.discount_amount > gross:
                raise DomainError(f"The discount on {line.description} is more than the line is worth.", status_code=400)
            line.save()

        # one place owns the figures: recompute re-taxes every line (the tax switch, the products' own %,
        # per-line overrides, else the tax classes) and re-totals the invoice
        inv.save(update_fields=["date", "due_date", "notes", "updated_at"])
        InvoiceService.recompute(inv)
        AuditService.log("invoice.corrected", inv, after={"total": str(inv.total)})
        return inv

    @staticmethod
    @transaction.atomic
    def set_discount(inv, *, action="remove", percent=None, actor=None):
        """
        Draft invoices only: drop the discount the customer's standing rate put on the lines, or apply a
        percentage to every line. A posted invoice is never edited — raise a credit note instead.
        """
        if inv.status != "draft":
            raise DomainError("This invoice is already posted — raise a credit note instead of changing its discount.")
        pct = Decimal("0") if action == "remove" else Decimal(str(percent if percent is not None else (inv.customer.default_discount_pct or 0)))
        for line in inv.lines.all():
            # the product's own % rides along — changing the customer % must not wipe it off the amount
            line.discount_amount = q4(q4(line.qty * line.unit_price) * (pct + Decimal(str(line.product_discount_pct or 0))) / 100)
            line.save(update_fields=["discount_amount"])
        inv.discount_pct = pct  # the co-funded total %; the manufacturer's share is claimed on posting
        inv.save(update_fields=["discount_pct"])
        InvoiceService.recompute(inv)
        AuditService.log("invoice.discount", inv, after={"action": action, "percent": str(pct)}, actor=actor)
        return inv

    @staticmethod
    def forced_line_tax(product, *, qty, unit_price, discount_amount, override=None):
        """
        The tax switch's charge for one line, as (display rate %, amount) — owner, 25 Oct 2026: "if
        tax on then use fbr rate via hs code. otherwise no tax at all", completed to the full FBR
        arithmetic on 26 Oct 2026 (INVOICE_TAX_FLOW_GUIDE.md): the amount comes from the SAME
        engine.compute_line the filing calls, so a compound rate keeps its rupee part, a fixed rate
        ("Rs.700/MT") is no longer invoiced at zero, and 3rd Schedule goods tax on max(MRP × qty,
        taxable). A % typed on the line overrides as a plain percentage — the human's word. No dated
        rates, no product Tax %, no invoice-level rate: an unclassified product simply carries no
        tax, and an FBR "Exempt"/"0%" product stays at zero on purpose.
        """
        from apps.fbr import engine

        if override is not None:
            taxable = engine.round2(engine.round2(Decimal(str(qty)) * Decimal(str(unit_price))) - Decimal(str(discount_amount or 0)))
            return override, engine.round2(taxable * Decimal(str(override)) / 100)
        if product is None or not product.fbr_rate_desc:
            return Decimal("0"), Decimal("0")
        mrp = product.mrp_ex_tax if engine.is_mrp_sale_type(product.fbr_sale_type) else None
        money = engine.compute_line(quantity=qty, unit_price=unit_price, discount_amount=discount_amount,
                                    rate_str=product.fbr_rate_desc, sale_type=product.fbr_sale_type, mrp=mrp)
        return engine.gst_rate_num(product.fbr_rate_desc), money["salesTaxApplicable"]

    @staticmethod
    def recompute(inv):
        """Re-total an invoice from its lines (used after a discount or tax change)."""
        forced = inv.force_tax_type
        subtotal = discount = t1 = t2 = Decimal("0")
        for line in inv.lines.select_related("variant", "variant__product"):
            gross = q4(line.qty * line.unit_price)
            taxable = gross - line.discount_amount
            if forced is not None:
                rate, amount = InvoiceService.forced_line_tax(
                    line.variant.product if line.variant_id else None,
                    qty=line.qty, unit_price=line.unit_price, discount_amount=line.discount_amount,
                    override=line.tax_pct_override)
                line.tax1_type, line.tax1_rate, line.tax1_amount = forced, rate, q4(amount)
            else:
                # no switch, no tax (owner, 25 Oct 2026): the tax classes do not feed sales invoices
                line.tax1_type, line.tax1_rate, line.tax1_amount = None, Decimal("0"), Decimal("0")
            line.tax2_type, line.tax2_rate, line.tax2_amount = None, Decimal("0"), Decimal("0")
            line.line_total = taxable + line.tax1_amount + line.tax2_amount
            line.save(update_fields=["tax1_type", "tax1_rate", "tax1_amount", "tax2_type", "tax2_rate", "tax2_amount", "line_total"])
            subtotal += gross
            discount += line.discount_amount
            t1 += line.tax1_amount
            t2 += line.tax2_amount
        inv.subtotal, inv.discount, inv.tax1_total, inv.tax2_total = subtotal, discount, t1, t2
        inv.tax1_type, inv.tax2_type = forced, None
        inv.total = subtotal - discount + t1 + t2
        inv.save(update_fields=["subtotal", "discount", "tax1_type", "tax2_type", "tax1_total", "tax2_total", "total", "updated_at"])
        return inv

    @staticmethod
    @transaction.atomic
    def set_tax(inv, *, apply, tax_type=None, actor=None):
        """
        The invoice's tax switch (owner, 25 Oct 2026): ON charges each line at its product's FBR GST
        rate — "if tax on then use fbr rate via hs code" — under the company's sales-tax type (a typed
        per-line % overrides); OFF means no tax at all. No dated rates needed, ever. Draft invoices
        only: a posted invoice's tax is already in the ledger.
        """
        from apps.catalog.models import TaxType

        if inv.status != "draft":
            raise DomainError("This invoice is already posted — its tax is in the ledger. Use a credit note.")
        if apply:
            tt = tax_type or TaxType.objects.filter(kind="sales_tax", applies_to__in=("sales", "both")).order_by("code").first()
            if tt is None:
                raise DomainError("No sales tax type exists — create one under Settings → Taxes first.", code="no_tax_type")
            # a switch that would charge zero on every line is a misconfiguration, not a tax — say so
            # instead of printing "Tax 0.00" (the silent-zero bug, owner 25 Oct 2026)
            if not any(l.tax_pct_override is not None or (l.variant_id and l.variant.product.fbr_rate_desc)
                       for l in inv.lines.select_related("variant__product")):
                raise DomainError(
                    "Tax is on but none of these products carries an FBR rate — classify them on the "
                    "product's FBR card (HS code → sale type → rate) first.", code="no_tax_rate")
            # 3rd Schedule goods tax on the PRINTED retail price (guide §5) — without it the invoice
            # would quietly under-tax on the charged price, and the filing would refuse anyway
            from apps.fbr import engine

            for l in inv.lines.select_related("variant__product"):
                p = l.variant.product if l.variant_id else None
                if p is not None and engine.is_mrp_sale_type(p.fbr_sale_type) and not (p.mrp_ex_tax or 0):
                    raise DomainError(
                        f"{p.name} is 3rd Schedule Goods but has no printed retail price — set the MRP "
                        "on the product's FBR card first; the law taxes these on the retail price.",
                        code="mrp_required")
            inv.force_tax_type, inv.force_tax_rate = tt, None
        else:
            inv.force_tax_type, inv.force_tax_rate = None, None
        inv.save(update_fields=["force_tax_type", "force_tax_rate", "updated_at"])
        InvoiceService.recompute(inv)
        AuditService.log("invoice.tax", inv, after={"apply": bool(apply), "tax_type": inv.force_tax_type.name if inv.force_tax_type else None,
                                                   "rate": "FBR rate per product"}, actor=actor)
        return inv

    @staticmethod
    def company_shares(inv):
        """
        The manufacturer-funded part of this invoice's discount, per supplier, from the DiscountContribution
        row for inv.discount_pct. The invoice posting and the claim journals both read THIS, so the split in
        Discounts Allowed can never drift from what is actually claimed.
        """
        from collections import defaultdict

        from .models import DiscountContribution

        groups = defaultdict(list)  # supplier_id -> [(supplier, line, company_amount)]
        if not inv.discount_pct or inv.discount_pct <= 0:
            return groups
        row = DiscountContribution.objects.filter(total_pct=inv.discount_pct, is_active=True).first()
        if row is None or row.company_pct <= 0 or row.total_pct <= 0:
            return groups
        # the company funds its percentage points OF THE SALE (5% = 3 + 2): its share is base x company%,
        # so a product's own discount (not co-funded) never inflates the claim
        for line in inv.lines.select_related("variant__product__supplier", "variant__product__brand__supplier"):
            if line.discount_amount <= 0:
                continue
            product = line.variant.product
            supplier = product.supplier or (product.brand.supplier if product.brand_id else None)
            if supplier is None:
                continue
            company_amt = q4(q4(line.qty * line.unit_price) * row.company_pct / 100)
            if company_amt <= 0:
                continue
            groups[supplier.pk].append((supplier, line, company_amt))
        return groups

    @staticmethod
    def present_lines(inv, lines):
        """
        How the printed invoice reads (owner's rule, 21 Oct 2026): a line's Discount column carries only the
        product's OWN discount; the customer-level % appears once, after the subtotal — never inside the
        item lines. Display amounts add the customer part back, so the column sums to the subtotal that the
        discount row is then taken from, and the page still checks with a pencil.
        """
        customer_part = Decimal("0")
        tax_shown = (inv.tax1_total or Decimal("0")) + (inv.tax2_total or Decimal("0"))
        # the column shows whenever the switch is on — zeros included, so a product missing its Tax % is
        # visible rather than silently untaxed
        tax_on = bool(inv.force_tax_type_id) or tax_shown > 0
        for l in lines:
            prod = q4(q4(l.qty * l.unit_price) * (l.product_discount_pct or 0) / 100)
            prod = min(prod, l.discount_amount or Decimal("0"))
            cust = (l.discount_amount or Decimal("0")) - prod
            l.prod_discount = prod
            l.tax_amount = (l.tax1_amount or Decimal("0")) + (l.tax2_amount or Decimal("0"))
            # with tax on the page, the Amount column is pre-tax so Subtotal − Discount + Tax = Total still
            # checks with a pencil; without tax it stays tax-inclusive as before
            l.display_amount = (l.line_total or Decimal("0")) + cust - (l.tax_amount if tax_on else Decimal("0"))
            customer_part += cust
        tax_label = ""
        if tax_on and inv.tax1_type_id:
            rate = next((l.tax1_rate for l in lines if l.tax1_rate), None)
            tax_label = f"{inv.tax1_type.name}{f' ({rate.normalize()}%)' if rate else ''}"
        return {"customer_discount": q4(customer_part), "tax_shown": q4(tax_shown), "tax_on": tax_on, "tax_label": tax_label,
                "display_subtotal": q4((inv.total or Decimal("0")) + customer_part - tax_shown)}

    @staticmethod
    def _raise_company_claims(inv):
        """
        After an invoice posts, recover the manufacturer's share of its discount. Each line's share is
        attributed to that product's manufacturer, so one invoice can raise several claims. Nothing is
        raised when no co-funding applies.
        """
        from apps.accounting.services import PostingService

        from .models import CompanyClaim, CompanyClaimLine

        from .models import DiscountContribution

        groups = InvoiceService.company_shares(inv)
        if not groups:
            return
        row = DiscountContribution.objects.filter(total_pct=inv.discount_pct, is_active=True).first()
        for items in groups.values():
            supplier = items[0][0]
            total = sum((amt for _, _, amt in items), Decimal("0"))
            if total <= 0:
                continue
            # a reopened invoice re-posts onto the SAME row (one claim per invoice×supplier): the cancelled
            # claim revives with the corrected figures instead of colliding with itself
            claim = CompanyClaim.objects.filter(invoice=inv, supplier=supplier).first()
            if claim is not None:
                claim.date, claim.discount_pct, claim.company_pct, claim.total = inv.date, inv.discount_pct, row.company_pct, total
                claim.status, claim.paid_amount = "posted", Decimal("0")
                claim.save(update_fields=["date", "discount_pct", "company_pct", "total", "status", "paid_amount", "updated_at"])
                claim.lines.all().delete()
            else:
                claim = CompanyClaim.objects.create(number=derived_number(inv.number, "company_claim", CompanyClaim), invoice=inv, supplier=supplier, date=inv.date, discount_pct=inv.discount_pct, company_pct=row.company_pct, total=total)
            for _, line, amt in items:
                CompanyClaimLine.objects.create(claim=claim, variant=line.variant, qty=line.qty, base_amount=q4(line.qty * line.unit_price), company_amount=amt)
            claim.journal = PostingService.post_company_claim(claim)
            claim.save(update_fields=["journal"])
            # the owner's rule (21 Oct 2026): the manufacturer's payable minuses at source — the claim is
            # born and netted in one breath, and the register still shows the whole trail
            if get_current_tenant().setting("claim_settlement") == "auto_net_off":
                CompanyClaimService.net_off(claim, notes="auto net-off at posting")

    @staticmethod
    @transaction.atomic
    def post(inv):
        if inv.status != "draft":
            raise DomainError("Invoice already posted.")
        from apps.accounting.services import PostingService

        inv.journal = PostingService.post_sales_invoice(inv)
        inv.status = "posted"
        inv.save(update_fields=["journal", "status", "updated_at"])
        InvoiceService._raise_company_claims(inv)
        if inv.order_id:
            order = inv.order
            if order.status == "delivered" and not order.delivery_notes.exclude(invoice__isnull=False).filter(status__in=["delivered", "partially_delivered"]).exists():
                OrderService._set(order, "invoiced", "invoiced")
        AuditService.log("status.posted", inv, after={"total": str(inv.total), "journal": inv.journal.number if inv.journal else None})
        emit("invoice.created", invoice=inv)
        return inv

    @staticmethod
    @transaction.atomic
    def reopen(inv, *, reason="", actor=None):
        """
        Edit a POSTED invoice the way books allow it (owner, 24 Oct 2026): nothing is mutated behind the
        ledger. Every journal this invoice put on the books — its posting, its COGS, its manufacturer
        claims and their net-offs — is reversed; the claims are cancelled; the invoice stands at draft
        again with its figures free to correct; posting it re-books everything from the corrected numbers,
        claims included. Money already received blocks it: remove the receipts first, or use a credit note.
        """
        from apps.accounting.models import JournalEntry
        from apps.accounting.services import PostingService

        if inv.status == "draft":
            return inv
        if inv.status == "cancelled":
            raise DomainError("A cancelled invoice cannot be reopened — raise a fresh invoice.")
        if inv.paid_amount > 0 or inv.status in ("partially_paid", "paid"):
            raise DomainError("Money has been received against this invoice — remove the receipts first, or correct it with a credit note.", code="has_payments")
        filing = getattr(inv, "fbr_filing", None)
        if filing is not None and filing.status == "submitted":
            raise DomainError("This invoice is filed with FBR and must stay exactly as filed — correct it with a credit note.", code="fbr_submitted")
        memo = f"Invoice {inv.number} reopened for editing"
        for claim in inv.company_claims.exclude(status="cancelled"):
            for je in JournalEntry.objects.filter(source_type="CompanyClaim", source_id=str(claim.pk), status="posted"):
                PostingService.reverse(je, memo=memo)
            claim.status, claim.paid_amount = "cancelled", Decimal("0")
            claim.save(update_fields=["status", "paid_amount", "updated_at"])
        for je in JournalEntry.objects.filter(source_type="SalesInvoice", source_id=str(inv.pk), status="posted"):
            PostingService.reverse(je, memo=memo)
        inv.journal = None
        inv.status = "draft"
        inv.save(update_fields=["journal", "status", "updated_at"])
        AuditService.log("invoice.reopened", inv, after={"reason": reason}, actor=actor)
        return inv

    @staticmethod
    @transaction.atomic
    def cancel(inv, reason=""):
        if inv.paid_amount > 0:
            raise DomainError("Invoice has payments; cancel them first.")
        filing = getattr(inv, "fbr_filing", None)
        if filing is not None and filing.status == "submitted":
            raise DomainError("This invoice is filed with FBR and must stay exactly as filed — correct it with a credit note.", code="fbr_submitted")
        from apps.accounting.services import PostingService

        if inv.journal_id:
            PostingService.reverse(inv.journal, memo=f"Invoice {inv.number} cancelled")
            from apps.accounting.models import JournalEntry

            for je in JournalEntry.objects.filter(source_type="SalesInvoice", source_id=str(inv.pk), status="posted").exclude(pk=inv.journal_id):
                PostingService.reverse(je, memo=f"Invoice {inv.number} cancelled")
        # unwind the manufacturer claims this invoice raised (only while they are still unsettled)
        open_claims = inv.company_claims.exclude(status="cancelled")
        if open_claims.filter(paid_amount__gt=0).exists():
            raise DomainError("A manufacturer claim on this invoice has been settled; reverse that first.")
        for claim in open_claims:
            CompanyClaimService.cancel(claim, reason=f"Invoice {inv.number} cancelled")
        inv.status = "cancelled"
        inv.notes = (inv.notes + f"\nCancelled: {reason}").strip()
        # release the delivery note (a OneToOne): cancelling is how a wrong invoice gets reissued, and the
        # slot staying occupied would block that delivery from ever being billed again
        inv.delivery_note = None
        inv.save(update_fields=["status", "notes", "delivery_note", "updated_at"])
        AuditService.log("status.cancelled", inv, after={"reason": reason})
        return inv

    @staticmethod
    def mark_overdue():
        today = timezone.localdate()
        return SalesInvoice.objects.filter(status__in=["posted", "partially_paid"], due_date__lt=today).update(status="overdue")


def _claw_back_claims(cn):
    """
    Keune does not fund goods that came home (owner, 21 Oct 2026). For each manufacturer claim on the
    credited invoice, the company share of the returned base is clawed back: an OPEN claim shrinks (Dr the
    Distribution Margin head / Cr the claims receivable), a SETTLED one puts the payable back up (Dr the
    margin head / Cr Trade Payables) — the mirror of how it was taken.
    """
    from collections import defaultdict

    from apps.accounting.services import Line, PostingService, account_for

    inv = cn.invoice
    if inv is None:
        return
    claims = {c.supplier_id: c for c in inv.company_claims.exclude(status__in=("cancelled", "written_off"))}
    if not claims:
        return
    base_by_supplier = defaultdict(Decimal)
    for l in cn.lines.select_related("variant__product__supplier", "variant__product__brand__supplier"):
        product = l.variant.product
        supplier = product.supplier or (product.brand.supplier if product.brand_id else None)
        if supplier is not None and supplier.pk in claims:
            base_by_supplier[supplier.pk] += q4(l.qty * l.unit_price)
    inc = account_for("manufacturer_incentive", "company_claim")
    recv = account_for("manufacturer_claim", "company_claim")
    for supplier_id, base in base_by_supplier.items():
        claim = claims[supplier_id]
        share = min(q4(base * claim.company_pct / 100), claim.total)
        if share <= 0:
            continue
        open_part = min(share, claim.balance)
        settled_part = share - open_part
        lines = []
        if open_part > 0:
            lines += [Line(inc, debit=open_part, description=f"Claim {claim.number} clawed back — {cn.number}"),
                      Line(recv, credit=open_part, description=f"Claim {claim.number} reduced by return")]
            claim.total = q4(claim.total - open_part)
        if settled_part > 0:
            ap = claim.supplier.account if claim.supplier.account_id else account_for("ap", "company_claim")
            lines += [Line(inc, debit=settled_part, description=f"Claim {claim.number} clawed back — {cn.number}"),
                      Line(ap, credit=settled_part, description=f"Returned goods un-fund {claim.number}", partner_type="supplier", partner_id=supplier_id)]
        PostingService.post(lines, date_=cn.date, memo=f"Manufacturer share returned — {cn.number} against {inv.number}", source=cn)
        if claim.total <= claim.paid_amount and claim.status != "paid":
            claim.status = "paid"
        claim.save(update_fields=["total", "status"])
        AuditService.log("claim.clawback", claim, after={"credit_note": cn.number, "share": str(share)})


class CreditNoteService:
    @staticmethod
    @transaction.atomic
    def create(*, customer, lines, invoice=None, warehouse=None, date=None, reason="", notes="", applied_to_invoice=True):
        cn = CreditNote.objects.create(number=next_number("credit_note"), customer=customer, invoice=invoice, warehouse=warehouse, date=date or timezone.localdate(), reason=reason, notes=notes, applied_to_invoice=applied_to_invoice and invoice is not None)
        # the credit reverses what the invoice actually charged, per variant — never the tax classes;
        # a standalone credit note (no invoice) charges no tax, because tax rides only on an invoice's
        # tax switch (owner, 25 Oct 2026). Mirrored as PER-UNIT tax, not a rate %: a fixed ("Rs.700/MT"),
        # compound or MRP-based charge has no single percentage, and reversing the rate alone
        # under-credited exactly the part the rate could not say (guide §4-§6, 26 Oct 2026). Aggregated
        # over ALL the variant's invoice lines — two lines of one variant (two batches, two packagings)
        # can carry different per-unit tax, and keeping only the last line's figure mis-credited both
        # (audit, 26 Oct 2026); both taxes ride along so a legacy class-taxed invoice credits in full.
        charged = {}
        if invoice is not None:
            for il in invoice.lines.all():
                c = charged.setdefault(il.variant_id, {"qty": Decimal("0"), "t1": Decimal("0"), "t2": Decimal("0"),
                                                       "tt1": None, "r1": None, "tt2": None, "r2": None})
                c["qty"] += il.qty
                c["t1"] += il.tax1_amount
                c["t2"] += il.tax2_amount
                c["tt1"], c["r1"] = c["tt1"] or il.tax1_type, c["r1"] if c["r1"] is not None else il.tax1_rate
                c["tt2"], c["r2"] = c["tt2"] or il.tax2_type, c["r2"] if c["r2"] is not None else il.tax2_rate
        from apps.fbr.engine import round2

        subtotal = t1 = t2 = Decimal("0")
        tax1_type = tax2_type = None
        for l in lines:
            variant = l["variant"]
            unit_price = q4(l["unit_price"])
            taxable = q4(Decimal(l["qty"]) * unit_price)
            hit = charged.get(variant.id)
            if hit is not None and hit["qty"]:
                tt1, r1 = hit["tt1"], hit["r1"] or Decimal("0")
                tt2, r2 = hit["tt2"], hit["r2"] or Decimal("0")
                amt1 = round2(hit["t1"] / hit["qty"] * Decimal(l["qty"]))
                amt2 = round2(hit["t2"] / hit["qty"] * Decimal(l["qty"]))
            elif invoice is not None and invoice.force_tax_type_id:
                # a variant the invoice never carried, credited against a switch invoice: same chain
                tt1 = invoice.force_tax_type
                r1, amt1 = InvoiceService.forced_line_tax(variant.product, qty=l["qty"], unit_price=unit_price, discount_amount=Decimal("0"))
                amt1, tt2, r2, amt2 = q4(amt1), None, Decimal("0"), Decimal("0")
            else:
                tt1, r1, amt1, tt2, r2, amt2 = None, Decimal("0"), Decimal("0"), None, Decimal("0"), Decimal("0")
            t = {"tax1_type": tt1 if amt1 else None, "tax1_rate": r1 or Decimal("0"), "tax1_amount": amt1,
                 "tax2_type": tt2 if amt2 else None, "tax2_rate": r2 or Decimal("0"), "tax2_amount": amt2}
            CreditNoteLine.objects.create(credit_note=cn, variant=variant, batch=l.get("batch"), qty=l["qty"], unit_price=unit_price, unit_cost=q4(l.get("unit_cost") or variant.avg_cost or variant.cost_price), reason=l.get("reason", ""), restock=l.get("restock", True), condition=l.get("condition", "good"), line_total=taxable + t["tax1_amount"] + t["tax2_amount"], tax1_type=t["tax1_type"], tax1_rate=t["tax1_rate"], tax1_amount=t["tax1_amount"], tax2_type=t["tax2_type"], tax2_rate=t["tax2_rate"], tax2_amount=t["tax2_amount"])
            subtotal += taxable
            t1 += t["tax1_amount"]
            t2 += t["tax2_amount"]
            tax1_type = tax1_type or t["tax1_type"]
            tax2_type = tax2_type or t["tax2_type"]
        cn.subtotal, cn.tax1_total, cn.tax2_total, cn.tax1_type, cn.tax2_type = subtotal, t1, t2, tax1_type, tax2_type
        cn.total = subtotal + t1 + t2
        cn.save()
        AuditService.log("created", cn, after={"customer": customer.name, "total": str(cn.total)})
        return cn

    @staticmethod
    @transaction.atomic
    def post(cn):
        if cn.status != "draft":
            raise DomainError("Credit note already posted.")
        from apps.accounting.services import PostingService
        from apps.inventory.models import Warehouse

        wh = cn.warehouse or Warehouse.objects.filter(type="main").first()
        quarantine = Warehouse.objects.filter(type="quarantine", is_active=True).first()
        for line in cn.lines.select_related("variant", "variant__product", "batch"):
            # Only good stock the user chose to restock goes back as sellable; broken and expired goods are
            # written off — held in the quarantine warehouse at zero value (see PostingService.post_credit_note
            # for the matching loss accounts). Keep this predicate identical to the one accounting uses.
            if line.restock and line.condition == "good":
                StockService.move(warehouse=wh, variant=line.variant, qty=line.qty, movement_type="sales_return", batch=line.batch, unit_cost=line.unit_cost, reference=cn, note=line.reason or cn.reason)
            elif quarantine is not None:
                StockService.move(warehouse=quarantine, variant=line.variant, qty=line.qty, movement_type="sales_return", batch=line.batch, unit_cost=Decimal("0"), reference=cn, note=line.reason or line.get_condition_display())
        cn.journal = PostingService.post_credit_note(cn)
        cn.status = "posted"
        cn.save(update_fields=["journal", "status", "updated_at"])
        if cn.applied_to_invoice and cn.invoice_id:
            inv = cn.invoice
            apply_amt = min(cn.total, inv.total - inv.paid_amount)
            if apply_amt > 0:
                inv.paid_amount += apply_amt
                inv.status = "paid" if inv.paid_amount >= inv.total else "partially_paid"
                inv.save(update_fields=["paid_amount", "status"])
        for line in cn.lines.select_related("variant"):
            ol = SalesOrderLine.objects.filter(order=cn.invoice.order if cn.invoice_id and cn.invoice.order_id else None, variant=line.variant).first() if cn.invoice_id else None
            if ol:
                ol.qty_returned += line.qty
                ol.save(update_fields=["qty_returned"])
        _claw_back_claims(cn)
        AuditService.log("status.posted", cn, after={"total": str(cn.total)})
        return cn


def _accuracy(gps):
    """The `accuracy_m` a phone sends with a fix — kept so a vague position can be told from a precise one."""
    value = (gps or {}).get("accuracy_m")
    return float(value) if value not in (None, "") else None


class RepCollectionService:
    @staticmethod
    @transaction.atomic
    def record(*, rep, customer, amount, date=None, method="cash", reference="", allocations=None, notes="", gps=None):
        amount = q4(amount)
        if amount <= 0:
            raise DomainError("Amount must be positive.")
        if not customer.assigned_reps.filter(pk=rep.pk).exists() and rep.user_id and get_current_user() == rep.user:
            raise DomainError("Customer is not assigned to this rep.")
        allocs = []
        remaining = amount
        open_inv = SalesInvoice.objects.filter(customer=customer, status__in=["posted", "partially_paid", "overdue"]).order_by("date")
        if allocations:
            for a in allocations:
                inv = SalesInvoice.objects.get(pk=a["invoice"], customer=customer)
                amt = q4(a["amount"])
                if amt > inv.total - inv.paid_amount + Decimal("0.01"):
                    raise DomainError(f"Allocation exceeds balance of {inv.number}.")
                allocs.append((inv, amt))
                remaining -= amt
        else:
            for inv in open_inv:
                if remaining <= 0:
                    break
                take = min(inv.total - inv.paid_amount, remaining)
                if take > 0:
                    allocs.append((inv, take))
                    remaining -= take
        col = RepCollection.objects.create(number=next_number("rep_collection"), rep=rep, customer=customer, date=date or timezone.localdate(), amount=amount, method=method, reference=reference, notes=notes, recorded_by=get_current_user() if getattr(get_current_user(), "is_authenticated", False) else None, gps_lat=(gps or {}).get("lat"), gps_lng=(gps or {}).get("lng"), gps_accuracy_m=_accuracy(gps))
        col.allocations = [{"invoice": str(inv.pk), "number": inv.number, "amount": str(amt)} for inv, amt in allocs]
        for inv, amt in allocs:
            inv.paid_amount += amt
            inv.status = "paid" if inv.paid_amount >= inv.total else "partially_paid"
            inv.save(update_fields=["paid_amount", "status"])
        from apps.accounting.services import PostingService

        col.journal = PostingService.post_rep_collection(col)
        col.save(update_fields=["allocations", "journal"])
        AuditService.log("rep_collection.recorded", col, after={"rep": rep.name, "customer": customer.name, "amount": str(amount)})
        emit("payment.received", payment=col)
        return col

    @staticmethod
    @transaction.atomic
    def settle(*, rep, collections, received_by=None, cash_account=None, bank_account=None, date=None, notes=""):
        collections = [c for c in collections if c.status == "held_by_rep" and c.rep_id == rep.pk]
        if not collections:
            raise DomainError("No unsettled collections selected for this rep.")
        amount = sum((c.amount for c in collections), Decimal("0"))
        if cash_account is None and bank_account is None:
            from apps.accounting.models import CashAccount

            cash_account = CashAccount.objects.filter(is_petty=False).first()
        st = RepSettlement.objects.create(number=next_number("rep_settlement"), rep=rep, date=date or timezone.localdate(), amount=amount, received_by=received_by or get_current_user(), cash_account=cash_account, bank_account=bank_account, notes=notes)
        for c in collections:
            c.status = "settled"
            c.settlement = st
            c.save(update_fields=["status", "settlement", "updated_at"])
        from apps.accounting.services import PostingService

        st.journal = PostingService.post_rep_settlement(st)
        st.save(update_fields=["journal"])
        AuditService.log("rep_settlement.recorded", st, after={"rep": rep.name, "amount": str(amount), "collections": len(collections)})
        return st

    @staticmethod
    def unsettled_alerts():
        days = get_current_tenant().setting("rep_unsettled_alert_days") or 3
        cutoff = timezone.localdate() - timedelta(days=int(days))
        return RepCollection.objects.filter(status="held_by_rep", date__lte=cutoff).select_related("rep", "customer")


class CompanyClaimService:
    """Settle a manufacturer's discount-share claim — either by a receipt from them or by netting it against
    what the distributor owes them on purchases."""

    @staticmethod
    @transaction.atomic
    def settle_receipt(claim, *, amount=None, method="bank_transfer", cash_account=None, bank_account=None, date=None, reference=""):
        from apps.accounting.services import PaymentService

        if claim.status not in ("posted", "partially_paid"):
            raise DomainError("Claim is not open for settlement.")
        amount = q4(amount) if amount else claim.balance
        if amount <= 0 or amount > claim.balance + Decimal("0.01"):
            raise DomainError(f"Amount must be between 0 and the balance {claim.balance}.")
        pay = PaymentService.record(
            direction="in", partner_type="company", partner_id=str(claim.supplier_id), amount=amount,
            method=method, cash_account=cash_account, bank_account=bank_account, date_=date,
            reference=reference or f"Discount claim {claim.number}",
            allocations=[{"document_type": "company_claim", "document_id": str(claim.pk), "amount": amount}],
        )
        AuditService.log("claim.receipt", claim, after={"amount": str(amount), "payment": pay.number})
        return claim

    @staticmethod
    @transaction.atomic
    def net_off(claim, *, amount=None, date=None, notes=""):
        """Reduce the supplier's payable by the claim: Dr Trade Payables, Cr Manufacturer Claims Receivable."""
        from apps.accounting.services import Line, PaymentService, PostingService, account_for

        if claim.status not in ("posted", "partially_paid"):
            raise DomainError("Claim is not open for settlement.")
        amount = q4(amount) if amount else claim.balance
        if amount <= 0 or amount > claim.balance + Decimal("0.01"):
            raise DomainError(f"Amount must be between 0 and the balance {claim.balance}.")
        supplier = claim.supplier
        ap = supplier.account if supplier.account_id else account_for("ap", "company_claim")
        recv = account_for("manufacturer_claim", "company_claim")
        PostingService.post(
            [Line(ap, debit=amount, description=f"Claim {claim.number} netted against purchases", partner_type="supplier", partner_id=supplier.id),
             Line(recv, credit=amount, description=f"Claim {claim.number} settled")],
            date_=date or timezone.localdate(), memo=f"Claim {claim.number} net-off – {supplier.name}", source=claim,
        )
        PaymentService._apply(claim, "company_claim", amount)
        AuditService.log("claim.netoff", claim, after={"amount": str(amount)})
        return claim

    @staticmethod
    @transaction.atomic
    def cancel(claim, reason=""):
        from apps.accounting.services import PostingService

        if claim.paid_amount > 0:
            raise DomainError("Claim has been (partly) settled; reverse the settlement first.")
        if claim.status == "cancelled":
            return claim
        if claim.journal_id:
            PostingService.reverse(claim.journal, memo=f"Claim {claim.number} cancelled")
        claim.status = "cancelled"
        claim.save(update_fields=["status", "updated_at"])
        AuditService.log("status.cancelled", claim, after={"reason": reason})
        return claim
