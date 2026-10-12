"""Numbers written out, the way an invoice in Pakistan writes them."""
from decimal import ROUND_HALF_UP, Decimal

ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
        "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
# lakh and crore, not million — "Rupees Twelve Lakh" is what a customer here expects to read on an invoice
GROUPS = [(10_000_000, "crore"), (100_000, "lakh"), (1_000, "thousand"), (100, "hundred")]


def _words(n: int) -> list:
    if n < 20:
        return [ONES[n]] if n else []
    if n < 100:
        return [TENS[n // 10]] + ([ONES[n % 10]] if n % 10 else [])
    for size, name in GROUPS:
        if n >= size:
            head = _words(n // size) + [name]
            rest = n % size
            # "three hundred and twenty", but "three lakh twenty thousand" — the 'and' belongs before the last
            # group under a hundred, which is how it is read aloud
            return head + (["and"] if rest and rest < 100 else []) + _words(rest)
    return []


def amount_in_words(value, currency="Rupees", fraction="Paisa") -> str:
    """
    `1234.50` → "Rupees One Thousand Two Hundred and Thirty Four and Paisa Fifty Only".

    Printed under the total because that is the line a customer disputes, and digits alone are easy to argue
    with. An absent value gives "" — nothing to print — while an actual zero is spelled out, because a zero
    total is a fact about the document and a blank line looks like a fault in it.
    """
    if value in (None, ""):
        return ""
    amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "Minus " if amount < 0 else ""
    amount = abs(amount)
    whole, paisa = int(amount), int((amount - int(amount)) * 100)
    parts = []
    if whole:
        parts.append(f"{currency} " + " ".join(_words(whole)))
    if paisa:
        parts.append(f"{fraction} " + " ".join(_words(paisa)))
    if not parts:
        return f"{currency} Zero Only"
    return sign + " and ".join(parts).title().replace(" And ", " and ") + " Only"
