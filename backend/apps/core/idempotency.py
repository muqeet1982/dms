from rest_framework.response import Response

from .models import IdempotencyKey
from .tenancy import get_current_tenant_id


class IdempotentCreateMixin:
    """
    If the request carries `Idempotency-Key`, replay the stored response for the same
    (tenant, user, key, path) instead of creating a second document.
    """

    def create(self, request, *args, **kwargs):
        key = request.headers.get("Idempotency-Key")
        if not key:
            return super().create(request, *args, **kwargs)
        tenant_id = get_current_tenant_id()
        user = request.user if request.user.is_authenticated else None
        existing = IdempotencyKey.objects.filter(tenant_id=tenant_id, user=user, key=key, path=request.path).first()
        if existing:
            return Response(existing.response_body, status=existing.response_status, headers={"Idempotent-Replayed": "true"})
        response = super().create(request, *args, **kwargs)
        if 200 <= response.status_code < 300:
            IdempotencyKey.objects.create(
                tenant_id=tenant_id, user=user, key=key, path=request.path, response_status=response.status_code, response_body=response.data
            )
        return response
