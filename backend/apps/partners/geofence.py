"""
Working areas on the map: the geometry, and the one question the rest of the system asks —
**"is this rep standing inside an area they cover?"**

The maths is deliberately plain. A distribution business has tens of areas and thousands of checks a day, not
millions, so there is no spatial database here: every fence carries its bounding box, the database throws away the
ones that cannot possibly contain the point, and the few that survive get an exact test in Python.

* a **circle** contains a point when the great-circle distance to its centre is within the radius;
* a **polygon** contains a point by ray casting — count the edges a ray to the east crosses; odd means inside.

Both work in plain latitude/longitude. Over a delivery area (tens of kilometres) the error from treating the
earth as locally flat in the polygon test is centimetres, and the circle test uses the real haversine distance.
"""
from decimal import Decimal
from math import asin, cos, radians, sin, sqrt

from django.utils import timezone

EARTH_M = 6371008.8  # mean radius, metres
MAX_POINTS = 200  # an OSM district outline can carry thousands; a delivery area needs nothing like that


def _f(v):
    return float(v) if v is not None and v != "" else None


def coords(value):
    """Read {lat, lng} / {latitude, longitude} / [lat, lng] into a (lat, lng) pair, or None when there is no fix."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        lat, lng = value
    elif isinstance(value, dict):
        lat = value.get("lat", value.get("latitude"))
        lng = value.get("lng", value.get("lon", value.get("longitude")))
    else:
        return None
    lat, lng = _f(lat), _f(lng)
    if lat is None or lng is None:
        return None
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return None
    return lat, lng


def distance_m(a, b):
    """Great-circle distance between two (lat, lng) pairs, in metres."""
    lat1, lng1 = radians(a[0]), radians(a[1])
    lat2, lng2 = radians(b[0]), radians(b[1])
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lng2 - lng1) / 2) ** 2
    return 2 * EARTH_M * asin(sqrt(h))


def point_in_ring(point, ring):
    """Ray casting: a ray east from the point crosses an odd number of edges when the point is inside."""
    lat, lng = point
    inside = False
    n = len(ring)
    for i in range(n):
        y1, x1 = ring[i]
        y2, x2 = ring[(i + 1) % n]
        if (y1 > lat) != (y2 > lat):
            x_at_lat = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if x_at_lat > lng:
                inside = not inside
    return inside


def ring_of(fence):
    """A polygon fence's points as [(lat, lng), …]; empty when the shape is not usable."""
    out = []
    for p in fence.points or []:
        c = coords(p)
        if c:
            out.append(c)
    return out if len(out) >= 3 else []


def contains(fence, point):
    """Is this (lat, lng) inside the fence?"""
    if fence.type == "circle":
        centre = coords({"lat": fence.centre_lat, "lng": fence.centre_lng})
        if not centre or not fence.radius_m:
            return False
        return distance_m(centre, point) <= fence.radius_m
    ring = ring_of(fence)
    return bool(ring) and point_in_ring(point, ring)


def distance_to(fence, point):
    """
    Metres from the point to the fence: 0 when inside, otherwise how far outside. For a polygon this is the
    distance to the nearest edge, which is what a rep standing just over the line wants to be told.
    """
    if contains(fence, point):
        return 0
    if fence.type == "circle":
        centre = coords({"lat": fence.centre_lat, "lng": fence.centre_lng})
        if not centre:
            return None
        return max(0, int(distance_m(centre, point) - (fence.radius_m or 0)))
    ring = ring_of(fence)
    if not ring:
        return None
    best = min(_distance_to_segment(point, ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring)))
    return int(best)


def _distance_to_segment(p, a, b):
    """Distance from p to the segment a→b, in metres, working in a local flat frame (fine over a few km)."""
    lat0 = radians(p[0])
    mx = cos(lat0) * EARTH_M  # metres per radian of longitude at this latitude
    to_xy = lambda q: (radians(q[1]) * mx, radians(q[0]) * EARTH_M)  # noqa: E731
    px, py = to_xy(p)
    ax, ay = to_xy(a)
    bx, by = to_xy(b)
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return sqrt((px - ax) ** 2 + (py - ay) ** 2)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return sqrt((px - cx) ** 2 + (py - cy) ** 2)


def bbox(fence):
    """(min_lat, max_lat, min_lng, max_lng) for the shape, so the database can pre-filter on it."""
    if fence.type == "circle":
        centre = coords({"lat": fence.centre_lat, "lng": fence.centre_lng})
        if not centre:
            return None
        lat, lng = centre
        dlat = (fence.radius_m or 0) / EARTH_M * 180 / 3.141592653589793
        dlng = dlat / max(0.01, cos(radians(lat)))
        return lat - dlat, lat + dlat, lng - dlng, lng + dlng
    ring = ring_of(fence)
    if not ring:
        return None
    lats = [p[0] for p in ring]
    lngs = [p[1] for p in ring]
    return min(lats), max(lats), min(lngs), max(lngs)


def area_km2(fence):
    """Rough size, for the list screen. Circles exactly; polygons by the shoelace formula in a local flat frame."""
    if fence.type == "circle":
        r = (fence.radius_m or 0) / 1000
        return round(3.141592653589793 * r * r, 2)
    ring = ring_of(fence)
    if not ring:
        return 0
    lat0 = radians(sum(p[0] for p in ring) / len(ring))
    mx = cos(lat0) * EARTH_M
    pts = [(radians(p[1]) * mx, radians(p[0]) * EARTH_M) for p in ring]
    s = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
    return round(abs(s) / 2 / 1_000_000, 2)


def simplify(points, max_points=MAX_POINTS):
    """
    Thin a traced outline down to something a phone can draw: Douglas–Peucker, with the tolerance raised until the
    result fits. An OSM district comes back with thousands of points; a delivery area is just as correct with 200.
    """
    ring = [coords(p) for p in points or []]
    ring = [p for p in ring if p]
    if len(ring) <= max_points:
        return [{"lat": round(p[0], 7), "lng": round(p[1], 7)} for p in ring]
    tolerance = 0.0001  # ~11 m
    out = ring
    for _ in range(14):
        out = _douglas_peucker(ring, tolerance)
        if len(out) <= max_points:
            break
        tolerance *= 2
    return [{"lat": round(p[0], 7), "lng": round(p[1], 7)} for p in out[:max_points]]


def _douglas_peucker(pts, tol):
    if len(pts) < 3:
        return pts
    first, last = pts[0], pts[-1]
    index, far = 0, 0.0
    for i in range(1, len(pts) - 1):
        d = _perpendicular(pts[i], first, last)
        if d > far:
            index, far = i, d
    if far > tol:
        left = _douglas_peucker(pts[: index + 1], tol)
        right = _douglas_peucker(pts[index:], tol)
        return left[:-1] + right
    return [first, last]


def _perpendicular(p, a, b):
    """Perpendicular distance in degrees — only used to rank points against each other."""
    dx, dy = b[1] - a[1], b[0] - a[0]
    if dx == 0 and dy == 0:
        return sqrt((p[1] - a[1]) ** 2 + (p[0] - a[0]) ** 2)
    t = ((p[1] - a[1]) * dx + (p[0] - a[0]) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return sqrt((p[1] - (a[1] + t * dx)) ** 2 + (p[0] - (a[0] + t * dy)) ** 2)


def block_response(error):
    """A refusal in the shape every other error in this API has, as a response rather than an exception."""
    from rest_framework.response import Response

    titles = {"outside_area": "Outside your work area", "location_required": "Location needed"}
    code = error.get_codes() if hasattr(error, "get_codes") else "outside_area"
    return Response({"type": code, "title": titles.get(code, "Outside your work area"), "status": 403,
                     "detail": str(error.detail), "errors": getattr(error, "errors", {})}, status=403)


class GeoFenceService:
    """Saving a fence, finding who is inside it, and the check that guards a rep's work."""

    @staticmethod
    def normalise(fence):
        """Keep the stored shape tidy: thin a traced polygon, and refresh the bounding box. Call before save."""
        if fence.type == "polygon":
            fence.points = simplify(fence.points)
            fence.radius_m = 0
            ring = ring_of(fence)
            if ring:  # the centre is handy for "fly to this fence" on the map
                fence.centre_lat = Decimal(str(round(sum(p[0] for p in ring) / len(ring), 7)))
                fence.centre_lng = Decimal(str(round(sum(p[1] for p in ring) / len(ring), 7)))
        else:
            fence.points = []
        box = bbox(fence)
        if box:
            fence.min_lat, fence.max_lat, fence.min_lng, fence.max_lng = (Decimal(str(round(v, 7))) for v in box)
        return fence

    @staticmethod
    def candidates(qs, point):
        """Narrow to the fences whose box covers the point — the exact test then runs on a handful."""
        lat, lng = point
        return qs.filter(min_lat__lte=lat, max_lat__gte=lat, min_lng__lte=lng, max_lng__gte=lng)

    @staticmethod
    def fences_for(rep, active_only=True):
        """The areas this rep covers right now — a paused assignment does not count."""
        from .models import GeoFence

        today = timezone.localdate()
        qs = GeoFence.objects.filter(assignments__rep=rep)
        if active_only:
            qs = qs.filter(is_active=True).exclude(assignments__rep=rep, assignments__paused_until__gte=today)
        return qs.distinct()

    @staticmethod
    def customers_in(fence):
        """Customers whose pin falls inside. Pins outside the box are dropped before the exact test."""
        from .models import Customer

        qs = Customer.objects.filter(gps_lat__isnull=False, gps_lng__isnull=False)
        if fence.min_lat is not None:
            qs = qs.filter(gps_lat__gte=fence.min_lat, gps_lat__lte=fence.max_lat, gps_lng__gte=fence.min_lng, gps_lng__lte=fence.max_lng)
        return [c for c in qs if contains(fence, (float(c.gps_lat), float(c.gps_lng)))]

    @staticmethod
    def locate(rep, point):
        """
        Where this point sits against the rep's areas:
        `{"fences": n, "inside": bool, "fence": …, "distance_m": …}` — `fence` is the area it fell in, or the
        nearest one when outside, so a refusal can say *which* area and *how far*.
        """
        fences = list(GeoFenceService.fences_for(rep)) if rep is not None else []
        if not fences or point is None:
            return {"fences": len(fences), "inside": not fences, "fence": None, "distance_m": None}
        nearest, best = None, None
        for f in fences:
            d = distance_to(f, point)
            if d is None:
                continue
            if d == 0:
                return {"fences": len(fences), "inside": True, "fence": f, "distance_m": 0}
            if best is None or d < best:
                nearest, best = f, d
        return {"fences": len(fences), "inside": False, "fence": nearest, "distance_m": best}

    @staticmethod
    def check(*, rep, gps, action, customer=None, document=None, user=None, enforce=True):
        """
        The guard the rest of the system calls. It records what it saw and, under a blocking policy, refuses.

        A rep with no area is never restricted — handing someone an area is what switches checks on for them.
        Under `block`, a fenced rep must say where they are: a missing fix cannot be allowed to be a way around
        the rule, so it is refused too (turn `geofence_require_location` off while apps catch up).

        `enforce=False` records the verdict and never refuses. That is what an observation needs: a check-in
        says "I was at this shop", and a rep standing outside their polygon still visited it. Refusing the
        write would destroy the very evidence the record exists to keep — so the position is stored, the
        verdict with it, and the office judges.
        """
        from apps.core.exceptions import DomainError
        from apps.core.tenancy import get_current_tenant, get_current_user

        from .models import GeoFenceEvent

        tenant = get_current_tenant()
        policy = (tenant.setting("geofence_policy") if tenant else "off") or "off"
        if policy == "off" or rep is None:
            return None
        fences = list(GeoFenceService.fences_for(rep))
        if not fences:
            return None

        point = coords(gps)
        where = GeoFenceService.locate(rep, point) if point else {"inside": False, "fence": None, "distance_m": None}
        require_fix = tenant.setting("geofence_require_location") if tenant else True
        if point is None:
            verdict, ok = "no_fix", not (policy == "block" and require_fix)
        else:
            verdict, ok = ("inside", True) if where["inside"] else ("outside", policy != "block")

        fields = dict(
            rep=rep, user=user or (get_current_user() if getattr(get_current_user(), "is_authenticated", False) else None),
            action=action, verdict=verdict, blocked=enforce and not ok,
            gps_lat=point[0] if point else None, gps_lng=point[1] if point else None,
            fence=where.get("fence"), distance_m=where.get("distance_m"), customer=customer,
            document_type=type(document).__name__ if document is not None else "",
            document_id=str(getattr(document, "pk", "") or ""), document_number=str(getattr(document, "number", "") or ""),
        )
        event = GeoFenceEvent.objects.create(**fields)
        if ok or not enforce:
            return event

        # A refusal is the most interesting thing to record, so the row is written *before* the error is raised —
        # and `guard()` below turns that error into a returned response, because DRF marks the request for
        # rollback whenever an API error is raised, which would take this row with it.
        if verdict == "no_fix":
            error = DomainError(
                "Turn on location to record this — your work area is checked.",
                code="location_required", status_code=403,
            )
        else:
            near = where.get("fence")
            how_far = f"{where['distance_m'] / 1000:.1f} km" if (where.get("distance_m") or 0) >= 1000 else f"{where.get('distance_m') or 0} m"
            error = DomainError(
                f"You are {how_far} outside {near.name if near else 'your work area'}. This can only be recorded inside the area you cover.",
                code="outside_area", status_code=403,
                errors={"distance_m": [where.get("distance_m")], "fence": [near.name if near else ""]},
            )
        error.geofence_event = event
        raise error

    @staticmethod
    def link(rep, action, document):
        """
        Point the check that just passed at the document it allowed. The order does not exist when the check runs,
        so the event is written first and claimed here — that is what makes the out-of-area report clickable.
        """
        from datetime import timedelta

        from .models import GeoFenceEvent

        if rep is None or document is None:
            return None
        event = (GeoFenceEvent.objects
                 .filter(rep=rep, action=action, document_id="", occurred_at__gte=timezone.now() - timedelta(minutes=2))
                 .order_by("-occurred_at").first())
        if event is None:
            return None
        event.document_type = type(document).__name__
        event.document_id = str(getattr(document, "pk", "") or "")
        event.document_number = str(getattr(document, "number", "") or "")
        event.save(update_fields=["document_type", "document_id", "document_number"])
        return event

    @staticmethod
    def guard(**kwargs):
        """
        The form an endpoint uses: `blocked = GeoFenceService.guard(...)` → `if blocked: return blocked`.

        It hands back a ready 403 instead of raising one, so the record of the refusal survives the request.
        """
        from apps.core.exceptions import DomainError

        try:
            GeoFenceService.check(**kwargs)
        except DomainError as error:
            return block_response(error)
        return None
