from django.db import models
from django.db.models import Expression

from .tenancy import get_current_tenant_id, tenant_bypass_active


class CurrentTenantId(Expression):
    """
    SQL parameter resolved at *compile time* from the tenant contextvar.
    This makes tenant filtering correct even for querysets that were built without a tenant in context
    (e.g. class-level `queryset = Model.objects.select_related(...)` on a ViewSet, evaluated at import).
    With no tenant in context the filter compiles to `tenant_id = NULL`, which matches nothing.
    """

    def __init__(self):
        super().__init__(output_field=models.UUIDField())

    def as_sql(self, compiler, connection):
        tid = get_current_tenant_id()
        if tid is None:
            return "NULL", []
        return "%s", [tid]

    def resolve_expression(self, *args, **kwargs):
        return self

    def get_group_by_cols(self):
        return []

    def __repr__(self):
        return "CurrentTenantId()"


class TenantQuerySet(models.QuerySet):
    def for_tenant(self, tenant):
        return self.filter(tenant=tenant)

    def alive(self):
        return self.filter(deleted_at__isnull=True)


class TenantManager(models.Manager):
    """Default manager: filters by the current tenant (resolved when the SQL runs) and hides soft-deleted rows."""

    def get_queryset(self):
        qs = TenantQuerySet(self.model, using=self._db).filter(deleted_at__isnull=True)
        if tenant_bypass_active():
            return qs
        return qs.filter(tenant_id=CurrentTenantId())


class AllObjectsManager(models.Manager):
    """Unfiltered manager (platform admin views, jobs, tests)."""

    def get_queryset(self):
        return TenantQuerySet(self.model, using=self._db)
