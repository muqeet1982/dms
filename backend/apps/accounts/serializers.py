from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.exceptions import DomainError

from .models import GroupProfile, TenantMembership, User, UserSession, UserWarehouseAccess
from .permissions_catalog import all_codenames, external_role_codenames


def find_user(identifier: str):
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    return User.objects.filter(Q(email__iexact=identifier) | Q(phone=identifier) | Q(username__iexact=identifier)).first()


class LoginSerializer(TokenObtainPairSerializer):
    """Login with email, phone or username + password. Returns tokens + memberships."""

    username_field = "identifier"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("username", None)
        self.fields[self.username_field] = serializers.CharField()
        self.fields["device_name"] = serializers.CharField(required=False, allow_blank=True)
        self.fields["platform"] = serializers.ChoiceField(choices=["web", "ios", "android"], required=False, default="web")

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["user_type"] = user.user_type
        token["name"] = user.full_name
        return token

    def validate(self, attrs):
        cfg = settings.DMS
        user = find_user(attrs.get("identifier"))
        if user is None:
            raise serializers.ValidationError({"identifier": ["No account matches these credentials."]})
        from apps.core.plan_limits import platform_setting

        cfg = {**cfg, "LOGIN_LOCKOUT_ATTEMPTS": platform_setting("login_lockout_attempts", cfg["LOGIN_LOCKOUT_ATTEMPTS"]), "LOGIN_LOCKOUT_MINUTES": platform_setting("login_lockout_minutes", cfg["LOGIN_LOCKOUT_MINUTES"])}
        if user.locked_until and user.locked_until > timezone.now():
            raise DomainError("Account temporarily locked after too many failed attempts. Try again later.", code="locked", status_code=423)
        auth_user = authenticate(request=self.context.get("request"), username=user.username, password=attrs.get("password"))
        if auth_user is None:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= cfg["LOGIN_LOCKOUT_ATTEMPTS"]:
                user.locked_until = timezone.now() + timezone.timedelta(minutes=cfg["LOGIN_LOCKOUT_MINUTES"])
                user.failed_login_attempts = 0
            user.save(update_fields=["failed_login_attempts", "locked_until"])
            raise serializers.ValidationError({"password": ["Incorrect password."]})
        if not user.is_active:
            raise DomainError("Account is inactive.", code="inactive", status_code=403)
        return open_session(user, self.context.get("request"), device_name=attrs.get("device_name"), platform=attrs.get("platform") or "web", context=self.context)


def open_session(user, request, *, device_name=None, platform="web", context=None):
    """Issue a refresh-token family + UserSession for `user` and build the login response (password login and QR login)."""
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_ip = getattr(request, "client_ip", None)
    user.last_login = timezone.now()
    user.save(update_fields=["failed_login_attempts", "locked_until", "last_login_ip", "last_login"])

    refresh = LoginSerializer.get_token(user)
    session = UserSession.objects.create(
        user=user,
        family=str(refresh["jti"]),
        refresh_jti=str(refresh["jti"]),
        device=device_name or (getattr(request, "user_agent", "") or "")[:200],
        ip=getattr(request, "client_ip", None),
        platform=platform or "web",
    )
    ctx = context if context is not None else {"request": request}
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": MeSerializer(user, context=ctx).data,
        "memberships": MembershipBriefSerializer(user.memberships.filter(status="active").select_related("tenant"), many=True).data,
        "must_change_password": user.must_change_password,
        "session_id": str(session.id),
    }


class MembershipBriefSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id")
    tenant_name = serializers.CharField(source="tenant.display_name")
    tenant_slug = serializers.CharField(source="tenant.slug")
    tenant_status = serializers.CharField(source="tenant.status")
    roles = serializers.SerializerMethodField()

    class Meta:
        model = TenantMembership
        fields = ["id", "tenant_id", "tenant_name", "tenant_slug", "tenant_status", "is_owner", "status", "roles"]

    def get_roles(self, obj):
        return [g.profile.name if hasattr(g, "profile") else g.name for g in obj.groups.all()]


class MeSerializer(serializers.ModelSerializer):
    is_platform_admin = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = ["id", "email", "phone", "full_name", "user_type", "is_platform_admin", "locale", "timezone", "avatar", "mfa_enabled", "must_change_password", "last_login"]
        read_only_fields = ["id", "email", "phone", "user_type", "must_change_password", "last_login"]


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(required=False, allow_blank=True)
    new_password = serializers.CharField()

    def validate_new_password(self, v):
        validate_password(v)
        return v


class OTPRequestSerializer(serializers.Serializer):
    identifier = serializers.CharField()
    purpose = serializers.ChoiceField(choices=["password_reset", "verify", "mfa"], default="password_reset")
    channel = serializers.ChoiceField(choices=["email", "sms"], default="email")


class OTPVerifySerializer(serializers.Serializer):
    identifier = serializers.CharField()
    purpose = serializers.ChoiceField(choices=["password_reset", "verify", "mfa", "invite"], default="password_reset")
    code = serializers.CharField(max_length=8)


class PasswordResetSerializer(OTPVerifySerializer):
    new_password = serializers.CharField()

    def validate_new_password(self, v):
        validate_password(v)
        return v


class SessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserSession
        fields = ["id", "device", "ip", "platform", "created_at", "last_used_at", "revoked_at"]


# --- tenant users & roles -------------------------------------------------------------------


class RoleSerializer(serializers.ModelSerializer):
    permissions = serializers.ListField(child=serializers.CharField(), required=False)
    permissions_effective = serializers.SerializerMethodField()
    users_count = serializers.SerializerMethodField()

    class Meta:
        model = GroupProfile
        fields = ["id", "name", "description", "is_system", "role_type", "approval_limits", "permissions", "permissions_effective", "users_count", "created_at"]
        read_only_fields = ["id", "is_system", "created_at", "permissions_effective"]

    def get_users_count(self, obj):
        return obj.group.memberships.count()

    def get_permissions_effective(self, obj):
        from .page_permissions import expand

        return sorted(expand(obj.permission_codenames))

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["permissions"] = instance.permission_codenames
        # `permissions_effective` expands the old coarse codenames, so the editor shows what the role really grants
        # and saving it quietly converts the role to the page switches without changing what it can do.
        return data

    def validate_permissions(self, perms):
        from .page_permissions import all_page_codenames

        valid = set(all_codenames(include_external=True)) | set(all_page_codenames())
        bad = [p for p in perms if p not in valid]
        if bad:
            raise serializers.ValidationError(f"Unknown permissions: {', '.join(bad)}")
        return perms

    def validate(self, attrs):
        role_type = attrs.get("role_type") or (self.instance.role_type if self.instance else "internal")
        perms = attrs.get("permissions")
        if perms is not None:
            if role_type == "external":
                # The catalogue decides, not the `external.` prefix: a rep role legitimately holds the rep slice
                # of visits/tracking, so a prefix test refused every seeded rep role on an unchanged save.
                refused = sorted(set(perms) - external_role_codenames())
                if refused:
                    raise serializers.ValidationError({"permissions": [f"A rep role cannot be given these permissions: {', '.join(refused)}."]})
            if role_type == "internal" and any(p.startswith("external.") for p in perms):
                raise serializers.ValidationError({"permissions": ["Internal roles cannot contain external.* permissions."]})
        return attrs


class TenantUserSerializer(serializers.ModelSerializer):
    """A user as seen inside a tenant (membership + user fields)."""

    user_id = serializers.UUIDField(source="user.id", read_only=True)
    email = serializers.EmailField(source="user.email", required=False, allow_null=True)
    phone = serializers.CharField(source="user.phone", required=False, allow_null=True, allow_blank=True)
    full_name = serializers.CharField(source="user.full_name")
    is_active = serializers.BooleanField(source="user.is_active", read_only=True)
    user_type = serializers.CharField(source="user.user_type", read_only=True)
    last_login = serializers.DateTimeField(source="user.last_login", read_only=True)
    roles = serializers.PrimaryKeyRelatedField(queryset=GroupProfile.objects.none(), many=True, required=False, source="role_profiles")
    role_names = serializers.SerializerMethodField()
    warehouses = serializers.ListField(child=serializers.UUIDField(), required=False)
    password = serializers.CharField(write_only=True, required=False)
    send_invite = serializers.BooleanField(write_only=True, required=False, default=False)
    # A field role only means something attached to a rep record: the rep app resolves which customers, areas
    # and targets belong to the caller from it. Either point at an existing rep, or name a supplier and one is
    # created for this person — so giving somebody field access stays a single form.
    rep_for_supplier = serializers.UUIDField(write_only=True, required=False, allow_null=True)
    rep_name = serializers.SerializerMethodField()

    class Meta:
        model = TenantMembership
        fields = ["id", "user_id", "email", "phone", "full_name", "is_active", "user_type", "is_owner", "status", "roles", "role_names", "warehouses", "last_login", "joined_at", "password", "send_invite", "supplier_rep", "rep_for_supplier", "rep_name"]
        read_only_fields = ["id", "is_owner", "joined_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None and getattr(request, "tenant", None) is not None:
            self.fields["roles"].child_relation.queryset = GroupProfile.objects.filter(tenant=request.tenant)

    def get_role_names(self, obj):
        return [g.profile.name for g in obj.groups.select_related("profile")]

    def get_rep_name(self, obj):
        rep = obj.supplier_rep
        return f"{rep.code} · {rep.name} ({rep.supplier.name})" if rep else ""

    def validate(self, attrs):
        """
        A login with no role can sign in and open nothing, which reads to the person as a broken account rather
        than as a missing right. So a role is part of creating one, and the last role cannot be taken off an
        existing user either — remove their access by suspending the login, which says what it means.

        The owner is exempt: ownership is not a role and carries everything by itself.
        """
        attrs = super().validate(attrs)
        if "role_profiles" in attrs and not attrs["role_profiles"] and not (self.instance and self.instance.is_owner):
            where = "Pick one before saving." if self.instance else "Pick one before creating the user."
            raise serializers.ValidationError({"roles": [f"Give the user at least one role — a login with no role opens nothing. {where}"]})
        if self.instance is None and "role_profiles" not in attrs:
            raise serializers.ValidationError({"roles": ["Give the user at least one role. If the Roles section is not shown to you, you need the users page's “manage access” right."]})
        return attrs

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["roles"] = [str(g.profile.id) for g in instance.groups.select_related("profile")]
        data["warehouses"] = [str(w.warehouse_id) for w in instance.warehouse_access.all()]
        return data


class TransferOwnershipSerializer(serializers.Serializer):
    membership_id = serializers.UUIDField()


class ImpersonateSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True)
    minutes = serializers.IntegerField(required=False, min_value=5, max_value=480)
