from decimal import Decimal

from django.db import migrations

# total%, distributor%, company% — the table the owner provided
DEFAULT_ROWS = [(3, 2, 1), (4, 2, 2), (5, 3, 2), (6, 3, 3), (7, 3, 4)]


def seed(apps, schema_editor):
    DiscountContribution = apps.get_model("sales", "DiscountContribution")
    Tenant = apps.get_model("platform", "Tenant")
    for tenant in Tenant.objects.all():
        if DiscountContribution.objects.filter(tenant=tenant).exists():
            continue
        for total, dist, comp in DEFAULT_ROWS:
            DiscountContribution.objects.create(
                tenant=tenant, total_pct=Decimal(total), distributor_pct=Decimal(dist),
                company_pct=Decimal(comp), is_active=True,
            )


def unseed(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0006_salesinvoice_discount_pct_companyclaim_and_more"),
        ("platform", "0001_initial"),
    ]
    operations = [migrations.RunPython(seed, unseed)]
