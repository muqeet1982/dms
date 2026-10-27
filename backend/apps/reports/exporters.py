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


def to_csv(result, title=""):
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = result.get("columns") or (list(result["rows"][0].keys()) if result["rows"] else [])
    w.writerow(_flat_headers(result, cols))
    for r in result["rows"]:
        w.writerow([cell(r.get(c, "")) for c in cols])
    if result.get("totals"):
        w.writerow([])
        for k, v in result["totals"].items():
            w.writerow([k, v])
    return buf.getvalue().encode("utf-8-sig")


def to_xlsx(result, title=""):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    # openpyxl refuses \ / * ? : [ ] in a sheet title — a report named with them must not 500 the export
    import re

    ws.title = (re.sub(r"[\\/*?:\[\]]", "-", title or "Report").strip() or "Report")[:30]
    cols = result.get("columns") or (list(result["rows"][0].keys()) if result["rows"] else [])
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
    for r in result["rows"]:
        row = []
        for c in cols:
            v = cell(r.get(c, ""))
            if isinstance(v, str):
                try:
                    if v and (v.replace(".", "", 1).replace("-", "", 1).isdigit()):
                        v = float(Decimal(v))
                except Exception:
                    pass
            row.append(v)
        ws.append(row)
    if result.get("totals"):
        ws.append([])
        for k, v in result["totals"].items():
            ws.append([k, float(v) if isinstance(v, str) and v.replace(".", "", 1).replace("-", "", 1).isdigit() else v])
    from openpyxl.utils import get_column_letter as _gcl

    for i in range(1, ws.max_column + 1):   # merged group cells have no column_letter of their own
        ws.column_dimensions[_gcl(i)].width = 18
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_pdf(result, title="", tenant=None, params=None):
    from django.template.loader import render_to_string
    from weasyprint import HTML

    cols = result.get("columns") or (list(result["rows"][0].keys()) if result["rows"] else [])
    rows = [[_qty_shape(c, cell(r.get(c, ""))) for c in cols] for r in result["rows"]]
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
            w = max(w, len(str(r[i])))
        widths.append(min(w, 34))
    units = sum(widths) + 2 * len(cols)
    landscape = units > 105
    budget = 160 if landscape else 105
    fit = "" if units <= budget else ("fit-sm" if units <= budget * 1.35 else "fit-xs")

    html = render_to_string("reports/report.html", {
        "title": title, "cols": cols, "col_labels": labels if result.get("column_labels") else None,
        "groups": result.get("header_groups"), "rows": rows, "totals": result.get("totals", {}), "tenant": tenant,
        "params": params or {}, "landscape": landscape, "fit": fit,
        "logo_url": logo_url(tenant), "printed_at": f"Printed {timezone.localtime():%d/%m/%Y %H:%M}",
    })
    return HTML(string=html).write_pdf()
