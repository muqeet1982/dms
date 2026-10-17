import os

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.page_permissions import page_perms
from apps.core.audit import AuditService
from apps.core.exceptions import DomainError
from apps.core.permissions import IsTenantUser, has_tenant_perm, require_perm

from .models import Attachment, AuditLog, City, DeviceToken, Notification, NotificationPreference, NumberSequence
from .notifications import NOTIFICATION_TYPES
from .numbering import DEFAULT_PREFIXES
from .serializers import AttachmentSerializer, CitySerializer, AuditLogSerializer, DeviceTokenSerializer, NotificationPreferenceSerializer, NotificationSerializer, NumberSequenceSerializer


class AttachmentViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """Upload files against any document: multipart {model, object_id, kind, file}."""

    permission_classes = [IsTenantUser]
    serializer_class = AttachmentSerializer
    parser_classes = [MultiPartParser, FormParser]
    filterset_fields = ["model", "object_id", "kind"]

    def _require(self, model, action):
        """A document's files follow the document: its page's View to read, Edit to attach or remove."""
        from apps.accounts.page_permissions import BY_KEY, page_for_model

        key = page_for_model(model)
        if key is None:
            raise DomainError(f"Unknown document type {model!r}.", status_code=400)
        actions = BY_KEY[key]["actions"]
        codename = actions.get(action) or actions.get("view")
        if not has_tenant_perm(self.request, codename):
            raise PermissionDenied(f"Missing permission: {codename}")

    def get_queryset(self):
        model = self.request.query_params.get("model")
        if self.action == "list":
            if not model:
                raise DomainError("Say which document: ?model=SalesOrder&object_id=…", status_code=400)
            self._require(model, "view")
        return Attachment.objects.all()

    def retrieve(self, request, *args, **kwargs):
        self._require(self.get_object().model, "view")
        return super().retrieve(request, *args, **kwargs)

    def perform_destroy(self, instance):
        self._require(instance.model, "edit")
        instance.delete()

    def create(self, request, *args, **kwargs):
        # checked before the payload is validated, so a missing permission answers 403 rather than a field error
        self._require(request.data.get("model"), "edit")
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        f = self.request.FILES.get("file")
        if f is None:
            raise DomainError("file is required", status_code=400)
        ext = os.path.splitext(f.name)[1].lower().lstrip(".")
        if ext not in settings.ALLOWED_UPLOAD_EXTENSIONS:
            raise DomainError(f"File type .{ext} is not allowed.", status_code=400)
        if f.size > settings.DATA_UPLOAD_MAX_MEMORY_SIZE:
            raise DomainError("File too large.", status_code=400)
        from apps.core.plan_limits import PlanLimitService

        PlanLimitService.check(self.request.tenant, "max_storage_mb", extra=max(1, f.size // (1024 * 1024)))
        serializer.save(name=f.name, size=f.size, content_type=getattr(f, "content_type", ""), uploaded_by=self.request.user)


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsTenantUser]
    serializer_class = AuditLogSerializer
    required_perm = "users.view_activity"
    filterset_fields = ["action", "model", "object_id", "actor"]
    search_fields = ["action", "model", "object_repr"]
    ordering_fields = ["at"]

    def get_queryset(self):
        return AuditLog.objects.filter(tenant=self.request.tenant).select_related("actor")


class DocumentHistoryView(APIView):
    """History tab for any document: /history/{model}/{object_id}"""

    permission_classes = [IsTenantUser]

    def get(self, request, model, object_id):
        from apps.accounts.page_permissions import BY_KEY, page_for_model

        key = page_for_model(model)
        if key is None or not has_tenant_perm(request, BY_KEY[key]["actions"].get("view")):
            raise PermissionDenied("You don't have permission to see this document's history.")
        qs = AuditLog.objects.filter(tenant=request.tenant, model__iexact=model, object_id=str(object_id)).select_related("actor")[:200]
        return Response(AuditLogSerializer(qs, many=True).data)


class MyNotificationViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer
    filterset_fields = ["type"]

    def get_queryset(self):
        qs = Notification.all_objects.filter(user=self.request.user)
        if getattr(self.request, "tenant", None) is not None:
            qs = qs.filter(tenant=self.request.tenant)
        if self.request.query_params.get("unread") == "1":
            qs = qs.filter(read_at__isnull=True)
        return qs

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        return Response({"unread": self.get_queryset().filter(read_at__isnull=True).count()})

    @action(detail=True, methods=["post"], url_path="read")
    def mark_read(self, request, pk=None):
        n = self.get_object()
        n.read_at = n.read_at or timezone.now()
        n.save(update_fields=["read_at"])
        return Response(NotificationSerializer(n).data)

    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        self.get_queryset().filter(read_at__isnull=True).update(read_at=timezone.now())
        return Response(status=204)


class MyDeviceViewSet(viewsets.ModelViewSet):
    """Register the mobile device's FCM token: POST {platform, token, device_name, app_version}."""

    permission_classes = [IsAuthenticated]
    serializer_class = DeviceTokenSerializer
    pagination_class = None

    def get_queryset(self):
        return DeviceToken.objects.filter(user=self.request.user)

    def create(self, request, *args, **kwargs):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        obj, _ = DeviceToken.objects.update_or_create(user=request.user, token=s.validated_data["token"], defaults={k: v for k, v in s.validated_data.items() if k != "token"})
        return Response(DeviceTokenSerializer(obj).data, status=201)


class MyNotificationPreferencesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        existing = {p.type: p for p in NotificationPreference.objects.filter(user=request.user)}
        rows = []
        for key, label in NOTIFICATION_TYPES:
            p = existing.get(key)
            rows.append({"type": key, "label": label, "push": p.push if p else True, "email": p.email if p else False, "sms": p.sms if p else False})
        return Response(rows)

    def put(self, request):
        for row in request.data:
            s = NotificationPreferenceSerializer(data=row)
            s.is_valid(raise_exception=True)
            NotificationPreference.objects.update_or_create(user=request.user, type=s.validated_data["type"], defaults={k: s.validated_data[k] for k in ("push", "email", "sms")})
        return self.get(request)


class NumberingViewSet(viewsets.ModelViewSet):
    permission_classes = [IsTenantUser]
    serializer_class = NumberSequenceSerializer
    perm_map = page_perms("settings_numbering")
    pagination_class = None
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return NumberSequence.objects.all().order_by("document_type")

    def list(self, request, *args, **kwargs):
        # make sure every document type has a row so the settings screen can edit it
        existing = set(NumberSequence.objects.values_list("document_type", flat=True))
        for dt, prefix in DEFAULT_PREFIXES.items():
            if dt not in existing:
                NumberSequence.objects.create(document_type=dt, prefix=prefix)
        return super().list(request, *args, **kwargs)

    @action(detail=False, methods=["get", "post"], url_path="company-code")
    def company_code(self, request):
        """The short company code stamped before every document number (TDN-SO-00001)."""
        from apps.platform.models import Tenant

        tenant = request.tenant
        if request.method == "POST":
            code = str(request.data.get("code", "")).strip().upper()
            if not code or len(code) > 8 or not code.replace("-", "").isalnum():
                raise DomainError("The company code must be 1–8 letters or digits.")
            if Tenant.objects.exclude(pk=tenant.pk).filter(doc_code=code).exists():
                raise DomainError(f"The code {code} is already used by another company.")
            tenant.doc_code = code
            tenant.save(update_fields=["doc_code"])
            AuditService.log("settings.doc_code", tenant, after={"code": code})
        return Response({"code": tenant.doc_code, "example": f"{tenant.doc_code}-SO-00001" if tenant.doc_code else "SO-00001"})


class TenantSubscriptionView(APIView):
    """Tenant's own plan, limits, usage and platform invoices."""

    permission_classes = [IsTenantUser]

    def get(self, request):
        from apps.core.plan_limits import effective_features, effective_limits
        from apps.platform.serializers import PlatformInvoiceSerializer, PlatformPaymentSerializer, SubscriptionSerializer
        from apps.platform.services import TenantService

        t = request.tenant
        sub = t.active_subscription
        return Response({
            "subscription": SubscriptionSerializer(sub).data if sub else None,
            "limits": effective_limits(t), "features": effective_features(t), "usage": TenantService.usage(t),
            "invoices": PlatformInvoiceSerializer(t.platform_invoices.all()[:50], many=True).data,
            "payments": PlatformPaymentSerializer(t.platform_payments.all()[:50], many=True).data,
        })


class TenantAnnouncementsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.db.models import Q

        from apps.platform.models import Announcement

        now = timezone.now()
        qs = Announcement.objects.filter(is_active=True, starts_at__lte=now).filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now))
        t = getattr(request, "tenant", None)
        if t is not None:
            sub = t.active_subscription
            qs = qs.filter(Q(audience="all") | Q(audience="selected", tenants=t) | Q(audience="plan", plan_code=sub.plan_snapshot.get("code") if sub else ""))
        else:
            qs = qs.filter(audience="all")
        return Response([{"id": str(a.id), "title": a.title, "body": a.body, "level": a.level, "dismissible": a.dismissible, "starts_at": a.starts_at} for a in qs.distinct()])


class TenantSupportTicketViewSet(viewsets.ModelViewSet):
    permission_classes = [IsTenantUser]
    perm_map = page_perms("settings_support", {"reply": "settings.add_ticket"})

    def get_serializer_class(self):
        from apps.platform.serializers import SupportTicketSerializer

        return SupportTicketSerializer

    def get_queryset(self):
        from apps.platform.models import SupportTicket

        return SupportTicket.objects.filter(tenant=self.request.tenant)

    def perform_create(self, serializer):
        from apps.platform.models import TicketMessage

        t = serializer.save(tenant=self.request.tenant, opened_by=self.request.user)
        body = self.request.data.get("body")
        if body:
            TicketMessage.objects.create(ticket=t, author=self.request.user, body=body)

    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        from apps.platform.models import TicketMessage
        from apps.platform.serializers import TicketMessageSerializer

        t = self.get_object()
        msg = TicketMessage.objects.create(ticket=t, author=request.user, body=request.data.get("body", ""))
        t.status = "open"
        t.save(update_fields=["status", "updated_at"])
        return Response(TicketMessageSerializer(msg).data, status=201)


class CityViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Shared city list for the customer/supplier forms — `?search=lah` or `?province=Punjab`."""

    permission_classes = [IsAuthenticated]
    serializer_class = CitySerializer
    pagination_class = None
    queryset = City.objects.filter(is_active=True)
    search_fields = ["name", "province"]
    filterset_fields = ["province", "country"]

    filter_backends: list = []  # the slice below is the whole filtering story — DRF's backends would re-filter it

    def get_queryset(self):
        qs = City.objects.filter(is_active=True)
        q = (self.request.query_params.get("search") or "").strip()
        province = self.request.query_params.get("province")
        if province:
            qs = qs.filter(province__iexact=province)
        if q:
            from django.db.models import Case, IntegerField, When

            qs = qs.filter(Q(name__icontains=q) | Q(province__istartswith=q)).annotate(
                rank=Case(When(name__istartswith=q, then=0), When(province__istartswith=q, then=2), default=1, output_field=IntegerField())
            ).order_by("rank", "name")  # cities that start with what was typed come first
        return qs[:50]
