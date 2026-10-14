from decimal import Decimal

from django.db.models import F, Q, Sum
from django.utils import timezone
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import AuditService
from apps.core.exceptions import DomainError
from apps.core.pagination import LedgerCursorPagination
from apps.accounts.page_permissions import extra_perm, page_perms
from apps.core.permissions import IsTenantUser, require_perm
from apps.core.viewsets import DocumentViewSet, TenantModelViewSet, warehouse_scope

from .models import Batch, Bin, InventoryAlert, ReasonCode, StockAdjustment, StockCount, StockLevel, StockMovement, StockTransfer, Warehouse
from .serializers import (
    BatchSerializer, BinSerializer, InventoryAlertSerializer, ReasonCodeSerializer, StockAdjustmentSerializer, StockCountSerializer, StockLevelSerializer,
    StockMovementSerializer, StockTransferSerializer, WarehouseSerializer,
)
from .services import AdjustmentService, AlertService, CountService, StockService, TransferService



class WarehouseViewSet(TenantModelViewSet):
    serializer_class = WarehouseSerializer
    perm_map = page_perms("settings_warehouses", {"set_default": "settings.change_warehouse", "bins": "settings.view_bin"})
    pagination_class = None
    filterset_fields = ["type", "is_active"]
    search_fields = ["name", "code"]

    def get_queryset(self):
        """Default first, then by name — forms preselect the first row."""
        return warehouse_scope(self.request, Warehouse.objects.prefetch_related("bins").select_related("manager", "vehicle").order_by("-is_default", "name"), field="id")

    @staticmethod
    def _only_one_default(warehouse):
        if warehouse.is_default:
            Warehouse.objects.exclude(pk=warehouse.pk).filter(is_default=True).update(is_default=False)

    @action(detail=True, methods=["post"], url_path="set-default")
    def set_default(self, request, pk=None):
        """Make this the warehouse every new document starts with."""
        warehouse = self.get_object()
        warehouse.is_default = True
        warehouse.save(update_fields=["is_default"])
        self._only_one_default(warehouse)
        AuditService.log("warehouse.default", warehouse, after={"code": warehouse.code}, request=request)
        return Response(self.get_serializer(warehouse).data)

    def perform_create(self, serializer):
        from apps.core.plan_limits import PlanLimitService

        PlanLimitService.check(self.request.tenant, "max_warehouses")
        first = not Warehouse.objects.exists()  # the very first warehouse a company creates becomes its default
        super().perform_create(serializer)
        if first and not serializer.instance.is_default:
            serializer.instance.is_default = True
            serializer.instance.save(update_fields=["is_default"])
        self._only_one_default(serializer.instance)

    def perform_update(self, serializer):
        super().perform_update(serializer)
        self._only_one_default(serializer.instance)

    def perform_destroy(self, instance):
        if StockLevel.objects.filter(warehouse=instance, qty_on_hand__gt=0).exists():
            raise DomainError("Warehouse still holds stock.")
        instance.is_active = False
        instance.save(update_fields=["is_active"])

    @action(detail=True, methods=["post"])
    @require_perm("settings.add_bin")
    def bins(self, request, pk=None):
        s = BinSerializer(data={**request.data, "warehouse": pk})
        s.is_valid(raise_exception=True)
        s.save()
        return Response(s.data, status=201)


class BinViewSet(TenantModelViewSet):
    """Storage locations (rack / shelf / bin) inside a warehouse — products point at one as their default location."""

    serializer_class = BinSerializer
    perm_map = page_perms("settings_locations")
    pagination_class = None
    search_fields = ["code", "name", "warehouse__name", "warehouse__code"]
    ordering_fields = ["code", "warehouse__code"]
    filterset_fields = ["warehouse"]

    def get_queryset(self):
        from django.db.models import Count

        return Bin.objects.select_related("warehouse").annotate(products_count=Count("products", distinct=True)).order_by("warehouse__code", "code")

    def perform_destroy(self, instance):
        if instance.stock_levels.filter(qty_on_hand__gt=0).exists():
            raise DomainError("This location still holds stock — move it out (transfer or adjustment) before deleting.")
        instance.products.update(location=None)
        super().perform_destroy(instance)


class ReasonCodeViewSet(TenantModelViewSet):
    serializer_class = ReasonCodeSerializer
    perm_map = page_perms("settings_reason_codes")
    pagination_class = None
    queryset = ReasonCode.objects.select_related("expense_account")
    filterset_fields = ["category", "is_active"]
    search_fields = ["code", "name"]

    def get_queryset(self):
        """`?used_for=purchase_return` narrows the list to reasons that apply to that document."""
        qs = super().get_queryset()
        used_for = self.request.query_params.get("used_for")
        if used_for:
            qs = qs.filter(used_for__contains=[used_for])
        return qs


class StockLevelViewSet(viewsets.ReadOnlyModelViewSet):
    """Stock by warehouse/bin/batch. ?group=variant collapses batches."""

    permission_classes = [IsTenantUser]
    serializer_class = StockLevelSerializer
    perm_map = page_perms("stock", {"summary": "inventory.view_stock", "lookup": "inventory.view_stock", "fefo": "inventory.view_stock"})
    filterset_fields = ["warehouse", "variant", "batch", "bin", "variant__product", "variant__product__brand", "variant__product__category", "variant__product__supplier"]
    search_fields = ["variant__name", "variant__sku", "batch__batch_no", "variant__barcodes__code"]
    ordering_fields = ["qty_on_hand", "variant__name", "updated_at"]

    def get_queryset(self):
        qs = StockLevel.objects.select_related("warehouse", "bin", "variant", "variant__product", "variant__product__brand", "variant__product__category", "variant__product__base_uom", "batch")
        qs = warehouse_scope(self.request, qs)
        if self.request.query_params.get("nonzero", "1") == "1":
            qs = qs.filter(Q(qty_on_hand__gt=0) | Q(qty_reserved__gt=0) | Q(qty_on_hand__lt=0))
        if self.request.query_params.get("low_stock") == "1":
            qs = qs.filter(qty_on_hand__lt=F("variant__product__min_stock"))
        return qs.distinct()

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Three buckets per variant: in stock, dispatched (on trip, sold), delivered (§7.1)."""
        from apps.sales.models import SalesOrderLine

        qs = warehouse_scope(request, StockLevel.objects.all())
        if request.query_params.get("warehouse"):
            qs = qs.filter(warehouse_id=request.query_params["warehouse"])
        rows = {}
        for r in qs.values("variant_id", "variant__sku", "variant__name", "variant__product__base_uom__code").annotate(on_hand=Sum("qty_on_hand"), reserved=Sum("qty_reserved")):
            rows[str(r["variant_id"])] = {"variant_id": str(r["variant_id"]), "sku": r["variant__sku"], "name": r["variant__name"], "uom": r["variant__product__base_uom__code"], "in_stock": str(r["on_hand"]), "reserved": str(r["reserved"]), "dispatched": "0", "delivered": "0"}
        date_from = request.query_params.get("date_from")
        ol = SalesOrderLine.objects.filter(order__status__in=["dispatched", "partially_delivered", "delivered", "invoiced"])
        if date_from:
            ol = ol.filter(order__order_date__gte=date_from)
        for r in ol.values("variant_id").annotate(d=Sum("qty_dispatched"), dl=Sum("qty_delivered")):
            key = str(r["variant_id"])
            if key in rows:
                rows[key]["dispatched"] = str((r["d"] or 0) - (r["dl"] or 0))
                rows[key]["delivered"] = str(r["dl"] or 0)
        return Response(sorted(rows.values(), key=lambda x: x["name"]))

    @action(detail=False, methods=["get"])
    def lookup(self, request):
        """Quick stock lookup by scan: ?code=... → variant + per-warehouse/batch levels."""
        from apps.catalog.serializers import ProductVariantSerializer
        from apps.catalog.services import BarcodeParser

        variant, packaging, batch = BarcodeParser.resolve(request.query_params.get("code", ""))
        levels = warehouse_scope(request, StockLevel.objects.filter(variant=variant)).select_related("warehouse", "batch", "bin")
        return Response({"variant": ProductVariantSerializer(variant).data, "packaging": packaging.name if packaging else None, "batch": batch.batch_no if batch else None, "levels": StockLevelSerializer(levels, many=True).data, "total_on_hand": str(sum((l.qty_on_hand for l in levels), Decimal("0")))})

    @action(detail=False, methods=["get"])
    def fefo(self, request):
        variant_id, wh_id, qty = request.query_params.get("variant"), request.query_params.get("warehouse"), Decimal(request.query_params.get("qty", "1"))
        from apps.catalog.models import ProductVariant

        plan, remaining = StockService.fefo_batches(ProductVariant.objects.get(pk=variant_id), Warehouse.objects.get(pk=wh_id), qty, request.tenant.setting("min_shelf_life_days"))
        return Response({"plan": [{"batch": str(b.id) if b else None, "batch_no": b.batch_no if b else None, "expiry_date": b.expiry_date if b else None, "qty": str(q), "bin": str(bin.id) if bin else None} for b, q, bin in plan], "short": str(remaining)})


class StockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsTenantUser]
    serializer_class = StockMovementSerializer
    perm_map = page_perms("movements")
    pagination_class = LedgerCursorPagination
    filterset_fields = ["warehouse", "variant", "batch", "movement_type", "reference_type", "reference_id", "performed_by"]
    search_fields = ["variant__name", "variant__sku", "reference_number", "note"]

    def get_queryset(self):
        qs = warehouse_scope(self.request, StockMovement.objects.select_related("warehouse", "variant", "variant__product", "batch", "performed_by"))
        p = self.request.query_params
        # the product page filters by variant__product; it is not a direct field, so DjangoFilter ignores it and
        # the tab ends up showing every movement in the company. Apply it here.
        if p.get("variant__product"):
            qs = qs.filter(variant__product_id=p["variant__product"])
        if p.get("date_from"):
            qs = qs.filter(occurred_at__date__gte=p["date_from"])
        if p.get("date_to"):
            qs = qs.filter(occurred_at__date__lte=p["date_to"])
        return qs


class BatchViewSet(TenantModelViewSet):
    """
    Batches and their dates.

    A batch's expiry is editable **after** the stock has been received, and deliberately so: a receipt that has
    been posted cannot be edited, so a date mistyped on the dock — or one the carton did not show at the time —
    could otherwise only be corrected by reversing the whole receipt. The date is load-bearing (picking is
    first-expiry-first-out, and the near-expiry alerts and report read it), so every change is written to the
    audit log with what it was before.
    """

    serializer_class = BatchSerializer
    perm_map = page_perms("batches", {"create": "inventory.change_batch", "destroy": "inventory.change_batch"})
    filterset_fields = ["variant", "supplier"]
    search_fields = ["batch_no", "variant__name", "variant__sku"]
    ordering_fields = ["expiry_date", "batch_no"]

    def perform_update(self, serializer):
        from apps.core.audit import AuditService

        batch = serializer.instance
        before = {"batch_no": batch.batch_no, "mfg_date": str(batch.mfg_date or ""), "expiry_date": str(batch.expiry_date or "")}
        new_no = (serializer.validated_data.get("batch_no") or batch.batch_no).strip()
        if new_no != batch.batch_no and Batch.objects.filter(variant=batch.variant, batch_no=new_no).exclude(pk=batch.pk).exists():
            raise DomainError(
                f"{batch.variant.name} already has a batch {new_no}. Two batches of one item cannot share a "
                f"number — the stock under each would become impossible to tell apart.",
                code="duplicate_batch", status_code=400,
            )
        batch = serializer.save()
        after = {"batch_no": batch.batch_no, "mfg_date": str(batch.mfg_date or ""), "expiry_date": str(batch.expiry_date or "")}
        if after != before:
            AuditService.log("batch.corrected", batch, before=before, after=after, request=self.request)

    def get_queryset(self):
        qs = Batch.objects.select_related("variant").annotate(qty_on_hand=Sum("stock_levels__qty_on_hand"))
        p = self.request.query_params
        if p.get("expiring_days"):
            qs = qs.filter(expiry_date__lte=timezone.localdate() + timezone.timedelta(days=int(p["expiring_days"])), expiry_date__gte=timezone.localdate())
        if p.get("expired") == "1":
            qs = qs.filter(expiry_date__lt=timezone.localdate())
        if p.get("nonzero") == "1":
            qs = qs.filter(qty_on_hand__gt=0)
        return qs


class StockTransferViewSet(DocumentViewSet):
    serializer_class = StockTransferSerializer
    perm_map = page_perms("transfers", {
        "approve": extra_perm("transfers", "approve"),
        "dispatch_transfer": extra_perm("transfers", "dispatch"),
        "receive": extra_perm("transfers", "dispatch"),
        "cancel": "inventory.delete_transfer",
    })
    filterset_fields = ["status", "from_warehouse", "to_warehouse"]
    search_fields = ["number", "notes"]
    ordering_fields = ["date", "number", "created_at"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        qs = StockTransfer.objects.select_related("from_warehouse", "to_warehouse", "requested_by", "approved_by").prefetch_related("lines__variant", "lines__batch")
        m = getattr(self.request, "membership", None)
        if m is not None and not m.is_owner:
            ids = list(m.warehouse_access.values_list("warehouse_id", flat=True))
            if ids:
                qs = qs.filter(Q(from_warehouse_id__in=ids) | Q(to_warehouse_id__in=ids))
        return qs

    def perform_create(self, serializer):
        d = serializer.validated_data
        lines = [{"variant": l["variant"], "batch": l.get("batch"), "qty": l["qty_requested"], "qty_sent": l.get("qty_sent")} for l in d["lines"]]
        serializer.instance = TransferService.create(from_warehouse=d["from_warehouse"], to_warehouse=d["to_warehouse"], lines=lines, date=d.get("date"), notes=d.get("notes", ""))

    def perform_update(self, serializer):
        tr = serializer.instance
        if tr.status not in ("requested", "approved"):
            raise DomainError("Only requested/approved transfers can be edited.")
        d = serializer.validated_data
        lines = d.pop("lines", None)
        super().perform_update(serializer)
        if lines is not None:
            tr.lines.all().delete()
            from .models import StockTransferLine

            for l in lines:
                StockTransferLine.objects.create(transfer=tr, variant=l["variant"], batch=l.get("batch"), qty_requested=l["qty_requested"], qty_sent=l.get("qty_sent") or l["qty_requested"], unit_cost=l["variant"].avg_cost or l["variant"].cost_price)

    @action(detail=True, methods=["post"])
    @require_perm("inventory.approve_transfer")
    def approve(self, request, pk=None):
        return Response(StockTransferSerializer(TransferService.approve(self.get_object())).data)

    @action(detail=True, methods=["post"], url_path="dispatch")
    @require_perm("inventory.transfer_stock")
    def dispatch_transfer(self, request, pk=None):
        return Response(StockTransferSerializer(TransferService.dispatch(self.get_object(), request.data.get("sent_qtys"))).data)

    @action(detail=True, methods=["post"])
    @require_perm("inventory.transfer_stock")
    def receive(self, request, pk=None):
        reason = ReasonCode.objects.filter(pk=request.data.get("lost_reason")).first() if request.data.get("lost_reason") else None
        return Response(StockTransferSerializer(TransferService.receive(self.get_object(), request.data.get("received_qtys"), reason)).data)

    @action(detail=True, methods=["post"])
    @require_perm("inventory.transfer_stock")
    def cancel(self, request, pk=None):
        return Response(StockTransferSerializer(TransferService.cancel(self.get_object(), request.data.get("reason", ""))).data)


class StockAdjustmentViewSet(DocumentViewSet):
    serializer_class = StockAdjustmentSerializer
    perm_map = page_perms("adjustments", {
        "submit": "inventory.add_adjustment",
        "approve": extra_perm("adjustments", "approve"),
        "reject": extra_perm("adjustments", "approve"),
        "stats": "inventory.view_adjustment",
    }, also={"create": [extra_perm("adjustments", "wastage")]})
    filterset_fields = ["status", "warehouse", "type", "reason", "reason__category"]
    search_fields = ["number", "notes", "lines__variant__name"]
    ordering_fields = ["date", "number", "total_value", "created_at"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return warehouse_scope(self.request, StockAdjustment.objects.select_related("warehouse", "reason", "approved_by", "created_by", "journal").prefetch_related("lines__variant", "lines__batch")).distinct()

    def perform_create(self, serializer):
        d = serializer.validated_data
        reason = d["reason"]
        if reason.requires_photo and not self.request.data.get("attachment_ids") and not self.request.data.get("photo_confirmed"):
            raise DomainError(f"Reason '{reason.name}' requires a photo attachment.")
        lines = [{"variant": l["variant"], "batch": l.get("batch"), "bin": l.get("bin"), "qty": l["qty"], "unit_cost": l.get("unit_cost"), "note": l.get("note", "")} for l in d["lines"]]
        serializer.instance = AdjustmentService.create(warehouse=d["warehouse"], type=d["type"], reason=reason, lines=lines, date=d.get("date"), notes=d.get("notes", ""), submit=self.request.data.get("submit", True))

    def perform_update(self, serializer):
        adj = serializer.instance
        if adj.status != "draft":
            raise DomainError("Only draft adjustments can be edited.")
        d = serializer.validated_data
        lines = d.pop("lines", None)
        super().perform_update(serializer)
        if lines is not None:
            from .models import StockAdjustmentLine

            adj.lines.all().delete()
            total = Decimal("0")
            for l in lines:
                unit_cost = Decimal(l.get("unit_cost") or l["variant"].avg_cost or l["variant"].cost_price)
                value = (Decimal(l["qty"]) * unit_cost).quantize(Decimal("0.0001"))
                StockAdjustmentLine.objects.create(adjustment=adj, variant=l["variant"], batch=l.get("batch"), bin=l.get("bin"), qty=l["qty"], unit_cost=unit_cost, value=value, note=l.get("note", ""))
                total += value
            adj.total_value = total
            adj.save(update_fields=["total_value"])

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        return Response(StockAdjustmentSerializer(AdjustmentService.submit(self.get_object())).data)

    @action(detail=True, methods=["post"])
    @require_perm("inventory.approve_adjustment")
    def approve(self, request, pk=None):
        return Response(StockAdjustmentSerializer(AdjustmentService.approve(self.get_object())).data)

    @action(detail=True, methods=["post"])
    @require_perm("inventory.approve_adjustment")
    def reject(self, request, pk=None):
        return Response(StockAdjustmentSerializer(AdjustmentService.reject(self.get_object(), request.data.get("reason", ""))).data)

    @action(detail=False, methods=["get"])
    def stats(self, request):
        qs = self.get_queryset()
        return Response({"pending": qs.filter(status="pending_approval").count(), "approved_month": qs.filter(status="approved", date__gte=timezone.localdate().replace(day=1)).count(), "value_month": str(qs.filter(status="approved", date__gte=timezone.localdate().replace(day=1)).aggregate(s=Sum("total_value"))["s"] or 0)})


class StockCountViewSet(DocumentViewSet):
    serializer_class = StockCountSerializer
    perm_map = page_perms("counts", {
        "record": "inventory.change_count",
        "review": "inventory.view_count",
        "post": extra_perm("counts", "post"),
        "cancel": "inventory.delete_count",
        "variances": "inventory.view_count",
    })
    filterset_fields = ["status", "warehouse", "type"]
    search_fields = ["number"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return warehouse_scope(self.request, StockCount.objects.select_related("warehouse", "started_by", "adjustment", "adjustment_increase"))

    def perform_create(self, serializer):
        d = serializer.validated_data
        serializer.instance = CountService.open(warehouse=d["warehouse"], type=d.get("type", "full"), scope=d.get("scope"), tolerance_pct=d.get("tolerance_pct"), notes=d.get("notes", ""))

    @action(detail=True, methods=["post"])
    def record(self, request, pk=None):
        entries = request.data if isinstance(request.data, list) else request.data.get("entries", [])
        return Response(StockCountSerializer(CountService.record(self.get_object(), entries)).data)

    @action(detail=True, methods=["post"])
    @require_perm("inventory.change_count")
    def review(self, request, pk=None):
        return Response(StockCountSerializer(CountService.review(self.get_object())).data)

    @action(detail=True, methods=["post"])
    @require_perm("inventory.approve_stock_count")
    def post(self, request, pk=None):
        reason = ReasonCode.objects.filter(pk=request.data.get("reason")).first() if request.data.get("reason") else None
        return Response(StockCountSerializer(CountService.post(self.get_object(), reason)).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        c = self.get_object()
        if c.status == "posted":
            raise DomainError("Posted counts cannot be cancelled.")
        c.status = "cancelled"
        c.save(update_fields=["status"])
        return Response(StockCountSerializer(c).data)

    @action(detail=True, methods=["get"])
    def variances(self, request, pk=None):
        c = self.get_object()
        rows = []
        for l in c.lines.select_related("variant", "batch", "bin"):
            if l.counted_qty is None:
                continue
            var = l.counted_qty - l.system_qty
            pct = (abs(var) / l.system_qty * 100) if l.system_qty else (Decimal("100") if var else Decimal("0"))
            rows.append({"line_id": str(l.id), "sku": l.variant.sku, "name": l.variant.name, "batch": l.batch.batch_no if l.batch else "", "bin": l.bin.code if l.bin else "", "system_qty": str(l.system_qty), "counted_qty": str(l.counted_qty), "variance": str(var), "variance_pct": str(pct.quantize(Decimal("0.01"))), "over_tolerance": pct > c.tolerance_pct, "value": str((var * (l.variant.avg_cost or l.variant.cost_price)).quantize(Decimal("0.0001")))})
        return Response(rows)


class ValuationView(APIView):
    permission_classes = [IsTenantUser]
    required_perm = "inventory.view_valuation"  # the Valuation page's View switch

    def get(self, request):
        wh = Warehouse.objects.filter(pk=request.query_params.get("warehouse")).first() if request.query_params.get("warehouse") else None
        return Response(StockService.valuation(wh))


class InventoryAlertViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsTenantUser]
    serializer_class = InventoryAlertSerializer
    perm_map = page_perms("stock_alerts", {"refresh": "inventory.view_alert", "reorder": "inventory.view_alert", "counts": "inventory.view_alert"})
    filterset_fields = ["type", "warehouse", "variant"]

    def get_queryset(self):
        qs = InventoryAlert.objects.filter(resolved_at__isnull=True).select_related("variant", "warehouse", "batch")
        return qs

    @action(detail=False, methods=["post"])
    def refresh(self, request):
        n = AlertService.compute(request.tenant)
        return Response({"alerts": n})

    @action(detail=False, methods=["get"], url_path="reorder-suggestions")
    def reorder(self, request):
        return Response(AlertService.reorder_suggestions())

    @action(detail=False, methods=["get"])
    def counts(self, request):
        qs = self.get_queryset()
        return Response({t: qs.filter(type=t).count() for t, _ in InventoryAlert.TYPES})


class OpeningStockView(APIView):
    """POST {warehouse, lines:[{variant, qty, unit_cost, batch_no?, expiry_date?}]} — opening stock movements."""

    permission_classes = [IsTenantUser]
    required_perm = "inventory.add_adjustment"  # opening stock is an adjustment

    def post(self, request):
        from apps.catalog.models import ProductVariant

        wh = Warehouse.objects.get(pk=request.data["warehouse"])
        out = []
        for l in request.data.get("lines", []):
            v = ProductVariant.objects.select_related("product").get(pk=l["variant"])
            batch = StockService.get_or_create_batch(v, l.get("batch_no"), l.get("expiry_date"), l.get("mfg_date")) if l.get("batch_no") else None
            mv = StockService.move(warehouse=wh, variant=v, qty=Decimal(l["qty"]), movement_type="opening", unit_cost=l.get("unit_cost"), batch=batch, note="Opening stock")
            out.append(str(mv.id))
        from apps.accounting.services import PostingService

        je = PostingService.post_opening_stock(wh, out)
        AuditService.log("opening_stock.posted", model="StockMovement", after={"warehouse": wh.code, "lines": len(out), "journal": je.number if je else None}, request=request)
        return Response({"movements": out, "journal": je.number if je else None}, status=201)
