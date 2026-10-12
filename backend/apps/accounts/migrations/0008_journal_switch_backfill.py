"""
`accounting.add_journal` used to cover editing and deleting a draft journal as well. Those are their own switches
now, so roles that held it get them once — the same backfill as 0007, re-run for the entries added since.
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
    dependencies = [("accounts", "0007_page_permission_backfill")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
