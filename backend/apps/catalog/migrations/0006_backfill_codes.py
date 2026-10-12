"""
Fill the new code fields from what each product already carries, so nothing looks empty on day one:

* `item_code` ← the old `sku` (the price-list code, e.g. Keune's "62001", or "CM-1.0-60" in the demo data)
* `upc` ← the product's existing primary unit barcode, where it has one
* `box_upc` ← an existing barcode attached to the 'carton' packaging, where it has one
"""
from django.db import migrations


def backfill(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    Barcode = apps.get_model("catalog", "Barcode")
    for product in Product.objects.all().iterator():
        changed = []
        if not product.item_code and product.sku:
            product.item_code = product.sku[:60]
            changed.append("item_code")
        if not product.upc:
            unit = Barcode.objects.filter(variant__product=product, packaging__isnull=True).order_by("-is_primary").first()
            if unit:
                product.upc = unit.code[:64]
                changed.append("upc")
        if not product.box_upc:
            box = Barcode.objects.filter(variant__product=product, packaging__name="carton").first()
            if box:
                product.box_upc = box.code[:64]
                changed.append("box_upc")
        if changed:
            product.save(update_fields=changed)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0005_product_box_upc_product_item_code_product_upc_and_more")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
