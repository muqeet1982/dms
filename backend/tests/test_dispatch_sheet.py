"""
The printable dispatch sheet — the paper the driver carries out of the warehouse.

Covers the two ways this has gone wrong in practice. First, the endpoint answering with something that is
not a PDF: a template that cannot render, or DRF claiming the `?format=` query parameter and returning 404
before the view runs, both reach the browser as a print button that silently does nothing. Second, a sheet
that renders but is useless on the road — the driver needs the stop in sequence with the customer's phone
and address, the order it belongs to and the items to hand over, so the PDF's own text is checked for them.
"""
import pytest
from decimal import Decimal

pytestmark = pytest.mark.django_db


@pytest.fixture
def dispatch(api, tenant):
    """A trip out for delivery with one stop: a confirmed order, issued by the warehouse, on a vehicle."""
    from apps.core.tenancy import tenant_context
    from apps.delivery.models import Driver, Vehicle

    from .conftest import seed_basic

    wh, _sup, variant, customer = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        customer.phone = "+923001234567"
        customer.address = "Shop 4, Gulberg"
        customer.credit_days = 0  # a cash customer, so the sheet has to tell the driver to collect
        customer.save()
        vehicle = Vehicle.objects.create(reg_no="LEA-1234", type="van")
        driver = Driver.objects.create(name="Akram", phone="03001234567")

    r = api.post("/api/v1/sales-orders", {"customer": str(customer.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(variant.id), "qty": "6"}]}, format="json")
    assert r.status_code == 201, r.content
    order = r.data

    r = api.post(f"/api/v1/sales-orders/{order['id']}/load-form", {}, format="json")
    assert r.status_code == 201, r.content
    picklist = r.data
    issues = [{"line_id": line["id"], "qty_issued": line["qty_to_pick"]} for line in picklist["lines"]]
    assert api.post(f"/api/v1/pick-lists/{picklist['id']}/issue", {"issues": issues}, format="json").status_code == 200

    r = api.post("/api/v1/trips", {"warehouse": str(wh.id), "driver": str(driver.id), "vehicle": str(vehicle.id), "order_ids": [str(order["id"])]}, format="json")
    assert r.status_code == 201, r.content
    trip = r.data
    assert api.post(f"/api/v1/trips/{trip['id']}/start", {"odometer_start": 12000}, format="json").status_code == 200
    return trip, order, customer, picklist


def _sheet_html(tenant, trip_id):
    """
    The document the PDF is made of.

    WeasyPrint writes text as glyph ids from a subset font, so the finished PDF cannot be searched without a
    font-aware parser. The content assertions therefore run against the same template render the PDF is
    produced from, and the HTTP tests below check that the render really does come back as a PDF.
    """
    from django.template.loader import render_to_string

    from apps.core.tenancy import tenant_context
    from apps.delivery.models import DeliveryTrip
    from apps.delivery.views import dispatch_sheet_context

    with tenant_context(tenant, tenant.owner):
        trip = DeliveryTrip.objects.get(pk=trip_id)
        return render_to_string("delivery/dispatch_sheet.html", dispatch_sheet_context(trip, tenant))


def _is_pdf(response):
    return response["Content-Type"] == "application/pdf" and response.content.startswith(b"%PDF-") and len(response.content) > 2000


def test_dispatch_sheet_is_served_as_a_pdf(api, dispatch):
    """The dispatch screen's print and PDF buttons both hit this; a 404 or an HTML error page reads as a dead button."""
    trip, _order, _customer, _picklist = dispatch

    r = api.get(f"/api/v1/trips/{trip['id']}/dispatch-sheet")
    assert r.status_code == 200, r.content
    assert _is_pdf(r), "an HTML error page or an empty file is not a dispatch sheet"
    assert "dispatch-sheet.pdf" in r["Content-Disposition"]


def test_dispatch_sheet_carries_what_the_driver_needs(api, tenant, dispatch):
    """Everything a driver cannot complete the round without: the stop, how to reach it, and room to sign."""
    trip, order, customer, _picklist = dispatch

    html = _sheet_html(tenant, trip["id"])
    assert "Dispatch Sheet" in html and trip["number"] in html
    assert customer.name in html and customer.phone in html and "Shop 4, Gulberg" in html
    assert order["number"] in html, "the driver has to know which order the stop belongs to"
    assert "Akram" in html and "LEA-1234" in html, "driver and vehicle identify the sheet at the gate"
    assert "STOP 1" in html, "stops are walked in sequence, so they have to be numbered"
    assert "cash on delivery" in html, "a cash customer must be flagged or the driver leaves without the money"
    assert "To collect on this run" in html, "the run's cash total is what the driver is counted against on return"
    assert "Received by" in html and "Signature" in html, "nowhere to sign makes the sheet worthless as proof of delivery"


def test_trip_sheet_url_still_serves_the_same_document(api, dispatch):
    """The mobile app and docs/API.md publish /trip-sheet; it was renamed, so the old URL must not 404."""
    trip, _order, _customer, _picklist = dispatch

    old = api.get(f"/api/v1/trips/{trip['id']}/trip-sheet")
    assert old.status_code == 200 and _is_pdf(old)
    assert "dispatch-sheet.pdf" in old["Content-Disposition"], "the old URL must serve the new document, not a second one"


def test_sheet_renders_before_anything_is_delivered(api, tenant):
    """A trip still being planned is exactly when the sheet gets printed, so an empty trip must not 500."""
    from apps.core.tenancy import tenant_context
    from apps.inventory.models import Warehouse

    with tenant_context(tenant, tenant.owner):
        wh = Warehouse.objects.create(code="W2", name="Second", type="main")
    r = api.post("/api/v1/trips", {"warehouse": str(wh.id), "order_ids": []}, format="json")
    assert r.status_code == 201, r.content

    trip_id = r.data["id"]
    r = api.get(f"/api/v1/trips/{trip_id}/dispatch-sheet")
    assert r.status_code == 200 and _is_pdf(r)
    assert "No stops" in _sheet_html(tenant, trip_id), "the sheet should say the dispatch is empty, not print a blank page"


def test_load_form_pdf_renders(api, dispatch):
    """The warehouse prints the load form to pack against; it shares the PDF plumbing with the sheet."""
    _trip, _order, _customer, picklist = dispatch

    r = api.get(f"/api/v1/pick-lists/{picklist['id']}/pdf")
    assert r.status_code == 200 and _is_pdf(r)


def test_format_query_param_does_not_swallow_the_request(api, tenant):
    """
    DRF reads `?format=` to pick a renderer and 404s when none matches. With only JSONRenderer installed that
    killed every endpoint that reads the parameter itself — the customer statement PDF and all three report
    exports answered 404 before the view ran, and the buttons looked dead.
    """
    from apps.core.tenancy import tenant_context
    from apps.partners.models import Customer

    with tenant_context(tenant, tenant.owner):
        customer = Customer.objects.create(code="C-9", name="Statement Co", credit_limit=Decimal("1000"))

    r = api.get(f"/api/v1/customers/{customer.id}/statement?format=pdf")
    assert r.status_code == 200, r.content
    assert r["Content-Type"] == "application/pdf" and r.content.startswith(b"%PDF-")

    r = api.get("/api/v1/reports/sales_by_customer?format=pdf")
    assert r.status_code == 200, r.content
    assert r["Content-Type"] == "application/pdf" and r.content.startswith(b"%PDF-")
