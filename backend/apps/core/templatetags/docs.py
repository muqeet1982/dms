"""Filters the printed documents need. Loaded as `{% load docs %}`."""
from decimal import Decimal, InvalidOperation

from django import template

from apps.core.words import amount_in_words

register = template.Library()


@register.filter
def in_words(value):
    """The total spelled out, for the line under it."""
    return amount_in_words(value)


@register.filter
def num(value, places=2):
    """A money or quantity figure with thousands separators — 1234.5 → 1,234.50."""
    try:
        d = Decimal(str(value or 0))
    except (InvalidOperation, TypeError):
        return value
    if places == 0 or d == d.to_integral_value():
        return f"{d:,.0f}" if places == 0 else f"{d:,.2f}"
    return f"{d:,.{places}f}"


@register.filter
def qty(value):
    """A quantity without trailing zeros: 12.000 → 12, 1.500 → 1.5."""
    try:
        d = Decimal(str(value or 0))
    except (InvalidOperation, TypeError):
        return value
    return f"{d.to_integral_value():,}" if d == d.to_integral_value() else f"{d.normalize():,}"


@register.filter
def cell(value):
    """A report figure: an amount (the API's 4-decimal money) reads 1,234.56; a quantity (3 decimals) keeps its own
    shape, 12 or 1.5 — the same rule as the report screen (owner, 27 Oct 2026: amounts with commas, 2 decimals)."""
    import re

    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"  # a count (invoices, lines, days) — never "7.00"
    if isinstance(value, str) and re.fullmatch(r"-?\d+\.\d{3}", value.strip()):
        return qty(value)
    return num(value)


@register.filter
def label(value):
    """`opening_debit` → "Opening Debit". Django's `cut` deletes the underscore instead of replacing it."""
    return str(value or "").replace("_", " ").strip().title()


@register.filter
def isnumber(value):
    """Whether a report cell is a figure, so it can be right-aligned and formatted and a code is left alone."""
    text = str(value or "").strip()
    if not text or text in {"-", "—"}:
        return False
    try:
        Decimal(text.replace(",", ""))
    except (InvalidOperation, ValueError):
        return False
    return not (text.count("-") > 1 or "/" in text)  # a date or a code is not a figure


@register.filter
def figure(value):
    """A totals value as a reader expects it: Yes/No for a flag, grouped digits for a number, as-is otherwise."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return num(value) if isnumber(value) else value


@register.filter
def fbr_rate(value):
    """FBR's prose rate in a narrow column: '18% along with rupees 60 per kilogram' → '18% + Rs.60'."""
    from apps.fbr import engine

    return engine.rate_label(value)
