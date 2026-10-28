from django.conf import settings as django_settings
from django.core.files.storage import default_storage
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import GroupProfile, User
from apps.accounts.permissions_catalog import PLATFORM_CATALOG, catalog_as_json
from apps.accounts.services import RoleService, SessionService, UserService, ensure_permission_catalog
from apps.core.audit import AuditService
from apps.core.exceptions import DomainError
from apps.core.uuid7 import uuid7
from apps.core.models import AuditLog
from apps.core.permissions import IsPlatformAdmin
from apps.core.plan_limits import effective_features
from apps.core.serializers import AuditLogSerializer

from .models import AddOn, Announcement, Coupon, FeatureFlag, LegalDocument, MessageTemplate, NotificationLog, Plan, PlatformInvoice, PlatformPayment, PlatformSetting, Subscription, SupportTicket, Tenant, TicketMessage
from .serializers import (
    AddOnSerializer, AnnouncementSerializer, CouponSerializer, FeatureFlagSerializer, LegalDocumentSerializer, MessageTemplateSerializer,
    NotificationLogSerializer, PlanSerializer, PlatformInvoiceSerializer, PlatformPaymentSerializer, PlatformRefundSerializer, PlatformUserSerializer,
    SubscriptionEventSerializer, SubscriptionSerializer, SupportTicketSerializer, TenantCreateSerializer, TenantSerializer, TicketMessageSerializer,
)
from .services import BillingService, DashboardService, PlanService, SubscriptionService, TenantService, reset_tenant_data, tenant_is_paying


def require_platform_perm(codename):
    from functools import wraps

    from rest_framework.exceptions import PermissionDenied

    def deco(fn):
        @wraps(fn)
        def wrapper(self, request, *a, **k):
            if f"platform.{codename}" not in request.user.platform_perms():
                raise PermissionDenied(f"Missing platform permission: {codename}")
            return fn(self, request, *a, **k)

        return wrapper

    return deco


class PlatformViewSet(viewsets.ModelViewSet):
    permission_classes = [IsPlatformAdmin]
    platform_perm = None

    def check_permissions(self, request):
        super().check_permissions(request)
        if self.platform_perm and request.method not in ("GET", "HEAD", "OPTIONS"):
            if f"platform.{self.platform_perm}" not in request.user.platform_perms():
                self.permission_denied(request, message=f"Missing platform permission: {self.platform_perm}")


class PlanViewSet(PlatformViewSet):
    serializer_class = PlanSerializer
    platform_perm = "manage_plans"
    filterset_fields = ["is_public", "is_archived", "is_current", "code"]
    search_fields = ["name", "code"]
    ordering_fields = ["sort_order", "price", "name"]

    def get_queryset(self):
        qs = Plan.objects.all()
        if self.request.query_params.get("all_versions") != "1":
            qs = qs.filter(is_current=True)
        return qs

    def perform_create(self, serializer):
        serializer.instance = PlanService.create_plan(**serializer.validated_data)

    def perform_update(self, serializer):
        serializer.instance = PlanService.new_version(serializer.instance, **serializer.validated_data)

    def perform_destroy(self, instance):
        instance.is_archived = True
        instance.save(update_fields=["is_archived"])

    @action(detail=True, methods=["get"])
    def versions(self, request, pk=None):
        plan = self.get_object()
        return Response(PlanSerializer(Plan.objects.filter(code=plan.code).order_by("-version"), many=True).data)


class AddOnViewSet(PlatformViewSet):
    serializer_class = AddOnSerializer
    platform_perm = "manage_plans"
    queryset = AddOn.objects.all()


class CouponViewSet(PlatformViewSet):
    serializer_class = CouponSerializer
    platform_perm = "manage_plans"
    queryset = Coupon.objects.all()


class TenantViewSet(PlatformViewSet):
    serializer_class = TenantSerializer
    platform_perm = "manage_tenants"
    filterset_fields = ["status", "base_currency"]
    search_fields = ["display_name", "legal_name", "slug", "email", "phone"]
    ordering_fields = ["display_name", "created_at", "status"]

    def get_queryset(self):
        """`?expiring=1` (renews within 7 days or already overdue), `?white_label=1`, `?plan=<id>` narrow the list."""
        from datetime import timedelta

        qs = Tenant.objects.select_related("owner").all()
        p = self.request.query_params
        if p.get("plan"):
            qs = qs.filter(subscriptions__plan_id=p["plan"], subscriptions__status__in=["trial", "active", "past_due"]).distinct()
        if p.get("expiring") in ("1", "true"):
            qs = qs.filter(subscriptions__status__in=["trial", "active", "past_due"], subscriptions__end_date__lte=timezone.localdate() + timedelta(days=7)).distinct()
        if p.get("white_label") in ("1", "true"):
            ids = [t.id for t in qs if effective_features(t).get("branding")]
            qs = qs.filter(id__in=ids)
        return qs

    def create(self, request, *args, **kwargs):
        s = TenantCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = dict(s.validated_data)
        tenant = TenantService.create_tenant(
            legal_name=d.pop("legal_name"), display_name=d.pop("display_name"), owner_name=d.pop("owner_name"), owner_email=d.pop("owner_email", None) or None,
            owner_phone=d.pop("owner_phone", None) or None, owner_password=d.pop("owner_password", None) or None, plan=d.pop("plan", None), slug=d.pop("slug", None) or None,
            created_by=request.user, send_invite=d.pop("send_invite", True), **d,
        )
        return Response(TenantSerializer(tenant).data, status=201)

    def perform_update(self, serializer):
        before = {k: getattr(serializer.instance, k) for k in ("display_name", "legal_name", "limit_overrides", "feature_overrides", "impersonation_allowed")}
        serializer.save()
        AuditService.log("platform.tenant.updated", serializer.instance, before=before, after={k: getattr(serializer.instance, k) for k in before}, actor=self.request.user)

    def perform_destroy(self, instance):
        TenantService.cancel(instance, actor=self.request.user, reason=self.request.data.get("reason", "") if hasattr(self.request, "data") else "")

    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        TenantService.suspend(self.get_object(), request.data.get("reason", ""), actor=request.user)
        return Response(TenantSerializer(self.get_object()).data)

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        TenantService.reactivate(self.get_object(), actor=request.user, reason=request.data.get("reason", ""))
        return Response(TenantSerializer(self.get_object()).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        TenantService.cancel(self.get_object(), actor=request.user, reason=request.data.get("reason", ""))
        return Response(TenantSerializer(self.get_object()).data)

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        if request.data.get("confirm") != self.get_object().slug:
            raise DomainError("Type the tenant slug in 'confirm' to archive.")
        TenantService.archive(self.get_object(), actor=request.user)
        return Response(TenantSerializer(self.get_object()).data)

    @action(detail=True, methods=["get"], url_path="reset-data-preview")
    def reset_data_preview(self, request, pk=None):
        """What a data reset would delete, counted but untouched — shown in the dialog before the admin commits."""
        tenant = self.get_object()
        preview = reset_tenant_data(tenant, dry_run=True)
        preview["is_paying"] = tenant_is_paying(tenant)
        return Response(preview)

    @action(detail=True, methods=["post"], url_path="reset-data")
    def reset_data(self, request, pk=None):
        """
        Put a company back to a clean start: delete its trading history, keep its setup (users, warehouses,
        catalogue, customers, suppliers, settings) and restart document numbering at 1. Irreversible, so the
        slug has to be typed, and a company we are currently billing needs `i_understand` on top of that.
        """
        tenant = self.get_object()
        if request.data.get("confirm") != tenant.slug:
            raise DomainError("Type the tenant slug in 'confirm' to reset this company's data.", code="confirm_required", status_code=400)
        if tenant_is_paying(tenant) and request.data.get("i_understand") is not True:
            raise DomainError(
                f"{tenant.display_name} is a paying customer on an active subscription. Send 'i_understand': true to confirm you mean to delete their live data.",
                code="paying_tenant_confirmation_required",
                status_code=400,
            )
        result = reset_tenant_data(tenant, actor=request.user, request=request)
        return Response({**result, "tenant": TenantSerializer(tenant).data})

    @action(detail=True, methods=["get"])
    def usage(self, request, pk=None):
        return Response(TenantService.usage(self.get_object()))

    @action(detail=True, methods=["post"], url_path="set-feature")
    def set_feature(self, request, pk=None):
        """
        Turn one feature on/off for this tenant regardless of its plan — e.g. white-label branding.
        Body: { "key": "branding", "value": true | false | null }  (null = follow the plan again)
        """
        tenant = self.get_object()
        key = str(request.data.get("key") or "").strip()
        if not key:
            raise DomainError("Which feature? Send `key`.", status_code=400)
        value = request.data.get("value")
        overrides = dict(tenant.feature_overrides or {})
        if value is None:
            overrides.pop(key, None)
        else:
            overrides[key] = value if isinstance(value, str) else bool(value)
        tenant.feature_overrides = overrides
        tenant.save(update_fields=["feature_overrides"])
        AuditService.log("tenant.feature.updated", tenant, after={"key": key, "value": value}, actor=request.user, request=request)
        return Response(TenantSerializer(tenant).data)

    @action(detail=True, methods=["post"])
    @require_platform_perm("impersonate")
    def impersonate(self, request, pk=None):
        session = TenantService.start_impersonation(request.user, self.get_object(), request.data.get("reason", ""), request.data.get("minutes"))
        return Response({"tenant_id": str(session.tenant_id), "target_user": str(session.target), "expires_at": session.expires_at})

    @action(detail=False, methods=["post"], url_path="end-impersonation")
    def end_impersonation(self, request):
        tenant = Tenant.objects.filter(pk=request.data.get("tenant")).first() if request.data.get("tenant") else None
        TenantService.end_impersonation(request.user, tenant=tenant)
        return Response(status=204)

    @action(detail=True, methods=["post"], url_path="reset-owner-password")
    def reset_owner_password(self, request, pk=None):
        tenant = self.get_object()
        pwd = request.data.get("password")
        if not tenant.owner or not pwd:
            raise DomainError("Owner and password are required.")
        UserService.reset_password(tenant.owner, pwd, by_admin=True)
        return Response({"detail": "Owner password reset."})

    @action(detail=True, methods=["post"], url_path="unlock-owner")
    def unlock_owner(self, request, pk=None):
        tenant = self.get_object()
        if tenant.owner:
            tenant.owner.locked_until = None
            tenant.owner.failed_login_attempts = 0
            tenant.owner.is_active = True
            tenant.owner.save()
        return Response({"detail": "Owner unlocked."})

    @action(detail=True, methods=["post"], url_path="revoke-sessions")
    def revoke_sessions(self, request, pk=None):
        tenant = self.get_object()
        for m in tenant.memberships.select_related("user"):
            SessionService.revoke_all(m.user, reason="platform_admin")
        return Response(status=204)

    @action(detail=True, methods=["get"])
    def subscriptions(self, request, pk=None):
        return Response(SubscriptionSerializer(self.get_object().subscriptions.all(), many=True).data)

    @action(detail=True, methods=["get"])
    def timeline(self, request, pk=None):
        from .models import SubscriptionEvent

        ev = SubscriptionEvent.objects.filter(subscription__tenant=self.get_object()).select_related("actor")[:200]
        return Response(SubscriptionEventSerializer(ev, many=True).data)

    @action(detail=True, methods=["get"])
    def export(self, request, pk=None):
        from .export import export_tenant_zip

        tenant = self.get_object()
        data = export_tenant_zip(tenant)
        resp = HttpResponse(data, content_type="application/zip")
        resp["Content-Disposition"] = f'attachment; filename="{tenant.slug}-export.zip"'
        return resp

    @action(detail=True, methods=["get"])
    def audit(self, request, pk=None):
        qs = AuditLog.objects.filter(tenant=self.get_object())[:500]
        return Response(AuditLogSerializer(qs, many=True).data)


class SubscriptionViewSet(PlatformViewSet):
    serializer_class = SubscriptionSerializer
    platform_perm = "manage_subscriptions"
    filterset_fields = ["status", "tenant", "plan"]
    search_fields = ["tenant__display_name"]
    ordering_fields = ["end_date", "start_date"]
    queryset = Subscription.objects.select_related("tenant", "plan").all()
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]  # delete is only used by the `discount` action

    def create(self, request, *args, **kwargs):
        tenant = Tenant.objects.get(pk=request.data.get("tenant"))
        plan = Plan.objects.get(pk=request.data.get("plan"))
        sub = SubscriptionService.assign(tenant, plan, trial=bool(request.data.get("trial", False)), actor=request.user, notes=request.data.get("notes", ""))
        tenant.status = "trial" if sub.status == "trial" else "active"
        tenant.save(update_fields=["status"])
        return Response(SubscriptionSerializer(sub).data, status=201)

    @action(detail=True, methods=["post"])
    def renew(self, request, pk=None):
        sub = self.get_object()
        pay = request.data.get("payment")
        sub, invoice = SubscriptionService.renew(sub, periods=int(request.data.get("periods", 1)), from_today=bool(request.data.get("from_today", False)), actor=request.user, record_payment=pay, coupon_code=request.data.get("coupon_code"), reason=request.data.get("reason", ""))
        return Response({"subscription": SubscriptionSerializer(sub).data, "invoice": PlatformInvoiceSerializer(invoice).data})

    @action(detail=True, methods=["post", "delete"])
    def discount(self, request, pk=None):
        """
        POST { type: "percent"|"fixed", value, cycles?, until?, reason? } — a discount for this tenant only.
        DELETE removes it. The discount is applied to every invoice until its cycle count or end date runs out.
        """
        sub = self.get_object()
        if request.method == "DELETE":
            SubscriptionService.set_discount(sub, type=None, actor=request.user)
        else:
            SubscriptionService.set_discount(
                sub, type=request.data.get("type"), value=request.data.get("value") or 0,
                cycles=request.data.get("cycles") or 0, until=request.data.get("until") or None,
                reason=request.data.get("reason", ""), actor=request.user,
            )
        return Response(SubscriptionSerializer(sub).data)

    @action(detail=True, methods=["get"], url_path="price")
    def price(self, request, pk=None):
        """What the next invoice will look like (plan + add-ons − discount)."""
        periods = int(request.query_params.get("periods") or 1)
        return Response(SubscriptionService.price_breakdown(self.get_object(), periods=periods))

    @action(detail=True, methods=["post"], url_path="change-plan/preview")
    def change_plan_preview(self, request, pk=None):
        sub = self.get_object()
        plan = Plan.objects.get(pk=request.data.get("plan"))
        return Response({"prorata": SubscriptionService.prorata(sub, plan), "over_limits": SubscriptionService.over_limits(sub.tenant, plan)})

    @action(detail=True, methods=["post"], url_path="change-plan")
    def change_plan(self, request, pk=None):
        sub = self.get_object()
        plan = Plan.objects.get(pk=request.data.get("plan"))
        sub, invoice = SubscriptionService.change_plan(sub, plan, when=request.data.get("when", "immediate"), actor=request.user, net_amount=request.data.get("net_amount"), record_payment=request.data.get("payment"), reason=request.data.get("reason", ""))
        return Response({"subscription": SubscriptionSerializer(sub).data, "invoice": PlatformInvoiceSerializer(invoice).data if invoice else None})

    @action(detail=True, methods=["post"], url_path="extend-trial")
    def extend_trial(self, request, pk=None):
        sub = SubscriptionService.extend_trial(self.get_object(), int(request.data.get("days", 7)), actor=request.user, reason=request.data.get("reason", ""))
        return Response(SubscriptionSerializer(sub).data)

    @action(detail=True, methods=["post"], url_path="convert-trial")
    def convert_trial(self, request, pk=None):
        sub, invoice = SubscriptionService.renew(self.get_object(), from_today=True, actor=request.user, record_payment=request.data.get("payment"), reason="trial converted")
        return Response({"subscription": SubscriptionSerializer(sub).data, "invoice": PlatformInvoiceSerializer(invoice).data})

    @action(detail=True, methods=["post"])
    def addons(self, request, pk=None):
        items = [{"addon": AddOn.objects.get(pk=i["addon"]), "quantity": int(i["quantity"])} for i in request.data.get("items", [])]
        sub = SubscriptionService.set_addons(self.get_object(), items, actor=request.user)
        return Response(SubscriptionSerializer(sub).data)

    @action(detail=True, methods=["get"])
    def events(self, request, pk=None):
        return Response(SubscriptionEventSerializer(self.get_object().events.select_related("actor"), many=True).data)


class PlatformInvoiceViewSet(PlatformViewSet):
    serializer_class = PlatformInvoiceSerializer
    platform_perm = "manage_billing"
    filterset_fields = ["status", "tenant", "is_credit_note"]
    search_fields = ["number", "tenant__display_name"]
    ordering_fields = ["created_at", "due_date", "total"]
    queryset = PlatformInvoice.objects.select_related("tenant").all()
    http_method_names = ["get", "post", "patch", "head", "options"]

    def create(self, request, *args, **kwargs):
        tenant = Tenant.objects.get(pk=request.data["tenant"])
        sub = tenant.active_subscription
        from datetime import date

        inv = BillingService.create_invoice(tenant, sub, date.fromisoformat(request.data["period_start"]), date.fromisoformat(request.data["period_end"]), description=request.data.get("description", ""), override_total=request.data.get("total"), actor=request.user, is_credit_note=bool(request.data.get("is_credit_note", False)))
        return Response(PlatformInvoiceSerializer(inv).data, status=201)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        inv = self.get_object()
        pdf = BillingService.invoice_pdf(inv)
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="{inv.number}.pdf"'
        return resp

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        inv = self.get_object()
        if inv.paid_amount > 0:
            raise DomainError("Cannot void an invoice with payments.")
        inv.status = "void"
        inv.save(update_fields=["status"])
        return Response(PlatformInvoiceSerializer(inv).data)

    @action(detail=False, methods=["get"])
    def outstanding(self, request):
        today = timezone.localdate()
        rows = []
        for inv in PlatformInvoice.objects.filter(status__in=["issued", "partial", "overdue"]).select_related("tenant"):
            days = (today - (inv.due_date or inv.period_start)).days
            bucket = "current" if days <= 0 else "1-30" if days <= 30 else "31-60" if days <= 60 else "61-90" if days <= 90 else "90+"
            rows.append({"tenant": inv.tenant.display_name, "tenant_id": str(inv.tenant_id), "number": inv.number, "due_date": inv.due_date, "days_overdue": max(days, 0), "bucket": bucket, "balance": str(inv.balance)})
        return Response(rows)


class PlatformPaymentViewSet(PlatformViewSet):
    serializer_class = PlatformPaymentSerializer
    platform_perm = "manage_billing"
    filterset_fields = ["status", "tenant", "method", "invoice"]
    search_fields = ["reference", "tenant__display_name", "invoice__number"]
    ordering_fields = ["paid_at", "amount"]
    queryset = PlatformPayment.objects.select_related("tenant", "invoice", "recorded_by").all()
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get("date_from"):
            qs = qs.filter(paid_at__gte=p["date_from"])
        if p.get("date_to"):
            qs = qs.filter(paid_at__lte=p["date_to"])
        if p.get("plan"):
            qs = qs.filter(tenant__subscriptions__plan__code=p["plan"]).distinct()
        return qs

    def create(self, request, *args, **kwargs):
        tenant = Tenant.objects.get(pk=request.data["tenant"])
        invoice = PlatformInvoice.objects.filter(pk=request.data.get("invoice")).first() if request.data.get("invoice") else None
        pay = BillingService.record_payment(
            tenant, amount=request.data["amount"], method=request.data.get("method", "bank_transfer"), reference=request.data.get("reference", ""),
            paid_at=request.data.get("paid_at") or None, note=request.data.get("note", ""), invoice=invoice, receipt_file=request.FILES.get("receipt_file"), actor=request.user, status=request.data.get("status", "paid"),
        )
        return Response(PlatformPaymentSerializer(pay).data, status=201)

    @action(detail=True, methods=["post"])
    def refund(self, request, pk=None):
        r = BillingService.refund(self.get_object(), request.data["amount"], request.data.get("reason", ""), actor=request.user)
        return Response(PlatformRefundSerializer(r).data, status=201)

    @action(detail=False, methods=["get"], url_path="export")
    def export_csv(self, request):
        import csv
        import io

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["date", "tenant", "invoice", "amount", "currency", "method", "reference", "status", "recorded_by"])
        for p in self.filter_queryset(self.get_queryset()):
            w.writerow([p.paid_at, p.tenant.display_name, p.invoice.number if p.invoice else "", p.amount, p.currency, p.method, p.reference, p.status, p.recorded_by.full_name if p.recorded_by else ""])
        resp = HttpResponse(buf.getvalue(), content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="platform-payments.csv"'
        return resp


class AnnouncementViewSet(PlatformViewSet):
    serializer_class = AnnouncementSerializer
    platform_perm = "manage_settings"
    queryset = Announcement.objects.all()


class MessageTemplateViewSet(PlatformViewSet):
    serializer_class = MessageTemplateSerializer
    platform_perm = "manage_settings"
    queryset = MessageTemplate.objects.all()
    pagination_class = None

    @action(detail=True, methods=["post"])
    def preview(self, request, pk=None):
        subject, body = self.get_object().render(request.data.get("context", {}))
        return Response({"subject": subject, "body": body})


class NotificationLogViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsPlatformAdmin]
    serializer_class = NotificationLogSerializer
    queryset = NotificationLog.objects.all()
    filterset_fields = ["channel", "status", "tenant"]


class FeatureFlagViewSet(PlatformViewSet):
    serializer_class = FeatureFlagSerializer
    platform_perm = "manage_settings"
    queryset = FeatureFlag.objects.all()
    pagination_class = None


class LegalDocumentViewSet(PlatformViewSet):
    serializer_class = LegalDocumentSerializer
    platform_perm = "manage_settings"
    queryset = LegalDocument.objects.all()

    def perform_create(self, serializer):
        LegalDocument.objects.filter(kind=serializer.validated_data["kind"]).update(is_current=False)
        serializer.save(is_current=True)


class SupportTicketViewSet(viewsets.ModelViewSet):
    """Platform side of support tickets (tenant side lives in core /support-tickets)."""

    permission_classes = [IsPlatformAdmin]
    serializer_class = SupportTicketSerializer
    filterset_fields = ["status", "priority", "tenant", "assigned_to"]
    search_fields = ["subject", "tenant__display_name"]
    queryset = SupportTicket.objects.select_related("tenant", "opened_by").all()

    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        t = self.get_object()
        msg = TicketMessage.objects.create(ticket=t, author=request.user, body=request.data.get("body", ""), is_staff_reply=True)
        t.status = request.data.get("status", "pending")
        t.save(update_fields=["status", "updated_at"])
        return Response(TicketMessageSerializer(msg).data, status=201)


class PlatformAuditViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsPlatformAdmin]
    serializer_class = AuditLogSerializer
    filterset_fields = ["action", "model", "tenant", "actor"]
    search_fields = ["action", "model", "object_repr"]

    def get_queryset(self):
        return AuditLog.objects.select_related("actor", "tenant").all()


class PlatformUserViewSet(PlatformViewSet):
    serializer_class = PlatformUserSerializer
    platform_perm = "manage_platform_users"
    search_fields = ["full_name", "email"]

    def get_queryset(self):
        return User.objects.filter(user_type="PLATFORM_ADMIN")

    def perform_create(self, serializer):
        d = serializer.validated_data
        role_ids = d.pop("role_ids", [])
        pwd = d.pop("password", None)
        user = User(user_type="PLATFORM_ADMIN", **d)
        user.set_password(pwd or User.objects.make_random_password())
        user.must_change_password = True
        user.save()
        user.groups.set([p.group for p in GroupProfile.objects.filter(tenant=None, role_type="platform", id__in=role_ids)])
        serializer.instance = user

    def perform_update(self, serializer):
        d = serializer.validated_data
        role_ids = d.pop("role_ids", None)
        pwd = d.pop("password", None)
        user = serializer.save()
        if pwd:
            user.set_password(pwd)
            user.save()
        if role_ids is not None:
            user.groups.set([p.group for p in GroupProfile.objects.filter(tenant=None, role_type="platform", id__in=role_ids)])

    @action(detail=False, methods=["get"])
    def roles(self, request):
        ensure_permission_catalog()
        from .services import seed_platform_roles

        seed_platform_roles()
        return Response([{"id": str(p.id), "name": p.name, "permissions": p.permission_codenames} for p in GroupProfile.objects.filter(tenant=None, role_type="platform")])

    @action(detail=False, methods=["get"], url_path="permissions-catalog")
    def permissions_catalog(self, request):
        return Response(catalog_as_json("platform"))


class PlatformDashboardView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        return Response(DashboardService.metrics())


class PlatformSettingsView(APIView):
    """
    Project-wide (not tenant) settings: branding, contact/SEO, defaults for new tenants, providers, security and
    maintenance. Stored as one JSON row (`PlatformSetting["global"]`); the public subset is served by PublicBrandingView.
    """

    permission_classes = [IsPlatformAdmin]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    DEFAULTS = {
        # branding / website
        "site_name": "DMS", "site_tagline": "Distribution Management", "company_name": "", "primary_color": "#F26522",
        "logo_url": "", "logo_dark_url": "", "favicon_url": "", "login_headline": "Every unit in, every unit out.",
        "login_subtext": "Distribution, delivery and accounts in one place.", "footer_text": "", "public_base_url": django_settings.PUBLIC_BASE_URL,
        # contact / legal
        "support_email": "support@dms.invoiceflow.pk", "support_phone": "", "support_whatsapp": "", "sales_email": "", "company_address": "",
        "terms_url": "/legal/terms", "privacy_url": "/legal/privacy", "meta_description": "Multi-tenant distribution management for wholesalers and distributors.",
        # defaults for new tenants
        "default_plan_code": "growth", "trial_days": 14, "currencies": ["PKR", "USD", "AED", "SAR"], "default_fiscal_year_start_month": 7, "default_timezone": django_settings.TIME_ZONE,
        "signup_enabled": False, "tenant_approval_required": True,
        # maps — which tiles the map screens draw on
        # "osm" is the plain OpenStreetMap map, which shows Pakistani names in Urdu; "esri_en" is the same roads
        # with English labels; "maptiler" uses your own MapTiler style (set its language there); "custom" is a URL.
        "map_tiles": "osm", "map_tiles_url": "", "map_tiles_key": "", "map_tiles_style": "streets-v2", "map_attribution": "",
        # providers
        "sms_provider": "console", "email_provider": "smtp", "push_provider": "fcm", "email_from_name": "DMS", "email_from_address": "", "sms_sender_id": "",
        # Microsoft 365 (Graph) — an app registration sends as the From address above; needs the application
        # permission Mail.Send with admin consent. The secret is never sent back to the browser, only its tail.
        "graph_tenant_id": "", "graph_client_id": "", "graph_client_secret": "",
        # security
        "login_lockout_attempts": django_settings.DMS["LOGIN_LOCKOUT_ATTEMPTS"], "login_lockout_minutes": django_settings.DMS["LOGIN_LOCKOUT_MINUTES"],
        "otp_ttl_minutes": django_settings.DMS["OTP_TTL_MINUTES"], "qr_login_ttl_seconds": django_settings.DMS["QR_LOGIN_TTL_SECONDS"],
        "impersonation_minutes": django_settings.DMS["IMPERSONATION_MINUTES"], "password_min_length": 8, "force_2fa_platform_admins": False,
        "session_idle_days": 14, "allowed_admin_ips": [],
        # operations
        "maintenance_mode": False, "maintenance_message": "We are performing scheduled maintenance. Please try again shortly.",
        "backup_schedule": "0 2 * * *", "backup_retention_days": 14, "max_upload_mb": 25, "api_rate_limit_per_min": 60, "announcement_banner": "",
    }
    ASSETS = {"logo": "logo_url", "logo_dark": "logo_dark_url", "favicon": "favicon_url", "login_image": "login_image_url"}

    @staticmethod
    def current():
        return {**PlatformSettingsView.DEFAULTS, **(PlatformSetting.get("global", {}) or {})}

    SECRETS = ("graph_client_secret",)

    @staticmethod
    def masked(cfg):
        """What the browser is allowed to see: a secret is shown as its last four characters, never in full."""
        out = dict(cfg)
        for key in PlatformSettingsView.SECRETS:
            value = out.get(key) or ""
            out[key] = f"••••••••{value[-4:]}" if value else ""
            out[f"{key}_set"] = bool(value)
        return out

    def get(self, request):
        return Response(self.masked(self.current()))

    def patch(self, request):
        current = self.current()
        data = {k: v for k, v in request.data.items() if k in self.DEFAULTS or k in self.ASSETS.values()}
        # a secret that comes back as its own mask means "unchanged", not "set it to dots"
        for key in self.SECRETS:
            if key in data and (not data[key] or str(data[key]).startswith("••••")):
                data.pop(key)
        for key in ("currencies", "allowed_admin_ips"):  # accept a textarea/comma list from the UI
            if isinstance(data.get(key), str):
                data[key] = [x.strip() for x in data[key].replace(",", "\n").splitlines() if x.strip()]
        current.update(data)
        PlatformSetting.set("global", current)
        from django.core.cache import cache

        cache.delete("platform.settings")
        AuditService.log("platform.settings.updated", model="PlatformSetting",
                         after={k: ("set" if k in self.SECRETS else v) for k, v in data.items()}, actor=request.user)
        return Response(self.masked(current))

    def post(self, request):
        """Upload a branding asset: multipart `kind` (logo | logo_dark | favicon | login_image) + `file`."""
        kind = request.data.get("kind")
        f = request.FILES.get("file")
        if kind not in self.ASSETS or f is None:
            raise DomainError(f"Send a file and kind: {', '.join(self.ASSETS)}.", status_code=400)
        if f.size > 5 * 1024 * 1024:
            raise DomainError("Image too large — keep branding files under 5 MB.", status_code=400)
        ext = (f.name.rsplit(".", 1)[-1] or "png").lower()[:5]
        if ext not in ("png", "jpg", "jpeg", "svg", "webp", "ico", "gif"):
            raise DomainError("Use a PNG, JPG, SVG, WEBP or ICO file.", status_code=400)
        path = default_storage.save(f"branding/{kind}-{uuid7().hex[:8]}.{ext}", f)
        url = f"{django_settings.MEDIA_URL}{path}"
        current = self.current()
        old = current.get(self.ASSETS[kind]) or ""
        current[self.ASSETS[kind]] = url
        PlatformSetting.set("global", current)
        from django.core.cache import cache

        cache.delete("platform.settings")
        if old.startswith(django_settings.MEDIA_URL):  # replace: drop the previous file
            try:
                default_storage.delete(old[len(django_settings.MEDIA_URL):])
            except Exception:  # pragma: no cover
                pass
        AuditService.log("platform.settings.asset", model="PlatformSetting", after={"kind": kind, "url": url}, actor=request.user)
        return Response({"kind": kind, "url": url, **current})


class PublicLegalView(APIView):
    """
    Public: the current terms or privacy policy, by kind.

    It has to be readable without logging in — Android's prominent-disclosure rule means a rep sees the privacy
    policy *before* they ever grant background location, and an app store reviewer reads it with no account at all.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, kind=None):
        doc = LegalDocument.objects.filter(kind=kind, is_current=True).order_by("-published_at").first()
        if doc is None:
            raise DomainError("That document has not been published yet.", status_code=404)
        return Response({"kind": doc.kind, "version": doc.version, "published_at": doc.published_at, "body": doc.body})


class PublicBrandingView(APIView):
    """Public: what the web app needs before anyone logs in (site name, logo, favicon, colours, maintenance flag)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    PUBLIC_KEYS = ["site_name", "site_tagline", "company_name", "primary_color", "logo_url", "logo_dark_url", "favicon_url", "login_image_url",
                   "login_headline", "login_subtext", "footer_text", "support_email", "support_phone", "support_whatsapp", "terms_url", "privacy_url",
                   "meta_description", "maintenance_mode", "maintenance_message", "announcement_banner", "signup_enabled"]

    # The ready-made tile sources, so a deployment picks one instead of pasting a URL.
    # Wikimedia's "osm-intl" style is deliberately left out: it renders English names but answers 403 to any site
    # other than Wikimedia's own, so it would simply leave the map blank.
    TILES = {
        "osm": {"url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                "attribution": '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
                "label": "OpenStreetMap — local language (no key needed)"},
        "esri_en": {"url": "https://services.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
                    "attribution": 'Tiles &copy; Esri — Esri, DeLorme, NAVTEQ, OpenStreetMap contributors',
                    "label": "Esri World Street Map — English labels (no key; check Esri's terms for commercial use)"},
        # Google's own map, drawn under the same editing tools; the key is the server's (GOOGLE_MAPS_API_KEY).
        # Its fallback tiles stand in while Google loads, and for good if Google refuses the key.
        "google": {"url": "https://services.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
                   "attribution": 'Tiles &copy; Esri — Esri, DeLorme, NAVTEQ, OpenStreetMap contributors',
                   "label": "Google Maps — map and place search (key in the server's .env)"},
        "maptiler": {"url": "https://api.maptiler.com/maps/{style}/256/{z}/{x}/{y}.png?key={key}",
                     "attribution": '&copy; <a href="https://www.maptiler.com/copyright/">MapTiler</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
                     "label": "MapTiler — your own style, English labels (free key)"},
    }

    @classmethod
    def map_config(cls, cur):
        """{url, attribution, max_zoom} for the map screens — the key is substituted here, never in the browser."""
        choice = cur.get("map_tiles") or "osm_en"
        spec = cls.TILES.get(choice)
        if choice == "custom" or not spec:
            url = cur.get("map_tiles_url") or cls.TILES["osm"]["url"]
            return {"url": url, "attribution": cur.get("map_attribution") or cls.TILES["osm"]["attribution"], "source": "custom"}
        if choice == "google":
            # owner, 28 Oct 2026: "use google map and google search now instead of other map"
            key = getattr(django_settings, "GOOGLE_MAPS_API_KEY", "")
            return {"url": spec["url"], "attribution": spec["attribution"], "source": "google" if key else "esri_en", "google_key": key}
        url = spec["url"].replace("{key}", cur.get("map_tiles_key") or "").replace("{style}", cur.get("map_tiles_style") or "streets-v2")
        if choice == "maptiler" and not cur.get("map_tiles_key"):
            spec, url = cls.TILES["osm"], cls.TILES["osm"]["url"]  # no key yet: a working map beats a blank one
        return {"url": url, "attribution": spec["attribution"], "source": choice}

    def get(self, request):
        cur = PlatformSettingsView.current()
        return Response({**{k: cur.get(k) for k in self.PUBLIC_KEYS}, "map": self.map_config(cur)})


class EmailTestView(APIView):
    """
    `POST /platform/settings/email-test {to}` — send one real message with the settings as they stand.

    The settings page can be filled in perfectly and still not deliver: a secret that was copied from Secret ID
    instead of Value, consent never granted, a From address that is not a mailbox. Those are invisible until
    something is actually sent, so this sends it and reports what came back in words rather than a status code.
    """

    permission_classes = [IsPlatformAdmin]

    def post(self, request):
        from apps.core import mail_graph

        to = (request.data.get("to") or "").strip()
        if not to:
            raise DomainError("Who should the test go to?", status_code=400)
        cfg = PlatformSettingsView.current()
        provider = cfg.get("email_provider")
        if provider != "graph":
            raise DomainError(f"Email provider is set to “{provider}”. Choose Microsoft 365 (Graph) and save before testing.",
                              status_code=400)
        subject = f"Test message from {cfg.get('site_name') or 'DMS'}"
        body = (f"This is a test of the Microsoft 365 connection for {cfg.get('site_name') or 'DMS'}.\n\n"
                f"Sent as {cfg.get('email_from_address')} at {timezone.localtime():%d/%m/%Y %H:%M}.\n"
                "If you are reading this, the app registration, the secret and the Mail.Send consent are all in order.")
        try:
            mail_graph.send(to, subject, body, cfg=mail_graph.config())
        except mail_graph.GraphMailError as e:
            AuditService.log("platform.email.test_failed", model="PlatformSetting", after={"to": to}, actor=request.user)
            raise DomainError(str(e), code="email_failed", status_code=400) from e
        AuditService.log("platform.email.tested", model="PlatformSetting", after={"to": to}, actor=request.user)
        return Response({"ok": True, "detail": f"Sent to {to} as {cfg.get('email_from_address')}. If it does not arrive, check the junk folder."})


class PlatformHealthView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        from django.db import connection

        from django_celery_beat.models import PeriodicTask

        with connection.cursor() as c:
            c.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
            db_size = c.fetchone()[0]
        queue_len = None
        try:
            import redis
            from django.conf import settings as dj

            r = redis.Redis.from_url(dj.CELERY_BROKER_URL)
            queue_len = r.llen("celery")
        except Exception:
            pass
        from apps.integrations.models import WebhookDelivery

        return Response({
            "db_size": db_size, "queue_length": queue_len, "failed_webhooks": WebhookDelivery.objects.filter(status="failed").count(),
            "scheduled_jobs": list(PeriodicTask.objects.values("name", "enabled", "last_run_at")), "time": timezone.now(),
        })
