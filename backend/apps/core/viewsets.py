"""Base viewsets shared by every tenant module."""
import uuid

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


def _uuid_like(v):
    return isinstance(v, uuid.UUID) or (isinstance(v, str) and len(v) == 36 and v.count("-") == 4)


def _is_plumbing(v):
    return isinstance(v, dict) or _uuid_like(v) or (isinstance(v, (list, tuple)) and any(not isinstance(x, str) or _uuid_like(x) for x in v))


def _printable_keys(records):
    """
    The fields of a list's rows a person would want in a spreadsheet, judged on its first rows.

    Ids, versions and anything nested go: a column of UUIDs helps nobody, and the readable version of a link is
    already there beside it as `customer_name`. What is left keeps the serializer's order, which is the order the
    screen shows.
    """
    skip_exact = {"id", "version", "tenant", "created_by", "updated_by", "deleted_at", "lines", "attachments"}

    def kind(value):
        # a link's id (posted_by, period) — its readable name is its own column; a UUID spelled out as a field of its own;
        # a list of names or numbers reads as one cell (rep_names, grn_numbers), a list of ids or records does not
        if _is_plumbing(value):
            return "plumbing"
        return "empty" if isinstance(value, (list, tuple)) and not value else "ok"

    sample = records[:50]
    keep = []
    for key in sample[0] if sample else {}:
        if key in skip_exact or key.endswith("_id"):
            continue
        kinds = {kind(r.get(key)) for r in sample}
        if "plumbing" not in kinds and kinds != {"empty"}:
            keep.append(key)
    return keep


class ExportableMixin:
    """
    `GET <list endpoint>/export?format=csv|xlsx|pdf` — this list as a file.

    The filters, the search and the ordering are taken from the request exactly as the screen sent them, so the
    download and the page agree by construction; a file that quietly differs from what was on screen is worse
    than no file. Pagination is deliberately ignored — nobody wants page 2 of their export.
    """

    EXPORT_LIMIT = 5000

    def export_columns(self, data):
        """The file's columns as (field, heading): the viewset's own pick, else the serializer's fields minus the plumbing."""
        picked = getattr(self, "export_fields", None)
        named = getattr(self, "export_labels", {})
        keys = list(picked) if picked else _printable_keys(data)
        return [(k, named.get(k) or k.replace("_", " ").strip().title()) for k in keys]

    @action(detail=False, methods=["get"])
    def export(self, request):
        return self.export_file(request, self.filter_queryset(self.get_queryset()))

    def export_file(self, request, qs):
        from django.http import HttpResponse, JsonResponse

        from apps.reports.exporters import to_csv, to_pdf, to_xlsx

        fmt = request.query_params.get("format", "csv")
        # the list's Columns switch names the file's columns too (owner, 27 Nov 2026: column selection "for all table and
        # pdf or print or csv excel"): a few rows are enough to know them
        data = self.get_serializer(qs[:50] if fmt == "columns" else qs[: self.EXPORT_LIMIT], many=True).data
        columns = self.export_columns(data)
        if fmt == "columns":
            return JsonResponse({"columns": [{"key": k, "label": label} for k, label in columns]})
        hide = {c for c in request.query_params.get("hide_columns", "").split(",") if c}
        columns = [(k, label) for k, label in columns if k not in hide]
        if hide and not columns:
            raise DomainError("Keep at least one column.", status_code=400, code="no_columns")
        # a column chosen from a sample: a row further down holding an id there gets a blank, never the id
        clean = (lambda v: v) if getattr(self, "export_fields", None) else (lambda v: "" if _is_plumbing(v) else v)
        rows = [{k: _flatten(clean(r.get(k))) for k, _ in columns} for r in data]
        title = request.query_params.get("title") or self.__class__.__name__.replace("ViewSet", "")
        # each column keeps its own heading ("GST %", not the exporter's "Gst %")
        result = {"columns": [k for k, _ in columns], "rows": rows, "column_labels": dict(columns)}
        if fmt == "xlsx":
            from apps.core.plan_limits import effective_features

            # a company's plan decides; the platform's own lists have no company behind them
            if getattr(request, "tenant", None) is not None and not effective_features(request.tenant).get("excel_export"):
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
