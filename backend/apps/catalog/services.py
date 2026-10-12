import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q

from apps.core.exceptions import DomainError
from apps.core.plan_limits import PlanLimitService

from .models import Barcode, Packaging, PriceList, PriceListItem, Product, ProductVariant, Scheme, TaxClass, TaxType, UnitOfMeasure

Q4 = Decimal("0.0001")


def seed_catalog_defaults(tenant):
    for code, name in [("PC", "Piece"), ("BOX", "Box"), ("CTN", "Carton"), ("KG", "Kilogram"), ("L", "Litre")]:
        UnitOfMeasure.all_objects.get_or_create(tenant=tenant, code=code, defaults={"name": name})
    if not PriceList.all_objects.filter(tenant=tenant).exists():
        PriceList.all_objects.create(tenant=tenant, name="Retail", is_default=True, currency=tenant.base_currency)
        PriceList.all_objects.create(tenant=tenant, name="Wholesale", currency=tenant.base_currency)
    if not TaxClass.all_objects.filter(tenant=tenant).exists():
        TaxClass.all_objects.create(tenant=tenant, name="Tax exempt", is_default=True)


def _sync_one(Barcode, variant, packaging, code, type_, previous=None):
    """
    Keep the barcode row for this (variant, packaging) in step with the product's code field.

    A blank code is *not* a licence to delete: barcodes also arrive from imports, scanning and the barcode editor,
    and wiping those on an unrelated product edit would make items unscannable. Only the code this field used to
    hold is removed when it is cleared.
    """
    code = (code or "").strip()
    previous = (previous or "").strip()
    if not code:
        if previous:
            Barcode.objects.filter(variant=variant, packaging=packaging, code=previous).delete()
        return
    clash = Barcode.objects.filter(code=code).exclude(variant=variant, packaging=packaging).first()
    if clash is not None:
        raise DomainError(f"The barcode {code} is already used by {clash.variant.name}.", code="duplicate", status_code=400)
    row = Barcode.objects.filter(variant=variant, packaging=packaging, code=code).first()
    if row is not None:
        return
    if previous:
        old = Barcode.objects.filter(variant=variant, packaging=packaging, code=previous).first()
        if old is not None:
            old.code, old.type = code, type_
            old.save(update_fields=["code", "type"])
            return
    Barcode.objects.create(variant=variant, packaging=packaging, code=code, type=type_, is_primary=packaging is None)


class ProductService:
    @staticmethod
    @transaction.atomic
    def create_product(tenant, data, variants=None, packagings=None, barcodes=None):
        PlanLimitService.check(tenant, "max_products", extra=max(1, len(variants or [])))
        # the internal key follows the UPC (or the item code) when the caller does not set one
        if not (data.get("sku") or "").strip():
            data["sku"] = (data.get("upc") or data.get("item_code") or "").strip()
        if not data["sku"]:
            raise DomainError("Give the product a UPC or an item code.", status_code=400)
        product = Product.objects.create(**data)
        if not variants:
            variants = [{"sku": product.sku, "name": product.name, "is_default": True, "cost_price": product.cost_price, "sale_price": product.sale_price, "retail_price": product.retail_price}]
        for v in variants:
            v.setdefault("cost_price", product.cost_price)
            v.setdefault("sale_price", product.sale_price)
            v.setdefault("retail_price", product.retail_price)
            variant = ProductVariant.objects.create(product=product, **v)
            if variant.avg_cost == 0:
                variant.avg_cost = variant.cost_price
                variant.save(update_fields=["avg_cost"])
            Packaging.objects.get_or_create(variant=variant, name=product.base_uom.name.lower(), defaults={"uom": product.base_uom, "qty_in_base": Decimal("1"), "is_default_sales": True})
        ProductService.sync_carton_packaging(product)
        ProductService.sync_codes(product)
        return product

    PRICE_FIELDS = ("cost_price", "sale_price", "retail_price")

    @staticmethod
    @transaction.atomic
    def post_opening_stock(product, qty, *, warehouse=None, unit_cost=None, batch_no="", expiry_date=None, note="Opening stock"):
        """
        Put the product's starting quantity on the shelf: one `opening` movement (+ its journal entry) for the
        default variant, in the product's location when it has one. Ignored for zero/negative quantities.
        """
        from apps.accounting.services import PostingService
        from apps.inventory.models import Warehouse
        from apps.inventory.services import StockService

        qty = Decimal(str(qty or 0))
        if qty <= 0:
            return None
        warehouse = warehouse or (product.location.warehouse if product.location_id else None) or Warehouse.objects.filter(is_active=True).order_by("-is_default", "name").first()
        if warehouse is None:
            raise DomainError("No warehouse to receive the opening stock — create one under Settings → Warehouses first.")
        variant = product.variants.filter(is_default=True).first() or product.variants.first()
        if variant is None:
            raise DomainError("The product has no variant to hold stock.")
        batch = StockService.get_or_create_batch(variant, batch_no, expiry_date) if (batch_no or product.is_batch_tracked) and batch_no else None
        bin_ = product.location if (product.location_id and product.location.warehouse_id == warehouse.id) else None
        mv = StockService.move(warehouse=warehouse, variant=variant, qty=qty, movement_type="opening", unit_cost=Decimal(str(unit_cost)) if unit_cost not in (None, "") else (product.cost_price or None), batch=batch, bin=bin_, note=note)
        PostingService.post_opening_stock(warehouse, [mv.id])
        return mv

    @staticmethod
    def sync_carton_packaging(product):
        """Keep a 'carton' packaging level on every variant equal to the product's `packing` (units per box)."""
        if not product.packing or product.packing <= 1:
            return
        ctn, _ = UnitOfMeasure.objects.get_or_create(code="CTN", defaults={"name": "Carton"})
        for variant in product.variants.all():
            pk, created = Packaging.objects.get_or_create(variant=variant, name="carton", defaults={"uom": ctn, "qty_in_base": Decimal(product.packing)})
            if not created and pk.qty_in_base != Decimal(product.packing):
                pk.qty_in_base = Decimal(product.packing)
                pk.save(update_fields=["qty_in_base"])

    @staticmethod
    def sync_codes(product, before=None):
        """
        Make the product's UPC and box UPC scannable: the unit code resolves to one unit, the box code to one box.

        A scan is resolved through `Barcode`, and a barcode that points at a packaging level multiplies by that
        level's size — so the box code only has to be attached to the 'carton' packaging for one scan to count a
        whole box. Clearing a code here removes the barcode it owned.
        """
        from .models import Barcode

        for variant in product.variants.all():
            unit_pack = None
            box_pack = Packaging.objects.filter(variant=variant, name="carton").first()
            before = before or {}
            _sync_one(Barcode, variant, unit_pack, product.upc, "UPC", previous=before.get("upc"))
            if product.box_upc and box_pack is None:
                raise DomainError("Set units per box before giving the box its own barcode.")
            _sync_one(Barcode, variant, box_pack, product.box_upc, "UPC", previous=before.get("box_upc"))

    @staticmethod
    @transaction.atomic
    def after_update(product, before: dict):
        """
        Called after a product header edit/import. Product-level prices are the defaults for its variants: every
        variant that still carried the old product price follows the new one (variants with their own price are kept).
        """
        for f in ProductService.PRICE_FIELDS:
            old, new = before.get(f), getattr(product, f)
            if old is None or old == new:
                continue
            for variant in product.variants.all():
                if getattr(variant, f) == old or variant.is_default:
                    setattr(variant, f, new)
                    variant.save(update_fields=[f])
        if before.get("packing") != product.packing:
            ProductService.sync_carton_packaging(product)
        if any(before.get(f) != getattr(product, f) for f in ("upc", "box_upc")) or before.get("packing") != product.packing:
            ProductService.sync_codes(product, before=before)

    @staticmethod
    @transaction.atomic
    def add_variant(product, data):
        PlanLimitService.check(product.tenant, "max_products")
        variant = ProductVariant.objects.create(product=product, **data)
        Packaging.objects.get_or_create(variant=variant, name=product.base_uom.name.lower(), defaults={"uom": product.base_uom, "qty_in_base": Decimal("1"), "is_default_sales": True})
        return variant


class BarcodeParser:
    """
    Resolve a scanned code to (variant, packaging, batch) — and, with `parse`, to the batch number and expiry the
    label carried even when no batch row exists yet.

    Order of attempts:

    1. an exact `Barcode` row (a code attached to a packaging level means that many units: the box code = one box);
    2. a `Packaging.barcode`;
    3. our internal label `T-{sku}[-{pack}][-B{batch}]`;
    4. GS1-128 `(01)GTIN(10)batch(17)expiry`;
    5. a **box label** that carries the item code and the batch together, e.g. `16418.01#195654` — **before the dot
       is the item code, after it is the batch number**. Item codes that contain a dot themselves still work: the
       split is tried at the first dot, then at the last one, then on `# | /`, and finally on the first five digits
       for labels printed without a separator;
    6. a bare **item code** — that is what is printed on a box, so one scan counts one box;
    7. a variant SKU — one unit.
    """

    GS1_RE = re.compile(r"\(01\)(?P<gtin>\d{14})(?:\(10\)(?P<batch>[^(]+))?(?:\(17\)(?P<exp>\d{6}))?")
    INTERNAL_RE = re.compile(r"^T-(?P<sku>[A-Za-z0-9._-]+?)(?:-(?P<pack>[A-Za-z]+))?(?:-B(?P<batch>[A-Za-z0-9]+))?$")
    # "16418.01#195654" → item code 16418, batch 01#195654 (before the dot / after the dot)
    OTHER_SEPARATORS = "#|/"
    ITEM_CODE_DIGITS = 5  # fallback split when the label has no separator: "16418195654"

    @classmethod
    def parse(cls, code: str) -> dict:
        """
        {variant, packaging, batch, batch_no, expiry_date, qty_per_scan, unit} — `unit` is "box" or "unit", and
        `qty_per_scan` is how many base units this one scan means.
        """
        from apps.inventory.models import Batch

        code = (code or "").strip()
        if not code:
            raise DomainError("Empty scan.", status_code=400)

        def answer(variant, packaging=None, batch=None, batch_no="", expiry=None, box=False):
            # `box` says the label was read off a carton. The quantity then falls back to the product's own
            # units-per-box when no packaging row exists for it — one live tenant is missing a single carton row,
            # and a carton quietly counting as one piece is the error this whole path exists to prevent.
            if packaging is not None:
                qty = Decimal(packaging.qty_in_base)
            elif box:
                qty = Decimal(int(variant.product.packing or 1))
            else:
                qty = Decimal("1")
            if batch is None and batch_no:
                batch = Batch.objects.filter(variant=variant, batch_no__iexact=batch_no).first()
            return {
                "variant": variant, "packaging": packaging, "batch": batch,
                "batch_no": (batch.batch_no if batch is not None else batch_no) or "",
                "expiry_date": (batch.expiry_date if batch is not None else expiry),
                "qty_per_scan": qty, "unit": "box" if qty > 1 else "unit",
            }

        bc = Barcode.objects.filter(code=code).select_related("variant", "packaging", "variant__product").first()
        if bc:
            return answer(bc.variant, bc.packaging)
        pk = Packaging.objects.filter(barcode=code).select_related("variant", "variant__product").first()
        if pk:
            return answer(pk.variant, pk)
        m = cls.INTERNAL_RE.match(code)
        if m:
            variant = ProductVariant.objects.filter(sku__iexact=m.group("sku")).select_related("product").first()
            if variant:
                packaging = Packaging.objects.filter(variant=variant, name__iexact=m.group("pack")).first() if m.group("pack") else None
                return answer(variant, packaging, batch_no=m.group("batch") or "")
        m = cls.GS1_RE.search(code)
        if m:
            gtin = m.group("gtin").lstrip("0")
            bc = Barcode.objects.filter(Q(code=gtin) | Q(code=m.group("gtin")) | Q(code=m.group("gtin")[1:])).select_related("variant", "packaging").first()
            if bc:
                expiry = None
                if m.group("exp"):
                    raw = m.group("exp")  # YYMMDD
                    expiry = date(2000 + int(raw[:2]), int(raw[2:4]), int(raw[4:6] or 1) or 1)
                return answer(bc.variant, bc.packaging, batch_no=m.group("batch") or "", expiry=expiry)

        # The carton's own barcode. A case is normally labelled GTIN-14 — the unit's EAN-13 behind a packaging
        # indicator digit — and nobody registers those by hand: 429 products here carry a unit UPC and not one
        # carries a case code, so a storeman scanning the carton got "no product matches" and went back to
        # typing. Reading the unit's number out of it costs nothing and makes every carton scannable.
        cased = cls._by_article_number(code)
        if cased is not None:
            bc, is_case = cased
            return answer(bc.variant, bc.packaging or (cls._box_packaging(bc.variant) if is_case else None), box=is_case)

        # a box label: the item code, then the batch number after the dot
        candidates = []
        if "." in code:
            head, _, tail = code.partition(".")          # first dot — the documented format
            candidates.append((head, tail.strip()))
            head, _, tail = code.rpartition(".")         # item codes that contain a dot themselves
            candidates.append((head, tail.strip()))
        for sep in cls.OTHER_SEPARATORS:
            if sep in code:
                head, _, tail = code.partition(sep)
                candidates.append((head, tail.strip()))
        # The whole code first: a label that is nothing but the item code means one carton of it.
        candidates.append((code, ""))
        # Then a label printed with no separator at all — "16418195654", the item code and then the batch. The
        # split used to be fixed at five characters, which quietly resolved a carton of **16418D** to the
        # *different* product **16418**: ten products in this catalogue differ from another only by a letter on
        # the end (16418/16418D, 50037/50037B, 62051/62051UC), and a wrong line on a receipt is not something
        # anybody catches. Every split is considered now, and the longest item code that actually exists wins.
        head = cls._longest_item_code_prefix(code)
        if head:
            candidates.append((head, code[len(head) :].strip(" .-/|#")))
        for item_code, batch_no in candidates:
            variant = cls._by_item_code(item_code)
            if variant is not None:
                # the item code is what is printed on the carton, so one scan of it is one carton
                return answer(variant, cls._box_packaging(variant), batch_no=batch_no, box=True)

        variant = ProductVariant.objects.filter(sku__iexact=code).select_related("product").first()
        if variant:
            return answer(variant)
        raise DomainError(f"No product matches code {code}.", status_code=404, code="not_found")

    @staticmethod
    def _ean_check(body: str) -> int:
        """The last digit of an EAN-13/GTIN-14, computed from the digits before it."""
        # weights run 3,1,3,1… leftwards from the digit beside the check digit, whatever the code's length
        total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
        return (10 - total % 10) % 10

    @classmethod
    def _by_article_number(cls, code: str):
        """
        `(barcode, this_is_a_carton)` for a number that is one of our articles printed differently, else None.

        A UPC-A is an EAN-13 with a zero in front of it — the same article, read differently by a different
        scanner. A GTIN-14 (the big barcode on a shipping carton, ITF-14 in print) is the unit's EAN-13 behind a
        packaging indicator digit: 1-8 says "a case of them", 0 says the same single article.

        The **check digit is not used for matching**. It exists so a scanner can catch its own misreads, not so
        our records can be checked against it, and a catalogue typed in from a price list does not always agree:
        8 of the 429 codes in the live catalogue do not check out, and several are stored as twelve digits with
        the check digit missing altogether. Refusing a perfectly good carton because of our own typing would be
        the worse failure, so an article is matched on its digits and the last one is ignored.
        """
        if not code.isdigit():
            return None
        if len(code) == 14:
            stem, is_case = code[1:13], code[0] != "0"
        elif len(code) == 13:
            # a UPC-A stored as twelve digits, scanned in its EAN-13 form (the same code behind a zero)
            if code.startswith("0"):
                bc = Barcode.objects.filter(code=code[1:]).select_related("variant", "packaging", "variant__product").first()
                if bc is not None:
                    return (bc, False)
            stem, is_case = code[:12], False
        elif len(code) == 12:
            # the same code as an EAN-13 is a zero in front; the plain form was already tried exactly above
            bc = Barcode.objects.filter(code="0" + code).select_related("variant", "packaging", "variant__product").first()
            return (bc, False) if bc else None
        else:
            return None
        rows = [b for b in Barcode.objects.filter(code__startswith=stem).select_related("variant", "packaging", "variant__product")
                if len(b.code) in (len(stem), len(stem) + 1)]
        rows.sort(key=lambda b: -len(b.code))  # a full 13-digit code before a truncated 12-digit one
        return (rows[0], is_case) if rows else None

    @staticmethod
    def _box_packaging(variant):
        """
        The box an item-code scan means. The product's `packing` ("units per box" on the form) is the authority —
        a carton packaging left behind with a different size must not make one scan count a different quantity.
        """
        per_box = int(variant.product.packing or 1)
        if per_box <= 1:
            return None
        box = Packaging.objects.filter(variant=variant, name="carton").first()
        if box is None:
            return None
        if box.qty_in_base != Decimal(per_box):
            box.qty_in_base = Decimal(per_box)
            box.save(update_fields=["qty_in_base"])
        return box

    MIN_ITEM_CODE = 3  # below this a "prefix" is noise, not a code

    @classmethod
    def _longest_item_code_prefix(cls, code):
        """
        The longest leading part of `code` that is a real item code, or "" — one query, not one per split.

        Longest wins so that a label for `16418D` is not read as `16418` with a batch beginning "D".
        """
        prefixes = [code[:k] for k in range(len(code) - 1, cls.MIN_ITEM_CODE - 1, -1)]
        if not prefixes:
            return ""
        wanted = set(prefixes) | {p.upper() for p in prefixes}
        rows = Product.objects.filter(Q(item_code__in=wanted) | Q(sku__in=wanted)).values_list("item_code", "sku")
        known = {str(c).upper() for row in rows for c in row if c}
        return next((p for p in prefixes if p.upper() in known), "")

    @staticmethod
    def _by_item_code(item_code):
        """The item code is a product-level code, so it answers with the product's default (or only) variant."""
        item_code = (item_code or "").strip()
        if not item_code:
            return None
        product = Product.objects.filter(Q(item_code__iexact=item_code) | Q(sku__iexact=item_code)).first()
        if product is None:
            return None
        return product.variants.filter(is_default=True).first() or product.variants.first()

    @classmethod
    def resolve(cls, code: str):
        """(variant, packaging, batch) — kept for callers that only need the row, not the label's hints."""
        hit = cls.parse(code)
        return hit["variant"], hit["packaging"], hit["batch"]

    @staticmethod
    def internal_code(variant, packaging=None, batch=None):
        code = f"T-{variant.sku}"
        if packaging is not None:
            code += f"-{packaging.name}"
        if batch is not None:
            code += f"-B{batch.batch_no}"
        return code


class PricingService:
    @staticmethod
    def price_for(variant, customer=None, price_list=None, packaging=None, qty=Decimal("1"), on_date=None):
        """Unit price (per base unit unless packaging given) with quantity slabs."""
        on_date = on_date or date.today()
        pl = price_list or (customer.price_list if customer is not None and customer.price_list_id else None) or PriceList.objects.filter(is_default=True).first()
        if pl is not None:
            items = PriceListItem.objects.filter(price_list=pl, variant=variant, min_qty__lte=qty).order_by("-min_qty")
            item = items.filter(packaging=packaging).first() if packaging is not None else None
            if item is None:
                base_item = items.filter(packaging__isnull=True).first()
                if base_item is not None:
                    return (base_item.price * (packaging.qty_in_base if packaging else 1)).quantize(Q4), pl
            else:
                return item.price.quantize(Q4), pl
        return (variant.sale_price * (packaging.qty_in_base if packaging else 1)).quantize(Q4), pl

    @staticmethod
    def applicable_schemes(variant, customer=None, on_date=None):
        on_date = on_date or date.today()
        qs = Scheme.objects.filter(is_active=True).filter(Q(valid_from__isnull=True) | Q(valid_from__lte=on_date)).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=on_date))
        qs = qs.filter(Q(variants=variant) | Q(brands=variant.product.brand_id) | Q(variants__isnull=True, brands__isnull=True)).distinct()
        out = []
        for s in qs:
            if s.customers.exists() and (customer is None or not s.customers.filter(pk=customer.pk).exists()):
                continue
            if s.customer_types and (customer is None or customer.type not in s.customer_types):
                continue
            out.append(s)
        return out

    @staticmethod
    def apply_schemes(variant, qty, unit_price, customer=None, on_date=None):
        """Returns (discount_amount, free_qty, scheme_names)."""
        discount, free_qty, names = Decimal("0"), Decimal("0"), []
        for s in PricingService.applicable_schemes(variant, customer, on_date):
            rules = s.rules or {}
            min_qty = Decimal(str(rules.get("min_qty", 0) or 0))
            if qty < min_qty:
                continue
            if s.type == "percent":
                discount += (qty * unit_price * Decimal(str(rules.get("value", 0))) / 100).quantize(Q4)
            elif s.type == "amount":
                discount += (Decimal(str(rules.get("value", 0))) * qty).quantize(Q4)
            elif s.type == "free_qty":
                buy = Decimal(str(rules.get("buy_qty", 0) or 0))
                free = Decimal(str(rules.get("free_qty", 0) or 0))
                if buy > 0:
                    free_qty += (qty // buy) * free
            names.append(s.name)
        return discount, free_qty, names


class TaxService:
    @staticmethod
    def compute(taxable: Decimal, tax_class: TaxClass | None, on_date=None):
        """Return dict with tax1/tax2 type, rate and amount for a taxable base."""
        on_date = on_date or date.today()
        out = {"tax1_type": None, "tax1_rate": Decimal("0"), "tax1_amount": Decimal("0"), "tax2_type": None, "tax2_rate": Decimal("0"), "tax2_amount": Decimal("0")}
        if tax_class is None:
            return out
        if tax_class.tax1_id:
            r = tax_class.tax1.rate_on(on_date)
            out.update(tax1_type=tax_class.tax1, tax1_rate=r, tax1_amount=(taxable * r / 100).quantize(Q4, ROUND_HALF_UP))
        if tax_class.tax2_id:
            r = tax_class.tax2.rate_on(on_date)
            base = taxable + out["tax1_amount"] if tax_class.tax2_compounds_on_tax1 else taxable
            out.update(tax2_type=tax_class.tax2, tax2_rate=r, tax2_amount=(base * r / 100).quantize(Q4, ROUND_HALF_UP))
        return out

    @staticmethod
    def resolve_class(variant, partner=None):
        """Partner override wins over the product's class; falls back to the tenant default."""
        if partner is not None and getattr(partner, "tax_class_override_id", None):
            return partner.tax_class_override
        if variant.product.tax_class_id:
            return variant.product.tax_class
        return TaxClass.objects.filter(is_default=True).first()


class LabelService:
    SIZES = {"38x25": (38, 25), "50x30": (50, 30), "100x50": (100, 50)}

    @staticmethod
    def render_pdf(items, size="50x30", template="product"):
        """items: list of dicts {code, title, subtitle, price, batch, expiry}. Returns PDF bytes."""
        import base64
        import io

        import barcode as pybarcode
        from barcode.writer import ImageWriter
        from django.template.loader import render_to_string
        from weasyprint import HTML

        w, h = LabelService.SIZES.get(size, (50, 30))
        for it in items:
            code = it["code"]
            try:
                cls = pybarcode.get_barcode_class("ean13") if (len(code) in (12, 13) and code.isdigit()) else pybarcode.get_barcode_class("code128")
                buf = io.BytesIO()
                cls(code, writer=ImageWriter()).write(buf, options={"write_text": False, "module_height": 8.0, "quiet_zone": 1.0})
                it["img"] = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
            except Exception:
                it["img"] = ""
        html = render_to_string("catalog/labels.html", {"items": items, "w": w, "h": h, "template": template})
        return HTML(string=html).write_pdf()
