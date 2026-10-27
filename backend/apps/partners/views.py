from decimal import Decimal

from django.db.models import Count, F, Q, Sum
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.page_permissions import extra_perm, page_perms
from apps.core.audit import AuditService
from apps.core.exceptions import DomainError
from apps.core.numbering import next_number
from apps.core.permissions import has_tenant_perm, require_perm
from apps.core.viewsets import TenantModelViewSet

from .geofence import GeoFenceService, area_km2, coords, distance_to
from .models import Customer, GeoFence, GeoFenceEvent, GeoFenceRep, Route, Supplier, SupplierRep, Territory
from .serializers import CustomerSerializer, GeoFenceEventSerializer, GeoFenceSerializer, RouteSerializer, SupplierRepSerializer, SupplierSerializer, TerritorySerializer
from .services import CustomerService, PartnerLedgerService, PartnerOpeningBalance, SupplierService


class TerritoryViewSet(TenantModelViewSet):
    serializer_class = TerritorySerializer
    perm_map = page_perms("territories")
    pagination_class = None
    search_fields = ["name"]

    def get_queryset(self):
        return Territory.objects.select_related("parent").annotate(customers_count=Count("customers"))


class RouteViewSet(TenantModelViewSet):
    serializer_class = RouteSerializer
    perm_map = page_perms("territories")
    pagination_class = None
    search_fields = ["name"]
    filterset_fields = ["territory", "day_of_week", "is_active"]

    def get_queryset(self):
        return Route.objects.select_related("territory").annotate(customers_count=Count("customers"))


class SupplierViewSet(TenantModelViewSet):
    serializer_class = SupplierSerializer
    perm_map = page_perms("suppliers", {
        "reps": "suppliers.view_supplier_rep",        # the supplier's reps tab belongs to the Supplier reps page
        "ledger": extra_perm("suppliers", "ledger"),
        "bills": extra_perm("suppliers", "ledger"),
    })
    search_fields = ["name", "business_name", "code", "phone", "alt_mobile", "landline", "email", "contact_person", "city", "ntn", "strn", "cnic"]
    filterset_fields = ["is_active", "city"]
    ordering_fields = ["name", "code", "created_at"]

    def get_queryset(self):
        return Supplier.objects.select_related("tax_class_override").annotate(reps_count=Count("reps", distinct=True), products_count=Count("products", distinct=True))

    def perform_create(self, serializer):
        from apps.core.plan_limits import PlanLimitService

        PlanLimitService.check(self.request.tenant, "max_suppliers")
        if not serializer.validated_data.get("code"):
            serializer.validated_data["code"] = next_number("supplier", unique_for=(Supplier, "code"))
        opening = serializer.validated_data.pop("opening_balance", None)
        opening_date = serializer.validated_data.pop("opening_balance_date", None)
        super().perform_create(serializer)
        if opening:
            PartnerOpeningBalance.set(serializer.instance, opening, as_of=opening_date, actor=self.request.user)

    def perform_update(self, serializer):
        """An opening balance typed here is posted to the ledger, not just stored."""
        opening = serializer.validated_data.pop("opening_balance", None)
        opening_date = serializer.validated_data.pop("opening_balance_date", None)
        super().perform_update(serializer)
        obj = serializer.instance
        if opening is not None and (Decimal(str(opening)) != obj.opening_balance or (opening_date and opening_date != obj.opening_balance_date)):
            PartnerOpeningBalance.set(obj, opening, as_of=opening_date, actor=self.request.user)

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=["is_active"])

    @action(detail=True, methods=["get", "post"])
    def reps(self, request, pk=None):
        supplier = self.get_object()
        if request.method == "GET":
            return Response(SupplierRepSerializer(supplier.reps.select_related("role", "user"), many=True).data)
        require_perm("suppliers.add_supplier_rep")(lambda s, r: None)(self, request)
        s = SupplierRepSerializer(data={**request.data, "supplier": str(supplier.id)})
        s.is_valid(raise_exception=True)
        d = dict(s.validated_data)
        d.pop("supplier", None)
        create_login = d.pop("create_login", False)
        password = d.pop("password", None)
        send_invite = d.pop("send_invite", True)
        rep = SupplierService.create_rep(supplier, create_login=create_login, password=password or None, send_invite=send_invite, **d)
        AuditService.log("rep.created", rep, after={"name": rep.name, "login": create_login}, request=request)
        return Response(SupplierRepSerializer(rep).data, status=201)

    @action(detail=True, methods=["get"])
    @require_perm("suppliers.view_supplier_ledger")
    def ledger(self, request, pk=None):
        return Response(PartnerLedgerService.ledger("supplier", pk, request.query_params.get("date_from"), request.query_params.get("date_to")))

    @action(detail=True, methods=["get"])
    def bills(self, request, pk=None):
        from apps.purchasing.models import PurchaseBill
        from apps.purchasing.serializers import PurchaseBillSerializer

        return Response(PurchaseBillSerializer(PurchaseBill.objects.filter(supplier_id=pk).order_by("-bill_date")[:200], many=True).data)


class SupplierRepViewSet(TenantModelViewSet):
    serializer_class = SupplierRepSerializer
    perm_map = page_perms("supplier_reps", {"performance": "suppliers.view_supplier_rep", "ledger": extra_perm("suppliers", "ledger")})
    search_fields = ["name", "code", "phone", "email"]
    filterset_fields = ["supplier", "is_active", "role"]
    queryset = SupplierRep.objects.select_related("supplier", "role", "user")

    def get_queryset(self):
        """
        `?has_login=false` narrows this to reps nobody signs in as.

        One rep record backs one login, so the user screen's rep picker could otherwise offer a rep that is
        already taken — a choice whose only possible outcome is "tenant membership with this supplier rep
        already exists". Better not to offer it.
        """
        qs = super().get_queryset()
        has_login = (self.request.query_params.get("has_login") or "").lower()
        if has_login in ("false", "0"):
            return qs.filter(user__isnull=True)
        if has_login in ("true", "1"):
            return qs.filter(user__isnull=False)
        return qs

    def perform_update(self, serializer):
        d = dict(serializer.validated_data)
        for k in ("create_login", "password", "send_invite", "supplier"):
            d.pop(k, None)
        create_login = serializer.validated_data.get("create_login")
        rep = SupplierService.update_rep(serializer.instance, **d)
        if create_login and rep.user_id is None:
            from apps.accounts.services import UserService

            membership = UserService.create_tenant_user(rep.tenant, rep.name, email=rep.email or None, phone=rep.phone or None, password=serializer.validated_data.get("password") or None, roles=[rep.role] if rep.role else [], user_type="EXTERNAL", send_invite=serializer.validated_data.get("send_invite", True), supplier_rep=rep)
            rep.user = membership.user
            rep.save(update_fields=["user"])
        serializer.instance = rep

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=["is_active"])
        if instance.user_id:
            from apps.accounts.models import TenantMembership

            TenantMembership.objects.filter(user=instance.user, tenant=instance.tenant).update(status="suspended")

    @action(detail=True, methods=["get"])
    def performance(self, request, pk=None):
        return Response(SupplierService.performance(self.get_object(), request.query_params.get("date_from"), request.query_params.get("date_to")))

    @action(detail=True, methods=["get"])
    def ledger(self, request, pk=None):
        return Response(PartnerLedgerService.ledger("rep", pk, request.query_params.get("date_from"), request.query_params.get("date_to")))


class CustomerViewSet(TenantModelViewSet):
    serializer_class = CustomerSerializer
    perm_map = page_perms("customers", {
        "ledger": extra_perm("customers", "ledger"),
        "statement": extra_perm("customers", "ledger"),
        "block": extra_perm("customers", "block"),
        "unblock": extra_perm("customers", "block"),
        "approve": "customers.change_customer",
        "orders": "customers.view_customer",
        "credit_check": "customers.view_customer",
        "stats": "customers.view_customer",
        # the Store locations map has its own switches in the role matrix
        "map": "customers.view_store_location",
        "pin": "customers.change_store_location",
        # reading coordinates out of a pasted maps link gives nothing away — anyone who can see customers may use it
        "parse_location": "customers.view_customer",
    })
    search_fields = ["name", "business_name", "code", "phone", "alt_mobile", "landline", "email", "contact_person", "address", "city", "ntn", "strn", "cnic"]
    filterset_fields = ["type", "status", "territory", "route", "price_list", "is_blocked", "assigned_reps", "city"]
    ordering_fields = ["name", "code", "created_at", "credit_limit"]

    def get_queryset(self):
        qs = Customer.objects.select_related("territory", "route", "price_list", "tax_class_override").prefetch_related("assigned_reps")
        membership = getattr(self.request, "membership", None)
        if membership is not None and membership.supplier_rep_id:
            qs = qs.filter(assigned_reps=membership.supplier_rep_id)
        return qs

    def create(self, request, *args, **kwargs):
        """A rep adds a shop while standing in front of it, so the pin has to be inside the area they cover."""
        membership = getattr(request, "membership", None)
        rep = membership.supplier_rep if membership is not None and membership.supplier_rep_id else None
        if rep is not None:
            blocked = GeoFenceService.guard(rep=rep, gps={"lat": request.data.get("gps_lat"), "lng": request.data.get("gps_lng")}, action="customer")
            if blocked:
                return blocked
        return super().create(request, *args, **kwargs)

    @action(detail=False, methods=["get"])
    def map(self, request):
        """
        Every store as a pin — what the store-locations screen draws. Light rows on purpose: a pin needs a name,
        a code, where it is and who looks after it, not the whole customer record.
        """
        qs = self.get_queryset().prefetch_related("assigned_reps")
        p = request.query_params
        if p.get("search"):
            qs = qs.filter(Q(name__icontains=p["search"]) | Q(code__icontains=p["search"]) | Q(phone__icontains=p["search"]) | Q(address__icontains=p["search"]))
        if p.get("rep"):
            qs = qs.filter(assigned_reps=p["rep"])
        if p.get("route"):
            qs = qs.filter(route_id=p["route"])
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        rows, without_pin = [], []
        for c in qs[:4000]:
            row = {"id": str(c.id), "name": c.name, "code": c.code, "phone": c.phone, "address": c.address, "city": c.city,
                   "status": c.status, "route": c.route.name if c.route_id else "",
                   "reps": [{"id": str(r.id), "name": r.name} for r in c.assigned_reps.all()]}
            if c.gps_lat is None or c.gps_lng is None:
                without_pin.append(row)
            else:
                rows.append({**row, "lat": float(c.gps_lat), "lng": float(c.gps_lng)})
        # which areas cover which store, so the screen can colour the pins and filter by area
        fences = list(GeoFence.objects.filter(is_active=True).prefetch_related("reps"))
        inside = {}
        for f in fences:
            for c in GeoFenceService.customers_in(f):
                inside.setdefault(str(c.pk), []).append(str(f.pk))
        for r in rows:
            r["fences"] = inside.get(r["id"], [])
        return Response({"stores": rows, "without_pin": without_pin,
                         "fences": [{"id": str(f.id), "name": f.name, "colour": f.colour, "type": f.type,
                                     "centre_lat": f.centre_lat, "centre_lng": f.centre_lng, "radius_m": f.radius_m,
                                     "points": f.points, "reps": [{"id": str(r.id), "name": r.name} for r in f.reps.all()]} for f in fences]})

    @action(detail=False, methods=["post"], url_path="parse-location")
    def parse_location(self, request):
        """POST {text}: a Google Maps link (short links followed server-side) or "lat, lng" → {lat, lng}."""
        from .maplink import parse_location as parse

        found = parse(request.data.get("text") or request.data.get("url") or "")
        if found is None:
            raise DomainError("Could not read a location from that — paste a Google Maps link or \"lat, lng\".", code="bad_location", status_code=400)
        return Response({"lat": found[0], "lng": found[1]})

    @action(detail=True, methods=["post"], url_path="pin")
    def pin(self, request, pk=None):
        """Drop or move this store's pin: POST {lat, lng} — what dragging a pin on the map calls."""
        c = self.get_object()
        point = coords(request.data)
        if point is None:
            if request.data.get("clear"):
                c.gps_lat = c.gps_lng = None
                c.save(update_fields=["gps_lat", "gps_lng", "updated_at"])
                return Response({"id": str(c.id), "gps": None})
            raise DomainError("Give a lat and lng.", status_code=400)
        c.gps_lat, c.gps_lng = Decimal(str(round(point[0], 7))), Decimal(str(round(point[1], 7)))
        c.save(update_fields=["gps_lat", "gps_lng", "updated_at"])
        AuditService.log("pinned", c, after={"lat": str(c.gps_lat), "lng": str(c.gps_lng)})
        return Response({"id": str(c.id), "name": c.name, "gps": {"lat": float(c.gps_lat), "lng": float(c.gps_lng)}})

    def perform_create(self, serializer):
        d = dict(serializer.validated_data)
        reps = d.pop("assigned_reps", [])
        # this path builds the Customer itself (CustomerService, not serializer.save()), so the serializer's
        # own pop of the multi-shop rows never runs — they would reach Customer(**data) and 500
        location_rows = d.pop("locations_write", None)
        if "credit_limit" in d and d["credit_limit"] and not has_tenant_perm(self.request, extra_perm("customers", "credit_limit")):
            raise DomainError("You cannot set credit limits.")
        membership = getattr(self.request, "membership", None)
        rep = membership.supplier_rep if membership is not None and membership.supplier_rep_id else None
        c = CustomerService.create(d, rep=rep) if rep is not None else CustomerService.create(d)
        if location_rows is not None:
            serializer._save_locations(c, location_rows)
        c.assigned_reps.set(reps)
        serializer.instance = c
        AuditService.log("created", c, after={"code": c.code, "name": c.name}, request=self.request)

    def perform_update(self, serializer):
        inst = serializer.instance
        d = serializer.validated_data
        m = self.request.membership
        if not m.is_owner:
            if "credit_limit" in d and d["credit_limit"] != inst.credit_limit and not m.has_perm(extra_perm("customers", "credit_limit")):
                raise DomainError("You cannot change credit limits.")
            if ("is_blocked" in d and d["is_blocked"] != inst.is_blocked) and not m.has_perm(extra_perm("customers", "block")):
                raise DomainError("You cannot block/unblock customers.")
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        instance.status = "inactive"
        instance.save(update_fields=["status"])

    @action(detail=True, methods=["get"])
    @require_perm("customers.view_customer_ledger")
    def ledger(self, request, pk=None):
        return Response(PartnerLedgerService.ledger("customer", pk, request.query_params.get("date_from"), request.query_params.get("date_to")))

    @action(detail=True, methods=["get"])
    @require_perm("customers.view_customer_ledger")
    def statement(self, request, pk=None):
        c = self.get_object()
        from apps.sales.models import SalesInvoice
        from apps.sales.serializers import SalesInvoiceSerializer

        ledger = PartnerLedgerService.ledger("customer", pk, request.query_params.get("date_from"), request.query_params.get("date_to"))
        open_inv = SalesInvoice.objects.filter(customer=c, status__in=["posted", "partially_paid", "overdue"]).order_by("date")
        data = {"customer": CustomerSerializer(c).data, "ledger": ledger, "ageing": CustomerService.ageing(c), "open_invoices": SalesInvoiceSerializer(open_inv, many=True).data, "outstanding": str(CustomerService.outstanding(c))}
        if request.query_params.get("format") == "pdf":
            from django.http import HttpResponse
            from django.template.loader import render_to_string
            from weasyprint import HTML

            html = render_to_string("partners/statement.html", {"d": data, "tenant": request.tenant, "ageing_rows": list(data["ageing"].items())})
            resp = HttpResponse(HTML(string=html).write_pdf(), content_type="application/pdf")
            resp["Content-Disposition"] = f'inline; filename="statement-{c.code}.pdf"'
            return resp
        return Response(data)

    @action(detail=True, methods=["post"])
    @require_perm("customers.block_customer")
    def block(self, request, pk=None):
        c = self.get_object()
        c.is_blocked = True
        c.status = "blocked"
        c.blocked_reason = request.data.get("reason", "")
        c.save()
        AuditService.log("customer.blocked", c, after={"reason": c.blocked_reason}, request=request)
        return Response(CustomerSerializer(c).data)

    @action(detail=True, methods=["post"])
    @require_perm("customers.block_customer")
    def unblock(self, request, pk=None):
        c = self.get_object()
        c.is_blocked = False
        c.status = "active"
        c.blocked_reason = ""
        c.save()
        AuditService.log("customer.unblocked", c, request=request)
        return Response(CustomerSerializer(c).data)

    @action(detail=True, methods=["post"])
    @require_perm("customers.change_customer")
    def approve(self, request, pk=None):
        c = self.get_object()
        if c.status != "pending":
            raise DomainError("Customer is not pending approval.")
        c.status = "active"
        c.save(update_fields=["status"])
        AuditService.log("customer.approved", c, request=request)
        return Response(CustomerSerializer(c).data)

    @action(detail=True, methods=["get"])
    def orders(self, request, pk=None):
        from apps.sales.models import SalesOrder
        from apps.sales.serializers import SalesOrderListSerializer

        from apps.sales.views import with_location_check

        qs = with_location_check(SalesOrder.objects.filter(customer_id=pk).select_related("customer", "location", "rep", "supplier", "warehouse", "trip"))
        return Response(SalesOrderListSerializer(qs.order_by("-order_date")[:100], many=True).data)

    @action(detail=True, methods=["get"], url_path="credit-check")
    def credit_check(self, request, pk=None):
        from decimal import Decimal

        ok, reason = CustomerService.credit_check(self.get_object(), Decimal(request.query_params.get("amount", "0")))
        return Response({"ok": ok, "reason": reason, "policy": request.tenant.setting("credit_policy")})

    @action(detail=False, methods=["get"])
    def stats(self, request):
        qs = self.get_queryset()
        return Response({"total": qs.count(), "active": qs.filter(status="active").count(), "pending": qs.filter(status="pending").count(), "blocked": qs.filter(is_blocked=True).count()})


class GeoFenceViewSet(TenantModelViewSet):
    """
    Working areas: draw one, say who covers it, and see who falls inside.

    The shape is normalised on the way in (a traced outline is thinned, the bounding box is refreshed) so every
    later check is cheap, and assigning reps is a plain write to `reps`.
    """

    serializer_class = GeoFenceSerializer
    perm_map = page_perms("geofences", {"customers": "customers.view_geofence", "map": "customers.view_geofence",
                                        "events": "customers.view_geofence", "assign": "customers.assign_geofence",
                                        "search": "customers.view_geofence", "locate": "customers.view_geofence",
                                        "preview": "customers.view_geofence",
                                        "assign_customers": "customers.change_customer"})
    search_fields = ["name", "code", "place_name", "notes"]
    ordering_fields = ["name", "created_at"]
    filterset_fields = ["type", "is_active"]

    def get_queryset(self):
        qs = GeoFence.objects.prefetch_related("assignments__rep__supplier", "reps")
        p = self.request.query_params
        if p.get("rep"):
            qs = qs.filter(assignments__rep_id=p["rep"]).distinct()
        if p.get("active") == "1":
            qs = qs.filter(is_active=True)
        return qs

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        # counting who is inside costs a query per row; the list says no unless it is asked
        ctx["with_counts"] = self.action != "list" or self.request.query_params.get("with_counts") == "1"
        return ctx

    def perform_create(self, serializer):
        reps = serializer.validated_data.pop("reps", None)
        fence = serializer.save(created_by=self.request.user)
        GeoFenceService.normalise(fence)
        fence.save()
        self._set_reps(fence, reps)
        AuditService.log("created", fence, after={"type": fence.type, "reps": fence.reps.count()})

    def perform_update(self, serializer):
        reps = serializer.validated_data.pop("reps", None)
        fence = serializer.save()
        GeoFenceService.normalise(fence)
        fence.save()
        self._set_reps(fence, reps)

    def _set_reps(self, fence, reps):
        """The assignment carries who made it and when, so it is written here rather than through the plain M2M."""
        if reps is None:
            return
        wanted = {r.pk for r in reps}
        GeoFenceRep.objects.filter(fence=fence).exclude(rep_id__in=wanted).delete()
        for rep_id in wanted:
            GeoFenceRep.objects.get_or_create(fence=fence, rep_id=rep_id, defaults={"assigned_by": self.request.user})

    @action(detail=True, methods=["get"])
    def customers(self, request, pk=None):
        """Everyone whose pin falls inside this area."""
        fence = self.get_object()
        rows = GeoFenceService.customers_in(fence)
        return Response({"count": len(rows), "results": [
            {"id": str(c.id), "code": c.code, "name": c.name, "address": c.address, "city": c.city, "phone": c.phone,
             "gps": {"lat": float(c.gps_lat), "lng": float(c.gps_lng)}, "route": c.route.name if c.route_id else "",
             "territory": c.territory.name if c.territory_id else "",
             "reps": [{"id": str(r.id), "name": r.name} for r in c.assigned_reps.all()]} for c in rows]})

    @action(detail=True, methods=["post"], url_path="assign-customers")
    def assign_customers(self, request, pk=None):
        """
        Hand the stores in this area to a rep: POST {rep, customers:[id,…]} — or `{rep, all: true}` for every
        store inside it, which is the usual case. `remove: true` takes them off that rep again.
        """
        from .models import Customer

        fence = self.get_object()
        rep = SupplierRep.objects.filter(pk=request.data.get("rep")).first()
        if rep is None:
            raise DomainError("Pick the rep these stores are for.", status_code=400)
        inside = {str(c.pk): c for c in GeoFenceService.customers_in(fence)}
        wanted = inside if request.data.get("all") else {k: v for k, v in inside.items() if k in set(request.data.get("customers") or [])}
        if not wanted:
            raise DomainError("No stores in this area were selected.", status_code=400)
        for c in wanted.values():
            c.assigned_reps.remove(rep) if request.data.get("remove") else c.assigned_reps.add(rep)
        AuditService.log("assigned_customers", fence, after={"rep": rep.name, "stores": len(wanted), "removed": bool(request.data.get("remove"))})
        return Response({"rep": rep.name, "stores": len(wanted), "removed": bool(request.data.get("remove")),
                         "customers": [{"id": k, "name": v.name} for k, v in wanted.items()]})

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        """POST {reps:[id,…]} — hand this area to those reps (replaces the list), or {rep, paused_until} to pause one."""
        fence = self.get_object()
        if "reps" in request.data:
            wanted = set(request.data.get("reps") or [])
            GeoFenceRep.objects.filter(fence=fence).exclude(rep_id__in=wanted).delete()
            for rep_id in wanted:
                GeoFenceRep.objects.get_or_create(fence=fence, rep_id=rep_id, defaults={"assigned_by": request.user})
        elif request.data.get("rep"):
            GeoFenceRep.objects.filter(fence=fence, rep_id=request.data["rep"]).update(paused_until=request.data.get("paused_until") or None)
        AuditService.log("assigned", fence, after={"reps": list(fence.reps.values_list("name", flat=True))})
        return Response(GeoFenceSerializer(fence, context=self.get_serializer_context()).data)

    @action(detail=False, methods=["get"])
    def map(self, request):
        """Everything the coverage map needs in one call: the areas, the customer pins, and who is in none of them."""
        fences = list(self.get_queryset().filter(is_active=True))
        pins = list(Customer.objects.filter(gps_lat__isnull=False, gps_lng__isnull=False).select_related("route")[:3000])
        covered = set()
        for f in fences:
            for c in GeoFenceService.customers_in(f):
                covered.add(c.pk)
        return Response({
            "fences": GeoFenceSerializer(fences, many=True, context={**self.get_serializer_context(), "with_counts": False}).data,
            "customers": [{"id": str(c.id), "name": c.name, "code": c.code, "lat": float(c.gps_lat), "lng": float(c.gps_lng),
                           "covered": c.pk in covered, "route": c.route.name if c.route_id else ""} for c in pins],
            "without_pin": Customer.objects.filter(gps_lat__isnull=True, status="active").count(),
            "uncovered": sum(1 for c in pins if c.pk not in covered),
        })

    @action(detail=False, methods=["get"])
    def events(self, request):
        """The out-of-area trail: what was checked, where, and what was refused."""
        qs = GeoFenceEvent.objects.select_related("rep", "fence", "customer").all()
        p = request.query_params
        if p.get("rep"):
            qs = qs.filter(rep_id=p["rep"])
        if p.get("verdict"):
            qs = qs.filter(verdict=p["verdict"])
        if p.get("blocked") == "1":
            qs = qs.filter(blocked=True)
        if p.get("date_from"):
            qs = qs.filter(occurred_at__date__gte=p["date_from"])
        if p.get("date_to"):
            qs = qs.filter(occurred_at__date__lte=p["date_to"])
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(GeoFenceEventSerializer(page, many=True).data)

    @action(detail=False, methods=["post"])
    def preview(self, request):
        """
        Who would fall inside a shape that has not been saved yet — what the editor shows while you drag.
        Takes the same shape fields as a save, and returns the pins so the map can draw them.
        """
        shape = GeoFence(type=request.data.get("type", "circle"), centre_lat=request.data.get("centre_lat") or None,
                         centre_lng=request.data.get("centre_lng") or None, radius_m=request.data.get("radius_m") or 0,
                         points=request.data.get("points") or [])
        try:
            GeoFenceService.normalise(shape)
        except Exception:
            return Response({"count": 0, "customers": [], "area_km2": 0})
        rows = GeoFenceService.customers_in(shape)
        return Response({"count": len(rows), "area_km2": area_km2(shape),
                         "customers": [{"id": str(c.id), "name": c.name, "code": c.code, "lat": float(c.gps_lat), "lng": float(c.gps_lng)} for c in rows[:500]]})

    @action(detail=False, methods=["get"])
    def locate(self, request):
        """?rep=&lat=&lng= — is that rep inside their area at that point? Used by the editor and by a phone."""
        rep = SupplierRep.objects.filter(pk=request.query_params.get("rep")).first()
        point = coords({"lat": request.query_params.get("lat"), "lng": request.query_params.get("lng")})
        if point is None:
            raise DomainError("Give a lat and lng.", status_code=400)
        where = GeoFenceService.locate(rep, point)
        return Response({"inside": where["inside"], "areas": where["fences"], "distance_m": where["distance_m"],
                         "fence": {"id": str(where["fence"].id), "name": where["fence"].name} if where["fence"] else None})

    @action(detail=False, methods=["get"])
    def search(self, request):
        """
        ?q=Clifton Karachi — look the place up on OpenStreetMap and hand back what it knows: a centre always, and
        an outline when the place has a published boundary. Nothing is saved until the fence itself is saved.
        """
        from .places import search_places

        return Response({"results": search_places(request.query_params.get("q", ""))})
