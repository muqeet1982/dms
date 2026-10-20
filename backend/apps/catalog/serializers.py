from rest_framework import serializers

from apps.inventory.models import Warehouse

from .models import Barcode, Brand, Category, Packaging, PriceList, PriceListItem, Product, ProductVariant, Scheme, TaxClass, TaxRate, TaxType, UnitOfMeasure


class BrandSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True, default="")
    products_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Brand
        fields = ["id", "name", "supplier", "supplier_name", "is_active", "products_count", "created_at"]


class CategorySerializer(serializers.ModelSerializer):
    path = serializers.CharField(read_only=True)
    parent_name = serializers.CharField(source="parent.name", read_only=True, default="")
    products_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Category
        fields = ["id", "name", "parent", "parent_name", "path", "is_active", "products_count"]


class UnitOfMeasureSerializer(serializers.ModelSerializer):
    class Meta:
        model = UnitOfMeasure
        fields = ["id", "code", "name"]


class TaxRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaxRate
        fields = ["id", "tax_type", "name", "rate_pct", "valid_from", "valid_to"]
        read_only_fields = ["id"]


class TaxTypeSerializer(serializers.ModelSerializer):
    rates = TaxRateSerializer(many=True, read_only=True)
    current_rate = serializers.SerializerMethodField()
    output_account_name = serializers.CharField(source="output_account.name", read_only=True, default="")
    input_account_name = serializers.CharField(source="input_account.name", read_only=True, default="")

    class Meta:
        model = TaxType
        fields = ["id", "code", "name", "kind", "applies_to", "output_account", "output_account_name", "input_account", "input_account_name", "is_active", "rates", "current_rate"]

    def get_current_rate(self, obj):
        from datetime import date

        return str(obj.rate_on(date.today()))


class TaxClassSerializer(serializers.ModelSerializer):
    tax1_name = serializers.CharField(source="tax1.name", read_only=True, default="")
    tax2_name = serializers.CharField(source="tax2.name", read_only=True, default="")

    class Meta:
        model = TaxClass
        fields = ["id", "name", "tax1", "tax1_name", "tax2", "tax2_name", "tax2_compounds_on_tax1", "is_default"]


class PackagingSerializer(serializers.ModelSerializer):
    uom_code = serializers.CharField(source="uom.code", read_only=True)

    class Meta:
        model = Packaging
        fields = ["id", "variant", "uom", "uom_code", "name", "qty_in_base", "barcode", "is_default_sales"]
        read_only_fields = ["id"]
        extra_kwargs = {"variant": {"required": False}}


class BarcodeSerializer(serializers.ModelSerializer):
    packaging_name = serializers.CharField(source="packaging.name", read_only=True, default="")

    class Meta:
        model = Barcode
        fields = ["id", "variant", "packaging", "packaging_name", "code", "type", "is_primary"]
        read_only_fields = ["id"]
        extra_kwargs = {"variant": {"required": False}}


class VariantSkuMixin:
    def validate_sku(self, value):
        qs = ProductVariant.objects.filter(sku__iexact=value.strip())
        if getattr(self, "instance", None) is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A product/variant with this code already exists.")
        return value.strip()


class ProductVariantSerializer(VariantSkuMixin, serializers.ModelSerializer):
    product_discount_pct = serializers.DecimalField(source="product.discount_pct", max_digits=6, decimal_places=3, read_only=True)
    product_discount_min_qty = serializers.DecimalField(source="product.discount_min_qty", max_digits=14, decimal_places=3, read_only=True)
    product_offers = serializers.SerializerMethodField()

    def get_product_offers(self, obj):
        from django.utils import timezone

        today = timezone.localdate()
        return [{"discount_pct": str(o.discount_pct), "min_qty": str(o.min_qty), "start_date": o.start_date, "end_date": o.end_date}
                for o in obj.product.offers.all() if o.is_active and o.end_date >= today]
    packagings = PackagingSerializer(many=True, read_only=True)
    barcodes = BarcodeSerializer(many=True, read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    brand_name = serializers.CharField(source="product.brand.name", read_only=True, default="")
    category_name = serializers.CharField(source="product.category.name", read_only=True, default="")
    base_uom = serializers.CharField(source="product.base_uom.code", read_only=True)
    is_batch_tracked = serializers.BooleanField(source="product.is_batch_tracked", read_only=True)
    is_expiry_tracked = serializers.BooleanField(source="product.is_expiry_tracked", read_only=True)
    tax_class = serializers.UUIDField(source="product.tax_class_id", read_only=True)
    unit_size = serializers.CharField(source="product.unit_size", read_only=True)
    packing = serializers.IntegerField(source="product.packing", read_only=True)
    stock_on_hand = serializers.DecimalField(max_digits=18, decimal_places=3, read_only=True, default=None)

    class Meta:
        model = ProductVariant
        fields = ["id", "product", "product_name", "product_sku", "brand_name", "category_name", "sku", "name", "attributes", "unit_size", "packing", "cost_price", "sale_price", "retail_price", "product_discount_pct", "product_discount_min_qty", "product_offers", "avg_cost", "is_default", "is_active", "image", "base_uom", "is_batch_tracked", "is_expiry_tracked", "tax_class", "packagings", "barcodes", "stock_on_hand"]
        read_only_fields = ["id", "avg_cost"]
        extra_kwargs = {"product": {"required": False}}


class ProductSerializer(serializers.ModelSerializer):
    variants = ProductVariantSerializer(many=True, read_only=True)
    # dated offers: [{discount_pct, min_qty, start_date, end_date}] — writing replaces the whole set
    offers = serializers.SerializerMethodField()
    offers_write = serializers.ListField(child=serializers.DictField(), write_only=True, required=False)

    def get_offers(self, obj):
        return [{"id": str(o.id), "discount_pct": str(o.discount_pct), "min_qty": str(o.min_qty),
                 "start_date": o.start_date, "end_date": o.end_date, "is_active": o.is_active} for o in obj.offers.all()]

    def _save_offers(self, product, rows):
        from decimal import Decimal, InvalidOperation

        from django.utils.dateparse import parse_date

        from .models import ProductOffer

        product.offers.all().delete()
        for r in rows:
            try:
                pct = Decimal(str(r.get("discount_pct") or 0))
                start, end = parse_date(str(r.get("start_date") or "")), parse_date(str(r.get("end_date") or ""))
                if pct <= 0 or start is None or end is None or end < start:
                    continue
                ProductOffer.objects.create(product=product, discount_pct=pct, min_qty=Decimal(str(r.get("min_qty") or 0)), start_date=start, end_date=end)
            except (InvalidOperation, ValueError):
                continue

    def create(self, validated_data):
        rows = validated_data.pop("offers_write", None)
        product = super().create(validated_data)
        if rows is not None:
            self._save_offers(product, rows)
        return product

    def update(self, instance, validated_data):
        rows = validated_data.pop("offers_write", None)
        product = super().update(instance, validated_data)
        if rows is not None:
            self._save_offers(product, rows)
        return product
    brand_name = serializers.CharField(source="brand.name", read_only=True, default="")
    category_name = serializers.CharField(source="category.name", read_only=True, default="")
    supplier_name = serializers.CharField(source="supplier.name", read_only=True, default="")
    base_uom_code = serializers.CharField(source="base_uom.code", read_only=True)
    tax_class_name = serializers.CharField(source="tax_class.name", read_only=True, default="")
    location_name = serializers.SerializerMethodField()
    variants_count = serializers.IntegerField(read_only=True, default=0)
    stock_on_hand = serializers.DecimalField(max_digits=18, decimal_places=3, read_only=True, default=None)

    class Meta:
        model = Product
        fields = ["id", "sku", "upc", "box_upc", "item_code", "name", "description", "brand", "brand_name", "category", "category_name", "supplier", "supplier_name", "base_uom", "base_uom_code", "is_batch_tracked", "is_expiry_tracked", "shelf_life_days", "tax_class", "tax_class_name", "location", "location_name", "unit_size", "packing", "cost_price", "sale_price", "retail_price", "discount_pct", "discount_min_qty", "offers", "offers_write", "price_effective_date", "min_stock", "max_stock", "reorder_qty", "status", "image", "attributes", "variants", "variants_count", "stock_on_hand", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {"sku": {"required": False}}

    def validate_sku(self, value):
        qs = Product.objects.filter(sku__iexact=value.strip())
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A product with this code already exists.")
        return value.strip()

    def validate(self, attrs):
        attrs = super().validate(attrs)
        # the UPC is what people type now; the internal key follows it (or the item code) when left blank
        if not (attrs.get("sku") or (self.instance and self.instance.sku)):
            fallback = (attrs.get("upc") or attrs.get("item_code") or "").strip()
            if not fallback:
                raise serializers.ValidationError({"upc": ["Give the product a UPC or an item code."]})
            if Product.objects.filter(sku__iexact=fallback).exists():
                raise serializers.ValidationError({"upc": [f"{fallback} is already used by another product."]})
            attrs["sku"] = fallback
        for field in ("upc", "box_upc", "item_code"):
            if attrs.get(field):
                attrs[field] = attrs[field].strip()
        if attrs.get("box_upc") and attrs.get("box_upc") == attrs.get("upc"):
            raise serializers.ValidationError({"box_upc": ["The box needs a different barcode from the unit."]})
        if attrs.get("box_upc") and int(attrs.get("packing") or (self.instance.packing if self.instance else 1) or 1) <= 1:
            raise serializers.ValidationError({"packing": ["Set how many units are in a box before giving the box its own barcode."]})
        return attrs

    def get_location_name(self, obj):
        return f"{obj.location.warehouse.code} / {obj.location.code}" + (f" — {obj.location.name}" if obj.location.name else "") if obj.location_id else ""


class ProductCreateSerializer(ProductSerializer):
    variants = serializers.ListField(child=serializers.DictField(), required=False, write_only=True)
    # opening stock: posted once, when the product is created (later corrections go through stock adjustments)
    opening_qty = serializers.DecimalField(max_digits=18, decimal_places=3, required=False, allow_null=True, write_only=True)
    opening_warehouse = serializers.PrimaryKeyRelatedField(queryset=Warehouse.objects.all(), required=False, allow_null=True, write_only=True)
    opening_unit_cost = serializers.DecimalField(max_digits=18, decimal_places=4, required=False, allow_null=True, write_only=True)
    opening_batch_no = serializers.CharField(required=False, allow_blank=True, allow_null=True, write_only=True)
    opening_expiry_date = serializers.DateField(required=False, allow_null=True, write_only=True)

    class Meta(ProductSerializer.Meta):
        fields = ProductSerializer.Meta.fields + ["opening_qty", "opening_warehouse", "opening_unit_cost", "opening_batch_no", "opening_expiry_date"]


class PriceListItemSerializer(serializers.ModelSerializer):
    variant_name = serializers.CharField(source="variant.name", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)
    packaging_name = serializers.CharField(source="packaging.name", read_only=True, default="")

    class Meta:
        model = PriceListItem
        fields = ["id", "price_list", "variant", "variant_name", "variant_sku", "packaging", "packaging_name", "min_qty", "price"]
        read_only_fields = ["id"]


class PriceListSerializer(serializers.ModelSerializer):
    items_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = PriceList
        fields = ["id", "name", "currency", "is_default", "valid_from", "valid_to", "is_active", "items_count"]


class SchemeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Scheme
        fields = ["id", "name", "type", "rules", "valid_from", "valid_to", "customers", "customer_types", "variants", "brands", "is_active"]
