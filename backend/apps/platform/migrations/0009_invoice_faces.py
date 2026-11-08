from django.db import migrations, models


class Migration(migrations.Migration):
    """A sales tax invoice and a plain one each carry their own logo (owner, 8 Nov 2026). No company had set the single
    invoice logo of 6 Nov, so it simply becomes the plain invoice's."""

    dependencies = [("platform", "0008_tenant_invoice_logo")]

    operations = [
        migrations.RenameField("tenant", "invoice_logo", "plain_invoice_logo"),
        migrations.AddField("tenant", "tax_invoice_logo", models.ImageField(blank=True, null=True, upload_to="tenants/logos/")),
    ]
