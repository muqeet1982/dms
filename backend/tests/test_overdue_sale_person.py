"""Overdue invoices names who sold each one (owner, 26 Nov 2026: "Sale Person column require in Overdue Invoices report")."""
from decimal import Decimal as D

import pytest
from django.utils import timezone

from apps.core.tenancy import tenant_context

from .conftest import issue_order, seed_basic

pytestmark = pytest.mark.django_db


def test_each_overdue_invoice_names_its_sale_person(api, tenant):
    from apps.partners.models import SupplierRep
    from apps.sales.models import SalesInvoice

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        rep = SupplierRep.objects.create(supplier=sup, code="R-9", name="Jamshaid Butt", phone="03001112222")
    o = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "rep": str(rep.id), "lines": [{"variant": str(v.id), "qty": "2"}]}, format="json").data
    issue_order(api, o["id"])
    inv = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": True}, format="json").data[0]
    with tenant_context(tenant, tenant.owner):
        SalesInvoice.objects.filter(pk=inv["id"]).update(rep=None, due_date=timezone.localdate() - timezone.timedelta(days=3))   # the rep on the order only
    data = api.get("/api/v1/reports/overdue_list").data
    assert data["columns"] == ["invoice", "date", "due_date", "days_overdue", "customer", "sale_person", "phone", "balance"]
    assert data["column_labels"]["sale_person"] == "Sale Person"
    row = data["rows"][0]
    assert (row["invoice"], row["sale_person"], row["days_overdue"], D(row["balance"])) == (inv["number"], "Jamshaid Butt", 3, D(inv["total"]))
    for fmt in ("csv", "xlsx", "pdf"):
        assert api.get("/api/v1/reports/overdue_list", {"format": fmt}).status_code == 200
