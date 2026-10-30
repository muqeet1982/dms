"""
TDN, 30 Oct 2026: "reports view is on in role and all report but user not able to see them" — 48 of 64 reports
asked for the old coarse "view_operational_reports", which the role editor has no switch for. Reports → View now
opens them; the Financial switch opens the financial ones (whichever codename it stored).
"""
import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


def _client_with(api, tenant, email, perms):
    from apps.accounts.models import User

    role = api.post("/api/v1/roles", {"name": f"R {email}", "role_type": "internal", "permissions": perms}, format="json")
    assert role.status_code == 201, role.content
    made = api.post("/api/v1/users", {"full_name": email, "email": email, "password": "Abcd-1234!", "roles": [role.data["id"]]}, format="json")
    assert made.status_code == 201, made.content
    User.objects.filter(email=email).update(must_change_password=False)
    c = APIClient()
    tok = c.post("/api/v1/auth/login", {"identifier": email, "password": "Abcd-1234!"}, format="json").data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}", HTTP_X_TENANT_ID=str(tenant.id))
    return c


def test_the_reports_view_switch_opens_the_operational_reports_and_financial_needs_its_own(api, tenant):
    from apps.reports.registry import FIN, REPORTS

    ops = {k for k, v in REPORTS.items() if v["perm"] != FIN and v["perm"] != "inventory.view_valuation"}
    fin = {k for k, v in REPORTS.items() if v["perm"] == FIN}

    viewer = _client_with(api, tenant, "viewer@probe.pk", ["reports.view_report"])
    seen = {r["key"] for r in viewer.get("/api/v1/reports").data}
    assert ops <= seen and not (fin & seen), "View alone: every operational report, no financial one"
    assert viewer.get(f"/api/v1/reports/{sorted(ops)[0]}").status_code == 200
    assert viewer.get(f"/api/v1/reports/{sorted(fin)[0]}").status_code == 403

    for i, financial in enumerate(("reports.view_financial_reports", "accounting.view_financial_reports")):
        c = _client_with(api, tenant, f"fin{i}@probe.pk", ["reports.view_report", financial])
        assert fin <= {r["key"] for r in c.get("/api/v1/reports").data}, f"the Financial switch as {financial}"
