"""
The code review of 27 Oct 2026's work: each test pins one defect it found, so it cannot come back.
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.core.exceptions import DomainError
from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db
D = Decimal


def _order(api, wh, cust, v, qty="5", **extra):
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(v.id), "qty": qty}], **extra}, format="json")
    assert r.status_code == 201, r.content
    return r.data


def _issue(api, o):
    pl = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json").data
    r = api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl["lines"]]}, format="json")
    assert r.status_code == 200, r.content
    return pl


def _invoice(api, o, post=True):
    return api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": post}, format="json").data[0]


def test_cash_on_delivery_against_a_draft_waits_for_it_instead_of_paying_older_invoices(api, tenant):
    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    older = _order(api, wh, cust, v, qty="2")
    _issue(api, older)
    old_inv = _invoice(api, older)                                   # posted, 300 open
    o = _order(api, wh, cust, v, qty="2")
    r = api.post(f"/api/v1/sales-orders/{o['id']}/mark-delivered", {"receiver_name": "Shop", "cash_collected": "200"}, format="json")
    assert r.status_code == 200, r.content
    assert D(api.get(f"/api/v1/invoices/{old_inv['id']}").data["paid_amount"]) == 0, "the older invoice is not touched"
    draft = api.get("/api/v1/invoices", {"order": o["id"]}).data["results"][0]
    assert draft["status"] == "draft"
    assert api.post(f"/api/v1/invoices/{draft['id']}/post", {}, format="json").status_code == 200
    assert D(api.get(f"/api/v1/invoices/{draft['id']}").data["paid_amount"]) == D("200"), "the cash waited for this invoice"


def test_no_receipt_path_can_settle_a_draft_invoice(api, tenant):
    from apps.accounting.services import PaymentService
    from apps.sales.models import SalesInvoice
    from apps.sales.services import RepCollectionService

    from .conftest import seed_basic

    wh, sup, v, cust = seed_basic(tenant)
    o = _order(api, wh, cust, v, qty="2")
    _issue(api, o)
    draft = _invoice(api, o, post=False)
    with tenant_context(tenant, tenant.owner):
        from apps.partners.models import SupplierRep

        rep = SupplierRep.objects.create(supplier=sup, code="R-T", name="Test Rep")
        with pytest.raises(DomainError, match="post it before"):
            PaymentService.record(direction="in", partner_type="customer", partner_id=str(cust.id), amount=D("100"),
                                  allocations=[{"document_type": "sales_invoice", "document_id": draft["id"], "amount": D("100")}])
        with pytest.raises(DomainError, match="post it before"):
            RepCollectionService.record(rep=rep, customer=cust, amount=D("100"), allocations=[{"invoice": draft["id"], "amount": "100"}])
        assert SalesInvoice.objects.get(pk=draft["id"]).paid_amount == 0


def test_filing_refuses_when_the_products_fbr_rates_moved_since_the_invoice_was_charged(api, tenant):
    from django.utils import timezone

    from apps.catalog.models import Product, TaxRate, TaxType
    from apps.fbr.services import build_payload
    from apps.sales.models import SalesInvoice

    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        tt = TaxType.objects.create(code="ST", name="Sales Tax", kind="sales_tax", applies_to="both")
        TaxRate.objects.create(tax_type=tt, rate_pct=D("18"), valid_from=timezone.localdate())
        Product.objects.filter(pk=v.product_id).update(fbr_rate_desc="18%", fbr_hs_code="7306.3090", fbr_sale_type="Goods at standard rate (default)",
                                                       fbr_uom="Numbers, pieces, units", fbr_fed_rate=D("2"))
    o = _order(api, wh, cust, v, qty="4", charge_tax=True)
    _issue(api, o)
    inv = _invoice(api, o)
    filing = SimpleNamespace(seller_ntn_cnic="1234567", seller_business_name="Demo", seller_province="Punjab", seller_address="Lahore",
                             buyer_ntn_cnic="", buyer_business_name=cust.name, buyer_province="Punjab", buyer_address="Lahore",
                             buyer_registration_type="Unregistered")
    with tenant_context(tenant, tenant.owner):
        build_payload(SalesInvoice.objects.get(pk=inv["id"]), filing, use_sandbox=False)   # unchanged rates file
        Product.objects.filter(pk=v.product_id).update(fbr_fed_rate=D("5"))
        with pytest.raises(DomainError, match="FBR rates changed"):
            build_payload(SalesInvoice.objects.get(pk=inv["id"]), filing, use_sandbox=False)


def test_the_warehouse_working_an_unsent_load_form_sends_it(api, tenant):
    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    o = _order(api, wh, cust, v)
    pl = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {}, format="json").data
    assert pl["status"] == "open"
    r = api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl["lines"]]}, format="json")
    assert r.status_code == 200, r.content
    assert r.data["sent_at"], "issuing an unsent form records it as sent"
    bad = api.post(f"/api/v1/sales-orders/{_order(api, wh, cust, v)['id']}/load-form", {}, format="json").data
    r = api.post(f"/api/v1/pick-lists/{bad['id']}/lines", {"lines": [{"line_id": bad["lines"][0]["id"], "qty_to_pick": "abc"}]}, format="json")
    assert r.status_code in (400, 422) and "number" in str(r.content)


def test_a_trip_letting_go_of_an_issued_order_keeps_its_note_and_never_issues_twice(api, tenant):
    from apps.delivery.models import DeliveryNote, Driver, Vehicle
    from apps.inventory.services import StockService

    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        driver = Driver.objects.create(name="Akram", phone="03001234567")
        vehicle = Vehicle.objects.create(reg_no="LEB-765")
        before = StockService.on_hand(v, wh)[0]
    o = _order(api, wh, cust, v)
    trip = api.post("/api/v1/trips", {"warehouse": str(wh.id), "driver": str(driver.id), "vehicle": str(vehicle.id),
                                      "delivery_rep": str(tenant.owner.id), "order_ids": [o["id"]]}, format="json").data
    _issue(api, o)
    assert api.get(f"/api/v1/sales-orders/{o['id']}").data["status"] == "dispatched"
    r = api.post(f"/api/v1/trips/{trip['id']}/cancel", {"reason": "van broke"}, format="json")
    assert r.status_code == 200, r.content
    with tenant_context(tenant, tenant.owner):
        notes = list(DeliveryNote.objects.filter(order_id=o["id"]))
        assert len(notes) == 1 and notes[0].trip_id is None and notes[0].stock_out_done, "the issued note survives, off the trip"
        assert StockService.on_hand(v, wh)[0] == before - 5
    assert api.get(f"/api/v1/sales-orders/{o['id']}").data["status"] == "issued"
    # a second trip takes the same note — nothing leaves the warehouse again
    t2 = api.post("/api/v1/trips", {"warehouse": str(wh.id), "driver": str(driver.id), "vehicle": str(vehicle.id),
                                    "delivery_rep": str(tenant.owner.id), "order_ids": [o["id"]]}, format="json")
    assert t2.status_code == 201, t2.content
    with tenant_context(tenant, tenant.owner):
        assert DeliveryNote.objects.filter(order_id=o["id"]).count() == 1
        assert StockService.on_hand(v, wh)[0] == before - 5


def test_a_cancelled_reopened_invoice_leaves_no_held_entry_and_held_entries_are_not_posted_by_hand(api, tenant):
    from apps.accounting.models import JournalEntry
    from apps.accounting.services import PostingService

    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    o = _order(api, wh, cust, v, qty="2")
    _issue(api, o)
    inv = _invoice(api, o)
    api.post(f"/api/v1/invoices/{inv['id']}/reopen", {"reason": "fix"}, format="json")
    with tenant_context(tenant, tenant.owner):
        held = JournalEntry.objects.filter(source_type="SalesInvoice", source_id=inv["id"], status="draft").first()
        assert held is not None
        with pytest.raises(DomainError, match="posted with that document"):
            PostingService.post_draft(held)
    assert api.post(f"/api/v1/invoices/{inv['id']}/cancel", {"reason": "wrong"}, format="json").status_code == 200
    with tenant_context(tenant, tenant.owner):
        assert not JournalEntry.objects.filter(source_type="SalesInvoice", source_id=inv["id"], status="draft").exists()


def test_a_new_customer_discount_rewrites_posted_invoices_only_for_who_may_and_drafts_respect_their_order(api, tenant):
    from apps.sales.models import SalesInvoice
    from apps.sales.services import OrderService

    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    o = _order(api, wh, cust, v, qty="2")
    _issue(api, o)
    posted = _invoice(api, o)
    own = _order(api, wh, cust, v, qty="2", discount_pct="0", apply_customer_discount=False)
    _issue(api, own)
    own_draft = _invoice(api, own, post=False)
    with tenant_context(tenant, tenant.owner):
        cust.default_discount_pct = D("10")
        cust.save(update_fields=["default_discount_pct"])
        done = OrderService.follow_customer_discount(cust, D("0"), D("10"), repost=False)
        assert posted["number"] in done["posted_invoices"] and not done["reposted_invoices"]
        assert SalesInvoice.objects.get(pk=posted["id"]).discount_pct == 0, "no right to re-post: listed, not rewritten"
        assert SalesInvoice.objects.get(pk=own_draft["id"]).discount_pct == 0, "its order took no customer discount"


def test_whoever_builds_a_trip_can_pick_its_delivery_person_without_the_users_page(api, tenant):
    """TDN, 28 Oct 2026: supervisor@… (Manager role: trips yes, users no) saw an empty Delivery person dropdown."""
    from rest_framework.test import APIClient

    from apps.accounts.models import User
    from apps.accounts.services import RoleService, UserService

    with tenant_context(tenant, tenant.owner):
        role = RoleService.create_role(tenant, "Dispatch supervisor", ["delivery.add_trip", "delivery.view_trip"])
        UserService.create_tenant_user(tenant, "Sup Visor", email="sup@probe.pk", password="Abcd-1234", roles=[role], warehouses=[])
        User.objects.filter(email="sup@probe.pk").update(must_change_password=False)
    c = APIClient()
    login = c.post("/api/v1/auth/login", {"identifier": "sup@probe.pk", "password": "Abcd-1234"}, format="json")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}", HTTP_X_TENANT_ID=str(tenant.id))
    assert c.get("/api/v1/users").status_code == 403, "the Users page stays theirs who hold its right"
    r = c.get("/api/v1/people", {"search": "Sup"})
    assert r.status_code == 200, r.content
    rows = r.data["results"] if isinstance(r.data, dict) else r.data
    me = next(x for x in rows if x["email"] == "sup@probe.pk")
    assert set(me) == {"id", "user_id", "full_name", "email", "role_names"}, "names only — no access details"
    assert c.get(f"/api/v1/people/{me['user_id']}").data["full_name"] == "Sup Visor"
