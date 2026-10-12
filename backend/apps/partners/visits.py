"""
Check-in: the record that a rep stood at a shop, whether or not anything was sold.

A day of driving that ends in four closed shutters used to leave no trace — the track showed stops, but nothing
said which shop, or why there was no order. This turns that into a document: who, where, when, what came of it,
and a photo taken on the spot.

The photo cannot be proved to come from a camera; bytes are bytes. So the rules here are the ones a server can
actually hold: it must arrive with the check-in, the device's own capture time must be recent, the position is
checked against the rep's working area exactly as an order's is, and the distance to the shop's own pin is
measured and kept. The app is told to open the camera and not the gallery (`visit_photo: "camera"` in the
policy), and `photo_taken_at` is what catches a gallery pick.
"""
import base64
import binascii
import uuid
from datetime import timedelta

from django.core.files.base import ContentFile
from django.utils import timezone

from apps.core.exceptions import DomainError
from apps.core.tenancy import get_current_tenant

from .geofence import GeoFenceService, coords, distance_m

# How far the photo may sit outside the visit it belongs to. The test is against the **visit's own clock** —
# `checked_in_at`, and `closed_at` where the app sends it — never against the time the batch reached us. A rep
# who works a beat with no signal and flushes at six in the evening took every one of those photos at the
# shop; measuring from receive time would refuse the whole day. (The mobile team found this, with the numbers.)
PHOTO_WINDOW_S = 600
# The wall-clock sanity bounds. Deliberately loose: they exist to refuse a photo backfilled next month, not to
# police a long visit or a day out of coverage. The phone buffers for about two days; a wrong device timezone
# shifts the photo and the check-in together, so the window test above still holds and only these would trip.
PHOTO_MAX_DELAY_S = 14 * 24 * 3600
PHOTO_MAX_FUTURE_S = 24 * 3600
MAX_PHOTO_BYTES = 8_000_000


def policy(tenant=None):
    """What the app needs to know before it shows the check-in button."""
    from .models import CustomerVisit

    tenant = tenant or get_current_tenant()
    want = (tenant.setting("rep_visit_photo") if tenant else "required") or "required"
    return {
        "enabled": want != "off",
        "photo": want,                      # required | optional | off
        "photo_source": "camera",           # never the gallery: an old photo is not proof of a visit
        # the photo must fall inside the visit, measured on the device's own clock — not on ours
        "photo_window_s": PHOTO_WINDOW_S,
        "photo_measured_against": "checked_in_at",
        "photo_max_age_s": PHOTO_WINDOW_S,          # the old name, kept for builds already in the field
        "reasons": [{"value": v, "label": label} for v, label in CustomerVisit.REASONS],
    }


def _photo_bytes(payload):
    """A data URL or bare base64 from the app → (bytes, extension)."""
    if not payload:
        return None, ""
    if hasattr(payload, "read"):                       # an uploaded file (multipart)
        data = payload.read()
        ext = "png" if (getattr(payload, "content_type", "") or "").endswith("png") else "jpg"
        return data, ext
    header, b64 = payload.split(",", 1) if "," in payload else ("data:image/jpeg;base64", payload)
    try:
        return base64.b64decode(b64, validate=False), ("png" if "png" in header else "jpg")
    except (binascii.Error, ValueError):
        raise DomainError("That photo could not be read — take it again.", code="bad_photo", status_code=400)


# The keys this server shipped with for a week, before the app's own names were adopted.
REASON_ALIASES = {"shop_closed": "closed", "owner_absent": "owner_away", "stock_in_hand": "has_stock",
                  "payment_pending": "payment_due", "no_need": "has_stock"}


class VisitService:
    @staticmethod
    def check_in(*, rep, customer, user=None, gps=None, at=None, closed_at=None, outcome="no_order", reason="",
                 note="", photo=None, photo_taken_at=None, client_id="", app_version="", order=None, order_ref=""):
        """
        Record a visit. Returns `(visit, created)` — `created=False` means the phone retried a batch and this
        `client_id` was already stored, which must not become a second visit.
        """
        from .models import CustomerVisit

        tenant = get_current_tenant()
        client_id = (client_id or "").strip() or uuid.uuid4().hex
        existing = CustomerVisit.objects.filter(user=user, client_id=client_id).first() if user else None
        if existing:
            return existing, False

        want = (tenant.setting("rep_visit_photo") if tenant else "required") or "required"
        if want == "off":
            raise DomainError("Check-in is switched off for this company.", code="visit_disabled", status_code=400)

        data, ext = _photo_bytes(photo)
        if want == "required" and not data:
            raise DomainError("Take a photo of the shop to check in.", code="photo_required", status_code=400)
        if data and len(data) > MAX_PHOTO_BYTES:
            raise DomainError("That photo is too large — take it again at a lower quality.", code="photo_too_large", status_code=413)

        # A photo chosen from the gallery carries a capture time from outside the visit. A check-in with no
        # `photo_taken_at` is still accepted, because an older build does not send it — but it is recorded as
        # unverified rather than waved through, so the office can see which photos nothing vouches for.
        now = timezone.now()
        at = at or now
        photo_verified = False
        if data and photo_taken_at:
            photo_verified = VisitService._check_photo_time(photo_taken_at, at, closed_at, now)

        reason = REASON_ALIASES.get(reason, reason)
        if outcome not in dict(CustomerVisit.OUTCOMES):
            raise DomainError("outcome must be 'order' or 'no_order'.", status_code=400)
        if outcome == "no_order" and reason and reason not in dict(CustomerVisit.REASONS):
            raise DomainError(f"reason must be one of: {', '.join(dict(CustomerVisit.REASONS))}", status_code=400)
        if outcome == "no_order" and not reason:
            raise DomainError("Say why there was no order.", code="reason_required", status_code=400)

        point = coords(gps)
        where = GeoFenceService.locate(rep, point) if (rep is not None and point) else None
        distance = None
        if point and customer.gps_lat is not None and customer.gps_lng is not None:
            distance = round(distance_m(point, (float(customer.gps_lat), float(customer.gps_lng))), 1)

        if order is None and order_ref:
            from apps.sales.models import SalesOrder

            order = SalesOrder.objects.filter(number=order_ref).first()

        visit = CustomerVisit.objects.create(
            customer=customer, rep=rep, user=user, client_id=client_id,
            at=at, closed_at=closed_at,
            outcome=outcome, reason=reason if outcome == "no_order" else "", note=(note or "")[:300],
            order=order,
            gps_lat=round(point[0], 7) if point else None, gps_lng=round(point[1], 7) if point else None,
            gps_accuracy_m=(gps or {}).get("accuracy_m") if isinstance(gps, dict) else None,
            customer_distance_m=distance,
            inside_area=where["inside"] if where else None,
            photo_taken_at=photo_taken_at, photo_verified=photo_verified, app_version=app_version or "",
        )
        if data:
            stamp = timezone.localtime(visit.at).strftime("%d %b %Y %H:%M")
            visit.photo.save(f"visit-{visit.pk}.{ext}", ContentFile(data), save=True)
            VisitService._attach(visit, stamp, point)
        GeoFenceService.link(rep, "visit", visit)
        return visit, True

    @staticmethod
    def _check_photo_time(taken_at, at, closed_at, now):
        """
        Was this photo taken *during the visit*?

        The window runs from a little before the rep checked in to a little after they left, so it survives
        both a twenty-minute order and an afternoon with no signal. Outside it is a gallery pick and is
        refused; the loose wall-clock bounds below only catch a photo backfilled weeks later.
        """
        ends = closed_at if (closed_at and closed_at > at) else at
        early = (at - taken_at).total_seconds()
        late = (taken_at - ends).total_seconds()
        if early > PHOTO_WINDOW_S or late > PHOTO_WINDOW_S:
            drift = int(max(early, late))
            raise DomainError(
                "That photo was not taken at this visit — take one at the shop.",
                code="photo_not_live", status_code=400,
                errors={"photo_taken_at": [f"{drift}s outside the visit, limit {PHOTO_WINDOW_S}s"]},
            )
        if (now - taken_at).total_seconds() > PHOTO_MAX_DELAY_S:
            raise DomainError("That photo is too old to file against a visit.", code="photo_not_live", status_code=400)
        if (taken_at - now).total_seconds() > PHOTO_MAX_FUTURE_S:
            raise DomainError("That photo's time is in the future — check the clock on your phone.",
                              code="photo_not_live", status_code=400)
        return True

    @staticmethod
    def _attach(visit, stamp, point):
        """Also file the photo as an attachment on the customer, so it shows on the shop's own page."""
        from apps.core.models import Attachment

        place = f" at {point[0]:.5f},{point[1]:.5f}" if point else ""
        try:
            Attachment.objects.create(
                model="Customer", object_id=str(visit.customer_id), file=visit.photo.name,
                name=f"Visit photo — {stamp}{place}", size=visit.photo.size,
                content_type="image/jpeg", uploaded_by=visit.user, kind="visit_photo",
            )
        except Exception:
            pass        # the photo is already on the visit; the convenience copy is not worth failing for

    @staticmethod
    def serialize(visit, *, full=True):
        row = {
            "id": str(visit.pk), "at": visit.at, "checked_in_at": visit.at, "closed_at": visit.closed_at,
            "client_id": visit.client_id, "outcome": visit.outcome,
            "reason": visit.reason, "no_order_reason": visit.reason,
            "reason_label": dict(visit.REASONS).get(visit.reason, ""),
            "no_order_reason_label": dict(visit.REASONS).get(visit.reason, ""),
            "note": visit.note,
            "customer_id": str(visit.customer_id), "customer_name": visit.customer.name if visit.customer_id else "",
            "customer_code": visit.customer.code if visit.customer_id else "",
            "gps": visit.gps, "gps_accuracy_m": visit.gps_accuracy_m,
            "customer_distance_m": visit.customer_distance_m, "inside_area": visit.inside_area,
            "photo_url": visit.photo.url if visit.photo else None,
            "photo_taken_at": visit.photo_taken_at,
            "photo_verified": visit.photo_verified if visit.photo else None,
            "order_id": str(visit.order_id) if visit.order_id else None,
            "order_number": visit.order.number if visit.order_id else None,
            "order_ref": visit.order.number if visit.order_id else None,
        }
        if full:
            row.update({"rep_id": str(visit.rep_id) if visit.rep_id else None,
                        "rep_name": visit.rep.name if visit.rep_id else "",
                        "user_name": (visit.user.full_name or visit.user.username) if visit.user_id else "",
                        "created_at": visit.created_at, "app_version": visit.app_version})
        return row


def day_of(user, rep, on_date):
    """The visits that belong to one person's day — used by the tracking day view."""
    from apps.partners.tracking import TrackingService

    from .models import CustomerVisit

    start, end = TrackingService.day_range(on_date)
    qs = CustomerVisit.objects.filter(at__gte=start, at__lt=end).select_related("customer", "order")
    qs = qs.filter(rep=rep) if rep is not None else qs.filter(user=user)
    return list(qs)
