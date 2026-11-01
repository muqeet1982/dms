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
                # An owner can "log in as" one of their own users to see the app with that user's rights. It
                # reuses the impersonation session (admin = owner, target = user) and swaps the effective
                # membership, exactly as a platform admin impersonating the owner does.
                if membership.is_owner:
                    imp = (
                        ImpersonationSession.objects.filter(admin=user, tenant=tenant, ended_at__isnull=True, expires_at__gt=timezone.now())
                        .select_related("target")
                        .first()
                    )
                    if imp is not None:
                        target_m = TenantMembership.objects.filter(user=imp.target, tenant=tenant, status="active").first()
                        if target_m is not None and not target_m.is_owner:
                            request.membership = target_m
                            request.impersonating = True
                            request.impersonation = _keep_impersonation_alive(imp)
        if tenant is not None:
            if tenant.status in ("cancelled", "archived") and not user.is_platform_admin:
                raise exceptions.PermissionDenied("This company account is closed.")
            if tenant.status in ("trial", "active", "past_due") and not user.is_platform_admin:
                _lapsed_now(tenant)   # past its grace days today, even if the scheduled check has not run yet
            if tenant.status == "suspended" and not user.is_platform_admin:
                _suspended_gate(request, user, tenant)
            # a rep's own login is the plan's "Rep portal" feature (Features & white-label)
            m = getattr(request, "membership", None)
            if m is not None and m.supplier_rep_id and not user.is_platform_admin and not getattr(request, "impersonating", False):
                from apps.core.plan_limits import effective_features

                if not effective_features(tenant).get("rep_portal"):
                    from apps.core.exceptions import DomainError

                    raise DomainError(f"Rep logins are not part of {tenant.display_name}'s plan.", code="feature_disabled", status_code=403)
            request.tenant = tenant
            set_current_tenant(tenant)
        return user, token


# A suspended company (owner, 1 Nov 2026: "tenant suspended all user logins"): every user may still ask who they
# are and sign out, so the app can say why it stopped; the owner may also reach the payment screen — the
# subscription, paying, the platform's announcements and support — to pay and reactivate it. Nothing else.
SUSPENDED_OPEN = ("/api/v1/me", "/api/v1/auth/")
SUSPENDED_OWNER = SUSPENDED_OPEN + ("/api/v1/subscription", "/api/v1/billing", "/api/v1/announcements", "/api/v1/support-tickets")


def _lapsed_now(tenant):
    from datetime import timedelta

    sub = tenant.active_subscription
    if sub is not None and sub.auto_status_change and sub.end_date + timedelta(days=sub.grace_days) < timezone.localdate():
        from apps.platform.services import SubscriptionService

        SubscriptionService.apply_status(sub)
        tenant.status = sub.tenant.status


def _suspended_gate(request, user, tenant):
    from apps.core.exceptions import DomainError

    from .models import TenantMembership

    owner = TenantMembership.objects.filter(user=user, tenant=tenant, is_owner=True, status="active").exists()
    if request.path.startswith(SUSPENDED_OWNER if owner else SUSPENDED_OPEN):
        return
    if owner:
        msg = f"{tenant.display_name} is suspended for non-payment. Open Subscription to pay and reactivate it."
    else:
        who = tenant.owner.full_name if tenant.owner_id and tenant.owner.full_name else "your company's owner"
        msg = f"{tenant.display_name} is suspended for non-payment. Ask {who} to renew the subscription."
    raise DomainError(msg, code="tenant_suspended", status_code=403)
