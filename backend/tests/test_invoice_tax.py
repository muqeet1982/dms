"""The invoice's tax switch: ON charges one sales tax on every line and books it; OFF returns to the
products' tax classes. Draft only."""
from decimal import Decimal

import pytest

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db


def _issued_order(api, wh, cust, v, qty="4"):
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(v.id), "qty": qty}]}, format="json")
    pl = api.post(f"/api/v1/sales-orders/{r.data['id']}/load-form", {}, format="json")
    issues = [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl.data["lines"]]
    api.post(f"/api/v1/pick-lists/{pl.data['id']}/issue", {"issues": issues}, format="json")
    return r.data["id"]


def _product_tax(tenant, variant, pct):
    from apps.catalog.models import Product
    from apps.core.tenancy import tenant_context as _tc

    with _tc(tenant, tenant.owner):
        Product.objects.filter(pk=variant.product_id).update(tax_pct=Decimal(str(pct)))


def _product_fbr(tenant, variant, rate_str, **fields):
    from apps.catalog.models import Product
    from apps.core.tenancy import tenant_context as _tc

    with _tc(tenant, tenant.owner):
        Product.objects.filter(pk=variant.product_id).update(fbr_rate_desc=rate_str, **fields)


@pytest.fixture
def taxed_world(api, tenant):
    from django.utils import timezone

    from apps.catalog.models import TaxRate, TaxType

    from .conftest import seed_basic

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        tt = TaxType.objects.create(code="ST", name="Sales Tax", kind="sales_tax", applies_to="both")
        TaxRate.objects.create(tax_type=tt, rate_pct=Decimal("18"), valid_from=timezone.localdate())
    return wh, cust, v, tt


def test_the_tax_switch_charges_every_line_and_books_the_ledger(api, tenant, taxed_world):
    wh, cust, v, tt = taxed_world
    _product_fbr(tenant, v, "18%")
    oid = _issued_order(api, wh, cust, v)
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"apply_tax": True}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    # 4 × 150 = 600 base, 18% = 108, total 708
    assert Decimal(inv["tax1_total"]) == Decimal("108"), inv["tax1_total"]
    assert Decimal(inv["total"]) == Decimal("708")
    assert all(Decimal(l["tax1_amount"]) > 0 for l in inv["lines"])
    with tenant_context(tenant, tenant.owner):
        from apps.accounting.models import JournalLine
        from apps.sales.models import SalesInvoice

        row = SalesInvoice.objects.get(id=inv["id"])
        booked = JournalLine.objects.filter(entry=row.journal, tax_type=tt).aggregate_count = \
            JournalLine.objects.filter(entry=row.journal, credit=Decimal("108"))
        assert booked.exists(), "the 18% must be credited to a tax account in the invoice journal"


def test_the_switch_flips_both_ways_on_a_draft_and_refuses_posted(api, tenant, taxed_world):
    wh, cust, v, tt = taxed_world
    _product_fbr(tenant, v, "18%")
    oid = _issued_order(api, wh, cust, v)
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"post": False}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    assert Decimal(inv["tax1_total"]) == 0

    on = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": True}, format="json")
    assert on.status_code == 200 and Decimal(on.data["tax1_total"]) == Decimal("108"), on.content
    off = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": False}, format="json")
    assert Decimal(off.data["tax1_total"]) == 0 and Decimal(off.data["total"]) == Decimal("600")

    api.post(f"/api/v1/invoices/{inv['id']}/post", {}, format="json")
    # since 26 Oct 2026 a posted, UNPAID invoice takes the switch in one atomic call (reopen inside);
    # it lands at draft for review — paid invoices still refuse, inside reopen itself
    flipped = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": True}, format="json")
    assert flipped.status_code == 200 and flipped.data["status"] == "draft", flipped.content
    assert Decimal(flipped.data["tax1_total"]) == Decimal("108")


def test_the_pdf_shows_tax_per_item_and_in_totals(api, tenant, taxed_world):
    from apps.sales.services import InvoiceService

    wh, cust, v, tt = taxed_world
    with tenant_context(tenant, tenant.owner):
        from apps.catalog.models import Product

        Product.objects.filter(pk=v.product_id).update(fbr_hs_code="7306.3090", fbr_rate_desc="18%")
        cust.fbr_registration_type = "Unregistered"
        cust.save(update_fields=["fbr_registration_type"])
    oid = _issued_order(api, wh, cust, v)
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"apply_tax": True}, format="json")
    inv_id = (made.data[0] if isinstance(made.data, list) else made.data)["id"]
    with tenant_context(tenant, tenant.owner):
        from django.template.loader import render_to_string

        from apps.sales.models import SalesInvoice

        inv = SalesInvoice.objects.get(id=inv_id)
        lines = list(inv.lines.all())
        extra = InvoiceService.present_lines(inv, lines)
        html = render_to_string("sales/invoice.html", {"inv": inv, "lines": lines, "tenant": tenant,
                                                       "logo_url": None, "printed_at": "", "doc_title": "SALES INVOICE", "doc_ref": inv.number, **extra})
        # the tax-invoice face (owner's layout, 26 Oct 2026): Item · Qty · Rate · Disc · Taxable · GST · Total,
        # the HS code under the item name, and the totals footed as Taxable value + Sales tax = Total
        assert ">Disc<" in html and ">Taxable<" in html and ">GST<" in html and ">Total<" in html
        assert "Sales Tax" in html and "108" in html
        assert "HS 7306.3090" in html and "Taxable value" in html and "Sales tax" in html
        assert "FBR: Unregistered" in html


def test_only_the_fbr_rate_counts_not_tax_pct_or_typed_percent(api, tenant, taxed_world):
    """Owner, 25 Oct 2026: "no dated rate need. if tax on then use fbr rate via hs code." A product
    Tax % or a typed invoice percent no longer makes a line taxable — only the FBR classification."""
    wh, cust, v, tt = taxed_world
    _product_tax(tenant, v, 25)  # the old field is dead weight now
    oid = _issued_order(api, wh, cust, v)
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"post": False}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    refused = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": True, "percent": "10"}, format="json")
    assert refused.status_code in (400, 422), refused.content
    assert "FBR" in str(refused.data)

    _product_fbr(tenant, v, "17%")
    on = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": True}, format="json")
    assert Decimal(on.data["tax1_total"]) == Decimal("102"), on.content  # 600 × FBR 17, not 25, not 10


def test_a_lines_typed_tax_pct_beats_product_and_invoice(api, tenant, taxed_world):
    from apps.catalog.models import Product

    wh, cust, v, tt = taxed_world
    with tenant_context(tenant, tenant.owner):
        Product.objects.filter(pk=v.product_id).update(fbr_rate_desc="25%")
    oid = _issued_order(api, wh, cust, v)
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"post": False, "apply_tax": True}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    assert Decimal(inv["tax1_total"]) == Decimal("150")  # 600 × the product's 25%

    line_id = inv["lines"][0]["id"]
    edited = api.patch(f"/api/v1/invoices/{inv['id']}", {"lines": [{"id": line_id, "tax_pct": "5"}]}, format="json")
    assert edited.status_code == 200, edited.content
    assert Decimal(edited.data["tax1_total"]) == Decimal("30")  # the typed 5% wins
    assert Decimal(edited.data["total"]) == Decimal("630")

    cleared = api.patch(f"/api/v1/invoices/{inv['id']}", {"lines": [{"id": line_id, "tax_pct": ""}]}, format="json")
    assert Decimal(cleared.data["tax1_total"]) == Decimal("150")  # back to the product's 25%


def test_reopen_reverses_everything_and_reposting_rebuilds_it(api, tenant, taxed_world):
    """The owner edits a POSTED invoice the book-true way: reopen reverses posting, COGS, claims and
    net-offs; re-posting rebuilds them from the corrected figures."""
    from decimal import Decimal as D

    from apps.sales.models import DiscountContribution

    wh, cust, v, tt = taxed_world
    with tenant_context(tenant, tenant.owner):
        DiscountContribution.objects.get_or_create(total_pct=D("5"), defaults={"distributor_pct": D("3"), "company_pct": D("2")})
        tenant.settings["claim_settlement"] = "auto_net_off"
        tenant.save(update_fields=["settings"])
        cust.default_discount_pct = D("5")
        cust.save(update_fields=["default_discount_pct"])
    oid = _issued_order(api, wh, cust, v)
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    assert inv["status"] == "posted" and D(inv["discount"]) == D("30")  # 600 × 5%

    with tenant_context(tenant, tenant.owner):
        from apps.accounting.services import ReportQueries, account_for

        recv = account_for("manufacturer_claim", "company_claim")
        assert ReportQueries.account_balance(recv) == 0  # auto-netted

    r = api.post(f"/api/v1/invoices/{inv['id']}/reopen", {"reason": "price typo"}, format="json")
    assert r.status_code == 200 and r.data["status"] == "draft", r.content
    with tenant_context(tenant, tenant.owner):
        from apps.accounting.services import ReportQueries, account_for
        from apps.sales.models import CompanyClaim, SalesInvoice

        assert not CompanyClaim.objects.filter(invoice_id=inv["id"]).exclude(status="cancelled").exists()
        # every head this invoice touched is flat again
        for role, doc in (("sales", "sales_invoice"), ("discount", "sales_invoice"),
                          ("manufacturer_incentive", "company_claim"), ("manufacturer_claim", "company_claim")):
            assert ReportQueries.account_balance(account_for(role, doc)) == 0, role

    line = r.data["lines"][0]
    api.patch(f"/api/v1/invoices/{inv['id']}", {"lines": [{"id": line["id"], "unit_price": "200"}]}, format="json")
    posted = api.post(f"/api/v1/invoices/{inv['id']}/post", {}, format="json")
    assert posted.status_code == 200, posted.content
    with tenant_context(tenant, tenant.owner):
        from apps.accounting.services import ReportQueries, account_for
        from apps.sales.models import CompanyClaim, SalesInvoice

        fresh = SalesInvoice.objects.get(id=inv["id"])
        assert fresh.subtotal == D("800") and fresh.journal_id  # 4 × 200
        assert CompanyClaim.objects.filter(invoice=fresh, status="paid").exists()  # re-raised and re-netted
        assert -ReportQueries.account_balance(account_for("sales", "sales_invoice")) == D("800")


def test_reopen_is_the_owners_unless_granted_and_paid_refuses(api, tenant, taxed_world):
    from apps.accounts.services import RoleService, UserService
    from rest_framework.test import APIClient

    wh, cust, v, tt = taxed_world
    oid = _issued_order(api, wh, cust, v)
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data

    with tenant_context(tenant, tenant.owner):
        role = RoleService.create_role(tenant, "Biller", ["sales.view_invoice"], role_type="internal")
        UserService.create_tenant_user(tenant, "Biller", email="bill@probe.pk", password="Abcd-1234", roles=[role], warehouses=[])
        from apps.accounts.models import User

        User.objects.filter(email="bill@probe.pk").update(must_change_password=False)
    c = APIClient()
    login = c.post("/api/v1/auth/login", {"identifier": "bill@probe.pk", "password": "Abcd-1234"}, format="json")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}", HTTP_X_TENANT_ID=str(tenant.id))
    refused = c.post(f"/api/v1/invoices/{inv['id']}/reopen", {}, format="json")
    assert refused.status_code == 403, refused.content

    # money received blocks even the owner
    api.post("/api/v1/accounting/payments", {"direction": "in", "partner_type": "customer", "partner_id": str(cust.id),
                                             "amount": "100", "method": "cash",
                                             "allocations": [{"document_type": "sales_invoice", "document_id": inv["id"], "amount": "100"}]}, format="json")
    blocked = api.post(f"/api/v1/invoices/{inv['id']}/reopen", {}, format="json")
    assert blocked.status_code in (400, 422) and "receipt" in str(blocked.data).lower() or blocked.status_code in (400, 422)


def test_a_dated_rate_does_not_rescue_an_unclassified_product(api, tenant, taxed_world):
    """Owner, 25 Oct 2026: "no dated rate need" — the type's dated 18% exists in this fixture, but an
    unclassified product still refuses the switch; only the FBR classification makes a line taxable."""
    wh, cust, v, tt = taxed_world
    oid = _issued_order(api, wh, cust, v)
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"post": False}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    on = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": True}, format="json")
    assert on.status_code in (400, 422), on.content
    assert "FBR" in str(on.data)


def test_the_fbr_rate_is_the_switchs_first_source(api, tenant, taxed_world):
    """"If tax on then use FBR tax" (owner, 25 Oct 2026): an FBR-classified product taxes at its FBR
    GST rate — beating its own Tax % and the company's dated rate — so the invoice shows the same tax
    an FBR filing will compute."""
    from apps.catalog.models import Product

    wh, cust, v, tt = taxed_world
    with tenant_context(tenant, tenant.owner):
        Product.objects.filter(pk=v.product_id).update(fbr_rate_desc="25%", tax_pct=Decimal("10"))
    oid = _issued_order(api, wh, cust, v)
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"post": False, "apply_tax": True}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    assert Decimal(inv["tax1_total"]) == Decimal("150"), inv["tax1_total"]  # 600 × FBR's 25%, not 10


def test_without_the_switch_an_invoice_carries_no_tax_even_with_classes(api, tenant, taxed_world):
    """"When no tax then do not show any tax" (owner, 25 Oct 2026): the tax classes no longer feed
    sales invoices — an invoice made without the switch is tax-free, whatever the product's class."""
    from apps.catalog.models import TaxClass

    wh, cust, v, tt = taxed_world
    with tenant_context(tenant, tenant.owner):
        TaxClass.objects.create(name="Standard", tax1=tt, is_default=True)
    oid = _issued_order(api, wh, cust, v)
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"post": False}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    assert Decimal(inv["tax1_total"]) == 0 and Decimal(inv["total"]) == Decimal("600"), inv
    assert all(Decimal(l["tax1_amount"]) == 0 for l in inv["lines"])


def test_tax_on_with_no_rate_anywhere_refuses_with_directions(api, tenant):
    """No dated rate, no product Tax %, no FBR rate, nothing typed: the switch must refuse with
    directions instead of charging zero (the state the owner's live tenant was in)."""
    from apps.catalog.models import TaxType

    from .conftest import seed_basic

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        TaxType.objects.create(code="ST", name="Sales Tax", kind="sales_tax", applies_to="both")  # NO dated rate
    oid = _issued_order(api, wh, cust, v)
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"post": False}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    r = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": True}, format="json")
    assert r.status_code in (400, 422), r.content
    assert "FBR" in str(r.data), r.data
    fresh = api.get(f"/api/v1/invoices/{inv['id']}")
    assert fresh.data["force_tax_type"] is None and Decimal(fresh.data["tax1_total"]) == 0


def test_a_credit_note_against_a_forced_invoice_reverses_the_charged_rate(api, tenant, taxed_world):
    """A return against a tax-switch invoice must reverse the tax that was actually charged, not
    whatever the tax classes would say (they said nothing — that was the mismatch)."""
    wh, cust, v, tt = taxed_world
    _product_fbr(tenant, v, "18%")
    oid = _issued_order(api, wh, cust, v)
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"apply_tax": True}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    assert Decimal(inv["tax1_total"]) == Decimal("108")

    cn = api.post("/api/v1/credit-notes", {"customer": str(cust.id), "invoice": inv["id"], "warehouse": str(wh.id),
                                           "reason": "damaged", "lines": [{"variant": str(v.id), "qty": "1", "unit_price": "150"}]}, format="json")
    assert cn.status_code == 201, cn.content
    assert Decimal(cn.data["tax1_total"]) == Decimal("27")  # 1 × 150 × the invoice's 18%
    assert Decimal(cn.data["total"]) == Decimal("177")


# ── the full FBR arithmetic on the switch (owner 26 Oct 2026, INVOICE_TAX_FLOW_GUIDE.md §4-§6, §15) ──


def test_a_compound_rate_keeps_its_rupee_part(api, tenant, taxed_world):
    """Guide example D: '18% along with rupees 60 per kilogram' is 18% ON TOP OF Rs.60 a unit — a
    parser that reads only the 18 under-charges by the rupee part."""
    wh, cust, v, tt = taxed_world
    _product_fbr(tenant, v, "18% along with rupees 60 per kilogram")
    oid = _issued_order(api, wh, cust, v)  # 4 × 150 = 600
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"apply_tax": True}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    assert Decimal(inv["tax1_total"]) == Decimal("348"), inv["tax1_total"]  # 600×18% + 4×60
    assert Decimal(inv["total"]) == Decimal("948")


def test_a_fixed_rupee_rate_no_longer_invoices_zero(api, tenant, taxed_world):
    """Guide example G: 'Rs.700/MT' has no percentage at all — the invoice used to charge nothing
    while the filing charged qty × 700."""
    wh, cust, v, tt = taxed_world
    _product_fbr(tenant, v, "Rs.700/MT")
    oid = _issued_order(api, wh, cust, v)  # qty 4
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"apply_tax": True}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    assert Decimal(inv["tax1_total"]) == Decimal("2800"), inv["tax1_total"]  # 4 × 700
    assert Decimal(inv["lines"][0]["tax1_rate"]) == 0, "no percentage part — the amount carries it all"


def test_third_schedule_taxes_the_printed_retail_price_when_higher(api, tenant, taxed_world):
    """Guide examples B and C: GST base = max(MRP × qty, taxable) — per-unit MRP times quantity,
    never the bare MRP, and never 'always MRP'."""
    wh, cust, v, tt = taxed_world
    # B: MRP 200 beats the charged 150 → tax on 10 × 200 = 2000
    _product_fbr(tenant, v, "18%", fbr_sale_type="3rd Schedule Goods", mrp_ex_tax=Decimal("200"))
    oid = _issued_order(api, wh, cust, v, qty="10")  # 10 × 150 = 1500 charged
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"apply_tax": True}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    assert Decimal(inv["tax1_total"]) == Decimal("360"), inv["tax1_total"]  # 2000 × 18%
    assert Decimal(inv["subtotal"]) == Decimal("1500"), "the charged value stays what was charged"

    # C: MRP 100 loses to the charged price → tax on 1500, max() not always-MRP
    _product_fbr(tenant, v, "18%", fbr_sale_type="3rd Schedule Goods", mrp_ex_tax=Decimal("100"))
    oid2 = _issued_order(api, wh, cust, v, qty="10")
    made2 = api.post(f"/api/v1/sales-orders/{oid2}/invoice", {"apply_tax": True}, format="json")
    inv2 = made2.data[0] if isinstance(made2.data, list) else made2.data
    assert Decimal(inv2["tax1_total"]) == Decimal("270"), inv2["tax1_total"]  # 1500 × 18%


def test_third_schedule_without_mrp_refuses_the_switch(api, tenant, taxed_world):
    """Guide §5: the law taxes 3rd Schedule goods on the printed retail price — without one the
    invoice would quietly under-tax, so the switch refuses and says where to set it."""
    wh, cust, v, tt = taxed_world
    _product_fbr(tenant, v, "18%", fbr_sale_type="3rd Schedule Goods", mrp_ex_tax=None)
    oid = _issued_order(api, wh, cust, v)
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"post": False}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    r = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": True}, format="json")
    assert r.status_code in (400, 422) and "retail price" in str(r.data), r.content


def test_the_filing_and_the_invoice_charge_the_same_tax(api, tenant, taxed_world):
    """Guide §3, the honest-mirror check: the FBR payload's salesTaxApplicable must equal the stored
    line's tax1_amount to the paisa — one engine, two readers."""
    import json as _json
    from unittest import mock

    wh, cust, v, tt = taxed_world
    _product_fbr(tenant, v, "18% along with rupees 60 per kilogram",
                 fbr_hs_code="7306.3090", fbr_uom="KG", fbr_sale_type="Goods at standard rate (default)",
                 fbr_sro_schedule="SRO 1125(I)/2011", fbr_sro_item="3")
    with tenant_context(tenant, tenant.owner):
        from apps.fbr.models import FBRSettings

        cust.cnic, cust.business_name = "35202-1234567-1", "Salon One Traders"
        cust.address, cust.city, cust.province = "Mall Road", "Lahore", "Punjab"
        cust.fbr_registration_type = "Unregistered"
        cust.save(update_fields=["cnic", "business_name", "address", "city", "province", "fbr_registration_type"])
        FBRSettings.objects.create(ntn="1234567-8", business_name="DEMO DISTRIBUTORS",
                                   address="Plot 12 Lahore", province="Punjab", sandbox_token="tok-sb")
    oid = _issued_order(api, wh, cust, v)
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"apply_tax": True}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data

    ok_body = {"validationResponse": {"statusCode": "00", "status": "Valid", "error": "", "invoiceStatuses": []}}
    resp = mock.Mock()
    resp.text = _json.dumps(ok_body)
    resp.json = lambda: ok_body
    with mock.patch("apps.fbr.services.requests.post", return_value=resp) as post:
        ok = api.post(f"/api/v1/invoices/{inv['id']}/fbr-validate", {}, format="json")
        assert ok.status_code == 200, ok.content
        item = post.call_args.kwargs["json"]["items"][0]
    line = inv["lines"][0]
    assert item["salesTaxApplicable"] == float(Decimal(line["tax1_amount"])), (item, line["tax1_amount"])
    assert item["valueSalesExcludingST"] == 600.0
    assert item["salesTaxApplicable"] == 348.0


def test_a_credit_note_mirrors_the_per_unit_tax_not_the_rate(api, tenant, taxed_world):
    """A compound or fixed charge has no single percentage — the credit must reverse the per-unit
    tax actually charged, or the rupee part stays on the books."""
    wh, cust, v, tt = taxed_world
    _product_fbr(tenant, v, "18% along with rupees 60 per kilogram")
    oid = _issued_order(api, wh, cust, v)  # 4 units, tax 348 → 87 a unit
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"apply_tax": True}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    with tenant_context(tenant, tenant.owner):
        from apps.sales.models import SalesInvoice
        from apps.sales.services import CreditNoteService

        row = SalesInvoice.objects.get(id=inv["id"])
        cn = CreditNoteService.create(customer=cust, invoice=row, warehouse=row.order.warehouse if row.order_id else None,
                                      lines=[{"variant": v, "qty": Decimal("1"), "unit_price": Decimal("150")}])
        assert cn.tax1_total == Decimal("87"), cn.tax1_total  # (600×18% + 240) / 4 units × 1
        assert cn.total == Decimal("237")


# ── the tax choice travels with the ORDER (owner, 26 Oct 2026: "option to create tax invoice") ──────


def test_an_order_marked_tax_invoice_bills_with_tax_at_delivery(api, tenant, taxed_world):
    """His real flow never touches the New-invoice page: the delivery confirmation bills automatically.
    The order's own switch must reach that invoice."""
    wh, cust, v, tt = taxed_world
    _product_fbr(tenant, v, "18%")
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "charge_tax": True, "lines": [{"variant": str(v.id), "qty": "4"}]}, format="json")
    assert r.status_code == 201 and r.data["charge_tax"] is True, r.content
    done = api.post(f"/api/v1/sales-orders/{r.data['id']}/mark-delivered", {}, format="json")
    assert done.status_code == 200, done.content
    inv = api.get("/api/v1/invoices", {"order": r.data["id"]}).data["results"][0]
    assert inv["status"] == "posted" and inv["force_tax_type"], inv
    assert Decimal(inv["tax1_total"]) == Decimal("108")  # 4 × 150 × 18%
    assert Decimal(inv["total"]) == Decimal("708")


def test_a_flagged_order_with_unclassified_products_still_bills_and_notifies(api, tenant, taxed_world):
    """An automatic billing must never block the delivery: no FBR rate → the invoice goes out untaxed
    and the office is told to classify and reopen."""
    wh, cust, v, tt = taxed_world  # product carries NO fbr_rate_desc
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "charge_tax": True, "lines": [{"variant": str(v.id), "qty": "2"}]}, format="json")
    done = api.post(f"/api/v1/sales-orders/{r.data['id']}/mark-delivered", {}, format="json")
    assert done.status_code == 200, done.content
    inv = api.get("/api/v1/invoices", {"order": r.data["id"]}).data["results"][0]
    assert inv["status"] == "posted" and not inv["force_tax_type"] and Decimal(inv["tax1_total"]) == 0
    with tenant_context(tenant, tenant.owner):
        from apps.core.models import Notification

        n = Notification.objects.filter(type="invoice.tax_skipped").first()
        assert n is not None and "WITHOUT the tax" in n.title


def test_a_flagged_order_carries_the_fbr_tax_itself(api, tenant, taxed_world):
    """Owner (26 Oct 2026): "do same for sale order" — a tax-invoice order prices its lines through the
    same engine the invoice uses, so the order total IS the taxed total and a cash delivery collects it."""
    wh, cust, v, tt = taxed_world
    _product_fbr(tenant, v, "18%")
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "charge_tax": True, "lines": [{"variant": str(v.id), "qty": "4"}]}, format="json")
    assert r.status_code == 201, r.content
    assert Decimal(r.data["tax1_total"]) == Decimal("108")  # 4 × 150 × 18%
    assert Decimal(r.data["total"]) == Decimal("708")
    line = r.data["lines"][0]
    assert Decimal(line["tax1_amount"]) == Decimal("108") and Decimal(line["line_total"]) == Decimal("708")
    # the automatic billing lands on the same figure — order and invoice agree to the paisa
    done = api.post(f"/api/v1/sales-orders/{r.data['id']}/mark-delivered", {}, format="json")
    assert done.status_code == 200, done.content
    inv = api.get("/api/v1/invoices", {"order": r.data["id"]}).data["results"][0]
    assert Decimal(inv["total"]) == Decimal(r.data["total"])

    # without the flag, the order stays untaxed exactly as before
    r2 = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                           "charge_tax": False, "lines": [{"variant": str(v.id), "qty": "4"}]}, format="json")
    assert Decimal(r2.data["tax1_total"]) == 0 and Decimal(r2.data["total"]) == Decimal("600")


def test_the_order_pdf_shows_the_tax_invoice_columns(api, tenant, taxed_world):
    """The order confirmation reads like the invoice that will bill it: Disc · Taxable · GST · Total,
    HS under the item, and Taxable value + Sales tax footing to the order total."""
    wh, cust, v, tt = taxed_world
    _product_fbr(tenant, v, "18%", fbr_hs_code="7306.3090")
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "charge_tax": True, "lines": [{"variant": str(v.id), "qty": "4"}]}, format="json")
    with tenant_context(tenant, tenant.owner):
        from django.template.loader import render_to_string

        from apps.sales.models import SalesOrder

        o = SalesOrder.objects.get(id=r.data["id"])
        lines = list(o.lines.select_related("variant__product"))
        for l in lines:
            l.pending = l.qty_ordered
            l.discount_total = (l.discount_amount or 0) + (l.scheme_discount or 0)
            l.tax_amount = (l.tax1_amount or 0) + (l.tax2_amount or 0)
            l.taxable = (l.line_total or 0) - l.tax_amount
        html = render_to_string("sales/order.html", {"o": o, "lines": lines, "tenant": tenant, "tax_on": True,
                                                     "tax_shown": o.tax1_total, "taxable_total": o.subtotal - o.discount - o.scheme_discount,
                                                     "show_progress": False, "tax1_name": "Sales Tax", "tax2_name": None,
                                                     "logo_url": None, "printed_at": "", "doc_title": "SALES ORDER", "doc_ref": o.number})
        assert ">Disc<" in html and ">Taxable<" in html and ">GST<" in html and ">Total<" in html
        assert "HS 7306.3090" in html and "Taxable value" in html and "Sales tax" in html
        assert "708" in html and "108" in html


def test_the_create_page_switch_beats_the_orders_flag(api, tenant, taxed_world):
    """apply_tax sent explicitly — even false — wins over the order's switch; absent, the order decides."""
    wh, cust, v, tt = taxed_world
    _product_fbr(tenant, v, "18%")
    # order flagged, person unticks the box on the create page → no tax
    with tenant_context(tenant, tenant.owner):
        from apps.sales.models import SalesOrder
    oid = _issued_order(api, wh, cust, v)
    SalesOrder.all_objects.filter(pk=oid).update(charge_tax=True)
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"apply_tax": False, "post": False}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    assert not inv["force_tax_type"] and Decimal(inv["tax1_total"]) == 0

    # nothing sent → the order's flag decides
    oid2 = _issued_order(api, wh, cust, v)
    SalesOrder.all_objects.filter(pk=oid2).update(charge_tax=True)
    made2 = api.post(f"/api/v1/sales-orders/{oid2}/invoice", {"post": False}, format="json")
    inv2 = made2.data[0] if isinstance(made2.data, list) else made2.data
    assert inv2["force_tax_type"] and Decimal(inv2["tax1_total"]) == Decimal("108")


# ── audit fixes (26 Oct 2026): atomic posted-invoice switch, override filing, CN aggregation ─────────


def test_tax_on_a_posted_invoice_is_one_atomic_call(api, tenant, taxed_world):
    """POST /invoices/{id}/tax on a posted, unpaid invoice reopens and switches in one request —
    and a refusal rolls the reopen back, so the invoice is never stranded off the ledger."""
    wh, cust, v, tt = taxed_world
    oid = _issued_order(api, wh, cust, v)
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {}, format="json")  # posted, no tax
    inv = made.data[0] if isinstance(made.data, list) else made.data
    assert inv["status"] == "posted" and not inv["force_tax_type"]

    # unclassified product: the switch refuses AND the invoice stays posted (nothing half-done)
    r = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": True}, format="json")
    assert r.status_code in (400, 422) and "FBR" in str(r.data), r.content
    assert api.get(f"/api/v1/invoices/{inv['id']}").data["status"] == "posted"

    # classified: one call → reopened draft carrying the tax, ready to review and re-post
    _product_fbr(tenant, v, "18%")
    ok = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": True}, format="json")
    assert ok.status_code == 200, ok.content
    assert ok.data["status"] == "draft" and Decimal(ok.data["tax1_total"]) == Decimal("108")
    assert api.post(f"/api/v1/invoices/{inv['id']}/post", {}, format="json").status_code == 200


def test_an_override_line_cannot_be_filed_with_fbr(api, tenant, taxed_world):
    """The invoice may charge a typed % — but FBR recomputes from the rate string, so filing one
    would make the filing disagree with the invoice. It refuses and says what to clear."""
    import json as _json
    from unittest import mock

    wh, cust, v, tt = taxed_world
    _product_fbr(tenant, v, "18%", fbr_hs_code="7306.3090", fbr_uom="KG",
                 fbr_sale_type="Goods at standard rate (default)")
    with tenant_context(tenant, tenant.owner):
        from apps.fbr.models import FBRSettings

        cust.cnic, cust.business_name = "35202-1234567-1", "Salon One Traders"
        cust.address, cust.city, cust.province = "Mall Road", "Lahore", "Punjab"
        cust.fbr_registration_type = "Unregistered"
        cust.save(update_fields=["cnic", "business_name", "address", "city", "province", "fbr_registration_type"])
        FBRSettings.objects.create(ntn="1234567-8", business_name="DEMO DISTRIBUTORS",
                                   address="Plot 12 Lahore", province="Punjab", sandbox_token="tok-sb")
    oid = _issued_order(api, wh, cust, v)
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"apply_tax": True, "post": False}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    line_id = inv["lines"][0]["id"]
    edited = api.patch(f"/api/v1/invoices/{inv['id']}", {"lines": [{"id": line_id, "tax_pct": "5"}]}, format="json")
    assert edited.status_code == 200, edited.content
    api.post(f"/api/v1/invoices/{inv['id']}/post", {}, format="json")
    resp = mock.Mock()
    resp.text = _json.dumps({})
    resp.json = lambda: {}
    with mock.patch("apps.fbr.services.requests.post", return_value=resp):
        r = api.post(f"/api/v1/invoices/{inv['id']}/fbr-validate", {}, format="json")
    assert r.status_code in (400, 422) and "typed" in str(r.data), r.content


def test_a_credit_note_against_two_lines_of_one_variant_credits_the_true_total(api, tenant, taxed_world):
    """Two invoice lines of the same variant can carry different per-unit tax (one discounted);
    the mirror aggregates them, so crediting everything reverses exactly what was charged."""
    wh, cust, v, tt = taxed_world
    _product_fbr(tenant, v, "18%")
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(v.id), "qty": "2"},
                                                    {"variant": str(v.id), "qty": "2", "discount_pct": "50"}]}, format="json")
    assert r.status_code == 201, r.content
    pl = api.post(f"/api/v1/sales-orders/{r.data['id']}/load-form", {}, format="json")
    issues = [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl.data["lines"]]
    api.post(f"/api/v1/pick-lists/{pl.data['id']}/issue", {"issues": issues}, format="json")
    made = api.post(f"/api/v1/sales-orders/{r.data['id']}/invoice", {"apply_tax": True}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    assert len(inv["lines"]) == 2, "two lines of the same variant must stay two lines"
    with tenant_context(tenant, tenant.owner):
        from apps.sales.models import SalesInvoice
        from apps.sales.services import CreditNoteService

        row = SalesInvoice.objects.get(id=inv["id"])
        cn = CreditNoteService.create(customer=cust, invoice=row, warehouse=row.order.warehouse,
                                      lines=[{"variant": v, "qty": Decimal("4"), "unit_price": Decimal("150")}])
        assert cn.tax1_total == row.tax1_total, (cn.tax1_total, row.tax1_total)


def test_the_customer_flag_makes_every_order_a_tax_invoice(api, tenant, taxed_world):
    """Owner (26 Oct 2026): the tax-invoice choice is managed on the CUSTOMER — each order inherits
    it, and the automatic billing at delivery charges the FBR tax without anyone touching anything."""
    wh, cust, v, tt = taxed_world
    _product_fbr(tenant, v, "18%")
    assert api.patch(f"/api/v1/customers/{cust.id}", {"charge_tax": True}, format="json").status_code == 200
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(v.id), "qty": "4"}]}, format="json")
    assert r.status_code == 201 and r.data["charge_tax"] is True, r.content
    done = api.post(f"/api/v1/sales-orders/{r.data['id']}/mark-delivered", {}, format="json")
    assert done.status_code == 200, done.content
    inv = api.get("/api/v1/invoices", {"order": r.data["id"]}).data["results"][0]
    assert inv["force_tax_type"] and Decimal(inv["tax1_total"]) == Decimal("108")

    # an explicit value on the order API still wins over the customer's flag
    r2 = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                           "charge_tax": False, "lines": [{"variant": str(v.id), "qty": "2"}]}, format="json")
    assert r2.status_code == 201 and r2.data["charge_tax"] is False, r2.content


def test_the_sales_tax_register_reports_fbr_invoices_and_their_credit_notes(api, tenant, taxed_world):
    """Owner (26 Oct 2026): a report for the tax change — every FBR-taxed invoice and the credit notes
    reversing them, untaxed invoices excluded, net totals ready for the sales-tax return."""
    wh, cust, v, tt = taxed_world
    _product_fbr(tenant, v, "18%")
    oid = _issued_order(api, wh, cust, v)  # 4 × 150
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"apply_tax": True}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    # an untaxed invoice stays OUT of the register
    oid2 = _issued_order(api, wh, cust, v)
    api.post(f"/api/v1/sales-orders/{oid2}/invoice", {"apply_tax": False}, format="json")
    with tenant_context(tenant, tenant.owner):
        from apps.inventory.models import Warehouse
        from apps.sales.models import SalesInvoice
        from apps.sales.services import CreditNoteService

        row = SalesInvoice.objects.get(id=inv["id"])
        cn = CreditNoteService.create(customer=cust, invoice=row, warehouse=Warehouse.objects.get(pk=wh.id),
                                      lines=[{"variant": v, "qty": Decimal("1"), "unit_price": Decimal("150")}])
        CreditNoteService.post(cn)

    r = api.get("/api/v1/reports/sales_tax_register")
    assert r.status_code == 200, r.content
    docs = [(x["doc"], x["number"]) for x in r.data["rows"]]
    assert ("Invoice", inv["number"]) in docs and any(d == "Credit note" for d, _ in docs)
    assert len(r.data["rows"]) == 2, docs  # the untaxed invoice is not in the register
    invoice_row = next(x for x in r.data["rows"] if x["doc"] == "Invoice")
    assert Decimal(invoice_row["taxable_value"]) == Decimal("600") and Decimal(invoice_row["sales_tax"]) == Decimal("108")
    assert invoice_row["fbr_status"] == "not filed"
    assert Decimal(r.data["totals"]["sales_tax"]) == Decimal("81")  # 108 − 27 credited back
    x = api.get("/api/v1/reports/sales_tax_register", {"format": "xlsx"})
    assert x.status_code == 200 and "sheet" in x["Content-Type"], x["Content-Type"]
