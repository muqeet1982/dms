from django.db import migrations


def seed(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    PostingRule = apps.get_model("accounting", "PostingRule")
    Tenant = apps.get_model("platform", "Tenant")
    CODE = "4-02-001-0001"  # "Scheme / Incentive from Manufacturer" (income), already in the COA
    for tenant in Tenant.objects.all():
        acct = Account.objects.filter(tenant=tenant, code=CODE).first()
        if acct is None:
            continue
        if not PostingRule.objects.filter(tenant=tenant, role="manufacturer_incentive", document_type="*").exists():
            PostingRule.objects.create(tenant=tenant, role="manufacturer_incentive", document_type="*", account=acct)


def unseed(apps, schema_editor):
    PostingRule = apps.get_model("accounting", "PostingRule")
    PostingRule.objects.filter(role="manufacturer_incentive").delete()


class Migration(migrations.Migration):
    dependencies = [("accounting", "0008_alter_postingrule_role")]
    operations = [migrations.RunPython(seed, unseed)]
