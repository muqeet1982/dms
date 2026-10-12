"""
Roles saved before the page matrix hold coarse codenames. Four of them covered pages that now have a switch of
their own (the stock ledger, batches and alerts came with "view stock"; the orders inbox with sales orders; credit
notes with invoices; purchase returns with purchase orders). Give those roles the new codenames once, so nobody
loses a page the day the matrix arrives. New roles are built from the matrix and are unaffected.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    from apps.accounts.page_permissions import BACKFILL

    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    Module = apps.get_model("core", "Module")
    ct = ContentType.objects.filter(app_label=Module._meta.app_label, model="module").first()
    if ct is None:
        return
    for old, new in BACKFILL.items():
        groups = list(Group.objects.filter(permissions__codename=old, permissions__content_type=ct))
        if not groups:
            continue
        perms = []
        for codename in new:
            perm, _ = Permission.objects.get_or_create(content_type=ct, codename=codename, defaults={"name": codename})
            perms.append(perm)
        for group in groups:
            group.permissions.add(*perms)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0006_usersession_prev_refresh_jti_usersession_rotated_at"), ("core", "0001_initial")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
