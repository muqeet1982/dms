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
            rows = services.uoms_for_hs(parent)   # empty for a 01–97 code = the code is wrong, and the UI says so
        else:
            qs = FBRLookup.objects.filter(kind=kind)
            if q:
                qs = qs.filter(description__icontains=q) | qs.filter(key__icontains=q)
            rows = list(qs[:60])
        return Response({"results": [{"key": r.key, "description": r.description, "meta": r.meta} for r in rows]})


class FBRCheckRegistrationView(APIView):
    permission_classes = [IsTenantUser]

    def post(self, request):
        from django.utils import timezone

        from apps.partners.models import Customer

        data = services.check_registration(request.data.get("registration_no") or "")
        raw = (data.get("REGISTRATION_TYPE") or "").strip()
        normalized = "Registered" if raw.lower() == "registered" else ("Unregistered" if raw else "")
        # given a customer, FBR's answer is written onto it with the check time — never typed, never guessed
        if request.data.get("customer_id") and normalized:
            c = Customer.objects.filter(pk=request.data["customer_id"]).first()
            if c is not None:
                c.fbr_registration_type = normalized
                c.fbr_reg_checked_at = timezone.now()
                c.save(update_fields=["fbr_registration_type", "fbr_reg_checked_at", "updated_at"])
                AuditService.log("fbr.reg_checked", c, after={"status": raw}, request=request)
        return Response({"registration_type": normalized, "raw_status": raw})


class FBRScenarioView(APIView):
    """The §10 scenario-test screen: list templates with their latest run, run one, paste a template."""

    permission_classes = [IsTenantUser]

    def get(self, request):
        from .models import FBRScenarioRun, FBRScenarioTemplate

        services.seed_scenarios()
        latest = {}
        for r in FBRScenarioRun.objects.order_by("scenario_id", "-created_at"):
            latest.setdefault(r.scenario_id, r)
        known = {
            "SN001": "Goods at standard rate — Registered", "SN002": "Goods at standard rate — Unregistered",
            "SN003": "Steel melting and re-rolling", "SN004": "Ship breaking", "SN005": "Goods at Reduced Rate",
            "SN006": "Exempt goods", "SN007": "Goods at zero-rate", "SN008": "3rd Schedule Goods (MRP)",
            "SN009": "Cotton ginners", "SN010": "Telecommunication services", "SN011": "Toll Manufacturing (blocked by FBR)",
            "SN012": "Petroleum Products (blocked by FBR)", "SN013": "Electricity to retailers", "SN014": "Gas to CNG stations",
            "SN015": "Mobile Phones", "SN016": "Processing/Conversion", "SN017": "Goods (FED in ST mode)",
            "SN018": "Services (FED in ST mode)", "SN019": "Services", "SN020": "Electric Vehicle",
            "SN021": "Cement / Concrete Block", "SN022": "Potassium Chlorate", "SN023": "CNG Sales",
            "SN024": "Goods per SRO.297(I)/2023", "SN025": "Non-Adjustable Supplies", "SN026": "Standard rate (retail)",
            "SN027": "3rd Schedule (retail)", "SN028": "Reduced Rate (retail)",
        }
        templates = {t.scenario_id: t for t in FBRScenarioTemplate.objects.all()}
        rows = []
        for sid in sorted(set(known) | set(templates)):
            run = latest.get(sid)
            rows.append({"scenario_id": sid, "description": (templates.get(sid).description if sid in templates else "") or known.get(sid, ""),
                         "has_template": sid in templates,
                         "last": None if run is None else {"passed": run.passed, "at": run.created_at,
                                                           "code": run.status_code, "error": run.error_text}})
        return Response({"results": rows})

    def post(self, request):
        _can_manage(request)
        from .models import FBRScenarioTemplate

        d = request.data
        if d.get("template") is not None:
            sid = (d.get("scenario_id") or "").strip().upper()
            if not sid.startswith("SN"):
                raise DomainError("Give the scenario id (SN001…SN028) with the pasted template.")
            FBRScenarioTemplate.objects.update_or_create(
                scenario_id=sid, defaults={"payload": d["template"], "description": d.get("description", "")})
            return Response({"stored": sid})
        run = services.run_scenario((d.get("scenario_id") or "").strip().upper(), actor=request.user)
        return Response({"scenario_id": run.scenario_id, "passed": run.passed, "code": run.status_code,
                         "error": run.error_text, "response": run.response[:2000]})
