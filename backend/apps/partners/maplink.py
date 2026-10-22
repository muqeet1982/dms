"""
A shop's location the way people actually have it: a Google Maps link, a shared short link, or a bare
"31.52, 74.33". One function turns any of them into a (lat, lng) pair, or None when it honestly cannot.

Pattern order matters: a place URL carries both the map centre (`@lat,lng`) and the pin (`!3d..!4d..`), and
the pin is the shop — so it is tried first. Short links (maps.app.goo.gl) only reveal their coordinates
after a redirect, so those are followed, briefly and only for known Google hosts.
"""
import re

import requests

_PIN = re.compile(r"!3d(-?\d{1,3}\.\d+)!4d(-?\d{1,3}\.\d+)")
# a WhatsApp "share location" is a maps.google.com/?q=lat,lng link; Android shares also come as geo:lat,lng
_GEO = re.compile(r"geo:(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)", re.I)
_QUERY = re.compile(r"[?&](?:q|ll|query|destination|center)=(?:loc:)?(-?\d{1,3}\.\d+)(?:%2C|,|\+|\s)+(-?\d{1,3}\.\d+)", re.I)
_AT = re.compile(r"@(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)")
_BARE = re.compile(r"^\s*(-?\d{1,3}\.\d+)\s*[, ]\s*(-?\d{1,3}\.\d+)\s*$")
_SHORT_HOSTS = ("maps.app.goo.gl", "goo.gl", "g.co")


def _match(text):
    for pattern in (_PIN, _GEO, _QUERY, _AT, _BARE):
        m = pattern.search(text or "")
        if m:
            lat, lng = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                return lat, lng
    return None


def parse_location(text, follow_links=True):
    """(lat, lng) out of a maps URL, short link or "lat, lng" text — or None."""
    text = str(text or "").strip()
    if not text:
        return None
    found = _match(text)
    if found:
        return found
    if follow_links and any(h in text for h in _SHORT_HOSTS):
        try:
            # the coordinates live in the URL the short link redirects to, not in the page
            r = requests.get(text, allow_redirects=True, timeout=6, headers={"User-Agent": "Mozilla/5.0"})
            return _match(r.url) or _match(" ".join(h.headers.get("Location", "") for h in r.history))
        except requests.RequestException:
            return None
    return None
