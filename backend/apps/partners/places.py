"""
Looking a place up by name, so an area can start from "Clifton, Karachi" instead of a hand-drawn outline.

OpenStreetMap's Nominatim answers this for free. Two things to know about it, both visible in the result we hand
back so the screen can be honest about them:

* **coverage is uneven.** A town like Clifton comes back with a real outline; a neighbourhood like Gulberg or Johar
  Town is often only a point. When there is no outline the screen falls back to a circle the user can resize.
* **it is a shared, donated service.** We send one request at a time with our own user agent, cache every answer
  for a month, and never put it in a loop — their usage policy asks for exactly that.
"""
import json
import urllib.parse
import urllib.request

from django.core.cache import cache

ENDPOINT = "https://nominatim.openstreetmap.org/search"
AGENT = "DMS/1.0 (distribution management; https://dms.invoiceflow.pk)"
TIMEOUT = 8
CACHE_SECONDS = 30 * 24 * 3600


def _ring(geometry):
    """The outline as [{lat,lng}, …]. A multi-part shape keeps its largest ring — an area is one shape to a user."""
    if not geometry:
        return []
    kind = geometry.get("type")
    if kind == "Polygon":
        ring = geometry.get("coordinates", [[]])[0]
    elif kind == "MultiPolygon":
        parts = [p[0] for p in geometry.get("coordinates", []) if p]
        ring = max(parts, key=len) if parts else []
    else:
        return []
    return [{"lat": round(lat, 7), "lng": round(lng, 7)} for lng, lat in ring]


def search_places(query, limit=6):
    """
    [{name, label, lat, lng, points, bbox, radius_m, osm_ref, kind}] — `points` is the published outline when the
    place has one. `bbox` is the box the place occupies, which every result has: that is what the screen draws
    when there is no outline, so you start from the area that was searched for and stretch the sides you want,
    rather than guessing a radius.
    """
    query = (query or "").strip()
    if len(query) < 3:
        return []
    key = f"places:{query.lower()}"
    hit = cache.get(key)
    if hit is not None:
        return hit

    # `accept-language=en` asks for the English name of each place; OpenStreetMap answers in the local script
    # otherwise, which is how "Gulberg" came back as "گلبرگ"
    params = urllib.parse.urlencode({"q": query, "format": "jsonv2", "polygon_geojson": 1, "limit": limit,
                                     "addressdetails": 1, "accept-language": "en"})
    req = urllib.request.Request(f"{ENDPOINT}?{params}", headers={"User-Agent": AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            rows = json.loads(resp.read().decode())
    except Exception:
        return []  # offline or rate-limited: the screen falls back to dropping a pin by hand

    # a city is worth a wider default circle than a shop
    default_radius = {"city": 12000, "town": 6000, "suburb": 3000, "neighbourhood": 2000, "village": 2500,
                      "county": 25000, "state": 60000, "road": 800}
    out = []
    for r in rows:
        points = _ring(r.get("geojson"))
        kind = r.get("addresstype") or r.get("type") or ""
        label = r.get("display_name", "")
        # Nominatim's boundingbox is [south, north, west, east] as strings
        box = [float(v) for v in (r.get("boundingbox") or [])]
        bbox = {"south": box[0], "north": box[1], "west": box[2], "east": box[3]} if len(box) == 4 else None
        out.append({
            "name": (r.get("name") or label.split(",")[0]).strip(),
            "label": label,
            "kind": kind,
            "lat": float(r["lat"]),
            "lng": float(r["lon"]),
            "points": points,
            "bbox": bbox,
            "radius_m": 0 if points else default_radius.get(kind, 1500),
            "osm_ref": f"{r.get('osm_type', '')}/{r.get('osm_id', '')}",
        })
    cache.set(key, out, CACHE_SECONDS)
    return out
