import re
from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from apps.core.audit import AuditService
from apps.core.exceptions import DomainError
from apps.core.models import Module
from apps.core.notifications import send_email, send_sms
from apps.core.plan_limits import PlanLimitService

from .models import GroupProfile, OneTimeCode, TenantMembership, User, UserSession, UserWarehouseAccess
from .permissions_catalog import CATALOG, DEFAULT_ROLES, PLATFORM_CATALOG


def ensure_permission_catalog():
    """Create Permission rows for every catalogue codename and every page switch (idempotent)."""
    from .page_permissions import ACTION_LABELS, PAGES

    ct = ContentType.objects.get_for_model(Module)
    for module, (label, perms) in {**CATALOG, **PLATFORM_CATALOG}.items():
        Module.objects.get_or_create(code=module, defaults={"name": label})
        for code, name in perms:
            Permission.objects.get_or_create(content_type=ct, codename=f"{module}.{code}", defaults={"name": name[:255]})
    # the per-page view/add/edit/delete switches (apps/accounts/page_permissions.py)
    for pg in PAGES:
        Module.objects.get_or_create(code=pg["module"], defaults={"name": pg["module"].title()})
        for action, codename in pg["actions"].items():
            Permission.objects.get_or_create(content_type=ct, codename=codename, defaults={"name": f"{ACTION_LABELS[action]} — {pg['label']}"[:255]})
        for extra in pg["extras"]:
            perms = extra["perm"] if isinstance(extra["perm"], list) else [extra["perm"]]
            for perm in perms:
                Permission.objects.get_or_create(content_type=ct, codename=perm, defaults={"name": f"{extra['label']} ({pg['label']})"[:255]})


def _perm_objects(codenames):
    """
    The Permission rows for these codenames — creating any that do not exist yet. Filtering alone silently
    dropped every switch whose row was missing (a page added after the catalog last ran), which read as
    "I turned Edit on, saved, and it came back off". Inputs are validated against the known catalog upstream,
    so materialising a row here is always legitimate.
    """
    ct = ContentType.objects.get_for_model(Module)
    have = set(Permission.objects.filter(content_type=ct, codename__in=codenames).values_list("codename", flat=True))
    for code in codenames:
        if code not in have:
            Permission.objects.get_or_create(content_type=ct, codename=code, defaults={"name": code[:255]})
    return Permission.objects.filter(content_type=ct, codename__in=codenames)


class RoleService:
    @staticmethod
    @transaction.atomic
    def create_role(tenant, name, permissions, role_type="internal", description="", is_system=False, approval_limits=None, full_access=False):
        if GroupProfile.objects.filter(tenant=tenant, name=name).exists():
            raise DomainError(f"A role named '{name}' already exists.")
        if tenant is not None and not is_system:
            PlanLimitService.check(tenant, "max_custom_roles")
        from .page_permissions import narrow

        permissions = narrow(permissions)
        group = Group.objects.create(name=f"{tenant.slug if tenant else 'platform'}:{name}"[:150])
        group.permissions.set(_perm_objects(permissions))
        profile = GroupProfile.objects.create(
            group=group, tenant=tenant, name=name, description=description, is_system=is_system, role_type=role_type, approval_limits=approval_limits or {},
            full_access=bool(full_access) and role_type == "internal",
        )
        AuditService.log("role.created", profile, after={"name": name, "permissions": list(permissions)}, tenant_id=tenant.id if tenant else None)
        return profile

    @staticmethod
    @transaction.atomic
    def update_role(profile, name=None, permissions=None, description=None, approval_limits=None, role_type=None, full_access=None):
        before = {"name": profile.name, "permissions": profile.permission_codenames, "full_access": profile.full_access}
        if name and name != profile.name:
            if profile.is_system:
                raise DomainError("System roles cannot be renamed.")
            profile.name = name
            profile.group.name = f"{profile.tenant.slug if profile.tenant else 'platform'}:{name}"[:150]
            profile.group.save(update_fields=["name"])
        if description is not None:
            profile.description = description
        if approval_limits is not None:
            profile.approval_limits = approval_limits
        if role_type and role_type != profile.role_type:
            if profile.group.memberships.exists():
                raise DomainError("Cannot change role type while users are assigned.")
            profile.role_type = role_type
        access_changed = full_access is not None and bool(full_access) != profile.full_access
        if full_access is not None:
            profile.full_access = bool(full_access) and profile.role_type == "internal"
        profile.save()
        if access_changed:
            # the rights moved as surely as a ticked box — every holder signs in again to pick them up (§2.5)
            for m in profile.group.memberships.all():
                SessionService.revoke_all(m.user, reason="role_changed")
        if permissions is not None:
            from .page_permissions import expand, narrow

            # an untick must survive the save: see narrow() for why a coarse codename has to be dropped
            permissions = narrow(permissions)

            # What matters is whether the role *grants* anything different, not whether the stored list of
            # codenames is spelled differently. The editor is seeded from the expanded set, so a role still
            # holding the old coarse codenames submits the expansion back — every internal role on this system
            # differs that way (Manager: 199 stored against 227 expanded). Comparing the raw lists therefore
            # made an untouched Save look like a real change and signed every holder out of their devices.
            changed = set(expand(before["permissions"])) != set(expand(permissions))
            profile.group.permissions.set(_perm_objects(permissions))
            # force logout on role change (§2.5), but only when the rights actually moved
            if changed:
                for m in profile.group.memberships.all():
                    SessionService.revoke_all(m.user, reason="role_changed")
        AuditService.log("role.updated", profile, before=before, after={"name": profile.name, "permissions": profile.permission_codenames, "full_access": profile.full_access})
        return profile

    @staticmethod
    @transaction.atomic
    def delete_role(profile):
        if profile.is_system:
            raise DomainError("System roles cannot be deleted.")
        if profile.group.memberships.exists():
            raise DomainError("Remove this role from all users before deleting it.")
        AuditService.log("role.deleted", profile, before={"name": profile.name})
        profile.group.delete()

    @staticmethod
    def seed_default_roles(tenant):
        ensure_permission_catalog()
        created = {}
        if not GroupProfile.objects.filter(tenant=tenant, name="Owner").exists():
            created["Owner"] = RoleService.create_role(tenant, "Owner", [], role_type="internal", description="Full access (system role)", is_system=True)
        for name, spec in DEFAULT_ROLES.items():
            if not GroupProfile.objects.filter(tenant=tenant, name=name).exists():
                created[name] = RoleService.create_role(tenant, name, spec["perms"], role_type=spec["type"], description="Default template", is_system=False)
        return created


class UserService:
    @staticmethod
    @transaction.atomic
    def create_tenant_user(tenant, full_name, email=None, phone=None, password=None, roles=(), warehouses=(), user_type="TENANT", send_invite=False, is_owner=False, supplier_rep=None, attach_existing=False):
        if not email and not phone:
            raise DomainError("Email or phone is required.")
        email = (email or "").strip().lower() or None
        phone = (phone or "").strip() or None
        # An email or a mobile number is how someone signs in, so it can only belong to one person. A second
        # account on the same address would make the login ambiguous, which is why it is refused here by name.
        taken = User.objects.filter(email__iexact=email).first() if email else None
        if taken is not None and not attach_existing:
            where = "in this company" if TenantMembership.objects.filter(user=taken, tenant=tenant).exists() else "on another account"
            raise DomainError(
                f"The email {email} is already used by {taken.full_name or taken.email} ({where}). Use a different address.",
                code="duplicate", errors={"email": [f"Already used by {taken.full_name or taken.email}."]}, status_code=400,
            )
        taken_phone = User.objects.filter(phone=phone).first() if phone else None
        if taken_phone is not None and not attach_existing:
            where = "in this company" if TenantMembership.objects.filter(user=taken_phone, tenant=tenant).exists() else "on another account"
            raise DomainError(
                f"The mobile number {phone} is already used by {taken_phone.full_name or phone} ({where}). Use a different number.",
                code="duplicate", errors={"phone": [f"Already used by {taken_phone.full_name or phone}."]}, status_code=400,
            )
        user = taken or taken_phone
        if user is None:
            if user_type != "EXTERNAL":
                PlanLimitService.check(tenant, "max_users")
            else:
                PlanLimitService.check(tenant, "max_reps")
            user = User(email=email or None, phone=phone or None, full_name=full_name, user_type=user_type)
            if password:
                user.set_password(password)
                user.must_change_password = True
            else:
                user.set_unusable_password()
            user.save()
        elif TenantMembership.objects.filter(user=user, tenant=tenant).exists():
            raise DomainError("This user is already a member of the company.")
        membership = TenantMembership.objects.create(user=user, tenant=tenant, is_owner=is_owner, status="active", supplier_rep=supplier_rep)
        if roles:
            membership.groups.set([r.group for r in roles])
        for wh in warehouses:
            UserWarehouseAccess.objects.create(membership=membership, warehouse=wh)
        if send_invite:
            UserService.send_invite(user, tenant)
        AuditService.log("user.created", membership, after={"user": str(user), "roles": [r.name for r in roles]}, tenant_id=tenant.id)
        return membership

    @staticmethod
    def send_invite(user, tenant):
        otp = OneTimeCode.issue(user, "invite", channel="email" if user.email else "sms", ttl_minutes=60 * 48)
        link = f"{settings.FRONTEND_BASE_URL}/accept-invite?identifier={user.email or user.phone}&code={otp.code}"
        text = f"You have been invited to {tenant.display_name}. Use code {otp.code} or open {link}"
        if user.email:
            send_email(user.email, f"Invitation to {tenant.display_name}", text)
        elif user.phone:
            send_sms(user.phone, text)
        return otp

    @staticmethod
    @transaction.atomic
    def update_membership(membership, full_name=None, email=None, phone=None, roles=None, warehouses=None, status=None):
        user = membership.user
        before = {"full_name": user.full_name, "roles": [g.name for g in membership.groups.all()], "status": membership.status}
        if full_name is not None:
            user.full_name = full_name
        if email is not None:
            email = (email or "").strip().lower() or None
            clash = User.objects.filter(email__iexact=email).exclude(pk=user.pk).first() if email else None
            if clash is not None:
                raise DomainError(
                    f"The email {email} is already used by {clash.full_name or clash.email}. Use a different address.",
                    code="duplicate", errors={"email": [f"Already used by {clash.full_name or clash.email}."]}, status_code=400,
                )
            user.email = email
        if phone is not None:
            phone = (phone or "").strip() or None
            clash = User.objects.filter(phone=phone).exclude(pk=user.pk).first() if phone else None
            if clash is not None:
                raise DomainError(
                    f"The mobile number {phone} is already used by {clash.full_name or phone}. Use a different number.",
                    code="duplicate", errors={"phone": [f"Already used by {clash.full_name or phone}."]}, status_code=400,
                )
            user.phone = phone
        user.save()
        if roles is not None:
            if membership.is_owner and not roles:
                pass
            membership.groups.set([r.group for r in roles])
            SessionService.revoke_all(user, reason="role_changed")
        if warehouses is not None:
            membership.warehouse_access.all().delete()
            for wh in warehouses:
                UserWarehouseAccess.objects.create(membership=membership, warehouse=wh)
        if status is not None and status != membership.status:
            if membership.is_owner:
                raise DomainError("The owner cannot be deactivated. Transfer ownership first.")
            membership.status = status
            if status == "suspended":
                SessionService.revoke_all(user, reason="deactivated")
        membership.save()
        AuditService.log("user.updated", membership, before=before, after={"full_name": user.full_name, "roles": [g.name for g in membership.groups.all()], "status": membership.status})
        return membership

    @staticmethod
    @transaction.atomic
    def transfer_ownership(tenant, from_membership, to_membership):
        if not from_membership.is_owner:
            raise DomainError("Only the owner can transfer ownership.")
        if to_membership.tenant_id != tenant.id or to_membership.user.user_type == "EXTERNAL":
            raise DomainError("Target must be an internal member of this company.")
        from_membership.is_owner = False
        to_membership.is_owner = True
        from_membership.save(update_fields=["is_owner"])
        to_membership.save(update_fields=["is_owner"])
        tenant.owner = to_membership.user
        tenant.save(update_fields=["owner"])
        AuditService.log("ownership.transferred", tenant, before={"owner": str(from_membership.user)}, after={"owner": str(to_membership.user)})

    @staticmethod
    def check_password_strength(password, user=None):
        """Django's validators plus the character mix an admin-set password must have."""
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError

        from apps.core.plan_limits import platform_setting

        minimum = int(platform_setting("password_min_length", 8) or 8)
        problems = []
        if len(password or "") < minimum:
            problems.append(f"at least {minimum} characters")
        if not re.search(r"[a-z]", password or ""):
            problems.append("a lowercase letter")
        if not re.search(r"[A-Z]", password or ""):
            problems.append("an uppercase letter")
        if not re.search(r"\d", password or ""):
            problems.append("a number")
        if problems:
            raise DomainError("The password needs " + ", ".join(problems) + ".", code="weak_password", status_code=400)
        try:
            validate_password(password, user)
        except DjangoValidationError as e:
            raise DomainError(" ".join(e.messages), code="weak_password", status_code=400) from e

    @staticmethod
    def reset_password(user, new_password, by_admin=False, validate=True):
        if validate:
            UserService.check_password_strength(new_password, user)
        user.set_password(new_password)
        user.must_change_password = by_admin
        user.failed_login_attempts = 0
        user.locked_until = None
        user.save()
        SessionService.revoke_all(user, reason="password_changed")
        AuditService.log("user.password_reset", user, after={"by_admin": by_admin})


class SessionService:
    @staticmethod
    def revoke_all(user, reason=""):
        for ot in OutstandingToken.objects.filter(user=user):
            BlacklistedToken.objects.get_or_create(token=ot)
        UserSession.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())
        AuditService.log("sessions.revoked_all", user, after={"reason": reason})

    @staticmethod
    def revoke(session: UserSession):
        ot = OutstandingToken.objects.filter(jti=session.refresh_jti).first()
        if ot:
            BlacklistedToken.objects.get_or_create(token=ot)
        session.revoked_at = timezone.now()
        session.save(update_fields=["revoked_at"])

    @staticmethod
    def rotate(session, new_jti):
        """Move a session on to a new refresh token, remembering the previous one for the grace window."""
        now = timezone.now()
        UserSession.objects.filter(pk=session.pk).update(prev_refresh_jti=session.refresh_jti, refresh_jti=new_jti, rotated_at=now, last_used_at=now)
        session.prev_refresh_jti, session.refresh_jti, session.rotated_at = session.refresh_jti, new_jti, now
        return session
