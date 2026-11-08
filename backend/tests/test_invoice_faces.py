"""
A sales tax invoice and a plain one each say who they are from (owner, 8 Nov 2026: "for tax invoice i want to show different
company name, address logo and bank detail and for non tax invoice different"), set in Settings, Company, Invoices; a blank
field prints the company profile's. Also on the printed invoice (owner, 8 Nov 2026): a sales tax invoice shows no order or
delivery note number, as those run in other series; the NTN is set apart on every sales tax invoice; no Prepared, Checked
or Approved By on any invoice.
"""
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.tenancy import tenant_context

from .test_invoice_tax import _issued_order, _product_fbr, taxed_world  # noqa: F401  (taxed_world is a fixture)

pytestmark = pytest.mark.django_db
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture
def printed(api, monkeypatch):
    """The HTML a printed document was rendered from."""
    from django.template import loader

    real = loader.render_to_string

    def get(path):
        seen = {}

        def capture(template, context=None, *a, **k):
            seen["html"] = real(template, context, *a, **k)
            return seen["html"]

        monkeypatch.setattr(loader, "render_to_string", capture)
        r = api.get(path)
        monkeypatch.setattr(loader, "render_to_string", real)
        assert r.status_code == 200, (path, r.content[:200])
        return seen["html"]

    return get


@pytest.fixture
def two(api, tenant, taxed_world):
    """A posted sales tax invoice and a posted plain one."""
    wh, cust, v, _tt = taxed_world
    _product_fbr(tenant, v, "18%")
    with tenant_context(tenant, tenant.owner):
        tenant.tax_numbers = {"NTN": "J833632-7", "STRN": "", "PRA": "P-77"}
        tenant.address, tenant.phone = "Profile Road, Lahore", "042-111"
        tenant.settings = {**(tenant.settings or {}), "bank_details": "Profile Bank 0001"}
        tenant.save(update_fields=["tax_numbers", "address", "phone", "settings"])
    taxed = api.post(f"/api/v1/sales-orders/{_issued_order(api, wh, cust, v)}/invoice", {"apply_tax": True, "post": True}, format="json").data
    plain = api.post(f"/api/v1/sales-orders/{_issued_order(api, wh, cust, v)}/invoice", {"post": True}, format="json").data
    taxed, plain = (taxed[0] if isinstance(taxed, list) else taxed), (plain[0] if isinstance(plain, list) else plain)
    assert Decimal(taxed["tax1_total"]) > 0 and Decimal(plain["tax1_total"]) == 0
    return taxed, plain


def test_each_invoice_prints_its_own_header(api, tenant, two, printed):
    taxed, plain = two
    r = api.patch("/api/v1/settings/company", {"settings": {
        "tax_invoice_name": "Trafco Distribution (Private) Limited", "tax_invoice_address": "Trafco House, Lahore", "tax_invoice_ntn": "J833632-7",
        "tax_invoice_bank_details": "Meezan 0101-TAX",
        "plain_invoice_name": "TDN Trading", "plain_invoice_address": "Shop 4, Hall Road", "plain_invoice_bank_details": "HBL 2020-PLAIN"}}, format="json")
    assert r.status_code == 200 and r.data["settings"]["tax_invoice_name"] == "Trafco Distribution (Private) Limited"
    t = printed(f"/api/v1/invoices/{taxed['id']}/pdf")
    assert '<div class="co">Trafco Distribution (Private) Limited</div>' in t and "Trafco House, Lahore" in t and "Meezan 0101-TAX" in t
    assert "TDN Trading" not in t and "HBL 2020-PLAIN" not in t and "Profile Road" not in t
    p = printed(f"/api/v1/invoices/{plain['id']}/pdf")
    assert '<div class="co">TDN Trading</div>' in p and "Shop 4, Hall Road" in p and "HBL 2020-PLAIN" in p
    assert "Trafco Distribution (Private) Limited" not in p and "Meezan 0101-TAX" not in p
    assert "Tel 042-111" in p, "a field left blank prints the company profile's"


def test_left_blank_both_print_the_company_profile(two, printed, tenant):
    taxed, plain = two
    for inv in two:
        html = printed(f"/api/v1/invoices/{inv['id']}/pdf")
        assert f'<div class="co">{tenant.display_name}</div>' in html and "Profile Road, Lahore" in html and "Profile Bank 0001" in html


def test_a_sales_tax_invoice_sets_its_ntn_apart_and_drops_the_order_and_delivery_numbers(two, printed):
    taxed, plain = two
    t = printed(f"/api/v1/invoices/{taxed['id']}/pdf")
    assert '<div class="ntn">NTN: J833632-7</div>' in t and '<div class="ntn">PRA: P-77</div>' in t
    assert "<h1>SALES TAX INVOICE</h1>" in t and "<h1>SALES INVOICE</h1>" in printed(f"/api/v1/invoices/{plain['id']}/pdf")
    assert '<span class="k">Order</span>' not in t and '<span class="k">Delivery note</span>' not in t
    assert taxed["order_number"] not in t, "no order number anywhere on it"
    assert "FBR: " not in t, "nor the buyer's FBR registration status (owner, 8 Nov 2026)"
    p = printed(f"/api/v1/invoices/{plain['id']}/pdf")
    assert '<span class="k">Order</span>' in p and '<span class="k">Delivery note</span>' in p, "a plain invoice keeps them"
    assert "<br>NTN: J833632-7" in p and 'class="ntn"' not in p


def test_a_sales_tax_invoice_always_has_an_ntn(api, tenant, two, printed):
    from apps.fbr.models import FBRSettings

    taxed, _plain = two
    with tenant_context(tenant, tenant.owner):
        tenant.tax_numbers = {}
        tenant.save(update_fields=["tax_numbers"])
        FBRSettings.objects.update_or_create(defaults={"ntn": "FBR-123", "business_name": "Registered Co"})
    assert '<div class="ntn">NTN: FBR-123</div>' in printed(f"/api/v1/invoices/{taxed['id']}/pdf"), "the FBR registration's, when the profile has none"
    api.patch("/api/v1/settings/company", {"settings": {"tax_invoice_ntn": "OWN-9", "tax_invoice_strn": "32-77"}}, format="json")
    t = printed(f"/api/v1/invoices/{taxed['id']}/pdf")
    assert '<div class="ntn">NTN: OWN-9</div>' in t and '<div class="ntn">STRN: 32-77</div>' in t


def test_no_signature_boxes_on_an_invoice_but_an_order_keeps_them(two, printed):
    taxed, plain = two
    for inv in two:
        html = printed(f"/api/v1/invoices/{inv['id']}/pdf")
        assert "Prepared By" not in html and "Checked By" not in html and "Approved By" not in html
    assert "Prepared By" in printed(f"/api/v1/sales-orders/{plain['order']}/pdf")


def test_each_invoice_prints_its_own_logo(api, two, printed, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    taxed, plain = two
    up = lambda kind, name: api.post("/api/v1/settings/company/logo", {"kind": kind, "file": SimpleUploadedFile(name, PNG, content_type="image/png")}, format="multipart")
    assert up("company", "company.png").status_code == 200
    r = up("tax_invoice", "taxface.png")
    assert r.status_code == 200 and "taxface" in r.data["tax_invoice_logo"] and r.data["plain_invoice_logo"] is None
    assert "taxface" in printed(f"/api/v1/invoices/{taxed['id']}/pdf")
    assert "company" in printed(f"/api/v1/invoices/{plain['id']}/pdf"), "no plain logo yet: the company's"
    assert up("plain_invoice", "plainface.png").status_code == 200
    assert "plainface" in printed(f"/api/v1/invoices/{plain['id']}/pdf") and "taxface" in printed(f"/api/v1/invoices/{taxed['id']}/pdf")
    assert "company" in printed(f"/api/v1/sales-orders/{plain['order']}/pdf"), "an order keeps the company logo"
    off = api.post("/api/v1/settings/company/logo", {"kind": "tax_invoice", "clear": "1"}, format="multipart")
    assert off.status_code == 200 and off.data["tax_invoice_logo"] is None and "plainface" in off.data["plain_invoice_logo"]
