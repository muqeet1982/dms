"""
The FBR arithmetic and text rules — one implementation, everything calls it (guide §6, §11).

Every function here encodes a trap that cost live debugging time; the comments say which.
"""
import json
import re
from decimal import ROUND_HALF_UP, Decimal

TWO = Decimal("0.01")


def round2(v) -> Decimal:
    """ROUND_HALF_UP to two places, applied at EVERY step (§11.10) — floats drift and FBR recomputes."""
    return Decimal(str(v or 0)).quantize(TWO, rounding=ROUND_HALF_UP)


# ── §11.1: never strip letters from an NTN; a CNIC's hyphens are formatting, not a check digit ──────
# re.sub(r"[^0-9]", "", "A014841-0") → "0148410" files under a DIFFERENT taxpayer. Introduced twice
# before this guide existed; do not write a second copy of this function anywhere.
def trim_ntn(value: str) -> str:
    v = (value or "").strip()
    if "-" not in v:
        return v
    bare = v.replace("-", "")
    if len(bare) == 13 and bare.isdigit():
        return bare                  # '42101-1234567-1' → '4210112345671'  (CNIC)
    return v.split("-")[0]           # 'A014841-0'       → 'A014841'        (NTN, check digit dropped)


# ── §11.2: FBR's gateway refuses any payload containing a double quote — even validly escaped ───────
def fbr_text(value: str) -> str:
    return (value or "").replace('"', "''")


# ── §6.2: a rate is PROSE ("18%", "Exempt", "Rs.700/MT", "18% and Rs. 80 per Liter") ────────────────
_FIXED_TAX_RE = re.compile(r"(?:\brs\.?|\brupees)\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)


def gst_rate_num(rate_str) -> Decimal:
    """The percentage part; 0 where there is none."""
    if not rate_str:
        return Decimal("0")
    s = str(rate_str).strip()
    if s.lower().startswith(("exempt", "rs.", "rs ")):
        return Decimal("0")
    if " " in s:                      # "18% along with rupees 60 per kilogram"
        s = s.split()[0]
    s = s.rstrip("%")
    try:
        return Decimal(s)
    except Exception:
        return Decimal("0")


def fixed_tax_amount(rate_str) -> Decimal:
    """The fixed per-unit amount; 0 where there is none. float('700/MT') raises — hence the regex."""
    if not rate_str:
        return Decimal("0")
    m = _FIXED_TAX_RE.search(str(rate_str))
    if not m:
        return Decimal("0")
    try:
        return Decimal(m.group(1).replace(",", ""))
    except Exception:
        return Decimal("0")


# ── §6.3: 3rd Schedule goods tax on the PRINTED retail price when higher — and the MRP is PER UNIT ──
_MRP_SALE_TYPES = {"3rd schedule goods"}


def is_mrp_sale_type(sale_type) -> bool:
    return (sale_type or "").strip().lower() in _MRP_SALE_TYPES


def compute_line(*, quantity, unit_price, discount_amount, rate_str, sale_type="", mrp=None,
                 fed_rate=0, extra_rate=0, further_rate=0, withheld_rate=0) -> dict:
    """§6.1, the one pricing function. Returns every payload money figure for one line."""
    qty = Decimal(str(quantity or 0))
    gross = round2(qty * Decimal(str(unit_price or 0)))
    discount = round2(discount_amount or 0)
    taxable = round2(gross - discount)

    base = taxable
    if is_mrp_sale_type(sale_type):
        retail = round2(Decimal(str(mrp or 0)) * qty)   # per-unit MRP × qty — comparing per-unit to the
        if retail > base:                                # line hides itself: right at qty 1, wrong above
            base = retail
    pct = gst_rate_num(rate_str)
    fixed = fixed_tax_amount(rate_str)
    gst = round2(base * pct / 100 + qty * fixed)

    fed = round2(taxable * Decimal(str(fed_rate or 0)) / 100)
    extra = round2(taxable * Decimal(str(extra_rate or 0)) / 100)
    further = round2(taxable * Decimal(str(further_rate or 0)) / 100)
    withheld = round2(taxable * Decimal(str(withheld_rate or 0)) / 100)
    total = round2(taxable + gst + extra + further + fed - withheld)   # withholding is SUBTRACTED
    return {"discount": discount, "valueSalesExcludingST": taxable, "salesTaxApplicable": gst,
            "fedPayable": fed, "extraTax": extra, "furtherTax": further,
            "salesTaxWithheldAtSource": withheld, "totalValues": total}


# ── §7.4: FBR refuses two lines sharing HS code AND description — combine at submission ─────────────
_SUMMED = ("totalValues", "valueSalesExcludingST", "salesTaxApplicable",
           "salesTaxWithheldAtSource", "extraTax", "furtherTax", "fedPayable", "discount")


def _f(v):
    return float(round2(v))


def combine_duplicate_lines(items):
    merged, order = {}, []
    for item in items:
        key = (item.get("hsCode", ""), item.get("productDescription", ""), item.get("rate", ""),
               item.get("saleType", ""), item.get("uoM", ""),
               item.get("sroScheduleNo", ""), item.get("sroItemSerialNo", ""))
        if key in merged:
            into = merged[key]
            into["quantity"] = round(float(into.get("quantity", 0)) + float(item.get("quantity", 0)), 3)
            for f in _SUMMED:
                a, b = item.get(f), into.get(f)
                if a in ("", None) and b in ("", None):
                    continue                      # keeps extraTax's "" when both are blank
                into[f] = _f(Decimal(str(b or 0)) + Decimal(str(a or 0)))
            # 3rd Schedule: the retail price is PER UNIT, so it does not add up
            into["fixedNotifiedValueOrRetailPrice"] = max(
                float(into.get("fixedNotifiedValueOrRetailPrice") or 0),
                float(item.get("fixedNotifiedValueOrRetailPrice") or 0))
        else:
            merged[key] = item
            order.append(key)
    out = [merged[k] for k in order]
    seen = {}
    for item in out:
        ident = (item.get("hsCode", ""), item.get("productDescription", ""))
        seen[ident] = seen.get(ident, 0) + 1
        if seen[ident] > 1:          # still sharing HS + description: differ in rate — number them apart
            item["productDescription"] = f"{item['productDescription']} ({seen[ident]})"
    return out


# ── §9: four response shapes, including the not-quite-JSON one with a trailing comma ────────────────
def parse_fbr_response(resp):
    try:
        data = resp.json()
        return data, validation_of(data)
    except Exception:
        pass
    try:
        data = json.loads(re.sub(r",\s*([}\]])", r"\1", resp.text or ""))
    except Exception:
        return {}, {}
    return data, validation_of(data)


def validation_of(data):
    vr = data.get("validationResponse")
    if isinstance(vr, dict) and vr:
        return vr
    code = data.get("Code") or data.get("code")
    error = data.get("error") or data.get("Error")
    if code or error:
        return {"statusCode": str(code or "01"), "status": "Invalid", "error": error or "", "invoiceStatuses": []}
    return {}


def errors_of(vr) -> str:
    errors = [i["error"] for i in (vr.get("invoiceStatuses") or []) if i.get("error")]
    return "; ".join(errors) or vr.get("error") or f"FBR returned status {vr.get('statusCode', '')}"


# ── §4: the payload wants abbreviations; the provinces lookup (and our customer form) use full names ─
FBR_PROVINCE_MAP = {
    "PUNJAB": "PUNJAB", "SINDH": "SINDH", "KHYBER PAKHTUNKHWA": "KPK", "BALOCHISTAN": "BALOCHISTAN",
    "CAPITAL TERRITORY": "ICT", "ISLAMABAD CAPITAL TERRITORY": "ICT",
    "AZAD JAMMU AND KASHMIR": "AJK", "AZAD JAMMU & KASHMIR": "AJK",
    "GILGIT BALTISTAN": "GILGIT BALTISTAN", "GILGIT-BALTISTAN": "GILGIT BALTISTAN",
}


def fbr_province(name: str) -> str:
    return FBR_PROVINCE_MAP.get((name or "").strip().upper(), "")


# ── §7.3: scenarioId — sandbox only, must agree with the sale type and the buyer's registration ─────
_SALE_TYPE_SCENARIO = {
    "3rd schedule goods": "SN008", "exempt goods": "SN006", "goods at zero-rate": "SN007",
    "goods at reduced rate": "SN005", "services": "SN019", "goods (fed in st mode)": "SN017",
    "services (fed in st mode)": "SN018", "processing/conversion of goods": "SN016",
    "electricity supply to retailers": "SN013", "gas to cng stations": "SN014",
    "petroleum products": "SN012", "potassium chlorate": "SN022", "mobile phones": "SN015",
    "steel melting and re-rolling": "SN003", "ship breaking": "SN004", "cotton ginners": "SN009",
    "electric vehicle": "SN020", "cement /concrete block": "SN021",   # the space is FBR's, not a typo
    "cng sales": "SN023", "non-adjustable supplies": "SN025", "toll manufacturing": "SN011",
    "goods at standard rate (default)": None,                          # depends on the buyer
}


def scenario_for(sale_type, reg_type) -> str:
    s = _SALE_TYPE_SCENARIO.get((sale_type or "").strip().lower())
    if s is None:
        s = "SN002" if "unregistered" in (reg_type or "").lower() else "SN001"
    return s


# ── the party pre-flight (party guide §4): everything that would make a filing fail, ALL at once ────
def party_fbr_problems(customer) -> list:
    problems = []
    name = (getattr(customer, "business_name", "") or getattr(customer, "name", "") or "").strip()
    if not name:
        problems.append("Business name is required.")
    if '"' in name:
        problems.append("Business name contains a double quote; FBR rejects the whole invoice.")
    if not ((customer.ntn or "").strip() or (customer.cnic or "").strip()):
        problems.append("No NTN or CNIC — FBR files the invoice under the buyer's number.")
    province = (getattr(customer, "province", "") or "").strip()
    if not province:
        problems.append("Province is required.")
    elif not fbr_province(province):
        problems.append(f"Province '{province}' is not one FBR recognises.")
    if not " ".join(filter(None, [customer.address or "", getattr(customer, "city", "") or ""])).strip():
        problems.append("Address is required.")
    if not (getattr(customer, "fbr_registration_type", "") or "").strip():
        problems.append("Registration type is not set — use Check with FBR on the customer.")
    return problems


# ── the product pre-flight (HS guide §6, §12): everything that would fail a filing, all at once ─────
def product_fbr_problems(p) -> list:
    problems = []
    if not (p.fbr_hs_code or "").strip():
        problems.append("No HS code — pick it from FBR's list on the product.")
    if not (p.fbr_sale_type or "").strip():
        problems.append("No FBR sale type.")
    if not (p.fbr_rate_desc or "").strip():
        problems.append("No FBR rate.")
    if not (p.fbr_uom or "").strip():
        problems.append("No FBR unit of measure — it must be one the HS code allows.")
    rate = (p.fbr_rate_desc or "").strip()
    # §6: at any rate other than 18% the SRO schedule AND its item serial are both mandatory
    if rate and rate != "18%":
        if not (p.fbr_sro_schedule or "").strip():
            problems.append(f"Rate {rate} needs an SRO schedule (mandatory for any rate other than 18%).")
        if not (p.fbr_sro_item or "").strip():
            problems.append(f"Rate {rate} needs the SRO item serial too.")
    if is_mrp_sale_type(p.fbr_sale_type) and not (p.mrp_ex_tax or 0):
        problems.append("3rd Schedule goods need the printed retail price (MRP, per unit).")
    return problems


# ── §9.1 / §12: FBR's own words sometimes point at the wrong field — say what to actually check ─────
_ERROR_HINTS = {
    "does not match with provided sale type":
        " (FBR's message is misleading: first check the HS code EXISTS in FBR's list — this usually means "
        "the code itself is wrong, not the sale type)",
    "uom is not allowed against the provided hs code":
        " (pick the unit from the ones FBR allows for this HS code, on the product)",
    "in case of 3rd schedule":
        " (the tax base must be the printed retail price × quantity when that is higher)",
    "sro/schedule no. is mandatory":
        " (any rate other than 18% needs the SRO schedule and its item serial, on the product)",
}


def translate_error(text: str) -> str:
    low = (text or "").lower()
    for needle, hint in _ERROR_HINTS.items():
        if needle in low:
            return text + hint
    return text
