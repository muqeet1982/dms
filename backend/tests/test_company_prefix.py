"""
Every document number carries its company's own code (TDN-SO-00001), and every company counts from 00001
independently — numbers can never be confused between companies in the one SaaS database.
"""
import pytest

pytestmark = pytest.mark.django_db


def test_each_company_counts_its_own_numbers(tenant, tenant_b):
    from apps.core.numbering import derived_number, next_number
    from apps.core.tenancy import tenant_context
    from apps.sales.models import SalesInvoice

    assert tenant.doc_code and tenant_b.doc_code and tenant.doc_code != tenant_b.doc_code
    with tenant_context(tenant, tenant.owner):
        a1 = next_number("sales_order")
        a2 = next_number("sales_order")
        inv = derived_number(a1, "sales_invoice", SalesInvoice)
    with tenant_context(tenant_b, tenant_b.owner):
        b1 = next_number("sales_order")
    assert a1 == f"{tenant.doc_code}-SO-00001", a1
    assert a2 == f"{tenant.doc_code}-SO-00002", a2
    assert b1 == f"{tenant_b.doc_code}-SO-00001", b1   # the other company starts at 00001 too
    assert inv == f"{tenant.doc_code}-INV-00001", inv  # the chain carries the company code and the counter
