from django.db import migrations


def derive(name, taken):
    words = [w for w in "".join(c if c.isalnum() or c.isspace() else " " for c in (name or "")).split() if w]
    code = ("".join(w[0] for w in words)[:4] if len(words) >= 2 else (words[0][:4] if words else "CO")).upper()
    base, n = code, 2
    while code in taken:
        code = f"{base}{n}"
        n += 1
    return code


def seed(apps, schema_editor):
    Tenant = apps.get_model("platform", "Tenant")
    taken = set()
    for t in Tenant.objects.order_by("created_at" if any(f.name == "created_at" for f in Tenant._meta.fields) else "slug"):
        if not t.doc_code:
            t.doc_code = derive(t.display_name or t.slug, taken)
            t.save(update_fields=["doc_code"])
        taken.add(t.doc_code)


class Migration(migrations.Migration):
    dependencies = [("platform", "0005_tenant_doc_code")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
