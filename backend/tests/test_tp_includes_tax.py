"""
Tax invoices, owner 1 Nov 2026:
- "TAX is included in TP" — on a tax order or invoice the price before tax is worked back from the TP at each item's own
  rate (TP × 100 ÷ 118 for 18%), so price + tax = TP (to the paisa FBR's rounding allows); a plain one bills the TP.
- "Tax invoices sequence will be separate like TDN-STI-00001 then TDN-STI-00002" — numbered when posted.
- "give separate page for tax invoices" — Invoices lists the plain ones, Tax invoices the tax ones.
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db
D = Decimal


@pytest.fixture
def world(api, tenant):
    from django.utils import timezone

    from apps.catalog.models import Product, TaxRate, TaxType

    from .conftest import seed_basic

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        tt = TaxType.objects.create(code="ST", name="Sales Tax", kind="sales_tax", applies_to="both")
        TaxRate.objects.create(tax_type=tt, rate_pct=D("18"), valid_from=timezone.localdate())
        Product.objects.filter(pk=v.product_id).update(fbr_rate_desc="18%", fbr_hs_code="3305.2000", fbr_sale_type="Goods at standard rate (default)",
                                                       fbr_uom="Numbers, pieces, units")
    assert tenant.setting("tp_includes_tax") is True, "the default: TP includes the tax"
    return wh, cust, v


def _issued(api, wh, cust, v, qty="4", charge_tax=True):
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "charge_tax": charge_tax,
                                          "lines": [{"variant": str(v.id), "qty": qty}]}, format="json")
    assert r.status_code == 201, r.content
    pl = api.post(f"/api/v1/sales-orders/{r.data['id']}/load-form", {}, format="json")
    api.post(f"/api/v1/pick-lists/{pl.data['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl.data["lines"]]}, format="json")
    return r.data


def _invoice(api, order_id, **body):
    made = api.post(f"/api/v1/sales-orders/{order_id}/invoice", body, format="json")
    assert made.status_code == 201, made.content
    return made.data[0] if isinstance(made.data, list) else made.data


def test_the_tp_includes_the_tax_on_the_order_and_its_invoice(api, tenant, world):
    wh, cust, v = world
    # 4 × TP 150 = 600 with the 18% inside: 600 ÷ 1.18 = 508.47 before tax, GST 91.52 — 599.99, the nearest
    # paisa FBR's rounding of the value and of the tax can reach
    order = _issued(api, wh, cust, v)
    assert (D(order["subtotal"]), D(order["tax1_total"]), D(order["total"])) == (D("508.47"), D("91.52"), D("599.99"))
    assert D(order["lines"][0]["unit_price"]) == D("150"), "the order keeps the TP the rep quoted"
    inv = _invoice(api, order["id"], apply_tax=True)
    line = inv["lines"][0]
    assert D(line["tp"]) == D("150") and D(line["unit_price"]) == D("127.1186"), "TP 150 ÷ 1.18"
    assert (D(inv["subtotal"]), D(inv["tax1_total"]), D(inv["total"])) == (D("508.47"), D("91.52"), D("599.99")), "the order and its invoice agree"

    from apps.fbr.services import build_payload
    from apps.sales.models import SalesInvoice

    filing = SimpleNamespace(seller_ntn_cnic="1234567", seller_business_name="Demo", seller_province="Punjab", seller_address="Lahore",
                             buyer_ntn_cnic="", buyer_business_name=cust.name, buyer_province="Punjab", buyer_address="Lahore", buyer_registration_type="Unregistered")
    with tenant_context(tenant, tenant.owner):
        item = build_payload(SalesInvoice.objects.get(pk=inv["id"]), filing, use_sandbox=False)["items"][0]
    assert (D(str(item["valueSalesExcludingST"])), D(str(item["salesTaxApplicable"])), D(str(item["totalValues"]))) == (D("508.47"), D("91.52"), D("599.99")), "FBR is filed what the invoice charged"


def test_a_discount_takes_the_last_paisa_and_the_switch_flips_back_to_the_tp(api, tenant, world):
    wh, cust, v = world
    order = _issued(api, wh, cust, v, charge_tax=False)
    inv = _invoice(api, order["id"], post=False)
    assert D(inv["total"]) == D("600") and inv["lines"][0]["unit_price"] == "150.0000", "a plain invoice bills the TP"
    taxed = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": True}, format="json").data
    assert D(taxed["total"]) == D("599.99")
    disc = api.post(f"/api/v1/invoices/{inv['id']}/discount", {"action": "apply", "percent": "10"}, format="json").data
    assert D(disc["total"]) == D("540"), "540 = 600 less 10%: the discount takes the rounding paisa, the line is its TP exactly"
    back = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": False}, format="json").data
    assert D(back["total"]) == D("540") and back["lines"][0]["unit_price"] == "150.0000", "tax off: the TP again, no tax"


def test_tax_invoices_are_their_own_series_and_their_own_page(api, tenant, world):
    wh, cust, v = world
    first = _invoice(api, _issued(api, wh, cust, v, qty="1")["id"], apply_tax=True)
    plain = _invoice(api, _issued(api, wh, cust, v, qty="1", charge_tax=False)["id"], post=True)
    second = _invoice(api, _issued(api, wh, cust, v, qty="1")["id"], apply_tax=True)
    assert first["number"].endswith("STI-00001") and second["number"].endswith("STI-00002"), (first["number"], second["number"])
    assert "-INV-" in plain["number"]
    tax_list = [i["number"] for i in api.get("/api/v1/invoices?tax=1").data["results"]]
    plain_list = [i["number"] for i in api.get("/api/v1/invoices?tax=0").data["results"]]
    assert sorted(tax_list) == sorted([first["number"], second["number"]]) and plain_list == [plain["number"]]
    draft = _invoice(api, _issued(api, wh, cust, v, qty="1")["id"], apply_tax=True, post=False)
    assert "-INV-" in draft["number"], "a draft takes its STI number only when posted — no gaps"
    posted = api.post(f"/api/v1/invoices/{draft['id']}/post", {}, format="json").data
    assert posted["number"].endswith("STI-00003")


def test_a_discounted_tax_order_and_its_invoice_both_come_to_the_tp(api, tenant, world):
    """Live on demo, 1 Nov 2026: TP 310 × 4 less 5% = 1178 came to 1178.01 on the order and 1177.99 on its invoice."""
    # the two must agree to the paisa — the order is what is collected on delivery, the invoice what is owed
    from apps.catalog.models import Product
    from apps.partners.models import Customer

    wh, cust, v = world
    with tenant_context(tenant, tenant.owner):
        Product.objects.filter(pk=v.product_id).update(sale_price=D("310"))
        from apps.catalog.models import ProductVariant
        ProductVariant.objects.filter(pk=v.pk).update(sale_price=D("310"))
        Customer.objects.filter(pk=cust.pk).update(default_discount_pct=D("5"))
    order = _issued(api, wh, cust, v)
    line = order["lines"][0]
    tp_net = D(line["qty_ordered"]) * D(line["unit_price"]) - D(line["discount_amount"]) - D(line["scheme_discount"])
    grand = D(order["subtotal"]) - D(order["discount"]) - D(order["scheme_discount"]) + D(order["tax1_total"])
    # 1178 itself cannot be reached: value and GST round to the paisa each (998.31 + 179.70, or 998.30 + 179.69)
    assert abs(grand - tp_net) <= D("0.01"), (grand, tp_net)
    inv = _invoice(api, order["id"], apply_tax=True)
    assert D(inv["total"]) == D(order["total"]), "the order and its invoice pick the same paisa"
