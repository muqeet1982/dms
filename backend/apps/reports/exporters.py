import csv
import datetime
import io
import re
from decimal import Decimal

from django.utils import timezone

_ISO_HEAD = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]|$)")


def cell(v):
    """
    A date in an export reads the way it reads on screen: 12/10/2026.

    Report rows arrive as the API serialised them, so a date is usually an ISO string by the time it gets here —
    which is why the downloaded file used to disagree with the page the person was looking at. A datetime is
    moved into the local day first: a receipt entered at nine in the evening in Karachi is stored as the next
    day in UTC, and exporting it under tomorrow's date is how a day's takings stop adding up.
    """
    if v is None or isinstance(v, (bool, int, float, Decimal)):
        return v
    if isinstance(v, datetime.datetime):
        return timezone.localtime(v).strftime("%d/%m/%Y %H:%M") if timezone.is_aware(v) else v.strftime("%d/%m/%Y %H:%M")
    if isinstance(v, datetime.date):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, str):
        m = _ISO_HEAD.match(v)
        if m is None:
            return v
        from django.utils.dateparse import parse_date, parse_datetime

        # A plain day has no time to print: `parse_datetime` would happily read "2026-10-12" as midnight and
        # hang a 00:00 on every order date in the file.
        try:
            if not m.group(1):
                d = parse_date(v)
                return d.strftime("%d/%m/%Y") if d is not None else v
            dt = parse_datetime(v)
            if dt is not None:
                return (timezone.localtime(dt) if timezone.is_aware(dt) else dt).strftime("%d/%m/%Y %H:%M")
        except ValueError:
            return v  # shaped like a date but is not one (a code, a bad row): print it as it came
    return v


# a column of units or counts — its figures keep their own shape (12, 1.5), never an amount's "12.00"
QTY_COL = re.compile(r"(^|_)(qty|quantity|per_box|packing|count|units|boxes|pieces|days|lines|stops|orders|invoices)(_|$)", re.I)


def _qty_shape(col, v):
    """A whole figure in a quantity column goes to the PDF as an int (the `cell` filter prints ints bare)."""
    if QTY_COL.search(str(col)) and isinstance(v, str) and re.fullmatch(r"-?\d+(\.0+)?", v.strip()):
        return int(v.split(".")[0])
    return v


# columns that hold codes, not amounts, even when every value is made of digits (a UPC, an NTN, a phone number)
IDENT_COL = re.compile(r"(^|_)(code|sku|upc|barcode|ntn|strn|cnic|phone|mobile|whatsapp|number|no|batch|reference|ref)(_|$)", re.I)


def numeric_columns(result, cols, rows):
    """The columns that read right-aligned: every value a figure (owner, 8 Nov 2026: "if text, should be left align and if
    number then right align ... in all tables and report"). The report screen applies the same rule."""
    from apps.core.templatetags.docs import isnumber

    text = set(result.get("text_columns") or ())
    out = set()
    for i, c in enumerate(cols):
        if c in text or IDENT_COL.search(str(c)):
            continue
        values = [r["cells"][i] for r in rows if str(r["cells"][i]).strip() not in ("", "—")]
        if values and all(isnumber(v) for v in values):
            out.add(i)
    return out


def _col_label(result, c):
    return (result.get("column_labels") or {}).get(c) or c.replace("_", " ").title()


def _flat_headers(result, cols):
    """Group + sub-label flattened to one row ("Opening Stock Qty") for CSV and plain consumers."""
    groups = result.get("header_groups")
    if not groups:
        return [_col_label(result, c) for c in cols]
    out, i = [], 0
    for g in groups:
        for _ in range(g["span"]):
            if i < len(cols):
                sub = _col_label(result, cols[i])
                out.append(f"{g['label']} {sub}".strip())
                i += 1
    out.extend(_col_label(result, c) for c in cols[i:])
    return out


def _footer_totals(result):
    """The figures printed under the table. A statement whose rows already carry its totals prints them only there."""
    return {} if result.get("totals_in_rows") else (result.get("totals") or {})


# how Python writes a figure with many decimals: 0E-7 (always a capital E and a sign, which no code in the catalogue has)
_SCIENTIFIC = re.compile(r"^-?\d(\.\d+)?E[+-]\d+$")


def phone_shape(v):
    """
    A Pakistani mobile number the one way, 0323 4833862, however it was typed (owner, 26 Nov 2026: Overdue invoices read
    03358351090 as the amount 3,358,351,090.00 and +92 323 4833862 beside it): +92 323 4833862, 923234833862 and
    3234833862 all read 0323 4833862. Anything else — a landline, a placeholder, two digits — is shown as typed.
    """
    raw = str(v or "").strip().rstrip(",").strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 12 and digits.startswith("92"):
        digits = "0" + digits[2:]
    elif len(digits) == 10 and digits.startswith("3"):
        digits = "0" + digits
    if len(digits) == 11 and digits.startswith("03"):
        return f"{digits[:4]} {digits[4:]}"
    return raw


def plain_figures(v, column=""):
    """
    A report's figures, never in scientific notation. A cost that nets to exactly zero (all of it came back) is 3 decimals
    of quantity × 4 of cost, and Python writes that zero as 0E-7: COGS and Gross Margin of Sales by Sale Person showed it
    (owner, 25 Nov 2026). Excel kept it as text and the column lost its right alignment as well.
    """
    if isinstance(v, dict):
        return {k: plain_figures(x, str(k)) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [plain_figures(x, column) for x in v]
    if isinstance(v, (str, Decimal)) and _SCIENTIFIC.match(str(v)) and not IDENT_COL.search(column):
        d = Decimal(str(v))
        text = f"{d:f}" if d else "0"
        return text if isinstance(v, str) else Decimal(text)
    return v


def without_columns(result, hide):
    """
    The report without the columns unticked for this file (owner, 25 Nov 2026, Sales by Sale Person). A column's figure
    under the table goes with it: a sheet printed without COGS must not carry its total at the bottom. A heading over
    several columns spans the ones left, and a total row keeps its name when the column that held it is left out.
    """
    from apps.core.exceptions import DomainError

    hide = set(hide)
    cols = result.get("columns") or (list(result["rows"][0].keys()) if result["rows"] else [])
    out = {**result, "columns": [c for c in cols if c not in hide]}
    if result.get("export_columns"):
        out["export_columns"] = [c for c in result["export_columns"] if c not in hide]
    # a link or a download button alone prints nothing (to_pdf leaves them out)
    printable = [c for c in out["columns"] if c not in (result.get("downloads") or {}) and not (c == "view" and "view" in (result.get("links") or {}))]
    if not printable or out.get("export_columns") == []:
        raise DomainError("Keep at least one column.", status_code=400, code="no_columns")
    if result.get("header_groups"):
        groups, at = [], 0
        for g in result["header_groups"]:
            span = sum(1 for c in cols[at:at + g["span"]] if c not in hide)
            at += g["span"]
            if span:
                groups.append({**g, "span": span})
        out["header_groups"] = groups if any(g["label"] for g in groups) else None
    out["totals"] = {k: v for k, v in (result.get("totals") or {}).items() if k not in hide}
    first = out["columns"][0]
    if cols[0] in hide:
        out["rows"] = [{**r, first: r[cols[0]]} if r.get("kind") == "subtotal" and r.get(cols[0]) and not r.get(first) else r for r in result["rows"]]
    return out


def to_csv(result, title=""):
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = result.get("export_columns") or result.get("columns") or (list(result["rows"][0].keys()) if result["rows"] else [])
    w.writerow(_flat_headers(result, cols))
    for r in result["rows"]:
        w.writerow([cell(r.get(c, "")) for c in cols])
    if _footer_totals(result):
        w.writerow([])
        for k, v in _footer_totals(result).items():
            w.writerow([k, v])
    return buf.getvalue().encode("utf-8-sig")


def to_xlsx(result, title=""):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    # openpyxl refuses \ / * ? : [ ] in a sheet title — a report named with them must not 500 the export
    import re

    ws.title = (re.sub(r"[\\/*?:\[\]]", "-", title or "Report").strip() or "Report")[:30]
    cols = result.get("export_columns") or result.get("columns") or (list(result["rows"][0].keys()) if result["rows"] else [])
    text_cols = set(result.get("text_columns") or ())
    groups = result.get("header_groups")
    if groups:
        # the two-row heading: merged group names over their sub-columns
        from openpyxl.utils import get_column_letter

        row, col = [], 1
        for g in groups:
            row.append(g["label"])
            row.extend([""] * (g["span"] - 1))
            col += g["span"]
        ws.append(row)
        col = 1
        for g in groups:
            if g["span"] > 1:
                ws.merge_cells(f"{get_column_letter(col)}1:{get_column_letter(col + g['span'] - 1)}1")
            col += g["span"]
    ws.append([_col_label(result, c) for c in cols])
    header_rows = 2 if groups else 1
    for n in range(1, header_rows + 1):
        for head in ws[n]:  # not `cell`: that is the function above, and shadowing it broke every xlsx export
            head.font = Font(bold=True, color="FFFFFF")
            head.fill = PatternFill("solid", fgColor="F26522")
            head.alignment = Alignment(wrap_text=True, vertical="center")   # a long column name wraps, it is not cut off
    for r in result["rows"]:
        row = []
        for c in cols:
            v = cell(r.get(c, ""))
            # a value a spreadsheet cell cannot hold (an id, a reference) goes in as text: one UUID used to fail the
            # whole file with a 500 (Journal entries, 14 Nov 2026)
            if v is not None and not isinstance(v, (str, bool, int, float, Decimal, datetime.date)):
                v = str(v)
            if isinstance(v, str) and c not in text_cols and not IDENT_COL.search(str(c)):   # 03358351090 kept its 0
                try:
                    if v and (v.replace(".", "", 1).replace("-", "", 1).isdigit()):
                        v = float(Decimal(v))
                except Exception:
                    pass
            row.append(v)
        ws.append(row)
        if r.get("kind") in ("heading", "subtotal", "profit", "net"):   # a statement's headings and totals stand out, as they do on the page
            for written in ws[ws.max_row]:
                written.font = Font(bold=True)
    if _footer_totals(result):
        ws.append([])
        for k, v in _footer_totals(result).items():
            ws.append([k, float(v) if isinstance(v, str) and v.replace(".", "", 1).replace("-", "", 1).isdigit() else v])
    from openpyxl.utils import get_column_letter as _gcl

    for i in range(1, ws.max_column + 1):   # merged group cells have no column_letter of their own
        ws.column_dimensions[_gcl(i)].width = 18
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_pdf(result, title="", tenant=None, params=None, param_labels=None):
    from django.template.loader import render_to_string
    from weasyprint import HTML

    cols = result.get("columns") or (list(result["rows"][0].keys()) if result["rows"] else [])
    if "view" in (result.get("links") or {}):
        cols = [c for c in cols if c != "view"]   # a link to click: nothing to print
    cols = [c for c in cols if c not in (result.get("downloads") or {})]   # a button that downloads a file: nothing to print either
    rows = [{"cells": [_qty_shape(c, cell(r.get(c, ""))) for c in cols], "kind": r.get("kind", "")} for r in result["rows"]]
    from apps.core.documents import logo_url

    # Fit by MEASURING, not by counting columns: a few wide columns (dates, long names) overflow a portrait
    # page at seven columns while ten narrow ones fit. Each column costs its widest text (capped — a long
    # name may wrap); the sum decides portrait vs landscape, and when even landscape is tight the type
    # steps down a size so every report lands inside the page.
    labels = [_col_label(result, c) for c in cols]
    widths = []
    for i, c in enumerate(cols):
        w = len(str(labels[i]))
        for r in rows[:400]:
            w = max(w, len(str(r["cells"][i])))
        widths.append(min(w, 34))
    units = sum(widths) + 2 * len(cols)
    landscape = units > 105
    budget = 160 if landscape else 105
    fit = "" if units <= budget else ("fit-sm" if units <= budget * 1.35 else ("fit-xs" if units <= budget * 1.8 else "fit-tiny"))

    # a small line under a cell (the codes under a product's name), put in after the sizing above: it may wrap
    notes = result.get("notes") or {}
    if notes:
        from django.utils.html import format_html

        for source, row in zip(result["rows"], rows):
            for i, c in enumerate(cols):
                if source.get(notes.get(c)):
                    row["cells"][i] = format_html('{}<div class="cell-note">{}</div>', row["cells"][i], source[notes[c]])

    html = render_to_string("reports/report.html", {
        "title": title, "cols": cols, "col_labels": labels if result.get("column_labels") else None,
        # a heading sits over its figures on the right, and over a column of words on the left
        "num_cols": numeric_columns(result, cols, rows), "bold_cols": [i for i, c in enumerate(cols) if (result.get("column_styles") or {}).get(c) == "bold"], "faint_zeros": result.get("faint_zeros"), "dark_header": result.get("header_groups") and not result.get("plain_header"),
        "groups": result.get("header_groups"), "rows": rows, "totals": _footer_totals(result), "tenant": tenant,
        "params": {(param_labels or {}).get(k, k): v for k, v in {**{k: cell(v) for k, v in (params or {}).items()}, **(result.get("print_params") or {})}.items() if v not in (None, "")}, "landscape": landscape, "fit": fit,
        "logo_url": logo_url(tenant), "printed_at": f"Printed {timezone.localtime():%d/%m/%Y %H:%M}",
    })
    return HTML(string=html).write_pdf()
