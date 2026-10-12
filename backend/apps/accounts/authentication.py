"""
JWT authentication + tenant resolution.

After the JWT is validated, the tenant is resolved from the `X-Tenant-ID` header (or the
single membership if the user has only one) and the membership is verified. Platform admins
may act inside any tenant only through an active impersonation session (§3.2), which slides
forward while the admin keeps working (up to a hard cap) so nobody is thrown out mid-task.
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import exceptions
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.core.tenancy import set_current_tenant, set_current_user

from .models import ImpersonationSession, TenantMembership


def _keep_impersonation_alive(imp):
    """
    Slide `expires_at` forward while the admin is active, capped at `impersonation_max_hours` from the start.
    Without this the admin is kicked out of the company the moment the window ends, in the middle of a form.
    """
    from apps.core.plan_limits import platform_setting

    window = timedelta(minutes=int(platform_setting("impersonation_minutes", settings.DMS["IMPERSONATION_MINUTES"]) or 60))
    cap = imp.started_at + timedelta(hours=int(platform_setting("impersonation_max_hours", 8) or 8))
    now = timezone.now()
    if imp.expires_at - now >= window / 2:
        return imp
    new_expiry = min(now + window, cap)
    if new_expiry > imp.expires_at + timedelta(seconds=60):
        ImpersonationSession.objects.filter(pk=imp.pk).update(expires_at=new_expiry)
        imp.expires_at = new_expiry
    return imp


class TenantJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, token = result
        if not user.is_active:
            raise exceptions.AuthenticationFailed("Account is inactive.")
        set_current_user(user)
        request.tenant = None
        request.membership = None
        request.impersonating = False
        request.api_key = None

        header_tid = request.headers.get("X-Tenant-ID") or token.get("tenant_id")
        tenant = None
        if user.is_platform_admin:
            if header_tid:
                imp = (
                    ImpersonationSession.objects.filter(admin=user, tenant_id=header_tid, ended_at__isnull=True, expires_at__gt=timezone.now())
                    .select_related("tenant", "target")
                    .first()
                )
                if imp is None:
                    from apps.core.exceptions import DomainError

                    # Machine-readable so the app can drop the stale company and carry on instead of looking logged out.
                    raise DomainError(
                        "Your session inside this company has ended. Open the company again from Tenants to continue.",
                        code="impersonation_expired",
                        status_code=403,
                    )
                imp = _keep_impersonation_alive(imp)
                tenant = imp.tenant
                request.impersonating = True
                request.impersonation = imp
                membership = TenantMembership.objects.filter(user=imp.target, tenant=tenant).first()
                request.membership = membership
        else:
            memberships = TenantMembership.objects.filter(user=user, status__in=["active", "suspended"]).select_related("tenant")
            membership = None
            if header_tid:
                membership = next((m for m in memberships if str(m.tenant_id) == str(header_tid)), None)
                if membership is None:
                    from apps.core.exceptions import DomainError

                    raise DomainError("You are not a member of this company.", code="tenant_not_member", status_code=403)
            elif memberships.count() == 1:
                membership = memberships.first()
            if membership is not None:
                if membership.status != "active":
                    raise exceptions.PermissionDenied("Your membership in this tenant is suspended.")
                tenant = membership.tenant
                request.membership = membership
        if tenant is not None:
            if tenant.status in ("cancelled", "archived") and not user.is_platform_admin:
                raise exceptions.PermissionDenied("This company account is closed.")
            request.tenant = tenant
            set_current_tenant(tenant)
        return user, token
