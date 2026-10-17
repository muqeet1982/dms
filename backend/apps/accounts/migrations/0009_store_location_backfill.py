"""
The Store locations map used to ride on the plain customer switches; it now has its own page in the matrix
(`customers.view_store_location` / `customers.change_store_location`). Roles that could view/edit customers
get the new switches once, so nobody loses the map — the owner can then turn it off per role.
Same backfill mechanism as 0007/0008, re-run for the entries added since.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    from apps.accounts.page_permissions import BACKFILL

    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    ct = ContentType.objects.filter(model="module").first()
    if ct is None:
        return
    for old, new in BACKFILL.items():
        groups = list(Group.objects.filter(permissions__codename=old, permissions__content_type=ct))
        if not groups:
            continue
        perms = [Permission.objects.get_or_create(content_type=ct, codename=c, defaults={"name": c})[0] for c in new]
        for group in groups:
            group.permissions.add(*perms)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0008_journal_switch_backfill")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
