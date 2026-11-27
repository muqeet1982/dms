"""
Start trips and Close trips become two switches (owner, 27 Nov 2026: a Delivery Rider starts the trip from the phone; the
close — returns back on the shelf, cash counted — stays with the office). Every role that could start (and so close) a trip
keeps both, so nobody loses anything; only the Delivery Rider role, made the same day, starts without closing.
"""
from django.db import migrations


def split(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    GroupProfile = apps.get_model("accounts", "GroupProfile")
    ct = ContentType.objects.filter(app_label="core", model="module").first()   # apps.core.models.Module
    if ct is None:
        return
    close, _ = Permission.objects.get_or_create(content_type=ct, codename="delivery.close_trip", defaults={"name": "Close trips (returns back, cash counted)"})
    start = Permission.objects.filter(content_type=ct, codename="delivery.start_trip").first()
    if start is None:
        return
    for profile in GroupProfile.objects.filter(group__permissions=start).exclude(name="Delivery Rider").select_related("group"):
        profile.group.permissions.add(close)


def unsplit(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Permission.objects.filter(codename="delivery.close_trip").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0012_membership_on_crew_lists"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(split, unsplit)]
