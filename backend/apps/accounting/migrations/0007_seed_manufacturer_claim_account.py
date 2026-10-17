from django.db import migrations


def seed(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    PostingRule = apps.get_model("accounting", "PostingRule")
    Tenant = apps.get_model("platform", "Tenant")
    CODE = "1-01-003-0003"
    for tenant in Tenant.objects.all():
        # only tenants whose chart of accounts has been seeded
        parent = Account.objects.filter(tenant=tenant, code="1-01-003").first()
        if parent is None:
            continue
        acct = Account.objects.filter(tenant=tenant, code=CODE).first()
        if acct is None:
            acct = Account.objects.create(
                tenant=tenant, code=CODE, name="Manufacturer Claims Receivable", level=4, parent=parent,
                type="asset", subtype="receivable", is_group=False, is_system=True,
                currency=getattr(tenant, "base_currency", "PKR"), allow_manual_posting=True,
            )
        if not PostingRule.objects.filter(tenant=tenant, role="manufacturer_claim", document_type="*").exists():
            PostingRule.objects.create(tenant=tenant, role="manufacturer_claim", document_type="*", account=acct)


def unseed(apps, schema_editor):
    PostingRule = apps.get_model("accounting", "PostingRule")
    PostingRule.objects.filter(role="manufacturer_claim").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0006_alter_postingrule_role"),
        ("platform", "0001_initial"),
    ]
    operations = [migrations.RunPython(seed, unseed)]
