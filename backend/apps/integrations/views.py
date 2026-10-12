import csv
import io

from django.http import HttpResponse
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import DomainError
from apps.accounts.page_permissions import extra_perm, page_perms
from apps.core.permissions import IsTenantUser
from apps.core.plan_limits import PlanLimitService
from apps.core.viewsets import TenantModelViewSet

from .models import SCOPES, ApiKey, ImportJob, WebhookDelivery, WebhookEndpoint
from .serializers import ApiKeySerializer, ImportJobSerializer, WebhookDeliverySerializer, WebhookEndpointSerializer
from .services import ImportService


class ApiKeyViewSet(TenantModelViewSet):
    serializer_class = ApiKeySerializer
    perm_map = page_perms("settings_api_keys")
    pagination_class = None
    queryset = ApiKey.objects.select_related("supplier")

    def create(self, request, *args, **kwargs):
        PlanLimitService.require_feature(request.tenant, "external_api")
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        from apps.core.plan_limits import effective_limits

        limit = effective_limits(request.tenant).get("api_rate_per_min") or 60
        d = dict(s.validated_data)
        d["rate_limit"] = min(int(d.get("rate_limit") or limit), int(limit)) if limit != -1 else d.get("rate_limit") or 300
        key, raw = ApiKey.generate(**d)
        from apps.core.audit import AuditService

        AuditService.log("api_key.created", key, after={"name": key.name, "scopes": key.scopes}, request=request)
        return Response({**ApiKeySerializer(key).data, "key": raw, "warning": "Store this key now; it will not be shown again."}, status=201)

    @action(detail=True, methods=["post"])
    def rotate(self, request, pk=None):
        old = self.get_object()
        key, raw = ApiKey.generate(name=old.name, scopes=old.scopes, supplier=old.supplier, rep=old.rep, rate_limit=old.rate_limit, ip_allowlist=old.ip_allowlist, expires_at=old.expires_at)
        old.is_active = False
        old.save(update_fields=["is_active"])
        return Response({**ApiKeySerializer(key).data, "key": raw}, status=201)

    @action(detail=True, methods=["get"])
    def usage(self, request, pk=None):
        rows = self.get_object().usage.order_by("-at").values("path", "method", "status", "ip", "at")[:200]
        return Response(list(rows))

    @action(detail=False, methods=["get"])
    def scopes(self, request):
        return Response([{"scope": s, "label": l} for s, l in SCOPES])


class WebhookEndpointViewSet(TenantModelViewSet):
    serializer_class = WebhookEndpointSerializer
    perm_map = page_perms("settings_webhooks")
    pagination_class = None
    queryset = WebhookEndpoint.objects.select_related("supplier")

    @action(detail=True, methods=["get"])
    def deliveries(self, request, pk=None):
        return Response(WebhookDeliverySerializer(WebhookDelivery.objects.filter(endpoint_id=pk)[:200], many=True).data)

    @action(detail=True, methods=["post"])
    def test(self, request, pk=None):
        from django.utils import timezone

        from .services import WebhookService

        ep = self.get_object()
        d = WebhookDelivery.objects.create(endpoint=ep, event="test", payload={"event": "test", "occurred_at": timezone.now().isoformat(), "data": {"hello": "world"}})
        WebhookService.attempt(d)
        return Response(WebhookDeliverySerializer(d).data)

    @action(detail=True, methods=["post"], url_path=r"deliveries/(?P<delivery_id>[^/.]+)/retry")
    def retry(self, request, pk=None, delivery_id=None):
        from .services import WebhookService

        d = WebhookDelivery.objects.get(pk=delivery_id, endpoint_id=pk)
        d.status = "pending"
        WebhookService.attempt(d)
        return Response(WebhookDeliverySerializer(d).data)

    @action(detail=False, methods=["get"])
    def events(self, request):
        return Response(WebhookEndpoint.EVENTS)


class ImportJobViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsTenantUser]
    serializer_class = ImportJobSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    filterset_fields = ["type", "status"]
    perm_map = page_perms("settings_imports", {"template": "settings.view_import"})

    def get_queryset(self):
        return ImportJob.objects.select_related("started_by")

    def perform_create(self, serializer):
        job = serializer.save(started_by=self.request.user)
        ImportService.run(job)

    TEMPLATES = {
        "products": ["Code", "Product Name", "Category", "Brand", "Supplier", "UNIT", "Packing", "D.P", "T.P", "R.P", "Location", "Opening Qty", "Barcode", "UOM", "Tax Class", "Batch Tracked", "Expiry Tracked", "Min Stock", "Max Stock", "Reorder Qty"],
        "customers": ["code", "name", "type", "phone", "address", "city", "territory", "route", "credit_limit", "credit_days", "opening_balance", "contact_person"],
        "suppliers": ["code", "name", "phone", "email", "address", "city", "tax_no", "payment_terms_days", "contact_person"],
        "opening_stock": ["sku", "warehouse", "qty", "unit_cost", "batch_no", "expiry_date"],
        "opening_balances": ["account", "debit", "credit", "partner_type", "partner_id"],
        "price_list": ["price_list", "sku", "min_qty", "price"],
        "purchase_order": ["Code", "Product Name", "UNIT", "Qty", "Cartons", "Rate"],
    }
    EXAMPLES = {"products": [["62001", "NO. 1 BLACK - 60 ML", "Tinta Color", "Keune", "", "60ml", 24, 1130, 1255, 1495, "A-01", 48, "", "PC", "", "", "", 0, 0, 0]], "purchase_order": [["62001", "NO. 1 BLACK - 60 ML", "60ml", 48, "", ""], ["62011", "NO. 1.1 BLUE BLACK - 60 ML", "60ml", "", 2, ""]]}

    @action(detail=False, methods=["get"], url_path=r"template/(?P<kind>[a-z_]+)")
    def template(self, request, kind=None):
        """CSV template (default) or `?as=xlsx` — the products template uses the supplier price-list columns (Code / Product Name / UNIT / Packing / D.P / T.P / R.P)."""
        headers = self.TEMPLATES.get(kind)
        if not headers:
            raise DomainError("Unknown template", status_code=404)
        if request.query_params.get("as") == "xlsx":
            import openpyxl
            from openpyxl.styles import Font

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = kind
            ws.append(headers)
            for c in ws[1]:
                c.font = Font(bold=True)
            for row in self.EXAMPLES.get(kind, []):
                ws.append(row)
            for i, h in enumerate(headers, start=1):
                ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = max(10, min(40, len(h) + 6))
            buf = io.BytesIO()
            wb.save(buf)
            resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            resp["Content-Disposition"] = f'attachment; filename="{kind}-template.xlsx"'
            return resp
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(headers)
        for row in self.EXAMPLES.get(kind, []):
            w.writerow(row)
        resp = HttpResponse(buf.getvalue(), content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="{kind}-template.csv"'
        return resp


class ExportView(APIView):
    """GET /exports/{kind} → CSV of products | customers | suppliers | stock"""

    permission_classes = [IsTenantUser]
    required_perm = extra_perm("reports", "export")

    def get(self, request, kind):
        buf = io.StringIO()
        w = csv.writer(buf)
        if kind == "products":
            from apps.catalog.models import ProductVariant

            # Same columns as the import template, so an export can be edited and re-imported (matched by Code).
            w.writerow(["Code", "Product Name", "Category", "Brand", "Supplier", "UNIT", "Packing", "D.P", "T.P", "R.P", "Location", "Stock Qty", "Barcode", "UOM", "Tax Class", "Batch Tracked", "Expiry Tracked", "Min Stock", "Max Stock", "Reorder Qty", "Avg Cost", "Status", "Price Date"])
            from django.db.models import Sum

            for v in ProductVariant.objects.select_related("product__brand", "product__category", "product__supplier", "product__base_uom", "product__tax_class", "product__location").prefetch_related("barcodes").annotate(on_hand=Sum("stock_levels__qty_on_hand")).order_by("product__category__name", "product__name"):
                p = v.product
                w.writerow([v.sku, v.name, p.category.name if p.category_id else "", p.brand.name if p.brand_id else "", p.supplier.name if p.supplier_id else "", p.unit_size, p.packing, v.cost_price, v.sale_price, v.retail_price, p.location.code if p.location_id else "", v.on_hand or 0, "|".join(b.code for b in v.barcodes.all()), p.base_uom.code, p.tax_class.name if p.tax_class_id else "", int(p.is_batch_tracked), int(p.is_expiry_tracked), p.min_stock, p.max_stock, p.reorder_qty, v.avg_cost, p.status, p.price_effective_date or ""])
        elif kind == "customers":
            from apps.partners.models import Customer

            # `price_list` is here because the field was taken off the customer screen: a customer pointed at a
            # named list still gets those prices, and this export is now the only place staff can see which.
            w.writerow(["code", "name", "type", "phone", "address", "city", "territory", "route", "credit_limit", "credit_days", "price_list", "status"])
            for c in Customer.objects.select_related("territory", "route", "price_list"):
                w.writerow([c.code, c.name, c.type, c.phone, c.address, c.city, c.territory.name if c.territory_id else "", c.route.name if c.route_id else "", c.credit_limit, c.credit_days, c.price_list.name if c.price_list_id else "", c.status])
        elif kind == "suppliers":
            from apps.partners.models import Supplier

            w.writerow(["code", "name", "phone", "email", "address", "city", "tax_no", "payment_terms_days"])
            for s in Supplier.objects.all():
                w.writerow([s.code, s.name, s.phone, s.email, s.address, s.city, s.tax_no, s.payment_terms_days])
        elif kind == "stock":
            from apps.inventory.models import StockLevel

            w.writerow(["warehouse", "sku", "name", "batch", "expiry", "qty_on_hand", "qty_reserved", "avg_cost"])
            for lv in StockLevel.objects.filter(qty_on_hand__gt=0).select_related("warehouse", "variant", "batch"):
                w.writerow([lv.warehouse.code, lv.variant.sku, lv.variant.name, lv.batch.batch_no if lv.batch else "", lv.batch.expiry_date if lv.batch else "", lv.qty_on_hand, lv.qty_reserved, lv.variant.avg_cost])
        else:
            raise DomainError("Unknown export", status_code=404)
        resp = HttpResponse(buf.getvalue(), content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="{kind}.csv"'
        return resp
