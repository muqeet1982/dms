"""Shared pieces of a printed document."""


def logo_url(tenant):
    """
    Absolute reference WeasyPrint can load for the company logo, or None.

    The renderer gets the HTML as a string with no base URL, so a relative /media/ path silently prints
    nothing. Local storage therefore needs file://, and S3 needs the signed URL. A logo that cannot be
    resolved must never cost anybody the document, so every failure here falls through to no logo.
    """
    logo = getattr(tenant, "logo", None)
    if not logo:
        return None
    try:
        return "file://" + logo.path
    except (NotImplementedError, ValueError, AttributeError):
        pass
    try:
        url = logo.url
        return url if url.startswith("http") else None
    except Exception:
        return None


def render_doc(request, template, ctx, filename, *, title=None, ref=None):
    """
    One printed document, as a PDF response the browser shows inline.

    Everything the house style in `templates/_doc.html` expects is filled in here — the logo, the company, the
    title the PDF itself carries, and when it was printed — so a new document template only has to describe
    itself. `inline` rather than an attachment because the first thing anybody does with an invoice is look at
    it, and the print dialog is one key away from there.
    """
    from django.http import HttpResponse
    from django.template.loader import render_to_string
    from django.utils import timezone
    from weasyprint import HTML

    tenant = getattr(request, "tenant", None)
    html = render_to_string(template, {
        "tenant": tenant,
        "logo_url": logo_url(tenant),
        "printed_at": f"Printed {timezone.localtime():%d/%m/%Y %H:%M}",
        "doc_title": title,
        "doc_ref": ref,
        **ctx,
    })
    resp = HttpResponse(HTML(string=html).write_pdf(), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{filename}.pdf"'
    return resp
