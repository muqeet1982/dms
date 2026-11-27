"""
The best order for a trip's stops, and what driving it costs (owner, 27 Nov 2026: "our system will calculate the best route
for delivery ... calculate time and distance as well").

Planned from where the rider is when the trip starts (no warehouse has a pin), through every shop still to visit and back
to that start. Google's road route when the server's key has the Routes API — one call per trip, up to 25 shops; TDN's
biggest trip so far had 18. Otherwise, or whenever Google fails, the nearest shop goes next and crossed legs are swapped
out, with straight lines × 1.3 for road km and 25 km/h for city driving. A shop with no pin goes last, in the order it had
(owner, 27 Oct 2026: a shop without a pin is allowed).

Actual km: the phone's GPS trail between two stops when there is one (tracking on), else the distance through the stops in
the order they were really visited — by road from Google once the trip is done, straight lines × 1.3 until then. Nothing
here may ever stop a delivery: a failure is logged and the trip goes on unplanned.
"""
import json
import logging
import math
import urllib.error
import urllib.request
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

log = logging.getLogger(__name__)

ROAD_FACTOR = 1.3      # a road is about this much longer than the straight line, in a city
CITY_KMH = 25
GOOGLE_MAX = 25        # intermediate stops one Routes API call takes
GOOGLE_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
OPEN = ("pending", "loaded", "dispatched")     # a stop still to visit
TRAIL_MIN_POINTS = 3
TRAIL_MAX_KMH = 150    # a jump faster than this is a bad fix, not a drive


def haversine_km(a, b):
    lat1, lng1, lat2, lng2 = map(math.radians, (float(a[0]), float(a[1]), float(b[0]), float(b[1])))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, h)))


def estimate(a, b):
    """(km, minutes) of a city drive from a to b, from the straight line."""
    km = haversine_km(a, b) * ROAD_FACTOR
    return km, km / CITY_KMH * 60


def encode_polyline(points):
    """Google's encoded polyline of [(lat, lng), …] — what a map draws the route from."""
    out, plat, plng = [], 0, 0
    for lat, lng in points:
        ilat, ilng = round(float(lat) * 1e5), round(float(lng) * 1e5)
        for v in (ilat - plat, ilng - plng):
            v = ~(v << 1) if v < 0 else (v << 1)
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plng = ilat, ilng
    return "".join(out)


def _pt(lat, lng):
    return (float(lat), float(lng)) if lat is not None and lng is not None else None


def pin_of(note):
    c = note.customer
    return _pt(c.gps_lat, c.gps_lng)


GOOD_FIX_M = 100   # a phone fix vaguer than this is no fix at all: the shop's pin stands in (mobile AI, 27 Nov 2026)


def point_of(note):
    """Where a visited stop really was: where the phone said Arrived, else where it delivered, else the shop's pin."""
    delivered = _pt(note.gps_lat, note.gps_lng) if note.gps_accuracy_m is None or note.gps_accuracy_m <= GOOD_FIX_M else None
    return _pt(note.arrived_lat, note.arrived_lng) or delivered or pin_of(note)


def _tour_km(origin, points, order, dest):
    path = [origin, *(points[i] for i in order), dest]
    return sum(haversine_km(path[k], path[k + 1]) for k in range(len(path) - 1))


def straight_order(origin, points, dest=None):
    """Indexes of `points`: the nearest next from `origin`, then any two legs that cross swapped (2-opt)."""
    dest = dest or origin
    left, order, at = list(range(len(points))), [], origin
    while left:
        j = min(left, key=lambda i: haversine_km(at, points[i]))
        order.append(j)
        left.remove(j)
        at = points[j]
    best = _tour_km(origin, points, order, dest)
    for _ in range(50):
        better = False
        for i in range(len(order) - 1):
            for k in range(i + 1, len(order)):
                cand = order[:i] + order[i:k + 1][::-1] + order[k + 1:]
                length = _tour_km(origin, points, cand, dest)
                if length < best - 1e-9:
                    order, best, better = cand, length, True
        if not better:
            break
    return order


def _seconds(value):
    try:
        return float(str(value or "0s").rstrip("s"))
    except ValueError:
        return 0.0


def google_route(origin, points, dest, optimize=True):
    """
    One Routes API call: {order, legs: [(km, minutes)…], polyline}, legs in visiting order and the last one back to `dest`.
    None when there is no key, too many stops, or Google says no — the caller estimates instead.
    """
    key = getattr(settings, "GOOGLE_MAPS_API_KEY", "")
    if not key or not points or len(points) > GOOGLE_MAX:
        return None

    def ll(p):
        return {"location": {"latLng": {"latitude": p[0], "longitude": p[1]}}}

    body = {"origin": ll(origin), "destination": ll(dest), "intermediates": [ll(p) for p in points], "travelMode": "DRIVE",
            "optimizeWaypointOrder": bool(optimize and len(points) > 1)}
    req = urllib.request.Request(GOOGLE_URL, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json", "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "routes.optimizedIntermediateWaypointIndex,routes.legs.distanceMeters,routes.legs.duration,routes.polyline.encodedPolyline"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            route = json.loads(resp.read())["routes"][0]
    except urllib.error.HTTPError as e:
        log.warning("google route refused: %s %s", e.code, e.read()[:300])
        return None
    except Exception as e:  # noqa: BLE001 — a network hiccup must not stop a trip
        log.warning("google route failed: %s", e)
        return None
    order = route.get("optimizedIntermediateWaypointIndex") or list(range(len(points)))
    legs = [(l.get("distanceMeters", 0) / 1000, _seconds(l.get("duration")) / 60) for l in route.get("legs", [])]
    if sorted(order) != list(range(len(points))) or len(legs) != len(points) + 1:
        log.warning("google route came back with %s legs for %s stops", len(legs), len(points))
        return None
    return {"order": order, "legs": legs, "polyline": (route.get("polyline") or {}).get("encodedPolyline", "")}


def start_point(trip):
    return _pt(trip.start_lat, trip.start_lng)


def _last_ping(trip, within=timedelta(hours=2)):
    from apps.partners.models import LocationPing

    user_id = rider_user_id(trip)
    if not user_id:
        return None
    p = LocationPing.objects.filter(user_id=user_id, at__gte=timezone.now() - within).order_by("-at").values_list("lat", "lng").first()
    return _pt(*p) if p else None


def rider_user_id(trip):
    return trip.delivery_rep_id or (trip.driver.user_id if trip.driver_id and trip.driver else None)


def _q2(v):
    return Decimal(str(round(v, 2))) if v is not None else None


def plan_route(trip, origin=None):
    """
    Put the stops still to visit in the best order from `origin` (the rider's position), and keep each leg's km and
    minutes. Stops already visited keep their place at the front. Returns the trip.
    """
    notes = list(trip.delivery_notes.exclude(status="cancelled").select_related("customer").order_by("sequence", "created_at"))
    done = sorted((n for n in notes if n.status not in OPEN), key=lambda n: n.arrived_at or n.delivered_at or n.created_at)
    todo = [n for n in notes if n.status in OPEN]
    pinned = [(n, pin_of(n)) for n in todo if pin_of(n)]
    unpinned = [n for n in todo if not pin_of(n)]
    last_seen = point_of(done[-1]) if done else None
    origin = origin or last_seen or start_point(trip) or _last_ping(trip) or (pinned[0][1] if pinned else None)
    dest = start_point(trip) or origin
    points = [p for _, p in pinned]
    route = google_route(origin, points, dest) if points else None
    if route:
        order, legs, polyline, source = route["order"], route["legs"], route["polyline"], "google"
    else:
        order = straight_order(origin, points, dest) if points else []
        path = [origin, *(points[i] for i in order), dest]
        legs = [estimate(path[k], path[k + 1]) for k in range(len(path) - 1)] if points else []
        polyline, source = (encode_polyline(path) if points else ""), "straight"
    seq = 1
    for n in done:
        n.sequence, seq = seq, seq + 1
    for idx, j in enumerate(order):
        n = pinned[j][0]
        n.sequence, n.planned_km, n.planned_minutes, seq = seq, _q2(legs[idx][0]), round(legs[idx][1]), seq + 1
    for n in unpinned:
        n.sequence, n.planned_km, n.planned_minutes, seq = seq, None, None, seq + 1
    for n in done + [p[0] for p in pinned] + unpinned:
        n.save(update_fields=["sequence", "planned_km", "planned_minutes", "updated_at"])
    back = legs[-1] if legs else (0, 0)
    planned = [n for n in notes if n.planned_km is not None]
    trip.planned_km = _q2(sum(float(n.planned_km) for n in planned) + back[0]) if planned else None
    trip.planned_minutes = round(sum(n.planned_minutes or 0 for n in planned) + back[1]) if planned else None
    trip.route_source, trip.route_polyline, trip.route_planned_at = (source if points else ""), polyline, timezone.now()
    trip.save(update_fields=["planned_km", "planned_minutes", "route_source", "route_polyline", "route_planned_at", "updated_at"])
    return trip


def trail_km(points):
    """km along a phone's fixes [(at, lat, lng, accuracy)…], dropping bad fixes and standing-still jitter."""
    km, last = 0.0, None
    for at, lat, lng, acc in points:
        if acc is not None and acc > 100:
            continue
        here = (float(lat), float(lng))
        if last is None:
            last = (at, here)
            continue
        step = haversine_km(last[1], here)
        hours = max((at - last[0]).total_seconds(), 1) / 3600
        if step / hours > TRAIL_MAX_KMH:
            continue
        if step >= 0.025:
            km += step
            last = (at, here)
    return km


def rode(trip):
    """
    Was this trip run from the phone — started with GPS, a shop Arrived at, or a stop saved with GPS? Only then do its times
    and km mean anything: TDN's office confirms stops on the web, often days later (21 Nov 2026 read 291 hours on the road).
    """
    return trip.start_lat is not None or any(n.arrived_at or n.gps_lat is not None for n in trip.delivery_notes.all())


def actuals(trip, road=False):
    """
    {legs: {note_id: km}, km, source} for the stops visited so far, in the order they were visited — the GPS trail where
    the phone sent one, else straight lines (or Google's road through the same stops when `road`), plus the way back.
    Reads only; `refresh_actuals` keeps the figures.
    """
    from apps.partners.models import LocationPing

    notes = sorted((n for n in trip.delivery_notes.all() if n.status not in OPEN and n.status != "cancelled" and (n.arrived_at or n.delivered_at)),
                   key=lambda n: n.arrived_at or n.delivered_at)
    if not notes or not rode(trip):
        return {"legs": {}, "km": None, "source": ""}
    user_id = rider_user_id(trip)
    end = trip.completed_at or timezone.now()
    pings = list(LocationPing.objects.filter(user_id=user_id, at__gte=trip.started_at - timedelta(minutes=5), at__lte=end + timedelta(minutes=5))
                 .order_by("at").values_list("at", "lat", "lng", "accuracy_m")) if user_id and trip.started_at else []
    start = start_point(trip)
    prev_pt, prev_t, legs, sources = start, trip.started_at, {}, set()
    for n in notes:
        pt, t = point_of(n), n.arrived_at or n.delivered_at
        seg = [p for p in pings if prev_t and prev_t <= p[0] <= t]
        if len(seg) >= TRAIL_MIN_POINTS:
            legs[n.id], src = trail_km(seg), "gps"
        elif prev_pt and pt:
            legs[n.id], src = estimate(prev_pt, pt)[0], "straight"
        else:
            legs[n.id], src = None, ""
        sources.add(src)
        prev_pt, prev_t = pt or prev_pt, t
    last = point_of(notes[-1])
    back = estimate(last, start)[0] if (start and last) else 0.0
    if road and "gps" not in sources:
        stops = [point_of(n) for n in notes]
        if all(stops):
            # from the start through every stop and back; with no start, from the first stop to the last
            route = google_route(start, stops, start, optimize=False) if start else google_route(stops[0], stops[1:-1], stops[-1], optimize=False)
            if route:
                ks = [k for k, _ in route["legs"]]
                for i, n in enumerate(notes):
                    legs[n.id] = (ks[i] if start else (ks[i - 1] if i else None))
                back = ks[-1] if start else 0.0
                sources = {"google"}
    known = [v for v in legs.values() if v is not None]
    km = sum(known) + back if known else None
    source = "gps" if "gps" in sources else ("google" if "google" in sources else ("straight" if known else ""))
    return {"legs": legs, "km": km, "source": source}


def refresh_actuals(trip, road=False):
    out = actuals(trip, road=road)
    for n in trip.delivery_notes.all():
        if n.id in out["legs"]:
            v = _q2(out["legs"][n.id])
            if v != n.actual_km:
                n.actual_km = v
                n.save(update_fields=["actual_km", "updated_at"])
    trip.actual_km, trip.actual_km_source = _q2(out["km"]), out["source"]
    trip.save(update_fields=["actual_km", "actual_km_source", "updated_at"])
    return trip


def after_stop(trip, origin=None):
    """
    After a stop is saved: the visited figures brought up to date, a trip that started without a plan planned from here,
    and a finished trip measured by road. Never raises — a delivery is never lost to a map.
    """
    from .models import DeliveryTrip

    if trip is None:
        return
    try:
        with transaction.atomic():
            trip = DeliveryTrip.objects.get(pk=trip.pk)
            refresh_actuals(trip)
            if trip.route_planned_at is None and trip.delivery_notes.filter(status__in=OPEN).exists():
                plan_route(trip, origin=origin)
    except Exception:  # noqa: BLE001
        log.exception("trip figures for %s", trip.pk)
        return
    if trip.status == "completed":
        pk = trip.pk

        def measure():
            try:
                with transaction.atomic():
                    refresh_actuals(DeliveryTrip.objects.get(pk=pk), road=True)
            except Exception:  # noqa: BLE001
                log.exception("road km for %s", pk)

        transaction.on_commit(measure)


def gps_of(data):
    """
    (lat, lng) from a request body: `gps: {lat, lng, accuracy_m}` (JSON or a JSON string) or `gps_lat` / `gps_lng`. A fix
    vaguer than GOOD_FIX_M is treated as none.
    """
    gps = data.get("gps")
    if isinstance(gps, str):
        try:
            gps = json.loads(gps) if gps else None
        except ValueError:
            gps = None
    if not gps and data.get("gps_lat"):
        gps = {"lat": data.get("gps_lat"), "lng": data.get("gps_lng")}
    try:
        if not gps or gps.get("lat") in (None, ""):
            return None
        accuracy = gps.get("accuracy_m", gps.get("accuracy"))
        if accuracy not in (None, "") and float(accuracy) > GOOD_FIX_M:
            return None
        return (float(gps["lat"]), float(gps["lng"]))
    except (TypeError, ValueError, KeyError, AttributeError):
        return None
