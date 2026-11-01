"""
Managing companies on the platform (owner, 1 Nov 2026): the plan's limits and features hold; a company that has not
paid is told 3 days before its expiry and through its grace days, and is suspended after them — no user signs in but
the owner, who may only pay; the owner pays by uploading the screenshot, a platform admin approves it and the
subscription renews; an admin can renew or extend with or without payment; every payment is on record and reported.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db
D = Decimal


@pytest.fixture
def admin_api(admin_user):
    c = APIClient()
    r = c.post("/api/v1/auth/login", {"identifier": "admin-test", "password": "pak1stan"}, format="json")
    assert r.status_code == 200, r.content
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
    return c


def _login(identifier, password, tenant=None):
    c = APIClient()
    r = c.post("/api/v1/auth/login", {"identifier": identifier, "password": password}, format="json")
    if r.status_code == 200:
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}", **({"HTTP_X_TENANT_ID": str(tenant.id)} if tenant else {}))
    return c, r


def _clerk(tenant, email="clerk@alpha.pk"):
    from apps.accounts.services import UserService

    with tenant_context(tenant, tenant.owner):
        return UserService.create_tenant_user(tenant, "Clerk", email=email, password="clerk123")


def _ends_in(tenant, days, grace=7):
    sub = tenant.active_subscription
    sub.status, sub.end_date, sub.grace_days = "active", timezone.localdate() + timedelta(days=days), grace
    sub.save()
    tenant.status = "active"
    tenant.save(update_fields=["status"])
    return sub


def _shot():
    return SimpleUploadedFile("receipt.png", b"\x89PNG\r\n\x1a\n" + b"0" * 200, content_type="image/png")


def test_the_plans_limits_hold_users_included(api, tenant):
    from apps.accounts.models import TenantMembership

    tenant.limit_overrides = {"max_users": 2}
    tenant.save(update_fields=["limit_overrides"])
    _clerk(tenant)                                                  # owner + clerk = 2 of 2
    with pytest.raises(Exception) as e:
        _clerk(tenant, "third@alpha.pk")
    assert "Plan limit reached for users (2/2)" in str(e.value)
    m = TenantMembership.objects.get(tenant=tenant, user__email="clerk@alpha.pk")
    r = api.patch(f"/api/v1/users/{m.id}", {"status": "suspended"}, format="json")
    assert r.status_code == 200, r.content
    _clerk(tenant, "third@alpha.pk")                                # the seat is free again
    r = api.patch(f"/api/v1/users/{m.id}", {"status": "active"}, format="json")
    assert r.status_code in (403, 422) and "Plan limit" in str(r.content), "switching a user back on takes a seat"
    usage = api.get("/api/v1/subscription").data["usage"]
    assert usage["users"] == 2


def test_features_off_on_the_plan_are_off_in_the_app(api, tenant):
    from .conftest import seed_basic

    seed_basic(tenant)                                              # one supplier already
    tenant.feature_overrides = {"multi_supplier": False, "barcode": False, "advanced_accounting": False}
    tenant.save(update_fields=["feature_overrides"])
    r = api.post("/api/v1/suppliers", {"name": "Second principal"}, format="json")
    assert r.status_code == 403 and "multi_supplier" in str(r.content)
    assert api.post("/api/v1/labels/print", {"items": []}, format="json").status_code == 403
    assert api.get("/api/v1/inventory/stock/lookup?code=X").status_code == 403
    assert api.post("/api/v1/accounting/cost-centres", {"code": "CC1", "name": "Lahore"}, format="json").status_code == 403
    assert api.get("/api/v1/reports/profit_and_loss").status_code == 403
    assert "profit_and_loss" not in [r["key"] for r in api.get("/api/v1/reports").data]


def test_the_owner_is_told_3_days_before_and_through_the_grace_days(api, tenant):
    _ends_in(tenant, 10)
    assert api.get("/api/v1/me").data["billing"] is None
    _ends_in(tenant, 2)
    b = api.get("/api/v1/me").data["billing"]
    assert b["level"] == "due" and "2 days left" in b["message"]
    sub = api.get("/api/v1/subscription").data
    assert sub["notice"]["invoice"] and any(i["purpose"] == "renewal" and i["status"] == "issued" for i in sub["invoices"]), "a renewal invoice to pay against"
    _ends_in(tenant, -3)
    b = api.get("/api/v1/me").data["billing"]
    assert b["level"] == "overdue" and "or access for every user stops" in b["message"]
    assert len([i for i in api.get("/api/v1/subscription").data["invoices"] if i["purpose"] == "renewal"]) == 1, "issued once"


def test_after_the_grace_days_nobody_but_the_owner_signs_in_and_the_owner_may_only_pay(api, tenant):
    _clerk(tenant)
    _ends_in(tenant, -8, grace=7)                                    # 8 days late, 7 of grace
    clerk, r = _login("clerk@alpha.pk", "clerk123")
    assert r.status_code == 403 and r.data["type"] == "tenant_suspended" and "alpha owner" in r.data["detail"]
    owner, r = _login("owner@alpha.pk", "owner123", tenant)
    assert r.status_code == 200
    assert owner.get("/api/v1/me").data["billing"]["level"] == "suspended"
    tenant.refresh_from_db()
    assert tenant.status == "suspended", "brought up to date without waiting for the scheduled check"
    blocked = owner.get("/api/v1/products")
    assert blocked.status_code == 403 and blocked.data["type"] == "tenant_suspended"
    assert owner.get("/api/v1/subscription").status_code == 200


def test_the_owner_pays_with_a_screenshot_and_the_approval_renews_and_reactivates(api, tenant, admin_api):
    from apps.core.models import Notification

    _clerk(tenant)
    sub = _ends_in(tenant, -8, grace=7)
    old_end = sub.end_date
    owner, _ = _login("owner@alpha.pk", "owner123", tenant)
    owner.get("/api/v1/me")                                          # suspended now
    no_shot = owner.post("/api/v1/billing/payments", {"amount": "12000", "method": "bank_transfer"}, format="multipart")
    assert no_shot.status_code == 400 and "screenshot" in no_shot.data["detail"]
    r = owner.post("/api/v1/billing/payments", {"amount": "12000", "method": "bank_transfer", "reference": "HBL-778", "receipt": _shot()}, format="multipart")
    assert r.status_code == 201, r.content
    assert r.data["status"] == "pending" and r.data["invoice_number"] and r.data["receipt_file"]
    assert "awaiting approval" in owner.get("/api/v1/me").data["billing"]["message"]
    clerk, r2 = _login("clerk@alpha.pk", "clerk123")
    assert r2.status_code == 403, "nothing reopens before the platform approves"

    pending = admin_api.get("/api/v1/platform/payments?status=pending").data["results"]
    assert [p["reference"] for p in pending] == ["HBL-778"]
    assert admin_api.get("/api/v1/platform/dashboard").data["pending_payments"] == 1
    ok = admin_api.post(f"/api/v1/platform/payments/{pending[0]['id']}/approve", {}, format="json")
    assert ok.status_code == 200, ok.content
    assert ok.data["status"] == "paid" and ok.data["reviewed_by_name"] == "Admin"
    tenant.refresh_from_db()
    sub.refresh_from_db()
    assert tenant.status == "active" and sub.status == "active" and sub.end_date > timezone.localdate() > old_end
    inv = admin_api.get(f"/api/v1/platform/invoices?tenant={tenant.id}").data["results"]
    assert [(i["purpose"], i["status"]) for i in inv] == [("renewal", "paid")]
    with tenant_context(tenant):
        assert Notification.objects.filter(user=tenant.owner, type="billing.payment_receipt").exists()
    clerk, r3 = _login("clerk@alpha.pk", "clerk123")
    assert r3.status_code == 200, "everyone is back"


def test_a_payment_turned_down_says_why_and_renews_nothing(api, tenant, admin_api):
    from apps.core.models import Notification

    sub = _ends_in(tenant, 1)
    pay = api.post("/api/v1/billing/payments", {"amount": "500", "method": "easypaisa", "reference": "EP1", "receipt": _shot()}, format="multipart").data
    assert admin_api.post(f"/api/v1/platform/payments/{pay['id']}/reject", {}, format="json").status_code in (400, 422), "a reason is required"
    r = admin_api.post(f"/api/v1/platform/payments/{pay['id']}/reject", {"reason": "No such deposit in our account"}, format="json")
    assert r.status_code == 200 and r.data["status"] == "rejected"
    sub.refresh_from_db()
    assert sub.end_date == timezone.localdate() + timedelta(days=1)
    with tenant_context(tenant):
        n = Notification.objects.get(user=tenant.owner, type="billing.payment_rejected")
    assert "No such deposit" in n.body
    assert admin_api.post(f"/api/v1/platform/payments/{pay['id']}/approve", {}, format="json").status_code in (400, 422), "decided once"


def test_an_admin_renews_with_without_or_free_of_payment_and_extends_the_expiry(api, tenant, admin_api):
    sub = _ends_in(tenant, 2)
    api.get("/api/v1/subscription")                                    # the renewal invoice is issued
    base = f"/api/v1/platform/subscriptions/{sub.id}"
    free = admin_api.post(f"{base}/renew", {"periods": 1, "charge": "free", "reason": "goodwill"}, format="json")
    assert free.status_code == 200 and free.data["invoice"] is None
    invs = admin_api.get(f"/api/v1/platform/invoices?tenant={tenant.id}").data["results"]
    assert [i["status"] for i in invs] == ["void"], "the renewal they were asked to pay is not owed any more"
    later = admin_api.post(f"{base}/renew", {"periods": 1, "charge": "invoice"}, format="json").data
    assert later["invoice"]["status"] == "issued"
    paid = admin_api.post(f"{base}/renew", {"periods": 2, "charge": "paid", "payment": {"method": "cash", "reference": "R-1"}}, format="json").data
    assert paid["invoice"]["status"] == "paid" and paid["invoice"]["periods"] == 2

    _ends_in(tenant, -9)
    SubscriptionService = __import__("apps.platform.services", fromlist=["SubscriptionService"]).SubscriptionService
    SubscriptionService.run_status_checks()
    tenant.refresh_from_db()
    assert tenant.status == "suspended"
    ext = admin_api.post(f"{base}/extend", {"days": 10, "reason": "promised to pay Friday"}, format="json")
    assert ext.status_code == 200 and ext.data["invoice"] is None
    tenant.refresh_from_db()
    assert tenant.status == "active" and ext.data["subscription"]["end_date"] == str(timezone.localdate() + timedelta(days=10))
    charged = admin_api.post(f"{base}/extend", {"until": str(timezone.localdate() + timedelta(days=40)), "amount": "3000",
                                               "payment": {"method": "bank_transfer", "reference": "X"}}, format="json").data
    assert charged["invoice"]["purpose"] == "extension" and charged["invoice"]["status"] == "paid" and D(charged["invoice"]["total"]) == D("3000")

    rep = admin_api.get("/api/v1/platform/billing-report").data
    assert D(rep["summary"]["received"]) >= D("3000") and "Cash" in rep["by_method"] and rep["payments"]
    assert any(r["tenant"] == "Alpha" for r in rep["renewals_due"] + rep["lapsed"]) or rep["summary"]["outstanding"]
