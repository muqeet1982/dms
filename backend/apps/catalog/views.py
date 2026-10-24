from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.page_permissions import extra_perm, page_perms
from apps.core.exceptions import DomainError
from apps.core.permissions import IsTenantUser, require_perm
from apps.core.viewsets import TenantModelViewSet

from .models import Barcode, Brand, Category, Packaging, PriceList, PriceListItem, Product, ProductVariant, Scheme, TaxClass, TaxRate, TaxType, UnitOfMeasure
from .serializers import (
    BarcodeSerializer, BrandSerializer, CategorySerializer, PackagingSerializer, PriceListItemSerializer, PriceListSerializer, ProductCreateSerializer,
    ProductSerializer, ProductVariantSerializer, SchemeSerializer, TaxClassSerializer, TaxRateSerializer, TaxTypeSerializer, UnitOfMeasureSerializer,
)
from .services import BarcodeParser, LabelService, PricingService, ProductService, TaxService



class BrandViewSet(TenantModelViewSet):
    serializer_class = BrandSerializer
    perm_map = page_perms("brands")
    search_fields = ["name"]
    filterset_fields = ["supplier", "is_active"]
    pagination_class = None

    def get_queryset(self):
        return Brand.objects.select_related("supplier").annotate(products_count=Count("products"))


class CategoryViewSet(TenantModelViewSet):
    serializer_class = CategorySerializer
    perm_map = page_perms("categories")
    search_fields = ["name"]
    filterset_fields = ["parent", "is_active"]
    pagination_class = None

    def get_queryset(self):
        return Category.objects.select_related("parent").annotate(products_count=Count("products"))


class UnitOfMeasureViewSet(TenantModelViewSet):
    serializer_class = UnitOfMeasureSerializer
    perm_map = page_perms("uoms")
    pagination_class = None
    queryset = UnitOfMeasure.objects.all()


class TaxTypeViewSet(TenantModelViewSet):
    serializer_class = TaxTypeSerializer
    perm_map = page_perms("settings_taxes", {"rates": "settings.change_tax", "delete_rate": "settings.change_tax"})
    pagination_class = None
    queryset = TaxType.objects.prefetch_related("rates")

    @action(detail=True, methods=["post"])
    def rates(self, request, pk=None):
        s = TaxRateSerializer(data={**request.data, "tax_type": pk})
        s.is_valid(raise_exception=True)
        s.save()
        return Response(s.data, status=201)

    @action(detail=True, methods=["delete"], url_path=r"rates/(?P<rate_id>[^/.]+)")
    def delete_rate(self, request, pk=None, rate_id=None):
        TaxRate.objects.filter(tax_type_id=pk, pk=rate_id).delete()
        return Response(status=204)


class TaxClassViewSet(TenantModelViewSet):
    serializer_class = TaxClassSerializer
    perm_map = page_perms("settings_taxes")
    pagination_class = None
    queryset = TaxClass.objects.select_related("tax1", "tax2")

    def perform_create(self, serializer):
        if serializer.validated_data.get("is_default"):
            TaxClass.objects.update(is_default=False)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        if serializer.validated_data.get("is_default"):
            TaxClass.objects.exclude(pk=serializer.instance.pk).update(is_default=False)
        super().perform_update(serializer)


class ProductViewSet(TenantModelViewSet):
    perm_map = page_perms("products", {"variants": "catalog.change_product", "stats": "catalog.view_product"})
    search_fields = ["name", "sku", "upc", "box_upc", "item_code", "variants__sku", "variants__name", "variants__barcodes__code"]
    filterset_fields = ["brand", "category", "supplier", "status", "is_batch_tracked", "tax_class", "location", "location__warehouse"]
    ordering_fields = ["name", "sku", "created_at", "sale_price", "cost_price", "retail_price", "packing", "category__name", "brand__name", "unit_size", "location__name", "status", "stock_on_hand"]

    def get_serializer_class(self):
        return ProductCreateSerializer if self.action == "create" else ProductSerializer

    def perform_update(self, serializer):
        before = {f: getattr(serializer.instance, f) for f in (*ProductService.PRICE_FIELDS, "packing", "upc", "box_upc")}
        product = serializer.save()
        ProductService.after_update(product, before)

    def get_queryset(self):
        qs = Product.objects.select_related("brand", "category", "supplier", "base_uom", "tax_class", "location", "location__warehouse").prefetch_related("variants__packagings", "variants__barcodes").annotate(variants_count=Count("variants", distinct=True))
        # stock_on_hand is annotated for the list display and also whenever someone sorts by it directly
        if self.request.query_params.get("with_stock") == "1" or "stock_on_hand" in self.request.query_params.get("ordering", ""):
            qs = qs.annotate(stock_on_hand=Sum("variants__stock_levels__qty_on_hand"))
        if self.request.query_params.get("low_stock") == "1":
            from django.db.models import F

            qs = qs.annotate(stock_on_hand=Sum("variants__stock_levels__qty_on_hand")).filter(stock_on_hand__lt=F("min_stock"))
        return qs.distinct()

    def perform_create(self, serializer):
        data = dict(serializer.validated_data)
        variants = data.pop("variants", None)
        # this path builds the Product itself (ProductService, not serializer.save()), so the serializer's
        # own pop of the dated offers never runs — they would reach Product(**data) and 500
        offer_rows = data.pop("offers_write", None)
        opening = {k: data.pop(f"opening_{k}", None) for k in ("qty", "warehouse", "unit_cost", "batch_no", "expiry_date")}
        product = ProductService.create_product(self.request.tenant, data, variants=variants)
        if offer_rows is not None:
            serializer._save_offers(product, offer_rows)
        if opening["qty"]:
            ProductService.post_opening_stock(product, opening["qty"], warehouse=opening["warehouse"], unit_cost=opening["unit_cost"], batch_no=opening["batch_no"] or "", expiry_date=opening["expiry_date"])
        serializer.instance = product
        from apps.core.audit import AuditService

        AuditService.log("created", product, after={"sku": product.sku, "name": product.name}, request=self.request)

    def perform_destroy(self, instance):
        instance.status = "archived"
        instance.save(update_fields=["status"])
        for v in instance.variants.all():
            v.is_active = False
            v.save(update_fields=["is_active"])

    @action(detail=True, methods=["post"])
    @require_perm("catalog.change_product")
    def variants(self, request, pk=None):
        product = self.get_object()
        s = ProductVariantSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        variant = ProductService.add_variant(product, s.validated_data)
        return Response(ProductVariantSerializer(variant).data, status=201)

    @action(detail=False, methods=["get"])
    def stats(self, request):
        from django.db.models import F

        qs = Product.objects.all()
        low = Product.objects.annotate(s=Sum("variants__stock_levels__qty_on_hand")).filter(s__lt=F("min_stock")).count()
        return Response({"total": qs.count(), "active": qs.filter(status="active").count(), "inactive": qs.exclude(status="active").count(), "low_stock": low, "variants": ProductVariant.objects.count()})


class VariantViewSet(TenantModelViewSet):
    serializer_class = ProductVariantSerializer
    perm_map = page_perms("products", {
        "create": "catalog.change_product", "update": "catalog.change_product", "partial_update": "catalog.change_product", "destroy": "catalog.change_product",
        "packagings": "catalog.change_product", "barcodes": "catalog.change_product",
        "generate_barcode": extra_perm("products", "print_barcode"), "price": "catalog.view_product",
    })
    search_fields = ["name", "sku", "product__name", "barcodes__code"]
    filterset_fields = ["product", "is_active", "product__brand", "product__category", "product__supplier"]
    ordering_fields = ["name", "sku", "sale_price"]

    def get_queryset(self):
        qs = ProductVariant.objects.select_related("product", "product__brand", "product__category", "product__base_uom").prefetch_related("packagings", "barcodes")
        if self.request.query_params.get("in_stock") == "1":
            qs = qs.annotate(_onhand=Sum("stock_levels__qty_on_hand")).filter(_onhand__gt=0)
        if self.request.query_params.get("with_stock") == "1":
            qs = qs.annotate(stock_on_hand=Sum("stock_levels__qty_on_hand"))
        # `for_supplier` is the purchase-order line search: this supplier's products, *plus* anything nobody has
        # assigned a supplier to. The strict `product__supplier` filter hid every unassigned product, and a
        # catalogue imported from a price list has no supplier column — so a whole company's search came back
        # empty and looked broken. An orphan is better shown than silently missing from an order.
        supplier = self.request.query_params.get("for_supplier")
        if supplier:
            qs = qs.filter(Q(product__supplier_id=supplier) | Q(product__supplier__isnull=True))
        return qs.distinct()

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=["is_active"])

    @action(detail=True, methods=["post"])
    @require_perm("catalog.change_product")
    def packagings(self, request, pk=None):
        s = PackagingSerializer(data={**request.data, "variant": pk})
        s.is_valid(raise_exception=True)
        s.save()
        return Response(s.data, status=201)

    @action(detail=True, methods=["post"])
    @require_perm("catalog.change_product")
    def barcodes(self, request, pk=None):
        s = BarcodeSerializer(data={**request.data, "variant": pk})
        s.is_valid(raise_exception=True)
        if s.validated_data.get("is_primary"):
            Barcode.objects.filter(variant_id=pk).update(is_primary=False)
        s.save()
        return Response(s.data, status=201)

    @action(detail=True, methods=["post"], url_path="generate-barcode")
    @require_perm("catalog.change_product")
    def generate_barcode(self, request, pk=None):
        variant = self.get_object()
        packaging = Packaging.objects.filter(pk=request.data.get("packaging")).first() if request.data.get("packaging") else None
        code = BarcodeParser.internal_code(variant, packaging)
        bc, _ = Barcode.objects.get_or_create(code=code, defaults={"variant": variant, "packaging": packaging, "type": "internal"})
        return Response(BarcodeSerializer(bc).data, status=201)

    @action(detail=True, methods=["get"])
    def price(self, request, pk=None):
        from decimal import Decimal

        from apps.partners.models import Customer

        variant = self.get_object()
        customer = Customer.objects.filter(pk=request.query_params.get("customer")).first() if request.query_params.get("customer") else None
        packaging = Packaging.objects.filter(pk=request.query_params.get("packaging")).first() if request.query_params.get("packaging") else None
        qty = Decimal(request.query_params.get("qty", "1"))
        price, pl = PricingService.price_for(variant, customer, packaging=packaging, qty=qty)
        tax_class = TaxService.resolve_class(variant, customer)
        taxes = TaxService.compute(price * qty, tax_class)
        disc, free, names = PricingService.apply_schemes(variant, qty, price, customer)
        return Response({"unit_price": str(price), "price_list": pl.name if pl else None, "tax_class": tax_class.name if tax_class else None, "tax1_rate": str(taxes["tax1_rate"]), "tax2_rate": str(taxes["tax2_rate"]), "scheme_discount": str(disc), "free_qty": str(free), "schemes": names})


class PackagingViewSet(TenantModelViewSet):
    serializer_class = PackagingSerializer
    perm_map = page_perms("products", {
        "create": "catalog.change_product", "update": "catalog.change_product", "partial_update": "catalog.change_product", "destroy": "catalog.change_product",
        "resolve": "catalog.view_product",
    })
    queryset = Packaging.objects.select_related("uom", "variant")
    filterset_fields = ["variant"]
    pagination_class = None


class BarcodeViewSet(TenantModelViewSet):
    serializer_class = BarcodeSerializer
    perm_map = page_perms("products", {
        "create": "catalog.change_product", "update": "catalog.change_product", "partial_update": "catalog.change_product", "destroy": "catalog.change_product",
        "resolve": "catalog.view_product",
    })
    queryset = Barcode.objects.select_related("variant", "packaging")
    filterset_fields = ["variant", "type", "is_primary"]
    search_fields = ["code"]

    def perform_update(self, serializer):
        """
        Only one barcode on a variant is the primary one, so promoting this barcode demotes its siblings.

        The nested create route already did this; editing came in through the flat route, which did not — so
        correcting a code and ticking "primary" used to leave two primaries on one variant, and the printed
        label then carried whichever the database happened to return first. Scanning was never affected:
        `resolve` matches on the code and never reads this flag.
        """
        primary = serializer.validated_data.get("is_primary", serializer.instance.is_primary)
        variant_id = serializer.validated_data.get("variant", serializer.instance.variant).pk
        if primary:
            Barcode.objects.filter(variant_id=variant_id).exclude(pk=serializer.instance.pk).update(is_primary=False)
        serializer.save()

    @action(detail=False, methods=["get"])
    def resolve(self, request):
        """
        Scan-to-search: ?code=… → the item, how much this one scan means, and anything the label carried.

        `qty_per_scan` is the number of base units (a box code gives the box size), and `batch_no` / `expiry_date`
        come back even when no batch row exists yet — a box label like `16418.01#195654` carries its batch.
        """
        hit = BarcodeParser.parse(request.query_params.get("code", ""))
        variant, packaging, batch = hit["variant"], hit["packaging"], hit["batch"]
        return Response({
            "variant": ProductVariantSerializer(variant).data,
            "packaging": PackagingSerializer(packaging).data if packaging else None,
            "batch": {"id": str(batch.id), "batch_no": batch.batch_no, "expiry_date": batch.expiry_date} if batch else None,
            "batch_no": hit["batch_no"],
            "expiry_date": hit["expiry_date"],
            "qty_per_scan": str(hit["qty_per_scan"]),
            "unit": hit["unit"],
            "item_code": variant.product.item_code,
            "packing": variant.product.packing,
            # where this product is normally kept, so a receiving screen can put the goods back in the right rack
            "location": str(variant.product.location_id) if variant.product.location_id else None,
            "location_name": f"{variant.product.location.warehouse.code} / {variant.product.location.code}" if variant.product.location_id else "",
            "warehouse": str(variant.product.location.warehouse_id) if variant.product.location_id else None,
            "warehouse_name": variant.product.location.warehouse.name if variant.product.location_id else "",
        })


class PriceListViewSet(TenantModelViewSet):
    serializer_class = PriceListSerializer
    perm_map = page_perms("price_lists", {"items": "catalog.view_price_list", "delete_item": "catalog.change_price_list"})
    pagination_class = None

    def get_queryset(self):
        return PriceList.objects.annotate(items_count=Count("items"))

    @action(detail=True, methods=["get", "post"])
    def items(self, request, pk=None):
        if request.method != "GET":
            require_perm("catalog.change_price_list")(lambda s, r: None)(self, request)
        if request.method == "GET":
            qs = PriceListItem.objects.filter(price_list_id=pk).select_related("variant", "packaging")
            q = request.query_params.get("search")
            if q:
                from django.db.models import Q

                qs = qs.filter(Q(variant__name__icontains=q) | Q(variant__sku__icontains=q))
            return Response(PriceListItemSerializer(qs[:1000], many=True).data)
        require_perm("catalog.change_price_list")(lambda s, r: None)(self, request)
        rows = request.data if isinstance(request.data, list) else [request.data]
        out = []
        for row in rows:
            s = PriceListItemSerializer(data={**row, "price_list": pk})
            s.is_valid(raise_exception=True)
            obj, _ = PriceListItem.objects.update_or_create(price_list_id=pk, variant=s.validated_data["variant"], packaging=s.validated_data.get("packaging"), min_qty=s.validated_data.get("min_qty", 0), defaults={"price": s.validated_data["price"]})
            out.append(PriceListItemSerializer(obj).data)
        return Response(out, status=201)

    @action(detail=True, methods=["delete"], url_path=r"items/(?P<item_id>[^/.]+)")
    def delete_item(self, request, pk=None, item_id=None):
        PriceListItem.objects.filter(price_list_id=pk, pk=item_id).delete()
        return Response(status=204)


class SchemeViewSet(TenantModelViewSet):
    serializer_class = SchemeSerializer
    perm_map = page_perms("schemes")
    queryset = Scheme.objects.all()
    filterset_fields = ["type", "is_active"]
    search_fields = ["name"]


class LabelPrintView(APIView):
    """POST {size, template, items:[{variant, packaging?, batch?, qty}]} → PDF of labels."""

    permission_classes = [IsTenantUser]
    required_perm = extra_perm("products", "print_barcode")

    def post(self, request):
        from apps.inventory.models import Batch

        items = []
        for it in request.data.get("items", []):
            variant = ProductVariant.objects.select_related("product").get(pk=it["variant"])
            packaging = Packaging.objects.filter(pk=it.get("packaging")).first() if it.get("packaging") else None
            batch = Batch.objects.filter(pk=it.get("batch")).first() if it.get("batch") else None
            bc = Barcode.objects.filter(variant=variant, packaging=packaging).order_by("-is_primary").first()
            code = bc.code if bc else BarcodeParser.internal_code(variant, packaging, batch)
            if batch and not bc:
                code = BarcodeParser.internal_code(variant, packaging, batch)
            for _ in range(int(it.get("qty", 1))):
                items.append({"code": code, "title": variant.name, "subtitle": f"{variant.sku}{' · ' + packaging.name if packaging else ''}", "price": str(variant.sale_price) if it.get("show_price") else "", "batch": batch.batch_no if batch else "", "expiry": batch.expiry_date.isoformat() if batch and batch.expiry_date else ""})
        if not items:
            raise DomainError("No items to print.", status_code=400)
        pdf = LabelService.render_pdf(items, size=request.data.get("size", "50x30"), template=request.data.get("template", "product"))
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = 'inline; filename="labels.pdf"'
        return resp
