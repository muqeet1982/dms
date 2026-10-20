"""
Check-in: the trace a visit leaves when nothing is sold.

The photo is the point, so most of these tests are about the photo being real — taken at the shop, now, rather
than picked out of a gallery.
"""
import base64
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

GULBERG = (31.5180, 74.3480)
KARACHI = (24.8607, 67.0011)

# the smallest valid JPEG-ish payload: the endpoint only cares that the bytes decode
PHOTO = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xdb" + b"0" * 64).decode()


@pytest.fixture
def rep_app(api, tenant):
    """A rep with a login, a shop with a pin, and an area around the shop."""
    from decimal import Decimal

    from apps.accounts.models import TenantMembership, User
    from apps.accounts.services import RoleService, UserService
    from apps.partners.models import GeoFence, GeoFenceRep, SupplierRep

    from .conftest import seed_basic

    wh, supplier, variant, customer = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        rep = SupplierRep.objects.create(supplier=supplier, code="R-VIS", name="Visiting Rep")
        customer.gps_lat, customer.gps_lng = Decimal(str(GULBERG[0])), Decimal(str(GULBERG[1]))
        customer.save(update_fields=["gps_lat", "gps_lng"])
        customer.assigned_reps.add(rep)
        fence = GeoFence.objects.create(name="Gulberg", type="circle", centre_lat=Decimal(str(GULBERG[0])),
                                        centre_lng=Decimal(str(GULBERG[1])), radius_m=3000)
        GeoFenceRep.objects.create(fence=fence, rep=rep)
        role = RoleService.create_role(tenant, "Visiting", ["external.view_assigned_customers", "external.submit_order",
                                                            "external.view_own_orders", "tracking.view_own",
                                                            "visits.record", "visits.view_own",
                                                            "external.view_stock_bands", "external.add_customer_request"], role_type="external")
        UserService.create_tenant_user(tenant, "Visiting Rep", email="visit@probe.pk", password="Abcd-1234", roles=[role], warehouses=[])
        user = User.objects.get(email="visit@probe.pk")
        User.objects.filter(pk=user.pk).update(must_change_password=False)
        rep.user, rep.role = user, role
        rep.save(update_fields=["user", "role"])
        TenantMembership.objects.filter(user=user, tenant=tenant).update(supplier_rep=rep)

    from rest_framework.test import APIClient

    phone = APIClient()
    login = phone.post("/api/v1/auth/login", {"identifier": "visit@probe.pk", "password": "Abcd-1234"}, format="json")
    assert login.status_code == 200, login.content
    phone.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}", HTTP_X_TENANT_ID=str(tenant.id))
    return {"tenant": tenant, "rep": rep, "user": user, "customer": customer, "variant": variant,
            "warehouse": wh, "phone": phone}


def _check_in(rep_app, **over):
    now = timezone.now()
    body = {"customer": str(rep_app["customer"].id), "outcome": "no_order", "no_order_reason": "closed",
            "gps": {"lat": GULBERG[0], "lng": GULBERG[1], "accuracy_m": 9.0},
            "checked_in_at": now.isoformat(), "photo": PHOTO, "photo_taken_at": now.isoformat(),
            "client_id": "visit-1", "note": "shutter down"}
    body.update(over)
    return rep_app["phone"].post("/ext/v1/visits", body, format="json")


def test_a_closed_shop_still_leaves_a_record(rep_app):
    """The whole point: a day with no orders is no longer a day with no evidence."""
    r = _check_in(rep_app)
    assert r.status_code == 201, r.content
    assert r.data["outcome"] == "no_order" and r.data["no_order_reason"] == "closed"
    assert r.data["reason_label"] == "Shop was closed"
    assert r.data["customer_name"] == rep_app["customer"].name
    assert r.data["photo_url"], "the photo is the evidence — it has to be stored"
    assert r.data["inside_area"] is True
    assert r.data["customer_distance_m"] < 5, "standing on the shop's own pin"


def test_a_visit_must_say_why_there_was_no_order(rep_app):
    r = _check_in(rep_app, no_order_reason="")
    assert r.status_code == 400 and r.data["type"] == "reason_required", r.content
    assert _check_in(rep_app, no_order_reason="made_it_up").status_code == 400


def test_a_photo_from_the_gallery_is_refused(rep_app):
    """A picture taken hours before the rep arrived is not proof of being at the shop."""
    now = timezone.now()
    r = _check_in(rep_app, checked_in_at=now.isoformat(),
                  photo_taken_at=(now - timedelta(hours=5)).isoformat())
    assert r.status_code == 400 and r.data["type"] == "photo_not_live", r.content
    assert "at this visit" in r.data["detail"]

    after = _check_in(rep_app, checked_in_at=now.isoformat(), closed_at=(now + timedelta(minutes=5)).isoformat(),
                      photo_taken_at=(now + timedelta(hours=3)).isoformat())
    assert after.status_code == 400, "a photo taken long after they left is not this visit either"


def test_a_day_with_no_signal_is_not_a_day_of_refusals(rep_app):
    """
    The bug the app team found with the numbers: freshness was measured against **our** receive time, so a rep
    who worked a beat with no coverage and flushed the queue at six in the evening had every visit refused —
    each one carrying a real photo, taken at the real shop, at the real time.

    The test is the visit's own clock, so an eight-hour-old batch is fine and a gallery pick still is not.
    """
    arrived = timezone.now() - timedelta(hours=8)
    late = _check_in(rep_app, checked_in_at=arrived.isoformat(),
                     closed_at=(arrived + timedelta(minutes=12)).isoformat(),
                     photo_taken_at=(arrived + timedelta(seconds=30)).isoformat())
    assert late.status_code == 201, late.content
    assert late.data["photo_verified"] is True

    # and a long visit on a good connection: photo on arrival, order written, closed forty minutes later
    start = timezone.now() - timedelta(minutes=40)
    slow = _check_in(rep_app, client_id="slow", checked_in_at=start.isoformat(),
                     closed_at=timezone.now().isoformat(), photo_taken_at=start.isoformat())
    assert slow.status_code == 201, "taking an order takes longer than ten minutes"


def test_a_photo_with_no_capture_time_is_kept_but_not_vouched_for(rep_app):
    """
    The app team spotted that omitting `photo_taken_at` would wave any photo through, and declined to use it.
    An older build is still accepted — but the row says nothing vouches for the photo.
    """
    r = _check_in(rep_app, photo_taken_at=None)
    assert r.status_code == 201 and r.data["photo_verified"] is False, r.content

    good = _check_in(rep_app, client_id="vouched")
    assert good.data["photo_verified"] is True


def test_a_check_in_with_no_photo_is_refused_while_the_company_requires_one(rep_app):
    r = _check_in(rep_app, photo=None)
    assert r.status_code == 400 and r.data["type"] == "photo_required", r.content

    t = rep_app["tenant"]
    t.settings = {**(t.settings or {}), "rep_visit_photo": "optional"}
    t.save(update_fields=["settings"])
    assert _check_in(rep_app, photo=None, client_id="visit-2").status_code == 201


def test_an_older_app_that_sends_no_capture_time_is_still_accepted(rep_app):
    """Freshness is checked when the app reports it; a missing field is not treated as a lie."""
    r = _check_in(rep_app, photo_taken_at=None)
    assert r.status_code == 201, r.content


def test_a_retried_check_in_does_not_double_up(rep_app):
    first = _check_in(rep_app)
    again = _check_in(rep_app)
    assert first.status_code == 201 and again.status_code == 201
    assert first.data["id"] == again.data["id"], "the same client_id is the same visit"

    from apps.partners.models import CustomerVisit

    with tenant_context(rep_app["tenant"], rep_app["tenant"].owner):
        assert CustomerVisit.objects.count() == 1


def test_a_visit_outside_the_area_is_kept_not_refused(rep_app):
    """
    An order is a transaction and the area rule guards it. A visit is an observation: refusing it would destroy
    the evidence the record exists to keep. So the position is stored, the verdict with it, and the office judges.
    """
    r = _check_in(rep_app, gps={"lat": KARACHI[0], "lng": KARACHI[1], "accuracy_m": 9.0})
    assert r.status_code == 201, r.content
    assert r.data["inside_area"] is False, "the office can see it was out of area"
    assert r.data["customer_distance_m"] > 1_000_000, "and how far from the shop they were"

    from apps.partners.models import GeoFenceEvent

    with tenant_context(rep_app["tenant"], rep_app["tenant"].owner):
        event = GeoFenceEvent.objects.filter(action="visit").order_by("-occurred_at").first()
        assert event is not None and event.verdict == "outside"
        assert event.blocked is False, "recorded, never refused"

    # an order from the same spot is still refused — the two rules are deliberately different
    order = rep_app["phone"].post("/ext/v1/orders", {
        "customer": {"id": str(rep_app["customer"].id)},
        "gps": {"lat": KARACHI[0], "lng": KARACHI[1], "accuracy_m": 9.0},
        "lines": [{"variant_id": str(rep_app["variant"].id), "qty": 1}],
    }, format="json")
    assert order.status_code == 403 and order.data["type"] == "outside_area"


def test_the_visit_shows_in_the_day_and_on_the_office_list(rep_app, api):
    assert _check_in(rep_app).status_code == 201

    day = api.get(f"/api/v1/tracking/users/{rep_app['user'].pk}")
    assert day.status_code == 200, day.content
    visit = next((e for e in day.data["events"] if e["type"] == "visit"), None)
    assert visit is not None, day.data["events"]
    assert visit["customer_name"] == rep_app["customer"].name and visit["outcome"] == "no_order"

    office = api.get("/api/v1/visits")
    assert office.status_code == 200 and office.data["no_order"] == 1
    assert office.data["results"][0]["rep_name"] == "Visiting Rep"


def test_a_rep_sees_their_own_visits_and_the_rules_to_follow(rep_app):
    assert _check_in(rep_app).status_code == 201
    mine = rep_app["phone"].get("/ext/v1/visits")
    assert mine.status_code == 200 and len(mine.data["results"]) == 1
    assert mine.data["policy"]["photo_source"] == "camera", "the app is told not to offer the gallery"
    assert mine.data["policy"]["photo"] == "required"
    assert any(r["value"] == "closed" for r in mine.data["policy"]["reasons"]), "the app renders whatever labels we send"

    areas = rep_app["phone"].get("/ext/v1/my-areas")
    assert areas.data["visit"]["photo_source"] == "camera", "and it arrives in the one login call too"


def test_a_visit_that_did_sell_can_point_at_its_order(rep_app):
    order = rep_app["phone"].post("/ext/v1/orders", {
        "customer": {"id": str(rep_app["customer"].id)},
        "gps": {"lat": GULBERG[0], "lng": GULBERG[1], "accuracy_m": 9.0},
        "lines": [{"variant_id": str(rep_app["variant"].id), "qty": 1}],
    }, format="json")
    assert order.status_code == 201, order.content
    r = _check_in(rep_app, outcome="order", no_order_reason="", order_ref=order.data["number"], client_id="visit-sold")
    assert r.status_code == 201 and r.data["outcome"] == "order" and r.data["no_order_reason"] == ""
    assert r.data["order_ref"] == order.data["number"], "the visit and its order are joined"


def test_the_phones_queue_sends_a_batch_and_a_retry_changes_nothing(rep_app):
    """§1 of the app's contract: visits ride the same offline queue as pings, so the answer is shaped the same."""
    shop = str(rep_app["customer"].id)
    now = timezone.now()
    # a real beat: three calls through the morning, each photo taken as the rep arrived
    def call(i):
        arrived = now - timedelta(minutes=30 - i * 10)
        return {"client_id": f"b{i}", "customer": shop,
                "checked_in_at": arrived.isoformat(), "closed_at": (arrived + timedelta(minutes=4)).isoformat(),
                "gps": {"lat": GULBERG[0], "lng": GULBERG[1], "accuracy_m": 11.0},
                "outcome": "no_order", "no_order_reason": "has_stock", "note": f"call {i}",
                "photo": PHOTO, "photo_taken_at": arrived.isoformat()}

    batch = {"visits": [call(i) for i in range(3)]}

    first = rep_app["phone"].post("/ext/v1/visits", batch, format="json")
    assert first.status_code == 202, first.content
    assert first.data == {"accepted": 3, "duplicates": 0, "rejected": 0}

    again = rep_app["phone"].post("/ext/v1/visits", batch, format="json")
    assert again.data == {"accepted": 0, "duplicates": 3, "rejected": 0}, "a retried batch is not six visits"


def test_a_malformed_item_in_a_batch_names_its_index(rep_app):
    """The app drops a 4xx batch and retries a 5xx one, so a bad row must be a 400 that says which row."""
    now = timezone.now().isoformat()
    bad = rep_app["phone"].post("/ext/v1/visits", {"visits": [
        {"client_id": "ok", "customer": str(rep_app["customer"].id), "checked_in_at": now,
         "outcome": "no_order", "no_order_reason": "closed", "photo": PHOTO, "photo_taken_at": now},
        {"client_id": "nope", "customer": "no-such-shop", "checked_in_at": now, "outcome": "no_order",
         "no_order_reason": "closed", "photo": PHOTO, "photo_taken_at": now},
    ]}, format="json")
    assert bad.status_code == 400 and "visits[1]" in bad.data["detail"], bad.content

    empty = rep_app["phone"].post("/ext/v1/visits", {"visits": []}, format="json")
    assert empty.status_code == 400

    huge = rep_app["phone"].post("/ext/v1/visits", {"visits": [{"client_id": f"h{i}"} for i in range(101)]}, format="json")
    assert huge.status_code == 413, huge.content


def test_the_office_gets_the_summary_a_distributor_actually_reads(rep_app, api):
    """Twelve calls with four orders is a different day from four calls with four orders."""
    shop = str(rep_app["customer"].id)
    now = timezone.now()
    rep_app["phone"].post("/ext/v1/visits", {"visits": [
        {"client_id": f"s{i}", "customer": shop, "checked_in_at": now.isoformat(),
         "gps": {"lat": GULBERG[0], "lng": GULBERG[1]}, "outcome": "no_order",
         "no_order_reason": "has_stock" if i < 2 else "closed", "photo": PHOTO, "photo_taken_at": now.isoformat()}
        for i in range(3)
    ]}, format="json")
    assert _check_in(rep_app, outcome="order", no_order_reason="", client_id="sold").status_code == 201

    out = api.get("/api/v1/visits", {"date": timezone.localdate().isoformat()})
    assert out.status_code == 200, out.content
    assert out.data["count"] == 4 and out.data["page"] == 1
    row = out.data["summary"][0]
    assert row["visits"] == 4 and row["orders"] == 1 and row["no_orders"] == 3
    assert row["by_reason"] == {"has_stock": 2, "closed": 1}, row["by_reason"]
    assert row["user_name"] == "Visiting Rep"


def test_a_rep_cannot_read_the_teams_visits(rep_app):
    mine = rep_app["phone"].get("/api/v1/visits")
    assert mine.status_code == 200, "their own visits are theirs"
    assert mine.data["summary"] == [] or all(s["user_name"] in ("Visiting Rep", "") for s in mine.data["summary"])

    assert _check_in(rep_app).status_code == 201
    only_mine = rep_app["phone"].get("/api/v1/visits")
    assert only_mine.data["count"] == 1


def test_the_old_reason_keys_still_work(rep_app):
    """The first keys were ours; the app's are canonical now. A phone that has not updated is not broken."""
    r = _check_in(rep_app, no_order_reason="shop_closed")
    assert r.status_code == 201 and r.data["no_order_reason"] == "closed", r.content


def test_a_batch_error_keeps_the_code_the_phone_branches_on(rep_app):
    """`type` rides on the detail string; naming the index must not flatten it back to `domain_error`."""
    now = timezone.now()
    r = rep_app["phone"].post("/ext/v1/visits", {"visits": [{
        "client_id": "stale", "customer": str(rep_app["customer"].id), "checked_in_at": now.isoformat(),
        "outcome": "no_order", "no_order_reason": "closed",
        "photo": PHOTO, "photo_taken_at": (now - timedelta(hours=5)).isoformat(),
    }]}, format="json")
    assert r.status_code == 400 and r.data["type"] == "photo_not_live", r.content
    assert r.data["detail"].startswith("visits[0]: ")


def test_an_item_with_nothing_in_the_warehouse_comes_back_as_out(rep_app):
    """
    What a rep must not order was the one thing the list left out.

    `/ext/v1/stock` was built from stock rows, so a variant with nothing in the warehouse had no row to
    build from and simply **vanished** from the response — the app had nothing to grey out, and the item
    looked no different from one it had never heard of. The list now starts from the catalogue.
    """
    from decimal import Decimal

    from apps.catalog.models import Product, ProductVariant
    from apps.partners.models import Supplier

    with tenant_context(rep_app["tenant"], rep_app["tenant"].owner):
        supplier = Supplier.objects.get(pk=rep_app["rep"].supplier_id)
        empty = Product.objects.create(item_code="NOSTOCK", sku="NOSTOCK", name="Nothing In Stock", supplier=supplier,
                                       base_uom=rep_app["variant"].product.base_uom, status="active")
        ProductVariant.objects.create(product=empty, sku="NOSTOCK-1", name="Nothing In Stock",
                                      sale_price=Decimal("100"), is_default=True, is_active=True)

    rows = {r["sku"]: r for r in rep_app["phone"].get("/ext/v1/stock").data["results"]}
    assert "NOSTOCK-1" in rows, "an item with no stock row must still be listed, as out"
    assert rows["NOSTOCK-1"]["band"] == "out"
    assert rep_app["variant"].sku in rows and rows[rep_app["variant"].sku]["band"] != "out"


def test_a_band_only_role_is_not_handed_an_empty_stock_field(rep_app):
    """A field that is always null is worse than no field: the app cannot tell "none" from "not told"."""
    rows = rep_app["phone"].get("/ext/v1/products").data["results"]
    assert rows and "stock_on_hand" not in rows[0], "a role that may not see the number should not see the key"
    assert "band" in rep_app["phone"].get("/ext/v1/stock").data["results"][0]


def test_a_role_trusted_with_the_number_gets_it_on_both_calls(rep_app):
    """`available` already existed — it is gated on `external.view_stock_exact`, which is a company's choice."""
    from apps.accounts.services import RoleService

    with tenant_context(rep_app["tenant"], rep_app["tenant"].owner):
        RoleService.update_role(rep_app["rep"].role, permissions=[
            "external.view_assigned_customers", "external.submit_order", "external.view_own_orders",
            "visits.record", "visits.view_own", "external.view_stock_exact",
        ])

    stock = rep_app["phone"].get("/ext/v1/stock").data["results"][0]
    assert "available" in stock and "on_hand" in stock and "band" in stock, stock

    product = rep_app["phone"].get("/ext/v1/products").data["results"][0]
    assert product["stock_on_hand"] is not None and "available" in product


def test_a_retried_new_shop_is_one_shop(rep_app):
    """
    The app queues a new shop and retries a lost connection. Without an idempotency key that is two shops,
    two codes and two ledgers for one shopkeeper — which is what the mobile team measured.
    """
    body = {"name": "Naya Beauty Centre", "type": "retailer", "phone": "03001234567", "cnic": "35202-1234567-1",
            "gps": {"lat": GULBERG[0], "lng": GULBERG[1], "accuracy_m": 9.0}}
    first = rep_app["phone"].post("/ext/v1/customers", body, format="json", HTTP_IDEMPOTENCY_KEY="newshop-1")
    assert first.status_code == 201, first.content
    again = rep_app["phone"].post("/ext/v1/customers", body, format="json", HTTP_IDEMPOTENCY_KEY="newshop-1")
    assert again.status_code == 200 and again.data["code"] == first.data["code"], again.content

    from apps.partners.models import Customer

    with tenant_context(rep_app["tenant"], rep_app["tenant"].owner):
        assert Customer.objects.filter(name="Naya Beauty Centre").count() == 1


def test_a_made_up_shop_kind_is_refused_and_the_list_is_published(rep_app):
    r = rep_app["phone"].post("/ext/v1/customers", {
        "name": "Kiryana Corner", "type": "kiryana",
        "gps": {"lat": GULBERG[0], "lng": GULBERG[1]},
    }, format="json")
    assert r.status_code == 400 and r.data["type"] == "bad_customer", r.content
    assert "retailer" in r.data["detail"]

    areas = rep_app["phone"].get("/ext/v1/my-areas")
    assert [t["value"] for t in areas.data["customer_types"]] == ["retailer", "salon", "wholesaler", "lmt", "other"]


def test_a_missing_name_is_reported_even_when_the_fix_is_missing_too(rep_app):
    """The area check used to fire first, so a rep fixed their location only to be told the name was blank."""
    r = rep_app["phone"].post("/ext/v1/customers", {"type": "retailer"}, format="json")
    assert r.status_code == 400 and "name" in r.data["detail"], r.content


def test_the_shop_a_rep_cannot_find_in_search_is_found_before_it_is_duplicated(rep_app):
    """The rep most likely to add a shop is the one search failed, so the duplicate comes through that door."""
    existing = rep_app["customer"]
    with tenant_context(rep_app["tenant"], rep_app["tenant"].owner):
        existing.name, existing.phone, existing.city = "Salon One & Co.", "0300-111 2233", "Lahore"
        existing.save(update_fields=["name", "phone", "city"])

    typed = rep_app["phone"].get("/ext/v1/customers/similar", {"name": "salon one and co"})
    assert typed.data["count"] == 1 and typed.data["results"][0]["matched_on"] == "name", typed.data
    assert typed.data["results"][0]["code"] == existing.code

    by_phone = rep_app["phone"].get("/ext/v1/customers/similar", {"phone": "+92 300 1112233"})
    assert by_phone.data["count"] == 1 and by_phone.data["results"][0]["matched_on"] == "phone"

    nothing = rep_app["phone"].get("/ext/v1/customers/similar", {"name": "Somewhere Else Entirely"})
    assert nothing.data["count"] == 0
    assert rep_app["phone"].get("/ext/v1/customers/similar").status_code == 400


def test_a_rep_can_sell_to_a_shop_the_office_has_not_approved_yet(rep_app):
    """A rep adds a shop and takes its first order on the same visit — only `blocked` stops a sale."""
    new = rep_app["phone"].post("/ext/v1/customers", {
        "name": "First Order Shop", "type": "retailer", "cnic": "35202-1234567-1",
        "gps": {"lat": GULBERG[0], "lng": GULBERG[1]},
    }, format="json")
    assert new.status_code == 201 and new.data["status"] == "pending"

    order = rep_app["phone"].post("/ext/v1/orders", {
        "customer": {"id": new.data["id"]},
        "gps": {"lat": GULBERG[0], "lng": GULBERG[1], "accuracy_m": 9.0},
        "lines": [{"variant_id": str(rep_app["variant"].id), "qty": 3}],
    }, format="json")
    assert order.status_code == 201, order.content


def test_a_walked_away_call_is_filterable_not_disguised_as_something_else(rep_app):
    """The app closes a call the rep abandoned (next day, or checking in elsewhere). `no_outcome` keeps that
    distinguishable from "not interested" — prose in a note cannot be filtered on."""
    r = _check_in(rep_app, no_order_reason="no_outcome", client_id="walked-1",
                  closed_at=timezone.now().isoformat(),
                  note="Left on an earlier day without recording an outcome; closed by the app.")
    assert r.status_code in (200, 201, 202), r.content

    reasons = [x["value"] for x in rep_app["phone"].get("/ext/v1/my-areas").data["visit"]["reasons"]]
    assert "no_outcome" in reasons, "the app renders the server's own list, so the value must be published"


def test_areas_leave_the_rep_payload_under_the_shop_rule(rep_app, tenant):
    """The owner retired employee area geofencing: under shop_proximity my-areas sends no areas at all,
    and a company switched back to area mode gets them again for the offline gate."""
    assert rep_app["phone"].get("/ext/v1/my-areas").data["areas"] == []

    with tenant_context(tenant, tenant.owner):
        tenant.settings = {**(tenant.settings or {}), "order_location_rule": "area"}
        tenant.save(update_fields=["settings"])
    assert len(rep_app["phone"].get("/ext/v1/my-areas").data["areas"]) >= 1
