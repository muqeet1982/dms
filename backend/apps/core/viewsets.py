"""Base viewsets shared by every tenant module."""
from rest_framework import viewsets
from rest_framework.decorators import action

from .audit import AuditService, snapshot
from .exceptions import ConflictError, DomainError
from .idempotency import IdempotentCreateMixin
from .models import DocumentModel
from .permissions import IsTenantUser


def _flatten(value):
    """A cell holds one value. A list of references reads as a list; a spreadsheet cannot hold one."""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value if v is not None)
    if isinstance(value, dict):
        return ", ".join(f"{k}: {v}" for k, v in value.items())
    return value


def _printable(record):
    """
    One serialised row, reduced to what a person would want in a spreadsheet.

    Ids, versions and anything nested go: a column of UUIDs helps nobody, and the readable version of a link is
    already there beside it as `customer_name`. What is left keeps the serializer's order, which is the order the
    screen shows.
    """
    skip_exact = {"id", "version", "tenant", "created_by", "updated_by", "deleted_at", "lines", "attachments"}
    out = {}
    for key, value in record.items():
        if key in skip_exact or key.endswith("_id") or isinstance(value, (dict, list)):
            continue
        if isinstance(value, str) and len(value) == 36 and value.count("-") == 4:
            continue  # a UUID that happens to be spelled out as a field of its own
        out[key.replace("_", " ").strip().title()] = value
    return out


class ExportableMixin:
    """
    `GET <list endpoint>/export?format=csv|xlsx|pdf` — this list as a file.

    The filters, the search and the ordering are taken from the request exactly as the screen sent them, so the
    download and the page agree by construction; a file that quietly differs from what was on screen is worse
    than no file. Pagination is deliberately ignored — nobody wants page 2 of their export.
    """

    EXPORT_LIMIT = 5000

    @action(detail=False, methods=["get"])
    def export(self, request):
        from django.http import HttpResponse

        from apps.reports.exporters import to_csv, to_pdf, to_xlsx

        qs = self.filter_queryset(self.get_queryset())[: self.EXPORT_LIMIT]
        data = self.get_serializer(qs, many=True).data
        # A viewset that says which columns it wants gets those, in that order. Without one the serializer's
        # fields are used as they come, minus the plumbing — fine for a settings table, noisy for a document.
        picked = getattr(self, "export_fields", None)
        if picked:
            named = getattr(self, "export_labels", {})
            rows = [{named.get(k) or k.replace("_", " ").strip().title(): _flatten(r.get(k)) for k in picked} for r in data]
        else:
            rows = [_printable(r) for r in data]
        title = request.query_params.get("title") or self.__class__.__name__.replace("ViewSet", "")
        # a named column keeps its own casing ("GST %", not the exporter's "Gst %")
        result = {"columns": list(rows[0].keys()) if rows else [], "rows": rows,
                  "column_labels": {v: v for v in getattr(self, "export_labels", {}).values()}}
        fmt = request.query_params.get("format", "csv")
        if fmt == "xlsx":
            from apps.core.plan_limits import effective_features

            if not effective_features(request.tenant).get("excel_export"):
                raise DomainError("Excel export is not enabled for your plan.", status_code=403)
            data, ct, ext = to_xlsx(result, title), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
        elif fmt == "pdf":
            data, ct, ext = to_pdf(result, title, getattr(request, "tenant", None), {}), "application/pdf", "pdf"
        else:
            data, ct, ext = to_csv(result, title), "text/csv", "csv"
        resp = HttpResponse(data, content_type=ct)
        name = title.lower().replace(" ", "-")
        resp["Content-Disposition"] = f'{"inline" if fmt == "pdf" else "attachment"}; filename="{name}.{ext}"'
        return resp


class TenantModelViewSet(ExportableMixin, viewsets.ModelViewSet):
    """
    - tenant-scoped queryset via the default manager
    - `perm_map` per action (falls back to `required_perm`)
    - audit log on create / update / delete
    - soft delete
    - optimistic locking for DocumentModel subclasses (PATCH must send `version`)
    """

    permission_classes = [IsTenantUser]
    audit_fields = None
    hard_delete = False

    def get_queryset(self):
        return self.queryset.all() if self.queryset is not None else self.serializer_class.Meta.model.objects.all()

    def get_object(self):
        obj = super().get_object()
        # Mutating actions run services that change related rows; drop prefetch caches so `obj.lines.all()` etc. are fresh.
        if self.request.method not in ("GET", "HEAD", "OPTIONS") and hasattr(obj, "_prefetched_objects_cache"):
            obj._prefetched_objects_cache = {}
        return obj

    def perform_create(self, serializer):
        instance = serializer.save()
        AuditService.log("created", instance, after=snapshot(instance, self.audit_fields), request=self.request)

    def perform_update(self, serializer):
        instance = serializer.instance
        if isinstance(instance, DocumentModel):
            sent = self.request.data.get("version")
            if sent is not None and int(sent) != instance.version:
                raise ConflictError()
            instance.bump_version()
        before = snapshot(instance, self.audit_fields)
        instance = serializer.save()
        AuditService.log("updated", instance, before=before, after=snapshot(instance, self.audit_fields), request=self.request)

    def perform_destroy(self, instance):
        AuditService.log("deleted", instance, before=snapshot(instance, self.audit_fields), request=self.request)
        if self.hard_delete or not hasattr(instance, "soft_delete"):
            instance.delete()
        else:
            instance.soft_delete()



class DocumentViewSet(IdempotentCreateMixin, TenantModelViewSet):
    """Documents: idempotent create + everything above."""

    pass


def status_action(instance, action_name, before_status, after_status, request=None, extra=None):
    AuditService.log(f"status.{action_name}", instance, before={"status": before_status}, after={"status": after_status, **(extra or {})}, request=request)


def warehouse_scope(request, qs, field="warehouse"):
    """Restrict a queryset to the caller's warehouses when UserWarehouseAccess rows exist (§2.3)."""
    membership = getattr(request, "membership", None)
    if membership is None or membership.is_owner:
        return qs
    ids = list(membership.warehouse_access.values_list("warehouse_id", flat=True))
    if not ids:
        return qs
    return qs.filter(**{f"{field}__in": ids})
