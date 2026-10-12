"""Domain event handlers: notifications (push/in-app) and webhooks (§4 Notifications, §9.3)."""
from apps.core.events import on
from apps.core.notifications import notify, notify_permission


def _rep_user(order):
    return order.rep.user if order.rep_id and order.rep.user_id else None


@on("order.submitted")
def _order_submitted(order, **_):
    if order.source in ("rep_app", "api", "portal"):
        notify_permission("sales.confirm_sales_order", "order.submitted", f"New order {order.number} from {order.customer.name}", f"{order.rep.name if order.rep else 'External'} · {order.total:.2f}", {"order_id": str(order.id), "screen": "order"})


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
    u = _rep_user(order)
    if u:
        notify(u, "delivery.completed", f"Order {order.number} delivered", f"{order.customer.name}", {"order_id": str(order.id), "screen": "order"})
    _webhook("order.delivered", order)


@on("delivery.assigned")
def _delivery_assigned(trip, **_):
    if trip.driver_id and trip.driver.user_id:
        notify(trip.driver.user, "delivery.assigned", f"Trip {trip.number} assigned to you", f"{trip.date:%d/%m/%Y} · {trip.stops_count} stops", {"trip_id": str(trip.id), "screen": "trip"})
    if trip.rep_id and trip.rep.user_id:
        notify(trip.rep.user, "delivery.assigned", f"Trip {trip.number} scheduled", f"{trip.date:%d/%m/%Y} · {trip.stops_count} stops", {"trip_id": str(trip.id), "screen": "trip"})


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
