"""FBR Digital Invoicing — the guide's own test vectors (§12) plus the flow against a mocked gateway."""
import json
from decimal import Decimal
from unittest import mock

import pytest

from apps.core.tenancy import tenant_context
from apps.fbr import engine

pytestmark = pytest.mark.django_db


# ── §12 vectors, verbatim ───────────────────────────────────────────────────────────────────────────
def test_trim_ntn_keeps_letters_and_a_cnics_thirteen_digits():
    assert engine.trim_ntn("A014841-0") == "A014841"
    assert engine.trim_ntn("1234567-8") == "1234567"
    assert engine.trim_ntn("42101-1234567-1") == "4210112345671"
    assert engine.trim_ntn("3520212345679") == "3520212345679"


def test_rate_parser_handles_all_eleven_shapes():
    cases = [("18%", 18, 0), ("0.5%", Decimal("0.5"), 0), ("1.43%", Decimal("1.43"), 0),
             ("Exempt", 0, 0), ("Rs.200", 0, 200), ("Rs. 5", 0, 5), ("Rs.3", 0, 3),
             ("Rs.700/MT", 0, 700), ("Rs.1,000", 0, 1000),
             ("18% along with rupees 60 per kilogram", 18, 60), ("18% and Rs. 80 per Liter", 18, 80)]
    for rate, pct, fixed in cases:
        assert engine.gst_rate_num(rate) == pct, rate
        assert engine.fixed_tax_amount(rate) == fixed, rate


def test_third_schedule_taxes_the_printed_retail_price_per_unit():
    m = engine.compute_line(quantity=10, unit_price=100, discount_amount=0,
                            rate_str="18%", sale_type="3rd Schedule Goods", mrp=150)
    assert m["salesTaxApplicable"] == Decimal("270.00")   # 150 × 10 × 18%, not 180
    plain = engine.compute_line(quantity=10, unit_price=100, discount_amount=0, rate_str="18%")
    assert plain["salesTaxApplicable"] == Decimal("180.00")


def test_withholding_is_subtracted_in_total():
    m = engine.compute_line(quantity=1, unit_price=1000, discount_amount=0, rate_str="18%", withheld_rate=4)
    assert m["totalValues"] == Decimal("1000") + Decimal("180") - Decimal("40")


def test_duplicate_lines_combine_and_every_money_field_sums():
    line = {"hsCode": "1", "productDescription": "X", "rate": "18%", "saleType": "s", "uoM": "KG",
            "sroScheduleNo": "", "sroItemSerialNo": "", "quantity": 2, "totalValues": 10.0,
            "valueSalesExcludingST": 8.0, "salesTaxApplicable": 2.0, "salesTaxWithheldAtSource": 0.0,
            "extraTax": "", "furtherTax": 0.0, "fedPayable": 0.0, "discount": 0.0,
            "fixedNotifiedValueOrRetailPrice": 0.0}
    out = engine.combine_duplicate_lines([dict(line), {**line, "quantity": 3, "totalValues": 15.0,
                                                      "valueSalesExcludingST": 12.0, "salesTaxApplicable": 3.0}])
    assert len(out) == 1 and out[0]["quantity"] == 5
    assert out[0]["totalValues"] == 25.0 and out[0]["salesTaxApplicable"] == 5.0
    assert out[0]["extraTax"] == ""                       # "" survives when both blank
    # every money field named in the payload is in the summed set — walk, don't trust
    for f in ("totalValues", "valueSalesExcludingST", "salesTaxApplicable", "salesTaxWithheldAtSource",
              "extraTax", "furtherTax", "fedPayable", "discount"):
        assert f in engine._SUMMED


def test_response_parser_handles_all_four_shapes():
    class R:
        def __init__(self, body):
            self.text = body
        def json(self):
            return json.loads(self.text)

    ok = R('{"invoiceNumber":"X","validationResponse":{"statusCode":"00","status":"Valid"}}')
    assert engine.parse_fbr_response(ok)[1]["statusCode"] == "00"
    gateway = R('{"Code":"03","error":"Requested JSON in Malformed"}')
    vr = engine.parse_fbr_response(gateway)[1]
    assert vr["statusCode"] == "03" and "Malformed" in vr["error"]
    trailing = R('{"validationResponse":{"statusCode":"01","status":"Invalid",}}')
    assert engine.parse_fbr_response(trailing)[1]["statusCode"] == "01"
    garbage = R("<html>Service Unavailable</html>")
    assert engine.parse_fbr_response(garbage) == ({}, {})


def test_fbr_text_and_province_and_scenarios():
    assert engine.fbr_text('PVC TAPE 3 4" NITTO') == "PVC TAPE 3 4'' NITTO"
    assert engine.fbr_province("Khyber Pakhtunkhwa") == "KPK"
    assert engine.fbr_province("Islamabad Capital Territory") == "ICT"
    assert engine.scenario_for("Goods at standard rate (default)", "Unregistered") == "SN002"
    assert engine.scenario_for("Goods at standard rate (default)", "Registered") == "SN001"
    assert engine.scenario_for("3rd Schedule Goods", "Registered") == "SN008"


# ── the flow, against a mocked gateway ──────────────────────────────────────────────────────────────
def _taxed_invoice(api, tenant):
    from datetime import date

    from django.utils import timezone

    from apps.catalog.models import Product, TaxRate, TaxType

    from .conftest import seed_basic

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        tt = TaxType.objects.create(code="ST", name="Sales Tax", kind="sales_tax", applies_to="both")
        TaxRate.objects.create(tax_type=tt, rate_pct=Decimal("18"), valid_from=timezone.localdate())
        Product.objects.filter(pk=v.product_id).update(
            tax_pct=Decimal("18"), fbr_hs_code="7306.3090", fbr_uom="KG",
            fbr_sale_type="Goods at standard rate (default)", fbr_rate_desc="18%")
        cust.cnic = "35202-1234567-1"
        cust.business_name = "Salon One Traders"
        cust.address, cust.city, cust.province = "Mall Road", "Lahore", "Punjab"
        cust.fbr_registration_type = "Unregistered"
        cust.save(update_fields=["cnic", "business_name", "address", "city", "province", "fbr_registration_type"])
        from apps.fbr.models import FBRSettings

        FBRSettings.objects.create(ntn="1234567-8", business_name="DEMO DISTRIBUTORS",
                                   address="Plot 12 Lahore", province="Punjab", sandbox_token="tok-sb")
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(v.id), "qty": "4"}]}, format="json")
    pl = api.post(f"/api/v1/sales-orders/{r.data['id']}/load-form", {}, format="json")
    issues = [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl.data["lines"]]
    api.post(f"/api/v1/pick-lists/{pl.data['id']}/issue", {"issues": issues}, format="json")
    made = api.post(f"/api/v1/sales-orders/{r.data['id']}/invoice", {"apply_tax": True}, format="json")
    return (made.data[0] if isinstance(made.data, list) else made.data)


def _resp(body):
    m = mock.Mock()
    m.text = json.dumps(body)
    m.json = lambda: body
    return m


ACCEPT = {"invoiceNumber": "3520277029257DIKVSTDE857820",
          "validationResponse": {"statusCode": "00", "status": "Valid", "error": "", "invoiceStatuses": []}}


def test_validate_then_submit_files_the_invoice_and_freezes_it(api, tenant):
    inv = _taxed_invoice(api, tenant)
    with mock.patch("apps.fbr.services.requests.post", return_value=_resp(ACCEPT)) as post:
        ok = api.post(f"/api/v1/invoices/{inv['id']}/fbr-validate", {}, format="json")
        assert ok.status_code == 200 and ok.data["status"] == "validated", ok.content
        sent = post.call_args.kwargs["json"]
        assert sent["scenarioId"] == "SN002"                       # sandbox + unregistered buyer
        assert sent["sellerNTNCNIC"] == "1234567"                  # check digit dropped
        assert sent["buyerNTNCNIC"] == "3520212345671"             # CNIC's 13 digits kept
        assert sent["items"][0]["rate"] == "18%"
        assert sent["items"][0]["salesTaxApplicable"] == 108.0     # 600 × 18%
        assert sent["items"][0]["extraTax"] == ""
        done = api.post(f"/api/v1/invoices/{inv['id']}/fbr-submit", {}, format="json")
        assert done.status_code == 200 and done.data["fbr_invoice_number"].endswith("857820"), done.content

    # §8.3: a submitted invoice is immutable — reopen and cancel both refuse
    reopened = api.post(f"/api/v1/invoices/{inv['id']}/reopen", {}, format="json")
    assert reopened.status_code in (400, 422) and "FBR" in str(reopened.data)
    cancelled = api.post(f"/api/v1/invoices/{inv['id']}/cancel", {}, format="json")
    assert cancelled.status_code in (400, 422) and "FBR" in str(cancelled.data)

    detail = api.get(f"/api/v1/invoices/{inv['id']}")
    assert detail.data["fbr"]["status"] == "submitted" and detail.data["fbr"]["sandbox"] is True


def test_rejection_is_reported_with_fbrs_words_and_submit_needs_validation(api, tenant):
    inv = _taxed_invoice(api, tenant)
    refused = api.post(f"/api/v1/invoices/{inv['id']}/fbr-submit", {}, format="json")
    assert refused.status_code in (400, 422)                      # no validation yet
    reject = {"validationResponse": {"statusCode": "01", "status": "Invalid", "error": "",
                                     "invoiceStatuses": [{"itemSNo": "1", "statusCode": "01",
                                                          "error": "DUPLICATE INVOICE EXISTS"}]}}
    with mock.patch("apps.fbr.services.requests.post", return_value=_resp(reject)):
        bad = api.post(f"/api/v1/invoices/{inv['id']}/fbr-validate", {}, format="json")
        assert bad.status_code in (400, 422) and "DUPLICATE INVOICE EXISTS" in str(bad.data)


def test_unclassified_product_refuses_before_any_call(api, tenant):
    inv = _taxed_invoice(api, tenant)
    from apps.catalog.models import Product

    with tenant_context(tenant, tenant.owner):
        Product.objects.update(fbr_hs_code="")
    r = api.post(f"/api/v1/invoices/{inv['id']}/fbr-validate", {}, format="json")
    assert r.status_code in (400, 422) and "HS code" in str(r.data)


def test_scenario_runner_swaps_seller_only_and_records_the_run(api, tenant):
    _taxed_invoice(api, tenant)   # creates FBRSettings with a sandbox token
    listed = api.get("/api/v1/fbr/scenarios")
    assert listed.status_code == 200
    rows = {r["scenario_id"]: r for r in listed.data["results"]}
    assert rows["SN001"]["has_template"] and rows["SN002"]["has_template"]
    assert len(rows) >= 28

    with mock.patch("apps.fbr.services.requests.post", return_value=_resp(ACCEPT)) as post:
        run = api.post("/api/v1/fbr/scenarios", {"scenario_id": "SN002"}, format="json")
        assert run.status_code == 200 and run.data["passed"] is True, run.content
        sent = post.call_args.kwargs["json"]
        assert sent["sellerNTNCNIC"] == "1234567"                 # seller swapped in
        assert sent["scenarioId"] == "SN002"
        assert sent["items"][0]["salesTaxApplicable"] == 180.0    # the template's OWN figures, untouched

    again = api.get("/api/v1/fbr/scenarios")
    sn2 = next(r for r in again.data["results"] if r["scenario_id"] == "SN002")
    assert sn2["last"]["passed"] is True


def test_party_preflight_lists_every_problem_and_the_vectors_hold(api, tenant):
    from apps.partners.models import Customer

    # buyer_tax_id precedence: NTN wins over CNIC; vectors from the party guide §7
    assert engine.trim_ntn("") == ""
    with tenant_context(tenant, tenant.owner):
        from .conftest import seed_basic

        seed_basic(tenant)
        bad = Customer.objects.create(code="C-FBR", name="", business_name='AL-FALAH 3/4" PIPES',
                                      province="", address="", city="")
        bad.refresh_from_db()
        assert '"' not in bad.business_name            # scrubbed at save: AL-FALAH 3/4'' PIPES
        probs = engine.party_fbr_problems(bad)
        assert len(probs) >= 3                          # id, province, address, reg type — all at once
        assert any("NTN or CNIC" in p for p in probs)
        assert any("Province" in p for p in probs)
        assert any("Registration type" in p for p in probs)

        good = Customer.objects.create(code="C-OK", name="Okay Store", business_name="Okay Traders",
                                       cnic="35202-1234567-1", province="Punjab", address="Mall Road",
                                       city="Lahore", fbr_registration_type="Unregistered")
        assert engine.party_fbr_problems(good) == []


def test_validate_reports_all_buyer_problems_in_one_breath(api, tenant):
    inv = _taxed_invoice(api, tenant)
    from apps.partners.models import Customer

    with tenant_context(tenant, tenant.owner):
        Customer.objects.update(cnic="", ntn="", province="", fbr_registration_type="")
    r = api.post(f"/api/v1/invoices/{inv['id']}/fbr-validate", {}, format="json")
    assert r.status_code in (400, 422)
    text = str(r.data)
    assert "NTN or CNIC" in text and "Province" in text and "Registration type" in text



def test_hs_guide_vectors_sro_rule_dedupe_and_worked_example():
    from types import SimpleNamespace as P

    # §12 worked example: 95.2 KG at 165, 18% → 2,827.44 / 18,535.44
    m = engine.compute_line(quantity="95.2", unit_price="165", discount_amount=0, rate_str="18%")
    assert m["salesTaxApplicable"] == Decimal("2827.44") and m["totalValues"] == Decimal("18535.44")
    # the same line as 3rd Schedule with MRP 200/unit → base 19,040, gst 3,427.20
    t = engine.compute_line(quantity="95.2", unit_price="165", discount_amount=0, rate_str="18%",
                            sale_type="3rd Schedule Goods", mrp="200")
    assert t["salesTaxApplicable"] == Decimal("3427.20")

    # §6: any rate other than 18% demands the SRO pair — reported together, with everything else
    prod = P(fbr_hs_code="1006.3010", fbr_sale_type="Goods at Reduced Rate", fbr_rate_desc="1%",
             fbr_uom="KG", fbr_sro_schedule="", fbr_sro_item="", mrp_ex_tax=None)
    probs = engine.product_fbr_problems(prod)
    assert any("SRO schedule" in x for x in probs) and any("item serial" in x for x in probs)
    ok = P(fbr_hs_code="7306.3090", fbr_sale_type="Goods at standard rate (default)", fbr_rate_desc="18%",
           fbr_uom="KG", fbr_sro_schedule="", fbr_sro_item="", mrp_ex_tax=None)
    assert engine.product_fbr_problems(ok) == []
    third = P(fbr_hs_code="3004.9099", fbr_sale_type="3rd Schedule Goods", fbr_rate_desc="18%",
              fbr_uom="Packs", fbr_sro_schedule="3rd Schedule goods", fbr_sro_item="1", mrp_ex_tax=None)
    assert any("retail price" in x for x in engine.product_fbr_problems(third))

    # §9.1: the misleading error gains the real instruction
    hinted = engine.translate_error("HS Code does not match with provided sale type. Please refer…")
    assert "EXISTS in FBR" in hinted or "EXISTS" in hinted
