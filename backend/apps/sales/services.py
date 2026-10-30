from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.catalog.services import PricingService
from apps.core.audit import AuditService
from apps.core.events import emit
from apps.core.exceptions import DomainError
from apps.core.numbering import derived_number, next_number
from apps.core.plan_limits import PlanLimitService
from apps.core.tenancy import get_current_tenant, get_current_user
from apps.inventory.services import StockService
from apps.partners.services import CustomerService

from .models import CompanyClaim, CreditNote, CreditNoteApplication, CreditNoteLine, RepCollection, RepSettlement, SalesInvoice, SalesInvoiceLine, SalesOrder, SalesOrderLine, StockReservation

Q4 = Decimal("0.0001")


def q4(v):
    return Decimal(v or 0).quantize(Q4, ROUND_HALF_UP)


def _base_qty(qty, packaging):
    return Decimal(qty) * (packaging.qty_in_base if packaging else Decimal("1"))


def gst_pct(rates):
    """"18%" — the GST % a footer prints beside GST (owner, 27 Oct 2026: "change GST to GST (N%)"), from the lines'
    own rates; a document mixing rates lists each ("17%, 18%"). Empty when no line carries a %."""
    vals = sorted({Decimal(str(r)).normalize() for r in rates if r and Decimal(str(r)) > 0})
    return ", ".join(f"{format(v, 'f')}%" for v in vals)


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
        if getattr(order, "charge_tax", False):
            # a tax-invoice order carries the SAME FBR tax its invoice will charge (owner, 26 Oct 2026:
            # "do same for sale order"), so a cash delivery collects the taxed total, not the net one.
            # No rate on the product → zero here, and the billing notifies (invoice.tax_skipped).
            if not hasattr(order, "_sales_tax_tt"):
                from apps.catalog.models import TaxType

                order._sales_tax_tt = TaxType.objects.filter(kind="sales_tax", applies_to__in=("sales", "both")).order_by("code").first()
            t = InvoiceService.line_taxes(
                line.variant.product, qty=line.qty_ordered, unit_price=line.unit_price,
                discount_amount=line.discount_amount + line.scheme_discount)
            line.tax1_type, line.tax1_rate, line.tax1_amount = order._sales_tax_tt, t["rate"], q4(t["gst"])
            line.tax2_type, line.tax2_rate, line.tax2_amount = None, Decimal("0"), Decimal("0")
            others = InvoiceService.set_other_taxes(line, t)
        else:
            # tax off = no tax at all (owner, 25-27 Oct 2026), exactly like the invoice that will bill it: the
            # legacy tax classes priced unflagged orders with a tax the invoice never charged and the page never
            # showed, so the order's payable and its invoice disagreed
            line.tax1_type, line.tax1_rate, line.tax1_amount = None, Decimal("0"), Decimal("0")
            line.tax2_type, line.tax2_rate, line.tax2_amount = None, Decimal("0"), Decimal("0")
            others = InvoiceService.set_other_taxes(line)
        # FED/Extra/Further ride in the line; WHT is withheld by the buyer and comes off the order total
        line.line_total = taxable + line.tax1_amount + line.tax2_amount + others
        return gross, flags

    @staticmethod
    def recompute(order, allow_override=False):
        subtotal = disc = sd = t1 = t2 = fed = extra = further = wht = Decimal("0")
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
            fed += line.fed_amount
            extra += line.extra_tax_amount
            further += line.further_tax_amount
            wht += line.wht_amount
        order.subtotal, order.discount, order.scheme_discount, order.tax1_total, order.tax2_total = subtotal, disc, sd, t1, t2
        order.fed_total, order.extra_tax_total, order.further_tax_total, order.wht_total = fed, extra, further, wht
        order.total = subtotal - disc - sd + t1 + t2 + fed + extra + further - wht
        order.price_flags = flags
        order.save(update_fields=["subtotal", "discount", "scheme_discount", "tax1_total", "tax2_total", "fed_total", "extra_tax_total",
                                  "further_tax_total", "wht_total", "total", "price_flags", "updated_at"])
        return order

    @staticmethod
    @transaction.atomic
    def follow_customer_discount(customer, old_pct, new_pct, actor=None, repost=True):
        """
        A customer's standing discount changed: their business still in flight follows it, books included (owner,
        27 Oct 2026 — TDN-SO-00055 kept 0% after House Of Charbel was set to 15%: "fix for the project as well …
        also update accounting as well"). An order takes the % once, at creation; this carries a change through.

        * Orders still on the old standing rate whose goods have NOT reached the customer (up to dispatched) take
          the new %, lines and all. Their draft invoices are re-discounted; an unpaid, unfiled POSTED invoice is
          reopened (its posting, COGS, claims and net-offs reversed), re-discounted and posted again — so the
          ledger, the discount split and the manufacturer's claim all follow.
        * Draft invoices anywhere on the old rate follow too: they are not on the books.
        * Delivered, paid or FBR-filed business is settled and is never rewritten behind anyone's back: those
          invoices are listed in a notification for the office to reopen on purpose.
        * A % set by hand on an order or invoice is that document's own and stays.
        """
        old_pct, new_pct = Decimal(str(old_pct or 0)), Decimal(str(new_pct or 0))
        done = {"orders": [], "draft_invoices": [], "reposted_invoices": [], "posted_invoices": []}
        if old_pct == new_pct:
            return done
        old_s, new_s = (format(x.normalize(), "f") for x in (old_pct, new_pct))   # 15.000 → "15"
        reason = f"customer's standing discount changed {old_s}% → {new_s}%"
        action = "apply" if new_pct > 0 else "remove"
        in_flight = ["draft", "submitted", "under_review", "confirmed", "on_hold_credit", "on_hold_stock", "issued", "dispatched"]

        def settled(inv):
            filing = getattr(inv, "fbr_filing", None)
            if inv.paid_amount > 0 or inv.status not in ("posted", "overdue") or (filing is not None and filing.status == "submitted"):
                return True
            try:
                InvoiceService.reversal_blockers(inv)   # a return against it, or its claim paid by a payment
            except DomainError:
                return True
            return False

        for o in SalesOrder.objects.filter(customer=customer, status__in=in_flight, apply_customer_discount=True):
            if o.discount_pct is not None and Decimal(str(o.discount_pct)) != old_pct:
                continue  # a % set by hand for this order
            billed = list(o.invoices.exclude(status__in=["draft", "cancelled"]))
            if any(settled(i) for i in billed) or (billed and not repost):
                continue  # money or a filing already rests on it, or no right to re-post — listed below
            o.discount_pct = new_pct
            o.save(update_fields=["discount_pct", "updated_at"])
            o.lines.filter(discount_pct=old_pct).update(discount_pct=new_pct)
            OrderService.recompute(o, allow_override=True)  # prices stay as they stand; only the discount moves
            AuditService.log("order.discount_followed_customer", o, before={"discount_pct": str(old_pct)}, after={"discount_pct": str(new_pct), "total": str(o.total)}, actor=actor)
            done["orders"].append(o.number)
            for inv in billed:
                if inv.discount_pct != old_pct:
                    continue
                InvoiceService.reopen(inv, reason=reason, actor=actor)
                InvoiceService.set_discount(inv, action=action, percent=new_pct, actor=actor)
                InvoiceService.post(inv)
                done["reposted_invoices"].append(inv.number)
        for inv in SalesInvoice.objects.filter(customer=customer, status="draft", discount_pct=old_pct).select_related("order"):
            o = inv.order
            if o is not None and o.number not in done["orders"] and (
                    not o.apply_customer_discount or (o.discount_pct is not None and Decimal(str(o.discount_pct)) != old_pct)):
                continue  # its order took no customer discount, or chose its own %
            InvoiceService.set_discount(inv, action=action, percent=new_pct, actor=actor)
            done["draft_invoices"].append(inv.number)
        done["posted_invoices"] = list(SalesInvoice.objects.filter(customer=customer, status__in=["posted", "overdue", "partially_paid"], discount_pct=old_pct)
                                       .exclude(number__in=done["reposted_invoices"]).values_list("number", flat=True))
        if any(done.values()):
            from apps.core.notifications import notify_permission

            body = [f"{customer.name}: standing discount {old_s}% → {new_s}%."]
            if done["orders"] or done["draft_invoices"]:
                body.append("Updated: " + ", ".join(done["orders"] + done["draft_invoices"]) + ".")
            if done["reposted_invoices"]:
                body.append("Re-posted with the new discount (ledger and claims updated): " + ", ".join(done["reposted_invoices"]) + ".")
            if done["posted_invoices"]:
                body.append("Delivered, paid or filed — kept at the old % (reopen to change): " + ", ".join(done["posted_invoices"]) + ".")
            notify_permission("sales.change_invoice", "customer.discount_changed", f"{customer.name} discount changed to {new_s}%", " ".join(body),
                              {"customer_id": str(customer.pk)})
        return done

    @staticmethod
    def preview(customer, lines, order=None):
        """
        Price a basket WITHOUT saving anything (the rep app shows subtotal / discount / taxes / total before Submit,
        27 Oct 2026). The lines go through the same price_line a real order uses — price list, salon R.P, the
        customer's or the order's %, product discounts, schemes, the tax-invoice taxes — but on unsaved objects:
        no number is taken, no stock is checked or held, no event, notification or webhook fires. `order` prices
        the basket as an edit of that order would (its date, %, price list and tax switch). Lines are in the
        shape write_lines takes: [{variant, packaging, qty, unit_price?, discount_pct?, price_per_packaging?}].
        """
        from apps.catalog.models import product_discount_for

        if order is not None:
            o = SalesOrder(customer=customer, order_date=order.order_date, price_list=order.price_list, source=order.source,
                           discount_pct=order.discount_pct, apply_customer_discount=order.apply_customer_discount, charge_tax=order.charge_tax)
            echoed = {(ol.variant_id, ol.packaging_id): ol.unit_price for ol in order.lines.all() if ol.requested_price is None}
        else:
            o = SalesOrder(customer=customer, order_date=timezone.localdate(), price_list=customer.price_list, source="rep_app",
                           discount_pct=Decimal(str(customer.default_discount_pct or 0)), apply_customer_discount=True, charge_tax=bool(customer.charge_tax))
            echoed = {}
        default_pct = OrderService.effective_discount_pct(o)
        totals = {k: Decimal("0") for k in ("subtotal", "discount", "scheme_discount", "tax1_total", "tax2_total", "fed_total", "extra_tax_total", "further_tax_total", "wht_total")}
        rows = []
        for l in lines:
            packaging = l.get("packaging")
            qty = _base_qty(l["qty"], packaging)
            if qty <= 0:
                continue
            req = l.get("unit_price")
            if req is not None and l.get("price_per_packaging") and packaging:
                req = Decimal(req) / packaging.qty_in_base
            if req is not None and echoed.get((l["variant"].pk, packaging.pk if packaging else None)) == q4(req):
                req = None
            line = SalesOrderLine(order=o, variant=l["variant"], packaging=packaging, qty_ordered=qty,
                                  requested_price=q4(req) if req is not None else None, unit_price=q4(req) if req is not None else Decimal("0"),
                                  discount_pct=l.get("discount_pct") or default_pct,
                                  product_discount_pct=product_discount_for(l["variant"].product, qty, o.order_date) or 0)
            gross, _flags = OrderService.price_line(o, line)
            for k, v in (("subtotal", gross), ("discount", line.discount_amount), ("scheme_discount", line.scheme_discount), ("tax1_total", line.tax1_amount),
                         ("tax2_total", line.tax2_amount), ("fed_total", line.fed_amount), ("extra_tax_total", line.extra_tax_amount),
                         ("further_tax_total", line.further_tax_amount), ("wht_total", line.wht_amount)):
                totals[k] += v
            rows.append({"variant": str(line.variant_id), "variant_name": line.variant.name, "variant_sku": line.variant.sku,
                         "packaging": str(packaging.pk) if packaging else None, "qty_ordered": str(qty), "unit_price": str(line.unit_price),
                         "discount_pct": str(line.discount_pct), "product_discount_pct": str(line.product_discount_pct),
                         "discount_amount": str(line.discount_amount), "scheme_discount": str(line.scheme_discount), "scheme_names": line.scheme_names,
                         "free_qty": str(line.free_qty), "tax1_name": line.tax1_type.name if line.tax1_type_id else "", "tax1_rate": str(line.tax1_rate),
                         "tax1_amount": str(line.tax1_amount), "tax2_name": line.tax2_type.name if line.tax2_type_id else "", "tax2_rate": str(line.tax2_rate),
                         "tax2_amount": str(line.tax2_amount), "fed_amount": str(line.fed_amount), "extra_tax_amount": str(line.extra_tax_amount),
                         "further_tax_amount": str(line.further_tax_amount), "wht_amount": str(line.wht_amount), "line_total": str(line.line_total)})
        t = totals
        total = t["subtotal"] - t["discount"] - t["scheme_discount"] + t["tax1_total"] + t["tax2_total"] + t["fed_total"] + t["extra_tax_total"] + t["further_tax_total"] - t["wht_total"]
        return {**{k: str(v) for k, v in t.items()}, "discount_pct": str(o.discount_pct if o.discount_pct is not None else default_pct),
                "charge_tax": bool(o.charge_tax), "total": str(total), "lines": rows,
                "gst_pct": gst_pct(r.get("tax1_rate") for r in rows)}   # "18%" — the footer's GST (N%), as the saved order

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
        # an edit form sends every line's price back. A price that is only the stored one echoed, on a line nobody
        # ever priced by hand (no requested_price — the server set it from the list), is NOT a request: treating it
        # as one raised false "price differences" whenever the list moved after the order was taken (TDN-SO-00055,
        # 27 Oct 2026). Such a line simply takes today's list price; a price somebody typed is still a request.
        echoed = {(ol.variant_id, ol.packaging_id): ol.unit_price for ol in order.lines.all() if ol.requested_price is None}
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
            if req is not None and echoed.get((l["variant"].pk, packaging.pk if packaging else None)) == q4(req):
                req = None
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
    def create(*, customer, lines, source="staff", rep=None, supplier=None, warehouse=None, order_date=None, requested_delivery_date=None, notes="", external_ref="", price_list=None, allow_override=False, auto_confirm=False, apply_customer_discount=True, discount_pct=None, location=None, payment_mode=None, charge_tax=None):
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
        order = SalesOrder.objects.create(number=next_number("sales_order"), customer=customer, location=location, supplier=supplier, rep=rep, source=source, warehouse=warehouse, order_date=order_date or timezone.localdate(), requested_delivery_date=requested_delivery_date, notes=notes, apply_customer_discount=apply_customer_discount, discount_pct=Decimal(str(discount_pct)), external_ref=external_ref, price_list=price_list or customer.price_list, currency=tenant.base_currency, payment_mode=payment_mode,
                                           # tax invoices are managed on the CUSTOMER (owner, 26 Oct 2026); an explicit value still wins via the API
                                           charge_tax=bool(customer.charge_tax) if charge_tax is None else bool(charge_tax),
                                           status="draft" if source == "staff" and not auto_confirm else "submitted")
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
            # refused at the door and the trip already closed: its close brought the goods home (delivery_return)
            home = dn.status in ("failed", "returned") and dn.trip_id is not None and dn.trip.status == "closed"
            if dn.stock_out_done and not home:
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
    def cancel(order, reason, by_rep=False, final_status="cancelled"):
        if by_rep and not OrderService.rep_can_cancel(order):
            raise DomainError("This order can no longer be cancelled from the app — ask the office.", code="cancel_window_closed", status_code=403)
        if order.status in ("partially_delivered", "delivered", "invoiced"):
            raise DomainError("Delivered or invoiced orders cannot be cancelled; use a return.")
        if order.status in ("cancelled", "rejected"):
            raise DomainError("Order already closed.")
        # The order's invoice goes with it: cancelling TDN-SO-00076 left its invoice on the books, and a return then
        # put the goods in stock a second time (29 Oct 2026). Money received or an FBR filing makes it a return.
        live = list(order.invoices.exclude(status="cancelled"))
        for inv in live:
            if inv.status != "draft":
                CreditNoteService.unapply_foreign(inv)   # kept credit it took goes back to its returns
                inv.refresh_from_db()
            InvoiceService.reversal_blockers(inv)
            filing = getattr(inv, "fbr_filing", None)
            if inv.paid_amount > 0 or (filing is not None and filing.status == "submitted"):
                why = "has money or a return against it" if inv.paid_amount > 0 else "is filed with FBR"
                raise DomainError(f"{order.number} cannot be cancelled: its invoice {inv.number} {why}. Record a return instead.", code="invoice_settled")
        for inv in live:
            if inv.status != "draft":
                InvoiceService.reopen(inv, reason=f"order {order.number} {'rejected by the customer' if final_status == 'rejected' else 'cancelled'}: {reason}")
                inv.refresh_from_db()
            # its entries come off the books with it (held by the reopen, dropped by the cancel; a closed month reverses)
            InvoiceService.cancel(inv, reason=("Customer rejected: " if final_status == "rejected" else "Order cancelled: ") + (reason or ""))
        # anything the warehouse issued — even if it is on a vehicle but not yet delivered — comes home first
        OrderService.unissue(order, reason=f"cancelled: {reason}" if reason else "cancelled")
        OrderService.release_reservations(order)
        order.rejection_reason = reason
        order.save(update_fields=["rejection_reason"])
        if final_status == "rejected":
            OrderService._set(order, "rejected", "customer_rejected", {"reason": reason})
            emit("order.rejected", order=order)
        else:
            OrderService._set(order, "cancelled", "cancelled", {"reason": reason})
            emit("order.cancelled", order=order)
        return order

    @staticmethod
    @transaction.atomic
    def customer_rejected(order, reason=""):
        """
        The customer refused the goods before or at delivery, nothing paid (owner, 30 Oct 2026, Case A): no return and
        no payment — the goods come back once (now if still in the warehouse; with the van, the refused delivery's
        trip close otherwise), the invoice's entries come off the books, and the order closes as Rejected.
        """
        if order.status in ("partially_delivered", "delivered", "invoiced"):
            raise DomainError(f"{order.number} was delivered — goods the customer kept and sent back later are a return: "
                              "use Credit note / return on the invoice.", code="delivered")
        return OrderService.cancel(order, f"Rejected by the customer{': ' + reason if reason else ''}", final_status="rejected")

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
        Correct a **draft** invoice — a new one, or a posted one reopened to edit — before it goes to the ledger:
        its date, and on its lines the price, the discount, the tax %, the QUANTITY, and items added or taken off
        (owner, 27 Oct 2026: "reopen to edit should allow all kind of change and change here should also update
        sale order as well").

        A quantity is what the warehouse issued, so it never moves alone: the delivery note, the load form and
        the stock move by the same difference (more issued, or the surplus back on the shelf), and the order
        line follows. Once the goods are DELIVERED that is no longer a correction but a return or a second
        delivery — prices, discounts and tax can still change, a quantity cannot. The order is then brought
        into line with the invoice (`sync_order`).

        lines: [{id, qty?, unit_price?, discount_amount?, product_discount_pct?, tax_pct?, remove?}] for lines on
        it, and [{variant, qty, unit_price?, product_discount_pct?}] (no id) for an item to add.

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

        by_id = {str(l.id): l for l in inv.lines.select_related("order_line", "variant", "batch")}
        for row in lines or []:
            if not row.get("id"):
                InvoiceService._add_line(inv, row)
                continue
            line = by_id.get(str(row.get("id")))
            if line is None:
                raise DomainError("That line is not on this invoice.", status_code=400)
            new_qty = None if row.get("qty") in (None, "") else Decimal(str(row["qty"]))
            if row.get("remove") or (new_qty == 0 and line.qty != 0):
                by_id.pop(str(line.id))
                InvoiceService._remove_line(inv, line)
                continue
            typed_disc = None if row.get("discount_amount") in (None, "") else Decimal(str(row["discount_amount"]))
            if new_qty is not None and new_qty != line.qty and new_qty != 0:
                if new_qty < 0:
                    raise DomainError(f"{line.description}: the quantity cannot be negative.", status_code=400)
                old_qty = line.qty
                InvoiceService._reissue(inv, line.order_line, line.variant, line.batch, new_qty - old_qty)
                if line.order_line_id:
                    ol = line.order_line
                    ol.qty_ordered = max(Decimal("0"), ol.qty_ordered + (new_qty - old_qty))
                    ol.save(update_fields=["qty_ordered"])
                # the discount travels with the quantity unless a new amount was typed
                if typed_disc is None or typed_disc == line.discount_amount:
                    typed_disc = q4(line.discount_amount * new_qty / old_qty) if old_qty else Decimal("0")
                line.qty = new_qty
            if row.get("unit_price") not in (None, ""):
                line.unit_price = Decimal(str(row["unit_price"]))
            if typed_disc is not None:
                line.discount_amount = typed_disc
            if row.get("product_discount_pct") not in (None, ""):
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
        if not inv.lines.exists():
            raise DomainError("An invoice needs at least one line — cancel it instead of emptying it.", status_code=400)

        # one place owns the figures: recompute re-taxes every line (the tax switch, the products' own %,
        # per-line overrides, else the tax classes) and re-totals the invoice
        inv.save(update_fields=["date", "due_date", "notes", "updated_at"])
        InvoiceService.recompute(inv)
        InvoiceService.sync_order(inv)
        AuditService.log("invoice.corrected", inv, after={"total": str(inv.total)})
        return inv

    @staticmethod
    def _reissue(inv, ol, variant, batch, delta):
        """
        A draft invoice's quantity moved by `delta` (billed units, free included): the warehouse paperwork and the
        stock follow — the delivery note's line, the load form's issued count, and, once the stock has left, a
        movement for the difference (more issued, or the surplus back in stock as "Issue reversed").
        Returns the unit cost the units went out at.
        """
        from apps.delivery.models import DeliveryNoteLine, PickListLine

        dn = inv.delivery_note
        cost = variant.avg_cost
        if dn is None or not delta:
            return cost
        if dn.status in ("delivered", "partially_delivered", "returned"):
            raise DomainError(f"{variant.name}: the goods on {dn.number} are already delivered, so its quantity cannot change here — "
                              "take a return with a credit note, or deliver the extra on a new order. Prices, discounts and tax can still be changed.",
                              code="delivered", status_code=400)
        dl = DeliveryNoteLine.objects.filter(delivery_note=dn, order_line=ol, variant=variant, batch=batch).first() if ol else None
        if dl is None and ol is not None:
            dl = DeliveryNoteLine(delivery_note=dn, order_line=ol, variant=variant, batch=batch, qty_loaded=Decimal("0"), unit_cost=cost)
        if dn.stock_out_done:
            mv = StockService.move(warehouse=dn.warehouse, variant=variant, qty=-delta, batch=batch, unit_cost=dl.unit_cost if dl and dl.unit_cost else None,
                                   movement_type="sale_dispatch" if delta > 0 else "issue_cancelled", reference=dn,
                                   note=f"Invoice {inv.number} edited")
            cost = mv.unit_cost or cost
            if ol is not None:
                ol.qty_dispatched = max(Decimal("0"), ol.qty_dispatched + delta)
                ol.save(update_fields=["qty_dispatched"])
        if dl is not None:
            dl.qty_loaded += delta
            if dl.unit_cost is None or not dl.unit_cost:
                dl.unit_cost = cost
            if dl.qty_loaded <= 0:
                if dl.pk:
                    dl.delete()
            else:
                dl.save()
        pick = PickListLine.objects.filter(picklist_id=dn.picklist_id, order_line=ol).first() if dn.picklist_id and ol else None
        if pick is not None:
            pick.qty_picked = max(Decimal("0"), pick.qty_picked + delta)
            pick.qty_to_pick = max(pick.qty_picked, Decimal("0"))
            pick.save(update_fields=["qty_picked", "qty_to_pick"])
        elif dn.picklist_id and ol is not None and delta > 0:
            PickListLine.objects.create(picklist_id=dn.picklist_id, order_line=ol, variant=variant, batch=batch, qty_to_pick=delta, qty_picked=delta)
        return cost

    @staticmethod
    def _remove_line(inv, line):
        """Take an item off a draft invoice: its units go back to stock and off the order."""
        InvoiceService._reissue(inv, line.order_line, line.variant, line.batch, -(line.qty + line.free_qty))
        ol = line.order_line
        line.delete()
        if ol is not None:
            ol.qty_ordered = max(Decimal("0"), ol.qty_ordered - line.qty)
            ol.free_qty = max(Decimal("0"), ol.free_qty - line.free_qty)
            ol.save(update_fields=["qty_ordered", "free_qty"])
            if ol.qty_ordered <= 0 and ol.free_qty <= 0 and not ol.delivery_lines.exists():
                ol.delete()

    @staticmethod
    def _add_line(inv, row):
        """An item added on a draft invoice: a new order line, issued from the warehouse onto the delivery note."""
        from apps.catalog.models import ProductVariant, product_discount_for

        order = inv.order
        if order is None:
            raise DomainError("This invoice has no order to add an item to.", status_code=400)
        variant = ProductVariant.objects.filter(pk=row.get("variant")).select_related("product").first()
        if variant is None:
            raise DomainError("Pick the item to add.", status_code=400)
        qty = Decimal(str(row.get("qty") or 0))
        if qty <= 0:
            raise DomainError(f"{variant.name}: enter a quantity.", status_code=400)
        if row.get("unit_price") not in (None, ""):
            price = q4(Decimal(str(row["unit_price"])))
        else:
            price, _pl = PricingService.price_for(variant, inv.customer, order.price_list, None, qty, order.order_date)
        prod_pct = Decimal(str(row["product_discount_pct"])) if row.get("product_discount_pct") not in (None, "") else Decimal(str(product_discount_for(variant.product, qty, order.order_date) or 0))
        seq = (order.lines.order_by("-sequence").values_list("sequence", flat=True).first() or 0) + 1
        ol = SalesOrderLine.objects.create(order=order, variant=variant, qty_ordered=qty, unit_price=price,
                                           discount_pct=inv.discount_pct or Decimal("0"), product_discount_pct=prod_pct, sequence=seq)
        # a batch-tracked item goes out by batch, earliest expiry first, as the warehouse issue picks it
        dn, plan = inv.delivery_note, [(None, qty)]
        if dn is not None and variant.product.is_batch_tracked:
            tenant = get_current_tenant()
            plan, short = StockService.fefo_batches(variant, dn.warehouse, qty, inv.customer.min_shelf_life_days or tenant.setting("min_shelf_life_days"))
            plan = [(b, q) for b, q, _bin in plan]
            if short > 0:
                raise DomainError(f"Not enough stock of {variant.name} in {dn.warehouse.name} (short {short:g}).", code="insufficient_stock", status_code=400)
        seq = inv.lines.order_by("-sequence").values_list("sequence", flat=True).first() or 0
        for batch, q in plan:
            cost = InvoiceService._reissue(inv, ol, variant, batch, q)
            gross = q4(q * price)
            disc = q4(gross * ((inv.discount_pct or Decimal("0")) + prod_pct) / 100)
            seq += 1
            SalesInvoiceLine.objects.create(invoice=inv, order_line=ol, variant=variant, batch=batch, description=variant.name, qty=q, unit_price=price,
                                            unit_cost=cost or variant.avg_cost, discount_amount=disc, product_discount_pct=prod_pct,
                                            line_total=gross - disc, sequence=seq)

    @staticmethod
    def sync_order(inv, *, tax=False):
        """
        The order follows its invoice's corrections (owner, 27 Oct 2026: "change here should also update sale order
        as well"): each line's price and discount and the discount % — then the order is re-totalled at those
        prices. Quantities have already moved with the stock (`update_draft`). `tax=True` carries the invoice's tax
        switch too: only when a person flipped it — an automatic tax that was skipped (unclassified items) must not
        quietly switch the order's tax off. An order line another live invoice also bills keeps its own price: two
        invoices would be arguing over one line. An item issued from two batches is two invoice lines of one
        order line, and syncs as one.
        """
        order = inv.order
        if order is None or order.status in ("cancelled", "rejected"):
            return
        others = set(SalesInvoiceLine.objects.filter(order_line__order=order).exclude(invoice__status="cancelled").exclude(invoice=inv)
                     .values_list("order_line_id", flat=True))
        groups = {}
        for il in inv.lines.select_related("order_line"):
            if il.order_line_id and il.order_line_id not in others:
                groups.setdefault(il.order_line_id, []).append(il)
        for ils in groups.values():
            ol = ils[0].order_line
            gross = sum((q4(il.qty * il.unit_price) for il in ils), Decimal("0"))
            disc = sum((il.discount_amount for il in ils), Decimal("0"))
            main = max(ils, key=lambda il: il.qty)   # batch lines share a price; the largest speaks for them
            total_pct = (disc / gross * 100) if gross else Decimal("0")
            ol.unit_price, ol.requested_price = main.unit_price, None
            ol.product_discount_pct = main.product_discount_pct
            ol.discount_pct = max(Decimal("0"), (total_pct - main.product_discount_pct).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))
            ol.save(update_fields=["unit_price", "requested_price", "product_discount_pct", "discount_pct"])
        order.discount_pct = inv.discount_pct
        fields = ["discount_pct", "updated_at"]
        if tax:
            order.charge_tax = bool(inv.force_tax_type_id)
            fields.append("charge_tax")
        order.save(update_fields=fields)
        OrderService.recompute(order, allow_override=True)   # prices as the invoice set them
        AuditService.log("order.followed_invoice", order, after={"invoice": inv.number, "total": str(order.total)})

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
        t = InvoiceService.line_taxes(product, qty=qty, unit_price=unit_price, discount_amount=discount_amount, override=override)
        return t["rate"], t["gst"]

    @staticmethod
    def line_taxes(product, *, qty, unit_price, discount_amount, override=None):
        """
        EVERY tax one tax-document line carries, from the same engine.compute_line the FBR filing calls:
        {"rate", "gst", "fed", "extra", "further", "wht"}. GST is forced_line_tax's (a typed % overrides it);
        FED, Extra Tax, Further Tax and WHT are the product's FBR-card rates on the line's TAXABLE value
        (guide §4 — never on taxable + GST, never on the MRP base), owner 26 Oct 2026.
        """
        from apps.fbr import engine

        zero = Decimal("0")
        rates = ({"fed_rate": product.fbr_fed_rate, "extra_rate": product.fbr_extra_tax_rate,
                  "further_rate": product.fbr_further_tax_rate, "withheld_rate": product.fbr_wht_rate}
                 if product is not None else {})
        rate_str = product.fbr_rate_desc if product is not None else ""
        mrp = product.mrp_ex_tax if product is not None and engine.is_mrp_sale_type(product.fbr_sale_type) else None
        money = engine.compute_line(quantity=qty, unit_price=unit_price, discount_amount=discount_amount,
                                    rate_str=rate_str, sale_type=product.fbr_sale_type if product is not None else "",
                                    mrp=mrp, **rates)
        if override is not None:
            rate = Decimal(str(override))
            gst = engine.round2(money["valueSalesExcludingST"] * rate / 100)
        elif rate_str:
            rate, gst = engine.gst_rate_num(rate_str), money["salesTaxApplicable"]
        else:
            rate, gst = zero, zero
        return {"rate": rate, "gst": gst, "fed": money["fedPayable"], "extra": money["extraTax"],
                "further": money["furtherTax"], "wht": money["salesTaxWithheldAtSource"]}

    @staticmethod
    def set_other_taxes(line, t=None):
        """Write line_taxes' FED/Extra/Further/WHT onto a line (zeros when `t` is None — no tax switch)."""
        t = t or {}
        line.fed_amount = q4(t.get("fed", 0))
        line.extra_tax_amount = q4(t.get("extra", 0))
        line.further_tax_amount = q4(t.get("further", 0))
        line.wht_amount = q4(t.get("wht", 0))
        return line.fed_amount + line.extra_tax_amount + line.further_tax_amount

    @staticmethod
    def recompute(inv):
        """Re-total an invoice from its lines (used after a discount or tax change)."""
        forced = inv.force_tax_type
        subtotal = discount = t1 = t2 = fed = extra = further = wht = Decimal("0")
        for line in inv.lines.select_related("variant", "variant__product"):
            gross = q4(line.qty * line.unit_price)
            taxable = gross - line.discount_amount
            if forced is not None:
                t = InvoiceService.line_taxes(
                    line.variant.product if line.variant_id else None,
                    qty=line.qty, unit_price=line.unit_price, discount_amount=line.discount_amount,
                    override=line.tax_pct_override)
                line.tax1_type, line.tax1_rate, line.tax1_amount = forced, t["rate"], q4(t["gst"])
                others = InvoiceService.set_other_taxes(line, t)
            else:
                # no switch, no tax (owner, 25 Oct 2026): the tax classes do not feed sales invoices
                line.tax1_type, line.tax1_rate, line.tax1_amount = None, Decimal("0"), Decimal("0")
                others = InvoiceService.set_other_taxes(line)
            line.tax2_type, line.tax2_rate, line.tax2_amount = None, Decimal("0"), Decimal("0")
            # the line's own taxes are in its total; WHT is withheld by the buyer, so it leaves the invoice total
            line.line_total = taxable + line.tax1_amount + line.tax2_amount + others
            line.save(update_fields=["tax1_type", "tax1_rate", "tax1_amount", "tax2_type", "tax2_rate", "tax2_amount",
                                     "fed_amount", "extra_tax_amount", "further_tax_amount", "wht_amount", "line_total"])
            subtotal += gross
            discount += line.discount_amount
            t1 += line.tax1_amount
            t2 += line.tax2_amount
            fed += line.fed_amount
            extra += line.extra_tax_amount
            further += line.further_tax_amount
            wht += line.wht_amount
        inv.subtotal, inv.discount, inv.tax1_total, inv.tax2_total = subtotal, discount, t1, t2
        inv.fed_total, inv.extra_tax_total, inv.further_tax_total, inv.wht_total = fed, extra, further, wht
        inv.tax1_type, inv.tax2_type = forced, None
        inv.total = subtotal - discount + t1 + t2 + fed + extra + further - wht
        inv.save(update_fields=["subtotal", "discount", "tax1_type", "tax2_type", "tax1_total", "tax2_total", "fed_total",
                                "extra_tax_total", "further_tax_total", "wht_total", "total", "updated_at"])
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
    def settle_status(inv):
        """
        An invoice's status from what settled it (owner, 30 Oct 2026: a return is not a payment). Settled in full by
        returns alone reads Returned; any money in it reads Paid / Partially paid; nothing yet is Posted, or
        Overdue once its due date has passed.
        """
        cash = inv.paid_amount - inv.credited_amount
        if inv.paid_amount >= inv.total - Decimal("0.005"):
            return "paid" if cash > Decimal("0.005") else "returned"
        if cash > Decimal("0.005"):
            return "partially_paid"
        return "overdue" if inv.due_date and inv.due_date < timezone.localdate() else "posted"

    @staticmethod
    def reversal_blockers(inv):
        """
        What makes reversing (reopening, cancelling, Customer rejected) this invoice wrong (review, 30 Oct 2026):
        a return against it — posted, or a draft that could be posted after the sale is gone; and a manufacturer
        claim on it settled by a PAYMENT (a receipt from the manufacturer, or a deduction on a supplier payment),
        whose journal would stay while the claim's own came off. A claim netted against payables is fine: its
        net-off is the claim's own entry and comes off with it.
        """
        from apps.accounting.models import PaymentAllocation

        live = list(CreditNote.objects.filter(invoice=inv).exclude(status="cancelled").values_list("number", "status"))
        if live:
            drafts = [n for n, st in live if st == "draft"]
            if drafts:
                raise DomainError(f"Return {', '.join(drafts)} is drafted against {inv.number} — delete that draft first.", code="draft_return")
            raise DomainError(f"Goods of {inv.number} were already returned ({', '.join(n for n, _ in live)}) — correct it with another return.", code="has_returns")
        claims = [str(c) for c in inv.company_claims.exclude(status="cancelled").values_list("pk", flat=True)]
        paid = PaymentAllocation.objects.filter(document_type="company_claim", document_id__in=claims).exclude(payment__status="cancelled")
        if claims and paid.exists():
            raise DomainError(f"The manufacturer's claim on {inv.number} was settled by a payment — reverse that payment first, or record a return.", code="claim_paid")

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
    def other_tax_context(doc, lines):
        """
        FED / Extra Tax / Further Tax / WHT on a printed tax document (owner 26 Oct 2026). Each line gets
        `other_tax` (FED + Extra + Further — inside its Line Total) and `other_label` naming the rates; the
        footer gets the guide's header figures: Total Tax = GST + FED + Extra + Further, Grand Total = Net +
        Total Tax, and WHT — withheld by the buyer — taken off to reach Total Payable (= doc.total).
        """
        for l in lines:
            l.other_tax = (l.fed_amount or 0) + (l.extra_tax_amount or 0) + (l.further_tax_amount or 0)
            p = l.variant.product if l.variant_id else None
            parts = []
            if p is not None:
                for label, rate, amt in (("FED", p.fbr_fed_rate, l.fed_amount), ("Extra", p.fbr_extra_tax_rate, l.extra_tax_amount),
                                         ("Further", p.fbr_further_tax_rate, l.further_tax_amount)):
                    if amt and rate:
                        parts.append(f"{label} {Decimal(rate).normalize():f}%")
            l.other_label = " · ".join(parts)
        gst = (doc.tax1_total or Decimal("0")) + (doc.tax2_total or Decimal("0"))
        fed, extra, further, wht = doc.fed_total or 0, doc.extra_tax_total or 0, doc.further_tax_total or 0, doc.wht_total or 0
        return {"show_other": any(l.other_tax for l in lines), "fed_total": q4(fed), "extra_total": q4(extra),
                "further_total": q4(further), "wht_total": q4(wht), "total_tax": q4(gst + fed + extra + further),
                "grand_total": q4((doc.total or Decimal("0")) + wht)}

    @staticmethod
    def present_lines(inv, lines):
        """
        How the printed invoice reads. A plain invoice (owner, 27 Oct 2026 — superseding the 21 Oct per-line
        product discount): "when no tax then show discount on total" — each line is Qty × Rate (`gross`), and
        the footer reads Sub Total → Total Discount (every discount, product's own and customer's alike) →
        Grand Total → Total Payable. A TAX invoice (owner's layout, 26 Oct 2026 — Item · Qty · Rate · Disc ·
        Taxable · GST · Total): the Disc cell carries the line's WHOLE discount, because Taxable =
        Qty × Rate − Disc is the figure the GST was computed on and filed with FBR; the page must foot the
        way the filing reads.
        """
        customer_part = gross_total = taxable_total = Decimal("0")
        tax_shown = (inv.tax1_total or Decimal("0")) + (inv.tax2_total or Decimal("0"))
        # the column shows whenever the switch is on — zeros included, so a product missing its Tax % is
        # visible rather than silently untaxed
        tax_on = bool(inv.force_tax_type_id) or tax_shown > 0
        for l in lines:
            gross = q4(l.qty * l.unit_price)
            l.gross = gross
            prod = q4(gross * (l.product_discount_pct or 0) / 100)
            prod = min(prod, l.discount_amount or Decimal("0"))
            cust = (l.discount_amount or Decimal("0")) - prod
            l.prod_discount = prod
            l.taxable = gross - (l.discount_amount or Decimal("0"))
            l.tax_amount = (l.tax1_amount or Decimal("0")) + (l.tax2_amount or Decimal("0"))
            # with tax on the page, the Amount column is pre-tax so Subtotal − Discount + Tax = Total still
            # checks with a pencil; without tax it stays tax-inclusive as before
            l.display_amount = (l.line_total or Decimal("0")) + cust - (l.tax_amount if tax_on else Decimal("0"))
            customer_part += cust
            gross_total += gross
            taxable_total += l.taxable
        tax_label = ""
        if tax_on and inv.tax1_type_id:
            # one shared % may be quoted beside the name; mixed FBR rates show per line instead
            rates = {l.tax1_rate for l in lines if l.tax1_rate}
            rate = rates.pop() if len(rates) == 1 else None
            tax_label = f"{inv.tax1_type.name}{f' ({rate.normalize()}%)' if rate else ''}"
        return {"customer_discount": q4(customer_part), "tax_shown": q4(tax_shown), "tax_on": tax_on, "tax_label": tax_label,
                "gst_pct": gst_pct(l.tax1_rate for l in lines),
                "gross_total": q4(gross_total), "taxable_total": q4(taxable_total),
                "display_subtotal": q4((inv.total or Decimal("0")) + customer_part - tax_shown),
                **InvoiceService.other_tax_context(inv, lines)}

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
        # entries held by a reopen that the corrected invoice no longer needs (a claim or COGS that fell away)
        PostingService.purge_held(inv)
        for claim in inv.company_claims.all():
            PostingService.purge_held(claim)
        # cash collected on delivery while this invoice was still a draft sits on the customer's account,
        # tagged with the delivery note — it settles the invoice now that the invoice is on the books
        if inv.delivery_note_id:
            from apps.accounting.models import Payment
            from apps.accounting.services import PaymentService

            for pay in Payment.objects.filter(delivery_note_id=inv.delivery_note_id, direction="in", status="posted", unallocated__gt=0).order_by("date"):
                inv.refresh_from_db(fields=["paid_amount", "status", "total"])
                if inv.balance <= 0:
                    break
                PaymentService.allocate(pay, [{"document_type": "sales_invoice", "document_id": str(inv.pk), "amount": min(pay.unallocated, inv.balance)}])
        # credit the customer kept from a return ("keep as credit") settles their next invoice
        CreditNoteService.auto_apply(inv)
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
        Edit a POSTED invoice (owner, 24 Oct 2026). Every journal this invoice put on the books — its posting, its
        COGS, its manufacturer claims and their net-offs — is HELD: taken off the books as a draft under its own
        number, and posting the corrected invoice rewrites those same entries in place (owner, 27 Oct 2026: "when
        edit invoice and save again it create multiple journal entries, instead it should update the existing").
        Only an entry in a closed period is reversed instead. The claims are cancelled until the re-post revives
        them. Money already received blocks it: remove the receipts first, or use a credit note.
        """
        from apps.accounting.models import JournalEntry
        from apps.accounting.services import PostingService

        if inv.status == "draft":
            return inv
        if inv.status == "cancelled":
            raise DomainError("A cancelled invoice cannot be reopened — raise a fresh invoice.")
        CreditNoteService.unapply_foreign(inv)
        InvoiceService.reversal_blockers(inv)
        if inv.credited_amount > 0:
            raise DomainError(f"A return already settles {inv.credited_amount:,.2f} of this invoice — it cannot be reopened. Correct it with another return.", code="has_returns")
        if inv.paid_amount > 0 or inv.status in ("partially_paid", "paid"):
            raise DomainError("Money has been received against this invoice — remove the receipts first, or correct it with a credit note.", code="has_payments")
        filing = getattr(inv, "fbr_filing", None)
        if filing is not None and filing.status == "submitted":
            raise DomainError("This invoice is filed with FBR and must stay exactly as filed — correct it with a credit note.", code="fbr_submitted")
        memo = f"Invoice {inv.number} reopened for editing"
        for claim in inv.company_claims.exclude(status="cancelled"):
            for je in JournalEntry.objects.filter(source_type="CompanyClaim", source_id=str(claim.pk), status="posted"):
                PostingService.hold(je, memo=memo)
            claim.status, claim.paid_amount = "cancelled", Decimal("0")
            claim.save(update_fields=["status", "paid_amount", "updated_at"])
        for je in JournalEntry.objects.filter(source_type="SalesInvoice", source_id=str(inv.pk), status="posted"):
            PostingService.hold(je, memo=memo)
        inv.journal = None
        inv.status = "draft"
        inv.save(update_fields=["journal", "status", "updated_at"])
        AuditService.log("invoice.reopened", inv, after={"reason": reason}, actor=actor)
        return inv

    @staticmethod
    @transaction.atomic
    def cancel(inv, reason=""):
        if inv.status not in ("draft", "cancelled"):
            CreditNoteService.unapply_foreign(inv)
        InvoiceService.reversal_blockers(inv)
        if inv.credited_amount > 0:
            raise DomainError(f"A return already settles {inv.credited_amount:,.2f} of {inv.number} — it cannot be cancelled.", code="has_returns")
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
        # a reopened invoice's entries are HELD (drafts, off the books): cancelled, they have nothing to come back for
        PostingService.purge_held(inv)
        for claim in inv.company_claims.all():
            PostingService.purge_held(claim)
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


def returned_company_shares(cn):
    """
    The manufacturer's share of the discount on the goods a return brings back, per supplier: {supplier_id: (claim,
    share)} — the returned list value x the claim's company %, never more than the claim. The return's journal
    credits it back to the incentive head and the claw-back reduces the claim by it, so the two always agree.
    """
    from collections import defaultdict

    inv = cn.invoice
    if inv is None:
        return {}
    claims = {c.supplier_id: c for c in inv.company_claims.exclude(status__in=("cancelled", "written_off"))}
    if not claims:
        return {}
    base_by_supplier = defaultdict(Decimal)
    for l in cn.lines.select_related("variant__product__supplier", "variant__product__brand__supplier"):
        product = l.variant.product
        supplier = product.supplier or (product.brand.supplier if product.brand_id else None)
        if supplier is not None and supplier.pk in claims:
            base_by_supplier[supplier.pk] += q4(l.qty * l.unit_price)
    out = {}
    for supplier_id, base in base_by_supplier.items():
        claim = claims[supplier_id]
        share = min(q4(base * claim.company_pct / 100), claim.total)
        if share > 0:
            out[supplier_id] = (claim, share)
    # never more than the discount the return actually reverses (a hand-edited line can carry less)
    total = sum((sh for _c, sh in out.values()), Decimal("0"))
    if total > cn.discount:
        scale = (cn.discount / total) if total else Decimal("0")
        out = {k: (c, q4(sh * scale)) for k, (c, sh) in out.items()}
    return out


def _claw_back_claims(cn):
    """
    Keune does not fund goods that came home (owner, 21 Oct 2026). For each manufacturer claim on the
    credited invoice, the company share of the returned base is clawed back: an OPEN claim shrinks (Dr the
    Distribution Margin head / Cr the claims receivable), a SETTLED one puts the payable back up (Dr the
    margin head / Cr Trade Payables) — the mirror of how it was taken.
    """
    from apps.accounting.services import Line, PostingService, account_for

    if cn.invoice is None:
        return
    inc = account_for("manufacturer_incentive", "company_claim")
    recv = account_for("manufacturer_claim", "company_claim")
    for supplier_id, (claim, share) in returned_company_shares(cn).items():
        inv = cn.invoice
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
    """
    Returns (owner, 30 Oct 2026: "do research and make plan … make sure update stock, inventory and accounting").

    A return against an invoice credits exactly what that invoice charged for the goods coming back — per variant,
    in proportion to the units returned: the price, the discount (its own and the manufacturer's share), every tax,
    and the cost of goods at the sale's cost. It can never return more than was sold less what was already
    returned. On posting, the credit settles the invoice's open balance as CREDIT, never as money (the invoice
    reads Returned, not Paid); whatever is left is the customer's credit — kept for their next invoice, applied to
    another open invoice now, or refunded in cash, by bank transfer or by cheque.
    """

    OTHER = ("fed_amount", "extra_tax_amount", "further_tax_amount", "wht_amount")

    @staticmethod
    def _sold(invoice):
        """What the invoice charged per variant: units (paid + free), gross, discount, each tax, cost."""
        sold = {}
        for il in invoice.lines.all():
            c = sold.setdefault(il.variant_id, {"units": Decimal("0"), "gross": Decimal("0"), "disc": Decimal("0"), "cost": Decimal("0"),
                                                "t1": Decimal("0"), "t2": Decimal("0"), "tt1": None, "r1": None, "tt2": None, "r2": None,
                                                **{k: Decimal("0") for k in CreditNoteService.OTHER}})
            units = il.qty + (il.free_qty or Decimal("0"))
            c["units"] += units
            c["gross"] += q4(il.qty * il.unit_price)
            c["disc"] += il.discount_amount
            c["cost"] += q4(units * (il.unit_cost or Decimal("0")))
            c["t1"] += il.tax1_amount
            c["t2"] += il.tax2_amount
            for k in CreditNoteService.OTHER:
                c[k] += getattr(il, k)
            c["tt1"], c["r1"] = c["tt1"] or il.tax1_type, c["r1"] if c["r1"] is not None else il.tax1_rate
            c["tt2"], c["r2"] = c["tt2"] or il.tax2_type, c["r2"] if c["r2"] is not None else il.tax2_rate
        return sold

    @staticmethod
    def _taken(invoice, exclude=None):
        out = {}
        qs = CreditNoteLine.objects.filter(credit_note__invoice=invoice).exclude(credit_note__status="cancelled")
        if exclude is not None:
            qs = qs.exclude(credit_note=exclude)
        for l in qs:
            d = out.setdefault(l.variant_id, {"units": Decimal("0"), "gross": Decimal("0"), "disc": Decimal("0"), "t1": Decimal("0"), "t2": Decimal("0"),
                                              **{k: Decimal("0") for k in CreditNoteService.OTHER}})
            in_line = l.fed_amount + l.extra_tax_amount + l.further_tax_amount
            d["units"] += l.qty
            d["gross"] += l.line_total - l.tax1_amount - l.tax2_amount - in_line + l.discount_amount
            d["disc"] += l.discount_amount
            d["t1"] += l.tax1_amount
            d["t2"] += l.tax2_amount
            for k in CreditNoteService.OTHER:
                d[k] += getattr(l, k)
        return out

    @staticmethod
    def returned_units(invoice, exclude=None):
        """Units already returned against this invoice, per variant (posted returns; drafts count too, so two
        drafts cannot both claim the same goods)."""
        out = {}
        qs = CreditNoteLine.objects.filter(credit_note__invoice=invoice).exclude(credit_note__status="cancelled")
        if exclude is not None:
            qs = qs.exclude(credit_note=exclude)
        for l in qs:
            out[l.variant_id] = out.get(l.variant_id, Decimal("0")) + l.qty
        return out

    @staticmethod
    @transaction.atomic
    def create(*, customer, lines, invoice=None, warehouse=None, date=None, reason="", notes="", applied_to_invoice=True):
        from apps.fbr.engine import round2

        if invoice is not None:
            CreditNoteService._invoice_can_take_returns(invoice)
        cn = CreditNote.objects.create(number=next_number("credit_note"), customer=customer, invoice=invoice, warehouse=warehouse, date=date or timezone.localdate(), reason=reason, notes=notes, applied_to_invoice=applied_to_invoice)
        sold = CreditNoteService._sold(invoice) if invoice is not None else {}
        back = CreditNoteService.returned_units(invoice, exclude=cn) if invoice is not None else {}
        asked = {}
        for l in lines:
            asked[l["variant"].id] = asked.get(l["variant"].id, Decimal("0")) + Decimal(str(l["qty"]))
        for vid, qty in asked.items():
            if qty <= 0:
                raise DomainError("A returned quantity must be more than zero.", code="bad_qty")
            if invoice is not None:
                have = sold.get(vid, {}).get("units", Decimal("0")) - back.get(vid, Decimal("0"))
                if vid not in sold:
                    name = next(l["variant"].name for l in lines if l["variant"].id == vid)
                    raise DomainError(f"{name} is not on {invoice.number}.", code="not_on_invoice")
                if qty > have:
                    name = next(l["variant"].name for l in lines if l["variant"].id == vid)
                    n = lambda x: format(Decimal(x).normalize(), "f")   # 2.000 → "2", 1.500 → "1.5"
                    raise DomainError(f"{name}: {n(qty)} returned but only {n(max(have, Decimal('0')))} of {invoice.number} is left to return "
                                      f"({n(sold[vid]['units'])} sold, {n(back.get(vid, Decimal('0')))} already returned).", code="over_return")

        if invoice is not None:
            CreditNoteService._goods_already_back(invoice, asked)
        # units already back in stock without a return (refused at the door, or the order was cancelled) are
        # returned on lines of their own that move no stock
        left_back = {vid: q - CreditNoteService.stock_back_units(invoice, exclude=cn).get(vid, Decimal("0"))
                     for vid, q in CreditNoteService.back_already(invoice).items()} if invoice is not None else {}
        parts = []
        for l in lines:
            qty = Decimal(str(l["qty"]))
            sb = min(qty, max(left_back.get(l["variant"].id, Decimal("0")), Decimal("0")))
            if sb > 0:
                left_back[l["variant"].id] -= sb
                parts.append({**l, "qty": sb, "stock_back": True, "restock": True, "condition": "good"})
            if qty - sb > 0:
                parts.append({**l, "qty": qty - sb, "stock_back": False})

        subtotal = disc = t1 = t2 = Decimal("0")
        sums = {k: Decimal("0") for k in CreditNoteService.OTHER}
        tax1_type = tax2_type = None
        taken = CreditNoteService._taken(invoice, exclude=cn) if invoice is not None else {}
        for l in parts:
            variant, qty = l["variant"], Decimal(str(l["qty"]))
            hit = sold.get(variant.id)
            other = {k: Decimal("0") for k in CreditNoteService.OTHER}
            if hit is not None and hit["units"]:
                # the share of what the invoice charged for these units — price, discount, taxes and cost alike;
                # the LAST of an item takes exactly what is left, so partial returns never leave a paisa behind
                done = taken.setdefault(variant.id, {"units": Decimal("0"), "gross": Decimal("0"), "disc": Decimal("0"), "t1": Decimal("0"), "t2": Decimal("0"),
                                                     **{k: Decimal("0") for k in CreditNoteService.OTHER}})
                if qty >= hit["units"] - done["units"]:
                    gross, line_disc = hit["gross"] - done["gross"], hit["disc"] - done["disc"]
                    amt1, amt2 = hit["t1"] - done["t1"], hit["t2"] - done["t2"]
                    other = {k: hit[k] - done[k] for k in CreditNoteService.OTHER}
                else:
                    ratio = qty / hit["units"]
                    gross, line_disc = q4(hit["gross"] * ratio), q4(hit["disc"] * ratio)
                    amt1, amt2 = round2(hit["t1"] * ratio), round2(hit["t2"] * ratio)
                    other = {k: round2(hit[k] * ratio) for k in CreditNoteService.OTHER}
                done["units"] += qty
                done["gross"] += gross
                done["disc"] += line_disc
                done["t1"] += amt1
                done["t2"] += amt2
                for k in CreditNoteService.OTHER:
                    done[k] += other[k]
                unit_price = q4(gross / qty) if qty else Decimal("0")
                unit_cost = q4(hit["cost"] / hit["units"])
                tt1, r1 = hit["tt1"], hit["r1"] or Decimal("0")
                tt2, r2 = hit["tt2"], hit["r2"] or Decimal("0")
            else:
                # a standalone return (no invoice): the price typed, no discount, no tax — tax rides only on an
                # invoice's tax switch (owner, 25 Oct 2026)
                unit_price = q4(l["unit_price"])
                gross, line_disc = q4(qty * unit_price), Decimal("0")
                unit_cost = q4(l.get("unit_cost") or variant.avg_cost or variant.cost_price)
                tt1, r1, amt1, tt2, r2, amt2 = None, Decimal("0"), Decimal("0"), None, Decimal("0"), Decimal("0")
            in_line = other["fed_amount"] + other["extra_tax_amount"] + other["further_tax_amount"]
            CreditNoteLine.objects.create(
                credit_note=cn, variant=variant, batch=l.get("batch"), qty=qty, unit_price=unit_price, unit_cost=unit_cost,
                reason=l.get("reason", ""), restock=l.get("restock", True), condition=l.get("condition", "good"), discount_amount=line_disc,
                stock_back=l.get("stock_back", False),
                line_total=gross - line_disc + amt1 + amt2 + in_line,
                tax1_type=tt1 if amt1 else None, tax1_rate=r1, tax1_amount=amt1, tax2_type=tt2 if amt2 else None, tax2_rate=r2, tax2_amount=amt2, **other)
            subtotal += gross
            disc += line_disc
            t1 += amt1
            t2 += amt2
            for k in CreditNoteService.OTHER:
                sums[k] += other[k]
            tax1_type = tax1_type or (tt1 if amt1 else None)
            tax2_type = tax2_type or (tt2 if amt2 else None)
        cn.subtotal, cn.discount, cn.tax1_total, cn.tax2_total, cn.tax1_type, cn.tax2_type = subtotal, disc, t1, t2, tax1_type, tax2_type
        cn.fed_total, cn.extra_tax_total = sums["fed_amount"], sums["extra_tax_amount"]
        cn.further_tax_total, cn.wht_total = sums["further_tax_amount"], sums["wht_amount"]
        cn.total = subtotal - disc + t1 + t2 + cn.fed_total + cn.extra_tax_total + cn.further_tax_total - cn.wht_total
        cn.save()
        AuditService.log("created", cn, after={"customer": customer.name, "total": str(cn.total)})
        return cn

    @staticmethod
    def _invoice_can_take_returns(invoice):
        """A return needs a posted invoice whose goods reached the customer — not one still on its way."""
        if invoice.status in ("draft", "cancelled"):
            raise DomainError(f"{invoice.number} is {invoice.get_status_display().lower()} — a return needs a posted invoice. A draft is simply edited.", code="not_posted")
        dn = invoice.delivery_note
        if dn is not None and dn.stock_out_done and dn.status in ("pending", "loaded", "dispatched"):
            raise DomainError(f"The goods of {invoice.number} have not been delivered yet ({dn.number} is {dn.get_status_display().lower()}) — "
                              "record the delivery or the refusal first; if the customer refused them, use Customer rejected.", code="not_delivered")

    @staticmethod
    def back_already(invoice):
        """
        Units of this invoice already back in stock without any return, per variant: the whole invoice when its
        order or its delivery note was cancelled (the issue was reversed); the good units the customer refused at
        the door otherwise (the trip brings them home and its close restocks them).
        """
        o, dn = invoice.order, invoice.delivery_note
        sold = CreditNoteService._sold(invoice)
        if (o is not None and o.status in ("cancelled", "rejected")) or (dn is not None and dn.status == "cancelled"):
            return {vid: c["units"] for vid, c in sold.items()}
        out = {}
        # refused at the door: only units the invoice BILLED but the customer did not keep (an invoice drafted at
        # delivery bills only what was delivered), and only when a trip brings them home — its close restocks the
        # good ones; with no trip nothing restocks them, so the return must (review, 30 Oct 2026)
        if dn is not None and dn.trip_id is not None:
            delivered, refused_good = {}, {}
            for l in dn.lines.all():
                delivered[l.variant_id] = delivered.get(l.variant_id, Decimal("0")) + l.qty_delivered
                if l.qty_returned > 0 and l.return_condition != "damaged":
                    refused_good[l.variant_id] = refused_good.get(l.variant_id, Decimal("0")) + l.qty_returned
            for vid, units in refused_good.items():
                billed_not_kept = sold.get(vid, {}).get("units", Decimal("0")) - delivered.get(vid, Decimal("0"))
                if billed_not_kept > 0:
                    out[vid] = min(units, billed_not_kept)
        return out

    @staticmethod
    def stock_back_units(invoice, exclude=None):
        out = {}
        qs = CreditNoteLine.objects.filter(credit_note__invoice=invoice, stock_back=True).exclude(credit_note__status="cancelled")
        if exclude is not None:
            qs = qs.exclude(credit_note=exclude)
        for l in qs:
            out[l.variant_id] = out.get(l.variant_id, Decimal("0")) + l.qty
        return out

    @staticmethod
    def _goods_already_back(invoice, asked=None):
        """
        Goods that came back without a return — and nothing paid, nothing filed with FBR — are not a return at
        all: the sale never happened, so Customer rejected reverses it (TDN-SO-00076, 29 Oct 2026, was returned
        AND cancelled, and its stock counted twice). A paid or FBR-filed invoice still takes a return; its lines
        for those goods move no stock.
        """
        filing = getattr(invoice, "fbr_filing", None)
        filed = filing is not None and filing.status == "submitted"
        if invoice.paid_amount > 0 or filed:
            return
        back = CreditNoteService.back_already(invoice)
        sold = CreditNoteService._sold(invoice)
        if back and all(back.get(vid, Decimal("0")) >= c["units"] for vid, c in sold.items()):
            o, dn = invoice.order, invoice.delivery_note
            what = f"order {o.number} was cancelled" if o is not None and o.status in ("cancelled", "rejected") else f"the customer refused {dn.number}"
            raise DomainError(f"The goods of {invoice.number} already came back ({what}) and nothing was paid — use Customer rejected "
                              "on the invoice to reverse the sale. A return would count the goods in stock twice.", code="goods_already_back")

    @staticmethod
    @transaction.atomic
    def post(cn):
        if cn.status != "draft":
            raise DomainError("Credit note already posted.")
        from apps.accounting.services import PostingService
        from apps.inventory.models import Warehouse

        if cn.invoice_id:
            inv = cn.invoice
            CreditNoteService._invoice_can_take_returns(inv)
            CreditNoteService._goods_already_back(inv)
            sold, back = CreditNoteService._sold(inv), CreditNoteService.returned_units(inv, exclude=cn)
            mine = {}
            for l in cn.lines.all():
                mine[l.variant_id] = mine.get(l.variant_id, Decimal("0")) + l.qty
            for vid, q in mine.items():
                if q > sold.get(vid, {}).get("units", Decimal("0")) - back.get(vid, Decimal("0")):
                    raise DomainError(f"{cn.number} returns more than is left of {inv.number} — another return took some of it since this draft was saved. "
                                      "Delete the draft and record the return again.", code="over_return")
        wh = cn.warehouse or Warehouse.objects.filter(type="main").first()
        quarantine = Warehouse.objects.filter(type="quarantine", is_active=True).first()
        for line in cn.lines.select_related("variant", "variant__product", "batch"):
            if line.stock_back:
                continue  # already in stock — refused at the door or the order cancelled; the cost still goes back
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
        # the credit settles its own invoice first — as credit, never as money
        if cn.applied_to_invoice and cn.invoice_id:
            inv = SalesInvoice.objects.get(pk=cn.invoice_id)
            if inv.status in ("posted", "partially_paid", "overdue"):
                CreditNoteService._apply(cn, inv, min(cn.available_credit, inv.balance))
        for line in cn.lines.select_related("variant"):
            ol = SalesOrderLine.objects.filter(order=cn.invoice.order if cn.invoice_id and cn.invoice.order_id else None, variant=line.variant).first() if cn.invoice_id else None
            if ol:
                ol.qty_returned += line.qty
                ol.save(update_fields=["qty_returned"])
        _claw_back_claims(cn)
        AuditService.log("status.posted", cn, after={"total": str(cn.total), "customer_credit": str(cn.available_credit)})
        return cn

    @staticmethod
    def _apply(cn, inv, amount):
        """Settle `amount` of an open invoice with this return's credit. No journal: both sides are the customer's
        receivable — the return already credited it; this only says which invoice the credit paid."""
        amount = q4(amount)
        if amount <= 0:
            return
        if inv.status not in ("posted", "partially_paid", "overdue"):
            raise DomainError(f"{inv.number} is {inv.get_status_display().lower()} — credit settles only an open invoice.", code="not_open")
        if amount > cn.available_credit + Decimal("0.005"):
            raise DomainError(f"{cn.number} has only {cn.available_credit:,.2f} of credit left.", code="no_credit")
        if amount > inv.balance + Decimal("0.005"):
            raise DomainError(f"{inv.number} has only {inv.balance:,.2f} left to settle.", code="over_balance")
        CreditNoteApplication.objects.create(credit_note=cn, invoice=inv, amount=amount, date=timezone.localdate())
        cn.applied_amount = q4(cn.applied_amount + amount)
        cn.save(update_fields=["applied_amount", "updated_at"])
        inv.paid_amount = q4(inv.paid_amount + amount)
        inv.credited_amount = q4(inv.credited_amount + amount)
        inv.status = InvoiceService.settle_status(inv)
        inv.save(update_fields=["paid_amount", "credited_amount", "status", "updated_at"])
        AuditService.log("credit.applied", inv, after={"credit_note": cn.number, "amount": str(amount)})

    @staticmethod
    @transaction.atomic
    def apply(cn, allocations):
        """Use a return's credit on the customer's other open invoices: [{invoice, amount}]."""
        if cn.status != "posted":
            raise DomainError("Post the return first.")
        for a in allocations or []:
            inv = SalesInvoice.objects.filter(pk=a.get("invoice"), customer_id=cn.customer_id).first()
            if inv is None:
                raise DomainError("That invoice is not this customer's.", status_code=400)
            if inv.status not in ("posted", "partially_paid", "overdue"):
                raise DomainError(f"{inv.number} is not open ({inv.get_status_display()}).", code="not_open")
            CreditNoteService._apply(cn, inv, Decimal(str(a.get("amount") or 0)))
        return cn

    @staticmethod
    def auto_apply(inv):
        """A newly posted invoice takes the customer's kept credit first — "keep as credit for the next invoice"."""
        from django.db.models import F

        with_credit = (CreditNote.objects.filter(customer_id=inv.customer_id, status="posted")
                       .annotate(left=F("total") - F("applied_amount") - F("refunded_amount")).filter(left__gt=0).order_by("date", "created_at"))
        for cn in with_credit:
            inv.refresh_from_db(fields=["paid_amount", "credited_amount", "status", "total"])
            if inv.balance <= 0 or inv.status not in ("posted", "partially_paid", "overdue"):
                break
            CreditNoteService._apply(cn, inv, min(cn.available_credit, inv.balance))

    @staticmethod
    def unapply_foreign(inv):
        """
        Take back credit that OTHER returns put on this invoice — kept credit auto-applied when it posted — so it can
        still be reversed: the credit goes back to those returns, to be used again (review, 30 Oct 2026). A return of
        this invoice's own goods is not undone: it stays, and blocks the reversal.
        """
        notes = {}   # one copy per return: two applications of the same return must both come off it
        for a in list(CreditNoteApplication.objects.filter(invoice=inv).exclude(credit_note__invoice=inv).select_related("credit_note")):
            cn = notes.setdefault(a.credit_note_id, a.credit_note)
            cn.applied_amount = max(q4(cn.applied_amount - a.amount), Decimal("0"))
            cn.save(update_fields=["applied_amount", "updated_at"])
            inv.paid_amount = q4(inv.paid_amount - a.amount)
            inv.credited_amount = q4(inv.credited_amount - a.amount)
            a.delete()
            AuditService.log("credit.unapplied", inv, after={"credit_note": cn.number, "amount": str(a.amount)})
        inv.status = InvoiceService.settle_status(inv) if inv.status not in ("draft", "cancelled") else inv.status
        inv.save(update_fields=["paid_amount", "credited_amount", "status", "updated_at"])

    @staticmethod
    @transaction.atomic
    def refund(cn, *, amount, method, cash_account=None, bank_account=None, cheque_no="", cheque_date=None, reference="", date=None, actor=None):
        """
        Pay a return's credit back to the customer — cash, bank transfer or cheque (owner, 30 Oct 2026: "give
        multiple options for customer amount return"). Dr the customer's receivable / Cr the cash or bank
        account (a cheque goes through Cheques Payable until it is presented, like any cheque we write).
        """
        from apps.accounting.services import PaymentService

        if cn.status != "posted":
            raise DomainError("Post the return first.")
        amount = q4(Decimal(str(amount or 0)))
        if amount <= 0:
            raise DomainError("Enter the amount to refund.", status_code=400)
        if amount > cn.available_credit + Decimal("0.005"):
            raise DomainError(f"{cn.number} has only {cn.available_credit:,.2f} of credit left to refund.", code="no_credit")
        if method not in ("cash", "bank_transfer", "cheque"):
            raise DomainError("Refund by cash, bank transfer or cheque.", status_code=400)
        if method in ("bank_transfer", "cheque") and bank_account is None:
            raise DomainError("Choose the bank account the refund is paid from.", status_code=400)
        # only the account the method uses: a bank picked and then switched to cash must not pay from the bank
        cash_account, bank_account = (cash_account, None) if method == "cash" else (None, bank_account)
        pay = PaymentService.record(direction="out", partner_type="customer", partner_id=str(cn.customer_id), partner_name=cn.customer.name,
                                    amount=amount, method=method, date_=date, cash_account=cash_account, bank_account=bank_account,
                                    cheque_no=cheque_no, cheque_date=cheque_date, reference=reference or f"Refund of {cn.number}",
                                    notes=f"Refund of return {cn.number}", allocations=[], auto_allocate=False)
        pay.credit_note = cn
        pay.unallocated = Decimal("0")   # matched to the return, not money on account
        pay.save(update_fields=["credit_note", "unallocated"])
        cn.refunded_amount = q4(cn.refunded_amount + amount)
        cn.save(update_fields=["refunded_amount", "updated_at"])
        AuditService.log("credit.refunded", cn, after={"amount": str(amount), "method": method, "payment": pay.number}, actor=actor)
        return pay


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
                if inv.status in ("draft", "cancelled"):
                    raise DomainError(f"{inv.number} is {inv.status} — post it before receiving money against it.", code="not_posted")
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
