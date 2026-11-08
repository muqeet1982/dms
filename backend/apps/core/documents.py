"""Shared pieces of a printed document."""


def logo_url(tenant, field="logo"):
    """
    Absolute reference WeasyPrint can load for the company logo, or None.

    The renderer gets the HTML as a string with no base URL, so a relative /media/ path silently prints
    nothing. Local storage therefore needs file://, and S3 needs the signed URL. A logo that cannot be
    resolved must never cost anybody the document, so every failure here falls through to no logo.
    """
    logo = getattr(tenant, field, None)
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


def invoice_issuer(tenant, tax):
    """
    Who an invoice says it is from (owner, 8 Nov 2026): a sales tax invoice and a plain one each carry their own name,
    address, phone, email, NTN, bank details and logo from Settings, Company, Invoices. Whatever is left blank there is
    the company profile's. A sales tax invoice always prints the NTN, set apart from the address (owner: "NTN should be
    clearly mentioned on every sales tax invoice"), falling back to the FBR registration's when the profile has none.
    """
    if tenant is None:
        return {"tax_lines": []}
    settings = tenant.settings or {}
    face = "tax_invoice_" if tax else "plain_invoice_"

    def own(key):
        return str(settings.get(face + key) or "").strip()

    numbers = {str(k).strip().upper(): str(v).strip() for k, v in (tenant.tax_numbers or {}).items() if str(v or "").strip()}
    ntn = own("ntn") or numbers.get("NTN", "")
    if tax and not ntn:
        try:
            from apps.fbr.models import FBRSettings

            ntn = (FBRSettings.objects.values_list("ntn", flat=True).first() or "").strip()
        except Exception:  # pragma: no cover - no FBR set-up is simply no NTN
            ntn = ""
    lines = [("NTN", ntn)] if ntn else []
    if tax and (own("strn") or numbers.get("STRN")):
        lines.append(("STRN", own("strn") or numbers["STRN"]))
    # any other registration number the company keeps (a licence, a PRA number) prints as it always has
    lines += [(str(k).strip(), str(v).strip()) for k, v in (tenant.tax_numbers or {}).items() if str(v or "").strip() and str(k).strip().upper() not in ("NTN", "STRN")]
    return {
        "name": own("name") or tenant.display_name, "address": own("address") or tenant.address, "phone": own("phone") or tenant.phone,
        "email": own("email") or tenant.email, "tax_lines": lines, "bank_details": own("bank_details") or str(settings.get("bank_details") or "").strip(),
        "logo_url": logo_url(tenant, "tax_invoice_logo" if tax else "plain_invoice_logo") or logo_url(tenant),
    }


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
    resp["Content-Disposition"] = content_disposition(filename)
    return resp


def content_disposition(name, ext="pdf", disposition="inline"):
    """
    The file name a download keeps (owner, 27 Oct 2026: invoices save as "TDN-INV-00062 - House Of Charbel.pdf").
    Characters no file system accepts are dropped and spaces collapsed; a plain ASCII `filename` for old clients
    and an RFC 5987 `filename*` so a name in Urdu or with accents arrives intact.
    """
    import re
    from urllib.parse import quote

    clean = re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", str(name or "document"))).strip(" .")[:150] or "document"
    ascii_name = re.sub(r"[\s.-]+$", "", re.sub(r"\s+", " ", clean.encode("ascii", "ignore").decode())).strip() or "document"
    return f'{disposition}; filename="{ascii_name}.{ext}"; filename*=UTF-8\'\'{quote(f"{clean}.{ext}")}'
