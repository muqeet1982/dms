"""The printed sales order and sales invoice — ONE renderer each, shared by the office API and the rep API
(/ext/v1), so a tax customer's documents read the same whoever prints or shares them."""
from decimal import Decimal

from apps.core.documents import render_doc


def order_pdf(request, o):
    from apps.sales.services import InvoiceService, gst_pct

    lines = list(o.lines.select_related("variant__product", "packaging", "tax1_type", "tax2_type").order_by("sequence"))
    tax_shown = Decimal(str(o.tax1_total or 0)) + Decimal(str(o.tax2_total or 0))
    other = InvoiceService.other_tax_context(o, lines)
    for l in lines:
        l.pending = max(Decimal(str(l.qty_ordered)) - Decimal(str(l.qty_delivered)), Decimal("0"))
        l.discount_total = Decimal(str(l.discount_amount or 0)) + Decimal(str(l.scheme_discount or 0))
        l.tax_amount = Decimal(str(l.tax1_amount or 0)) + Decimal(str(l.tax2_amount or 0))
        l.taxable = Decimal(str(l.line_total or 0)) - l.tax_amount - l.other_tax
        l.gross = Decimal(str(l.qty_ordered or 0)) * Decimal(str(l.unit_price or 0))
    return render_doc(request, "sales/order.html", {
        "o": o, "lines": lines, **other,
        # the tax face follows the customer's flag alone (owner 26 Oct 2026: "if customer tax is off then do not
        # show") — an unflagged order's legacy tax-class tax does not turn it into a tax document
        "tax_on": bool(o.charge_tax), "tax_shown": tax_shown,
        "taxable_total": Decimal(str(o.subtotal or 0)) - Decimal(str(o.discount or 0)) - Decimal(str(o.scheme_discount or 0)),
        "total_discount": Decimal(str(o.discount or 0)) + Decimal(str(o.scheme_discount or 0)),
        "show_progress": any(l.qty_dispatched or l.qty_delivered for l in lines),
        "tax1_name": next((l.tax1_type.name for l in lines if l.tax1_type_id), None),
        "gst_pct": gst_pct(l.tax1_rate for l in lines),
        "tax2_name": next((l.tax2_type.name for l in lines if l.tax2_type_id), None),
    }, o.number, title="SALES ORDER", ref=o.number)


def invoice_pdf(request, inv):
    from apps.sales.services import InvoiceService

    lines = list(inv.lines.select_related("variant__product", "batch").order_by("sequence"))
    extra = InvoiceService.present_lines(inv, lines)
    # a filed tax invoice must print FBR's number with a QR encoding it — the legal requirement
    filing = getattr(inv, "fbr_filing", None)
    if filing is not None and filing.status == "submitted" and filing.fbr_invoice_number:
        import base64
        import io

        import segno

        buf = io.BytesIO()
        segno.make(filing.fbr_invoice_number).save(buf, kind="png", scale=3, border=1)
        extra["fbr_number"] = filing.fbr_invoice_number
        extra["fbr_qr"] = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        extra["fbr_sandbox"] = filing.sandbox
    doc_title = "SALES TAX INVOICE" if extra.get("tax_on") else "SALES INVOICE"
    # every invoice download carries its number AND who it is for (owner, 27 Oct 2026)
    return render_doc(request, "sales/invoice.html", {"inv": inv, "lines": lines, **extra}, f"{inv.number} - {inv.customer.name}",
                      title=doc_title, ref=inv.number)
