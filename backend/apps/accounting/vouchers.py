"""
Voucher codes, as the owner's old ERP printed them. A manual entry carries its voucher_type (CPV, BPV, JV …);
an automatic entry derives a report code from what it was and whether its money side is a bank or cash account.
"""

TITLES = {
    "CPV": "Cash Payment Voucher", "CRV": "Cash Receipt Voucher", "BPV": "Bank Payment Voucher",
    "BRV": "Bank Receipt Voucher", "JV": "Journal Voucher", "JRV": "Journal Receipt Voucher",
    "JPV": "Journal Payment Voucher", "JCV": "Journal Contra Voucher", "SJV": "Standing Journal Voucher",
    "CV": "Contra Voucher", "CLV": "Closing Voucher", "OB": "Opening Balance Entry",
}

# the signature strip on the printed voucher, per §6.4 of the guide
SIGNATURES_TWO = ["Prepared by", "Verified by"]
SIGNATURES_FIVE = ["Prepared", "Verified", "Approved", "Received", "Audited"]


def _is_bank(account):
    return (account.subtype or "") in ("bank", "pdc")


def _is_cash(account):
    return (account.subtype or "") in ("cash", "petty_cash") or "cash" in (account.name or "").lower()


def voucher_code(entry, lines=None):
    """The short code shown in lists, the ledger and on the printed voucher."""
    if entry.voucher_type:
        return entry.voucher_type.upper()
    if entry.type == "opening":
        return "OB"
    if entry.type == "closing":
        return "CLV"
    lines = list(lines) if lines is not None else list(entry.lines.select_related("account"))
    if not lines:
        return "JV"
    bank = any(_is_bank(l.account) for l in lines)
    if (entry.source_type or "") == "Payment":
        money_debited = any((_is_bank(l.account) or _is_cash(l.account)) and l.debit > 0 for l in lines)
        return ("BRV" if bank else "CRV") if money_debited else ("BPV" if bank else "CPV")
    if (entry.source_type or "") == "Expense":
        return "BPV" if any(_is_bank(l.account) and l.credit > 0 for l in lines) else "CPV"
    if all(_is_bank(l.account) or _is_cash(l.account) for l in lines):
        return "CV"
    return "JV"


def voucher_title(code):
    return TITLES.get(code, "Journal Voucher")


def signature_boxes(code):
    return SIGNATURES_TWO if code in ("BPV", "JV") else SIGNATURES_FIVE
