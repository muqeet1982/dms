"""Operational reports (§12): sales, purchasing, inventory, delivery, receivables."""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDay, TruncMonth
from django.utils import timezone

Z = Decimal("0")

from .exporters import phone_shape  # noqa: E402

def boxes_loose(units, packing):
    """A quantity as a warehouse counts it: (boxes, loose) where one box holds `packing` units."""
    units = Decimal(str(units or 0))
    per = int(packing or 0)
    if per <= 1:
        return "", str(units)
    whole = int(units // per)
    return str(whole), str(units - whole * per)


def _window(qs, field, date_from, date_to):
    if date_from:
        qs = qs.filter(**{f"{field}__gte": date_from})
    if date_to:
        qs = qs.filter(**{f"{field}__lte": date_to})
    return qs


def _inv_lines(date_from, date_to, **filters):
    from apps.sales.models import SalesInvoiceLine

    qs = SalesInvoiceLine.objects.filter(invoice__status__in=["posted", "partially_paid", "paid", "overdue", "returned"]).select_related("invoice", "variant", "variant__product", "variant__product__brand", "variant__product__supplier", "invoice__customer", "invoice__rep")
    qs = _window(qs, "invoice__date", date_from, date_to)
    return qs.filter(**filters)


def _net_sales(l):
    """What an invoice line sold for: its total without the taxes on it."""
    return l.line_total - l.tax1_amount - l.tax2_amount - l.fed_amount - l.extra_tax_amount - l.further_tax_amount


def _sale_person(inv):
    """
    (rep id, name) of who sold an invoice — the rule the invoice list's Sales person follows: the invoice's own rep, else
    its order's, else the sales rep on the order's or the delivery's trip. Reports used the invoice's rep alone and put
    the rest under "(staff)": TDN's nine in October were NAVEED's and Anza's, their rep only on the order (owner, 24 Nov 2026).
    """
    if inv.rep_id:
        return inv.rep_id, inv.rep.name
    o = inv.order if inv.order_id else None
    if o is not None and o.rep_id:
        return o.rep_id, o.rep.name
    if o is not None and o.trip_id and o.trip.rep_id:
        return o.trip.rep_id, o.trip.rep.name
    dn = inv.delivery_note if inv.delivery_note_id else None
    if dn is not None and dn.trip_id and dn.trip.rep_id:
        return dn.trip.rep_id, dn.trip.rep.name
    return None, "No Sale Person"


SALE_PERSON_RELATED = ("invoice__rep", "invoice__order__rep", "invoice__order__trip__rep", "invoice__delivery_note__trip__rep")


def _rep_names():
    """{rep id: name} where two sale persons share a name, the name carries the rep's code — TDN has two "Muhammad Haris"
    (TDN-REP-00009 and TDN-REP-00012), and the report showed two rows of the same name (24 Nov 2026)."""
    from collections import Counter

    from apps.partners.models import SupplierRep

    reps = list(SupplierRep.objects.values_list("pk", "name", "code"))
    seen = Counter(n.strip().lower() for _pk, n, _c in reps)
    return {str(pk): (f"{n} ({c})" if seen[n.strip().lower()] > 1 and c else n) for pk, n, c in reps}


def _company_per_unit(invoice_ids):
    """
    What the company (manufacturer) pays of an invoice's discount, per unit of each item: {(invoice, variant): amount}.
    Read off the claims raised when the invoice posted — every invoice has them, where the split on the line is stored
    only since 8 Nov 2026 — so it is the figure in the books. A cancelled claim pays nothing.
    """
    from apps.sales.models import CompanyClaimLine

    got = defaultdict(lambda: [Z, Z])
    for cl in CompanyClaimLine.objects.filter(claim__invoice_id__in=set(invoice_ids)).exclude(claim__status="cancelled").values("claim__invoice_id", "variant_id", "qty", "company_amount"):
        g = got[(cl["claim__invoice_id"], cl["variant_id"])]
        g[0] += cl["company_amount"]
        g[1] += cl["qty"]
    return {k: amount / qty for k, (amount, qty) in got.items() if qty}


def _q4(x):
    return x.quantize(Decimal("0.0001"))


def _returned_shares(lines):
    """
    The company's share on each returned line, {line id: amount}: it does not fund goods that came back. Never more in a
    note than the discount the note reverses — as the return posts it (sales.services.returned_company_shares).
    """
    funded = _company_per_unit(l.credit_note.invoice_id for l in lines if l.credit_note.invoice_id)
    share, per_note = {}, defaultdict(lambda: Z)
    for l in lines:
        share[l.pk] = funded.get((l.credit_note.invoice_id, l.variant_id), Z) * l.qty
        per_note[l.credit_note_id] += share[l.pk]
    out = {}
    for l in lines:
        most, all_of_it = l.credit_note.discount, per_note[l.credit_note_id]
        out[l.pk] = _q4(share[l.pk] * most / all_of_it if all_of_it > most else share[l.pk])
    return out


def _no_crumbs(x):
    """Under half a paisa is nothing: a tax line sold and all returned left -0.0040 of sales, read as -0.00 at 100% margin."""
    return Z if abs(x) < Decimal("0.005") else x


def _margin_pct(margin, net):
    """Of net sales. Where nothing was sold net of returns there is no rate: a loss on returns alone (-76 of -760) read as 10%."""
    return str((margin / net * 100).quantize(Decimal("0.01"))) if net > 0 else ""


def _sales_totals(out, keys):
    """The columns' totals, to the last decimal of the books (the rows are cleared of crumbs, see _no_crumbs), and the margin rate of the whole."""
    exact = out.pop("exact", {})
    totals = {k: str(exact[k] if k in exact else sum((Decimal(r[k]) for r in out["rows"]), Z)) for k in keys}
    pct = _margin_pct(Decimal(totals["gross_margin"]), Decimal(totals["net_sales"]))
    return {**totals, "margin_pct": pct} if pct else totals


MARGIN_LABELS = {"qty": "Net Qty", "net_sales": "Net Sales", "cogs": "COGS", "margin_pct": "Margin %"}


def _group(rows, key_fn, label_fn, extra_fn=None):
    """
    Gross Margin = sales + the company's share of the discount − COGS. The customer pays less by the whole discount, but the
    company pays its part of it: Waqas Traders' 7% was 3 of ours and 4 of Keune's, and the report took all 7 as ours — 3.20%
    for a sale that made 7.50% (owner, 25 Nov 2026). The income statement's gross profit counts it the same way.
    """
    out = defaultdict(lambda: {"qty": Z, "sales": Z, "share": Z, "cogs": Z, "invoices": set()})
    funded = _company_per_unit(l.invoice_id for l in rows)
    for l in rows:
        k = key_fn(l)
        r = out[k]
        r["label"] = label_fn(l)
        if extra_fn is not None and "extra" not in r:
            r["extra"] = extra_fn(l)
        r["qty"] += l.qty
        r["sales"] += _net_sales(l)
        r["share"] += _q4(funded.get((l.invoice_id, l.variant_id), Z) * l.qty)
        r["cogs"] += (l.qty + l.free_qty) * l.unit_cost   # free goods cost what they cost — as the invoice's COGS journal books them
        r["invoices"].add(l.invoice_id)
    result = [{"key": str(k), "label": r["label"], "qty": str(r["qty"]), "sales": str(r["sales"]), "company_share": str(r["share"]), "cogs": str(r["cogs"]),
               "invoices": len(r["invoices"]), **r.get("extra", {})} for k, r in out.items()]
    return {"columns": [], "rows": result, "totals": {}}   # columns, margin and totals come once the returns are off (_net_of_returns)


def sales_by_product(date_from=None, date_to=None, **_):
    """Headed Product Name, with the item code and UPC under the name (owner, 6 Nov 2026). A spreadsheet takes them as columns of their own, as text: 0062001 is not a number."""
    def codes(l):
        p = l.variant.product
        return {"item_code": p.item_code, "upc": p.upc, "codes": " · ".join(x for x in (f"Item Code: {p.item_code}" if p.item_code else "", f"UPC: {p.upc}" if p.upc else "") if x)}

    out = _group(_inv_lines(date_from, date_to), lambda l: l.variant_id, lambda l: l.variant.name, codes)
    out["column_labels"] = {"label": "Product Name", "upc": "UPC"}
    _with_returns(out, date_from, date_to, lambda l: (str(l.variant_id), l.variant.name, codes(l)))
    out["column_styles"] = {"label": "bold"}   # owner, 8 Nov 2026
    out["notes"] = {"label": "codes"}
    out["export_columns"] = ["label", "item_code", "upc", *out["columns"][1:]]
    out["text_columns"] = ["item_code", "upc"]
    return out


def sales_by_brand(date_from=None, date_to=None, **_):
    brand = lambda l: (str(l.variant.product.brand_id), l.variant.product.brand.name if l.variant.product.brand_id else "(no brand)")
    out = _group(_inv_lines(date_from, date_to), lambda l: brand(l)[0], lambda l: brand(l)[1])
    out["column_labels"] = {"label": "Brand"}
    return _with_returns(out, date_from, date_to, brand)


def sales_by_supplier(date_from=None, date_to=None, **_):
    maker = lambda l: (str(l.variant.product.supplier_id), l.variant.product.supplier.name if l.variant.product.supplier_id else "(no supplier)")
    out = _group(_inv_lines(date_from, date_to), lambda l: maker(l)[0], lambda l: maker(l)[1])
    out["column_labels"] = {"label": "Supplier Name"}
    return _with_returns(out, date_from, date_to, maker)


def _net_of_returns(out, date_from, date_to, by_type, key_of=None, blank=None, keep=None):
    """
    Returns in the same period, by credit-note date (as the ledger's Sales Returns), taken off each row's sales: Returns
    and Net Sales, the returned qty off Qty, the returned goods' cost off COGS and the company's share of their discount off
    Company Share, the margin on net sales. Sales by customer
    and by sale person counted every sale in full — November had 111 credit notes (600,612) and 49 TDN customers who sent
    back all they bought still showed their full sales (owner, 24 Nov 2026). `key_of(credit note)` → (row key, label);
    a key with returns and no sale in the period gets a row of its own (with `blank` for the caller's other columns).
    """
    from apps.sales.models import CreditNoteLine

    key_of = key_of or (lambda l: (str(l.credit_note.customer_id), l.credit_note.customer.name))
    cnl = _window(CreditNoteLine.objects.filter(credit_note__status="posted", **by_type).select_related(
        "variant__product__brand", "variant__product__supplier", "credit_note__customer__territory", "credit_note__customer__route",
        "credit_note__invoice__rep", "credit_note__invoice__order__rep", "credit_note__invoice__order__trip__rep",
        "credit_note__invoice__delivery_note__trip__rep"), "credit_note__date", date_from, date_to)
    if keep is not None:
        cnl = [l for l in cnl if keep(l)]
    unfunded = _returned_shares(cnl)
    back = defaultdict(lambda: {"value": Z, "share": Z, "cost": Z, "qty": Z, "name": "", "extra": {}})
    for l in cnl:
        got = key_of(l)
        key, name = got[0], got[1]
        b = back[key]
        b["extra"] = got[2] if len(got) > 2 else {}
        b["value"] += l.line_total - l.tax1_amount - l.tax2_amount - l.fed_amount - l.extra_tax_amount - l.further_tax_amount
        b["share"] += unfunded[l.pk]
        b["cost"] += l.qty * l.unit_cost
        b["qty"] += l.qty
        b["name"] = name
    have = {r["key"] for r in out["rows"]}
    for k, b in back.items():
        if k not in have:
            out["rows"].append({"key": k, "label": b["name"], "qty": "0", "sales": "0", "company_share": "0", "cogs": "0", "invoices": 0, **b["extra"], **(blank or {})})
    exact = {"net_sales": Z, "company_share": Z, "cogs": Z, "gross_margin": Z}
    for r in out["rows"]:
        b = back.get(r["key"], {"value": Z, "share": Z, "cost": Z, "qty": Z})
        net, share, cogs = Decimal(r["sales"]) - b["value"], Decimal(r["company_share"]) - b["share"], Decimal(r["cogs"]) - b["cost"]
        for k, v in (("net_sales", net), ("company_share", share), ("cogs", cogs), ("gross_margin", net + share - cogs)):
            exact[k] += v
        net, share, cogs = _no_crumbs(net), _no_crumbs(share), _no_crumbs(cogs)
        margin = net + share - cogs
        r.update({"returns": str(b["value"]), "net_sales": str(net), "qty": str(Decimal(r["qty"]) - b["qty"]), "company_share": str(share), "cogs": str(cogs),
                  "gross_margin": str(margin), "margin_pct": _margin_pct(margin, net)})
    out["rows"].sort(key=lambda r: -Decimal(r["net_sales"]))
    out["exact"] = exact


SALES_COLUMNS = ["label", "qty", "sales", "returns", "net_sales", "company_share", "cogs", "gross_margin", "margin_pct", "invoices"]
SALES_TOTALS = ("sales", "returns", "net_sales", "company_share", "cogs", "gross_margin")


def _with_returns(out, date_from, date_to, key_of):
    """Sales by product / brand / supplier / territory / route, net of the period's returns (owner, 24 Nov 2026: "yes fix")."""
    _net_of_returns(out, date_from, date_to, by_type={}, key_of=key_of)
    out["columns"] = list(SALES_COLUMNS)
    out["totals"] = _sales_totals(out, SALES_TOTALS)
    out["column_labels"] = {**out.get("column_labels", {}), **MARGIN_LABELS}
    return out


def _customer_names(keys):
    """{customer id: name}, with the customer's code where another customer has the same name — TDN has 34 such names
    ("She Cosmetics" of Township and of Walton), two rows of the report nobody could tell apart (25 Nov 2026)."""
    from django.db.models.functions import Lower, Trim

    from apps.partners.models import Customer

    shared = set(Customer.objects.annotate(n=Lower(Trim("name"))).values("n").annotate(c=Count("id")).filter(c__gt=1).order_by().values_list("n", flat=True))
    return {str(pk): (f"{n} ({c})" if n.strip().lower() in shared and c else n) for pk, n, c in Customer.objects.filter(pk__in=list(keys)).values_list("pk", "name", "code")}


def sales_by_customer(date_from=None, date_to=None, customer_type=None, **_):
    """Headed Customer Name, with who sold to them under the name (owner, 8 Nov 2026): the sale persons on its invoices in
    the period, else the reps the customer is assigned to. A spreadsheet takes them as a column of their own. Customer
    Type narrows it to salons, retailers ... (owner, 24 Nov 2026)."""
    by_type = {"invoice__customer__type": CUSTOMER_TYPES[customer_type]} if customer_type in CUSTOMER_TYPES else {}
    lines = _inv_lines(date_from, date_to, **by_type).select_related("invoice__customer", *SALE_PERSON_RELATED)
    out = _group(lines, lambda l: l.invoice.customer_id, lambda l: l.invoice.customer.name)
    _net_of_returns(out, date_from, date_to, by_type={"credit_note__customer__type": by_type["invoice__customer__type"]} if by_type else {})
    out["columns"] = list(SALES_COLUMNS)
    out["totals"] = _sales_totals(out, SALES_TOTALS)
    out["column_labels"] = dict(MARGIN_LABELS)
    sold_by = defaultdict(dict)
    names = _rep_names()
    for l in lines:
        rep_id, name = _sale_person(l.invoice)
        if rep_id:
            sold_by[str(l.invoice.customer_id)][names.get(str(rep_id), name)] = None
    from apps.partners.models import Customer

    assigned = {str(c.pk): [r.name for r in c.assigned_reps.all()] for c in Customer.objects.filter(pk__in=[r["key"] for r in out["rows"] if r["key"] not in sold_by]).prefetch_related("assigned_reps")}
    called = _customer_names(r["key"] for r in out["rows"])
    for r in out["rows"]:
        r["label"] = called.get(r["key"], r["label"])
        names = list(sold_by.get(r["key"], {})) or assigned.get(r["key"], [])
        r["sale_person"] = ", ".join(names)
        r["sold_by"] = f"Sale Person: {r['sale_person']}" if names else ""
    out["column_labels"] = {"label": "Customer Name", "sale_person": "Sale Person", **out.get("column_labels", {})}
    out["notes"] = {"label": "sold_by"}
    out["export_columns"] = ["label", "sale_person", *out["columns"][1:]]
    return out


def _sold_on_credit(invoice):
    """As its order was taken. An older order that never said goes by the customer's terms, the rule new orders default to."""
    mode = invoice.order.payment_mode if invoice.order_id else ""
    return mode == "credit" if mode else bool(invoice.customer.credit_days)


def sales_by_rep(date_from=None, date_to=None, **_):
    lines = _inv_lines(date_from, date_to).select_related("invoice__order", "invoice__customer", *SALE_PERSON_RELATED)
    names = _rep_names()
    out = _group(lines, lambda l: _sale_person(l.invoice)[0], lambda l: names.get(str(_sale_person(l.invoice)[0]), _sale_person(l.invoice)[1]))
    out["drill"] = {"column": "label", "report": "rep_daily_sheet", "param": "rep"}   # a rep's name opens their daily sheet
    out["column_labels"] = {"label": "Sale Person"}   # owner, 8 Nov 2026: "Sales by Sale Person"

    # how much of it was sold on cash and how much on credit (owner, 5 Nov 2026): the two add up to Sales
    credit, returns, received, outstanding, counted = defaultdict(lambda: Z), defaultdict(lambda: Z), defaultdict(lambda: Z), defaultdict(lambda: Z), set()
    # Sales is before tax; what was billed, returned, received and is still owed include it — Billed (Incl. Tax) makes the
    # last four add up on a tax invoice (Billed = Returns + Received + Outstanding), owner 24 Nov 2026
    billed = defaultdict(lambda: Z)
    for l in lines:
        rep = str(_sale_person(l.invoice)[0])
        if _sold_on_credit(l.invoice):
            credit[rep] += _net_sales(l)
        if l.invoice_id not in counted:
            # the money that has come in on these invoices and what is still owed on them (owner, 5 Nov 2026). What a
            # credit note settled is neither: those goods came back, no money did.
            counted.add(l.invoice_id)
            billed[rep] += l.invoice.total
            returns[rep] += l.invoice.credited_amount   # credit on these invoices, tax included — the money side
            received[rep] += l.invoice.cash_paid
            outstanding[rep] += l.invoice.balance
    for r in out["rows"]:
        r["credit_sales"] = str(credit[r["key"]])
        r["cash_sales"] = str(Decimal(r["sales"]) - credit[r["key"]])
        r["billed"] = str(billed[r["key"]])
        r["credited"] = str(returns[r["key"]])
        r["received"] = str(received[r["key"]])
        r["outstanding"] = str(outstanding[r["key"]])
    # returns in the period (credit-note date) come off the sales side, as in Sales by customer (owner, 24 Nov 2026)
    _net_of_returns(out, date_from, date_to, by_type={},
                    key_of=lambda l: (str(_sale_person(l.credit_note.invoice)[0]), names.get(str(_sale_person(l.credit_note.invoice)[0]), _sale_person(l.credit_note.invoice)[1])) if l.credit_note.invoice_id else ("None", "No Sale Person"),
                    blank={k: "0" for k in ("cash_sales", "credit_sales", "billed", "credited", "received", "outstanding")})
    # Billed and Credited (both incl. tax) are worked out but not shown (owner, 25 Nov 2026: hide them)
    out["columns"] = ["label", "qty", "sales", "returns", "net_sales", "cash_sales", "credit_sales", "received", "outstanding", "company_share", "cogs", "gross_margin", "margin_pct", "invoices"]
    out["column_labels"].update(MARGIN_LABELS)
    out["totals"] = _sales_totals(out, ("sales", "returns", "net_sales", "cash_sales", "credit_sales", "received", "outstanding", "company_share", "cogs", "gross_margin"))
    return out


def product_by_rep(date_from=None, date_to=None, rep=None, **_):
    """
    What each sale person sold, product by product (owner, 27 Nov 2026: "also need product by sale person"): one row per
    sale person and product — Net Qty, Sales, Returns, Net Sales, the company's share, COGS and the margin, as Sales by
    product reads, with the sale person beside the name. The Sale Person filter narrows it to one.
    """
    names = _rep_names()
    person = lambda inv: _sale_person(inv) if inv is not None else (None, "No Sale Person")  # noqa: E731
    called = lambda inv: names.get(str(person(inv)[0]), person(inv)[1])  # noqa: E731

    def codes(v):
        p = v.product
        return " · ".join(x for x in (f"Item Code: {p.item_code}" if p.item_code else "", f"UPC: {p.upc}" if p.upc else "") if x)

    lines = [l for l in _inv_lines(date_from, date_to).select_related("invoice__order", *SALE_PERSON_RELATED) if not rep or str(person(l.invoice)[0]) == str(rep)]
    out = _group(lines, lambda l: f"{person(l.invoice)[0]}|{l.variant_id}", lambda l: l.variant.name,
                 lambda l: {"sale_person": called(l.invoice), "codes": codes(l.variant), "item_code": l.variant.product.item_code})
    _net_of_returns(out, date_from, date_to, by_type={},
                    key_of=lambda l: (f"{person(l.credit_note.invoice)[0]}|{l.variant_id}", l.variant.name,
                                      {"sale_person": called(l.credit_note.invoice), "codes": codes(l.variant), "item_code": l.variant.product.item_code}),
                    keep=(lambda l: str(person(l.credit_note.invoice)[0]) == str(rep)) if rep else None)
    # the biggest seller first, each one's best product first (owner, 27 Nov 2026): by name, Aliza Saleem — whose one sale this
    # month all came back — led the report, and its first rows read Returns = Sales
    by_person = defaultdict(lambda: Z)
    for r in out["rows"]:
        by_person[r["sale_person"]] += Decimal(r["net_sales"])
    out["rows"].sort(key=lambda r: (-by_person[r["sale_person"]], r["sale_person"].lower(), -Decimal(r["net_sales"]), r["label"].lower()))
    # no Company Share column here (owner, 27 Nov 2026); the margin still counts it, as every sales report's does
    cols = [c for c in SALES_COLUMNS if c != "company_share"]
    out["columns"] = ["sale_person", *cols]
    out["totals"] = {k: v for k, v in _sales_totals(out, SALES_TOTALS).items() if k != "company_share"}
    out["column_labels"] = {**MARGIN_LABELS, "sale_person": "Sale Person", "label": "Product Name"}
    out["column_styles"] = {"label": "bold"}
    out["notes"] = {"label": "codes"}
    out["export_columns"] = ["sale_person", "label", "item_code", *cols[1:]]
    out["text_columns"] = ["item_code"]
    return out


def items_not_sold(days=30, supplier=None, show=None, **_):
    """
    Every active item with no sale in the last N days (owner, 27 Nov 2026: "an item not sold in last 30 days"): when it last
    sold (or Never), how long ago, and the stock and money sitting in it — the most money first. A sale is a posted invoice
    line; Show In stock only leaves out what is not on the shelf anyway.
    """
    from django.db.models import Max

    from apps.catalog.models import ProductVariant
    from apps.inventory.models import StockLevel
    from apps.sales.models import SalesInvoiceLine

    try:
        days = max(1, int(days or 30))
    except (TypeError, ValueError):
        days = 30
    today = timezone.localdate()
    since = today - timezone.timedelta(days=days - 1)
    last = dict(SalesInvoiceLine.objects.filter(invoice__status__in=["posted", "partially_paid", "paid", "overdue", "returned"], qty__gt=0)
                .values("variant_id").annotate(d=Max("invoice__date")).values_list("variant_id", "d"))
    stock = dict(StockLevel.objects.values("variant_id").annotate(q=Sum("qty_on_hand")).values_list("variant_id", "q"))
    qs = ProductVariant.objects.filter(is_active=True, product__status="active").select_related("product__brand", "product__supplier", "product__brand__supplier")
    if supplier:
        qs = qs.filter(Q(product__supplier_id=supplier) | Q(product__supplier__isnull=True, product__brand__supplier_id=supplier))
    rows = []
    for v in qs:
        sold = last.get(v.pk)
        if sold is not None and sold >= since:
            continue
        on_hand = stock.get(v.pk) or Z
        if show == "In stock only" and on_hand <= 0:
            continue
        p = v.product
        maker = p.supplier or (p.brand.supplier if p.brand_id else None)
        cost = v.avg_cost or v.cost_price or Z
        rows.append({"label": v.name, "codes": " · ".join(x for x in (f"Item Code: {p.item_code}" if p.item_code else "", f"UPC: {p.upc}" if p.upc else "") if x),
                     "item_code": p.item_code, "brand": p.brand.name if p.brand_id else "", "company": maker.name if maker else "", "tp": str(v.sale_price or Z),
                     "last_sold": sold if sold else "Never", "days_since": (today - sold).days if sold else "", "on_hand": str(on_hand),
                     "stock_value": str((on_hand * cost).quantize(Decimal("0.01")) if on_hand > 0 else Z)})
    rows.sort(key=lambda r: (-Decimal(r["stock_value"]), r["label"].lower()))
    cols = ["label", "brand", "company", "tp", "last_sold", "days_since", "on_hand", "stock_value"]
    return {"columns": cols, "rows": rows,
            "totals": {"products": len(rows), "on_hand": str(sum((Decimal(r["on_hand"]) for r in rows if Decimal(r["on_hand"]) > 0), Z)), "stock_value": str(sum((Decimal(r["stock_value"]) for r in rows), Z))},
            "column_labels": {"label": "Product Name", "company": "Company", "tp": "T.P", "last_sold": "Last Sold", "days_since": "Days Since Sold", "on_hand": "Stock On Hand", "stock_value": "Stock Value"},
            "column_styles": {"label": "bold"}, "notes": {"label": "codes"}, "text_columns": ["item_code"],
            "export_columns": ["label", "item_code", *cols[1:]], "print_params": {"Not sold in the last": f"{days} days"}}


def settlement_discounts(date_from=None, date_to=None, customer=None, **_):
    """
    Every few rupees let go to close an invoice (owner, 27 Nov 2026: 604 billed, 600 paid): the receipt, the customer, the
    invoice, what was received and what was let go to Settlement Discount, and who took the money. A cancelled receipt's
    is gone with it.
    """
    from apps.accounting.models import PaymentAllocation
    from apps.sales.models import SalesInvoice

    qs = _window(PaymentAllocation.objects.filter(settlement__gt=0, document_type="sales_invoice").exclude(payment__status="cancelled")
                 .select_related("payment", "payment__collected_by"), "payment__date", date_from, date_to)
    if customer:
        qs = qs.filter(payment__partner_id=str(customer))
    invoices = {str(i.pk): i for i in SalesInvoice.objects.filter(pk__in=[a.document_id for a in qs]).select_related("customer", *[f[len("invoice__"):] for f in SALE_PERSON_RELATED])}
    names = _rep_names()
    rows = []
    for a in qs.order_by("payment__date", "payment__number"):
        inv = invoices.get(a.document_id)
        rep_id, person = _sale_person(inv) if inv is not None else (None, "")
        rows.append({"date": a.payment.date, "receipt": a.payment.number, "customer": a.payment.partner_name, "invoice": a.document_number,
                     "sale_person": names.get(str(rep_id), person) if rep_id else person, "invoice_total": str(inv.total if inv is not None else Z),
                     "received": str(a.amount), "settlement": str(a.settlement),
                     "by": (a.payment.collected_by.full_name or a.payment.collected_by.username) if a.payment.collected_by_id else ""})
    return {"columns": ["date", "receipt", "customer", "invoice", "sale_person", "invoice_total", "received", "settlement", "by"], "rows": rows,
            "totals": {"received": str(sum((Decimal(r["received"]) for r in rows), Z)), "settlement": str(sum((Decimal(r["settlement"]) for r in rows), Z))},
            "column_labels": {"receipt": "Receipt No", "invoice": "Invoice No", "sale_person": "Sale Person", "invoice_total": "Invoice Total",
                              "settlement": "Settlement Discount", "by": "Received By"}}


def _pct_off(pct, discount, subtotal):
    """A document's discount as a %: its own Discount %, else the discount over the subtotal (a product's own discount)."""
    if pct:
        return str(Decimal(pct).quantize(Decimal("0.01")))
    # nothing where there is no discount: a column of 0.00 read as a column that does not work (owner, 26 Nov 2026)
    return str((Decimal(discount) / Decimal(subtotal) * 100).quantize(Decimal("0.01"))) if discount and subtotal else ""


PAYMENT_STATUS = {"posted": "Unpaid", "partially_paid": "Partially Paid", "paid": "Paid", "overdue": "Overdue", "returned": "Returned"}


def daily_sales(date_from=None, date_to=None, rep=None, payment_status=None, show=None, **_):
    """
    Daily sale report (owner, 24 Nov 2026): the day's invoices AND its returns (credit notes), one row each — Date, Type,
    Invoice / Return No, Customer, Sale Person, Payment Status, Total (before discount), Discount, Discount %, Tax, Grand
    Total and the document's PDF (26 Nov 2026; a View link until then). Total − Discount + Tax = Grand Total on every row: a tax invoice's Grand Total carries its GST and
    advance income tax, which is why it could exceed Total with no Tax column to show it. A return reads in minus, so the
    totals are the net. Cards on top: Sales, Returns, Net Sales, and what is Received / still Outstanding on the day's invoices.
    Opens on today; filters: sale person, payment status (sales only), Show sales and returns / sales only / returns only.
    """
    from apps.sales.models import CreditNote, SalesInvoice

    names = _rep_names()
    sales_too = show in (None, "", "Sales and returns", "Sales only")
    returns_too = show in (None, "", "Sales and returns", "Returns only") and not payment_status
    rows = []
    sold = Z
    received = pending = Z
    n_sales = 0
    if sales_too:
        qs = SalesInvoice.objects.filter(status__in=list(PAYMENT_STATUS)).select_related("customer", "rep", "order__rep", "order__trip__rep", "delivery_note__trip__rep")
        qs = _window(qs, "date", date_from, date_to)
        wanted = {v: k for k, v in PAYMENT_STATUS.items()}.get(payment_status)
        if wanted:
            qs = qs.filter(status=wanted)
        for inv in qs:
            rep_id, person = _sale_person(inv)
            if rep and str(rep_id) != str(rep):
                continue
            rows.append({"date": inv.date, "type": "Sale", "invoice": inv.number, "customer": inv.customer.name, "sale_person": names.get(str(rep_id), person),
                         "payment_status": PAYMENT_STATUS[inv.status], "total": str(inv.subtotal), "discount": str(inv.discount),
                         "discount_pct": _pct_off(inv.discount_pct, inv.discount, inv.subtotal),
                         "tax": str(inv.total - (inv.subtotal - inv.discount)), "grand_total": str(inv.total), "pdf": "PDF",
                         "pdf_url": f"/api/v1/invoices/{inv.pk}/pdf", "_order": 0})
            sold += inv.total
            received += inv.cash_paid
            pending += inv.balance
            n_sales += 1
    back = Z
    n_back = 0
    if returns_too:
        qs = CreditNote.objects.filter(status="posted").select_related("customer", "invoice", "invoice__rep", "invoice__order__rep", "invoice__order__trip__rep", "invoice__delivery_note__trip__rep")
        qs = _window(qs, "date", date_from, date_to)
        for cn in qs:
            rep_id, person = _sale_person(cn.invoice) if cn.invoice_id else (None, "No Sale Person")
            if rep and str(rep_id) != str(rep):
                continue
            rows.append({"date": cn.date, "type": "Return", "invoice": cn.number, "customer": cn.customer.name, "sale_person": names.get(str(rep_id), person),
                         "payment_status": f"Return of {cn.invoice.number}" if cn.invoice_id else "Return", "total": str(-cn.subtotal), "discount": str(-cn.discount),
                         "discount_pct": _pct_off(None, cn.discount, cn.subtotal),
                         "tax": str(-(cn.total - (cn.subtotal - cn.discount))), "grand_total": str(-cn.total), "pdf": "PDF",
                         "pdf_url": f"/api/v1/credit-notes/{cn.pk}/pdf", "_order": 1})
            back += cn.total
            n_back += 1
    rows.sort(key=lambda r: (r["date"], r.pop("_order"), r["invoice"]))
    tot = {k: sum((Decimal(r[k]) for r in rows), Z) for k in ("total", "discount", "tax", "grand_total")}
    plural = lambda n, w: f"{n} {w}{'' if n == 1 else 's'}"
    summary = [
        {"label": "Sales", "value": str(sold), "sub": plural(n_sales, "invoice")},
        {"label": "Returns", "value": str(back), "sub": plural(n_back, "credit note"), "tone": "warning"},
        {"label": "Net sales", "value": str(sold - back), "sub": "sales less returns", "tone": "success"},
        {"label": "Received", "value": str(received), "sub": "on these invoices"},
        {"label": "Outstanding", "value": str(pending), "sub": "still owed on them", "tone": "danger"},
    ]
    # Discount % beside the Discount (owner, 26 Nov 2026); the last column downloads the invoice's or the return's PDF,
    # where a View link was (owner, 26 Nov 2026: "no need for view column … instead give pdf download")
    cols = ["date", "type", "invoice", "customer", "sale_person", "payment_status", "total", "discount", "discount_pct", "tax", "grand_total", "pdf"]
    return {"columns": cols, "rows": rows, "summary": summary, "totals": {k: str(v) for k, v in tot.items()},
            "column_labels": {"invoice": "Invoice / Return No", "sale_person": "Sale Person", "payment_status": "Payment Status", "discount_pct": "Discount %",
                              "grand_total": "Grand Total", "pdf": "PDF"},
            "downloads": {"pdf": {"url": "pdf_url"}},   # the number is plain text, not a link (owner, 26 Nov 2026)
            "export_columns": cols[:-1]}


DUE_WINDOWS = {"Next 7 days": 7, "Next 14 days": 14, "Next 30 days": 30}


def pending_payments(due_within="Next 7 days", pay_type=None, rep=None, customer=None, **_):
    """
    What customers still owe (owner, 24 Nov 2026: "what much pending and how much cash or credit pending ... which payments
    are due in next 7 days with option to change to 14 or 30"). Cards on top: Total, Cash and Credit pending, Overdue, and
    Due in the chosen window — over everything pending for the chosen sale person / customer / cash-or-credit. The table
    lists the invoices in the window (Next 7, 14 or 30 days from today), the Overdue ones, or All pending. Cash or credit
    is the order's own choice; a cash sale is due on its invoice day.
    """
    from apps.sales.models import SalesInvoice

    today = timezone.localdate()
    qs = SalesInvoice.objects.filter(status__in=["posted", "partially_paid", "overdue"]).select_related(
        "customer", "rep", "order", "order__rep", "order__trip__rep", "delivery_note__trip__rep")
    if customer:
        qs = qs.filter(customer_id=customer)
    names = _rep_names()
    pending = []
    for inv in qs:
        due = inv.balance
        if due <= Decimal("0.005"):
            continue
        rep_id, person = _sale_person(inv)
        if rep and str(rep_id) != str(rep):
            continue
        kind = "Credit" if _sold_on_credit(inv) else "Cash"
        if pay_type in ("Cash", "Credit") and kind != pay_type:
            continue
        on = inv.due_date or inv.date
        days = (on - today).days
        when = "Due today" if days == 0 else (f"Due in {days} day{'' if days == 1 else 's'}" if days > 0 else f"{-days} day{'' if days == -1 else 's'} overdue")
        pending.append({"date": inv.date, "invoice": inv.number, "customer": inv.customer.name, "sale_person": names.get(str(rep_id), person),
                        "pay_type": kind, "due_date": on, "when": when, "cash_due": str(due) if kind == "Cash" else "", "credit_due": str(due) if kind == "Credit" else "",
                        "due": str(due), "view": "View", "invoice_id": str(inv.pk), "_days": days, "_due": due, "_kind": kind})
    window = DUE_WINDOWS.get(due_within)
    if due_within == "Overdue":
        rows = [r for r in pending if r["_days"] < 0]
    elif due_within == "All pending":
        rows = pending
    else:
        window = window or 7
        rows = [r for r in pending if 0 <= r["_days"] <= window]
    rows.sort(key=lambda r: (r["due_date"], r["invoice"]))

    def total(rs, kind=None):
        return sum((r["_due"] for r in rs if kind is None or r["_kind"] == kind), Z)

    soon = window or 7
    summary = [
        {"label": "Total pending", "value": str(total(pending)), "count": len(pending)},
        {"label": "Cash pending", "value": str(total(pending, "Cash")), "count": sum(1 for r in pending if r["_kind"] == "Cash")},
        {"label": "Credit pending", "value": str(total(pending, "Credit")), "count": sum(1 for r in pending if r["_kind"] == "Credit")},
        {"label": "Overdue", "value": str(total([r for r in pending if r["_days"] < 0])), "count": sum(1 for r in pending if r["_days"] < 0), "tone": "danger"},
        {"label": f"Due in next {soon} days", "value": str(total([r for r in pending if 0 <= r["_days"] <= soon])), "count": sum(1 for r in pending if 0 <= r["_days"] <= soon), "tone": "warning"},
    ]
    for r in rows:
        for k in ("_days", "_due", "_kind"):
            r.pop(k, None)
    cols = ["date", "invoice", "customer", "sale_person", "pay_type", "due_date", "when", "cash_due", "credit_due", "due", "view"]
    return {"columns": cols, "rows": rows, "summary": summary,
            "totals": {"cash_due": str(sum((Decimal(r["cash_due"] or 0) for r in rows), Z)), "credit_due": str(sum((Decimal(r["credit_due"] or 0) for r in rows), Z)),
                       "due": str(sum((Decimal(r["due"]) for r in rows), Z))},
            "column_labels": {"invoice": "Invoice No", "sale_person": "Sale Person", "pay_type": "Cash / Credit", "due_date": "Due Date", "when": "Status",
                              "cash_due": "Cash Pending", "credit_due": "Credit Pending", "due": "Total Pending", "view": "View Invoice"},
            "links": {"invoice": {"to": "/app/sales/invoices/", "id": "invoice_id", "query": "view=1"}, "view": {"to": "/app/sales/invoices/", "id": "invoice_id", "query": "view=1"}},
            "column_styles": {"when": "danger"} if due_within == "Overdue" else {},
            "export_columns": cols[:-1]}


def sales_by_territory(date_from=None, date_to=None, **_):
    area = lambda c: (str(c.territory_id), c.territory.name if c.territory_id else "(no territory)")
    out = _group(_inv_lines(date_from, date_to).select_related("invoice__customer__territory", "invoice__customer__route"), lambda l: area(l.invoice.customer)[0], lambda l: area(l.invoice.customer)[1])
    return _with_returns(out, date_from, date_to, lambda l: area(l.credit_note.customer))


def sales_by_route(date_from=None, date_to=None, **_):
    road = lambda c: (str(c.route_id), c.route.name if c.route_id else "(no route)")
    out = _group(_inv_lines(date_from, date_to).select_related("invoice__customer__route"), lambda l: road(l.invoice.customer)[0], lambda l: road(l.invoice.customer)[1])
    return _with_returns(out, date_from, date_to, lambda l: road(l.credit_note.customer))


def sales_by_period(date_from=None, date_to=None, granularity="day", **_):
    from apps.sales.models import SalesInvoice

    qs = _window(SalesInvoice.objects.filter(status__in=["posted", "partially_paid", "paid", "overdue", "returned"]), "date", date_from, date_to)
    trunc = TruncMonth("date") if granularity == "month" else TruncDay("date")
    rows = [{"period": r["p"].isoformat() if hasattr(r["p"], "isoformat") else str(r["p"]), "invoices": r["n"], "sales": str(r["s"] or 0), "tax": str(sum((r[k] or 0) for k in ("t1", "t2", "fed", "extra", "further")))} for r in qs.annotate(p=trunc).values("p").annotate(n=Count("id"), s=Sum("subtotal") - Sum("discount"), t1=Sum("tax1_total"), t2=Sum("tax2_total"), fed=Sum("fed_total"), extra=Sum("extra_tax_total"), further=Sum("further_tax_total")).order_by("p")]
    return {"columns": ["period", "invoices", "sales", "tax"], "rows": rows, "totals": {"sales": str(sum((Decimal(r["sales"]) for r in rows), Z))}}


def order_fulfilment(date_from=None, date_to=None, **_):
    from apps.sales.models import SalesOrder

    qs = _window(SalesOrder.objects.all(), "order_date", date_from, date_to)
    counts = dict(qs.values_list("status").annotate(c=Count("id")).values_list("status", "c"))
    total = sum(counts.values())
    fulfilled = counts.get("delivered", 0) + counts.get("invoiced", 0)
    rows = [{"status": k, "count": v, "pct": str((Decimal(v) / total * 100).quantize(Decimal("0.1")) if total else Z)} for k, v in sorted(counts.items())]
    return {"columns": ["status", "count", "pct"], "rows": rows, "totals": {"orders": total, "fulfilled": fulfilled, "fulfilment_rate": str((Decimal(fulfilled) / total * 100).quantize(Decimal("0.1")) if total else Z), "rejected": counts.get("rejected", 0), "cancelled": counts.get("cancelled", 0)}}


def pending_orders(**_):
    from apps.sales.models import SalesOrder

    from apps.delivery.models import units_came_back

    came_back = units_came_back()
    rows = []
    for o in SalesOrder.objects.filter(status__in=["submitted", "under_review", "on_hold", "confirmed", "on_hold_credit", "on_hold_stock", "dispatched", "partially_delivered"]).select_related("customer", "rep"):
        pend = sum((l.qty_ordered + l.free_qty - l.qty_delivered - l.qty_closed - came_back.get(l.pk, Z) for l in o.lines.all()), Z)
        rows.append({"number": o.number, "date": o.order_date, "customer": o.customer.name, "rep": o.rep.name if o.rep_id else "", "status": o.status, "total": str(o.total), "qty_pending": str(pend), "age_days": (timezone.localdate() - o.order_date).days, "hold_reason": o.hold_reason})
    return {"columns": ["number", "date", "customer", "rep", "status", "total", "qty_pending", "age_days"], "rows": rows, "totals": {"count": len(rows)}}


def dispatched_not_delivered(**_):
    from apps.sales.models import SalesOrder

    rows = [{"number": o.number, "customer": o.customer.name, "trip": o.trip.number if o.trip_id else "", "driver": o.trip.driver.name if o.trip_id and o.trip.driver_id else "", "dispatched_at": o.dispatched_at, "total": str(o.total)} for o in SalesOrder.objects.filter(status__in=["dispatched", "partially_delivered"]).select_related("customer", "trip", "trip__driver")]
    return {"columns": ["number", "customer", "trip", "driver", "dispatched_at", "total"], "rows": rows, "totals": {"count": len(rows), "value": str(sum((Decimal(r["total"]) for r in rows), Z))}}


def rejected_orders(date_from=None, date_to=None, **_):
    from apps.sales.models import SalesOrder

    qs = _window(SalesOrder.objects.filter(status__in=["rejected", "cancelled"]), "order_date", date_from, date_to).select_related("customer", "rep")
    rows = [{"number": o.number, "date": o.order_date, "customer": o.customer.name, "rep": o.rep.name if o.rep_id else "", "status": o.status, "reason": o.rejection_reason, "total": str(o.total)} for o in qs]
    return {"columns": ["number", "date", "customer", "rep", "status", "reason", "total"], "rows": rows, "totals": {"count": len(rows)}}


def scheme_cost(date_from=None, date_to=None, **_):
    from apps.sales.models import SalesOrderLine

    qs = _window(SalesOrderLine.objects.exclude(order__status__in=["cancelled", "rejected"]), "order__order_date", date_from, date_to).exclude(scheme_names=[])
    out = defaultdict(lambda: {"discount": Z, "free_qty": Z, "lines": 0})
    for l in qs:
        for n in l.scheme_names or ["(scheme)"]:
            out[n]["discount"] += l.scheme_discount
            out[n]["free_qty"] += l.free_qty
            out[n]["lines"] += 1
    rows = [{"scheme": k, **{kk: str(vv) for kk, vv in v.items()}} for k, v in out.items()]
    return {"columns": ["scheme", "discount", "free_qty", "lines"], "rows": rows, "totals": {"discount": str(sum((v["discount"] for v in out.values()), Z))}}


# --------------------------------------------------------------------- purchasing
def purchase_register(date_from=None, date_to=None, supplier=None, **_):
    from apps.purchasing.models import PurchaseBill

    qs = _window(PurchaseBill.objects.exclude(status="cancelled"), "bill_date", date_from, date_to).select_related("supplier")
    if supplier:
        qs = qs.filter(supplier_id=supplier)
    rows = [{"number": b.number, "date": b.bill_date, "supplier": b.supplier.name, "supplier_invoice": b.supplier_invoice_no, "subtotal": str(b.subtotal), "tax1": str(b.tax1_total), "tax2": str(b.tax2_total), "total": str(b.total), "paid": str(b.paid_amount), "status": b.status} for b in qs]
    return {"columns": ["number", "date", "supplier", "supplier_invoice", "subtotal", "tax1", "tax2", "total", "paid", "status"], "rows": rows, "totals": {"total": str(sum((Decimal(r["total"]) for r in rows), Z))}}


def grn_register(date_from=None, date_to=None, **_):
    from apps.purchasing.models import GoodsReceipt

    qs = _window(GoodsReceipt.objects.exclude(status="cancelled"), "date", date_from, date_to).select_related("supplier", "warehouse", "po")
    rows = [{"number": g.number, "date": g.date, "supplier": g.supplier.name, "warehouse": g.warehouse.name, "po": g.po.number if g.po_id else "", "value": str(g.total_value), "status": g.status, "billed": g.billed} for g in qs]
    return {"columns": ["number", "date", "supplier", "warehouse", "po", "value", "status", "billed"], "rows": rows, "totals": {"value": str(sum((Decimal(r["value"]) for r in rows), Z))}}


def receipts_by_item(date_from=None, date_to=None, supplier=None, **_):
    """
    What we bought and what actually arrived, item by item: ordered, received, rejected, still due — in boxes and
    in units — and where the goods were put away. This is the report that answers "how many boxes came in".
    """
    from apps.purchasing.models import GoodsReceiptLine, PurchaseOrderLine

    lines = _window(PurchaseOrderLine.objects.exclude(po__status__in=["draft", "cancelled"]), "po__date", date_from, date_to).select_related(
        "po", "po__supplier", "po__warehouse", "variant", "variant__product"
    )
    if supplier:
        lines = lines.filter(po__supplier_id=supplier)
    received = {}
    for g in GoodsReceiptLine.objects.filter(po_line__in=lines, grn__status="posted").select_related("grn", "bin", "bin__warehouse"):
        agg = received.setdefault(g.po_line_id, {"damaged": Z, "short": Z, "where": set(), "reasons": set()})
        agg["damaged"] += g.qty_damaged
        agg["short"] += g.qty_short
        if g.bin_id:
            agg["where"].add(f"{g.bin.warehouse.code} / {g.bin.code}")
        if g.reject_reason:
            agg["reasons"].add(g.reject_reason)
    rows = []
    for l in lines:
        product = l.variant.product
        per = product.packing or 1
        agg = received.get(l.pk, {"damaged": Z, "short": Z, "where": set(), "reasons": set()})
        ordered_box, ordered_loose = boxes_loose(l.qty_ordered, per)
        got_box, got_loose = boxes_loose(l.qty_received, per)
        rows.append({
            "po": l.po.number, "date": l.po.date, "supplier": l.po.supplier.name, "warehouse": l.po.warehouse.name,
            "item_code": product.item_code, "upc": product.upc, "sku": l.variant.sku, "name": l.variant.name,
            "per_box": str(per),
            "ordered_boxes": ordered_box, "ordered_loose": ordered_loose, "ordered_units": str(l.qty_ordered),
            "received_boxes": got_box, "received_loose": got_loose, "received_units": str(l.qty_received),
            "rejected": str(agg["damaged"]), "not_received": str(agg["short"]),
            "pending_units": str(l.qty_pending), "closed": l.closed,
            "placed_in": ", ".join(sorted(agg["where"])) or l.po.warehouse.code,
            "reason": "; ".join(sorted(agg["reasons"])),
        })
    return {
        "columns": ["po", "date", "supplier", "warehouse", "item_code", "upc", "sku", "name", "per_box",
                    "ordered_boxes", "ordered_loose", "ordered_units", "received_boxes", "received_loose", "received_units",
                    "rejected", "not_received", "pending_units", "closed", "placed_in", "reason"],
        "rows": rows,
        "totals": {
            "ordered_units": str(sum((Decimal(r["ordered_units"]) for r in rows), Z)),
            "received_units": str(sum((Decimal(r["received_units"]) for r in rows), Z)),
            "rejected": str(sum((Decimal(r["rejected"]) for r in rows), Z)),
            "not_received": str(sum((Decimal(r["not_received"]) for r in rows), Z)),
        },
    }


def purchases_by_supplier(date_from=None, date_to=None, **_):
    from apps.purchasing.models import PurchaseBill

    qs = _window(PurchaseBill.objects.filter(status__in=["posted", "partially_paid", "paid"]), "bill_date", date_from, date_to)
    rows = [{"supplier": r["supplier__name"], "bills": r["n"], "total": str(r["t"] or 0), "paid": str(r["p"] or 0), "outstanding": str((r["t"] or 0) - (r["p"] or 0))} for r in qs.values("supplier__name").annotate(n=Count("id"), t=Sum("total"), p=Sum("paid_amount")).order_by("-t")]
    return {"columns": ["supplier", "bills", "total", "paid", "outstanding"], "rows": rows, "totals": {"total": str(sum((Decimal(r["total"]) for r in rows), Z))}}


def pending_pos(**_):
    from apps.purchasing.models import PurchaseOrder

    rows = []
    for po in PurchaseOrder.objects.filter(status__in=["sent", "partially_received"]).select_related("supplier"):
        pend = sum((l.qty_ordered - l.qty_received for l in po.lines.all()), Z)
        rows.append({"number": po.number, "date": po.date, "expected": po.expected_date, "supplier": po.supplier.name, "status": po.status, "total": str(po.total), "qty_pending": str(pend), "age_days": (timezone.localdate() - po.date).days})
    return {"columns": ["number", "date", "expected", "supplier", "status", "total", "qty_pending", "age_days"], "rows": rows, "totals": {"count": len(rows)}}


def price_variance(date_from=None, date_to=None, **_):
    from apps.accounting.models import GL_LIVE, JournalLine

    qs = _window(JournalLine.objects.filter(account__code="5-01-002-0001", entry__status__in=GL_LIVE), "entry__date", date_from, date_to).select_related("entry")
    rows = [{"date": l.entry.date, "journal": l.entry.number, "source": l.entry.source_number, "variance": str(l.debit - l.credit)} for l in qs]
    return {"columns": ["date", "journal", "source", "variance"], "rows": rows, "totals": {"variance": str(sum((Decimal(r["variance"]) for r in rows), Z))}}


def returns_to_supplier(date_from=None, date_to=None, **_):
    from apps.purchasing.models import PurchaseReturn

    qs = _window(PurchaseReturn.objects.filter(status="posted"), "date", date_from, date_to).select_related("supplier")
    rows = [{"number": r.number, "date": r.date, "supplier": r.supplier.name, "reason": r.reason, "total": str(r.total), "debit_note_only": r.is_debit_note_only} for r in qs]
    return {"columns": ["number", "date", "supplier", "reason", "total"], "rows": rows, "totals": {"total": str(sum((Decimal(r["total"]) for r in rows), Z))}}


# ---------------------------------------------------------------------- inventory
def stock_summary(warehouse=None, **_):
    from apps.inventory.models import StockLevel
    from apps.sales.models import SalesOrderLine

    qs = StockLevel.objects.select_related("variant", "variant__product", "warehouse", "batch").filter(Q(qty_on_hand__gt=0) | Q(qty_reserved__gt=0))
    if warehouse:
        qs = qs.filter(warehouse_id=warehouse)
    from apps.delivery.services import DeliveryService

    out_statuses = ["dispatched", "partially_delivered", "delivered", "invoiced", "returned"]
    back = DeliveryService.settled_returns(status__in=out_statuses)
    disp = {r["variant_id"]: ((r["d"] or 0) - (r["dl"] or 0) - back.get(r["variant_id"], 0), r["dl"] or 0) for r in SalesOrderLine.objects.filter(order__status__in=out_statuses).values("variant_id").annotate(d=Sum("qty_dispatched"), dl=Sum("qty_delivered"))}
    rows = []
    for lv in qs:
        d = disp.get(lv.variant_id, (0, 0))
        product = lv.variant.product
        box, loose = boxes_loose(lv.qty_on_hand, product.packing)
        rows.append({"warehouse": lv.warehouse.name, "item_code": product.item_code, "upc": product.upc, "sku": lv.variant.sku, "name": lv.variant.name, "batch": lv.batch.batch_no if lv.batch else "", "expiry": lv.batch.expiry_date if lv.batch else None, "per_box": str(product.packing or 1), "boxes": box, "loose": loose, "in_stock": str(lv.qty_on_hand), "reserved": str(lv.qty_reserved), "available": str(lv.qty_on_hand - lv.qty_reserved), "dispatched": str(d[0]), "delivered": str(d[1]), "avg_cost": str(lv.variant.avg_cost), "value": str((lv.qty_on_hand * (lv.variant.avg_cost or lv.variant.cost_price)).quantize(Decimal("0.0001")))})
    return {"columns": ["warehouse", "item_code", "upc", "sku", "name", "batch", "expiry", "per_box", "boxes", "loose", "in_stock", "reserved", "available", "dispatched", "delivered", "avg_cost", "value"], "rows": rows, "totals": {"value": str(sum((Decimal(r["value"]) for r in rows), Z))}}


def stock_ledger(variant=None, warehouse=None, date_from=None, date_to=None, **_):
    from apps.inventory.models import StockMovement

    qs = StockMovement.objects.select_related("variant", "warehouse", "batch", "performed_by").order_by("occurred_at", "created_at")
    if variant:
        qs = qs.filter(variant_id=variant)
    if warehouse:
        qs = qs.filter(warehouse_id=warehouse)
    opening = Z
    if date_from:
        opening = qs.filter(occurred_at__date__lt=date_from).aggregate(s=Sum("qty"))["s"] or Z
        qs = qs.filter(occurred_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(occurred_at__date__lte=date_to)
    rows, bal = [], opening
    for m in qs[:5000]:
        bal += m.qty
        rows.append({"date": m.occurred_at, "warehouse": m.warehouse.name, "sku": m.variant.sku, "name": m.variant.name, "batch": m.batch.batch_no if m.batch else "", "type": m.movement_type, "reference": m.reference_number, "qty_in": str(m.qty if m.qty > 0 else Z), "qty_out": str(-m.qty if m.qty < 0 else Z), "unit_cost": str(m.unit_cost), "balance": str(bal), "by": m.performed_by.full_name if m.performed_by else ""})
    return {"columns": ["date", "warehouse", "sku", "name", "batch", "type", "reference", "qty_in", "qty_out", "unit_cost", "balance", "by"], "rows": rows, "totals": {"opening": str(opening), "closing": str(bal)}}


def movement_summary(date_from=None, date_to=None, warehouse=None, **_):
    from apps.inventory.models import StockMovement

    qs = _window(StockMovement.objects.all(), "occurred_at__date", date_from, date_to)
    if warehouse:
        qs = qs.filter(warehouse_id=warehouse)
    rows = [{"type": r["movement_type"], "count": r["n"], "qty": str(r["q"] or 0), "value": str(r["v"] or 0)} for r in qs.values("movement_type").annotate(n=Count("id"), q=Sum("qty"), v=Sum(F("qty") * F("unit_cost"))).order_by("movement_type")]
    return {"columns": ["type", "count", "qty", "value"], "rows": rows, "totals": {}}


def stock_valuation(warehouse=None, **_):
    """
    The stock on hand at its D.P, its T.P and its R.P (owner, 27 Nov 2026: "inventory valuation. give me in DP, TP and RP
    value"): one row per item and warehouse, the batches added up, with each price and the value at it. What the books
    carry (the moving average cost) is Inventory valuation vs GL.
    """
    from apps.inventory.models import StockLevel

    qs = StockLevel.objects.filter(qty_on_hand__gt=0).select_related("variant__product__brand", "warehouse")
    if warehouse:
        qs = qs.filter(warehouse_id=warehouse)
    by = {}
    for lv in qs:
        k = (lv.warehouse_id, lv.variant_id)
        if k not in by:
            v, p = lv.variant, lv.variant.product
            by[k] = {"warehouse": lv.warehouse.name, "label": v.name, "item_code": p.item_code, "brand": p.brand.name if p.brand_id else "",
                     "codes": " · ".join(x for x in (f"Item Code: {p.item_code}" if p.item_code else "", f"UPC: {p.upc}" if p.upc else "") if x),
                     "qty": Z, "dp": v.cost_price or Z, "tp": v.sale_price or Z, "rp": v.retail_price or Z}
        by[k]["qty"] += lv.qty_on_hand
    rows, tot = [], {"qty": Z, "dp_value": Z, "tp_value": Z, "rp_value": Z}
    for r in by.values():
        q = r["qty"]
        vals = {"dp_value": (q * r["dp"]).quantize(Decimal("0.01")), "tp_value": (q * r["tp"]).quantize(Decimal("0.01")), "rp_value": (q * r["rp"]).quantize(Decimal("0.01"))}
        for k, x in vals.items():
            tot[k] += x
        tot["qty"] += q
        rows.append({**r, "qty": str(q), "dp": str(r["dp"]), "tp": str(r["tp"]), "rp": str(r["rp"]), **{k: str(x) for k, x in vals.items()}})
    rows.sort(key=lambda r: (r["warehouse"].lower(), -Decimal(r["dp_value"]), r["label"].lower()))
    cols = ["warehouse", "label", "brand", "qty", "dp", "dp_value", "tp", "tp_value", "rp", "rp_value"]
    return {"columns": cols, "rows": rows, "totals": {k: str(x) for k, x in tot.items()},
            "column_labels": {"label": "Product Name", "qty": "Qty On Hand", "dp": "D.P", "dp_value": "D.P Value", "tp": "T.P", "tp_value": "T.P Value", "rp": "R.P", "rp_value": "R.P Value"},
            "column_styles": {"label": "bold"}, "notes": {"label": "codes"}, "text_columns": ["item_code"],
            "export_columns": ["warehouse", "label", "item_code", *cols[2:]]}


def stock_ageing(warehouse=None, **_):
    from apps.inventory.models import StockLevel, StockMovement

    today = timezone.localdate()
    rows = []
    qs = StockLevel.objects.filter(qty_on_hand__gt=0).select_related("variant", "warehouse", "batch")
    if warehouse:
        qs = qs.filter(warehouse_id=warehouse)
    for lv in qs:
        last_in = StockMovement.objects.filter(variant=lv.variant, warehouse=lv.warehouse, qty__gt=0).order_by("-occurred_at").values_list("occurred_at", flat=True).first()
        days = (today - last_in.date()).days if last_in else None
        rows.append({"warehouse": lv.warehouse.name, "sku": lv.variant.sku, "name": lv.variant.name, "batch": lv.batch.batch_no if lv.batch else "", "qty": str(lv.qty_on_hand), "days_in_stock": days, "bucket": "0-30" if days is not None and days <= 30 else "31-60" if days is not None and days <= 60 else "61-90" if days is not None and days <= 90 else "90+", "value": str((lv.qty_on_hand * (lv.variant.avg_cost or lv.variant.cost_price)).quantize(Decimal("0.0001")))})
    return {"columns": ["warehouse", "sku", "name", "batch", "qty", "days_in_stock", "bucket", "value"], "rows": rows, "totals": {}}


def expiry_report(days=90, warehouse=None, **_):
    from apps.inventory.models import StockLevel

    today = timezone.localdate()
    qs = StockLevel.objects.filter(qty_on_hand__gt=0, batch__expiry_date__isnull=False, batch__expiry_date__lte=today + timezone.timedelta(days=int(days))).select_related("variant", "warehouse", "batch").order_by("batch__expiry_date")
    if warehouse:
        qs = qs.filter(warehouse_id=warehouse)
    rows = [{"warehouse": lv.warehouse.name, "sku": lv.variant.sku, "name": lv.variant.name, "batch": lv.batch.batch_no, "expiry": lv.batch.expiry_date, "days_left": (lv.batch.expiry_date - today).days, "qty": str(lv.qty_on_hand), "value": str((lv.qty_on_hand * (lv.variant.avg_cost or lv.variant.cost_price)).quantize(Decimal("0.0001")))} for lv in qs]
    return {"columns": ["warehouse", "sku", "name", "batch", "expiry", "days_left", "qty", "value"], "rows": rows, "totals": {"value": str(sum((Decimal(r["value"]) for r in rows), Z))}}


def low_stock(**_):
    from apps.inventory.services import AlertService

    rows = AlertService.reorder_suggestions()
    return {"columns": ["sku", "name", "supplier", "on_hand", "min_stock", "max_stock", "reorder_point", "suggested_qty"], "rows": rows, "totals": {"count": len(rows)}}


def dead_stock(days=90, **_):
    from apps.inventory.models import InventoryAlert

    rows = [{"sku": a.variant.sku, "name": a.variant.name, "days": a.data.get("days"), "on_hand": a.data.get("on_hand"), "message": a.message} for a in InventoryAlert.objects.filter(type="dead_stock", resolved_at__isnull=True).select_related("variant")]
    return {"columns": ["sku", "name", "days", "on_hand"], "rows": rows, "totals": {"count": len(rows)}}


def wastage_by_reason(date_from=None, date_to=None, **_):
    from apps.inventory.models import StockAdjustment

    from apps.sales.models import CreditNoteLine

    qs = _window(StockAdjustment.objects.filter(status="approved", type="decrease"), "date", date_from, date_to)
    rows = [{"reason": r["reason__name"], "category": r["reason__category"], "adjustments": r["n"], "value": str(r["v"] or 0)} for r in qs.values("reason__name", "reason__category").annotate(n=Count("id"), v=Sum("total_value")).order_by("-v")]
    # goods that came back broken or expired are expensed on their credit note, with no adjustment to show for it
    lost = {}
    for l in _window(CreditNoteLine.objects.filter(credit_note__status="posted", stock_back=False).exclude(restock=True, condition="good"), "credit_note__date", date_from, date_to):
        kind = "expiry" if l.condition == "expired" else "damage"
        g = lost.setdefault(kind, {"notes": set(), "value": Z})
        g["notes"].add(l.credit_note_id)
        g["value"] += l.qty * l.unit_cost
    names = {"damage": "Damaged goods returned", "expiry": "Expired goods returned"}
    rows += [{"reason": names[k], "category": k, "adjustments": len(g["notes"]), "value": str(g["value"])} for k, g in lost.items()]
    rows.sort(key=lambda r: -Decimal(r["value"]))
    return {"columns": ["reason", "category", "adjustments", "value"], "column_labels": {"adjustments": "Documents"}, "rows": rows, "totals": {"value": str(sum((Decimal(r["value"]) for r in rows), Z))}}


def stock_position(today=None):
    """
    Where the stock is, at cost (owner, 11 Nov 2026: the dashboard's stock panel). The stock we hold is what sits in the
    warehouses, ready to sell or booked for confirmed orders, plus what is out for delivery: issued from the warehouse,
    not yet handed over. Delivered this month is apart from it: it is the customers' now. A van or quarantine warehouse
    is left out (quarantine holds a supplier's rejected goods; what a van carries is counted on its delivery notes).
    """
    from apps.catalog.models import ProductVariant
    from apps.delivery.models import DeliveryNoteLine
    from apps.inventory.models import StockLevel

    today = today or timezone.localdate()
    levels = list(StockLevel.objects.exclude(warehouse__type__in=["van", "quarantine"]).values("variant_id").annotate(h=Sum("qty_on_hand"), r=Sum("qty_reserved")))
    cost = {pk: (avg if avg and avg > 0 else cp) for pk, avg, cp in ProductVariant.objects.filter(pk__in=[r["variant_id"] for r in levels]).values_list("pk", "avg_cost", "cost_price")}
    free = {"value": Z, "units": Z}
    booked = {"value": Z, "units": Z}
    for r in levels:
        held = max(r["h"] or Z, Z)
        b = min(max(r["r"] or Z, Z), held)       # booked can never be more than is there
        c = cost.get(r["variant_id"]) or Z
        booked["units"] += b
        booked["value"] += b * c
        free["units"] += held - b
        free["value"] += (held - b) * c

    def lines(qs, qty):
        agg = {"value": Z, "units": Z}
        for q, uc in qs.values_list(qty, "unit_cost"):
            agg["units"] += q or Z
            agg["value"] += (q or Z) * (uc or Z)
        return agg

    out = lines(DeliveryNoteLine.objects.filter(delivery_note__stock_out_done=True, delivery_note__status__in=["pending", "loaded", "dispatched"]), "qty_loaded")
    delivered = lines(DeliveryNoteLine.objects.filter(delivery_note__status__in=["delivered", "partially_delivered"], delivery_note__delivered_at__date__gte=today.replace(day=1)), "qty_delivered")
    held = {"value": free["value"] + booked["value"] + out["value"], "units": free["units"] + booked["units"] + out["units"]}
    fmt = lambda a: {"value": str(a["value"].quantize(Decimal("0.01"))), "units": format(a["units"].normalize(), "f") if a["units"] else "0"}  # noqa: E731
    return {"held": fmt(held), "free": fmt(free), "booked": fmt(booked), "out": fmt(out), "delivered_month": fmt(delivered)}


CUSTOMER_TYPES = {"Salon": "salon", "Retailer": "retailer", "Wholesaler": "wholesaler", "LMT": "lmt", "Other": "other"}


def discount_sharing(date_from=None, date_to=None, customer_type=None, customer=None, supplier=None, show=None, **_):
    """
    Who bore every discount given (owner, 8 Nov 2026: salon discount sharing, "need report for this as well"): per
    invoice line, the list price (a salon's R.P), the T.P, the discount, and the distributor's and the company's
    shares, with the rule that split it. The company's share is what its claim carries, so a cancelled claim pays nothing,
    and the distributor's is Discounts Allowed. Returns of the period are rows of their own in minus (25 Nov 2026), so the
    totals are the books': the report counted the discount on goods that had come back.

    Every figure of a row is before tax, as the discount is: on a tax invoice (its T.P includes tax) the list price was the
    T.P with tax and the discount without, so 6 × 1,255 less 3% read 180.72 where 225.90 was expected (25 Nov 2026).
    """
    from apps.sales.models import CreditNoteLine, SalesInvoiceLine

    by = {}
    if customer_type in CUSTOMER_TYPES:
        by["customer__type"] = CUSTOMER_TYPES[customer_type]
    if customer:
        by["customer_id"] = customer
    maker_of = lambda p: p.supplier or (p.brand.supplier if p.brand_id else None)  # noqa: E731
    wanted = lambda p: not supplier or (maker_of(p) is not None and str(maker_of(p).pk) == str(supplier))  # noqa: E731

    lines = [l for l in _inv_lines(date_from, date_to, discount_amount__gt=0, **{f"invoice__{k}": v for k, v in by.items()})
             .select_related("variant__product__brand__supplier").order_by("invoice__date", "invoice__number", "sequence") if wanted(l.variant.product)]
    returns = list(_window(CreditNoteLine.objects.filter(credit_note__status="posted", discount_amount__gt=0, **{f"credit_note__{k}": v for k, v in by.items()}),
                           "credit_note__date", date_from, date_to).select_related("credit_note__customer", "credit_note__invoice", "variant__product__brand__supplier", "variant__product__supplier")
                   .order_by("credit_note__date", "credit_note__number", "id"))
    funded = _company_per_unit(l.invoice_id for l in lines)
    unfunded = _returned_shares(returns)
    # what is charged before tax, of the T.P as sold: 1 except on a tax invoice
    sold = {(x.invoice_id, x.variant_id): x for x in SalesInvoiceLine.objects.filter(invoice_id__in={l.credit_note.invoice_id for l in returns})}
    names = _customer_names({str(l.invoice.customer_id) for l in lines} | {str(l.credit_note.customer_id) for l in returns})

    def pct(x):
        return f"{Decimal(x).normalize():f}%"

    def before_tax(line):
        return line.unit_price / line.tp if line is not None and line.tp else Decimal("1")

    def rule_of(l, company, gross):
        table = f"Discount Table (Company {pct((company / gross * 100).quantize(Decimal('0.01')))} Of Sale)" if gross else ""
        if l.company_discount and not company:
            return "Claim Cancelled"
        if not l.discount_rule:
            return table if company else "No Company Share"
        return {"table": table if company else "No Company Share", "above_tp": "Salon: At Or Above T.P", "below_tp": f"Salon: Below T.P (Company {pct(l.share_pct)} Of R.P)",
                "bracket": f"Salon Bracket (Company Bears {pct(l.share_pct)})", "no_bracket": "Salon: No Bracket Set"}[l.discount_rule]

    rows, tot = [], defaultdict(Decimal)

    def add(l, document, on, who, sign, company, factor, rule):
        p = l.variant.product
        gross = l.qty * l.unit_price
        off = (l.discount_amount / gross) if gross else Z
        maker = maker_of(p)
        rows.append({
            "date": on, "invoice": document, "tax_note": "Tax Invoice, Before Tax" if factor != 1 else "", "customer": names.get(str(who.pk), who.name), "customer_type": who.get_type_display(),
            "label": l.variant.name, "codes": " · ".join(x for x in (f"Item Code: {p.item_code}" if p.item_code else "", f"UPC: {p.upc}" if p.upc else "") if x),
            "qty": str(sign * l.qty), "list_price": str(l.unit_price), "trade_price": str((l.variant.sale_price * factor).quantize(Decimal("0.01"))),
            "discount_pct": str((off * 100).quantize(Decimal("0.01"))), "price_after": str((l.unit_price * (1 - off)).quantize(Decimal("0.01"))),
            "discount": str(sign * l.discount_amount), "distributor_share": str(sign * (l.discount_amount - company) or Z), "company_share": str(sign * company or Z),
            "rule": rule, "company": maker.name if maker else "",
        })
        tot["discount"] += sign * l.discount_amount
        tot["company_share"] += sign * company

    for l in lines:
        company = _q4(funded.get((l.invoice_id, l.variant_id), Z) * l.qty)
        add(l, l.invoice.number, l.invoice.date, l.invoice.customer, 1, company, before_tax(l), rule_of(l, company, l.qty * l.unit_price))
    for l in returns:
        if wanted(l.variant.product):
            cn = l.credit_note
            add(l, cn.number, cn.date, cn.customer, -1, unfunded[l.pk], before_tax(sold.get((cn.invoice_id, l.variant_id))), f"Return Of {cn.invoice.number}" if cn.invoice_id else "Return")
    rows.sort(key=lambda r: r["date"])      # a day's returns after its sales
    tot["distributor_share"] = tot["discount"] - tot["company_share"]
    if show == "Invoice wise":
        return _sharing_by_invoice(rows, tot)
    return {
        "columns": ["date", "invoice", "customer", "label", "qty", "list_price", "trade_price", "discount_pct", "price_after", "discount", "distributor_share", "company_share", "rule", "company"],
        "rows": rows, "totals": {k: str(tot[k]) for k in ("discount", "distributor_share", "company_share")},
        "column_labels": {"invoice": "Invoice / Return No", "label": "Product Name", "list_price": "List Price", "trade_price": "T.P", "discount_pct": "Discount %", "price_after": "Price After Discount",
                          "discount": "Total Discount", "distributor_share": "Distributor Share", "company_share": "Company Share"},
        "column_styles": {"label": "bold"}, "notes": {"label": "codes", "customer": "customer_type", "invoice": "tax_note"},
        "export_columns": ["date", "invoice", "tax_note", "customer", "customer_type", "label", "codes", "qty", "list_price", "trade_price", "discount_pct", "price_after", "discount", "distributor_share", "company_share", "rule", "company"],
        "text_columns": ["codes"],
    }


def _sharing_by_invoice(rows, tot):
    """
    Discount sharing, one row per invoice or return (owner, 26 Nov 2026: "also need this report invoice wise"): what it
    sold before the discount, the discount and its %, and who bore it — the item rows added up, so the totals are the same.
    """
    by = {}
    for r in rows:
        b = by.get(r["invoice"])
        if b is None:
            b = by[r["invoice"]] = {"date": r["date"], "invoice": r["invoice"], "tax_note": r["tax_note"], "customer": r["customer"], "customer_type": r["customer_type"],
                                    "items": 0, "gross": Z, "discount": Z, "distributor_share": Z, "company_share": Z, "rules": {}, "companies": {}}
        b["items"] += 1
        b["gross"] += Decimal(r["qty"]) * Decimal(r["list_price"])
        for k in ("discount", "distributor_share", "company_share"):
            b[k] += Decimal(r[k])
        b["rules"][r["rule"]] = None
        if r["company"]:
            b["companies"][r["company"]] = None
    out = []
    for b in by.values():
        pct = (b["discount"] / b["gross"] * 100).quantize(Decimal("0.01")) if b["gross"] else Z
        out.append({"date": b["date"], "invoice": b["invoice"], "tax_note": b["tax_note"], "customer": b["customer"], "customer_type": b["customer_type"], "items": b["items"],
                    "gross": str(b["gross"]), "discount_pct": str(pct), "discount": str(b["discount"]), "distributor_share": str(b["distributor_share"] or Z),
                    "company_share": str(b["company_share"] or Z), "rule": " / ".join(b["rules"]), "company": ", ".join(b["companies"])})
    cols = ["date", "invoice", "customer", "items", "gross", "discount_pct", "discount", "distributor_share", "company_share", "rule", "company"]
    return {
        "columns": cols, "rows": out, "totals": {k: str(tot[k]) for k in ("discount", "distributor_share", "company_share")},
        "column_labels": {"invoice": "Invoice / Return No", "gross": "Total Before Discount", "discount_pct": "Discount %", "discount": "Total Discount",
                          "distributor_share": "Distributor Share", "company_share": "Company Share"},
        "notes": {"customer": "customer_type", "invoice": "tax_note"},
        "export_columns": ["date", "invoice", "tax_note", "customer", "customer_type", *cols[3:]],
    }


def returns_report(date_from=None, date_to=None, result=None, **_):
    """
    Everything that came back and where it went (owner, 5 Nov 2026: "report if item going back to stock or wastage"):
    returned at the door, refused outright, and returned later on a credit note.
    """
    from apps.delivery.models import DeliveryNoteLine
    from apps.inventory.models import StockAdjustmentLine, StockMovement
    from apps.sales.models import CreditNoteLine, SalesOrder

    rows, tied, by_stop = [], set(), defaultdict(list)
    credits = list(CreditNoteLine.objects.filter(credit_note__status="posted").select_related(
        "credit_note", "credit_note__customer", "credit_note__invoice", "credit_note__invoice__rep", "credit_note__invoice__order", "credit_note__invoice__delivery_note", "variant", "batch"
    ).prefetch_related("credit_note__lines"))
    for c in credits:
        # the note a stop posted, or (before 5 Nov 2026) the one somebody took afterwards for goods the trip brought home
        stop = c.credit_note.delivery_note_id or (c.credit_note.invoice.delivery_note_id if c.stock_back and c.credit_note.invoice_id else None)
        if stop:
            by_stop[stop].append(c)

    def credit_value(c):
        """Its share of the credit note, the advance income tax and what the buyer withheld included, so a note adds up to its total."""
        cn = c.credit_note
        all_lines = sum((x.line_total for x in cn.lines.all()), Z)
        return c.line_total + ((cn.advance_tax_total - cn.wht_total) * c.line_total / all_lines if all_lines else Z)

    def add(on, order, note, invoice, credit_note, customer, rep, item, batch, qty, reason, res, cost, credit):
        rows.append({"date": on, "order": order, "delivery_note": note, "invoice": invoice, "credit_note": credit_note, "customer": customer, "rep": rep, "item": item,
                     "batch": batch, "qty": str(qty), "reason": reason, "result": res, "cost_value": str(q2(cost)), "credit_value": str(q2(credit))})

    stops = _window(DeliveryNoteLine.objects.filter(qty_returned__gt=0).exclude(delivery_note__status="cancelled"), "delivery_note__delivered_at__date", date_from, date_to).select_related(
        "delivery_note", "delivery_note__order", "delivery_note__order__rep", "delivery_note__customer", "delivery_note__invoice", "delivery_note__trip", "variant", "batch")
    for l in stops:
        dn, left, credit, numbers = l.delivery_note, l.qty_returned, Z, []
        for c in by_stop.get(dn.pk, []):
            if left > 0 and c.variant_id == l.variant_id and c.pk not in tied:
                tied.add(c.pk)
                left -= c.qty
                credit += credit_value(c)
                numbers.append(c.credit_note.number)
        invoice = getattr(dn, "invoice", None)
        add(timezone.localtime(dn.delivered_at).date() if dn.delivered_at else None, dn.order.number, dn.number, invoice.number if invoice else "", ", ".join(dict.fromkeys(numbers)), dn.customer.name,
            dn.order.rep.name if dn.order.rep_id else "", l.variant.name, l.batch.batch_no if l.batch_id else "", l.qty_returned, l.get_return_reason_display() or "Rejected by customer",
            l.return_result, l.qty_returned * l.unit_cost, credit)
    # returned later, on a credit note of their own
    for c in credits:
        cn = c.credit_note
        if c.pk in tied or (date_from and str(cn.date) < str(date_from)) or (date_to and str(cn.date) > str(date_to)):
            continue
        inv = cn.invoice
        note = inv.delivery_note if inv is not None else None
        res = "Back To Stock" if c.stock_back or (c.restock and c.condition == "good") else "Wastage"
        add(cn.date, inv.order.number if inv is not None and inv.order_id else "", note.number if note is not None else "", inv.number if inv is not None else "", cn.number, cn.customer.name,
            inv.rep.name if inv is not None and inv.rep_id else "", c.variant.name, c.batch.batch_no if c.batch_id else "", c.qty, c.reason or cn.reason or c.get_condition_display(), res, c.qty * c.unit_cost, credit_value(c))
    # a stop refused outright and closed as Customer rejected: the stop's lines are gone, its goods' movements stay
    for o in SalesOrder.objects.filter(status="rejected").select_related("customer", "rep").prefetch_related("delivery_notes", "invoices"):
        invoice = next(iter(o.invoices.all()), None)
        for dn in o.delivery_notes.all():
            base = (o.number, dn.number, invoice.number if invoice else "", "", o.customer.name, o.rep.name if o.rep_id else "")
            for m in _window(StockMovement.objects.filter(Q(reference_type="DeliveryNote", reference_id=str(dn.pk), movement_type="issue_cancelled") | Q(movement_type="delivery_return", note=f"Returned from {dn.number}")),
                             "occurred_at__date", date_from, date_to).select_related("variant", "batch"):
                add(timezone.localtime(m.occurred_at).date(), *base, m.variant.name, m.batch.batch_no if m.batch_id else "", m.qty, "Customer rejected", "Back To Stock", m.qty * m.unit_cost, Z)
            for w in _window(StockAdjustmentLine.objects.filter(adjustment__source_type="DeliveryNote", adjustment__source_id=str(dn.pk), adjustment__status="approved"), "adjustment__date", date_from, date_to).select_related("adjustment", "variant", "batch"):
                add(w.adjustment.date, *base, w.variant.name, w.batch.batch_no if w.batch_id else "", w.qty, "Customer rejected", "Wastage", w.value, Z)
    if result:
        rows = [r for r in rows if r["result"] == result]
    rows.sort(key=lambda r: (str(r["date"] or ""), r["order"]), reverse=True)

    def total(field, res=None):
        return str(sum((Decimal(r[field]) for r in rows if res is None or r["result"] == res), Z))

    waiting = {"not_back_yet_qty": total("qty", "Not Back Yet")} if any(r["result"] == "Not Back Yet" for r in rows) else {}
    # what the owner asked for first, so the screen shows it without scrolling
    return {"columns": ["date", "customer", "item", "batch", "qty", "result", "reason", "cost_value", "credit_value", "order", "delivery_note", "invoice", "credit_note", "rep"],
            "column_labels": {"result": "Back To Stock / Wastage"}, "rows": rows,
            "totals": {"back_to_stock_qty": total("qty", "Back To Stock"), "back_to_stock_cost": total("cost_value", "Back To Stock"),
                       "wastage_qty": total("qty", "Wastage"), "wastage_cost": total("cost_value", "Wastage"), **waiting, "credit_value": total("credit_value")}}


def transfer_register(date_from=None, date_to=None, **_):
    from apps.inventory.models import StockTransfer

    qs = _window(StockTransfer.objects.all(), "date", date_from, date_to).select_related("from_warehouse", "to_warehouse")
    rows = [{"number": t.number, "date": t.date, "from": t.from_warehouse.name, "to": t.to_warehouse.name, "status": t.status, "lines": t.lines.count(), "lost": str(sum((l.qty_lost for l in t.lines.all()), Z))} for t in qs]
    return {"columns": ["number", "date", "from", "to", "status", "lines", "lost"], "rows": rows, "totals": {}}


def count_variance_history(date_from=None, date_to=None, **_):
    from apps.inventory.models import StockCount

    qs = _window(StockCount.objects.filter(status="posted"), "posted_at__date", date_from, date_to).select_related("warehouse", "adjustment", "adjustment_increase")
    rows = [{"number": c.number, "warehouse": c.warehouse.name, "posted_at": c.posted_at, "lines": c.lines.count(), "variances": c.lines.exclude(counted_qty=F("system_qty")).exclude(counted_qty__isnull=True).count(), "decrease": str(c.adjustment.total_value if c.adjustment_id else Z), "increase": str(c.adjustment_increase.total_value if c.adjustment_increase_id else Z)} for c in qs]
    return {"columns": ["number", "warehouse", "posted_at", "lines", "variances", "decrease", "increase"], "rows": rows, "totals": {}}


# ---------------------------------------------------------------------- delivery
def trip_register(date_from=None, date_to=None, **_):
    from apps.delivery.models import DeliveryTrip

    qs = _window(DeliveryTrip.objects.all(), "date", date_from, date_to).select_related("driver", "vehicle", "rep", "warehouse")
    rows = [{"number": t.number, "date": t.date, "driver": t.driver.name if t.driver_id else "", "vehicle": t.vehicle.reg_no if t.vehicle_id else "", "rep": t.rep.name if t.rep_id else "", "status": t.status, "stops": t.stops_count, "delivered": t.delivery_notes.filter(status__in=["delivered", "partially_delivered"]).count(), "cash": str(t.cash_collected), "loaded_value": str(t.loaded_value)} for t in qs]
    return {"columns": ["number", "date", "driver", "vehicle", "rep", "status", "stops", "delivered", "cash", "loaded_value"], "rows": rows, "totals": {"trips": len(rows), "cash": str(sum((Decimal(r["cash"]) for r in rows), Z))}}


def driver_performance(date_from=None, date_to=None, **_):
    from apps.delivery.models import DeliveryNote

    qs = _window(DeliveryNote.objects.filter(trip__isnull=False), "trip__date", date_from, date_to).select_related("trip__driver")
    out = defaultdict(lambda: {"stops": 0, "delivered": 0, "failed": 0, "returns": Z})
    for dn in qs:
        d = dn.trip.driver.name if dn.trip.driver_id else "(none)"
        out[d]["stops"] += 1
        if dn.status in ("delivered", "partially_delivered"):
            out[d]["delivered"] += 1
        if dn.status in ("failed", "returned"):
            out[d]["failed"] += 1
        out[d]["returns"] += sum((l.qty_returned for l in dn.lines.all()), Z)
    rows = [{"driver": k, "stops": v["stops"], "delivered": v["delivered"], "failed": v["failed"], "success_rate": str((Decimal(v["delivered"]) / v["stops"] * 100).quantize(Decimal("0.1")) if v["stops"] else Z), "returned_qty": str(v["returns"])} for k, v in out.items()]
    return {"columns": ["driver", "stops", "delivered", "failed", "success_rate", "returned_qty"], "rows": rows, "totals": {}}


def rider_performance(date_from=None, date_to=None, rider=None, **_):
    """
    Delivery Rider Performance (owner, 27 Nov 2026: "calculate time and distance as well"): per rider per day — trips, stops
    delivered, refused and shop closed, km planned against km actually driven, hours on the road, minutes at a shop, cash.
    Times and km count only for a trip run from the phone (routing.rode): one confirmed on the web days later would read
    hundreds of hours on the road.
    """
    from apps.delivery.models import DeliveryTrip
    from apps.delivery.routing import actuals, rode

    qs = _window(DeliveryTrip.objects.exclude(status="cancelled"), "date", date_from, date_to).select_related("delivery_rep", "driver").prefetch_related("delivery_notes__customer")
    if rider:
        qs = qs.filter(delivery_rep_id=rider)
    out = {}
    for t in qs:
        who = (t.delivery_rep.full_name if t.delivery_rep_id else "") or (t.driver.name if t.driver_id else "") or "(none)"
        r = out.setdefault((t.date, who), {"date": t.date, "rider": who, "trips": 0, "stops": 0, "delivered": 0, "refused": 0, "shop_closed": 0,
                                            "planned_km": 0.0, "actual_km": 0.0, "minutes": 0, "stop_minutes": [], "cash_collected": Z})
        r["trips"] += 1
        notes = [n for n in t.delivery_notes.all() if n.status != "cancelled"]
        r["stops"] += len(notes)
        r["delivered"] += sum(1 for n in notes if n.status in ("delivered", "partially_delivered"))
        r["refused"] += sum(1 for n in notes if n.status == "returned")
        r["shop_closed"] += sum(1 for n in notes if n.status == "failed")
        r["cash_collected"] += sum((n.cash_collected or Z for n in notes), Z)
        r["planned_km"] += float(t.planned_km or 0)
        km = t.actual_km if t.actual_km is not None else actuals(t)["km"]
        r["actual_km"] += float(km or 0)
        if t.started_at and t.completed_at and t.completed_at >= t.started_at and rode(t):
            r["minutes"] += (t.completed_at - t.started_at).total_seconds() / 60
        r["stop_minutes"] += [(n.delivered_at - n.arrived_at).total_seconds() / 60 for n in notes if n.arrived_at and n.delivered_at and n.delivered_at >= n.arrived_at]
    rows = []
    for (_d, _w), r in sorted(out.items(), key=lambda kv: (kv[0][0], kv[0][1]), reverse=True):
        mins = r.pop("stop_minutes")
        r["planned_km"] = _q2(r["planned_km"]) if r["planned_km"] else ""
        r["actual_km"] = _q2(r["actual_km"]) if r["actual_km"] else ""
        r["hours"] = _q2(r.pop("minutes") / 60) if r.get("minutes") else ""
        r.pop("minutes", None)
        r["avg_minutes_per_stop"] = round(sum(mins) / len(mins)) if mins else ""
        r["cash_collected"] = str(r["cash_collected"])
        rows.append(r)
    cols = ["date", "rider", "trips", "stops", "delivered", "refused", "shop_closed", "planned_km", "actual_km", "hours", "avg_minutes_per_stop", "cash_collected"]
    total = lambda k: sum((Decimal(str(r[k])) for r in rows if r[k] != ""), Z)  # noqa: E731
    return {
        "columns": cols, "rows": rows,
        "column_labels": {"rider": "Delivery Rider", "shop_closed": "Shop Closed", "planned_km": "Planned Km", "actual_km": "Actual Km", "avg_minutes_per_stop": "Minutes Per Stop", "cash_collected": "Cash Collected"},
        "text_columns": ["rider"],
        "totals": {"trips": int(total("trips")), "stops": int(total("stops")), "delivered": int(total("delivered")), "refused": int(total("refused")), "shop_closed": int(total("shop_closed")),
                   "planned_km": _q2(total("planned_km")), "actual_km": _q2(total("actual_km")), "hours": _q2(total("hours")), "cash_collected": str(total("cash_collected"))},
    }


def _q2(v):
    return Decimal(str(v)).quantize(Decimal("0.01"))


def vehicle_usage(date_from=None, date_to=None, **_):
    from apps.delivery.models import VehicleLog

    qs = _window(VehicleLog.objects.all(), "date", date_from, date_to).select_related("vehicle")
    out = defaultdict(lambda: {"trips": 0, "km": 0, "fuel": Z, "expense": Z})
    for l in qs:
        v = out[l.vehicle.reg_no]
        v["trips"] += 1
        if l.odometer_start is not None and l.odometer_end is not None:
            v["km"] += max(l.odometer_end - l.odometer_start, 0)
        v["fuel"] += l.fuel_amount
        v["expense"] += l.expense
    rows = [{"vehicle": k, "trips": v["trips"], "km": v["km"], "fuel": str(v["fuel"]), "expense": str(v["expense"]), "cost_per_km": str(((v["fuel"] + v["expense"]) / v["km"]).quantize(Decimal("0.01")) if v["km"] else Z)} for k, v in out.items()]
    return {"columns": ["vehicle", "trips", "km", "fuel", "expense", "cost_per_km"], "rows": rows, "totals": {}}


def delivery_success(date_from=None, date_to=None, **_):
    from apps.delivery.models import DeliveryNote

    qs = _window(DeliveryNote.objects.filter(delivered_at__isnull=False), "delivered_at__date", date_from, date_to)
    counts = dict(qs.values_list("status").annotate(c=Count("id")).values_list("status", "c"))
    total = sum(counts.values())
    reasons = dict(qs.filter(status__in=["failed", "returned"]).values_list("failure_reason").annotate(c=Count("id")).values_list("failure_reason", "c"))
    rows = [{"status": k, "count": v} for k, v in counts.items()] + [{"status": f"reason: {k or '(none)'}", "count": v} for k, v in reasons.items()]
    return {"columns": ["status", "count"], "rows": rows, "totals": {"total": total, "success_rate": str((Decimal(counts.get("delivered", 0)) / total * 100).quantize(Decimal("0.1")) if total else Z)}}


def cash_per_trip(date_from=None, date_to=None, **_):
    from apps.accounting.models import Payment

    qs = _window(Payment.objects.filter(trip__isnull=False, status="posted"), "date", date_from, date_to).select_related("trip", "collected_by")
    rows = [{"trip": r["trip__number"], "date": r["trip__date"], "receipts": r["n"], "amount": str(r["s"] or 0)} for r in qs.values("trip__number", "trip__date").annotate(n=Count("id"), s=Sum("amount")).order_by("-trip__date")]
    return {"columns": ["trip", "date", "receipts", "amount"], "rows": rows, "totals": {"amount": str(sum((Decimal(r["amount"]) for r in rows), Z))}}


# --------------------------------------------------------------------- receivables
def collection_report(date_from=None, date_to=None, **_):
    from apps.accounting.models import Payment
    from apps.sales.models import RepCollection

    qs = _window(Payment.objects.filter(direction="in", status="posted", partner_type="customer"), "date", date_from, date_to).select_related("collected_by")
    rows = [{"collector": r["collected_by__full_name"] or "(unknown)", "kind": "staff", "receipts": r["n"], "amount": str(r["s"] or 0)} for r in qs.values("collected_by__full_name").annotate(n=Count("id"), s=Sum("amount"))]
    rc = _window(RepCollection.objects.exclude(status="cancelled"), "date", date_from, date_to)
    rows += [{"collector": r["rep__name"], "kind": "rep", "receipts": r["n"], "amount": str(r["s"] or 0)} for r in rc.values("rep__name").annotate(n=Count("id"), s=Sum("amount"))]
    return {"columns": ["collector", "kind", "receipts", "amount"], "rows": rows, "totals": {"amount": str(sum((Decimal(r["amount"]) for r in rows), Z))}}


def overdue_list(**_):
    """Every invoice past its due date with money still owed, the oldest first — with who sold it (owner, 26 Nov 2026)."""
    from apps.sales.models import SalesInvoice

    today = timezone.localdate()
    names = _rep_names()
    rows = []
    for i in SalesInvoice.objects.filter(status__in=["posted", "partially_paid", "overdue"], due_date__lt=today).select_related("customer", *[f[len("invoice__"):] for f in SALE_PERSON_RELATED]).order_by("due_date"):
        rep_id, person = _sale_person(i)
        rows.append({"invoice": i.number, "date": i.date, "due_date": i.due_date, "days_overdue": (today - i.due_date).days if i.due_date else 0, "customer": i.customer.name,
                     "sale_person": names.get(str(rep_id), person) if rep_id else person, "phone": phone_shape(i.customer.phone), "balance": str(i.total - i.paid_amount)})
    return {"columns": ["invoice", "date", "due_date", "days_overdue", "customer", "sale_person", "phone", "balance"], "rows": rows,
            "totals": {"balance": str(sum((Decimal(r["balance"]) for r in rows), Z))}, "column_labels": {"sale_person": "Sale Person"}}


def cheque_register(cheque_status=None, **_):
    from apps.accounting.models import Payment

    qs = Payment.objects.filter(method="cheque").exclude(status="cancelled")
    if cheque_status:
        qs = qs.filter(cheque_status=cheque_status)
    rows = [{"number": p.number, "direction": p.direction, "partner": p.partner_name, "cheque_no": p.cheque_no, "cheque_date": p.cheque_date, "bank": p.cheque_bank, "amount": str(p.amount), "status": p.cheque_status} for p in qs.order_by("cheque_date")]
    return {"columns": ["number", "direction", "partner", "cheque_no", "cheque_date", "bank", "amount", "status"], "rows": rows, "totals": {}}


def credit_utilisation(**_):
    from apps.partners.models import Customer
    from apps.partners.services import CustomerService

    rows = []
    for c in Customer.objects.filter(credit_limit__gt=0):
        out = CustomerService.outstanding(c)
        rows.append({"code": c.code, "customer": c.name, "credit_limit": str(c.credit_limit), "outstanding": str(out), "utilisation_pct": str((out / c.credit_limit * 100).quantize(Decimal("0.1"))), "available": str(c.credit_limit - out)})
    rows.sort(key=lambda r: -Decimal(r["utilisation_pct"]))
    return {"columns": ["code", "customer", "credit_limit", "outstanding", "utilisation_pct", "available"], "rows": rows, "totals": {}}


def payment_register(date_from=None, date_to=None, direction=None, **_):
    from apps.accounting.models import Payment

    qs = _window(Payment.objects.exclude(status="cancelled"), "date", date_from, date_to).select_related("collected_by")
    if direction:
        qs = qs.filter(direction=direction)
    rows = [{"number": p.number, "date": p.date, "direction": p.direction, "partner": p.partner_name, "method": p.method, "reference": p.reference, "amount": str(p.amount), "by": p.collected_by.full_name if p.collected_by else ""} for p in qs]
    return {"columns": ["number", "date", "direction", "partner", "method", "reference", "amount", "by"], "rows": rows, "totals": {"in": str(sum((Decimal(r["amount"]) for r in rows if r["direction"] == "in"), Z)), "out": str(sum((Decimal(r["amount"]) for r in rows if r["direction"] == "out"), Z))}}


def due_payments_calendar(**_):
    from apps.purchasing.models import PurchaseBill

    rows = [{"due_date": r["due_date"], "bills": r["n"], "amount": str((r["t"] or 0) - (r["p"] or 0))} for r in PurchaseBill.objects.filter(status__in=["posted", "partially_paid"]).values("due_date").annotate(n=Count("id"), t=Sum("total"), p=Sum("paid_amount")).order_by("due_date")]
    return {"columns": ["due_date", "bills", "amount"], "rows": rows, "totals": {"amount": str(sum((Decimal(r["amount"]) for r in rows), Z))}}


def gross_margin_by_supplier(date_from=None, date_to=None, **_):
    return sales_by_supplier(date_from, date_to)


def out_of_area_activity(date_from=None, date_to=None, rep=None, **_):
    """
    Every time a rep's location was checked and fell outside the area they cover — what they were doing, how far
    out they were, and whether it was refused. This is the report the sales manager reads on Monday morning.
    """
    from apps.partners.models import GeoFenceEvent

    qs = _window(GeoFenceEvent.objects.exclude(verdict="inside"), "occurred_at__date", date_from, date_to).select_related("rep", "fence", "customer")
    if rep:
        qs = qs.filter(rep_id=rep)
    rows = [{
        "when": e.occurred_at, "rep": e.rep.name if e.rep_id else "", "action": e.get_action_display(),
        "outcome": "Refused" if e.blocked else ("No location" if e.verdict == "no_fix" else "Allowed"),
        "nearest_area": e.fence.name if e.fence_id else "", "distance_km": str(round((e.distance_m or 0) / 1000, 2)) if e.distance_m else "",
        "customer": e.customer.name if e.customer_id else "", "document": e.document_number,
        "lat": str(e.gps_lat or ""), "lng": str(e.gps_lng or ""),
    } for e in qs[:5000]]
    return {"columns": ["when", "rep", "action", "outcome", "nearest_area", "distance_km", "customer", "document", "lat", "lng"],
            "rows": rows, "totals": {"count": str(len(rows)), "refused": str(sum(1 for r in rows if r["outcome"] == "Refused"))}}


def area_coverage(**_):
    """Each working area: who covers it, how big it is, and how many customers fall inside."""
    from apps.partners.geofence import GeoFenceService, area_km2
    from apps.partners.models import GeoFence

    rows = []
    for f in GeoFence.objects.prefetch_related("reps"):
        inside = GeoFenceService.customers_in(f)
        rows.append({"area": f.name, "type": f.get_type_display(), "size_km2": str(area_km2(f)),
                     "reps": ", ".join(r.name for r in f.reps.all()) or "— not assigned —",
                     "customers": str(len(inside)), "active": f.is_active})
    rows.sort(key=lambda r: -int(r["customers"]))
    return {"columns": ["area", "type", "size_km2", "reps", "customers", "active"], "rows": rows,
            "totals": {"areas": str(len(rows)), "customers": str(sum(int(r["customers"]) for r in rows))}}


def customers_without_area(**_):
    """Customers no area covers — either the pin is missing, or nobody's area reaches them."""
    from apps.partners.geofence import GeoFenceService
    from apps.partners.models import Customer, GeoFence

    covered = set()
    for f in GeoFence.objects.filter(is_active=True):
        covered.update(c.pk for c in GeoFenceService.customers_in(f))
    rows = []
    for c in Customer.objects.filter(status="active").select_related("route", "territory")[:5000]:
        if c.pk in covered:
            continue
        rows.append({"customer": c.name, "code": c.code, "city": c.city, "route": c.route.name if c.route_id else "",
                     "territory": c.territory.name if c.territory_id else "",
                     "why": "No pin on the map" if c.gps_lat is None else "Outside every area",
                     "reps": ", ".join(r.name for r in c.assigned_reps.all())})
    return {"columns": ["customer", "code", "city", "route", "territory", "why", "reps"], "rows": rows,
            "totals": {"count": str(len(rows)), "no_pin": str(sum(1 for r in rows if r["why"] == "No pin on the map"))}}


def q2(v):
    from decimal import Decimal

    return Decimal(v).quantize(Decimal("0.01"))


def stock_report(date_from=None, date_to=None, category=None, search=None, show_zero=None, **_):
    """
    The owner's stock report (21 Oct 2026, modelled on his InvoiceFlow screen): one row per product with the
    period's story in quantities AND values — opening, purchased, issued, wastage, returns, closing — valued
    at each movement's own cost (opening at the running average).
    """
    from collections import defaultdict
    from decimal import Decimal

    from django.utils import timezone

    from apps.inventory.models import StockMovement

    start = date_from or timezone.localdate().replace(day=1)
    end = date_to or timezone.localdate()
    IN_BUCKETS = {"grn": "purchase", "transfer_in": "purchase"}
    OUT_BUCKETS = {"sale_dispatch": "issue", "transfer_out": "issue", "wastage": "wastage"}
    ADJUST_TYPES = {"adjustment", "count_variance", "grn_cancelled"}
    RETURN_BUCKETS = {"sales_return": "return", "delivery_return": "return", "purchase_return": "return", "return_cancelled": "return"}

    qs = StockMovement.objects.select_related("variant__product__category").filter(occurred_at__date__lte=end)
    if category:
        qs = qs.filter(variant__product__category_id=category)
    if search:
        qs = qs.filter(variant__product__name__icontains=search) | qs.filter(variant__sku__icontains=search)

    per = defaultdict(lambda: defaultdict(Decimal))
    names, prices = {}, {}
    for m in qs:
        key = m.variant_id
        names[key] = m.variant.product.name if m.variant.name == m.variant.product.name else m.variant.name
        prices[key] = m.unit_cost or prices.get(key) or Decimal("0")
        value = (m.qty or 0) * (m.unit_cost or 0)
        if m.occurred_at.date() < start:
            per[key]["opening_qty"] += m.qty
            per[key]["opening_value"] += value
            continue
        if m.movement_type == "issue_cancelled":
            per[key]["issue_qty"] -= m.qty
            per[key]["issue_value"] -= value
            continue
        if m.movement_type in ADJUST_TYPES:
            per[key]["adjust_qty"] += m.qty          # signed: an increase adds, a decrease subtracts
            per[key]["adjust_value"] += value
            continue
        bucket = IN_BUCKETS.get(m.movement_type) or OUT_BUCKETS.get(m.movement_type) or RETURN_BUCKETS.get(m.movement_type)
        if m.movement_type == "opening":
            per[key]["opening_qty"] += m.qty
            per[key]["opening_value"] += value
        elif bucket == "purchase":
            per[key]["purchase_qty"] += m.qty
            per[key]["purchase_value"] += value
        elif bucket == "issue":
            per[key]["issue_qty"] += -m.qty
            per[key]["issue_value"] += -value
        elif bucket == "wastage":
            per[key]["wastage_qty"] += -m.qty
            per[key]["wastage_value"] += -value
        elif bucket == "return":
            per[key]["return_qty"] += m.qty
            per[key]["return_value"] += value

    rows = []
    for key, b in sorted(per.items(), key=lambda kv: names.get(kv[0], "")):
        closing_qty = b["opening_qty"] + b["purchase_qty"] + b["return_qty"] + b["adjust_qty"] - b["issue_qty"] - b["wastage_qty"]
        closing_value = b["opening_value"] + b["purchase_value"] + b["return_value"] + b["adjust_value"] - b["issue_value"] - b["wastage_value"]
        if not show_zero and all(not b[f] for f in ("opening_qty", "purchase_qty", "issue_qty", "wastage_qty", "return_qty", "adjust_qty")):
            continue
        rows.append({
            "sr": len(rows) + 1,
            "item": names.get(key, ""), "price": str(prices.get(key) or 0),
            "opening_qty": str(b["opening_qty"]), "opening_value": str(q2(b["opening_value"])),
            "purchase_qty": str(b["purchase_qty"]), "purchase_value": str(q2(b["purchase_value"])),
            "issue_qty": str(b["issue_qty"]), "issue_value": str(q2(b["issue_value"])),
            "wastage_qty": str(b["wastage_qty"]), "wastage_value": str(q2(b["wastage_value"])),
            "return_qty": str(b["return_qty"]), "return_value": str(q2(b["return_value"])),
            "adjust_qty": str(b["adjust_qty"]), "adjust_value": str(q2(b["adjust_value"])),
            "closing_qty": str(closing_qty), "closing_value": str(q2(closing_value)),
        })
    totals = {f: str(q2(sum(Decimal(r[f]) for r in rows))) for f in
              ("opening_value", "purchase_value", "issue_value", "wastage_value", "return_value", "adjust_value", "closing_value")}
    qty_price = ["Stock Qty", "Stock Price"]
    return {
        "columns": ["sr", "item", "price", "opening_qty", "opening_value", "purchase_qty", "purchase_value",
                    "issue_qty", "issue_value", "wastage_qty", "wastage_value", "return_qty", "return_value",
                    "adjust_qty", "adjust_value", "closing_qty", "closing_value"],
        # the two-row heading the owner wants: group names over Stock Qty / Stock Price pairs
        "header_groups": [{"label": "", "span": 1}, {"label": "", "span": 1}, {"label": "", "span": 1},
                          {"label": "Opening", "span": 2}, {"label": "Purchase", "span": 2},
                          {"label": "Issuance", "span": 2}, {"label": "Wastage", "span": 2},
                          {"label": "Return", "span": 2}, {"label": "Adjustment", "span": 2},
                          {"label": "Closing", "span": 2}],
        "column_labels": {"sr": "Sr No", "item": "Items", "price": "Price",
                          **{f"{g}_qty": qty_price[0] for g in ("opening", "purchase", "issue", "wastage", "return", "adjust", "closing")},
                          **{f"{g}_value": qty_price[1] for g in ("opening", "purchase", "issue", "wastage", "return", "adjust", "closing")}},
        # the reference design tints movement quantities: in green, out red, closing bold
        "column_styles": {"purchase_qty": "success", "issue_qty": "danger", "wastage_qty": "danger",
                          "return_qty": "danger", "closing_qty": "bold", "closing_value": "bold"},
        "rows": rows, "totals": totals,
    }


def sales_tax_register(date_from=None, date_to=None, **_):
    """The sales-tax return's working paper (owner, 26 Oct 2026): every FBR-taxed invoice and every
    posted credit note that reverses one, with the buyer's registration and the FBR filing state.
    Credit notes carry negative figures so the totals are the period's net output tax."""
    from apps.sales.models import CreditNote, SalesInvoice

    rows = []
    inv_qs = _window(SalesInvoice.objects.filter(status__in=["posted", "partially_paid", "paid", "overdue", "returned"]),
                     "date", date_from, date_to)
    inv_qs = inv_qs.filter(Q(force_tax_type__isnull=False) | Q(tax1_total__gt=0)).select_related("customer")
    for i in inv_qs:
        f = getattr(i, "fbr_filing", None)
        rows.append({"date": i.date, "doc": "Invoice", "number": i.number, "customer": i.customer.name,
                     "ntn_cnic": (i.customer.ntn or i.customer.cnic or "").strip(),
                     "registration": i.customer.fbr_registration_type or "",
                     "taxable_value": str(i.subtotal - i.discount), "sales_tax": str(i.tax1_total),
                     "fed": str(i.fed_total), "extra_tax": str(i.extra_tax_total), "further_tax": str(i.further_tax_total),
                     "wht": str(i.wht_total), "advance_tax": str(i.advance_tax_total), "total": str(i.total),
                     "fbr_status": (f.status if f is not None else "") or "not filed",
                     "fbr_number": (f.fbr_invoice_number if f is not None else "") or ""})
    cn_qs = _window(CreditNote.objects.filter(Q(tax1_total__gt=0) | Q(fed_total__gt=0) | Q(extra_tax_total__gt=0)
                                              | Q(further_tax_total__gt=0) | Q(wht_total__gt=0), status="posted"),
                    "date", date_from, date_to)
    for c in cn_qs.select_related("customer", "invoice"):
        rows.append({"date": c.date, "doc": "Credit note", "number": c.number, "customer": c.customer.name,
                     "ntn_cnic": (c.customer.ntn or c.customer.cnic or "").strip(),
                     "registration": c.customer.fbr_registration_type or "",
                     "taxable_value": str(-(c.subtotal - c.discount)), "sales_tax": str(-c.tax1_total),
                     "fed": str(-c.fed_total), "extra_tax": str(-c.extra_tax_total), "further_tax": str(-c.further_tax_total),
                     "wht": str(-c.wht_total), "advance_tax": str(-c.advance_tax_total), "total": str(-c.total),
                     "fbr_status": f"against {c.invoice.number}" if c.invoice_id else "standalone", "fbr_number": ""})
    rows.sort(key=lambda r: (str(r["date"]), r["number"]))
    money = ("taxable_value", "sales_tax", "fed", "extra_tax", "further_tax", "wht", "advance_tax", "total")
    totals = {k: str(sum((Decimal(r[k]) for r in rows), Z)) for k in money}
    # FED / Extra / Further / WHT / advance income tax columns appear once any document in the period carries them
    # (owner 26 / 31 Oct 2026) — total is what the buyers owe: with the advance tax, net of WHT
    others = [k for k in ("fed", "extra_tax", "further_tax", "wht", "advance_tax") if any(Decimal(r[k]) for r in rows)]
    return {"columns": ["date", "doc", "number", "customer", "ntn_cnic", "registration", "taxable_value",
                        "sales_tax", *others, "total", "fbr_status", "fbr_number"], "rows": rows, "totals": totals}


def advance_tax_register(date_from=None, date_to=None, **_):
    """
    Advance income tax collected, section 236G/236H (owner, 31 Oct 2026) — the working paper for the withholding
    statement and the deposit: every posted invoice that charged it, on its Grand Total at the customer's rate,
    and every return that gave some back (negative), with the buyer's NTN/CNIC. Taxed and untaxed invoices alike.
    """
    from apps.sales.models import CreditNote, SalesInvoice

    rows = []
    inv_qs = _window(SalesInvoice.objects.filter(status__in=["posted", "partially_paid", "paid", "overdue", "returned"], advance_tax_total__gt=0),
                     "date", date_from, date_to).select_related("customer")
    for i in inv_qs:
        grand = i.total + i.wht_total - i.advance_tax_total
        rows.append({"date": i.date, "doc": "Invoice", "number": i.number, "customer": i.customer.name,
                     "ntn_cnic": (i.customer.ntn or i.customer.cnic or "").strip(), "registration": i.customer.fbr_registration_type or "",
                     "grand_total": str(grand), "rate": str(i.advance_tax_rate), "advance_tax": str(i.advance_tax_total)})
    cn_qs = _window(CreditNote.objects.filter(status="posted", advance_tax_total__gt=0), "date", date_from, date_to).select_related("customer", "invoice")
    for c in cn_qs:
        grand = c.total + c.wht_total - c.advance_tax_total
        rows.append({"date": c.date, "doc": "Credit note", "number": c.number + (f" (against {c.invoice.number})" if c.invoice_id else ""),
                     "customer": c.customer.name, "ntn_cnic": (c.customer.ntn or c.customer.cnic or "").strip(),
                     "registration": c.customer.fbr_registration_type or "",
                     "grand_total": str(-grand), "rate": str(c.advance_tax_rate), "advance_tax": str(-c.advance_tax_total)})
    rows.sort(key=lambda r: (str(r["date"]), r["number"]))
    totals = {k: str(sum((Decimal(r[k]) for r in rows), Z)) for k in ("grand_total", "advance_tax")}
    return {"columns": ["date", "doc", "number", "customer", "ntn_cnic", "registration", "grand_total", "rate", "advance_tax"],
            "column_labels": {"ntn_cnic": "NTN / CNIC", "grand_total": "Grand Total", "rate": "Rate %", "advance_tax": "Advance Income Tax"},
            "rows": rows, "totals": totals}
