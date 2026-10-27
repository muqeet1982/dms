"""
External Order API (/ext/v1, §9.3). Same order model, two ways in:
  1. Rep user JWT + X-Tenant-ID (visibility governed by the rep's external role)
  2. X-API-Key bound to a supplier (scopes)
"""
import base64
import uuid
from decimal import Decimal

from django.core.files.base import ContentFile
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import status
from rest_framework.negotiation import BaseContentNegotiation
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import Packaging, ProductVariant
from apps.catalog.serializers import ProductVariantSerializer
from apps.catalog.services import BarcodeParser, PricingService
from apps.core.exceptions import DomainError
from apps.core.models import Attachment, IdempotencyKey
from apps.core.permissions import IsTenantUser, has_tenant_perm
from apps.core.tenancy import get_current_tenant, get_current_tenant_id
from apps.partners.geofence import GeoFenceService, coords
from apps.delivery.models import DeliveryNote
from apps.delivery.serializers import DeliveryNoteSerializer
from apps.inventory.models import StockLevel
from apps.partners.models import Customer, SupplierRep
from apps.partners.services import CustomerService
from apps.sales.models import RepCollection, SalesOrder
from apps.sales.serializers import RepCollectionSerializer, SalesOrderListSerializer, SalesOrderSerializer
from apps.sales.services import OrderService, RepCollectionService

from .models import WebhookEndpoint
from .serializers import WebhookEndpointSerializer


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _loose(name):
    """A shop name as a person would compare it: case, punctuation and spacing ignored, "and" and "&" alike."""
    text = str(name or "").lower().replace("&", " and ")
    return " ".join("".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text).split())


def _parse_date(value):
    from django.utils.dateparse import parse_date

    return parse_date(value) if value else None


class ExtBase(APIView):
    permission_classes = [IsTenantUser]
    throttle_scope = "ext_api"

    # external permission → internal equivalent (staff using the mobile app / API on behalf of the company)
    INTERNAL_EQUIVALENT = {
        "external.submit_order": "sales.add_sales_order", "external.cancel_own_order": "sales.cancel_sales_order", "external.view_own_orders": "sales.view_sales_order",
        "external.view_assigned_customers": "customers.view_customer", "external.add_customer_request": "customers.add_customer", "external.view_customer_outstanding": "customers.view_customer_ledger",
        "external.view_prices": "catalog.view_product", "external.view_stock_exact": "inventory.view_stock", "external.view_stock_bands": "inventory.view_stock",
        "external.view_delivery_status": "delivery.view_all_trips", "external.record_collection": "cash.receive_payment", "external.view_own_collections": "cash.view_cash",
    }

    def perm(self, request, codename):
        if has_tenant_perm(request, codename):
            return
        alt = self.INTERNAL_EQUIVALENT.get(codename)
        if alt and has_tenant_perm(request, alt):
            return
        raise DomainError(f"Not allowed: {codename}", status_code=403)

    def context(self, request):
        """Resolve (supplier, rep) for the caller."""
        m = getattr(request, "membership", None)
        key = getattr(request, "api_key", None)
        if m is not None and m.supplier_rep_id:
            return m.supplier_rep.supplier, m.supplier_rep
        if key is not None:
            return key.supplier, key.rep
        if m is not None:
            return None, None
        raise DomainError("No supplier context.", status_code=403)

    def customers_qs(self, request):
        """
        Which customers this caller may see: a rep's own, a supplier's across its reps, or — for office staff
        working through the app — the company's.

        That last case is the one with teeth. A login holding **field** permissions resolves its customers from
        its rep record, so without one it would fall through to every customer in the company: rep permissions
        with no rep identity is an unscoped view, not a narrow one. Office staff reach these endpoints through
        `INTERNAL_EQUIVALENT` instead and are meant to see the company's book, so the test is whether the
        caller is a field login, not whether it happens to lack a rep.
        """
        supplier, rep = self.context(request)
        qs = Customer.objects.exclude(status="inactive")
        if rep is not None:
            return qs.filter(assigned_reps=rep)
        if supplier is not None:
            return qs.filter(assigned_reps__supplier=supplier).distinct()
        membership = getattr(request, "membership", None)
        if membership is not None and any(p.startswith("external.") for p in membership.permission_codenames()):
            raise DomainError(
                "This login has rep permissions but is not linked to a rep, so it has no customers of its own. "
                "Link it to a rep record under Users, or give it office roles instead.",
                code="rep_required", status_code=403,
            )
        return qs

    def _resolve_lines(self, rows):
        """
        Order lines as the app sends them — by barcode, SKU or variant id — into what `OrderService` wants.

        Shared by creating an order and correcting one, so the two cannot drift: a scanned carton has to mean
        the same quantity on a correction as it did on the original.
        """
        lines = []
        for l in rows or []:
            variant, packaging = None, None
            if l.get("barcode"):
                variant, packaging, _ = BarcodeParser.resolve(l["barcode"])
            elif l.get("sku"):
                variant = ProductVariant.objects.filter(sku=l["sku"], is_active=True).first()
            elif l.get("variant_id"):
                variant = ProductVariant.objects.filter(pk=l["variant_id"], is_active=True).first()
            if variant is None:
                raise DomainError(f"Unknown product in line {l}", status_code=400)
            if l.get("packaging"):
                packaging = Packaging.objects.filter(variant=variant, name__iexact=l["packaging"]).first() or packaging
            lines.append({"variant": variant, "packaging": packaging, "qty": Decimal(str(l.get("qty", 1))),
                          "unit_price": Decimal(str(l["unit_price"])) if l.get("unit_price") is not None else None,
                          "discount_pct": Decimal(str(l.get("discount_pct", 0) or 0)),
                          "price_per_packaging": bool(packaging and l.get("unit_price") is not None)})
        return lines

    def resolve_shop(self, request, cid):
        """A row id from the flattened list — the account's own id or a branch's — to (customer, location)."""
        if not cid:
            return None, None
        c = self.customers_qs(request).filter(pk=cid).first()
        if c is not None:
            return c, None
        from apps.partners.models import CustomerLocation

        loc = CustomerLocation.objects.filter(pk=cid, is_active=True).select_related("customer").first() if _is_uuid(str(cid)) else None
        if loc is not None and self.customers_qs(request).filter(pk=loc.customer_id).exists():
            return loc.customer, loc
        return None, None

    def log_usage(self, request, status_code):
        key = getattr(request, "api_key", None)
        if key is not None:
            from .models import ApiUsage

            ApiUsage.objects.create(api_key=key, path=request.path[:200], method=request.method, status=status_code, ip=getattr(request, "client_ip", None))

    def finalize_response(self, request, response, *args, **kwargs):
        resp = super().finalize_response(request, response, *args, **kwargs)
        try:
            self.log_usage(request, resp.status_code)
        except Exception:
            pass
        return resp


class ExtOrdersView(ExtBase):
    def get(self, request):
        self.perm(request, "external.view_own_orders")
        supplier, rep = self.context(request)
        qs = SalesOrder.objects.select_related("customer", "rep", "supplier", "trip")
        if rep is not None:
            qs = qs.filter(rep=rep)
        elif supplier is not None:
            qs = qs.filter(supplier=supplier)
        p = request.query_params
        # since/until bound the sync cursor (updated_at); date_from/date_to bound the business day (order_date)
        if p.get("since"):
            qs = qs.filter(updated_at__gte=p["since"])
        if p.get("until"):
            qs = qs.filter(updated_at__lte=p["until"])
        if _parse_date(p.get("date_from")):
            qs = qs.filter(order_date__gte=_parse_date(p["date_from"]))
        if _parse_date(p.get("date_to")):
            qs = qs.filter(order_date__lte=_parse_date(p["date_to"]))
        if p.get("status"):
            qs = qs.filter(status__in=p["status"].split(","))
        if p.get("customer"):
            cust, loc = self.resolve_shop(request, p["customer"])
            if loc is not None:
                qs = qs.filter(location=loc)
            else:
                qs = qs.filter(customer_id=p["customer"])
        if p.get("external_ref"):
            qs = qs.filter(external_ref=p["external_ref"])
        from apps.sales.views import with_location_check

        orders = list(with_location_check(qs).order_by("-created_at")[: int(p.get("limit", 100))])
        rows = SalesOrderListSerializer(orders, many=True).data
        for row, order in zip(rows, orders):
            row["can_edit"] = OrderService.rep_can_edit(order)
            row["can_cancel"] = OrderService.rep_can_cancel(order)
            row["editable_until"] = OrderService.edit_window_end(order)
        return Response({"results": rows})

    def post(self, request):
        self.perm(request, "external.submit_order")
        supplier, rep = self.context(request)
        d = request.data
        idem = request.headers.get("Idempotency-Key")
        if idem:
            existing = IdempotencyKey.objects.filter(tenant_id=get_current_tenant_id(), key=idem, path=request.path).first()
            if existing:
                return Response(existing.response_body, status=existing.response_status, headers={"Idempotent-Replayed": "true"})
        # resolve rep by code when using an API key
        if rep is None and d.get("rep_code") and supplier is not None:
            rep = SupplierRep.objects.filter(supplier=supplier, code=d["rep_code"]).first()
        # resolve customer
        c = d.get("customer") or {}
        customer, picked_location = None, None
        if c.get("id"):
            # the flattened list means the app's "customer" id may name a BRANCH — that names the shop too
            customer, picked_location = self.resolve_shop(request, c["id"])
        elif c.get("code"):
            customer = self.customers_qs(request).filter(code=c["code"]).first()
        elif c.get("phone"):
            customer = Customer.objects.filter(phone=c["phone"]).first()
            if customer is None:
                self.perm(request, "external.add_customer_request")
                customer = CustomerService.create({"name": c.get("name") or c["phone"], "phone": c["phone"], "address": c.get("address", ""), "type": c.get("type", "retailer")}, rep=rep)
        if customer is None:
            raise DomainError("Customer not found or not assigned to you.", status_code=404)
        # which of the account's shops: the id the app sent (a branch row names its shop), an explicit
        # location_id, else wherever the rep is standing
        location = picked_location
        if d.get("location_id"):
            location = customer.locations.filter(pk=d["location_id"], is_active=True).first()
            if location is None:
                raise DomainError("That shop does not belong to this customer.", code="bad_location", status_code=400)
        elif location is None:
            location = GeoFenceService.nearest_location(customer, d.get("gps"))
        # the owner's shift rule: no shift started, no order submitted; on a break, come back first
        if rep is not None and get_current_tenant() and get_current_tenant().setting("require_shift_for_orders"):
            from apps.partners.shifts import ShiftService

            open_shift = ShiftService.current(rep)
            if open_shift is None:
                raise DomainError("Start your shift to send orders.", code="no_shift", status_code=403)
            if open_shift.status == "break":
                raise DomainError("You are on a break. Come back from your break to send orders.", code="on_break", status_code=403)
        # an order is taken at the shop: check where the rep is standing against the area they cover
        blocked = GeoFenceService.guard(rep=rep, gps=d.get("gps"), action="order", customer=customer)
        if blocked:
            return blocked
        if rep is not None and not customer.assigned_reps.filter(pk=rep.pk).exists():
            customer.assigned_reps.add(rep)
        lines = self._resolve_lines(d.get("lines"))
        if not lines:
            raise DomainError("No lines.", status_code=400)
        # photos taken at the visit — proof the rep stood there, next to the location check. Each entry is
        # either a bare data URI or {"data": …, "kind": "shop"|"shelf"}; a parallel attachment_kinds list
        # labels bare entries, so older app builds keep working untouched.
        kinds = d.get("attachment_kinds") or []
        photos = []
        for i, a in enumerate(d.get("attachments") or []):
            if not a:
                continue
            if isinstance(a, dict):
                data, k = a.get("data") or "", a.get("kind") or ""
            else:
                data, k = a, (kinds[i] if i < len(kinds) else "")
            if data:
                photos.append((data, str(k or "").strip().lower()))
        want_photo = (get_current_tenant().setting("rep_order_photo") if get_current_tenant() else "optional") or "optional"
        if want_photo == "required" and not photos and rep is not None:
            raise DomainError("Take a photo of the shop to submit this order.", code="photo_required", status_code=400)
        source = "api" if getattr(request, "api_key", None) else "rep_app"
        order = OrderService.create(customer=customer, lines=lines, source=source, rep=rep, supplier=supplier, location=location, requested_delivery_date=d.get("requested_delivery_date"), notes=d.get("notes", ""), external_ref=d.get("external_ref", ""), allow_override=False, payment_mode=d.get("payment_mode"))
        GeoFenceService.link(rep, "order", order)
        where = coords(d.get("gps"))
        # the position the order was taken at is kept on the order itself, not just checked and dropped
        if where:
            order.gps_lat, order.gps_lng = Decimal(str(round(where[0], 7))), Decimal(str(round(where[1], 7)))
            accuracy = (d.get("gps") or {}).get("accuracy_m") if isinstance(d.get("gps"), dict) else None
            order.gps_accuracy_m = float(accuracy) if accuracy not in (None, "") else None
            order.save(update_fields=["gps_lat", "gps_lng", "gps_accuracy_m"])
        photo_labels = {"shop": "Shop front", "shelf": "Shelf stock"}
        for i, (att, photo_kind) in enumerate(photos):
            try:
                header, b64 = att.split(",", 1) if "," in att else ("data:image/jpeg;base64", att)
                ext = "png" if "png" in header else "jpg"
                # the name carries what, when and where it was taken, so the photo is evidence rather than decoration
                stamp = timezone.localtime().strftime("%d %b %Y %H:%M")
                place = f" at {where[0]:.5f},{where[1]:.5f}" if where else ""
                label = photo_labels.get(photo_kind, photo_kind.replace("_", " ").capitalize() if photo_kind else "Shop photo")
                Attachment.objects.create(model="SalesOrder", object_id=str(order.id), file=ContentFile(base64.b64decode(b64), name=f"order-{order.number}-{i}.{ext}"), name=f"{label} — {stamp}{place}", size=len(b64), content_type=f"image/{ext}", uploaded_by=request.user if getattr(request.user, "pk", None) and not getattr(request, "api_key", None) else None, kind=f"visit_photo:{photo_kind}" if photo_kind else "visit_photo")
            except Exception:
                pass
        body = SalesOrderSerializer(order).data
        code = status.HTTP_201_CREATED if order.created_at >= timezone.now() - timezone.timedelta(seconds=5) else status.HTTP_200_OK
        if idem:
            IdempotencyKey.objects.create(tenant_id=get_current_tenant_id(), user=None, key=idem, path=request.path, response_status=code, response_body=body)
        return Response(body, status=code)


class ExtOrderPreviewView(ExtBase):
    """
    `POST /ext/v1/orders/preview` — what a basket would cost, before it is sent (mobile AI's ask, 27 Oct 2026: the
    owner wants subtotal, discount, taxes and total on the new-order and change-the-order screens). Takes the body
    of `POST /ext/v1/orders` ({customer: {id|code}, lines, order?}); saves nothing — no number, no stock hold, no
    notification, no idempotency record. Same scope and refusals as placing the order (a shop that is not the
    rep's → 404, an unknown product → 400); the location gate, the shift rule and stock do not apply: it is a
    price, not an order. `order` (the rep's own) prices it as that order's edit would.
    """

    def post(self, request):
        self.perm(request, "external.submit_order")
        self.perm(request, "external.view_prices")
        supplier, rep = self.context(request)
        d = request.data
        c = d.get("customer") or {}
        customer = None
        if c.get("id"):
            customer, _location = self.resolve_shop(request, c["id"])
        elif c.get("code"):
            customer = self.customers_qs(request).filter(code=c["code"]).first()
        if customer is None:
            raise DomainError("Customer not found or not assigned to you.", status_code=404)
        order = None
        if d.get("order"):
            qs = SalesOrder.objects.filter(pk=d["order"], customer=customer) if _is_uuid(str(d["order"])) else SalesOrder.objects.none()
            if rep is not None:
                qs = qs.filter(rep=rep)
            elif supplier is not None:
                qs = qs.filter(supplier=supplier)
            order = qs.first()
            if order is None:
                raise DomainError("Not your order.", status_code=404)
        lines = self._resolve_lines(d.get("lines"))
        if not lines:
            raise DomainError("No lines.", status_code=400)
        return Response(OrderService.preview(customer, lines, order=order))


class ExtOrderDetailView(ExtBase):
    """
    `GET /ext/v1/orders/{id}` — one of the rep's own orders.
    `PATCH /ext/v1/orders/{id}` — correct it, until midnight of the day it was taken.

    The row carries `can_edit` and `editable_until` so the app shows the button only when it will work, rather
    than letting a rep rebuild a basket and then meet a refusal.
    """

    def _find(self, request, pk):
        supplier, rep = self.context(request)
        qs = SalesOrder.objects.all()
        if rep is not None:
            qs = qs.filter(rep=rep)
        elif supplier is not None:
            qs = qs.filter(supplier=supplier)
        o = qs.filter(Q(pk=pk) | Q(number=pk) | Q(external_ref=pk)).first()
        if o is None:
            raise DomainError("Order not found.", status_code=404)
        return o

    @staticmethod
    def _body(order):
        from apps.sales.services import OrderService

        # labels only, no image data — enough for the app to check its own photo uploads
        atts = [{"kind": a.kind.split(":", 1)[1] if ":" in a.kind else "", "name": a.name}
                for a in Attachment.objects.filter(model="SalesOrder", object_id=str(order.id)).order_by("created_at")]
        return {**SalesOrderSerializer(order).data,
                "can_edit": OrderService.rep_can_edit(order),
                "can_cancel": OrderService.rep_can_cancel(order),
                "editable_until": OrderService.edit_window_end(order),
                "attachments": atts}

    def get(self, request, pk):
        self.perm(request, "external.view_own_orders")
        return Response(self._body(self._find(request, pk)))

    def patch(self, request, pk):
        """
        No area check, deliberately: a rep spots the mistake in the next street or back at the office, which
        is the ordinary case. Where they stood when the order was *taken* is already on the order and is not
        touched by a correction.
        """
        self.perm(request, "external.submit_order")
        from apps.sales.services import OrderService

        order = self._find(request, pk)
        d = request.data if isinstance(request.data, dict) else {}
        lines = None
        if d.get("lines") is not None:
            lines = self._resolve_lines(d["lines"])
        order = OrderService.edit_by_rep(
            order, lines=lines,
            notes=d.get("notes"),
            requested_delivery_date=d.get("requested_delivery_date"),
        )
        return Response(self._body(order))


class ExtSalesSummaryView(ExtBase):
    """
    The rep's day (or range) in one call, for the app's Home: how many orders, how much of it was cash and
    how much credit, what got cancelled, and the cash the rep physically took. Bounded by order_date (the
    business day), scoped exactly like /ext/v1/orders, and totals are null without the price permission.
    """

    def get(self, request):
        self.perm(request, "external.view_own_orders")
        supplier, rep = self.context(request)
        qs = SalesOrder.objects.all()
        if rep is not None:
            qs = qs.filter(rep=rep)
        elif supplier is not None:
            qs = qs.filter(supplier=supplier)
        p = request.query_params
        date_from = _parse_date(p.get("date_from")) or timezone.localdate()
        date_to = _parse_date(p.get("date_to")) or date_from
        qs = qs.filter(order_date__gte=date_from, order_date__lte=date_to)
        live = qs.exclude(status__in=("cancelled", "rejected", "draft"))
        show_prices = has_tenant_perm(request, "external.view_prices") or has_tenant_perm(request, "catalog.view_product")

        def total(q):
            if not show_prices:
                return None
            return str(q.aggregate(s=Coalesce(Sum("total"), Decimal("0")))["s"])

        collected = None
        if show_prices and rep is not None and rep.user_id:
            from apps.accounting.models import Payment

            collected = str(Payment.objects.filter(direction="in", status="posted", collected_by_id=rep.user_id,
                                                   date__gte=date_from, date__lte=date_to).aggregate(s=Coalesce(Sum("amount"), Decimal("0")))["s"])
        return Response({
            "date_from": date_from, "date_to": date_to, "orders": live.count(),
            "total": total(live), "cash_total": total(live.filter(payment_mode="cash")),
            "credit_total": total(live.filter(payment_mode="credit")),
            "cancelled_total": total(qs.filter(status__in=("cancelled", "rejected"))),
            "cash_collected": collected,
        })


class ExtOrderCancelView(ExtBase):
    def post(self, request, pk):
        self.perm(request, "external.cancel_own_order")
        supplier, rep = self.context(request)
        qs = SalesOrder.objects.all()
        if rep is not None:
            qs = qs.filter(rep=rep)
        elif supplier is not None:
            qs = qs.filter(supplier=supplier)
        o = qs.filter(Q(pk=pk) | Q(number=pk)).first()
        if o is None:
            raise DomainError("Order not found.", status_code=404)
        OrderService.cancel(o, request.data.get("reason", "Cancelled by rep"), by_rep=True)
        return Response(SalesOrderSerializer(o).data)


class ExtCustomersView(ExtBase):
    def get(self, request):
        self.perm(request, "external.view_assigned_customers")
        qs = self.customers_qs(request).select_related("territory", "route", "price_list")
        p = request.query_params
        if p.get("territory"):
            qs = qs.filter(Q(territory__name__iexact=p["territory"]) | Q(territory_id=p["territory"]) if _is_uuid(p["territory"]) else Q(territory__name__iexact=p["territory"]))
        if p.get("search"):
            qs = qs.filter(Q(name__icontains=p["search"]) | Q(code__icontains=p["search"]) | Q(phone__icontains=p["search"]))
        if p.get("since"):
            qs = qs.filter(updated_at__gte=p["since"])
        show_outstanding = has_tenant_perm(request, "external.view_customer_outstanding") or has_tenant_perm(request, "customers.view_customer_ledger")
        rows = []
        for c in qs[:2000]:
            # FLATTENED (the app team's design, 21 Oct 2026): one row per SHOP. The account's own pin is its
            # first shop and KEEPS the customer's id, so every reference already on a phone still resolves;
            # extra branches ride as their own rows with the location's id. Money is the account's, on every row.
            locs = list(c.locations.filter(is_active=True))
            account = {"id": str(c.id), "name": c.name, "location_count": 1 + len(locs)}
            base = {"code": c.code, "type": c.type, "phone": c.phone, "city": c.city,
                    "territory": c.territory.name if c.territory_id else None, "route": c.route.name if c.route_id else None,
                    "status": c.status, "is_blocked": c.is_blocked,
                    "credit_limit": str(c.credit_limit) if show_outstanding else None, "credit_days": c.credit_days,
                    "price_list": c.price_list.name if c.price_list_id else None,
                    "ntn": c.ntn, "strn": c.strn, "cnic": c.cnic, "updated_at": c.updated_at, "account": account}
            if show_outstanding:
                base["outstanding"] = str(CustomerService.outstanding(c))
                base["overdue"] = str(CustomerService.overdue(c))
            rows.append({**base, "id": str(c.id), "name": c.name, "address": c.address, "gps": c.gps, "order_radius_m": c.order_radius_m})
            for l in locs:
                rows.append({**base, "id": str(l.id), "name": l.name, "address": l.address or c.address, "gps": l.gps, "order_radius_m": l.order_radius_m})
        return Response({"results": rows})

    def post(self, request):
        """
        Add a shop. Honours `Idempotency-Key`, like orders: a replayed key answers `200` with the original row.

        The app queues a new shop and retries on a lost connection, so without that a rep at the back of a
        market creates the same shop twice, with two codes and two ledgers. (Found by the mobile team, who
        kept the retry and reported it rather than papering over it.)
        """
        self.perm(request, "external.add_customer_request")
        supplier, rep = self.context(request)
        d = request.data

        idem = request.headers.get("Idempotency-Key")
        if idem:
            seen = IdempotencyKey.objects.filter(tenant_id=get_current_tenant_id(), key=idem, path=request.path).first()
            if seen:
                return Response(seen.response_body, status=200)

        # Validate the payload *before* the area check, so a request missing a field and a fix hears about
        # both. The other way round, a rep fixes their location only to be told the phone number is missing.
        if not str(d.get("name") or "").strip():
            raise DomainError("name is required", code="bad_customer", status_code=400)
        if d.get("parent_customer_id"):
            # another branch of an account the rep already has: it inherits the account's NTN/CNIC and ledger
            from apps.partners.models import CustomerLocation

            parent = self.customers_qs(request).filter(pk=d["parent_customer_id"]).first()
            if parent is None:
                raise DomainError("That account is not yours.", code="bad_customer", status_code=400, errors={"parent_customer_id": [str(d["parent_customer_id"])]})
            gps_b = d.get("gps") or {}
            pin_b = (gps_b.get("lat"), gps_b.get("lng"))
            if d.get("map_url"):
                from apps.partners.maplink import parse_location as _parse

                found_b = _parse(d["map_url"])
                if found_b is None:
                    raise DomainError("map_url could not be read — share a Google Maps link or \"lat, lng\".", code="bad_customer", status_code=400)
                pin_b = found_b
            branch = CustomerLocation.objects.create(customer=parent, name=d["name"].strip()[:120],
                                                     address=d.get("address", ""), city=d.get("city", "")[:80],
                                                     gps_lat=pin_b[0], gps_lng=pin_b[1])
            body = {"id": str(branch.id), "name": branch.name, "status": "active", "type": parent.type,
                    "account": {"id": str(parent.pk), "name": parent.name, "location_count": 1 + parent.locations.filter(is_active=True).count()}}
            if idem:
                IdempotencyKey.objects.create(tenant_id=get_current_tenant_id(), user=None, key=idem,
                                              path=request.path, response_status=201, response_body=body)
            return Response(body, status=201)
        kind = d.get("type") or "retailer"
        if kind not in dict(Customer.TYPES):
            raise DomainError(f"type must be one of: {', '.join(dict(Customer.TYPES))}",
                              code="bad_customer", status_code=400, errors={"type": [kind]})
        # the owner's rule (20 Oct 2026): a new shop carries NTN or CNIC — one of the two, always
        if not str(d.get("ntn") or "").strip() and not str(d.get("cnic") or "").strip():
            raise DomainError("Give the shop's NTN or CNIC — one of the two is required.",
                              code="bad_customer", status_code=400, errors={"ntn": ["required (or cnic)"], "cnic": ["required (or ntn)"]})

        gps = d.get("gps") or {}
        rep_gps = d.get("rep_gps") or {}
        pin_source = str(d.get("pin_source") or "")[:12]
        later = bool(d.get("location_later"))
        # the shop's pin: from 5.1 `gps` IS the pin (pin_source says how it was set, rep_gps says where the
        # rep stood); older builds send only gps, which doubles as both. A pasted link still wins. A shop may
        # also arrive with NO location at all (location_later) — the record matters more than the pin.
        pin = {"lat": None, "lng": None} if later else {"lat": gps.get("lat"), "lng": gps.get("lng")}
        if not later and d.get("map_url"):
            from apps.partners.maplink import parse_location

            found = parse_location(d["map_url"])
            if found is None:
                raise DomainError("map_url could not be read — share a Google Maps link or \"lat, lng\".",
                                  code="bad_customer", status_code=400, errors={"map_url": [str(d["map_url"])[:200]]})
            pin = {"lat": found[0], "lng": found[1]}
            pin_source = pin_source or "link"
        stood = rep_gps if rep_gps.get("lat") is not None else gps
        # A new shop is an observation, not a transaction: a rep is told about one in the next market, or adds
        # it from the office at the end of the day, or the area has grown and nobody has redrawn the polygon.
        # Refusing the write would destroy the record — and the shop arrives `pending`, so the office has the
        # final say. The verdict is recorded on the row instead, and an *order* from outside is still refused.
        event = GeoFenceService.check(rep=rep, gps=stood, action="customer", enforce=False)
        c = CustomerService.create({
            "name": d["name"].strip(), "type": kind, "phone": d.get("phone", ""), "address": d.get("address", ""),
            "city": d.get("city", ""), "contact_person": d.get("contact_person", ""),
            # a rep copies these off the shop's licence, punctuated however it is printed, so they are kept as
            # typed — they were being accepted and silently dropped
            "ntn": (d.get("ntn") or "").strip(), "strn": (d.get("strn") or "").strip(), "cnic": (d.get("cnic") or "").strip(),
            "gps_lat": pin["lat"], "gps_lng": pin["lng"],
        }, rep=rep)
        if event is not None:
            Customer.objects.filter(pk=c.pk).update(
                added_inside_area=(event.verdict == "inside"),
                added_distance_m=event.distance_m,
            )
            c.refresh_from_db(fields=["added_inside_area", "added_distance_m"])
        extra = {"pin_source": pin_source}
        if pin["lat"] is not None and stood.get("lat") is not None:
            from apps.partners.geofence import distance_m as _dist

            extra["pin_rep_distance_m"] = _dist((float(pin["lat"]), float(pin["lng"])),
                                                (float(stood["lat"]), float(stood["lng"])))
        Customer.objects.filter(pk=c.pk).update(**extra)
        GeoFenceService.link(rep, "customer", c)
        body = {"id": str(c.id), "code": c.code, "name": c.name, "status": c.status, "type": c.type,
                "ntn": c.ntn, "strn": c.strn, "cnic": c.cnic,
                "inside_area": c.added_inside_area, "distance_m": c.added_distance_m,
                "pin_source": pin_source, "location_later": later and pin["lat"] is None}
        if idem:
            IdempotencyKey.objects.create(tenant_id=get_current_tenant_id(), user=None, key=idem,
                                          path=request.path, response_status=201, response_body=body)
        return Response(body, status=201)


class ExtCustomerPinView(ExtBase):
    """
    `POST /ext/v1/customers/{id}/location` — a rep gives a pinless shop its pin, usually on the first visit
    ("Save where I am"). A shop that already has one is the office's to move, so this refuses with 409.
    """

    def post(self, request, pk):
        self.perm(request, "external.add_customer_request")
        supplier, rep = self.context(request)
        d = request.data

        idem = request.headers.get("Idempotency-Key")
        if idem:
            seen = IdempotencyKey.objects.filter(tenant_id=get_current_tenant_id(), key=idem, path=request.path).first()
            if seen:
                return Response(seen.response_body, status=200)

        c = self.customers_qs(request).filter(pk=pk).first()
        if c is None:
            raise DomainError("That shop is not assigned to you.", status_code=404)
        if c.gps_lat is not None and c.gps_lng is not None:
            raise DomainError("Only the office can move a shop's location.", code="pin_exists", status_code=409)

        gps = d.get("gps") or {}
        rep_gps = d.get("rep_gps") or {}
        pin_source = str(d.get("pin_source") or ("gps" if gps.get("lat") is not None else ""))[:12]
        pin = (gps.get("lat"), gps.get("lng"))
        if d.get("map_url"):
            from apps.partners.maplink import parse_location

            found = parse_location(d["map_url"])
            if found is None:
                raise DomainError("map_url could not be read — share a Google Maps link or \"lat, lng\".",
                                  code="bad_pin", status_code=400, errors={"map_url": [str(d["map_url"])[:200]]})
            pin = found
            pin_source = pin_source or "link"
        if pin[0] is None or pin[1] is None:
            raise DomainError("Send gps {lat, lng} or a map_url.", code="bad_pin", status_code=400)
        c.gps_lat, c.gps_lng = pin[0], pin[1]
        c.pin_source = pin_source
        stood = rep_gps if rep_gps.get("lat") is not None else gps
        if stood.get("lat") is not None:
            from apps.partners.geofence import distance_m as _dist

            c.pin_rep_distance_m = _dist((float(pin[0]), float(pin[1])), (float(stood["lat"]), float(stood["lng"])))
        c.save(update_fields=["gps_lat", "gps_lng", "pin_source", "pin_rep_distance_m", "updated_at"])
        from apps.core.audit import AuditService

        AuditService.log("customer.pin_set", c, after={"pin_source": pin_source, "lat": str(pin[0]), "lng": str(pin[1]),
                                                      "rep": rep.name if rep else None})
        body = {"id": str(c.id), "code": c.code, "name": c.name, "status": c.status, "type": c.type,
                "gps": {"lat": float(c.gps_lat), "lng": float(c.gps_lng)},
                "pin_source": c.pin_source, "pin_rep_distance_m": c.pin_rep_distance_m}
        if idem:
            IdempotencyKey.objects.create(tenant_id=get_current_tenant_id(), user=None, key=idem,
                                          path=request.path, response_status=201, response_body=body)
        return Response(body, status=201)


def _is_uuid(v):
    try:
        uuid.UUID(str(v))
        return True
    except Exception:
        return False


class ExtCustomerMatchView(ExtBase):
    """
    `GET /ext/v1/customers/similar?name=&phone=&city=` — does this shop already exist?

    The rep most likely to add a shop is the one who could not find it in search, so the duplicate arrives
    through the one door search does not cover. This matches the way the name was actually typed — case,
    punctuation and spacing ignored, "and"/"&" treated alike — and on the phone's digits, which is the
    strongest signal there is. Nothing is created and nothing is blocked: the app asks, shows "this looks
    like C-00013", and the rep decides.
    """

    def get(self, request):
        self.perm(request, "external.view_assigned_customers")
        name = (request.query_params.get("name") or "").strip()
        phone = _digits(request.query_params.get("phone") or "")
        city = (request.query_params.get("city") or "").strip()
        if not name and not phone:
            raise DomainError("Send a name or a phone number to match on.", code="bad_match", status_code=400)

        # every customer on the company, not only the rep's own — a shop another rep already added is
        # exactly the duplicate worth catching
        rows = Customer.objects.all().only("id", "code", "name", "phone", "city", "type", "status")
        wanted = _loose(name)
        out = []
        for c in rows:
            why = None
            if phone and _digits(c.phone or "") and _digits(c.phone)[-9:] == phone[-9:]:
                why = "phone"
            elif wanted and _loose(c.name) == wanted:
                why = "name"
            elif wanted and len(wanted) > 6 and (wanted in _loose(c.name) or _loose(c.name) in wanted):
                why = "name_part"
            if why is None:
                continue
            if city and why.startswith("name") and (c.city or "").strip().lower() not in ("", city.lower()):
                why += "_other_city"
            out.append({"id": str(c.id), "code": c.code, "name": c.name, "phone": c.phone,
                        "city": c.city, "type": c.type, "status": c.status, "matched_on": why})
            if len(out) >= 10:
                break
        order = {"phone": 0, "name": 1, "name_part": 2, "name_other_city": 3, "name_part_other_city": 4}
        out.sort(key=lambda r: order.get(r["matched_on"], 9))
        return Response({"count": len(out), "results": out})


class ExtProductsView(ExtBase):
    def get(self, request):
        self.perm(request, "catalog.view_product") if getattr(request, "api_key", None) else None
        supplier, rep = self.context(request)
        show_prices = has_tenant_perm(request, "external.view_prices") or has_tenant_perm(request, "catalog.view_product")
        qs = ProductVariant.objects.filter(is_active=True, product__status="active").select_related("product", "product__brand", "product__category", "product__base_uom").prefetch_related("packagings", "barcodes")
        if supplier is not None:
            qs = qs.filter(Q(product__supplier=supplier) | Q(product__supplier__isnull=True))
        p = request.query_params
        if p.get("search"):
            qs = qs.filter(Q(name__icontains=p["search"]) | Q(sku__icontains=p["search"]) | Q(barcodes__code=p["search"])).distinct()
        if p.get("since"):
            qs = qs.filter(updated_at__gte=p["since"])
        customer = Customer.objects.filter(pk=p.get("customer")).first() if p.get("customer") else None
        # `stock_on_hand` is declared on the serializer and annotated only by the staff views, so it came back
        # null on every row here — a field that is always empty is worse than no field. Fill it for a role that
        # may see the number, and drop it for one that may not.
        exact_stock = has_tenant_perm(request, "external.view_stock_exact") or has_tenant_perm(request, "inventory.view_stock")
        if exact_stock:
            qs = qs.annotate(stock_on_hand=Coalesce(Sum("stock_levels__qty_on_hand"), Decimal("0")),
                             stock_reserved=Coalesce(Sum("stock_levels__qty_reserved"), Decimal("0")))
        rows = []
        for v in qs[:5000]:
            row = ProductVariantSerializer(v).data
            row.pop("cost_price", None)
            row.pop("avg_cost", None)
            if exact_stock:
                row["stock_on_hand"] = str(v.stock_on_hand)
                row["available"] = str(v.stock_on_hand - v.stock_reserved)
            else:
                row.pop("stock_on_hand", None)
            if show_prices:
                price, pl = PricingService.price_for(v, customer)
                row["price"] = str(price)
                row["price_list"] = pl.name if pl else None
            else:
                row.pop("sale_price", None)
                row.pop("retail_price", None)
            rows.append(row)
        return Response({"results": rows})


class ExtStockView(ExtBase):
    def get(self, request):
        exact = has_tenant_perm(request, "external.view_stock_exact") or has_tenant_perm(request, "inventory.view_stock")
        bands = has_tenant_perm(request, "external.view_stock_bands")
        if not exact and not bands:
            raise DomainError("Stock visibility is not enabled for your role.", status_code=403)
        supplier, rep = self.context(request)
        # Start from the catalogue, not from stock rows: an item with nothing in the warehouse has no
        # StockLevel at all, and filtering on stock made it *disappear* from this list rather than come back
        # as `out` — so the app had nothing to grey out and the one thing a rep must not order looked ordinary.
        where = Q()
        if request.query_params.get("warehouse"):
            where = Q(stock_levels__warehouse_id=request.query_params["warehouse"])
        qs = (ProductVariant.objects.filter(is_active=True, product__status="active")
              .select_related("product"))
        if supplier is not None:
            qs = qs.filter(Q(product__supplier=supplier) | Q(product__supplier__isnull=True))
        qs = qs.annotate(q=Coalesce(Sum("stock_levels__qty_on_hand", filter=where), Decimal("0")),
                         r=Coalesce(Sum("stock_levels__qty_reserved", filter=where), Decimal("0")))
        rows = []
        for v in qs[:5000]:
            avail = (v.q or 0) - (v.r or 0)
            row = {"variant_id": str(v.pk), "sku": v.sku, "name": v.name}
            if exact:
                row["available"] = str(avail)
                row["on_hand"] = str(v.q or 0)
            mn, mx = v.product.min_stock or 0, v.product.max_stock or 0
            # the band goes to everyone, including a role that sees the number — the app colours by it
            row["band"] = "out" if avail <= 0 else "low" if (mn and avail <= mn) else "high" if (mx and avail >= mx * Decimal("0.6")) else "medium"
            rows.append(row)
        return Response({"results": rows})


class ExtDeliveriesView(ExtBase):
    def get(self, request):
        self.perm(request, "external.view_delivery_status")
        supplier, rep = self.context(request)
        qs = DeliveryNote.objects.select_related("order", "customer", "trip", "trip__driver", "trip__vehicle")
        if rep is not None:
            qs = qs.filter(order__rep=rep)
        elif supplier is not None:
            qs = qs.filter(order__supplier=supplier)
        if request.query_params.get("order"):
            qs = qs.filter(Q(order_id=request.query_params["order"]) if _is_uuid(request.query_params["order"]) else Q(order__number=request.query_params["order"]))
        if request.query_params.get("since"):
            qs = qs.filter(updated_at__gte=request.query_params["since"])
        rows = [{"id": str(d.id), "number": d.number, "order": d.order.number, "customer": d.customer.name, "status": d.status, "trip": d.trip.number if d.trip_id else None, "driver": d.trip.driver.name if d.trip_id and d.trip.driver_id else None, "vehicle": d.trip.vehicle.reg_no if d.trip_id and d.trip.vehicle_id else None, "delivered_at": d.delivered_at, "receiver_name": d.receiver_name, "cash_collected": str(d.cash_collected)} for d in qs.order_by("-created_at")[:500]]
        return Response({"results": rows})


class ExtCollectionsView(ExtBase):
    def get(self, request):
        self.perm(request, "external.view_own_collections")
        supplier, rep = self.context(request)
        qs = RepCollection.objects.select_related("rep", "customer", "settlement")
        if rep is not None:
            qs = qs.filter(rep=rep)
        elif supplier is not None:
            qs = qs.filter(rep__supplier=supplier)
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        return Response({"results": RepCollectionSerializer(qs[:500], many=True).data, "held_total": str(qs.filter(status="held_by_rep").aggregate(s=Sum("amount"))["s"] or 0)})

    def post(self, request):
        self.perm(request, "external.record_collection")
        supplier, rep = self.context(request)
        d = request.data
        if rep is None and d.get("rep_code") and supplier is not None:
            rep = SupplierRep.objects.filter(supplier=supplier, code=d["rep_code"]).first()
        if rep is None:
            raise DomainError("rep_code is required.", status_code=400)
        customer = None
        if _is_uuid(d.get("customer", "")):
            customer, _loc = self.resolve_shop(request, d.get("customer"))
        else:
            customer = self.customers_qs(request).filter(Q(code=d.get("customer"))).first()
        if customer is None:
            raise DomainError("Customer not found.", status_code=404)
        blocked = GeoFenceService.guard(rep=rep, gps=d.get("gps"), action="collection", customer=customer)
        if blocked:
            return blocked
        col = RepCollectionService.record(rep=rep, customer=customer, amount=d["amount"], method=d.get("method", "cash"), reference=d.get("reference", ""), allocations=d.get("allocations"), notes=d.get("notes", ""), gps=d.get("gps"))
        return Response(RepCollectionSerializer(col).data, status=201)


class ExtVisitsView(ExtBase):
    """
    `GET /ext/v1/visits?date=` — the rep's own beat for a day, newest first.
    `POST /ext/v1/visits` — record visits, one object or a batch, whether or not anything was sold.

    This is the trace a day of driving leaves when nothing sells: the office can otherwise not tell twelve
    calls with four orders from four calls with four orders.

    The batch form is the phone's offline queue, and answers the same way `tracking/pings` does, because the
    same queue code carries both:

        {"visits": [{"client_id": "mg4k2p-x7b3qa", "customer": "FS-001", "checked_in_at": "…Z",
                     "gps": {"lat": 31.518, "lng": 74.348, "accuracy_m": 12.4},
                     "outcome": "no_order", "no_order_reason": "has_stock", "note": "asked for a shade card",
                     "closed_at": "…Z", "photo": "data:image/jpeg;base64,…", "photo_taken_at": "…Z"}]}
        → 202 {"accepted": 1, "duplicates": 0, "rejected": 0}

    **A visit is never refused for being outside the rep's area.** An order is a transaction and the area rule
    guards it; a visit is an observation, and refusing it would destroy the evidence it exists to keep. The
    position and the verdict are recorded and the office judges.
    """

    MAX_BATCH = 100

    def get(self, request):
        self.perm(request, "visits.view_own")
        from apps.partners.models import CustomerVisit
        from apps.partners.visits import VisitService, policy

        supplier, rep = self.context(request)
        qs = CustomerVisit.objects.select_related("customer", "order", "rep", "user")
        if rep is not None:
            qs = qs.filter(rep=rep)
        elif supplier is not None:
            qs = qs.filter(rep__supplier=supplier)
        else:
            qs = qs.filter(user=request.user)
        if request.query_params.get("customer"):
            c = request.query_params["customer"]
            qs = qs.filter(Q(customer_id=c) if _is_uuid(c) else Q(customer__code=c))
        on = _parse_date(request.query_params.get("date"))
        if on:
            from apps.partners.tracking import TrackingService

            start, end = TrackingService.day_range(on)
            qs = qs.filter(at__gte=start, at__lt=end)
        return Response({"date": on, "results": [VisitService.serialize(v, full=False) for v in qs[:300]],
                         "policy": policy()})

    def post(self, request):
        self.perm(request, "visits.record")
        batch = request.data.get("visits") if isinstance(request.data, dict) else None
        if batch is None:
            visit, _ = self._one(request, request.data, index=None)
            from apps.partners.visits import VisitService

            return Response(VisitService.serialize(visit), status=201)

        if not isinstance(batch, list) or not batch:
            raise DomainError("Send `visits` as a non-empty list.", code="bad_visit", status_code=400)
        if len(batch) > self.MAX_BATCH:
            raise DomainError(f"Send at most {self.MAX_BATCH} visits per request.", code="too_many", status_code=413)
        accepted = duplicates = rejected = 0
        for i, item in enumerate(batch):
            _, created = self._one(request, item, index=i)
            if created is None:
                rejected += 1
            elif created:
                accepted += 1
            else:
                duplicates += 1
        return Response({"accepted": accepted, "duplicates": duplicates, "rejected": rejected}, status=202)

    def _one(self, request, d, index):
        if _is_uuid(str(d.get("customer") or d.get("customer_id") or "")):
            _c, _l = self.resolve_shop(request, d.get("customer") or d.get("customer_id"))
            if _c is not None:
                d = {**d, "customer": str(_c.pk), "customer_id": str(_c.pk)}
        """
        Record one visit. In a batch, a bad item is a 400 naming its index — the phone drops a 4xx batch, so a
        malformed row must never come back as a 500 and be retried for ever.
        """
        from django.utils.dateparse import parse_datetime

        from apps.partners.geofence import GeoFenceService
        from apps.partners.visits import VisitService

        where = f"visits[{index}]: " if index is not None else ""
        if not isinstance(d, dict):
            raise DomainError(f"{where}expected an object.", code="bad_visit", status_code=400)
        supplier, rep = self.context(request)
        if rep is None and d.get("rep_code") and supplier is not None:
            rep = SupplierRep.objects.filter(supplier=supplier, code=d["rep_code"]).first()
        key = d.get("customer") or ""
        customer = self.customers_qs(request).filter(Q(pk=key) if _is_uuid(str(key)) else Q(code=key)).first()
        if customer is None:
            raise DomainError(f"{where}customer not found or not assigned to you.", code="bad_visit", status_code=404 if index is None else 400)

        when = d.get("checked_in_at") or d.get("at")
        try:
            # the verdict is recorded; it never refuses — see the class docstring
            GeoFenceService.check(rep=rep, gps=d.get("gps"), action="visit", customer=customer, enforce=False)
            visit, created = VisitService.check_in(
                rep=rep, customer=customer, user=request.user if getattr(request.user, "pk", None) else None,
                gps=d.get("gps"), at=parse_datetime(when) if when else None,
                closed_at=parse_datetime(d["closed_at"]) if d.get("closed_at") else None,
                outcome=d.get("outcome", "no_order"),
                reason=d.get("no_order_reason") or d.get("reason", ""),
                note=d.get("note", ""),
                photo=request.FILES.get("photo") or d.get("photo"),
                photo_taken_at=parse_datetime(d["photo_taken_at"]) if d.get("photo_taken_at") else None,
                client_id=d.get("client_id", ""), app_version=d.get("app_version", ""),
                order_ref=d.get("order_ref") or "",
            )
        except DomainError as error:
            if index is None:
                raise
            # `type` is carried on the detail string, not on the exception, so naming the index must not flatten
            # it to a plain str — the phone's queue branches on `photo_not_live` versus `bad_visit`.
            from rest_framework.exceptions import ErrorDetail

            error.detail = ErrorDetail(f"{where}{error.detail}", code=getattr(error.detail, "code", None))
            raise
        return visit, created


class ExtTrackingPolicyView(ExtBase):
    """`GET /ext/v1/tracking/policy` — how often to sample, when, and for how long it is kept. The app owns none
    of this: battery life, working hours and retention are the company's decisions."""

    def get(self, request):
        from apps.partners.tracking import policy

        return Response(policy(get_current_tenant()))


class ExtTrackingPingsView(ExtBase):
    """
    `POST /ext/v1/tracking/pings` — a batch of fixes the phone collected, possibly hours ago and possibly twice.

    Deliberately forgiving: a malformed point is a `400` naming its index (the app drops that batch), while
    anything transient must be a `5xx` so the offline queue retries instead of throwing the day away. The working
    area is **not** checked here — a rep driving home is outside every area, and those points are the route.
    """

    def post(self, request):
        from apps.partners.tracking import MAX_BATCH, TrackingService, clean_ping, policy

        tenant = get_current_tenant()
        if not policy(tenant)["enabled"]:
            raise DomainError("Tracking is switched off for this company.", code="tracking_off", status_code=403)
        pings = request.data.get("pings")
        if not isinstance(pings, list) or not pings:
            raise DomainError("Send a non-empty `pings` array.", status_code=400)
        if len(pings) > MAX_BATCH:
            raise DomainError(f"At most {MAX_BATCH} pings per request — split the batch.", code="too_many", status_code=413)
        try:
            cleaned = [clean_ping(p, i) for i, p in enumerate(pings)]
        except ValueError as e:
            raise DomainError(str(e), code="bad_ping", status_code=400) from e
        _supplier, rep = self.context(request)
        out = TrackingService.record(request.user, rep, cleaned)
        return Response(out, status=202)


class ExtTrackingMeView(ExtBase):
    """`GET /ext/v1/tracking/me?date=YYYY-MM-DD` — a rep's own day. Being tracked without being able to see what
    was recorded is what makes people uninstall the app."""

    def get(self, request):
        from apps.partners.tracking import day_for

        _supplier, rep = self.context(request)
        on = _parse_date(request.query_params.get("date")) or timezone.localdate()
        day = day_for(request.user, on, rep=rep, full=request.query_params.get("precision") == "full")
        day.pop("user_id", None)
        day.pop("user_name", None)
        return Response(day)


class ExtMyAreasView(ExtBase):
    """
    `GET /ext/v1/my-areas` — the working areas this rep covers, ready to draw on a map and to check against
    offline. `policy` says what happens outside: `off`, `warn` (recorded) or `block` (refused).
    """

    def get(self, request):
        from apps.partners.geofence import GeoFenceService, coords, area_km2

        supplier, rep = self.context(request)
        tenant = get_current_tenant()
        fences = list(GeoFenceService.fences_for(rep)) if rep is not None else []
        from apps.partners.tracking import policy as tracking_policy
        from apps.partners.visits import policy as visit_policy

        return Response({
            # per rep where the office has set one on their profile, the company default otherwise
            "policy": GeoFenceService.policy_for(tenant, rep),
            "location_required": bool(tenant.setting("geofence_require_location")) if tenant else False,
            # what the policy is measured against: at the shop's own pin, or inside the areas below
            "order_rule": {"mode": "shop_proximity" if (tenant.setting("order_location_rule") if tenant else "shop") == "shop" else "area",
                           "radius_m": int(tenant.setting("order_proximity_m") or 50) if tenant else 50},
            # the app reads every field rule in one call on login
            "tracking": tracking_policy(tenant),
            # the shift model (owner, 20 Oct 2026): absent/enabled:false = no shift card, no order gate, clock
            # tracking as before; enabled = the shift owns the tracker and (optionally) the order gate
            "shift": {
                "enabled": bool(tenant and (tenant.setting("tracking_mode") == "shift" or tenant.setting("require_shift_for_orders"))),
                "require_for_orders": bool(tenant and tenant.setting("require_shift_for_orders")),
                "auto_end_at": str(tenant.setting("shift_auto_end_at") or "23:59") if tenant else "23:59",
                "gap_minutes": int(tenant.setting("shift_gap_minutes") or 15) if tenant else 15,
            },
            "visit": visit_policy(tenant),
            # the shop kinds a new customer may be given — the app renders these rather than shipping a copy
            "customer_types": [{"value": v, "label": label} for v, label in Customer.TYPES],
            # the owner retired employee area geofencing (19 Oct 2026): under shop_proximity nothing a rep
            # does involves an area, so none are sent; a company switched back to "area" gets them again,
            # because the app's offline gate needs the polygons
            "areas": [] if (tenant.setting("order_location_rule") if tenant else "shop") == "shop" else [{
                "id": str(f.id), "name": f.name, "type": f.type, "colour": f.colour,
                "centre": {"lat": float(f.centre_lat), "lng": float(f.centre_lng)} if f.centre_lat is not None else None,
                "radius_m": f.radius_m, "points": f.points, "area_km2": area_km2(f),
            } for f in fences],
        })


class ExtWebhooksView(ExtBase):
    def get(self, request):
        key = getattr(request, "api_key", None)
        qs = WebhookEndpoint.objects.filter(api_key=key) if key else WebhookEndpoint.objects.none()
        return Response({"results": WebhookEndpointSerializer(qs, many=True).data})

    def post(self, request):
        key = getattr(request, "api_key", None)
        if key is None or "webhooks:manage" not in key.scopes:
            raise DomainError("Webhook registration requires an API key with webhooks:manage scope.", status_code=403)
        s = WebhookEndpointSerializer(data={"url": request.data.get("url"), "events": request.data.get("events", WebhookEndpoint.EVENTS), "description": request.data.get("description", "")})
        s.is_valid(raise_exception=True)
        ep = s.save(api_key=key, supplier=key.supplier)
        return Response(WebhookEndpointSerializer(ep).data, status=201)


class ExtMeView(ExtBase):
    def get(self, request):
        supplier, rep = self.context(request)
        m = getattr(request, "membership", None)
        key = getattr(request, "api_key", None)
        perms = sorted(m.permission_codenames()) if m else sorted({p for s in key.scopes for p in __import__("apps.integrations.models", fromlist=["SCOPE_TO_PERMS"]).SCOPE_TO_PERMS.get(s, [])})
        return Response({"tenant": {"id": str(request.tenant.id), "name": request.tenant.display_name, "currency": request.tenant.base_currency}, "supplier": {"id": str(supplier.id), "name": supplier.name} if supplier else None, "rep": {"id": str(rep.id), "code": rep.code, "name": rep.name, "target": str(rep.monthly_target)} if rep else None, "permissions": perms, "auth": "api_key" if key else "jwt"})


class _ServerPicksTheType(BaseContentNegotiation):
    """
    The handler builds the PDF response itself; DRF's negotiation only governs error bodies. Without this,
    a client sending `Accept: application/pdf` — the honest header for a PDF endpoint — met a 406 before
    the handler ever ran, because only a JSON renderer was registered.
    """

    def select_parser(self, request, parsers):
        return parsers[0] if parsers else None

    def select_renderer(self, request, renderers, format_suffix=None):
        return (renderers[0], renderers[0].media_type)


class ExtOrderPdfView(ExtBase):
    """
    `GET /ext/v1/orders/{id}/pdf` — the rep's own order as the printed document, for the app's print view and
    the WhatsApp/email share sheet. Same scoping as the order detail: only an order that is theirs.
    `GET /ext/v1/orders/{id}/invoice-pdf` — the invoice raised on that order, when one exists.
    Any `Accept` header is honoured: success is a PDF, an error body is JSON.
    """

    content_negotiation_class = _ServerPicksTheType

    def get(self, request, pk, kind="order"):
        from apps.sales.documents import invoice_pdf, order_pdf
        from apps.sales.models import SalesInvoice

        self.perm(request, "external.view_own_orders")
        supplier, rep = self.context(request)
        qs = SalesOrder.objects.all()
        if rep is not None:
            qs = qs.filter(rep=rep)
        elif supplier is not None:
            qs = qs.filter(supplier=supplier)
        o = qs.filter(pk=pk).first()
        if o is None:
            return Response({"type": "not_found", "detail": "Not your order."}, status=404)
        if kind == "invoice":
            inv = SalesInvoice.objects.filter(order=o).exclude(status="cancelled").order_by("-created_at").first()
            if inv is None:
                return Response({"type": "no_invoice", "detail": f"{o.number} has no invoice yet."}, status=404)
            # the office's own renderer: a tax invoice prints as one (title, columns, FBR number + QR) here too
            return invoice_pdf(request, inv)
        return order_pdf(request, o)


def _shift_body(shift, tenant=None):
    from apps.partners.shifts import ShiftService

    if shift is None:
        return None
    return {
        "id": str(shift.id), "client_id": shift.client_id or None, "status": shift.status,
        "started_at": shift.started_at, "ended_at": shift.ended_at, "end_reason": shift.end_reason or None,
        "start_gps": {"lat": float(shift.start_lat), "lng": float(shift.start_lng)} if shift.start_lat is not None else None,
        "breaks": [{"id": str(b.id), "started_at": b.started_at, "ended_at": b.ended_at} for b in shift.breaks.all()],
        "last_ping_at": shift.last_ping_at,
        "tracking_state": ShiftService.tracking_state(shift, tenant),
        "working_minutes": ShiftService.working_minutes(shift),
        "corrected": shift.corrected,
    }


class ExtShiftsView(ExtBase):
    """
    `GET /ext/v1/shifts/current` — the rep's open shift or null.
    `POST /ext/v1/shifts` — start one. The app verifies tracking can actually run before allowing the tap and
    sends those `checks` as evidence; a client `at` up to 12 h old replays a start made in a dead zone.
    """

    def get(self, request):
        from apps.partners.shifts import ShiftService

        from apps.partners.models import RepShift

        _supplier, rep = self.context(request)
        if rep is None:
            raise DomainError("Shifts belong to field logins.", code="rep_required", status_code=403)
        shift = ShiftService.current(rep)
        if shift is None:
            # today's ended shift, so the app can say "your last shift ended at 2:14 — and why"
            start_of_day = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
            shift = RepShift.objects.filter(rep=rep, status="ended", started_at__gte=start_of_day).order_by("-started_at").first()
        return Response({"shift": _shift_body(shift, get_current_tenant())})

    def post(self, request):
        from apps.partners.shifts import ShiftService

        _supplier, rep = self.context(request)
        if rep is None:
            raise DomainError("Shifts belong to field logins.", code="rep_required", status_code=403)
        d = request.data if isinstance(request.data, dict) else {}
        gps = d.get("gps") or {}
        if gps.get("mocked") is True:
            raise DomainError("This position comes from a fake-GPS app — switch it off to start your shift.", code="mocked_fix", status_code=400)
        shift = ShiftService.start(
            rep=rep, user=request.user if getattr(request.user, "pk", None) else None,
            client_id=str(d.get("client_id") or request.headers.get("Idempotency-Key") or "")[:64],
            at=d.get("at"), gps=gps, device=d.get("device") or {}, checks=d.get("checks") or {},
        )
        return Response({"shift": _shift_body(shift, get_current_tenant())}, status=201)


class ExtShiftActionView(ExtBase):
    """`POST /ext/v1/shifts/{id}/break|resume|end` — all idempotent: repeating a transition returns the shift."""

    def post(self, request, pk, action):
        from apps.partners.models import RepShift
        from apps.partners.shifts import ShiftService

        _supplier, rep = self.context(request)
        shift = None
        if rep is not None:
            # a shift started in a dead zone has no server id yet — the app names it, and that name works here
            shift = RepShift.objects.filter(rep=rep).filter(Q(client_id=pk) | (Q(pk=pk) if _is_uuid(pk) else Q(pk=None))).order_by("-started_at").first()
        if shift is None:
            raise DomainError("Shift not found.", status_code=404)
        d = request.data if isinstance(request.data, dict) else {}
        if action == "break":
            shift = ShiftService.break_(shift, at=d.get("at"), gps=d.get("gps"))
        elif action == "resume":
            shift = ShiftService.resume(shift, at=d.get("at"), gps=d.get("gps"))
        else:
            reason = d.get("reason") if d.get("reason") in dict(RepShift.END_REASONS) else "rep"
            shift = ShiftService.end(shift, at=d.get("at"), gps=d.get("gps"), reason=reason)
        return Response({"shift": _shift_body(shift, get_current_tenant())})


class ExtShopOrdersView(ExtBase):
    """
    `GET /ext/v1/customers/{pk}/orders?limit=10` — a shop's history for the rep who has it NOW, whoever took
    the orders (this rep, another rep, the office). A branch id answers with that branch's orders; the
    account's id answers for the whole account. Rows the rep did not take are visible but not openable.
    """

    def get(self, request, pk):
        self.perm(request, "external.view_own_orders")
        _supplier, rep = self.context(request)
        customer, location = self.resolve_shop(request, pk)
        if customer is None:
            raise DomainError("That shop is not assigned to you.", status_code=404)
        qs = SalesOrder.objects.filter(customer=customer).select_related("rep", "trip", "location")
        if location is not None:
            qs = qs.filter(location=location)
        limit = min(int(request.query_params.get("limit", 10)), 50)
        show_prices = has_tenant_perm(request, "external.view_prices") or has_tenant_perm(request, "catalog.view_product")
        rows = []
        for o in qs.order_by("-created_at")[:limit]:
            row = SalesOrderListSerializer(o).data
            # the sale person is auto-stamped on office orders too, so WHO TOOK IT keys off the source
            if o.source != "rep_app":
                row["taken_by"] = {"kind": "office", "name": "the office"}
            elif o.rep_id and rep is not None and o.rep_id == rep.pk:
                row["taken_by"] = {"kind": "self", "name": o.rep.name}
            else:
                row["taken_by"] = {"kind": "rep", "name": o.rep.name if o.rep_id else "another rep"}
            row["can_open"] = bool(rep is not None and o.rep_id == rep.pk)
            if not show_prices:
                row["total"] = None
            rows.append(row)
        return Response({"results": rows})
