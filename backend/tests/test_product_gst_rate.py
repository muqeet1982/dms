"""
The product's own Tax %, owner 2 Nov 2026: "current price is including 25% tax so when create tax it should show actual
product price 25% less and show tax 25% … add tax and price without tax and current price field will be price with tax
… 25% is default 25 and option to change … submit same way as well."

- Owner, 5 Nov 2026: "remove tax field in product and use field in tax GST rate instead". A product's tax is its GST
  Rate, else the company default (25). The TP includes it.
- A tax order or invoice bills the price without tax (TP × 100 ÷ 125), the tax on it, and the TP as the line total.
- FBR is filed the same figures. A 3rd Schedule item's printed retail price includes the tax too, so it is worked back
  at the same rate: TDN typed the TP there (1255) and is taxed 25% of 1004, not of 1255.
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db
D = Decimal


@pytest.fixture
def world(api, tenant):
    from apps.catalog.models import Product, ProductVariant, TaxType

    from .conftest import seed_basic

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        TaxType.objects.create(code="ST", name="Sales Tax", kind="sales_tax", applies_to="both")
        Product.objects.filter(pk=v.product_id).update(sale_price=D("1255"))
        ProductVariant.objects.filter(pk=v.pk).update(sale_price=D("1255"))
    assert tenant.setting("default_tax_pct") == 25
    return wh, cust, v


def _order(api, wh, cust, v, qty="1", charge_tax=True):
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "charge_tax": charge_tax,
                                          "lines": [{"variant": str(v.id), "qty": qty}]}, format="json")
    assert r.status_code == 201, r.content
    return r.data


def _invoice(api, order):
    pl = api.post(f"/api/v1/sales-orders/{order['id']}/load-form", {}, format="json")
    api.post(f"/api/v1/pick-lists/{pl.data['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl.data["lines"]]}, format="json")
    made = api.post(f"/api/v1/sales-orders/{order['id']}/invoice", {"apply_tax": True}, format="json")
    assert made.status_code == 201, made.content
    return made.data[0] if isinstance(made.data, list) else made.data


def _filing(cust):
    return SimpleNamespace(seller_ntn_cnic="1234567", seller_business_name="Demo", seller_province="Punjab", seller_address="Lahore",
                           buyer_ntn_cnic="", buyer_business_name=cust.name, buyer_province="Punjab", buyer_address="Lahore",
                           buyer_registration_type="Unregistered")


def test_a_product_with_no_rate_of_its_own_takes_the_default_25_inside_its_tp(api, tenant, world):
    wh, cust, v = world
    p = api.get(f"/api/v1/products/{v.product_id}").data
    assert "tax_pct" not in p and p["tax_rate"] == "25%" and p["price_ex_tax"] == "1004.00", "TP 1255 = 1004 + 25%"

    order = _order(api, wh, cust, v, qty="2")
    assert (D(order["subtotal"]), D(order["tax1_total"]), D(order["total"])) == (D("2008"), D("502"), D("2510"))
    line = order["lines"][0]
    assert D(line["unit_price"]) == D("1255") and D(line["price_ex_tax"]) == D("1004") and D(line["discount_ex_tax"]) == 0, \
        "the order keeps the TP and reads the price without tax beside it"

    inv = _invoice(api, order)
    il = inv["lines"][0]
    assert (D(il["unit_price"]), D(il["tax1_rate"]), D(il["tax1_amount"]), D(il["line_total"])) == (D("1004"), D("25"), D("502"), D("2510"))
    assert il["fbr_rate"] == "25%"


def test_the_gst_rate_sets_the_tax_on_the_product(api, tenant, world):
    wh, cust, v = world
    r = api.patch(f"/api/v1/products/{v.product_id}", {"fbr_rate_desc": "18%"}, format="json")
    assert r.status_code == 200, r.content
    assert r.data["tax_rate"] == "18%" and r.data["price_ex_tax"] == "1063.56", "1255 ÷ 1.18"
    order = _order(api, wh, cust, v)
    assert (D(order["subtotal"]), D(order["tax1_total"]), D(order["total"])) == (D("1063.56"), D("191.44"), D("1255"))
    old_field = api.patch(f"/api/v1/products/{v.product_id}", {"tax_pct": "5"}, format="json")
    assert old_field.status_code == 200 and old_field.data["tax_rate"] == "18%", "the old Tax % field is gone: an API caller still sending it changes nothing"


def test_a_3rd_schedule_item_with_its_tp_as_printed_price_files_25_pct_of_the_price_without_tax(api, tenant, world):
    """TDN's 30 colour items, 2 Nov 2026: 3rd Schedule at 25%, the TP typed as the retail price — billed 941.25 + 313.75."""
    from apps.catalog.models import Product
    from apps.fbr.services import build_payload
    from apps.sales.models import SalesInvoice

    wh, cust, v = world
    with tenant_context(tenant, tenant.owner):
        Product.objects.filter(pk=v.product_id).update(fbr_rate_desc="25%", fbr_hs_code="3305.9020", fbr_sale_type="3rd Schedule Goods",
                                                       fbr_uom="Numbers, pieces, units", fbr_sro_schedule="3rd Schedule goods",
                                                       fbr_sro_item="1", mrp_ex_tax=D("1255"))
    inv = _invoice(api, _order(api, wh, cust, v, qty="3"))
    il = inv["lines"][0]
    assert (D(il["unit_price"]), D(il["tax1_amount"]), D(inv["total"])) == (D("1004"), D("753"), D("3765")), "3 × (1004 + 251)"
    with tenant_context(tenant, tenant.owner):
        item = build_payload(SalesInvoice.objects.get(pk=inv["id"]), _filing(cust), use_sandbox=False)["items"][0]
    assert item["rate"] == "25%"
    assert (D(str(item["valueSalesExcludingST"])), D(str(item["salesTaxApplicable"])), D(str(item["totalValues"]))) == (D("3012"), D("753"), D("3765"))
    assert D(str(item["fixedNotifiedValueOrRetailPrice"])) == D("1004"), "the retail price without its tax, per unit"


def test_the_invoice_and_the_filing_read_one_rate(api, tenant, world):
    from apps.catalog.models import Product
    from apps.fbr import engine

    wh, cust, v = world
    with tenant_context(tenant, tenant.owner):
        Product.objects.filter(pk=v.product_id).update(fbr_rate_desc="18%", fbr_hs_code="3305.9020", fbr_sale_type="Goods at standard rate (default)",
                                                       fbr_uom="Numbers, pieces, units")
        p = Product.objects.get(pk=v.product_id)
        assert p.tax_rate == p.fbr_rate_desc == "18%" and engine.product_fbr_problems(p) == []


def test_with_no_default_and_no_rate_a_product_carries_no_tax(api, tenant, world):
    wh, cust, v = world
    tenant.settings = {**(tenant.settings or {}), "default_tax_pct": 0}
    tenant.save(update_fields=["settings"])
    order = _order(api, wh, cust, v, charge_tax=False)
    pl = api.post(f"/api/v1/sales-orders/{order['id']}/load-form", {}, format="json")
    api.post(f"/api/v1/pick-lists/{pl.data['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl.data["lines"]]}, format="json")
    inv = api.post(f"/api/v1/sales-orders/{order['id']}/invoice", {"post": False}, format="json").data
    inv = inv[0] if isinstance(inv, list) else inv
    r = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": True}, format="json")
    assert r.status_code == 422 and "none of these products carries a tax rate" in r.data["detail"], r.data


def test_an_order_priced_with_tax_on_top_reads_as_it_always_did(api, tenant, world):
    """TDN-SO-00102 was priced before 1 Nov 2026 with the tax added on top: its lines show the TP, not TP ÷ 1.25."""
    wh, cust, v = world
    tenant.settings = {**(tenant.settings or {}), "tp_includes_tax": False}
    tenant.save(update_fields=["settings"])
    order = _order(api, wh, cust, v)
    assert (D(order["subtotal"]), D(order["tax1_total"]), D(order["total"])) == (D("1255"), D("313.75"), D("1568.75"))
    tenant.settings = {**tenant.settings, "tp_includes_tax": True}
    tenant.save(update_fields=["settings"])
    line = api.get(f"/api/v1/sales-orders/{order['id']}").data["lines"][0]
    assert line["price_ex_tax"] is None and line["discount_ex_tax"] is None
