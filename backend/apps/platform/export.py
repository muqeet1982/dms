"""Full tenant data export (JSON per model inside a zip)."""
import io
import json
import zipfile

from django.apps import apps
from django.core.serializers.json import DjangoJSONEncoder
from django.forms.models import model_to_dict

from apps.core.models import TenantModel


def export_tenant_zip(tenant):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for model in apps.get_models():
            if not issubclass(model, TenantModel):
                continue
            rows = [model_to_dict(o) for o in model.all_objects.filter(tenant=tenant)]
            if rows:
                zf.writestr(f"{model._meta.app_label}.{model.__name__}.json", json.dumps(rows, cls=DjangoJSONEncoder, indent=1, default=str))
        zf.writestr("tenant.json", json.dumps(model_to_dict(tenant), cls=DjangoJSONEncoder, indent=1, default=str))
    return buf.getvalue()
