"""
Platform admin's "Reset data": put one company back to a clean start without losing what it set up.

A distributor finishes a trial, or an import lands 500 wrong products and 3,000 wrong orders, and asks us to
clear it out. Before this they had two options — live with the mess or be re-created from scratch, losing their
users, warehouses and catalogue. The reset deletes the trading history and nothing else. It is irreversible, so
most of what is tested here is the refusals: the wrong slug, the wrong kind of user, the paying customer, and
above all the company next door, whose data must not move by a single row.
"""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.core.tenancy import tenant_context

from .conftest import make_tenant, seed_basic

pytestmark = pytest.mark.django_db

P = "/api/v1/platform"


@pytest.fixture
def admin_api(admin_user):
    c = APIClient()
    r = c.post("/api/v1/auth/login", {"identifier": "admin-test", "password": "pak1stan"}, format="json")
    assert r.status_code == 200, r.content
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
    return c


def trade(tenant):
    """
    A week of business: a purchase order, an order that went out and became an invoice with a journal entry
    behind it, a receipt against it, a rep's visit and ping, an attachment and a notification. One of
    everything the reset is meant to remove, created through the real services so the row graph is the real one.
    """
    from apps.accounting.services import PaymentService
    from apps.core.models import Attachment, Notification
    from apps.partners.models import CustomerVisit, GeoFence, GeoFenceEvent, LocationPing, SupplierRep
    from apps.purchasing.services import PurchaseOrderService
    from apps.sales.models import SalesInvoice
    from apps.sales.services import OrderService, RepCollectionService
    from django.utils import timezone

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        rep = SupplierRep.objects.create(supplier=sup, code="R-1", name="Field Rep")
        cust.assigned_reps.add(rep)
        PurchaseOrderService.create(supplier=sup, warehouse=wh, lines=[{"variant": v, "qty": Decimal("24"), "unit_price": Decimal("100")}])
        order = OrderService.create(customer=cust, lines=[{"variant": v, "qty": Decimal("10"), "unit_price": None}], source="staff", warehouse=wh)
        OrderService.confirm(order)
        OrderService.mark_delivered(order, receiver_name="Shop owner")
        invoice = SalesInvoice.objects.get(order=order)
        PaymentService.record(direction="in", partner_type="customer", partner_id=str(cust.id), amount=Decimal("500"), method="cash")
        RepCollectionService.record(rep=rep, customer=cust, amount=Decimal("250"))
        fence = GeoFence.objects.create(name="Lahore North", type="circle", centre_lat=Decimal("31.5"), centre_lng=Decimal("74.3"), radius_m=500)
        GeoFenceEvent.objects.create(rep=rep, action="order", verdict="inside", fence=fence, customer=cust)
        CustomerVisit.objects.create(customer=cust, rep=rep, at=timezone.now(), outcome="order", order=order)
        LocationPing.objects.create(user=tenant.owner, rep=rep, client_id="p1", at=timezone.now(), lat=Decimal("31.5"), lng=Decimal("74.3"))
        Attachment.objects.create(model="SalesInvoice", object_id=str(invoice.id), file="attachments/2026/01/pod.jpg", name="pod.jpg", size=2048, kind="pod_photo")
        Notification.objects.create(user=tenant.owner, type="order.delivered", title="Order delivered")
    return {"warehouse": wh, "supplier": sup, "variant": v, "customer": cust, "rep": rep, "fence": fence}


def counts(tenant, labels):
    from django.apps import apps as django_apps

    return {label: django_apps.get_model(label).all_objects.filter(tenant=tenant).count() for label in labels}


GONE = [
    "sales.SalesOrder", "sales.SalesOrderLine", "sales.SalesInvoice", "sales.SalesInvoiceLine", "sales.RepCollection",
    "purchasing.PurchaseOrder", "purchasing.PurchaseOrderLine", "delivery.DeliveryNote", "delivery.DeliveryNoteLine",
    "inventory.StockMovement", "inventory.StockLevel", "accounting.JournalEntry", "accounting.JournalLine",
    "accounting.Payment", "accounting.PaymentAllocation", "partners.CustomerVisit", "partners.LocationPing",
    "partners.GeoFenceEvent", "core.Attachment", "core.Notification",
]
KEPT = [
    "inventory.Warehouse", "catalog.Product", "catalog.ProductVariant", "catalog.Barcode", "catalog.UnitOfMeasure",
    "catalog.TaxClass", "catalog.PriceList", "partners.Customer", "partners.Supplier", "partners.SupplierRep",
    "partners.GeoFence", "accounting.Account", "inventory.ReasonCode", "core.NumberSequence",
]


def test_reset_deletes_the_trading_history_and_leaves_the_setup_standing(admin_api, tenant):
    """
    The whole point: the orders, invoices, stock and ledger go, and the catalogue, customers, warehouses and
    chart of accounts the company spent weeks building stay exactly where they were. Getting this split wrong
    in either direction is the failure — a half-cleared company, or a wiped catalogue.
    """
    trade(tenant)
    before_gone, before_kept = counts(tenant, GONE), counts(tenant, KEPT)
    assert all(n > 0 for n in before_gone.values()), f"the fixture must create every kind of row it checks: {before_gone}"

    r = admin_api.post(f"{P}/tenants/{tenant.id}/reset-data", {"confirm": tenant.slug}, format="json")
    assert r.status_code == 200, r.content

    assert counts(tenant, GONE) == dict.fromkeys(GONE, 0)
    assert counts(tenant, KEPT) == before_kept
    assert r.data["total"] >= sum(before_gone.values()), "the report must account for at least the rows checked here"
    assert r.data["deleted"]["sales.SalesOrder"] == before_gone["sales.SalesOrder"]


def test_the_company_its_users_and_its_roles_survive_the_reset(admin_api, tenant):
    """A reset is not a delete: the owner must still be able to sign in afterwards and find their team there."""
    from apps.accounts.models import TenantMembership
    from apps.platform.models import Tenant

    trade(tenant)
    users = TenantMembership.objects.filter(tenant=tenant).count()
    roles = tenant.roles.count()

    assert admin_api.post(f"{P}/tenants/{tenant.id}/reset-data", {"confirm": tenant.slug}, format="json").status_code == 200

    assert Tenant.objects.filter(pk=tenant.pk).exists()
    assert TenantMembership.objects.filter(tenant=tenant).count() == users > 0
    assert tenant.roles.count() == roles > 0
    assert Tenant.objects.get(pk=tenant.pk).owner_id == tenant.owner_id


def test_the_first_order_after_a_reset_is_numbered_one_again(admin_api, tenant):
    """
    A fresh start that carries on at SO-00042 is not a fresh start — the owner's first invoice after the reset
    would quote a number their customers never saw. The sequence keeps its prefix and padding; only the counter
    rewinds.
    """
    from apps.core.models import NumberSequence
    from apps.core.numbering import next_number

    ready = trade(tenant)
    with tenant_context(tenant, tenant.owner):
        seq = NumberSequence.all_objects.get(tenant=tenant, document_type="sales_order")
        seq.prefix, seq.padding = "ORD/", 4
        seq.save(update_fields=["prefix", "padding"])
    assert seq.next_number > 1

    assert admin_api.post(f"{P}/tenants/{tenant.id}/reset-data", {"confirm": tenant.slug}, format="json").status_code == 200

    with tenant_context(tenant, tenant.owner):
        from apps.sales.services import OrderService

        order = OrderService.create(customer=ready["customer"], lines=[{"variant": ready["variant"], "qty": Decimal("1"), "unit_price": Decimal("10")}], source="staff", warehouse=ready["warehouse"])
        assert order.number == f"{tenant.doc_code}-ORD/0001"  # the company code rides in front of every number
        assert next_number("sales_invoice").endswith("00001")


def test_a_wrong_confirm_slug_deletes_nothing(admin_api, tenant):
    """
    The typed slug is the one thing standing between a mis-aimed click and a company's books. A near miss — the
    display name, another company's slug, nothing at all — has to leave every row where it was.
    """
    trade(tenant)
    before = counts(tenant, GONE)

    for bad in ({}, {"confirm": ""}, {"confirm": tenant.display_name}, {"confirm": f"{tenant.slug} "}, {"confirm": "beta"}):
        r = admin_api.post(f"{P}/tenants/{tenant.id}/reset-data", bad, format="json")
        assert r.status_code == 400, (bad, r.content)
        assert r.data["type"] == "confirm_required"
    assert counts(tenant, GONE) == before


def test_a_tenant_user_cannot_reset_their_own_company(api, tenant):
    """
    This belongs to whoever runs the service, not to the customer. A tenant owner who found the URL — or a
    disgruntled staff member with a token — must be refused, and their data left alone.
    """
    trade(tenant)
    before = counts(tenant, GONE)

    r = api.post(f"{P}/tenants/{tenant.id}/reset-data", {"confirm": tenant.slug}, format="json")
    assert r.status_code == 403, r.content
    assert api.get(f"{P}/tenants/{tenant.id}/reset-data-preview").status_code == 403
    assert counts(tenant, GONE) == before


def test_an_anonymous_caller_cannot_reset_a_company(tenant):
    """The endpoint must not be reachable without a token at all."""
    trade(tenant)
    r = APIClient().post(f"{P}/tenants/{tenant.id}/reset-data", {"confirm": tenant.slug}, format="json")
    assert r.status_code in (401, 403), r.content
    assert counts(tenant, GONE)["sales.SalesOrder"] == 1


def test_resetting_one_company_does_not_touch_the_company_next_door(admin_api, tenant, tenant_b):
    """
    The one that matters most. Every delete here is a bulk statement across shared tables, so a missing
    `tenant=` on any one of them would wipe the whole server while the screen still said "done". Tenant B's
    rows are counted before and after, down to the number.
    """
    trade(tenant)
    trade(tenant_b)
    b_before = counts(tenant_b, GONE + KEPT)
    with tenant_context(tenant_b, tenant_b.owner):
        from apps.core.models import NumberSequence

        b_numbers = dict(NumberSequence.all_objects.filter(tenant=tenant_b).values_list("document_type", "next_number"))

    r = admin_api.post(f"{P}/tenants/{tenant.id}/reset-data", {"confirm": tenant.slug}, format="json")
    assert r.status_code == 200, r.content

    assert counts(tenant, GONE) == dict.fromkeys(GONE, 0)
    assert counts(tenant_b, GONE + KEPT) == b_before
    with tenant_context(tenant_b, tenant_b.owner):
        from apps.core.models import NumberSequence

        assert dict(NumberSequence.all_objects.filter(tenant=tenant_b).values_list("document_type", "next_number")) == b_numbers
    assert any(n > 1 for n in b_numbers.values()), "tenant B must have used some numbers, or this proves nothing"


def test_a_paying_customer_needs_a_second_explicit_acknowledgement(admin_api, tenant):
    """
    Losing a live customer's data by a misclick is the failure mode worth an extra step. A trial company resets
    on the typed slug alone; one we are billing right now also has to be acknowledged in so many words.
    """
    trade(tenant)
    sub = tenant.active_subscription
    sub.status = "active"
    sub.save(update_fields=["status"])
    assert Decimal(sub.plan_snapshot["price"]) > 0, "the fixture plan must cost money for this test to mean anything"

    r = admin_api.post(f"{P}/tenants/{tenant.id}/reset-data", {"confirm": tenant.slug}, format="json")
    assert r.status_code == 400, r.content
    assert r.data["type"] == "paying_tenant_confirmation_required"
    assert counts(tenant, GONE)["sales.SalesOrder"] == 1

    r = admin_api.post(f"{P}/tenants/{tenant.id}/reset-data", {"confirm": tenant.slug, "i_understand": True}, format="json")
    assert r.status_code == 200, r.content
    assert counts(tenant, GONE)["sales.SalesOrder"] == 0


def test_the_preview_counts_what_would_go_without_deleting_it(admin_api, tenant):
    """
    The admin sees the damage before they type the slug — and the number they read has to be true, so the
    preview runs the same list of tables the reset does and must leave every row in place.
    """
    trade(tenant)
    before = counts(tenant, GONE)

    r = admin_api.get(f"{P}/tenants/{tenant.id}/reset-data-preview")
    assert r.status_code == 200, r.content
    assert r.data["dry_run"] is True and r.data["is_paying"] is False
    assert r.data["deleted"]["sales.SalesOrder"] == before["sales.SalesOrder"]
    assert r.data["total"] > 0 and r.data["numbering_reset"] > 0
    assert counts(tenant, GONE) == before

    done = admin_api.post(f"{P}/tenants/{tenant.id}/reset-data", {"confirm": tenant.slug}, format="json")
    assert done.data["total"] == r.data["total"], "the preview promised a number; the reset must deliver it"


def test_the_reset_is_recorded_in_the_audit_log_with_what_it_deleted(admin_api, tenant):
    """
    Irreversible and done by us, so the only account of it is the audit row — which is also why the audit log
    is one of the things the reset does not delete. It has to say who, which company, and how much went.
    """
    from apps.core.models import AuditLog

    trade(tenant)
    r = admin_api.post(f"{P}/tenants/{tenant.id}/reset-data", {"confirm": tenant.slug}, format="json")
    assert r.status_code == 200, r.content

    log = AuditLog.objects.filter(tenant=tenant, action="platform.tenant.data_reset").first()
    assert log is not None, "a reset with no audit trail is unaccountable"
    assert log.actor is not None and log.actor.user_type == "PLATFORM_ADMIN"
    assert log.after["total"] == r.data["total"] > 0
    assert log.after["deleted"]["sales.SalesOrder"] == 1
    assert AuditLog.objects.filter(tenant=tenant).count() > 1, "the history of the company's own actions stays"


def test_stock_and_balances_read_as_zero_after_a_reset(admin_api, tenant):
    """
    A clean start means the reports agree: no stock on hand, nothing owed, no cached average cost left over
    from purchases that no longer exist. A number still showing in a report is how an owner discovers the
    reset only half happened.
    """
    from apps.accounting.services import ReportQueries, account_for
    from apps.catalog.models import ProductVariant
    from apps.inventory.services import StockService
    from apps.partners.services import CustomerService

    ready = trade(tenant)
    with tenant_context(tenant, tenant.owner):
        assert StockService.on_hand(ready["variant"], ready["warehouse"])[0] > 0
        assert CustomerService.outstanding(ready["customer"]) != 0

    assert admin_api.post(f"{P}/tenants/{tenant.id}/reset-data", {"confirm": tenant.slug}, format="json").status_code == 200

    with tenant_context(tenant, tenant.owner):
        assert StockService.on_hand(ready["variant"], ready["warehouse"]) == (Decimal("0"), Decimal("0"))
        assert CustomerService.outstanding(ready["customer"]) == 0
        assert ReportQueries.account_balance(account_for("ar")) == 0
        assert ProductVariant.all_objects.get(pk=ready["variant"].pk).avg_cost == 0


def test_a_tenant_reset_twice_in_a_row_is_a_no_op_the_second_time(admin_api, tenant):
    """An admin who clicks twice, or reruns the command, should get an empty report rather than an error."""
    trade(tenant)
    first = admin_api.post(f"{P}/tenants/{tenant.id}/reset-data", {"confirm": tenant.slug}, format="json")
    second = admin_api.post(f"{P}/tenants/{tenant.id}/reset-data", {"confirm": tenant.slug}, format="json")
    assert first.data["total"] > 0
    assert second.status_code == 200 and second.data["total"] == 0


def test_the_management_command_refuses_without_the_typed_slug(tenant):
    """The command line is the same door: --dry-run reports, and nothing is deleted until the slug is typed back."""
    from django.core.management import call_command
    from django.core.management.base import CommandError

    trade(tenant)
    before = counts(tenant, GONE)
    with pytest.raises(CommandError, match="--confirm"):
        call_command("reset_tenant_data", tenant.slug)
    call_command("reset_tenant_data", tenant.slug, "--dry-run")
    assert counts(tenant, GONE) == before

    call_command("reset_tenant_data", tenant.slug, "--confirm", tenant.slug)
    assert counts(tenant, GONE) == dict.fromkeys(GONE, 0)


def test_a_slug_that_is_not_this_company_cannot_reset_it(admin_api, tenant, tenant_b):
    """
    Two companies open in two tabs is the obvious way to reset the wrong one, so the slug is checked against
    the company in the URL, not against any company that exists.
    """
    trade(tenant)
    trade(tenant_b)
    r = admin_api.post(f"{P}/tenants/{tenant.id}/reset-data", {"confirm": tenant_b.slug}, format="json")
    assert r.status_code == 400 and r.data["type"] == "confirm_required"
    assert counts(tenant, GONE)["sales.SalesOrder"] == 1 and counts(tenant_b, GONE)["sales.SalesOrder"] == 1


def test_a_reset_leaves_a_company_able_to_trade_again(admin_api, tenant):
    """
    The real acceptance test the owner cares about: after the reset, can they take an order, send it out and
    invoice it exactly as before? Closed accounting periods and a zeroed stock ledger are the two things that
    quietly break this.
    """
    from apps.sales.models import SalesInvoice
    from apps.sales.services import OrderService

    ready = trade(tenant)
    assert admin_api.post(f"{P}/tenants/{tenant.id}/reset-data", {"confirm": tenant.slug}, format="json").status_code == 200

    with tenant_context(tenant, tenant.owner):
        from apps.inventory.services import StockService

        StockService.move(warehouse=ready["warehouse"], variant=ready["variant"], qty=Decimal("50"), movement_type="opening", unit_cost=Decimal("100"))
        order = OrderService.create(customer=ready["customer"], lines=[{"variant": ready["variant"], "qty": Decimal("5"), "unit_price": None}], source="staff", warehouse=ready["warehouse"])
        OrderService.confirm(order)
        OrderService.mark_delivered(order, receiver_name="Shop owner")
        order.refresh_from_db()
        invoice = SalesInvoice.objects.get(order=order)
        assert order.status == "invoiced" and invoice.journal is not None and invoice.journal.status == "posted"
        assert invoice.number.endswith("00001"), "invoice numbering restarted, so the first invoice is number 1"
