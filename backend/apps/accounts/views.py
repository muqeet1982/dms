from django.conf import settings
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenBackendError, TokenError
from rest_framework_simplejwt.state import token_backend
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.core.audit import AuditService
from apps.core.exceptions import DomainError
from apps.core.notifications import send_email, send_sms
from apps.core.permissions import IsTenantUser, has_tenant_perm, require_perm
from apps.core.plan_limits import effective_features, effective_limits

from .models import GroupProfile, OneTimeCode, QrLoginToken, TenantMembership, UserSession
from .page_permissions import extra_perm, extra_perm_or, page_perms
from .permissions_catalog import catalog_as_json
from .serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    MeSerializer,
    MembershipBriefSerializer,
    OTPRequestSerializer,
    OTPVerifySerializer,
    PasswordResetSerializer,
    RoleSerializer,
    SessionSerializer,
    TenantUserSerializer,
    TransferOwnershipSerializer,
    find_user,
    open_session,
)
from .services import RoleService, SessionService, UserService


class LoginView(TokenObtainPairView):
    serializer_class = LoginSerializer
    throttle_scope = "auth"

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200:
            AuditService.log("auth.login", model="User", object_id=response.data["user"]["id"], request=request)
        return response


class QrLoginCreateView(APIView):
    """
    POST /auth/qr-login/create — the signed-in web user asks for a "log in on the phone" code.
    Returns the QR as a data-URI PNG (the secret never appears in a URL/log) plus the URL/deep link it encodes.
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = "auth"

    def post(self, request):
        import base64
        import io

        import segno

        from apps.platform.app_release import mobile_app_config

        user = request.user
        if user.user_type == "PLATFORM_ADMIN":
            raise DomainError("Platform admins do not use the mobile app — sign in on the phone as a company user.", status_code=403)
        if user.must_change_password:
            raise DomainError("Change your password before linking a phone.", status_code=403)
        tenant = getattr(request, "tenant", None)
        row, raw = QrLoginToken.issue(user, tenant=tenant, request=request)
        url = f"{settings.PUBLIC_BASE_URL}/app-login?t={raw}"
        cfg = mobile_app_config()
        buf = io.BytesIO()
        segno.make(url, error="m").save(buf, kind="png", scale=6, border=2, dark="#202C4B")
        AuditService.log("auth.qr_login.issued", model="User", object_id=user.id, request=request)
        return Response({
            "id": str(row.id), "url": url, "deep_link": f"{cfg['scheme']}://login?t={raw}", "qr_png": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
            "expires_at": row.expires_at, "expires_in": settings.DMS.get("QR_LOGIN_TTL_SECONDS", 300), "tenant_id": str(tenant.id) if tenant is not None else None,
        })


class QrLoginStatusView(APIView):
    """GET /auth/qr-login/status?id=<uuid> (web modal) or ?t=<token> (landing page) → pending | used | expired."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "auth"

    def get(self, request):
        row = None
        if request.query_params.get("t"):
            row = QrLoginToken.find(request.query_params["t"])
        elif request.query_params.get("id"):
            row = QrLoginToken.objects.filter(pk=request.query_params["id"]).first()
        if row is None:
            return Response({"status": "invalid"})
        return Response({"status": row.status, "expires_at": row.expires_at, "redeemed_at": row.used_at, "redeemed_device": row.redeemed_device if row.used_at else ""})


class QrLoginRedeemView(APIView):
    """
    POST /auth/qr-login/redeem { token | url, device_name, platform } — the phone app sends what it scanned
    (raw token, the https://…/app-login?t= URL or the dms://login?t= deep link) and receives the same payload as /auth/login.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "auth"

    @staticmethod
    def extract_token(value: str) -> str:
        from urllib.parse import parse_qs, urlparse

        value = (value or "").strip()
        if "://" in value or value.startswith("/"):
            q = parse_qs(urlparse(value).query)
            return (q.get("t") or q.get("token") or [""])[0].strip()
        return value

    def post(self, request):
        raw = self.extract_token(request.data.get("token") or request.data.get("url") or "")
        row = QrLoginToken.find(raw) if raw else None
        if row is None:
            raise DomainError("This QR code is not valid. Open the DMS web app → profile → 'Log in on phone' and scan the new code.", code="invalid", status_code=400)
        if row.status == "used":
            raise DomainError("This QR code has already been used — generate a new one on the computer.", code="used", status_code=410)
        if row.status == "expired":
            raise DomainError("This QR code has expired — generate a new one on the computer (codes last 5 minutes).", code="expired", status_code=410)
        user = row.user
        if not user.is_active:
            raise DomainError("Account is inactive.", code="inactive", status_code=403)
        platform = request.data.get("platform") or "android"
        data = open_session(user, request, device_name=request.data.get("device_name"), platform=platform)
        row.used_at = timezone.now()
        row.redeemed_ip = getattr(request, "client_ip", None)
        row.redeemed_device = (request.data.get("device_name") or getattr(request, "user_agent", "") or "")[:200]
        row.redeemed_platform = platform
        row.session_id = data.get("session_id")
        row.save(update_fields=["used_at", "redeemed_ip", "redeemed_device", "redeemed_platform", "session"])
        AuditService.log("auth.qr_login", model="User", object_id=user.id, request=request, after={"platform": platform, "device": row.redeemed_device})
        data["tenant_id"] = str(row.tenant_id) if row.tenant_id else None
        data["login_method"] = "qr"
        return Response(data)


class RefreshView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    @extend_schema(request={"application/json": {"type": "object", "properties": {"refresh": {"type": "string"}}}}, responses={200: None})
    def post(self, request):
        from datetime import timedelta

        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

        raw = request.data.get("refresh")
        if not raw:
            raise DomainError("refresh token required", status_code=400)
        # Decoded here rather than through RefreshToken() so that an already-rotated token still reaches the
        # grace check below; the signature and expiry are verified either way.
        try:
            payload = token_backend.decode(raw)
        except (TokenError, TokenBackendError) as e:
            raise DomainError(str(e), code="token_invalid", status_code=401)
        old_jti = payload.get("jti") or ""
        user_id = payload.get("user_id")
        session = UserSession.objects.filter(refresh_jti=old_jti).first()
        if session is None or session.revoked_at:
            grace_seconds = int(settings.DMS.get("REFRESH_GRACE_SECONDS", 60))
            raced = UserSession.objects.filter(
                prev_refresh_jti=old_jti, revoked_at__isnull=True, rotated_at__gte=timezone.now() - timedelta(seconds=grace_seconds)
            ).first()
            if raced is not None:
                # Another tab (or a retry) rotated this token a moment ago: hand out a fresh pair instead of
                # locking the user out of every tab.
                session = raced
            else:
                # Real reuse of a rotated/revoked token → revoke the whole family (reuse detection). The token is no
                # longer any session's current one, so the family is found through the session that replaced it.
                # Answered instead of raised: ATOMIC_REQUESTS would roll the revocation back with the exception.
                stolen = session or UserSession.objects.filter(prev_refresh_jti=old_jti).first()
                if stolen is not None:
                    UserSession.objects.filter(family=stolen.family).update(revoked_at=timezone.now())
                return Response(
                    {"type": "token_reuse", "title": "Session ended", "status": 401, "detail": "This session was used from somewhere else; please log in again.", "errors": {}},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
        from .models import User

        user = User.objects.filter(pk=user_id, is_active=True).first()
        if user is None:
            raise DomainError("User inactive.", code="inactive", status_code=401)
        new = LoginSerializer.get_token(user)
        ot = OutstandingToken.objects.filter(jti=old_jti).first()
        if ot is not None:
            BlacklistedToken.objects.get_or_create(token=ot)
        SessionService.rotate(session, str(new["jti"]))
        return Response({"access": str(new.access_token), "refresh": str(new)})


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        raw = request.data.get("refresh")
        if raw:
            try:
                token = RefreshToken(raw)
                session = UserSession.objects.filter(refresh_jti=token["jti"]).first()
                if session:
                    SessionService.revoke(session)
                else:
                    token.blacklist()
            except TokenError:
                pass
        AuditService.log("auth.logout", request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class OTPRequestView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "otp"

    def post(self, request):
        s = OTPRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = find_user(s.validated_data["identifier"])
        # Always answer 200 to avoid account enumeration
        if user is not None:
            otp = OneTimeCode.issue(user, s.validated_data["purpose"], s.validated_data["channel"], settings.DMS["OTP_TTL_MINUTES"])
            text = f"Your verification code is {otp.code}. It expires in {settings.DMS['OTP_TTL_MINUTES']} minutes."
            if s.validated_data["channel"] == "sms" and user.phone:
                send_sms(user.phone, text)
            elif user.email:
                send_email(user.email, "Your verification code", text)
        return Response({"detail": "If the account exists, a code has been sent."})


def _consume_otp(identifier, purpose, code):
    user = find_user(identifier)
    if user is None:
        raise DomainError("Invalid code.", status_code=400)
    otp = OneTimeCode.objects.filter(user=user, purpose=purpose, used_at__isnull=True).order_by("-created_at").first()
    if otp is None or not otp.is_valid() or otp.code != code:
        if otp is not None:
            otp.attempts += 1
            otp.save(update_fields=["attempts"])
        raise DomainError("Invalid or expired code.", status_code=400)
    otp.used_at = timezone.now()
    otp.save(update_fields=["used_at"])
    return user


class OTPVerifyView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "otp"

    def post(self, request):
        s = OTPVerifySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = _consume_otp(s.validated_data["identifier"], s.validated_data["purpose"], s.validated_data["code"])
        # verified → issue a short-lived reset ticket (new OTP row) usable once for password set
        ticket = OneTimeCode.issue(user, s.validated_data["purpose"], ttl_minutes=15)
        return Response({"detail": "Code verified.", "ticket": ticket.code})


class PasswordResetView(APIView):
    """Complete a reset/invite: identifier + code (or ticket) + new password."""

    permission_classes = [AllowAny]
    throttle_scope = "otp"

    def post(self, request):
        s = PasswordResetSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = _consume_otp(s.validated_data["identifier"], s.validated_data["purpose"], s.validated_data["code"])
        user.set_password(s.validated_data["new_password"])
        user.must_change_password = False
        user.is_active = True
        user.save()
        SessionService.revoke_all(user, reason="password_reset")
        for m in user.memberships.filter(status="invited"):
            m.status = "active"
            m.save(update_fields=["status"])
        AuditService.log("auth.password_reset", user, request=request)
        return Response({"detail": "Password updated. You can now sign in."})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        tenant = getattr(request, "tenant", None)
        membership = getattr(request, "membership", None)
        data = {
            "user": MeSerializer(user, context={"request": request}).data,
            "memberships": MembershipBriefSerializer(user.memberships.filter(status="active").select_related("tenant"), many=True).data,
            "tenant": None,
            "permissions": [],
            "features": {},
            "limits": {},
            "is_owner": False,
            "impersonating": getattr(request, "impersonating", False),
            "impersonation_expires_at": getattr(getattr(request, "impersonation", None), "expires_at", None),
            # who the user is being acted as, when a *specific* user was chosen (not the plain owner view).
            # Set for both the tenant-owner login-as and a platform admin who logged in as a user.
            "impersonating_as": (getattr(getattr(request, "impersonation", None), "target", None).full_name
                                 if getattr(request, "impersonating", False) and membership is not None and not membership.is_owner else None),
            "platform_permissions": sorted(user.platform_perms()),
            "warehouses": [],
            "supplier_rep": None,
            "pages": {},
        }
        if tenant is not None:
            from apps.platform.serializers import TenantPublicSerializer

            data["tenant"] = TenantPublicSerializer(tenant).data
            data["features"] = effective_features(tenant)
            # white-label: the stored branding only reaches the app when the platform allows it
            white_label = bool(data["features"].get("branding"))
            data["tenant"]["white_label"] = white_label
            if not white_label:
                data["tenant"]["branding"] = {}
            data["limits"] = effective_limits(tenant)
            if membership is not None:
                from .page_permissions import resolve_pages

                data["permissions"] = sorted(membership.permission_codenames())
                data["is_owner"] = membership.is_owner
                # Which kinds of role this login holds, as a fact rather than something the client works out by
                # subtracting the rep-grantable pages from `pages`. It decides which of the two front ends the
                # user can use, so an inference there would quietly mis-route a supervisor who also sells.
                data["role_types"] = sorted({g.profile.role_type for g in membership.groups.select_related("profile")})
                data["has_office_role"] = membership.is_owner or "internal" in data["role_types"]
                # what this user may do page by page — the UI hides anything that is off (see page_permissions.py)
                data["pages"] = resolve_pages(data["permissions"], is_owner=membership.is_owner)
                data["warehouses"] = [str(w.warehouse_id) for w in membership.warehouse_access.all()]
                if membership.supplier_rep_id:
                    rep = membership.supplier_rep
                    data["supplier_rep"] = {"id": str(rep.id), "supplier_id": str(rep.supplier_id), "supplier_name": rep.supplier.name, "code": rep.code, "territory": rep.territory}
        return Response(data)

    def patch(self, request):
        s = MeSerializer(request.user, data=request.data, partial=True, context={"request": request})
        s.is_valid(raise_exception=True)
        s.save()
        return Response(s.data)


class MePermissionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        membership = getattr(request, "membership", None)
        perms = sorted(membership.permission_codenames()) if membership else []
        return Response({"permissions": perms, "platform_permissions": sorted(request.user.platform_perms())})


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        s = ChangePasswordSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = request.user
        if not user.must_change_password and not user.check_password(s.validated_data.get("current_password", "")):
            raise DomainError("Current password is incorrect.", status_code=400)
        user.set_password(s.validated_data["new_password"])
        user.must_change_password = False
        user.save()
        SessionService.revoke_all(user, reason="password_changed")
        return Response({"detail": "Password changed. Please sign in again."})


class SessionViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = SessionSerializer
    pagination_class = None

    def get_queryset(self):
        return UserSession.objects.filter(user=self.request.user, revoked_at__isnull=True)

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        SessionService.revoke(self.get_object())
        return Response(status=204)

    @action(detail=False, methods=["post"], url_path="revoke-all")
    def revoke_all(self, request):
        SessionService.revoke_all(request.user, reason="user_request")
        return Response(status=204)


class PermissionsCatalogView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        role_type = request.query_params.get("role_type", "internal")
        return Response(catalog_as_json(role_type))


class PermissionPagesView(APIView):
    """The page matrix the role editor renders: groups → pages → the view/add/edit/delete switches each page has."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .page_permissions import pages_as_json

        if request.query_params.get("role_type") == "external":
            # reps get the rep catalogue, which is not page-shaped
            return Response([])
        return Response(pages_as_json())


class RoleViewSet(viewsets.ModelViewSet):
    permission_classes = [IsTenantUser]
    serializer_class = RoleSerializer
    perm_map = page_perms("roles", {"clone": extra_perm_or("roles", "add")})
    filterset_fields = ["role_type", "is_system"]
    search_fields = ["name", "description"]
    pagination_class = None

    def get_queryset(self):
        return GroupProfile.objects.filter(tenant=self.request.tenant).select_related("group").order_by("name")

    def perform_create(self, serializer):
        d = serializer.validated_data
        profile = RoleService.create_role(self.request.tenant, d["name"], d.get("permissions", []), d.get("role_type", "internal"), d.get("description", ""), approval_limits=d.get("approval_limits"))
        serializer.instance = profile

    def perform_update(self, serializer):
        d = serializer.validated_data
        RoleService.update_role(serializer.instance, name=d.get("name"), permissions=d.get("permissions"), description=d.get("description"), approval_limits=d.get("approval_limits"), role_type=d.get("role_type"))

    def perform_destroy(self, instance):
        RoleService.delete_role(instance)

    @action(detail=True, methods=["post"])
    @require_perm("users.add_role")
    def clone(self, request, pk=None):
        src = self.get_object()
        name = request.data.get("name") or f"{src.name} (copy)"
        profile = RoleService.create_role(request.tenant, name, src.permission_codenames, src.role_type, src.description, approval_limits=src.approval_limits)
        return Response(RoleSerializer(profile).data, status=201)


class TenantUserViewSet(viewsets.ModelViewSet):
    permission_classes = [IsTenantUser]
    serializer_class = TenantUserSerializer
    perm_map = page_perms("users", {
        "activate": "users.change_user",
        "reset_password": extra_perm("users", "reset_password"),
        "resend_invite": "users.add_user",
        "revoke_sessions": "users.change_user",
        "activity": "users.view_activity",
        "transfer_ownership": "users.change_user",
        "access": "users.view_user",
        "preview_access": "users.view_user",
        "login_as": "users.change_user_access",
        # no perm entry for exit: while acting as a user the owner may hold none of these rights, and they must
        # always be able to come back. The method only ends the real user's own session.
    })
    search_fields = ["user__full_name", "user__email", "user__phone"]
    filterset_fields = ["status", "is_owner", "user__user_type"]
    ordering_fields = ["joined_at", "user__full_name"]

    def get_queryset(self):
        return TenantMembership.objects.filter(tenant=self.request.tenant).select_related("user").prefetch_related("groups__profile", "warehouse_access")

    def _field_access(self, membership, roles, data):
        """
        A field (rep) role has to hang off a rep record, and this is where that is enforced.

        The rep app works out whose customers, whose working areas and whose targets a caller has from
        `membership.supplier_rep`. Without it a login holds rep *permissions* with no rep *identity*, which is
        not a narrower view of the company but an unscoped one. So a field role requires a rep: an existing
        one, or a new one created here from the supplier named, which keeps it to a single form.

        Taking the last field role away unlinks the rep, because the login is office staff again.
        """
        from apps.core.numbering import next_number
        from apps.partners.models import Supplier, SupplierRep

        wants_field = any(r.role_type == "external" for r in (roles or []))
        rep = data.get("supplier_rep", "__unset__")
        supplier_id = data.get("rep_for_supplier")

        if not wants_field:
            if roles is not None and membership is not None and membership.supplier_rep_id:
                membership.supplier_rep = None
                membership.save(update_fields=["supplier_rep"])
            return None

        if rep not in ("__unset__", None):
            return rep
        if membership is not None and membership.supplier_rep_id:
            return membership.supplier_rep
        if supplier_id:
            supplier = Supplier.objects.filter(pk=supplier_id).first()
            if supplier is None:
                raise DomainError("That supplier is not on this company.", code="bad_supplier", status_code=400)
            name = (data.get("user", {}) or {}).get("full_name") or (membership.user.full_name if membership else "Rep")
            return SupplierRep.objects.create(
                supplier=supplier, code=next_number("supplier_rep", unique_for=(SupplierRep, "code")), name=name,
                email=(data.get("user", {}) or {}).get("email") or "",
            )
        raise DomainError(
            "A field role needs a rep record — pick the rep this login belongs to, or choose the supplier they "
            "sell for and one will be created. Without it the login would hold rep permissions but have no "
            "customers of its own.",
            code="rep_required", status_code=400,
        )

    def _roles(self, ids):
        if ids is None:
            return None
        return list(GroupProfile.objects.filter(tenant=self.request.tenant, id__in=[getattr(r, "id", r) for r in ids]))

    def _warehouses(self, ids):
        if ids is None:
            return None
        from apps.inventory.models import Warehouse

        return list(Warehouse.objects.filter(id__in=ids))

    def perform_create(self, serializer):
        d = serializer.validated_data
        u = d.get("user", {})
        roles = self._roles(d.get("role_profiles")) or []
        rep = self._field_access(None, roles, d)
        if (roles or d.get("warehouses")) and not has_tenant_perm(self.request, extra_perm("users", "manage_access")):
            raise DomainError("You can add users, but not give them roles or warehouse access.", code="forbidden", status_code=403)
        if d.get("password"):  # a password typed by an admin has to be a strong one
            UserService.check_password_strength(d["password"])
        membership = UserService.create_tenant_user(
            self.request.tenant, u.get("full_name", ""), email=u.get("email"), phone=u.get("phone"), password=d.get("password"), roles=roles, warehouses=self._warehouses(d.get("warehouses")) or [], send_invite=d.get("send_invite", False),
            supplier_rep=rep,
        )
        if rep is not None and rep.user_id is None:
            rep.user = membership.user
            rep.save(update_fields=["user"])
        serializer.instance = membership

    def perform_update(self, serializer):
        d = serializer.validated_data
        u = d.get("user", {})
        # changing *what someone can do* is a separate switch from editing their name or phone
        if (d.get("role_profiles") is not None or d.get("warehouses") is not None) and not has_tenant_perm(self.request, extra_perm("users", "manage_access")):
            raise DomainError("You can edit this user, but not change their roles or warehouse access.", code="forbidden", status_code=403)
        roles = self._roles(d.get("role_profiles"))
        rep = self._field_access(serializer.instance, roles, d) if roles is not None else None
        membership = UserService.update_membership(serializer.instance, full_name=u.get("full_name"), email=u.get("email"), phone=u.get("phone"), roles=roles, warehouses=self._warehouses(d.get("warehouses")), status=d.get("status"))
        if rep is not None and membership.supplier_rep_id != rep.pk:
            membership.supplier_rep = rep
            membership.save(update_fields=["supplier_rep"])
            if rep.user_id is None:
                rep.user = membership.user
                rep.save(update_fields=["user"])

    def perform_destroy(self, instance):
        UserService.update_membership(instance, status="suspended")

    @action(detail=True, methods=["post"], url_path="login-as")
    def login_as(self, request, pk=None):
        """
        The owner starts acting as this user, to see the app with that user's roles and pages. Only the owner,
        only within their own company, and never as another owner. It reuses the impersonation session the
        platform admin flow uses, so the same expiry and audit apply; `exit-impersonation` ends it.
        """
        from datetime import timedelta

        from django.utils import timezone

        from apps.core.plan_limits import platform_setting
        from .models import ImpersonationSession

        target_membership = self.get_object()
        if target_membership.is_owner:
            raise DomainError("You cannot log in as another owner.", status_code=400)
        if target_membership.status != "active":
            raise DomainError("That login is not active.", status_code=400)

        # Two ways to reach here. A platform admin (or anyone) already inside the tenant through an impersonation
        # session just re-points that session at the chosen user. A plain tenant owner, not impersonating, opens
        # a fresh owner login-as session. Either way `exit-impersonation` takes them back.
        existing = getattr(request, "impersonation", None)
        if existing is not None and getattr(request, "impersonating", False):
            existing.target = target_membership.user
            existing.save(update_fields=["target"])
        else:
            me = getattr(request, "membership", None)
            if me is None or not me.is_owner:
                raise DomainError("Only the company owner can log in as a user.", code="forbidden", status_code=403)
            minutes = int(platform_setting("impersonation_minutes", 60) or 60)
            ImpersonationSession.objects.filter(admin=request.user, tenant=request.tenant, ended_at__isnull=True).update(ended_at=timezone.now())
            ImpersonationSession.objects.create(
                admin=request.user, target=target_membership.user, tenant=request.tenant,
                expires_at=timezone.now() + timedelta(minutes=minutes), reason="owner login-as-user",
            )
        AuditService.log("user.login_as", target_membership, after={"as": target_membership.user.full_name}, tenant_id=request.tenant.id)
        return Response({"ok": True, "acting_as": target_membership.user.full_name})

    @action(detail=False, methods=["post"], url_path="exit-impersonation")
    def exit_impersonation(self, request):
        """
        Go back from acting as a user.

        A platform admin was already viewing the tenant before they logged in as a user, so they revert to the
        owner and stay in the company. A plain tenant owner ends the login-as and becomes themselves again.
        """
        from django.utils import timezone

        from .models import ImpersonationSession, TenantMembership

        imp = ImpersonationSession.objects.filter(admin=request.user, tenant=request.tenant, ended_at__isnull=True).first()
        if imp is None:
            return Response({"ok": True})
        if getattr(request.user, "is_platform_admin", False):
            owner = TenantMembership.objects.filter(tenant=request.tenant, is_owner=True).select_related("user").first()
            if owner is not None:
                imp.target = owner.user
                imp.save(update_fields=["target"])
                return Response({"ok": True, "reverted_to_owner": True})
        imp.ended_at = timezone.now()
        imp.save(update_fields=["ended_at"])
        return Response({"ok": True, "ended": True})

    @action(detail=False, methods=["post"], url_path="preview-access")
    @require_perm("users.view_user")
    def preview_access(self, request):
        """
        `POST /api/v1/users/preview-access {"roles": [id, …]}` — what *these* roles would grant, before saving.

        The screen that hands out roles has to show their effect while the administrator is still choosing, and
        `/access` can only answer for roles already saved. Without this the browser had to re-implement
        `resolve_pages` and the codename expansion to fill the gap — two copies of the permission rules, correct
        on the day they were written and drifting from then on. The answer comes from the same resolution the
        user's own session uses.
        """
        from .page_permissions import PAGES, expand, resolve_pages

        ids = request.data.get("roles") or []
        roles = list(GroupProfile.objects.filter(tenant=request.tenant, id__in=[str(i) for i in ids]))
        stored = set()
        for role in roles:
            stored |= set(role.permission_codenames)
        perms = sorted(expand(stored))
        pages = resolve_pages(perms, is_owner=False)
        labels = {p["key"]: {"label": p["label"], "group": p["group"], "path": p["path"]} for p in PAGES}
        visible = [{
            **labels.get(key, {"label": key, "group": "", "path": ""}),
            "key": key,
            "actions": {a: v for a, v in rights.items() if a != "extras"},
            "extras": rights.get("extras", {}),
        } for key, rights in pages.items() if rights.get("view")]
        field = [p for p in perms if p.startswith("external.")]
        return Response({
            "roles": [{"id": str(r.id), "name": r.name, "type": r.role_type} for r in roles],
            "unknown_roles": sorted({str(i) for i in ids} - {str(r.id) for r in roles}),
            "pages": sorted(visible, key=lambda p: (p["group"], p["label"])),
            "pages_total": len(PAGES),
            "permissions": perms,
            "field_permissions": field,
            "needs_rep": bool(field),
        })

    @action(detail=True, methods=["get"])
    @require_perm("users.view_user")
    def access(self, request, pk=None):
        """
        `GET /api/v1/users/{id}/access` — what this user can actually reach, worked out from their roles.

        The same resolution the user's own session uses, so the screen that grants a role can show its effect
        before anyone saves: which pages appear in their menu, what they may do on each, and the extras. An
        administrator should not have to log in as somebody to find out what they just handed them.
        """
        from .page_permissions import PAGES, resolve_pages

        membership = self.get_object()
        perms = sorted(membership.permission_codenames())
        pages = resolve_pages(perms, is_owner=membership.is_owner)
        labels = {p["key"]: {"label": p["label"], "group": p["group"], "path": p["path"]} for p in PAGES}
        visible = [{
            **labels.get(key, {"label": key, "group": "", "path": ""}),
            "key": key,
            "actions": {a: v for a, v in rights.items() if a != "extras"},
            "extras": rights.get("extras", {}),
        } for key, rights in pages.items() if rights.get("view")]
        field = [p for p in perms if p.startswith("external.")]
        return Response({
            "user_name": membership.user.full_name or membership.user.username,
            "is_owner": membership.is_owner,
            "roles": [{"id": str(g.profile.id), "name": g.profile.name, "type": g.profile.role_type}
                      for g in membership.groups.select_related("profile")],
            "pages": sorted(visible, key=lambda p: (p["group"], p["label"])),
            "pages_total": len(PAGES),
            "permissions": perms,
            "field_permissions": field,
            # the same shape the user row uses, so a screen reading both does not need two code paths
            "supplier_rep": str(membership.supplier_rep_id) if membership.supplier_rep_id else None,
            "supplier_rep_code": membership.supplier_rep.code if membership.supplier_rep_id else None,
            "rep_name": f"{membership.supplier_rep.code} · {membership.supplier_rep.name}" if membership.supplier_rep_id else "",
            # a field role with no rep is the one combination that grants permissions it cannot use
            "field_without_rep": bool(field) and not membership.supplier_rep_id,
        })

    @action(detail=True, methods=["post"])
    @require_perm("users.change_user")
    def activate(self, request, pk=None):
        m = UserService.update_membership(self.get_object(), status="active")
        return Response(TenantUserSerializer(m, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="reset-password")
    @require_perm("users.reset_password")
    def reset_password(self, request, pk=None):
        m = self.get_object()
        pwd = request.data.get("password")
        if not pwd:
            raise DomainError("password is required", status_code=400)
        UserService.reset_password(m.user, pwd, by_admin=True)
        return Response({"detail": "Password reset."})

    @action(detail=True, methods=["post"], url_path="resend-invite")
    @require_perm("users.add_user")
    def resend_invite(self, request, pk=None):
        m = self.get_object()
        UserService.send_invite(m.user, request.tenant)
        return Response({"detail": "Invitation sent."})

    @action(detail=True, methods=["post"], url_path="revoke-sessions")
    @require_perm("users.change_user")
    def revoke_sessions(self, request, pk=None):
        SessionService.revoke_all(self.get_object().user, reason="admin")
        return Response(status=204)

    @action(detail=True, methods=["get"], url_path="activity")
    @require_perm("users.view_activity")
    def activity(self, request, pk=None):
        from apps.core.models import AuditLog
        from apps.core.serializers import AuditLogSerializer

        m = self.get_object()
        qs = AuditLog.objects.filter(tenant=request.tenant, actor=m.user)[:200]
        return Response(AuditLogSerializer(qs, many=True).data)

    @action(detail=False, methods=["post"], url_path="transfer-ownership")
    def transfer_ownership(self, request):
        s = TransferOwnershipSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        target = self.get_queryset().get(pk=s.validated_data["membership_id"])
        UserService.transfer_ownership(request.tenant, request.membership, target)
        return Response({"detail": "Ownership transferred."})
