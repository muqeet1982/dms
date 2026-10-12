"""
Order-to-delivery chain, as one answer the UI can render.

    Sales order → Load form → Warehouse issue → Invoice → Dispatch (vehicle, driver, helper)
    → Out for delivery → Delivery confirmation

Every step says where it stands, which document carries it, who did it and when, and what the next action is.
The steps are read from the documents themselves — nothing new is stored — so a chain that was built the old way
(deliver first, invoice after) reads correctly too.
"""
from apps.delivery.models import DeliveryNote, PickList


def _person(user):
    return getattr(user, "full_name", "") or getattr(user, "username", "") or ""


def _step(key, title, state, **kw):
    return {"key": key, "title": title, "state": state, "at": None, "by": "", "detail": "", "docs": [], "action": None, **kw}


def order_workflow(order):
    """[{key, title, state: done|current|todo|blocked|skipped, at, by, detail, docs, action}]"""
    load_forms = list(PickList.objects.filter(orders=order).order_by("created_at"))
    live_form = next((p for p in load_forms if p.status != "cancelled"), None)
    notes = list(DeliveryNote.objects.filter(order=order).select_related("trip", "invoice", "picklist").order_by("created_at"))
    issued = [n for n in notes if n.stock_out_done]
    invoices = list(order.invoices.all())
    trips = [n.trip for n in notes if n.trip_id]
    trip = trips[0] if trips else (order.trip if order.trip_id else None)
    delivered = [n for n in notes if n.status in ("delivered", "partially_delivered", "failed", "returned")]
    cancelled = order.status in ("cancelled", "rejected")
    steps = []

    # 1 · Sales order
    confirmed = bool(order.confirmed_at) or order.status not in ("draft", "submitted", "under_review", "on_hold_credit", "on_hold_stock")
    steps.append(_step(
        "order", "Sales order", "done" if confirmed else ("blocked" if cancelled else "current"),
        at=order.confirmed_at or order.created_at, by=_person(order.confirmed_by) or order.created_by_name if hasattr(order, "created_by_name") else _person(order.confirmed_by),
        detail=f"{order.lines.count()} item(s) · {order.get_status_display()}",
        docs=[{"label": order.number, "to": f"/app/sales/orders/{order.pk}"}],
        action=None if confirmed or cancelled else {"label": "Confirm order", "post": f"/api/v1/sales-orders/{order.pk}/confirm", "perm": "sales.confirm_sales_order"},
    ))

    # 2 · Load form → the warehouse
    if live_form is None:
        steps.append(_step(
            "load_form", "Load form", "current" if confirmed and not cancelled else "todo",
            detail="Tell the warehouse what to take out for this order.",
            action={"label": "Create load form", "post": f"/api/v1/sales-orders/{order.pk}/load-form", "perm": "delivery.add_load_form"} if confirmed and not cancelled else None,
        ))
    else:
        sent = live_form.status != "open"
        steps.append(_step(
            "load_form", "Load form", "done" if sent else "current",
            at=live_form.sent_at or live_form.created_at, by=_person(live_form.sent_by),
            detail=f"{live_form.warehouse.name} · {live_form.get_status_display()}",
            docs=[{"label": live_form.number, "to": f"/app/delivery/pick-lists/{live_form.pk}"}],
            action=None if sent else {"label": "Send to warehouse", "post": f"/api/v1/pick-lists/{live_form.pk}/send", "perm": "delivery.change_load_form"},
        ))

    # 3 · Warehouse issue (stock leaves)
    issue_done = bool(issued)
    issue_detail = ""
    if live_form is not None and live_form.status in ("issued", "partially_issued"):
        issue_detail = live_form.get_status_display()
    elif issue_done:
        issue_detail = "Stock already out of the warehouse"
    steps.append(_step(
        "issue", "Warehouse issue", "done" if issue_done else ("current" if live_form is not None and live_form.status in ("sent", "picking") else "todo"),
        at=live_form.issued_at if live_form is not None else None, by=_person(live_form.issued_by) if live_form is not None else "",
        detail=issue_detail or "The warehouse records what it hands over; stock drops when it is confirmed.",
        docs=[{"label": n.number, "to": f"/app/delivery/notes/{n.pk}"} for n in issued],
        action=(
            {"label": "Issue stock", "to": f"/app/delivery/pick-lists/{live_form.pk}", "perm": "delivery.issue_stock"}
            if live_form is not None and not issue_done and live_form.status in ("sent", "picking", "open") else None
        ),
    ))

    # 4 · Invoice
    invoiceable = next((n for n in issued if not getattr(n, "invoice", None)), None)
    steps.append(_step(
        "invoice", "Invoice", "done" if invoices else ("current" if issue_done else "todo"),
        at=invoices[0].created_at if invoices else None,
        detail=" · ".join(f"{i.number} {i.get_status_display()}" for i in invoices) or "Raised against what the warehouse issued.",
        docs=[{"label": i.number, "to": f"/app/sales/invoices/{i.pk}"} for i in invoices],
        action=(
            # `sales.add_sales_invoice` was not a codename anybody can hold, so this button was invisible to
            # everyone but the owner. The switch the endpoint actually checks is `sales.add_invoice`.
            {"label": "Create invoice", "post": f"/api/v1/sales-orders/{order.pk}/invoice", "perm": "sales.add_invoice",
             "alt": {"label": "Raise as draft to check first",
                     "post": f"/api/v1/sales-orders/{order.pk}/invoice", "body": {"post": False}}}
            if invoiceable is not None and not invoices else None
        ),
    ))

    # 5 · Dispatch — vehicle, driver, helper
    crew = []
    if trip is not None:
        crew = [x for x in [trip.vehicle.reg_no if trip.vehicle_id else "", trip.driver.name if trip.driver_id else "", _person(trip.helper)] if x]
    steps.append(_step(
        "dispatch", "Dispatch", "done" if trip is not None else ("current" if issue_done else "todo"),
        at=trip.created_at if trip is not None else None,
        detail=" · ".join(crew) or ("Assign the vehicle, driver and helper." if issue_done else "After the stock is issued."),
        docs=[{"label": trip.number, "to": f"/app/delivery/trips/{trip.pk}"}] if trip is not None else [],
        action=(
            {"label": "Create dispatch", "to": f"/app/delivery/trips/new?order={order.pk}&warehouse={order.warehouse_id or ''}", "perm": "delivery.create_trip"}
            if trip is None and issue_done else None
        ),
    ))

    # 6 · Out for delivery
    out = trip is not None and trip.status in ("out_for_delivery", "completed", "closed")
    steps.append(_step(
        "out_for_delivery", "Out for delivery", "done" if out else ("current" if trip is not None else "todo"),
        at=trip.started_at if trip is not None else None,
        detail=(trip.get_status_display() if trip is not None else "Starts when the vehicle leaves."),
        action=(
            {"label": "Start trip", "post": f"/api/v1/trips/{trip.pk}/start", "perm": "delivery.create_trip"}
            if trip is not None and trip.status in ("planned", "loading") else None
        ),
    ))

    # 7 · Delivery confirmation
    pending_note = next((n for n in notes if n.status in ("pending", "loaded", "dispatched")), None)
    done_note = delivered[0] if delivered else None
    steps.append(_step(
        "delivered", "Delivery confirmation", "done" if order.status in ("delivered", "invoiced") or done_note else ("current" if out else "todo"),
        at=done_note.delivered_at if done_note else order.delivered_at,
        by=_person(done_note.delivered_by) if done_note else "",
        detail=(
            f"{done_note.get_status_display()}{f' · received by {done_note.receiver_name}' if done_note and done_note.receiver_name else ''}"
            if done_note else "Received by, quantities delivered/returned, remarks and proof."
        ),
        docs=[{"label": n.number, "to": f"/app/delivery/notes/{n.pk}"} for n in delivered],
        action=(
            {"label": "Confirm delivery", "to": f"/app/delivery/notes/{pending_note.pk}", "perm": "delivery.complete_delivery"}
            if pending_note is not None and out else None
        ),
    ))
    for s in steps:
        if cancelled and s["state"] in ("current", "todo"):
            s["state"] = "skipped"
    return steps
