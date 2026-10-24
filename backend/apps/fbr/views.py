"""FBR endpoints: settings, reference sync, the chained dropdown feeds, and registration checks."""
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsTenantUser
from apps.core.audit import AuditService
from apps.core.exceptions import DomainError

from . import services
from .models import FBRLookup

MASK = "••••••••"


def _can_manage(request):
    m = request.membership
    if not (m.is_owner or m.has_perm("settings.change_setting")):
        raise DomainError("Only the owner can change FBR settings.", status_code=403)


class FBRSettingsView(APIView):
    permission_classes = [IsTenantUser]

    def get(self, request):
        f = services.settings_for()
        return Response({
            "ntn": f.ntn, "cnic": f.cnic, "ntn_verified": f.ntn_verified, "ntn_reg_status": f.ntn_reg_status,
            "business_name": f.business_name, "address": f.address, "province": f.province, "pos_id": f.pos_id,
            "use_sandbox": f.use_sandbox,
            # the tokens never travel back to a browser — only whether each exists
            "has_sandbox_token": bool(f.sandbox_token), "has_production_token": bool(f.production_token),
            "lookups": {k: FBRLookup.objects.filter(kind=k).count() for k in ("hscode", "transtype", "uom", "sroitem", "province")},
        })

    def patch(self, request):
        _can_manage(request)
        f = services.settings_for()
        d = request.data
        for field in ("ntn", "cnic", "business_name", "address", "province", "pos_id"):
            if field in d:
                setattr(f, field, (d.get(field) or "").strip())
        if "use_sandbox" in d:
            f.use_sandbox = bool(d["use_sandbox"])
        # an empty or masked token field NEVER wipes a stored token (guide §3)
        for field in ("sandbox_token", "production_token"):
            v = (d.get(field) or "").strip()
            if v and v != MASK:
                setattr(f, field, v)
        f.save()
        AuditService.log("fbr.settings", f, after={"use_sandbox": f.use_sandbox}, request=request)
        return self.get(request)


class FBRSyncView(APIView):
    permission_classes = [IsTenantUser]

    def post(self, request):
        _can_manage(request)
        done = services.sync_reference(kinds=request.data.get("kinds"))
        return Response({"synced": done})


class FBRLookupView(APIView):
    """The product form's chained dropdowns read from the cache; parametric kinds sync lazily."""

    permission_classes = [IsTenantUser]

    def get(self, request):
        kind = request.query_params.get("kind") or ""
        parent = request.query_params.get("parent") or ""
        q = (request.query_params.get("q") or "").strip()
        if kind == "rate" and parent:
            rows = services.rates_for(parent)
        elif kind == "sroschedule" and parent:
            rows = services.schedules_for(parent)
        elif kind == "hs_uom" and parent:
            rows = services.uoms_for_hs(parent) or list(FBRLookup.objects.filter(kind="uom"))
        else:
            qs = FBRLookup.objects.filter(kind=kind)
            if q:
                qs = qs.filter(description__icontains=q) | qs.filter(key__icontains=q)
            rows = list(qs[:60])
        return Response({"results": [{"key": r.key, "description": r.description, "meta": r.meta} for r in rows]})


class FBRCheckRegistrationView(APIView):
    permission_classes = [IsTenantUser]

    def post(self, request):
        data = services.check_registration(request.data.get("registration_no") or "")
        return Response({"registration_type": data.get("REGISTRATION_TYPE", ""), "raw": data})
