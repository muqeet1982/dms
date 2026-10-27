from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.page_permissions import extra_perm, page_perms
from apps.core.audit import AuditService
from apps.core.documents import render_doc
from apps.core.exceptions import DomainError
from apps.core.permissions import IsTenantUser, require_perm
from apps.core.viewsets import DocumentViewSet, ExportableMixin, TenantModelViewSet, warehouse_scope

from .models import CompanyClaim, CreditNote, DiscountContribution, RepCollection, RepSettlement, SalesInvoice, SalesOrder
from .serializers import CompanyClaimSerializer, CreditNoteSerializer, DiscountContributionSerializer, SalesInvoiceEditSerializer, RepCollectionSerializer, RepSettlementSerializer, SalesInvoiceSerializer, SalesOrderListSerializer, SalesOrderSerializer
from .services import CompanyClaimService, CreditNoteService, InvoiceService, OrderService, RepCollectionService



def _rep_scope(request, qs, field="rep"):
    m = getattr(request, "membership", None)
    if m is not None and m.supplier_rep_id:
        return qs.filter(**{field: m.supplier_rep_id})
    return qs


class SalesOrderViewSet(DocumentViewSet):
    export_fields = ["number", "order_date", "customer_code", "customer_name", "rep_name", "warehouse_name",
                     "status", "lines_count", "subtotal", "discount", "tax1_total", "total", "requested_delivery_date", "external_ref"]
    perm_map = page_perms(
        "sales_orders",
        {
            "inbox": "sales.view_order_inbox",
            "stats": "sales.view_sales_order",
            "submit": "sales.add_sales_order",
            "review": extra_perm("sales_inbox", "confirm"),
            "confirm": extra_perm("sales_orders", "confirm"),
            "hold": extra_perm("sales_orders", "confirm"),
            "reject": extra_perm("sales_orders", "hold"),
            "cancel": "sales.delete_sales_order",
            "allocate": extra_perm("sales_orders", "confirm"),
            "availability": "sales.view_sales_order",
            "mark_delivered": extra_perm("delivery_notes", "complete"),
            "workflow": "sales.view_sales_order",
            "load_form": "delivery.add_load_form",
            "invoice": "sales.add_invoice",
            "pdf": "sales.view_sales_order",
        },
        also={
            "list": ["external.view_own_orders"],
            "retrieve": ["external.view_own_orders"],
            "create": ["external.submit_order"],
            "cancel": ["external.cancel_own_order"],
            "stats": ["external.view_own_orders"],
        },
    )
    filterset_fields = ["status", "customer", "rep", "supplier", "source", "warehouse", "trip", "order_date"]
    search_fields = ["number", "customer__name", "customer__code", "external_ref", "rep__name", "notes"]
    ordering_fields = ["order_date", "number", "total", "created_at", "status"]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_serializer_class(self):
        return SalesOrderListSerializer if self.action == "list" else SalesOrderSerializer

    def get_queryset(self):
        qs = SalesOrder.objects.select_related("customer", "rep", "supplier", "warehouse", "trip", "trip__driver", "trip__vehicle", "confirmed_by", "created_by")
        # where the order was taken, as the location check recorded it (owner, 27 Oct 2026: "when order is not
        # created from location then mention it") — one subquery, so the list stays a single query
        from django.db.models import CharField, OuterRef, Subquery
        from django.db.models.functions import Cast

        from apps.partners.models import GeoFenceEvent

        ev = GeoFenceEvent.objects.filter(action="order", document_id=Cast(OuterRef("pk"), output_field=CharField())).order_by("-occurred_at")
        qs = qs.annotate(loc_verdict=Subquery(ev.values("verdict")[:1]), loc_distance=Subquery(ev.values("distance_m")[:1]))
        if self.action != "list":
            qs = qs.prefetch_related("lines__variant__product__base_uom", "lines__packaging", "lines__tax1_type", "lines__tax2_type", "invoices", "delivery_notes__trip")
        qs = _rep_scope(self.request, qs)
        qs = warehouse_scope(self.request, qs)
        p = self.request.query_params
        if p.get("date_from"):
            qs = qs.filter(order_date__gte=p["date_from"])
        if p.get("date_to"):
            qs = qs.filter(order_date__lte=p["date_to"])
        if p.get("open") == "1":
            qs = qs.filter(status__in=SalesOrder.OPEN_STATUSES)
        if p.get("status__in"):
            qs = qs.filter(status__in=[s.strip() for s in p["status__in"].split(",") if s.strip()])
        if p.get("uninvoiced") == "1":
            # the New Invoice picker: an order appears only while it still has an issued delivery nobody billed;
            # once every delivery is invoiced it drops out, so an invoice cannot be issued twice
            qs = qs.filter(delivery_notes__stock_out_done=True, delivery_notes__invoice__isnull=True).exclude(status__in=["cancelled", "rejected"]).distinct()
        if p.get("updated_after"):
            qs = qs.filter(updated_at__gt=p["updated_after"])
        return qs

    def _lines(self, lines):
        return [{"variant": l["variant"], "packaging": l.get("packaging"), "qty": l.get("qty") or 0, "unit_price": l.get("unit_price"), "discount_pct": l.get("discount_pct", 0), "product_discount_pct": l.get("product_discount_pct"), "price_per_packaging": l.get("price_per_packaging", False)} for l in lines]

    def perform_create(self, serializer):
        d = serializer.validated_data
        m = self.request.membership
        rep = m.supplier_rep if m.supplier_rep_id else d.get("rep")
        source = "rep_app" if m.supplier_rep_id else d.get("source", "staff")
        can_override = m.has_perm("sales.override_price") or m.is_owner
        if m.supplier_rep_id and not d["customer"].assigned_reps.filter(pk=rep.pk).exists():
            raise DomainError("Customer is not assigned to you.", status_code=403)
        serializer.instance = OrderService.create(
            customer=d["customer"], lines=self._lines(d["lines"]), source=source, rep=rep, supplier=d.get("supplier"), warehouse=d.get("warehouse"), order_date=d.get("order_date"), requested_delivery_date=d.get("requested_delivery_date"),
            notes=d.get("notes", ""), external_ref=d.get("external_ref", ""), price_list=d.get("price_list"), allow_override=can_override, apply_customer_discount=d.get("apply_customer_discount", True), discount_pct=d.get("discount_pct"), charge_tax=d.get("charge_tax"), auto_confirm=bool(self.request.data.get("confirm")) and m.has_perm("sales.confirm_sales_order"),
        )

    def perform_update(self, serializer):
        order = serializer.instance
        m = self.request.membership
        if not order.is_editable:
            raise DomainError(f"Order in status {order.status} cannot be edited.")
        # the owner's rule (21 Oct 2026): past confirmation, changing an order is the owner's — and an edit
        # of issued/dispatched work pulls the goods back and cancels the paperwork before it applies
        if order.status in ("confirmed", "issued", "dispatched") and not (m.is_owner or m.has_perm("sales.edit_confirmed_order")):
            raise DomainError("Only the owner can change an order after it is confirmed.", status_code=403)
        d = serializer.validated_data
        lines = d.pop("lines", None)
        can_override = m.has_perm("sales.override_price") or m.is_owner
        if "discount_pct" in str(self.request.data) and not (m.has_perm("sales.override_discount") or m.is_owner):
            pass
        super().perform_update(serializer)
        if lines is not None:
            OrderService.update_lines(order, self._lines(lines), allow_override=can_override)
        else:
            OrderService.recompute(order, allow_override=can_override)

    # -------------------------------------------------------------- inbox
    @action(detail=False, methods=["get"])
    def inbox(self, request):
        qs = self.filter_queryset(self.get_queryset().filter(status__in=["submitted", "under_review", "on_hold_credit", "on_hold_stock"]).order_by("created_at"))
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(SalesOrderListSerializer(page, many=True).data)

    @action(detail=False, methods=["get"])
    def stats(self, request):
        qs = self.get_queryset()
        today = timezone.localdate()
        counts = dict(qs.values_list("status").annotate(c=Count("id")).values_list("status", "c"))
        return Response({"by_status": counts, "today": qs.filter(order_date=today).count(), "today_value": str(qs.filter(order_date=today).exclude(status__in=["cancelled", "rejected"]).aggregate(s=Sum("total"))["s"] or 0), "inbox": sum(counts.get(s, 0) for s in ("submitted", "under_review", "on_hold_credit", "on_hold_stock")), "dispatched": counts.get("dispatched", 0) + counts.get("partially_delivered", 0)})

    # ------------------------------------------------------------ actions
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        return Response(SalesOrderSerializer(OrderService.submit(self.get_object())).data)

    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        return Response(SalesOrderSerializer(OrderService.review(self.get_object())).data)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        m = request.membership
        override = bool(request.data.get("override_credit")) and (m.has_perm("sales.approve_credit_hold") or m.is_owner)
        return Response(SalesOrderSerializer(OrderService.confirm(self.get_object(), override_credit=override, allow_partial=bool(request.data.get("allow_partial")))).data)

    @action(detail=True, methods=["post"])
    def hold(self, request, pk=None):
        return Response(SalesOrderSerializer(OrderService.hold(self.get_object(), request.data.get("reason", ""))).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return Response(SalesOrderSerializer(OrderService.reject(self.get_object(), request.data.get("reason", ""))).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        m = request.membership
        by_rep = bool(m.supplier_rep_id)
        order = self.get_object()
        if not by_rep and not (m.has_perm("sales.cancel_sales_order") or m.is_owner):
            raise DomainError("You cannot cancel orders.", status_code=403)
        if by_rep and not m.has_perm("external.cancel_own_order"):
            raise DomainError("Your role cannot cancel orders.", status_code=403)
        # the owner's rule (21 Oct 2026): once the office has said yes, cancelling is the owner's call —
        # unless they hand out the "Cancel after confirmation" switch in the role matrix
        if not by_rep and order.status in ("confirmed", "on_hold_credit", "on_hold_stock", "issued", "dispatched") \
                and not (m.is_owner or m.has_perm("sales.cancel_confirmed_order")):
            raise DomainError("Only the owner can cancel an order after it is confirmed.", status_code=403)
        return Response(SalesOrderSerializer(OrderService.cancel(order, request.data.get("reason", ""), by_rep=by_rep)).data)

    @action(detail=True, methods=["post"])
    def allocate(self, request, pk=None):
        return Response(SalesOrderSerializer(OrderService.allocate(self.get_object(), request.data.get("allocations", []))).data)

    @action(detail=True, methods=["get"])
    def availability(self, request, pk=None):
        ok, rows = OrderService.availability(self.get_object())
        return Response({"ok": ok, "rows": rows})

    @action(detail=True, methods=["post"], url_path="mark-delivered")
    def mark_delivered(self, request, pk=None):
        order = OrderService.mark_delivered(self.get_object(), lines=request.data.get("lines"), receiver_name=request.data.get("receiver_name", ""), cash_collected=Decimal(str(request.data.get("cash_collected", 0) or 0)), delivered_at=request.data.get("delivered_at"))
        order.refresh_from_db()
        return Response(SalesOrderSerializer(order).data)

    @action(detail=True, methods=["get"])
    def workflow(self, request, pk=None):
        """Sales order → load form → warehouse issue → invoice → dispatch → delivery, as it stands right now."""
        from .workflow import order_workflow

        return Response({"steps": order_workflow(self.get_object())})

    @action(detail=True, methods=["post"], url_path="load-form")
    def load_form(self, request, pk=None):
        """Raise the load form for this order — SAVED, not sent (owner, 27 Oct 2026: "i need to create load form and
        save it not auto created"): it opens for review and correction, and goes to the warehouse only when someone
        presses Send. `send: true` still hands it over in the same call, for callers that ask for it."""
        from apps.delivery.serializers import PickListSerializer
        from apps.delivery.services import PickListService

        order = self.get_object()
        if order.status != "confirmed":
            raise DomainError("Confirm the order before sending it to the warehouse.")
        if order.warehouse_id is None:
            raise DomainError("Set the warehouse on the order first.")
        existing = order.pick_lists.exclude(status="cancelled").first()
        if existing is not None:
            raise DomainError(f"Load form {existing.number} already covers this order.", code="duplicate", errors={"picklist": [str(existing.pk)]})
        pl = PickListService.create(warehouse=order.warehouse, orders=[order], notes=request.data.get("notes", ""))
        if request.data.get("send", False) in (True, "true", "1", 1):
            PickListService.send(pl)
        return Response(PickListSerializer(pl, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def invoice(self, request, pk=None):
        """Invoice what the warehouse issued (before the vehicle leaves). One invoice per delivery note."""
        from apps.delivery.models import DeliveryNote

        from .serializers import SalesInvoiceSerializer

        order = self.get_object()
        notes = [n for n in DeliveryNote.objects.filter(order=order, stock_out_done=True) if getattr(n, "invoice", None) is None]
        if not notes:
            raise DomainError("Nothing to invoice yet — the warehouse has to issue the stock first.")
        # `post: false` leaves them draft, which is the only state in which the figures can still be corrected
        should_post = request.data.get("post", True) not in (False, "false", "0", 0)
        pct = request.data.get("discount_pct")
        apply_tax = request.data.get("apply_tax")
        # the create page's switch wins when present (even unticked); absent, the ORDER's own switch decides
        charge = None if apply_tax is None else apply_tax in (True, "true", "1", 1)
        tax_type = None
        if charge and request.data.get("tax_type"):
            from apps.catalog.models import TaxType

            tax_type = TaxType.objects.filter(pk=request.data["tax_type"]).first()
        invoices = [InvoiceService.from_delivery_note(n, basis="loaded", post=False, charge_tax=charge, tax_type=tax_type) for n in notes]
        for inv in invoices:
            # the % was shown on the create page, seeded from the order — apply a change before posting
            if pct is not None and Decimal(str(pct)) != inv.discount_pct:
                InvoiceService.set_discount(inv, action="apply" if Decimal(str(pct)) > 0 else "remove", percent=pct)
            if should_post:
                InvoiceService.post(inv)
        return Response(SalesInvoiceSerializer(invoices, many=True).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        from apps.sales.documents import order_pdf

        return order_pdf(request, self.get_object())


    def perform_destroy(self, instance):
        """
        Delete only an order that never went anywhere — draft, submitted, under review or rejected, and with no
        delivery note, invoice or trip behind it. A confirmed order has reserved stock and a processed one has a
        ledger entry; those are **cancelled** (which reverses them and keeps the record), never deleted.
        """
        if instance.status not in ("draft", "submitted", "under_review", "rejected"):
            raise DomainError(f"{instance.number} is {instance.get_status_display().lower()} and cannot be deleted — cancel it instead.",
                              code="not_deletable", status_code=400)
        if instance.delivery_notes.exists() or instance.invoices.exists() or instance.trip_id:
            raise DomainError(f"{instance.number} has deliveries or invoices and cannot be deleted — cancel it instead.",
                              code="not_deletable", status_code=400)
        AuditService.log("order.deleted", instance, before={"number": instance.number, "status": instance.status}, tenant_id=self.request.tenant.id)
        instance.delete()


class SalesInvoiceViewSet(ExportableMixin, mixins.UpdateModelMixin, viewsets.ReadOnlyModelViewSet):
    export_fields = ["number", "date", "due_date", "customer_code", "customer_name", "order_number", "rep_name",
                     "subtotal", "discount", "tax1_total", "tax2_total", "total", "paid_amount", "balance", "status"]
    """
    Invoices are read, printed and corrected in the narrow sense — see `SalesInvoiceEditSerializer` for why the
    amounts are not among the things a PATCH may change.
    """

    permission_classes = [IsTenantUser]
    serializer_class = SalesInvoiceSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]
    perm_map = page_perms("invoices", {
        "cancel": "sales.delete_invoice",
        "post_to_ledger": "sales.add_invoice",
        "post": "sales.add_invoice",  # belt and braces: any POST reaching this viewset needs the same right
        "discount": extra_perm("invoices", "discount"),
        "tax": extra_perm("invoices", "discount"),  # the tax switch rides on the same right as the discount one
        "reopen": "sales.view_invoice",  # the real gate is inside: owner, or the matrix's edit-posted switch
        "fbr_validate": extra_perm("invoices", "fbr_file"),
        "fbr_submit": extra_perm("invoices", "fbr_file"),
        "fbr_post": extra_perm("invoices", "fbr_file"),
        "payments": "sales.view_invoice",
        "stats": "sales.view_invoice",
        "pdf": "sales.view_invoice",
    })
    filterset_fields = ["status", "customer", "order", "rep", "date"]
    search_fields = ["number", "customer__name", "customer__code", "order__number"]
    ordering_fields = ["date", "due_date", "number", "total", "created_at"]

    def get_serializer_class(self):
        return SalesInvoiceEditSerializer if self.action == "partial_update" else SalesInvoiceSerializer

    def perform_update(self, serializer):
        inv = serializer.instance
        if inv.status == "cancelled":
            raise DomainError("A cancelled invoice cannot be edited.", code="cancelled", status_code=400)
        d = serializer.validated_data
        if inv.status == "draft":
            # before the ledger has it, the figures are still the office's to correct
            InvoiceService.update_draft(inv, date=d.get("date"), due_date=d.get("due_date"),
                                        notes=d.get("notes"), lines=d.get("lines"))
            return
        before = {"due_date": str(inv.due_date), "notes": inv.notes}
        serializer.save()
        AuditService.log("invoice.edited", inv, before=before,
                         after={"due_date": str(inv.due_date), "notes": inv.notes})

    # NOT named `post`: DRF looks a handler up by the request method, so an action called `post` is also what a
    # bare POST to the detail URL would reach. The URL is still .../post.
    @action(detail=True, methods=["post"], url_path="post")
    def post_to_ledger(self, request, pk=None):
        """Send a draft invoice to the ledger. After this its figures are fixed and a credit note is the way back."""
        inv = self.get_object()
        InvoiceService.post(inv)
        inv.refresh_from_db()
        return Response(SalesInvoiceSerializer(inv).data)

    def update(self, request, *args, **kwargs):
        """
        A full replace would quietly blank the fields it does not carry, so only PATCH is accepted.

        The answer is the whole invoice as it now stands, not the handful of fields that were sent: a corrected
        price changes the tax and the total too, and the screen should not have to ask again to find that out.
        """
        if not kwargs.get("partial"):
            raise DomainError("Send the fields you are changing (PATCH).", status_code=405)
        super().update(request, *args, **kwargs)
        return Response(SalesInvoiceSerializer(self.get_object()).data)

    def get_queryset(self):
        qs = SalesInvoice.objects.select_related("customer", "order", "delivery_note", "rep", "journal", "tax1_type", "tax2_type").prefetch_related("lines__variant", "lines__batch")
        qs = _rep_scope(self.request, qs)
        p = self.request.query_params
        if p.get("date_from"):
            qs = qs.filter(date__gte=p["date_from"])
        if p.get("date_to"):
            qs = qs.filter(date__lte=p["date_to"])
        if p.get("open") == "1":
            qs = qs.filter(status__in=["posted", "partially_paid", "overdue"])
        if p.get("returnable") == "1":
            # a credit note can be raised against any real invoice, PAID included — goods come back after
            # payment all the time; only drafts and cancelled ones are no basis for a return
            qs = qs.filter(status__in=["posted", "partially_paid", "paid", "overdue"])
        if p.get("overdue") == "1":
            qs = qs.filter(status__in=["posted", "partially_paid", "overdue"], due_date__lt=timezone.localdate())
        if p.get("customer_type"):
            # retailer / salon / wholesaler / LMT / other — several may be asked for at once (owner, 27 Oct 2026)
            qs = qs.filter(customer__type__in=p["customer_type"].split(","))
        return qs

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return Response(SalesInvoiceSerializer(InvoiceService.cancel(self.get_object(), request.data.get("reason", ""))).data)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        from apps.sales.documents import invoice_pdf

        return invoice_pdf(request, self.get_object())

    @action(detail=True, methods=["post"])
    def discount(self, request, pk=None):
        """POST { action: "remove" | "apply", percent? } — only on a draft invoice."""
        inv = self.get_object()
        InvoiceService.set_discount(inv, action=request.data.get("action", "remove"), percent=request.data.get("percent"), actor=request.user)
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        """Posted → draft by reversing everything it booked — the owner's call, or a role holding the switch."""
        m = self.request.membership
        if not (m.is_owner or m.has_perm("sales.edit_posted_invoice")):
            raise DomainError("Only the owner can reopen a posted invoice for editing.", status_code=403)
        inv = InvoiceService.reopen(self.get_object(), reason=request.data.get("reason", ""), actor=request.user)
        return Response(self.get_serializer(inv).data)

    @action(detail=True, methods=["post"], url_path="fbr-validate")
    def fbr_validate(self, request, pk=None):
        """Dry-run this invoice against FBR — free, catches classification errors, issues no number."""
        from apps.fbr import services as fbr

        inv = self.get_object()
        if inv.status == "draft":
            raise DomainError("Post the invoice first — FBR files final invoices, not drafts.")
        filing = fbr.validate(inv, actor=request.user)
        return Response({"status": filing.status, "code": filing.validate_code, "sandbox": filing.sandbox})

    @action(detail=True, methods=["post"], url_path="fbr-submit")
    def fbr_submit(self, request, pk=None):
        """Post this invoice to FBR for real; the 28-character number lands on the printed copy."""
        from apps.fbr import services as fbr

        filing = fbr.submit(self.get_object(), actor=request.user)
        return Response({"status": filing.status, "fbr_invoice_number": filing.fbr_invoice_number,
                         "sandbox": filing.sandbox})

    @action(detail=True, methods=["post"], url_path="fbr-post")
    def fbr_post(self, request, pk=None):
        """ONE button for a posted tax invoice (owner, 27 Oct 2026: "after submit also give button to post to fbr"):
        validate, and only if FBR passes it, post it for real — the guide's validate-before-post rule, in one click."""
        from apps.fbr import services as fbr

        inv = self.get_object()
        if inv.status in ("draft", "cancelled"):
            raise DomainError("Post the invoice to the ledger first — FBR files final invoices, not drafts.")
        if inv.force_tax_type_id is None:
            raise DomainError("Only a tax invoice goes to FBR — switch the tax on first.", code="not_tax_invoice")
        filing = getattr(inv, "fbr_filing", None)
        if filing is None or filing.status != "submitted":
            fbr.validate(inv, actor=request.user)
            filing = fbr.submit(inv, actor=request.user)
        return Response({"status": filing.status, "fbr_invoice_number": filing.fbr_invoice_number, "sandbox": filing.sandbox})

    @action(detail=True, methods=["post"])
    def tax(self, request, pk=None):
        """POST { apply: true|false, tax_type? } — the invoice's tax switch. A draft switches in place; a
        POSTED, unpaid invoice is reopened and switched in this ONE request (review + re-post by the user) —
        two separate calls could strand it reopened off the books when the switch refuses (audit, 26 Oct 2026).
        ATOMIC_REQUESTS rolls the reopen back with the refusal, so a failure leaves the invoice exactly posted."""
        from apps.catalog.models import TaxType

        inv = self.get_object()
        if inv.status in ("posted", "overdue") and not inv.paid_amount:
            m = request.membership
            if not (m.is_owner or m.has_perm("sales.edit_posted_invoice")):
                raise DomainError("Changing tax on a posted invoice needs the edit-posted right.", status_code=403)
            InvoiceService.reopen(inv, reason="tax switch", actor=request.user)
            inv.refresh_from_db()
        tt = TaxType.objects.filter(pk=request.data["tax_type"]).first() if request.data.get("tax_type") else None
        InvoiceService.set_tax(inv, apply=request.data.get("apply") in (True, "true", "1", 1), tax_type=tt,
                               actor=request.user)
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["get"])
    def payments(self, request, pk=None):
        from apps.accounting.models import PaymentAllocation
        from apps.accounting.serializers import PaymentSerializer

        pays = [a.payment for a in PaymentAllocation.objects.filter(document_type="sales_invoice", document_id=str(pk)).select_related("payment")]
        cols = RepCollection.objects.filter(allocations__contains=[{"invoice": str(pk)}])
        return Response({"payments": PaymentSerializer(pays, many=True).data, "rep_collections": RepCollectionSerializer(cols, many=True).data})

    @action(detail=False, methods=["get"])
    def stats(self, request):
        qs = self.get_queryset()
        today = timezone.localdate()
        open_qs = qs.filter(status__in=["posted", "partially_paid", "overdue"])
        agg = open_qs.aggregate(t=Sum("total"), p=Sum("paid_amount"))
        ov = open_qs.filter(due_date__lt=today).aggregate(t=Sum("total"), p=Sum("paid_amount"))
        return Response({"open_count": open_qs.count(), "outstanding": str((agg["t"] or 0) - (agg["p"] or 0)), "overdue": str((ov["t"] or 0) - (ov["p"] or 0)), "month_sales": str(qs.filter(date__gte=today.replace(day=1)).exclude(status="cancelled").aggregate(s=Sum("total"))["s"] or 0)})


class CreditNoteViewSet(DocumentViewSet):
    export_fields = ["number", "date", "customer_name", "invoice_number", "reason", "subtotal", "tax1_total", "total", "status"]
    serializer_class = CreditNoteSerializer
    perm_map = page_perms("credit_notes", {"post": "sales.add_credit_note"})
    filterset_fields = ["status", "customer", "invoice"]
    search_fields = ["number", "customer__name", "reason"]
    http_method_names = ["get", "post", "patch", "head", "options"]
    queryset = CreditNote.objects.select_related("customer", "invoice", "warehouse", "journal").prefetch_related("lines__variant")

    def perform_create(self, serializer):
        d = serializer.validated_data
        serializer.instance = CreditNoteService.create(customer=d["customer"], lines=[dict(l) for l in d["lines"]], invoice=d.get("invoice"), warehouse=d.get("warehouse"), date=d.get("date"), reason=d.get("reason", ""), notes=d.get("notes", ""), applied_to_invoice=d.get("applied_to_invoice", True))
        if self.request.data.get("post"):
            CreditNoteService.post(serializer.instance)

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        return Response(CreditNoteSerializer(CreditNoteService.post(self.get_object())).data)


class RepCollectionViewSet(DocumentViewSet):
    export_fields = ["number", "date", "rep_name", "customer_name", "amount", "method", "reference", "status", "settlement_number"]
    serializer_class = RepCollectionSerializer
    perm_map = page_perms(
        "rep_collections",
        {"unsettled": extra_perm("rep_collections", "settle")},
        also={
            "list": ["external.view_own_collections"],
            "retrieve": ["external.view_own_collections"],
            "create": ["external.record_collection"],
        },
    )
    filterset_fields = ["status", "rep", "customer", "method", "settlement"]
    search_fields = ["number", "customer__name", "rep__name", "reference"]
    ordering_fields = ["date", "amount", "created_at"]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        return _rep_scope(self.request, RepCollection.objects.select_related("rep", "customer", "settlement", "recorded_by", "journal"))

    def create(self, request, *args, **kwargs):
        """Cash is collected at the shop, so a rep with a working area has to be standing in it."""
        from apps.partners.geofence import GeoFenceService
        from apps.partners.models import Customer, SupplierRep

        m = request.membership
        rep = m.supplier_rep if m.supplier_rep_id else SupplierRep.objects.filter(pk=request.data.get("rep")).first()
        customer = Customer.objects.filter(pk=request.data.get("customer")).first()
        blocked = GeoFenceService.guard(rep=rep, gps=request.data.get("gps"), action="collection", customer=customer)
        return blocked or super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        d = serializer.validated_data
        m = self.request.membership
        rep = m.supplier_rep if m.supplier_rep_id else d.get("rep")
        if rep is None:
            raise DomainError("rep is required.")
        serializer.instance = RepCollectionService.record(rep=rep, customer=d["customer"], amount=d["amount"], date=d.get("date"), method=d.get("method", "cash"), reference=d.get("reference", ""), allocations=d.get("allocations") or None, notes=d.get("notes", ""), gps=d.get("gps"))

    @action(detail=False, methods=["get"])
    def unsettled(self, request):
        qs = self.get_queryset().filter(status="held_by_rep")
        if request.query_params.get("rep"):
            qs = qs.filter(rep_id=request.query_params["rep"])
        by_rep = qs.values("rep_id", "rep__name").annotate(total=Sum("amount"), n=Count("id"))
        return Response({"by_rep": [{"rep_id": str(r["rep_id"]), "rep": r["rep__name"], "total": str(r["total"]), "count": r["n"]} for r in by_rep], "collections": RepCollectionSerializer(qs, many=True).data})


class RepSettlementViewSet(DocumentViewSet):
    export_fields = ["number", "date", "rep_name", "amount", "received_by_name", "journal_number", "collection_numbers"]
    serializer_class = RepSettlementSerializer
    perm_map = page_perms("rep_settlements")
    filterset_fields = ["rep"]
    search_fields = ["number", "rep__name"]
    http_method_names = ["get", "post", "head", "options"]
    queryset = RepSettlement.objects.select_related("rep", "received_by", "journal").prefetch_related("collections__customer")

    def perform_create(self, serializer):
        d = serializer.validated_data
        serializer.instance = RepCollectionService.settle(rep=d["rep"], collections=list(d["collections"]), cash_account=d.get("cash_account"), bank_account=d.get("bank_account"), date=d.get("date"), notes=d.get("notes", ""))


class DiscountContributionViewSet(TenantModelViewSet):
    """The co-funded discount table (Settings): add/edit/delete/list the total % → distributor/company split."""
    serializer_class = DiscountContributionSerializer
    perm_map = page_perms("settings_discount_contributions")
    pagination_class = None
    queryset = DiscountContribution.objects.all()


class CompanyClaimViewSet(DocumentViewSet):
    """The manufacturer's share of invoice discounts, raised automatically on posting. Read-only + settle/cancel."""
    serializer_class = CompanyClaimSerializer
    perm_map = page_perms("company_claims", {"settle": "sales.settle_company_claim", "net_off": "sales.settle_company_claim"})
    http_method_names = ["get", "post", "head", "options"]
    filterset_fields = ["status", "supplier", "invoice"]
    search_fields = ["number", "supplier__name", "invoice__number"]

    def get_queryset(self):
        return CompanyClaim.objects.select_related("supplier", "invoice", "invoice__customer", "journal").prefetch_related("lines__variant")

    @action(detail=True, methods=["post"])
    def settle(self, request, pk=None):
        d = request.data
        CompanyClaimService.settle_receipt(
            self.get_object(), amount=d.get("amount"), method=d.get("method", "bank_transfer"),
            cash_account=d.get("cash_account") or None, bank_account=d.get("bank_account") or None,
            date=d.get("date") or None, reference=d.get("reference", ""),
        )
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="net-off")
    def net_off(self, request, pk=None):
        CompanyClaimService.net_off(self.get_object(), amount=request.data.get("amount"), date=request.data.get("date") or None, notes=request.data.get("notes", ""))
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        CompanyClaimService.cancel(self.get_object(), reason=request.data.get("reason", ""))
        return Response(self.get_serializer(self.get_object()).data)
