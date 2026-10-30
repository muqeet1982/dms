"""Domain event handlers: notifications (push/in-app) and webhooks (§4 Notifications, §9.3)."""
from apps.core.events import on
from apps.core.notifications import notify, notify_permission


def _rep_user(order):
    return order.rep.user if order.rep_id and order.rep.user_id else None


@on("order.submitted")
def _order_submitted(order, **_):
    if order.source in ("rep_app", "api", "portal"):
        notify_permission("sales.confirm_sales_order", "order.submitted", f"New order {order.number} from {order.customer.name}", f"{order.rep.name if order.rep else 'External'} · {order.total:.2f}", {"order_id": str(order.id), "screen": "order"})
    # the sale person gets a receipt of their own order — it matters most when an offline day syncs in late
    u = _rep_user(order)
    if u:
        notify(u, "order.received", f"Order {order.number} received", f"{order.customer.name} · {order.total:.2f}", {"order_id": str(order.id), "screen": "order"})


@on("order.confirmed")
def _order_confirmed(order, **_):
    u = _rep_user(order)
    if u:
        notify(u, "order.confirmed", f"Order {order.number} confirmed", f"{order.customer.name} · {order.total:.2f}", {"order_id": str(order.id), "screen": "order"})
    _webhook("order.confirmed", order)


@on("order.rejected")
def _order_rejected(order, **_):
    u = _rep_user(order)
    if u:
        notify(u, "order.rejected", f"Order {order.number} rejected", order.rejection_reason, {"order_id": str(order.id), "screen": "order"})
    _webhook("order.rejected", order)


@on("order.dispatched")
def _order_dispatched(order, **_):
    u = _rep_user(order)
    if u:
        notify(u, "order.dispatched", f"Order {order.number} dispatched", f"{order.customer.name}", {"order_id": str(order.id), "screen": "order"})
    _webhook("order.dispatched", order)


@on("order.delivered")
def _order_delivered(order, **_):
    # the office hears a delivery too, not just the rep — the owner reads these in the bell (21 Oct 2026)
    notify_permission("delivery.view_all_trips", "delivery.completed", f"Delivered: {order.number}",
                      f"{order.customer.name} · {order.total:.2f}", {"order_id": str(order.id), "screen": "order"})
    u = _rep_user(order)
    if u:
        notify(u, "delivery.completed", f"Order {order.number} delivered", f"{order.customer.name}", {"order_id": str(order.id), "screen": "order"})
    _webhook("order.delivered", order)


@on("delivery.assigned")
def _delivery_assigned(trip, **_):
    if trip.driver_id and trip.driver.user_id:
        notify(trip.driver.user, "delivery.assigned", f"Trip {trip.number} assigned to you", f"{trip.date:%d/%m/%Y} · {trip.stops_count} stops", {"trip_id": str(trip.id), "screen": "trip"})
    # a trip reaches a rep's phone only when it carries one of their own orders — otherwise it is the driver's news
    if trip.rep_id and trip.rep.user_id and trip.orders.filter(rep_id=trip.rep_id).exists():
        notify(trip.rep.user, "delivery.assigned", f"Trip {trip.number} scheduled", f"{trip.date:%d/%m/%Y} · {trip.stops_count} stops", {"trip_id": str(trip.id), "screen": "trip"})


@on("order.cancelled")
def _order_cancelled(order, **_):
    u = _rep_user(order)
    if u:
        notify(u, "order.cancelled", f"Order {order.number} cancelled", order.rejection_reason or "", {"order_id": str(order.id), "screen": "order"})
    _webhook("order.cancelled", order)


@on("delivery.failed")
def _delivery_failed(delivery_note, reason="", **_):
    order = delivery_note.order
    u = _rep_user(order)
    if u:
        notify(u, "delivery.failed", f"Delivery failed: {order.number}", reason or delivery_note.remarks or "", {"order_id": str(order.id), "screen": "order"})
    # goods already invoiced did not reach the customer: the office decides on the order (owner, 30 Oct 2026) —
    # Deliver again (same goods, same invoice) or Customer rejected (the sale reversed). Never a return, which would
    # count the goods coming home with the trip in stock twice; never a second delivery, which would bill twice
    inv = getattr(delivery_note, "invoice", None)
    if inv is not None and inv.status not in ("cancelled", "draft"):
        from apps.core.notifications import notify_permission

        notify_permission("sales.delete_invoice", "invoice.customer_refused", f"{order.number}: {delivery_note.number} not delivered",
                          f"{order.customer.name}{' — ' + reason if reason else ''}. Open {order.number}: Deliver again sends the same goods and "
                          f"invoice {inv.number} out again; Customer rejected reverses the sale.",
                          {"invoice_id": str(inv.id), "order_id": str(order.id), "screen": "order"})


@on("delivery.completed")
def _delivery_completed(delivery_note, invoice=None, **_):
    _webhook("delivery.completed", delivery_note)
    if invoice is not None:
        _webhook("invoice.created", invoice)


@on("payment.received")
def _payment_received(payment, **_):
    _webhook("payment.received", payment)


@on("grn.posted")
def _grn_posted(grn, **_):
    pass


def _webhook(event, obj):
    try:
        from apps.integrations.services import WebhookService

        WebhookService.dispatch(event, obj)
    except Exception:  # pragma: no cover
        import logging

        logging.getLogger(__name__).exception("webhook dispatch failed")
