import decimal
import uuid
from datetime import date, datetime

from django.db.models import Model
from django.forms.models import model_to_dict

from .models import AuditLog
from .tenancy import get_current_tenant_id, get_current_user

_request_meta = {}


def _jsonable(value):
    if isinstance(value, (decimal.Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Model):
        return str(value.pk)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def snapshot(instance, fields=None):
    if instance is None:
        return None
    data = model_to_dict(instance, fields=fields)
    data.pop("tenant", None)
    return _jsonable(data)


class AuditService:
    @staticmethod
    def log(action, instance=None, before=None, after=None, model=None, object_id=None, request=None, tenant_id=None, actor=None):
        user = actor or get_current_user()
        tenant_id = tenant_id or get_current_tenant_id() or (getattr(instance, "tenant_id", None) if instance is not None else None)
        AuditLog.objects.create(
            tenant_id=tenant_id,
            actor=user if user is not None and getattr(user, "is_authenticated", False) else None,
            action=action,
            model=model or (instance.__class__.__name__ if instance is not None else ""),
            object_id=str(object_id or (instance.pk if instance is not None else "")),
            object_repr=str(instance)[:200] if instance is not None else "",
            before=_jsonable(before),
            after=_jsonable(after),
            ip=getattr(request, "client_ip", None) if request else None,
            user_agent=getattr(request, "user_agent", "") if request else "",
        )
