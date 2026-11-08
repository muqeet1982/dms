"""
The sheet a rep sends the office at the end of the day (owner, 5 Nov 2026): who, when and where, the shops they covered,
the units of each product family, and the day's and the month's money. A rep's name on Sales By Rep opens it.

It reads ORDERS, because this is the rep's own day: the invoice follows when the warehouse has issued the goods, days
later, and would leave a day of shops visited with nothing sold against it.
"""
import re
from collections import defaultdict
from decimal import Decimal

from django.utils import timezone
from django.utils.dateparse import parse_date

Z = Decimal("0")
LOST = ("cancelled", "rejected")   # taken, and it counts as a shop visited, but it is not a sale

# the product columns, in the order of the owner's sheet: (key, heading over it, label)
FAMILIES = [
    ("tinta_regular", "Tinta", "Regular Color"),
    ("tinta_lifting", "Tinta", "Lifting Colors"),
    ("developer_60", "Developer", "60 ml"),
    ("developer_1000", "Developer", "1000 ml"),
    ("lifting_powder", "", "Lifting Powder 500 gms"),
    ("cream_blonde", "", "Cream Blonde 50 g"),
    ("care_shampoo", "Care", "Shampoo"),
    ("care_conditioner", "Care", "Conditioner"),
    ("care_mask", "Care", "Mask"),
    ("care_others", "Care", "Others"),
    ("spray", "", "Spray"),
    ("gels", "", "Gels"),
    ("smooth_operator", "", "Smooth Operator"),
    ("others", "", "Others"),
    ("hair_straightener", "", "Hair Straightener Normal & Rebonding Extra Forte"),
    ("keratin_pack", "Keratin Straight", "Pack"),
    ("keratin_400", "Keratin Straight", "400 ml"),
    ("accessories", "", "Accessories"),
]

_ACCESSORY_CATEGORIES = {"accessories", "color accessories", "color bowl"}
# Keune's care ranges: each is its own category in the catalogue
_CARE_CATEGORIES = {
    "absolute volume", "after color treatment", "blonde savior", "color brillianz", "confident curl", "curl control",
    "dandruff detox", "derma activate", "derma exfoliate", "derma regulate", "derma sensitive", "instant revive",
    "keratin smooth", "long & strong", "lumi coat", "miracle elixir", "perfect clarity", "radiant gloss", "satin oil",
    "scalp sensitive", "silver savior / clarify", "so pure treatments", "tinta color care", "velvet smooth", "vital nutrition",
}
_LIFTING_SHADE = re.compile(r"^NO\.?\s*\d{4}\b")   # the four-figure shades: NO. 1000, 1511, 2000, 3011 ...


def family_of(category, name):
    """Which column of the sheet a product counts under. Anything these rules do not recognise is Others, never lost."""
    n, c = (name or "").upper(), (category or "").strip().lower()
    if c in _ACCESSORY_CATEGORIES:
        return "accessories"
    if "DEVELOPER" in n:
        if re.search(r"\b1000\s*ML", n):
            return "developer_1000"
        return "developer_60" if re.search(r"\b60\s*ML", n) else "others"
    if c == "tinta color":
        if _LIFTING_SHADE.match(n):
            return "tinta_lifting"
        return "tinta_regular" if n.startswith("NO") or n == "TINTA COLOR CLEAR" or "METALLIC" in n else "others"
    if "SMOOTH OPERATOR" in n:
        return "smooth_operator"
    if "CREAM BLEACH" in n:
        return "cream_blonde"
    if c == "bleach" and re.search(r"\bUB (MAGIC|POWER|FREEDOM) BLONDE", n):
        return "lifting_powder"
    if "HAIR STRAIGHTENER" in n or "H.STRAIGHT" in n or re.search(r"REBOU?NDING (E/FORT|KIT)", n):
        return "hair_straightener"
    if "KERATIN STRAIGHTENING PACK" in n:
        return "keratin_pack"
    if "KERATIN STRAIGHTENING CREAM" in n:
        return "keratin_400"
    if "SHAMPOO" in n:
        return "care_shampoo"
    if "CONDITIONER" in n or "CONDITION -" in n:
        return "care_conditioner"
    if re.search(r"\bMASK\b", n):
        return "care_mask"
    if c in _CARE_CATEGORIES or n.startswith("CARE "):
        return "care_others"
    if "SPRAY" in n or "FREESTYLER" in n:
        return "spray"
    return "gels" if re.search(r"\bGEL\b", n) else "others"


def _area(customer, location=None):
    """Where a shop is: its territory, else its route, else its city."""
    if customer.territory_id:
        return customer.territory.name
    if customer.route_id:
        return customer.route.name
    return (location.city if location is not None else "") or customer.city or ""


def _columns():
    columns = ["name", "date", "area", "visited_count", "productive_count", "new_count", *[k for k, _h, _l in FAMILIES], "total_today", "total_till_date"]
    labels = {"name": "Sale Person", "date": "Date", "area": "Area", "visited_count": "No Of Shops Visited", "productive_count": "Productive Shops", "new_count": "New Shops",
              **{k: label for k, _h, label in FAMILIES}, "total_today": "Today", "total_till_date": "Till Date"}
    groups = [{"label": "", "span": 1} for _ in range(6)]
    for _k, heading, _l in FAMILIES:
        if heading and groups[-1]["label"] == heading:
            groups[-1]["span"] += 1
        else:
            groups.append({"label": heading, "span": 1})
    groups.append({"label": "Total", "span": 2})
    return columns, labels, groups


def daily_sheet(date_from=None, date_to=None, rep=None, **_):
    from apps.partners.models import Customer, CustomerVisit, SupplierRep
    from apps.sales.models import SalesOrder, SalesOrderLine

    start, end = parse_date(str(date_from)) if date_from else None, parse_date(str(date_to)) if date_to else None
    shown = lambda d: (start is None or d >= start) and (end is None or d <= end)

    def day():
        return {"visited": set(), "productive": set(), "new": set(), "areas": set(), "units": defaultdict(lambda: Z), "today": Z}

    days = defaultdict(day)

    # "Till Date" runs from the 1st of the row's month, so it reads back to the 1st of the first month shown
    orders = SalesOrder.objects.exclude(status="draft").select_related("customer", "customer__territory", "customer__route", "location")
    if start:
        orders = orders.filter(order_date__gte=start.replace(day=1))
    if end:
        orders = orders.filter(order_date__lte=end)
    if rep:
        orders = orders.filter(rep_id=rep)
    month = defaultdict(dict)
    sold = {}
    for o in orders:
        key = (o.rep_id, o.order_date)
        live = o.status not in LOST
        if live:
            spot = month[(o.rep_id, o.order_date.year, o.order_date.month)]
            spot[o.order_date] = spot.get(o.order_date, Z) + o.total
        if not shown(o.order_date):
            continue
        d = days[key]
        d["visited"].add(o.customer_id)
        d["areas"].add(_area(o.customer, o.location))
        if live:
            d["productive"].add(o.customer_id)
            d["today"] += o.total
            sold[o.pk] = key

    lines = SalesOrderLine.objects.filter(order_id__in=sold).select_related("variant__product__category")
    family = {}
    for l in lines:
        p = l.variant.product if l.variant_id else None
        pid = p.pk if p is not None else None
        if pid not in family:
            family[pid] = family_of(p.category.name if p is not None and p.category_id else "", p.name if p is not None else "")
        days[sold[l.order_id]]["units"][family[pid]] += l.qty_ordered

    visits = CustomerVisit.objects.exclude(rep=None).select_related("customer", "customer__territory", "customer__route")
    shops = Customer.objects.exclude(requested_by_rep=None).select_related("territory", "route")
    if start:
        visits, shops = visits.filter(at__date__gte=start), shops.filter(created_at__date__gte=start)
    if end:
        visits, shops = visits.filter(at__date__lte=end), shops.filter(created_at__date__lte=end)
    if rep:
        visits, shops = visits.filter(rep_id=rep), shops.filter(requested_by_rep_id=rep)
    for v in visits:
        d = days[(v.rep_id, timezone.localtime(v.at).date())]
        d["visited"].add(v.customer_id)
        d["areas"].add(_area(v.customer))
    for c in shops:
        d = days[(c.requested_by_rep_id, timezone.localtime(c.created_at).date())]
        d["visited"].add(c.pk)   # adding a shop is standing in it
        d["new"].add(c.pk)
        d["areas"].add(_area(c))

    reps = {r.pk: r for r in SupplierRep.objects.filter(pk__in={k[0] for k in days if k[0]}).prefetch_related("territories")}

    def who(rep_id):
        return reps[rep_id].name if rep_id in reps else "(No Rep)"

    columns, labels, groups = _columns()
    rows = []
    for (rep_id, on), d in sorted(days.items(), key=lambda kv: (kv[0][0] is None, who(kv[0][0]).lower(), str(kv[0][0]), kv[0][1])):
        spot = month.get((rep_id, on.year, on.month), {})
        areas = sorted(a for a in d["areas"] if a) or (sorted(t.name for t in reps[rep_id].territories.all()) if rep_id in reps else [])
        rows.append({
            "name": who(rep_id), "date": on.isoformat(), "area": ", ".join(areas),
            "visited_count": str(len(d["visited"])), "productive_count": str(len(d["productive"])), "new_count": str(len(d["new"])),
            **{k: str(Decimal(d["units"][k]).quantize(Decimal("0.001"))) for k, _h, _l in FAMILIES},
            "total_today": str(d["today"]), "total_till_date": str(sum((v for when, v in spot.items() if when <= on), Z)),
        })

    summed = {k: sum((int(r[k]) for r in rows), 0) for k in ("visited_count", "productive_count", "new_count")}
    summed.update({k: sum((Decimal(r[k]) for r in rows), Z) for k, _h, _l in FAMILIES})
    today = sum((Decimal(r["total_today"]) for r in rows), Z)
    if rows:
        rows.append({"name": "Total", "date": "", "area": "", **{k: str(v) for k, v in summed.items()}, "total_today": str(today), "total_till_date": "", "is_group": True, "kind": "subtotal"})
    picked = SupplierRep.objects.filter(pk=rep).first() if rep else None
    if picked is not None:
        # one sale person's sheet: their name heads it, not a column repeating it on every row (owner, 8 Nov 2026)
        columns = columns[1:]
        groups = [{"label": "", "span": groups[0]["span"] - 1}, *groups[1:]] if groups and groups[0]["span"] > 1 else groups[1:]
        for r in rows:
            if r.get("kind") == "subtotal":
                r["date"] = "Total"
    return {
        "columns": columns, "column_labels": labels, "header_groups": groups, "column_styles": {"total_today": "bold", "total_till_date": "bold"},
        "rows": rows, "totals": {"sales": str(today)}, "totals_in_rows": True, "faint_zeros": True, "plain_header": True,
        "title_suffix": picked.name if picked else "", "print_params": {"rep": None} if picked else {},
    }
