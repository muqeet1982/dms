"""
How a date is written in this business: 12/10/2026.

Django renders `{{ order.date }}` through the active locale, and `en` means the American reading — "Oct. 12,
2026" in prose and 10/12/2026 in the short form, which is the one shape nobody here would read correctly. These
override the locale's own formats everywhere at once: the printed invoice, the GRN, the statement, the dispatch
sheet, and any template written later. The API is untouched — DRF serialises its own ISO-8601, which is what the
apps parse.
"""

DATE_FORMAT = "d/m/Y"
DATETIME_FORMAT = "d/m/Y H:i"
SHORT_DATE_FORMAT = "d/m/Y"
SHORT_DATETIME_FORMAT = "d/m/Y H:i"
TIME_FORMAT = "H:i"
YEAR_MONTH_FORMAT = "F Y"
MONTH_DAY_FORMAT = "j F"

# Day first when something is typed in (the Django admin, a management form). ISO stays accepted: scripts and
# imported files write it, and refusing it would break them for a cosmetic reason.
DATE_INPUT_FORMATS = ["%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%Y-%m-%d"]
DATETIME_INPUT_FORMATS = [
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
]
