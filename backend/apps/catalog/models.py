from decimal import Decimal

from django.db import models

from apps.core.models import MONEY, QTY, TenantModel


class Brand(TenantModel):
    name = models.CharField(max_length=100)
    supplier = models.ForeignKey("partners.Supplier", null=True, blank=True, on_delete=models.SET_NULL, related_name="brands")
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("tenant", "name")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class Category(TenantModel):
    name = models.CharField(max_length=100)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("tenant", "name", "parent")]
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def path(self):
        parts, node = [], self
        while node is not None:
            parts.append(node.name)
            node = node.parent
        return " › ".join(reversed(parts))


class UnitOfMeasure(TenantModel):
    code = models.CharField(max_length=10)
    name = models.CharField(max_length=40)

    class Meta:
        unique_together = [("tenant", "code")]
        ordering = ["code"]

    def __str__(self):
        return self.code


class TaxType(TenantModel):
    KINDS = [("sales_tax", "Sales tax"), ("further_tax", "Further tax"), ("withholding", "Withholding"), ("other", "Other")]
    APPLIES = [("sales", "Sales"), ("purchases", "Purchases"), ("both", "Both")]
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=15, choices=KINDS, default="sales_tax")
    applies_to = models.CharField(max_length=10, choices=APPLIES, default="both")
    output_account = models.ForeignKey("accounting.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", help_text="Tax payable account (sales)")
    input_account = models.ForeignKey("accounting.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", help_text="Tax receivable account (purchases)")
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("tenant", "code")]
        ordering = ["code"]

    def __str__(self):
        return self.name

    def rate_on(self, on_date):
        r = self.rates.filter(valid_from__lte=on_date).filter(models.Q(valid_to__isnull=True) | models.Q(valid_to__gte=on_date)).order_by("-valid_from").first()
        return r.rate_pct if r else Decimal("0")


class TaxRate(TenantModel):
    tax_type = models.ForeignKey(TaxType, on_delete=models.CASCADE, related_name="rates")
    name = models.CharField(max_length=60, blank=True)
    rate_pct = models.DecimalField(max_digits=7, decimal_places=3, default=Decimal("0"))
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-valid_from"]


class TaxClass(TenantModel):
    name = models.CharField(max_length=60)
    tax1 = models.ForeignKey(TaxType, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    tax2 = models.ForeignKey(TaxType, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    tax2_compounds_on_tax1 = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False)

    class Meta:
        unique_together = [("tenant", "name")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(TenantModel):
    """
    Codes a distributor actually works with:

    * `upc` — the barcode printed on the unit. Scanning it means **one unit**.
    * `box_upc` — the barcode on the outer box. Scanning it means **one box**, i.e. `packing` units.
    * `item_code` — the supplier's own catalogue code (Keune's "62001"), what people quote on orders.
    * `sku` — our internal key, kept unique per company; filled from the UPC (or item code) when not given, so
      every existing document, import and report that refers to a SKU keeps working.
    """

    STATUS = [("active", "Active"), ("inactive", "Inactive"), ("archived", "Archived")]
    sku = models.CharField(max_length=60)
    upc = models.CharField(max_length=64, blank=True, db_index=True, help_text="Barcode on the unit — one scan = one unit")
    box_upc = models.CharField(max_length=64, blank=True, db_index=True, help_text="Barcode on the box — one scan = one box (`packing` units)")
    item_code = models.CharField(max_length=60, blank=True, db_index=True, help_text="Supplier's item / catalogue code")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    brand = models.ForeignKey(Brand, null=True, blank=True, on_delete=models.SET_NULL, related_name="products")
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="products")
    supplier = models.ForeignKey("partners.Supplier", null=True, blank=True, on_delete=models.SET_NULL, related_name="products")
    base_uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")
    is_batch_tracked = models.BooleanField(default=False)
    is_expiry_tracked = models.BooleanField(default=False)
    shelf_life_days = models.PositiveIntegerField(null=True, blank=True)
    tax_class = models.ForeignKey(TaxClass, null=True, blank=True, on_delete=models.SET_NULL, related_name="products")
    location = models.ForeignKey("inventory.Bin", null=True, blank=True, on_delete=models.SET_NULL, related_name="products", help_text="Default storage location (warehouse rack/bin)")
    unit_size = models.CharField(max_length=30, blank=True, help_text="Pack size as printed on the price list: 60ml, 1L, 500g, pcs")
    packing = models.PositiveIntegerField(default=1, help_text="Units per box / carton (price-list 'Packing')")
    cost_price = models.DecimalField(**MONEY)  # D.P — distributor price (what we pay the supplier)
    sale_price = models.DecimalField(**MONEY)
    # the product's own standing discount, applied on every sales line of this product (editable per line);
    # deliberately separate from the customer's co-funded discount — the manufacturer does not co-fund it
    discount_pct = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal("0"))  # T.P — trade price (what we charge salons / retailers)
    # the standard discount only bites from this many units on a line (owner's rule, 21 Oct 2026); 0 = always
    discount_min_qty = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
    retail_price = models.DecimalField(**MONEY)  # R.P — retail price / MRP printed for the end consumer
    price_effective_date = models.DateField(null=True, blank=True, help_text="Date of the price list these prices came from")
    min_stock = models.DecimalField(**QTY)
    max_stock = models.DecimalField(**QTY)
    reorder_qty = models.DecimalField(**QTY)
    status = models.CharField(max_length=10, choices=STATUS, default="active")
    image = models.ImageField(upload_to="products/", null=True, blank=True)
    attributes = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = [("tenant", "sku")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class ProductVariant(TenantModel):
    """The stock-keeping unit. A simple product has exactly one default variant."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    sku = models.CharField(max_length=60)
    name = models.CharField(max_length=200)
    attributes = models.JSONField(default=dict, blank=True)  # {"shade_code": "7.1", "size_ml": 60}
    cost_price = models.DecimalField(**MONEY)  # D.P
    sale_price = models.DecimalField(**MONEY)  # T.P
    retail_price = models.DecimalField(**MONEY)  # R.P
    avg_cost = models.DecimalField(**MONEY)  # weighted average, maintained by StockService
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    image = models.ImageField(upload_to="products/", null=True, blank=True)

    class Meta:
        unique_together = [("tenant", "sku")]
        ordering = ["product__name", "name"]

    def __str__(self):
        return self.name

    @property
    def display_name(self):
        return self.name if self.name != self.product.name else self.product.name


class Packaging(TenantModel):
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="packagings")
    uom = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=40)  # piece, box, carton
    qty_in_base = models.DecimalField(max_digits=18, decimal_places=3, default=Decimal("1"))
    barcode = models.CharField(max_length=64, blank=True)
    is_default_sales = models.BooleanField(default=False)

    class Meta:
        unique_together = [("variant", "name")]
        ordering = ["qty_in_base"]

    def __str__(self):
        return f"{self.name} ({self.qty_in_base})"


class Barcode(TenantModel):
    TYPES = [("EAN13", "EAN-13"), ("UPC", "UPC"), ("CODE128", "Code 128"), ("QR", "QR"), ("internal", "Internal")]
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="barcodes")
    packaging = models.ForeignKey(Packaging, null=True, blank=True, on_delete=models.CASCADE, related_name="barcodes")
    code = models.CharField(max_length=64)
    type = models.CharField(max_length=10, choices=TYPES, default="EAN13")
    is_primary = models.BooleanField(default=False)

    class Meta:
        unique_together = [("tenant", "code")]

    def __str__(self):
        return self.code


class PriceList(TenantModel):
    name = models.CharField(max_length=80)
    currency = models.CharField(max_length=3, default="PKR")
    is_default = models.BooleanField(default=False)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("tenant", "name")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class PriceListItem(TenantModel):
    price_list = models.ForeignKey(PriceList, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="prices")
    packaging = models.ForeignKey(Packaging, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    min_qty = models.DecimalField(max_digits=18, decimal_places=3, default=Decimal("0"))
    price = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["variant", "min_qty"]


class Scheme(TenantModel):
    TYPES = [("percent", "Percent off"), ("amount", "Amount off"), ("free_qty", "Buy X get Y free")]
    name = models.CharField(max_length=100)
    type = models.CharField(max_length=10, choices=TYPES)
    rules = models.JSONField(default=dict, blank=True)  # {"min_qty": 12, "value": "5", "buy_qty": 12, "free_qty": 1}
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    customers = models.ManyToManyField("partners.Customer", blank=True, related_name="schemes")
    customer_types = models.JSONField(default=list, blank=True)
    variants = models.ManyToManyField(ProductVariant, blank=True, related_name="schemes")
    brands = models.ManyToManyField(Brand, blank=True, related_name="schemes")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ProductOffer(TenantModel):
    """
    A dated discount on one product: % off from min_qty units, between two dates inclusive. While an offer
    matches (date in window, line qty at the minimum), it REPLACES the product's standard discount — the
    owner's rule, 21 Oct 2026. Several matching offers resolve to the best one for the customer.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="offers")
    discount_pct = models.DecimalField(max_digits=6, decimal_places=3)
    min_qty = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["start_date"]

    def matches(self, qty, on):
        return self.is_active and self.start_date <= on <= self.end_date and qty >= self.min_qty


def product_discount_for(product, qty, on_date=None):
    """The % this line earns: the best matching dated offer, else the standard % once qty reaches its minimum."""
    from django.utils import timezone

    on = on_date or timezone.localdate()
    offered = [o.discount_pct for o in product.offers.all() if o.matches(qty, on)]
    if offered:
        return max(offered)
    if product.discount_pct and qty >= (product.discount_min_qty or 0):
        return product.discount_pct
    return Decimal("0")
