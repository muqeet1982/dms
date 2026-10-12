from .tenancy import _current_tenant, _current_user


class TenantContextMiddleware:
    """Guarantees the tenant/user contextvars are cleared when the response is done."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        t1 = _current_tenant.set(None)
        t2 = _current_user.set(None)
        try:
            return self.get_response(request)
        finally:
            _current_tenant.reset(t1)
            _current_user.reset(t2)


class RequestMetaMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        request.client_ip = xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")
        request.user_agent = request.META.get("HTTP_USER_AGENT", "")[:300]
        return self.get_response(request)
