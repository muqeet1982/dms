"""
DRF permission classes for the tenant permission catalogue.

Usage on a ViewSet:
    permission_classes = [IsTenantUser]
    perm_map = {"list": "sales.view_sales_order", "create": "sales.add_sales_order", ...}
Or per-action: `required_perm = "catalog.view_product"`.
"""
from rest_framework.permissions import BasePermission


class IsPlatformAdmin(BasePermission):
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and getattr(u, "is_platform_admin", False))


class IsTenantUser(BasePermission):
    """Authenticated user with a resolved tenant membership (or an API key bound to a tenant)."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if getattr(request, "tenant", None) is None:
            return False
        required = None
        perm_map = getattr(view, "perm_map", None)
        if perm_map:
            required = perm_map.get(getattr(view, "action", None) or request.method.lower())
        if required is None:
            required = getattr(view, "required_perm", None)
        if required is None:
            return True
        if isinstance(required, str):
            required = [required]
        return any(has_tenant_perm(request, p) for p in required)


def has_tenant_perm(request, codename: str) -> bool:
    user = request.user
    if getattr(user, "is_platform_admin", False) and getattr(request, "impersonating", False):
        return True
    membership = getattr(request, "membership", None)
    if membership is not None:
        return membership.has_perm(codename)
    api_key = getattr(request, "api_key", None)
    if api_key is not None:
        return api_key.has_scope(codename)
    return False


class HasPerm(BasePermission):
    """Factory: HasPerm("sales.confirm_sales_order")."""

    def __init__(self, codename):
        self.codename = codename

    def __call__(self):
        return self

    def has_permission(self, request, view):
        return has_tenant_perm(request, self.codename)


def require_perm(codename):
    """Decorator for @action methods: checks the codename and raises 403."""
    from functools import wraps

    from rest_framework.exceptions import PermissionDenied

    def deco(fn):
        @wraps(fn)
        def wrapper(self, request, *args, **kwargs):
            if not has_tenant_perm(request, codename):
                raise PermissionDenied(f"Missing permission: {codename}")
            return fn(self, request, *args, **kwargs)

        return wrapper

    return deco
