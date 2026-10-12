"""
Tenant context.

`current_tenant` is a contextvar set per request by the authentication layer
(see apps.accounts.authentication) and by `tenant_context()` in background jobs.
`TenantManager` filters every query by it.
"""
import contextvars
from contextlib import contextmanager

_current_tenant = contextvars.ContextVar("current_tenant", default=None)
_current_user = contextvars.ContextVar("current_user", default=None)
_bypass = contextvars.ContextVar("tenant_bypass", default=False)


def get_current_tenant():
    return _current_tenant.get()


def get_current_tenant_id():
    t = _current_tenant.get()
    return t.id if t is not None else None


def set_current_tenant(tenant):
    return _current_tenant.set(tenant)


def reset_current_tenant(token):
    _current_tenant.reset(token)


def get_current_user():
    return _current_user.get()


def set_current_user(user):
    return _current_user.set(user)


def reset_current_user(token):
    _current_user.reset(token)


def tenant_bypass_active():
    return _bypass.get()


@contextmanager
def tenant_context(tenant, user=None):
    t1 = _current_tenant.set(tenant)
    t2 = _current_user.set(user)
    try:
        yield
    finally:
        _current_tenant.reset(t1)
        _current_user.reset(t2)


@contextmanager
def bypass_tenant():
    """Run a block without tenant filtering (platform jobs, seeding)."""
    token = _bypass.set(True)
    try:
        yield
    finally:
        _bypass.reset(token)
