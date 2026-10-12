from decimal import Decimal

from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.accounts.page_permissions import extra_perm, page_perms
from apps.core.exceptions import DomainError
from apps.core.permissions import IsTenantUser, has_tenant_perm, require_perm
from apps.core.viewsets import DocumentViewSet, TenantModelViewSet, warehouse_scope
from apps.sales.models import SalesOrder

from .models import DeliveryNote, DeliveryTrip, Driver, PickList, Vehicle, VehicleLog
from .serializers import DeliveryNoteSerializer, DeliveryTripListSerializer, DeliveryTripSerializer, DriverSerializer, PickListSerializer, VehicleLogSerializer, VehicleSerializer
from .services import DeliveryService, PickListService, TripService


def _money(value):
    """Group thousands. A driver reads this sheet on paper in a van, where 105120.9484 is easily misread."""
    return f"{Decimal(value or 0):,.2f}"


def _qty(value):
    """Quantities are stored with three decimals; a driver counting cartons wants "39", not "39.000"."""
    q = Decimal(value or 0)
    return f"{q.to_integral_value():f}" if q == q.to_integral_value() else f"{q.normalize():f}"


def _logo_url(tenant):
    from apps.core.documents import logo_url

    return logo_url(tenant)


def dispatch_sheet_context(trip, tenant):
    """
    Everything the dispatch sheet template prints, for a trip.

    Stops in delivery sequence with the customer, phone, address and what to drop, the order value and
    whether it is a cash or credit customer. The totals and the cash due are worked out here rather than in
    the template because the trip does not store them. Kept separate from the PDF response so the document's
    content can be asserted without going through the renderer.
    """
    stops, qty_total, line_count, orders_value, cash_due = [], Decimal("0"), 0, Decimal("0"), Decimal("0")
    for note in trip.delivery_notes.all():
        lines = list(note.lines.all())
        loaded = sum((line.qty_loaded for line in lines), Decimal("0"))
        credit_days = note.customer.credit_days or 0
        order_total = note.order.total or Decimal("0")
        # reverse one-to-one: an uninvoiced note raises AttributeError here, so getattr gives None
        invoice = getattr(note, "invoice", None)
        stops.append({
            "note": note,
            "lines": [{"variant": line.variant, "batch": line.batch, "loaded": _qty(line.qty_loaded),
                       # blank on a sheet that is still going out, so the driver can write the count in
                       "delivered": _qty(line.qty_delivered) if note.delivered_at else "",
                       "returned": _qty(line.qty_returned) if note.delivered_at else ""} for line in lines],
            "terms": "cash on delivery" if not credit_days else f"{credit_days}-day credit",
            "order_total": _money(order_total),
            # "" rather than "0.00" so the template can leave the line out for a credit stop
            "cash_collected": _money(note.cash_collected) if note.cash_collected else "",
            "invoice_number": invoice.number if invoice else "",
            "qty_loaded": _qty(loaded),
            "qty_delivered": _qty(sum((line.qty_delivered for line in lines), Decimal("0"))) if note.delivered_at else "",
            "qty_returned": _qty(sum((line.qty_returned for line in lines), Decimal("0"))) if note.delivered_at else "",
        })
        qty_total += loaded
        line_count += len(lines)
        orders_value += order_total
        if not credit_days:
            cash_due += order_total

    return {
        "trip": trip, "tenant": tenant, "logo_url": _logo_url(tenant),
        "stops": stops, "qty_total": _qty(qty_total), "line_count": line_count,
        "orders_value": _money(orders_value), "cash_due": _money(cash_due) if cash_due else "",
        "fuel_amount": _money(trip.fuel_amount), "expense_amount": _money(trip.expense_amount),
        "cash_collected": _money(trip.cash_collected),
    }


def _dispatch_sheet_pdf(request, trip):
    """The dispatch sheet as a PDF: the paper the driver carries out of the warehouse."""
    from django.template.loader import render_to_string
    from weasyprint import HTML

    html = render_to_string("delivery/dispatch_sheet.html", dispatch_sheet_context(trip, request.tenant))
    resp = HttpResponse(HTML(string=html).write_pdf(), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{trip.number}-dispatch-sheet.pdf"'
    return resp


def _driver_scope(request, qs, field="driver"):
    """Drivers with a login only see their own trips."""
    m = getattr(request, "membership", None)
    if m is None or m.is_owner or m.has_perm("delivery.view_all_trips"):
        return qs
    drivers = Driver.objects.filter(user=request.user).values_list("id", flat=True)
    if drivers:
        return qs.filter(**{f"{field}__in": list(drivers)})
    if m.supplier_rep_id:
        return qs.filter(**{f"{'rep' if field == 'driver' else field.replace('driver', 'rep')}": m.supplier_rep_id})
    return qs


class VehicleViewSet(TenantModelViewSet):
    serializer_class = VehicleSerializer
    perm_map = page_perms("vehicles", {"logs": "delivery.view_vehicle"})
    search_fields = ["reg_no", "make_model"]
    filterset_fields = ["is_active", "owner_type", "type"]
    pagination_class = None

    def get_queryset(self):
        return Vehicle.objects.annotate(trips_count=Count("trips"))

    @action(detail=True, methods=["get"])
    def logs(self, request, pk=None):
        return Response(VehicleLogSerializer(VehicleLog.objects.filter(vehicle_id=pk).select_related("trip")[:200], many=True).data)


class DriverViewSet(TenantModelViewSet):
    serializer_class = DriverSerializer
    perm_map = page_perms("drivers")
    search_fields = ["name", "phone", "cnic", "license_no"]
    filterset_fields = ["is_active", "default_vehicle"]
    pagination_class = None

    def get_queryset(self):
        return Driver.objects.select_related("default_vehicle", "user").annotate(trips_count=Count("trips"))


class PickListViewSet(DocumentViewSet):
    serializer_class = PickListSerializer
    perm_map = page_perms("load_forms", {
        "picks": "delivery.change_load_form",
        "scans": "delivery.change_load_form",
        "send": "delivery.change_load_form",
        "issue": extra_perm("load_forms", "issue"),
        "complete": "delivery.change_load_form",
        "create_dispatch": "delivery.add_trip",
        "pdf": "delivery.view_load_form",
    })
    filterset_fields = ["status", "warehouse", "picker"]
    search_fields = ["number"]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        return warehouse_scope(self.request, PickList.objects.select_related("warehouse", "picker").prefetch_related("orders", "lines__variant", "lines__batch", "lines__bin", "lines__order_line__order"))

    def perform_create(self, serializer):
        d = serializer.validated_data
        serializer.instance = PickListService.create(warehouse=d["warehouse"], orders=list(d.get("orders", [])), notes=d.get("notes", ""))

    @action(detail=True, methods=["post"])
    def picks(self, request, pk=None):
        return Response(PickListSerializer(PickListService.record_picks(self.get_object(), request.data.get("picks", [])), context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def scans(self, request, pk=None):
        return Response(PickListSerializer(PickListService.scans(self.get_object(), request.data.get("scans", [])), context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        """Hand the load form over to the warehouse."""
        return Response(PickListSerializer(PickListService.send(self.get_object()), context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        """Warehouse issue: record the quantities handed over, drop the stock and open the delivery note."""
        pl = PickListService.issue(self.get_object(), request.data.get("issues") or request.data.get("picks"), allow_short=request.data.get("allow_short", True))
        return Response(PickListSerializer(pl, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        return Response(PickListSerializer(PickListService.complete(self.get_object()), context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="create-dispatch")
    def create_dispatch(self, request, pk=None):
        """Loader form is packed → hand it over as a dispatch (trip) with driver, vehicle, sales rep and delivery rep."""
        from apps.accounts.models import User
        from apps.delivery.models import Driver, Vehicle
        from apps.partners.models import Route, SupplierRep

        pl = self.get_object()
        d = request.data
        trip = TripService.from_picklist(
            pl,
            driver=Driver.objects.filter(pk=d.get("driver")).first(),
            vehicle=Vehicle.objects.filter(pk=d.get("vehicle")).first(),
            rep=SupplierRep.objects.filter(pk=d.get("rep")).first(),
            delivery_rep=User.objects.filter(pk=d.get("delivery_rep"), memberships__tenant=request.tenant).first(),
            loader=User.objects.filter(pk=d.get("loader"), memberships__tenant=request.tenant).first(),
            helper=User.objects.filter(pk=d.get("helper"), memberships__tenant=request.tenant).first(),
            route=Route.objects.filter(pk=d.get("route")).first(),
            date=d.get("date") or None,
            notes=d.get("notes", ""),
        )
        return Response(DeliveryTripSerializer(trip).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        from django.template.loader import render_to_string
        from weasyprint import HTML

        pl = self.get_object()
        html = render_to_string("delivery/picklist.html", {"pl": pl, "tenant": request.tenant})
        resp = HttpResponse(HTML(string=html).write_pdf(), content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="{pl.number}.pdf"'
        return resp


class TripViewSet(DocumentViewSet):
    perm_map = page_perms("trips", {
        "assign_orders": "delivery.change_trip",
        "remove_order": "delivery.change_trip",
        "reorder_stops": "delivery.change_trip",
        "load": "delivery.change_trip",
        "start": extra_perm("trips", "start"),
        "close": extra_perm("trips", "start"),
        "cancel": "delivery.delete_trip",
        "reconciliation": "delivery.view_trip",
        "dispatch_sheet": "delivery.view_trip",
        "trip_sheet": "delivery.view_trip",
        "stats": "delivery.view_trip",
    })
    filterset_fields = ["status", "warehouse", "driver", "vehicle", "rep", "date"]
    search_fields = ["number", "driver__name", "vehicle__reg_no", "delivery_notes__order__number", "delivery_notes__customer__name"]
    ordering_fields = ["date", "number", "created_at"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_serializer_class(self):
        return DeliveryTripListSerializer if self.action == "list" else DeliveryTripSerializer

    def get_queryset(self):
        qs = DeliveryTrip.objects.select_related("warehouse", "driver", "vehicle", "rep", "route", "closed_by")
        if self.action != "list":
            qs = qs.prefetch_related("delivery_notes__lines__variant", "delivery_notes__lines__batch", "delivery_notes__lines__order_line", "delivery_notes__order", "delivery_notes__customer", "delivery_notes__invoice")
        qs = _driver_scope(self.request, qs)
        qs = warehouse_scope(self.request, qs)
        p = self.request.query_params
        if p.get("date_from"):
            qs = qs.filter(date__gte=p["date_from"])
        if p.get("date_to"):
            qs = qs.filter(date__lte=p["date_to"])
        if p.get("active") == "1":
            qs = qs.filter(status__in=["planned", "loading", "out_for_delivery", "completed"])
        return qs.distinct()

    def perform_create(self, serializer):
        d = serializer.validated_data
        orders = list(SalesOrder.objects.filter(id__in=d.pop("order_ids", [])))
        serializer.instance = TripService.create(warehouse=d["warehouse"], date=d.get("date"), driver=d.get("driver"), vehicle=d.get("vehicle"), rep=d.get("rep"), delivery_rep=d.get("delivery_rep"), loader=d.get("loader"), helper=d.get("helper"), route=d.get("route"), orders=orders, notes=d.get("notes", ""), picklist=d.get("picklist"))

    def perform_update(self, serializer):
        trip = serializer.instance
        if trip.status not in ("planned", "loading"):
            raise DomainError("Trip can only be edited before it starts.")
        # who drives and in what is its own switch — editing the dispatch does not imply crewing it
        crew = {"driver", "vehicle", "helper", "delivery_rep", "loader"}
        changed = {f for f in crew if f in serializer.validated_data and getattr(trip, f"{f}_id") != getattr(serializer.validated_data.get(f), "pk", None)}
        if changed and not has_tenant_perm(self.request, extra_perm("trips", "assign_driver")):
            raise DomainError("You can edit this dispatch, but not change its driver, vehicle or crew.", code="forbidden", status_code=403)
        serializer.validated_data.pop("order_ids", None)
        super().perform_update(serializer)

    @action(detail=True, methods=["post"], url_path="assign-orders")
    def assign_orders(self, request, pk=None):
        orders = list(SalesOrder.objects.filter(id__in=request.data.get("order_ids", [])))
        return Response(DeliveryTripSerializer(TripService.assign_orders(self.get_object(), orders), context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="remove-order")
    def remove_order(self, request, pk=None):
        return Response(DeliveryTripSerializer(TripService.remove_order(self.get_object(), SalesOrder.objects.get(pk=request.data["order_id"])), context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="reorder-stops")
    def reorder_stops(self, request, pk=None):
        return Response(DeliveryTripSerializer(TripService.reorder_stops(self.get_object(), request.data.get("note_ids", [])), context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def load(self, request, pk=None):
        return Response(DeliveryTripSerializer(TripService.load(self.get_object(), request.data.get("loaded")), context={"request": request}).data)

    @action(detail=True, methods=["post"])
    @require_perm("delivery.start_trip")
    def start(self, request, pk=None):
        return Response(DeliveryTripSerializer(TripService.start(self.get_object(), request.data.get("odometer_start")), context={"request": request}).data)

    @action(detail=True, methods=["get"])
    def reconciliation(self, request, pk=None):
        return Response(TripService.reconciliation(self.get_object()))

    @action(detail=True, methods=["post"])
    @require_perm("delivery.start_trip")
    def close(self, request, pk=None):
        m = request.membership
        override = bool(request.data.get("override")) and (m.has_perm("inventory.approve_adjustment") or m.is_owner)
        from apps.inventory.models import ReasonCode

        reason = ReasonCode.objects.filter(pk=request.data.get("damage_reason")).first() if request.data.get("damage_reason") else None
        trip = TripService.close(self.get_object(), odometer_end=request.data.get("odometer_end"), fuel_amount=request.data.get("fuel_amount"), expense_amount=request.data.get("expense_amount"), override=override, damage_reason=reason)
        return Response(DeliveryTripSerializer(trip, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return Response(DeliveryTripSerializer(TripService.cancel(self.get_object(), request.data.get("reason", "")), context={"request": request}).data)

    @action(detail=True, methods=["get"], url_path="dispatch-sheet")
    def dispatch_sheet(self, request, pk=None):
        """The dispatch sheet the driver signs for and carries — printed and downloaded from the dispatch screen."""
        return _dispatch_sheet_pdf(request, self.get_object())

    @action(detail=True, methods=["get"], url_path="trip-sheet")
    def trip_sheet(self, request, pk=None):
        """Older name for the dispatch sheet, kept because the mobile app and docs/API.md both publish it."""
        return _dispatch_sheet_pdf(request, self.get_object())

    @action(detail=False, methods=["get"])
    def stats(self, request):
        qs = self.get_queryset()
        today = timezone.localdate()
        return Response({"today": qs.filter(date=today).count(), "out_for_delivery": qs.filter(status="out_for_delivery").count(), "planned": qs.filter(status__in=["planned", "loading"]).count(), "to_close": qs.filter(status="completed").count(), "cash_on_trips": str(qs.filter(status__in=["out_for_delivery", "completed"]).aggregate(s=__import__("django.db.models", fromlist=["Sum"]).Sum("cash_collected"))["s"] or 0)})


class DeliveryNoteViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsTenantUser]
    serializer_class = DeliveryNoteSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    perm_map = page_perms("delivery_notes", {
        "deliver": extra_perm("delivery_notes", "complete"),
        "pdf": "delivery.view_delivery_note",
    })
    filterset_fields = ["status", "trip", "order", "customer", "warehouse"]
    search_fields = ["number", "order__number", "customer__name"]
    ordering_fields = ["created_at", "sequence"]

    def get_queryset(self):
        qs = DeliveryNote.objects.select_related("trip", "order", "customer", "warehouse", "delivered_by").prefetch_related("lines__variant", "lines__batch", "lines__order_line")
        qs = _driver_scope(self.request, qs, field="trip__driver")
        qs = warehouse_scope(self.request, qs)
        m = getattr(self.request, "membership", None)
        if m is not None and m.supplier_rep_id:
            qs = qs.filter(order__rep_id=m.supplier_rep_id)
        return qs.distinct()

    @action(detail=True, methods=["post"])
    @require_perm("delivery.complete_delivery")
    def deliver(self, request, pk=None):
        """
        Multipart or JSON: {delivered:[{line_id, qty_delivered, qty_returned, return_reason, return_condition}], receiver_name, gps:{lat,lng},
        cash_collected, signature (file), photo (file), failed, failure_reason}
        """
        import json

        dn = self.get_object()
        data = request.data
        delivered = data.get("delivered")
        if isinstance(delivered, str):
            delivered = json.loads(delivered)
        gps = data.get("gps")
        if isinstance(gps, str):
            gps = json.loads(gps) if gps else None
        if gps is None and data.get("gps_lat"):
            gps = {"lat": data.get("gps_lat"), "lng": data.get("gps_lng")}
        dn = DeliveryService.deliver(
            dn, delivered=delivered, receiver_name=data.get("receiver_name", ""), signature_file=request.FILES.get("signature"), photo_file=request.FILES.get("photo"), gps=gps,
            cash_collected=Decimal(str(data.get("cash_collected", 0) or 0)), delivered_at=data.get("delivered_at") or None, failure_reason=data.get("failure_reason", ""), failed=str(data.get("failed", "")).lower() in ("1", "true", "yes"), remarks=data.get("remarks", ""),
        )
        return Response(DeliveryNoteSerializer(dn, context={"request": request}).data)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        from django.template.loader import render_to_string
        from weasyprint import HTML

        dn = self.get_object()
        html = render_to_string("delivery/challan.html", {"dn": dn, "tenant": request.tenant})
        resp = HttpResponse(HTML(string=html).write_pdf(), content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="{dn.number}.pdf"'
        return resp
