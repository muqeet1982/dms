"""
Talking to FBR: reference sync, the payload built from a DMS invoice, validate and post (§2, §7, §8).

Filing is opt-in per invoice — these run only when someone presses the button on one invoice.
"""
import json
from datetime import date
from decimal import Decimal

import requests
from django.utils import timezone

from apps.core.audit import AuditService
from apps.core.exceptions import DomainError
from apps.core.tenancy import get_current_tenant

from . import engine
from .models import FBRFiling, FBRLookup, FBRSettings

GW = "https://gw.fbr.gov.pk"
POST_URL = {True: f"{GW}/di_data/v1/di/postinvoicedata_sb", False: f"{GW}/di_data/v1/di/postinvoicedata"}
VALIDATE_URL = {True: f"{GW}/di_data/v1/di/validateinvoicedata_sb", False: f"{GW}/di_data/v1/di/validateinvoicedata"}
REF_TIMEOUT, TX_TIMEOUT = 15, 30


def settings_for(tenant=None) -> FBRSettings:
    tenant = tenant or get_current_tenant()
    obj = FBRSettings.objects.first()
    if obj is None:
        obj = FBRSettings.objects.create()
    return obj


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _require_token(fbr):
    token, sandbox = fbr.token()
    if not token:
        raise DomainError(f"No FBR {'sandbox' if fbr.use_sandbox else 'production'} token is saved — "
                          "add it under Settings → FBR.", code="fbr_no_token")
    return token, sandbox


# ── reference data (§4): cache locally, sync from an admin action, never per request ────────────────
def _get(url, token, params=None):
    r = requests.get(url, headers=_headers(token), params=params or {}, timeout=REF_TIMEOUT)
    r.raise_for_status()
    return r.json()


def sync_reference(kinds=None):
    """
    The static lists. Parametric ones (rates per transaction type) sync lazily through
    `rates_for` / `schedules_for` / `uoms_for_hs` when a dropdown first asks.
    """
    fbr = settings_for()
    token, _sb = _require_token(fbr)
    done = {}
    plans = {
        "province": ("v1/provinces", "stateProvinceCode", "stateProvinceDesc"),
        "doctype": ("v1/doctypecode", "docTypeId", "docDescription"),
        "hscode": ("v1/itemdesccode", "hS_CODE", "description"),
        # §4: sroitemcode WITH a parameter answers the UoM list — a server-side bug. No parameters, ever.
        "sroitem": ("v1/sroitemcode", "srO_ITEM_ID", "srO_ITEM_DESC"),
        "transtype": ("v1/transtypecode", "transactioN_TYPE_ID", "transactioN_DESC"),
        "uom": ("v1/uom", "uoM_ID", "description"),
    }
    for kind, (path, key_field, desc_field) in plans.items():
        if kinds and kind not in kinds:
            continue
        rows = _get(f"{GW}/pdi/{path}", token)
        # FBR returns three HS codes twice with identical descriptions (guide §3) — first row wins
        seen, unique = set(), []
        for r in rows:
            k = str(r.get(key_field, ""))
            if r.get(key_field) is None or k in seen:
                continue
            seen.add(k)
            unique.append(r)
        FBRLookup.objects.filter(kind=kind).delete()
        FBRLookup.objects.bulk_create([
            # verbatim, spaces and all: FBR ships ' 3rd Schedule Goods ' and friends (field-flow A1), the
            # payload echoes the stored description, and every comparison in engine.py strips for itself
            FBRLookup(tenant_id=get_current_tenant().id, kind=kind, key=str(r.get(key_field, "")),
                      description=str(r.get(desc_field, "") or ""))
            for r in unique
        ], batch_size=1000)
        done[kind] = len(unique)
    return done


def _ref_date(d=None) -> str:
    """§4: the parametric lookups want DD-MMM-YYYY; an ISO date answers HTTP 500 with no body."""
    return (d or date.today()).strftime("%d-%b-%Y")


def rates_for(trans_type_id, on_date=None):
    cached = list(FBRLookup.objects.filter(kind="rate", parent_key=str(trans_type_id)))
    if cached:
        return cached
    fbr = settings_for()
    token, _sb = _require_token(fbr)
    rows = _get(f"{GW}/pdi/v2/SaleTypeToRate", token,
                {"date": _ref_date(on_date), "transTypeId": trans_type_id, "originationSupplier": 1})
    out = [FBRLookup.objects.create(kind="rate", key=str(r["ratE_ID"]), description=str(r["ratE_DESC"]).strip(),
                                    parent_key=str(trans_type_id), meta={"rate_value": r.get("ratE_VALUE")})
           for r in rows]
    return out


def schedules_for(rate_id, on_date=None):
    cached = list(FBRLookup.objects.filter(kind="sroschedule", parent_key=str(rate_id)))
    if cached:
        return cached
    fbr = settings_for()
    token, _sb = _require_token(fbr)
    rows = _get(f"{GW}/pdi/v1/SroSchedule", token,
                {"rate_id": rate_id, "date": _ref_date(on_date), "origination_supplier_csv": 1})
    return [FBRLookup.objects.create(kind="sroschedule", key=str(r["srO_ID"]), description=str(r["srO_DESC"]).strip(),
                                     parent_key=str(rate_id), meta={"serNo": r.get("serNo")})
            for r in rows]          # empty is NORMAL: no SRO applies


def sroitems_for(sro_id, on_date=None):
    """
    The items inside one SRO schedule. FBR's `sroitemcode?sro_id=` is server-side buggy for some
    ids (field-flow guide §14.4: wrong schedule's items, an empty list, or the UoM list shape), so a
    response whose rows lack `srO_ITEM_ID` is discarded rather than cached, and the caller falls back
    to the flat synced list.
    """
    cached = list(FBRLookup.objects.filter(kind="sroitem", parent_key=str(sro_id)))
    if cached:
        return cached
    fbr = settings_for()
    token, _sb = _require_token(fbr)
    rows = _get(f"{GW}/pdi/v1/sroitemcode", token, {"date": _ref_date(on_date), "sro_id": sro_id})
    good = [r for r in rows if isinstance(r, dict) and r.get("srO_ITEM_ID") is not None]
    if not good:
        return []
    seen, out = set(), []
    for r in good:
        k = str(r["srO_ITEM_ID"])
        if k in seen:
            continue
        seen.add(k)
        out.append(FBRLookup.objects.create(kind="sroitem", key=k, parent_key=str(sro_id),
                                            description=str(r.get("srO_ITEM_DESC", "") or "").strip()))
    return out


def uoms_for_hs(hs_code):
    """Empty for a 01–97 code means THE CODE IS WRONG (guide §5), never "no restriction";
    chapter 98 is a service heading, absent from the goods list, and takes the neutral unit."""
    cached = list(FBRLookup.objects.filter(kind="hs_uom", parent_key=str(hs_code)))
    if cached:
        return cached
    # a chapter-98 service heading never has goods units — the neutral unit, no token needed
    if str(hs_code).startswith("98"):
        return [FBRLookup.objects.create(kind="hs_uom", key="0", description="Numbers, pieces, units",
                                         parent_key=str(hs_code))]
    fbr = settings_for()
    token, _sb = _require_token(fbr)
    rows = _get(f"{GW}/pdi/v2/HS_UOM", token, {"hs_code": hs_code, "annexure_id": 3})
    return [FBRLookup.objects.create(kind="hs_uom", key=str(r["uoM_ID"]), description=str(r["description"]).strip(),
                                     parent_key=str(hs_code))
            for r in rows]


def check_registration(reg_no):
    """Get_Reg_Type: set the buyer's registration type from FBR, not from a guess."""
    fbr = settings_for()
    token, _sb = _require_token(fbr)
    r = requests.post(f"{GW}/dist/v1/Get_Reg_Type", headers=_headers(token),
                      json={"Registration_No": engine.trim_ntn(reg_no)}, timeout=REF_TIMEOUT)
    r.raise_for_status()
    return r.json()


# ── the payload, from a DMS invoice (§7) ────────────────────────────────────────────────────────────
def build_payload(inv, filing, *, use_sandbox):
    items = []
    for line in inv.lines.select_related("variant__product"):
        p = line.variant.product if line.variant_id else None
        problems = engine.product_fbr_problems(p) if p is not None else ["no product on the line"]
        if problems:
            raise DomainError(f"{line.description}: " + " ".join(problems), code="fbr_not_ready")
        # FBR recomputes tax from the RATE it is sent, so a line charged at a typed % cannot be filed
        # truthfully under the product's rate string — the invoice and the filing would disagree, the
        # exact drift the one-engine rule exists to prevent (audit, 26 Oct 2026)
        if line.tax_pct_override is not None:
            raise DomainError(
                f"{line.description}: this line charges a typed {line.tax1_rate}% instead of the product's "
                f"FBR rate ({p.fbr_rate_desc}) — FBR recomputes tax from the rate itself. Clear the line's "
                "typed % (edit the invoice) before filing.", code="fbr_override")
        mrp = p.mrp_ex_tax if engine.is_mrp_sale_type(p.fbr_sale_type) else None
        money = engine.compute_line(
            quantity=line.qty, unit_price=line.unit_price, discount_amount=line.discount_amount,
            rate_str=p.fbr_rate_desc, sale_type=p.fbr_sale_type, mrp=mrp,
            # the same rates InvoiceService.line_taxes charged, so the filing equals the stored invoice
            fed_rate=p.fbr_fed_rate, extra_rate=p.fbr_extra_tax_rate,
            further_rate=p.fbr_further_tax_rate, withheld_rate=p.fbr_wht_rate,
        )
        # FBR recomputes from the rates it is sent — today's product rates. If they moved since the invoice was
        # charged, the filing would disagree with the posted invoice and its journal: refuse, and say how to fix
        charged = {"GST": (line.tax1_amount, money["salesTaxApplicable"]), "FED": (line.fed_amount, money["fedPayable"]),
                   "Extra Tax": (line.extra_tax_amount, money["extraTax"]), "Further Tax": (line.further_tax_amount, money["furtherTax"]),
                   "WHT": (line.wht_amount, money["salesTaxWithheldAtSource"])}
        moved = [f"{k} {Decimal(str(was or 0)):.2f} → {Decimal(str(now or 0)):.2f}" for k, (was, now) in charged.items()
                 if abs(Decimal(str(was or 0)) - Decimal(str(now or 0))) > Decimal("0.01")]
        if moved:
            raise DomainError(f"{line.description}: the product's FBR rates changed since this invoice was charged ({', '.join(moved)}). "
                              "Reopen the invoice and post it again to charge today's rates, then file.", code="fbr_rates_changed")
        items.append({
            "hsCode": p.fbr_hs_code,
            "productDescription": engine.fbr_text((p.name or "").strip() or line.description),  # §11.3
            "rate": p.fbr_rate_desc,
            "uoM": p.fbr_uom or "Numbers, pieces, units",
            "quantity": float(line.qty),
            "totalValues": engine._f(money["totalValues"]),
            "valueSalesExcludingST": engine._f(money["valueSalesExcludingST"]),
            "fixedNotifiedValueOrRetailPrice": engine._f(mrp or 0) if mrp else 0.0,   # 0.00 unless 3rd Schedule
            "salesTaxApplicable": engine._f(money["salesTaxApplicable"]),
            "salesTaxWithheldAtSource": engine._f(money["salesTaxWithheldAtSource"]),
            "extraTax": engine._f(money["extraTax"]) if money["extraTax"] else "",    # "" when zero — §7.2
            "furtherTax": engine._f(money["furtherTax"]),
            "sroScheduleNo": p.fbr_sro_schedule or "",
            "fedPayable": engine._f(money["fedPayable"]),
            "discount": engine._f(money["discount"]),
            "saleType": p.fbr_sale_type,
            "sroItemSerialNo": p.fbr_sro_item or "",
        })
    payload = {
        "invoiceType": "Sale Invoice",
        "invoiceDate": inv.date.strftime("%Y-%m-%d"),     # ISO here, UNLIKE the reference APIs
        "sellerNTNCNIC": engine.trim_ntn(filing.seller_ntn_cnic),
        "sellerBusinessName": engine.fbr_text(filing.seller_business_name),
        "sellerProvince": engine.fbr_province(filing.seller_province),
        "sellerAddress": engine.fbr_text(filing.seller_address),
        "buyerNTNCNIC": engine.trim_ntn(filing.buyer_ntn_cnic),
        "buyerBusinessName": engine.fbr_text(filing.buyer_business_name),
        "buyerProvince": engine.fbr_province(filing.buyer_province),
        "buyerAddress": engine.fbr_text(filing.buyer_address),
        "buyerRegistrationType": filing.buyer_registration_type or "Unregistered",
        "invoiceRefNo": "0",
        "items": engine.combine_duplicate_lines(items),   # §7.4 — unconditionally
    }
    if use_sandbox:                                        # §7.3: absent entirely in production
        first = payload["items"][0] if payload["items"] else {}
        payload["scenarioId"] = engine.scenario_for(first.get("saleType", ""), payload["buyerRegistrationType"])
    return payload


def refresh_snapshot(inv) -> FBRFiling:
    """§8.1: refresh seller/buyer onto the filing right before building — then it freezes at submit."""
    fbr = settings_for()
    filing, _ = FBRFiling.objects.get_or_create(invoice=inv)
    if filing.status == "submitted":
        raise DomainError("This invoice is already filed with FBR — correct it with a credit note.", code="fbr_submitted")
    c = inv.customer
    filing.seller_ntn_cnic = fbr.ntn or fbr.cnic or ""
    filing.seller_business_name = fbr.business_name
    filing.seller_province = fbr.province
    filing.seller_address = fbr.address
    filing.buyer_ntn_cnic = (c.ntn or c.cnic or "").strip()
    filing.buyer_business_name = c.business_name or c.name
    filing.buyer_province = getattr(c, "province", "") or ""
    filing.buyer_address = " ".join(filter(None, [c.address, c.city]))
    filing.buyer_registration_type = c.fbr_registration_type or "Unregistered"
    filing.save()
    if not filing.seller_ntn_cnic or not filing.seller_business_name or not filing.seller_address:
        raise DomainError("Fill the seller identity under Settings → FBR first (NTN, business name, address).",
                          code="fbr_seller_incomplete")
    # the party pre-flight: every buyer problem in ONE message, not one per attempt
    problems = engine.party_fbr_problems(c)
    if problems:
        raise DomainError(f"{filing.buyer_business_name or c.name}: " + " ".join(problems), code="fbr_buyer_problems")
    return filing


def _call(url, token, payload):
    try:
        resp = requests.post(url, json=payload, headers=_headers(token), timeout=TX_TIMEOUT)
    except requests.exceptions.Timeout:
        raise DomainError("FBR did not answer within 30 seconds — try again shortly.", code="fbr_timeout")
    except requests.exceptions.ConnectionError as e:
        raise DomainError(f"Could not reach FBR's gateway: {e}", code="fbr_unreachable")
    return engine.parse_fbr_response(resp)


def validate(inv, actor=None):
    """Dry run — identical payload, records nothing at FBR, catches classification errors for free."""
    fbr = settings_for()
    token, sandbox = _require_token(fbr)
    filing = refresh_snapshot(inv)
    payload = build_payload(inv, filing, use_sandbox=sandbox)
    filing.last_payload = json.dumps(payload, ensure_ascii=False)
    filing.sandbox = sandbox
    filing.save(update_fields=["last_payload", "sandbox", "updated_at"])   # §8.2: payload BEFORE the call
    data, vr = _call(VALIDATE_URL[sandbox], token, payload)
    filing.validate_response = json.dumps(data, ensure_ascii=False)[:20000]
    filing.validate_code = str(vr.get("statusCode", ""))
    filing.validated_at = timezone.now()
    ok = vr.get("statusCode") == "00"
    filing.status = "validated" if ok else "failed"
    filing.error_text = "" if ok else engine.errors_of(vr)
    filing.save()
    AuditService.log("fbr.validated" if ok else "fbr.validate_failed", inv,
                     after={"code": filing.validate_code, "error": filing.error_text[:300]}, actor=actor)
    if not ok:
        raise DomainError(f"FBR refused the invoice: {engine.translate_error(filing.error_text)}", code="fbr_rejected")
    return filing


def submit(inv, actor=None):
    """Post for real. Only a validated filing may post (§8), and a submitted one is immutable."""
    fbr = settings_for()
    token, sandbox = _require_token(fbr)
    filing = FBRFiling.objects.filter(invoice=inv).first()
    if filing is None or filing.status != "validated" or filing.sandbox != sandbox:
        raise DomainError("Validate with FBR first — posting without a passed validation burns rejections.",
                          code="fbr_not_validated")
    payload = json.loads(filing.last_payload)
    data, vr = _call(POST_URL[sandbox], token, payload)
    filing.response = json.dumps(data, ensure_ascii=False)[:20000]
    filing.response_code = str(vr.get("statusCode", ""))
    ok = vr.get("statusCode") == "00"
    if ok:
        filing.fbr_invoice_number = data.get("invoiceNumber", "")
        filing.submitted_at = timezone.now()
        filing.status = "submitted"
        filing.error_text = ""
    else:
        filing.status = "failed"
        filing.error_text = engine.errors_of(vr)
    filing.save()
    AuditService.log("fbr.submitted" if ok else "fbr.submit_failed", inv,
                     after={"number": filing.fbr_invoice_number, "error": filing.error_text[:300]}, actor=actor)
    if not ok:
        raise DomainError(f"FBR refused the invoice: {filing.error_text}", code="fbr_rejected")
    return filing


# ── scenario tests (§10): FBR's sample payloads, seller fields and date replaced — NOTHING else ─────
MINIMAL_ITEM = {
    "hsCode": "0101.2100", "productDescription": "Test item", "rate": "18%",
    "uoM": "Numbers, pieces, units", "quantity": 1.0, "totalValues": 1180.0,
    "valueSalesExcludingST": 1000.0, "fixedNotifiedValueOrRetailPrice": 0,
    "salesTaxApplicable": 180.0, "salesTaxWithheldAtSource": 0, "extraTax": "",
    "furtherTax": 0, "sroScheduleNo": "", "fedPayable": 0, "discount": 0,
    "saleType": "Goods at standard rate (default)", "sroItemSerialNo": "",
}


def seed_scenarios():
    """SN001/SN002 from the guide's accepted minimal payload; the other 26 are FBR's own samples and
    must be pasted verbatim (the runner never recomputes a template's amounts — §10)."""
    from .models import FBRScenarioTemplate

    base = {"invoiceType": "Sale Invoice", "invoiceRefNo": "", "items": [dict(MINIMAL_ITEM)],
            "buyerProvince": "PUNJAB", "buyerAddress": "Lahore"}
    seeds = [
        ("SN001", "Goods at standard rate — Registered buyer",
         {**base, "buyerNTNCNIC": "3652565", "buyerBusinessName": "Scenario Buyer One",
          "buyerRegistrationType": "Registered", "scenarioId": "SN001"}),
        ("SN002", "Goods at standard rate — Unregistered buyer",
         {**base, "buyerNTNCNIC": "3520212345671", "buyerBusinessName": "Scenario Buyer Two",
          "buyerRegistrationType": "Unregistered", "scenarioId": "SN002"}),
    ]
    made = 0
    for sid, desc, payload in seeds:
        _, created = FBRScenarioTemplate.objects.get_or_create(scenario_id=sid,
                                                               defaults={"description": desc, "payload": payload})
        made += int(created)
    return made


def run_scenario(scenario_id, actor=None):
    import copy

    from .models import FBRScenarioRun, FBRScenarioTemplate

    fbr = settings_for()
    token, sandbox = _require_token(fbr)
    if not sandbox:
        raise DomainError("Scenario tests run against the SANDBOX only — switch back to sandbox mode first.",
                          code="fbr_not_sandbox")
    tpl = FBRScenarioTemplate.objects.filter(scenario_id=scenario_id).first()
    if tpl is None:
        raise DomainError(f"No template stored for {scenario_id} — paste FBR's sample payload for it first.",
                          code="fbr_no_template")
    payload = copy.deepcopy(tpl.payload)
    payload["sellerNTNCNIC"] = engine.trim_ntn(fbr.ntn or fbr.cnic or "")
    payload["sellerBusinessName"] = engine.fbr_text(fbr.business_name)
    payload["sellerProvince"] = engine.fbr_province(fbr.province)
    payload["sellerAddress"] = engine.fbr_text(fbr.address)
    payload["invoiceDate"] = date.today().strftime("%Y-%m-%d")
    # FBR dedupes identical invoices — vary the buyer name per run while testing (guide appendix)
    payload["buyerBusinessName"] = f"{payload.get('buyerBusinessName', 'Scenario Buyer')} {timezone.now():%H%M%S}"
    data, vr = _call(POST_URL[True], token, payload)
    passed = vr.get("statusCode") == "00"
    run = FBRScenarioRun.objects.create(
        scenario_id=scenario_id, passed=passed, payload=json.dumps(payload, ensure_ascii=False)[:20000],
        response=json.dumps(data, ensure_ascii=False)[:20000], status_code=str(vr.get("statusCode", "")),
        error_text="" if passed else engine.errors_of(vr))
    AuditService.log("fbr.scenario", run, after={"scenario": scenario_id, "passed": passed}, actor=actor)
    return run
