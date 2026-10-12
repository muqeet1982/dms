"""
Where the field force has been.

Two jobs: take the position fixes a phone collected (often hours later, out of order, sometimes twice) and give
them back as something a person can read — a route, the places someone stopped, and what they did there.

The rules the office sets live in the tenant's settings, not in the app, because battery life, working hours and
how long location history is kept are company decisions. `policy()` is the one answer both the phone and the
screens read.

Nothing here applies the working-area check. A rep driving home is outside every area, and those points are
exactly what makes the day's route legible.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

from .geofence import distance_m

# the shape of a stop: stayed inside this many metres for at least this long
STOP_RADIUS_M = 60
STOP_MINUTES = 5
# a stop is credited to a customer whose own pin is this close
STOP_NEAR_CUSTOMER_M = 100
# fixes worse than this are kept but ignored when adding up distance — they are noise, not travel
USABLE_ACCURACY_M = 100
MIN_SEGMENT_M = 15
# nobody on a motorbike covers ground faster than this; a segment that implies it is a bad fix, not a journey
MAX_PLAUSIBLE_MPS = 42  # ~150 km/h
MAX_POINTS = 1500
MAX_BATCH = 200

DEFAULTS = {
    "tracking_enabled": True,
    "tracking_mode": "work_hours",      # off | work_hours | always
    "tracking_interval_s": 120,
    "tracking_min_distance_m": 50,
    "tracking_from": "09:00",
    "tracking_to": "19:00",
    "tracking_days": [1, 2, 3, 4, 5, 6],
    "tracking_retention_days": 90,
    "tracking_notice": "Your location is recorded during working hours so the office can plan routes and confirm visits.",
}


def policy(tenant):
    """
    What the phone should do, as the office set it. Returned to the app verbatim — it must not guess any of this.
    `privacy_url` comes from the platform's own settings so the disclosure screen can link it.
    """
    get = (lambda k: tenant.setting(k) if tenant else None) if tenant else (lambda k: None)
    val = {k: (get(k) if get(k) is not None else d) for k, d in DEFAULTS.items()}
    mode = val["tracking_mode"] if val["tracking_enabled"] else "off"
    from apps.platform.models import PlatformSetting

    privacy = (PlatformSetting.get("global", {}) or {}).get("privacy_url") or "/legal/privacy"
    return {
        "enabled": bool(val["tracking_enabled"]),
        "mode": mode,
        "interval_s": int(val["tracking_interval_s"]),
        "min_distance_m": int(val["tracking_min_distance_m"]),
        "window": {"from": val["tracking_from"], "to": val["tracking_to"], "days": list(val["tracking_days"] or [])},
        "retention_days": int(val["tracking_retention_days"]),
        "notice": val["tracking_notice"],
        "privacy_url": privacy,
        "timezone": getattr(tenant, "timezone", None) or timezone.get_current_timezone_name(),
    }


def _num(value, lo=None, hi=None):
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ValueError("not a number")
    if lo is not None and not (lo <= out <= hi):
        raise ValueError(f"out of range {lo}..{hi}")
    return out


def _when(value):
    from django.utils.dateparse import parse_datetime

    if not value:
        raise ValueError("missing")
    at = parse_datetime(str(value))
    if at is None:
        raise ValueError("not a timestamp")
    return at if timezone.is_aware(at) else timezone.make_aware(at, timezone.utc)


def clean_ping(raw, index):
    """One ping, validated. Raises ValueError naming the field — the caller turns that into a 400 with the index."""
    if not isinstance(raw, dict):
        raise ValueError(f"pings[{index}]: expected an object")
    out = {}
    try:
        out["client_id"] = str(raw["client_id"])[:64]
        if not out["client_id"]:
            raise KeyError("client_id")
        out["at"] = _when(raw.get("at"))
        out["lat"] = Decimal(str(round(_num(raw.get("lat"), -90, 90), 7)))
        out["lng"] = Decimal(str(round(_num(raw.get("lng"), -180, 180), 7)))
    except KeyError as e:
        raise ValueError(f"pings[{index}]: {e.args[0]} is required") from e
    except ValueError as e:
        raise ValueError(f"pings[{index}]: {e}") from e
    for field, lo, hi in (("accuracy_m", 0, 100000), ("speed_mps", 0, 400), ("heading_deg", 0, 360), ("altitude_m", -500, 20000)):
        try:
            out[field] = _num(raw.get(field), lo, hi)
        except ValueError:
            out[field] = None          # a bad optional value is dropped, never a reason to refuse the batch
    battery = raw.get("battery_pct")
    out["battery_pct"] = int(battery) if isinstance(battery, (int, float)) and 0 <= battery <= 100 else None
    out["is_charging"] = raw.get("is_charging") if isinstance(raw.get("is_charging"), bool) else None
    out["source"] = raw.get("source") if raw.get("source") in ("background", "foreground", "manual") else "background"
    out["app_version"] = str(raw.get("app_version") or "")[:20]
    return out


class TrackingService:
    @staticmethod
    def record(user, rep, pings):
        """
        Store a batch. Returns {accepted, duplicates, rejected}. Points already seen are counted, not re-written —
        the phone resends anything it is unsure about.
        """
        from apps.core.tenancy import get_current_tenant_id

        from .models import LocationPing

        tenant_id = get_current_tenant_id()
        seen = set(
            LocationPing.objects.filter(user=user, client_id__in=[p["client_id"] for p in pings])
            .values_list("client_id", flat=True)
        )
        fresh = []
        for p in pings:
            if p["client_id"] in seen:
                continue
            seen.add(p["client_id"])     # the same batch can carry a point twice
            # bulk_create skips Model.save(), so the tenant has to be stamped here
            fresh.append(LocationPing(tenant_id=tenant_id, user=user, rep=rep, **p))
        if fresh:
            LocationPing.objects.bulk_create(fresh, batch_size=200, ignore_conflicts=True)
        return {"accepted": len(fresh), "duplicates": len(pings) - len(fresh), "rejected": 0}

    @staticmethod
    def day_range(on_date, tenant=None):
        """The tenant's own day, as a UTC window — a day ends at midnight where the rep works, not in UTC."""
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(on_date, time.min), tz)
        return start, start + timedelta(days=1)

    @staticmethod
    def points_for(user, on_date):
        from .models import LocationPing

        start, end = TrackingService.day_range(on_date)
        return list(LocationPing.objects.filter(user=user, at__gte=start, at__lt=end).order_by("at"))

    @staticmethod
    def distance_of(points):
        """
        Metres travelled, ignoring what is not travel: poor fixes, hops shorter than a stride, and jumps that
        would need an aeroplane. A single bad fix on the other side of the country is common and would otherwise
        turn a day's ride into a thousand kilometres.
        """
        total, last, last_at = 0.0, None, None
        for p in points:
            if p.accuracy_m is not None and p.accuracy_m > USABLE_ACCURACY_M:
                continue
            here = (float(p.lat), float(p.lng))
            if last is None:
                last, last_at = here, p.at
                continue
            step = distance_m(last, here)
            seconds = max(1.0, (p.at - last_at).total_seconds())
            if step >= MIN_SEGMENT_M and step / seconds <= MAX_PLAUSIBLE_MPS:
                total += step
            last, last_at = here, p.at
        return int(total)

    @staticmethod
    def stops_of(points, customers=None):
        """
        Where someone actually stayed: a run of fixes inside `STOP_RADIUS_M` lasting `STOP_MINUTES` or more,
        credited to a customer whose pin is within `STOP_NEAR_CUSTOMER_M`.

        The office reads this far more than the route itself, so it is derived here and not on the phone.
        """
        stops, i = [], 0
        while i < len(points):
            anchor = (float(points[i].lat), float(points[i].lng))
            j = i + 1
            while j < len(points) and distance_m(anchor, (float(points[j].lat), float(points[j].lng))) <= STOP_RADIUS_M:
                j += 1
            minutes = (points[j - 1].at - points[i].at).total_seconds() / 60
            if j - i >= 2 and minutes >= STOP_MINUTES:
                lat = sum(float(p.lat) for p in points[i:j]) / (j - i)
                lng = sum(float(p.lng) for p in points[i:j]) / (j - i)
                stop = {"from": points[i].at, "to": points[j - 1].at, "minutes": int(round(minutes)),
                        "lat": round(lat, 7), "lng": round(lng, 7),
                        "customer": None, "customer_name": "", "customer_distance_m": None}
                near = TrackingService._nearest_customer((lat, lng), customers)
                if near:
                    customer, metres = near
                    stop.update(customer=str(customer.pk), customer_name=customer.name, customer_distance_m=int(metres))
                stops.append(stop)
                i = j
            else:
                i += 1
        return stops

    @staticmethod
    def _nearest_customer(point, customers):
        best = None
        for c, pin in customers or []:
            metres = distance_m(point, pin)
            if metres <= STOP_NEAR_CUSTOMER_M and (best is None or metres < best[1]):
                best = (c, metres)
        return best

    @staticmethod
    def customers_with_pins(rep=None):
        """(customer, (lat, lng)) for everyone who has a pin — the rep's own shops first when there is a rep."""
        from .models import Customer

        qs = Customer.objects.filter(gps_lat__isnull=False, gps_lng__isnull=False)
        if rep is not None:
            qs = qs.filter(assigned_reps=rep)
        return [(c, (float(c.gps_lat), float(c.gps_lng))) for c in qs]

    @staticmethod
    def events_for(user, rep, on_date):
        """
        What they did that day, with the position captured at the time: the orders, the cash, the deliveries.
        This is what ties "was at this shop" to "did something there".
        """
        from apps.delivery.models import DeliveryNote
        from apps.sales.models import RepCollection, SalesOrder

        start, end = TrackingService.day_range(on_date)
        rows = []

        def add(kind, number, when, customer_name, lat, lng):
            if when is None:
                return
            rows.append({"at": when, "type": kind, "ref": number, "customer_name": customer_name or "",
                         "lat": float(lat) if lat is not None else None, "lng": float(lng) if lng is not None else None})

        orders = SalesOrder.objects.filter(created_at__gte=start, created_at__lt=end).select_related("customer")
        orders = orders.filter(rep=rep) if rep is not None else orders.filter(created_by=user)
        for o in orders:
            add("order", o.number, o.created_at, o.customer.name if o.customer_id else "", o.gps_lat, o.gps_lng)

        cash = RepCollection.objects.filter(created_at__gte=start, created_at__lt=end).select_related("customer")
        cash = cash.filter(rep=rep) if rep is not None else cash.filter(recorded_by=user)
        for c in cash:
            add("collection", c.number, c.created_at, c.customer.name if c.customer_id else "", c.gps_lat, c.gps_lng)

        for d in DeliveryNote.objects.filter(delivered_at__gte=start, delivered_at__lt=end).select_related("customer"):
            add("delivery", d.number, d.delivered_at, d.customer.name if d.customer_id else "", d.gps_lat, d.gps_lng)

        # a check-in is the only event that can say "was here, sold nothing, and here is why"
        from apps.partners.visits import day_of

        for v in day_of(user, rep, on_date):
            rows.append({"at": v.at, "type": "visit", "ref": v.get_reason_display() if v.reason else v.get_outcome_display(),
                         "customer_name": v.customer.name if v.customer_id else "",
                         "lat": float(v.gps_lat) if v.gps_lat is not None else None,
                         "lng": float(v.gps_lng) if v.gps_lng is not None else None,
                         "outcome": v.outcome, "photo_url": v.photo.url if v.photo else None})

        return sorted(rows, key=lambda r: r["at"])

    @staticmethod
    def thin(points, limit=MAX_POINTS):
        """
        Keep the shape of the route with fewer points — a phone draws 1,500 smoothly and 20,000 badly.
        Douglas–Peucker over the indexes, so each kept point keeps its time, accuracy and speed.
        """
        if len(points) <= limit:
            return points
        keep = {0, len(points) - 1}
        tolerance = 0.00005  # ~5 m
        for _ in range(16):
            keep = {0, len(points) - 1}
            _dp(points, 0, len(points) - 1, tolerance, keep)
            if len(keep) <= limit:
                break
            tolerance *= 2
        return [points[i] for i in sorted(keep)]


def _dp(points, first, last, tolerance, keep):
    if last <= first + 1:
        return
    ax, ay = float(points[first].lng), float(points[first].lat)
    bx, by = float(points[last].lng), float(points[last].lat)
    dx, dy = bx - ax, by - ay
    worst, index = 0.0, first
    for i in range(first + 1, last):
        px, py = float(points[i].lng), float(points[i].lat)
        if dx == 0 and dy == 0:
            d = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
        else:
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
            d = ((px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2) ** 0.5
        if d > worst:
            worst, index = d, i
    if worst > tolerance:
        keep.add(index)
        _dp(points, first, index, tolerance, keep)
        _dp(points, index, last, tolerance, keep)


def ping_row(p):
    return {"at": p.at, "lat": float(p.lat), "lng": float(p.lng),
            "accuracy_m": p.accuracy_m, "speed_mps": p.speed_mps}


def day_for(user, on_date, *, rep=None, full=False):
    """The whole answer for one person on one day: route, stops, events and the totals."""
    points = TrackingService.points_for(user, on_date)
    shown = points if full else TrackingService.thin(points)
    customers = TrackingService.customers_with_pins(rep)
    return {
        "user_id": str(user.pk),
        "user_name": user.full_name or user.username,
        "date": on_date,
        "first_at": points[0].at if points else None,
        "last_at": points[-1].at if points else None,
        "distance_m": TrackingService.distance_of(points),
        "points": [ping_row(p) for p in shown],
        "points_total": len(points),
        "stops": TrackingService.stops_of(points, customers),
        "events": TrackingService.events_for(user, rep, on_date),
    }


def purge(tenant, days=None):
    """Drop points older than the company's retention. Returns how many went."""
    from .models import LocationPing

    days = int(days or (tenant.setting("tracking_retention_days") if tenant else None) or DEFAULTS["tracking_retention_days"])
    cutoff = timezone.now() - timedelta(days=days)
    gone, _ = LocationPing.objects.filter(at__lt=cutoff).delete()
    return {"deleted": gone, "older_than_days": days, "cutoff": cutoff}
