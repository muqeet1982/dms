"""
A shop's location arrives as whatever the phone's share sheet produced. Every format people actually paste
must land as the same pin — and the pin in a place URL must beat the map centre next to it.
"""
import pytest

from apps.partners.maplink import parse_location

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("text,expected", [
    ("https://www.google.com/maps/place/X/@31.52,74.33,17z/data=!3d31.5204000!4d74.3387000", (31.5204, 74.3387)),
    ("https://www.google.com/maps/@31.5204,74.3387,15z", (31.5204, 74.3387)),
    ("https://maps.google.com/?q=31.5204,74.3387", (31.5204, 74.3387)),
    ("https://www.google.com/maps/search/?api=1&query=31.5204%2C74.3387", (31.5204, 74.3387)),
    ("31.5204, 74.3387", (31.5204, 74.3387)),
    ("31.5204 74.3387", (31.5204, 74.3387)),
    ("-33.8688, 151.2093", (-33.8688, 151.2093)),
])
def test_every_shared_format_lands_as_the_same_pin(text, expected):
    assert parse_location(text, follow_links=False) == expected


@pytest.mark.parametrize("text", ["", "just a shop name", "https://example.com/page", "999, 999"])
def test_what_is_not_a_location_says_so(text):
    assert parse_location(text, follow_links=False) is None


def test_the_place_pin_beats_the_map_centre():
    """A place URL carries both @centre and !3d!4d pin; the pin is the shop."""
    url = "https://www.google.com/maps/place/Shop/@31.0000,74.0000,17z/data=!3d31.4710!4d74.2800"
    assert parse_location(url, follow_links=False) == (31.471, 74.28)


def test_the_office_form_takes_a_maps_link_too(api, tenant):
    """The customer form (and its API) accepts map_url and turns it into the pin."""
    r = api.post("/api/v1/customers", {"name": "Pasted Pin Store", "type": "retailer", "cnic": "35202-1234567-1",
                                       "map_url": "https://maps.google.com/?q=31.4710,74.2800"}, format="json")
    assert r.status_code == 201, r.content
    assert (round(float(r.data["gps_lat"]), 4), round(float(r.data["gps_lng"]), 4)) == (31.471, 74.28)

    bad = api.post("/api/v1/customers", {"name": "Bad Pin Store", "cnic": "35202-1234567-1", "map_url": "no coordinates here"}, format="json")
    assert bad.status_code == 400, bad.content

    parsed = api.post("/api/v1/customers/parse-location", {"text": "https://www.google.com/maps/@31.52,74.33,15z"}, format="json")
    assert parsed.status_code == 200 and parsed.data == {"lat": 31.52, "lng": 74.33}
