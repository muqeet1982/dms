"""
The OpenAPI schema is how the mobile AI (and any integrator) reads this API without asking us.

It broke once without anyone noticing: a field name pasted into the wrong serializer's `fields` list only
blows up when DRF *builds* the fields — which normal request paths for that endpoint also do, but nothing
exercises every serializer at once except schema generation. So this walks the whole surface in one go.
"""
import pytest

pytestmark = pytest.mark.django_db


def test_the_openapi_schema_generates_for_the_whole_api(capsys):
    from drf_spectacular.generators import SchemaGenerator

    schema = SchemaGenerator().get_schema(request=None, public=True)
    assert schema["paths"], "an empty schema would pass silently otherwise"
    assert any(p.startswith("/ext/v1/") for p in schema["paths"]), "the rep API must be documented too"
