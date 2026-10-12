"""RFC 7807 style error responses: {type, title, status, detail, errors}."""
import re

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_handler


class DomainError(exceptions.APIException):
    """Business-rule violation raised by services."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "Business rule violated."
    default_code = "domain_error"

    def __init__(self, detail=None, code=None, errors=None, status_code=None):
        super().__init__(detail, code)
        self.errors = errors or {}
        if status_code:
            self.status_code = status_code


class ConflictError(DomainError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The document was modified by someone else. Reload and try again."
    default_code = "conflict"


class PlanLimitError(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Plan limit reached."
    default_code = "plan_limit"


class FeatureDisabledError(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "This feature is not enabled for your plan."
    default_code = "feature_disabled"


def _flatten(detail):
    if isinstance(detail, dict):
        out = {}
        for k, v in detail.items():
            if isinstance(v, (list, tuple)):
                out[k] = [str(x) if not isinstance(x, dict) else _flatten(x) for x in v]
            elif isinstance(v, dict):
                out[k] = _flatten(v)
            else:
                out[k] = [str(v)]
        return out
    if isinstance(detail, (list, tuple)):
        return {"non_field_errors": [str(x) for x in detail]}
    return {"non_field_errors": [str(detail)]}


FRIENDLY_FIELDS = {"sku": "code", "code": "code", "number": "number", "name": "name", "email": "email", "phone": "phone", "username": "username", "slug": "slug", "barcode": "barcode"}


def _integrity_error(exc):
    """Turn a database constraint violation into something a user can act on (and a field error when we can name it)."""
    text = str(exc)
    dup = re.search(r"Key \(([^)]+)\)=\(([^)]*)\) already exists", text)
    if dup or "duplicate key value" in text:
        cols = [c.strip() for c in (dup.group(1).split(",") if dup else []) if c.strip() not in ("tenant_id", "deleted_at")]
        value = (dup.group(2).split(",")[-1].strip() if dup else "").strip()
        field = cols[-1] if cols else ""
        label = FRIENDLY_FIELDS.get(field, field.replace("_", " ") or "value")
        detail = f"This {label}{f' ({value})' if value and len(value) < 40 else ''} is already used by another record — enter a different one."
        return DomainError(detail=detail, code="duplicate", errors={field: [detail]} if field else {}, status_code=400)
    if "violates foreign key constraint" in text:
        return DomainError(detail="This record is still linked to other records, so it cannot be changed or removed.", code="in_use", status_code=409)
    if "not-null constraint" in text:
        m = re.search(r'column "([^"]+)"', text)
        f = (m.group(1) if m else "").replace("_id", "").replace("_", " ")
        return DomainError(detail=f"{f.capitalize() or 'A required value'} is required.", code="required", status_code=400)
    return DomainError(detail=f"Database constraint violated: {text.splitlines()[0][:200]}", code="integrity")


def problem_exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(detail=exc.message_dict if hasattr(exc, "message_dict") else exc.messages)
    elif isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()
    elif isinstance(exc, IntegrityError):
        exc = _integrity_error(exc)

    response = drf_handler(exc, context)
    if response is None:
        return None

    code = getattr(exc, "default_code", "error")
    if isinstance(exc, exceptions.ValidationError):
        errors = _flatten(exc.detail)
        body = {
            "type": "validation_error",
            "title": "Validation failed",
            "status": response.status_code,
            "detail": "One or more fields are invalid.",
            "errors": errors,
        }
    else:
        detail = exc.detail if hasattr(exc, "detail") else str(exc)
        if isinstance(detail, (dict, list)):
            detail_text = "; ".join(str(v) for v in (detail.values() if isinstance(detail, dict) else detail))
        else:
            detail_text = str(detail)
        # DomainError(code="used") → "type": "used" so clients can branch on it (default_code otherwise)
        specific = getattr(detail, "code", None) if not isinstance(detail, (dict, list)) else None
        body = {
            "type": str(specific or code),
            "title": exc.__class__.__name__.replace("Error", " error").replace("Exception", ""),
            "status": response.status_code,
            "detail": detail_text,
            "errors": getattr(exc, "errors", {}) or {},
        }
    response.data = body
    return response
