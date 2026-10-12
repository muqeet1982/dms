from decimal import Decimal

from django.db.models import Count, DecimalField, F, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce, Greatest
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.page_permissions import extra_perm, page_perms
from apps.core.audit import AuditService
from apps.core.exceptions import DomainError
from apps.core.permissions import require_perm
from apps.core.viewsets import DocumentViewSet, warehouse_scope


def sub_count(qs, by="po"):
    """A `COUNT(*)` subquery grouped on the parent — counting over two joins in one query multiplies the rows."""
    return Coalesce(Subquery(qs.order_by().values(by).annotate(n=Count("*")).values("n")[:1]), Value(0))

from .models import GoodsReceipt, PurchaseBill, PurchaseOrder, PurchaseOrderLine, PurchaseReturn
from .serializers import GoodsReceiptSerializer, POListSerializer, PurchaseBillSerializer, PurchaseOrderSerializer, PurchaseReturnSerializer
from .services import GoodsReceiptService, PurchaseBillService, PurchaseOrderService, PurchaseReturnService, _default_bin



def _pdf(request, template, ctx, filename):
    from django.template.loader import render_to_string
    from weasyprint import HTML

    html = render_to_string(template, {**ctx, "tenant": request.tenant})
    resp = HttpResponse(HTML(string=html).write_pdf(), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{filename}.pdf"'
    return resp


class PurchaseOrderViewSet(DocumentViewSet):
    serializer_class = PurchaseOrderSerializer
    perm_map = page_perms("purchase_orders", {
        "send": extra_perm("purchase_orders", "approve"),
        "cancel": "purchasing.delete_purchase_order",
        "pdf": "purchasing.view_purchase_order",
        "from_reorder": "purchasing.add_purchase_order",
        "stats": "purchasing.view_purchase_order",
        "workflow": "purchasing.view_purchase_order", "receiving": "purchasing.view_purchase_order",
        "close_line": "purchasing.change_purchase_order",
        "close_remaining": "purchasing.change_purchase_order",
    })
    filterset_fields = ["status", "supplier", "warehouse"]
    search_fields = ["number", "reference", "supplier_ref", "supplier__name", "supplier__code", "notes", "lines__variant__sku", "lines__variant__product__item_code"]
    ordering_fields = ["date", "number", "total", "created_at"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_serializer_class(self):
        return POListSerializer if self.action == "list" else PurchaseOrderSerializer

    def get_queryset(self):
        qs = PurchaseOrder.objects.select_related("supplier", "warehouse", "created_by")
        if self.action == "list":
            # the list and the pickers only need the counts, so the lines are counted in SQL and never serialized
            due = Greatest(F("qty_ordered") - F("qty_received"), Value(0, output_field=DecimalField(max_digits=18, decimal_places=3)))
            qs = qs.annotate(
                lines_count=sub_count(PurchaseOrderLine.objects.filter(po=OuterRef("pk"))),
                grns_count=sub_count(GoodsReceipt.objects.filter(po=OuterRef("pk")).exclude(status="cancelled")),
                units_due=Coalesce(
                    Subquery(
                        PurchaseOrderLine.objects.filter(po=OuterRef("pk"), closed=False).values("po").annotate(s=Sum(due)).values("s")[:1],
                        output_field=DecimalField(max_digits=18, decimal_places=3),
                    ),
                    Value(0, output_field=DecimalField(max_digits=18, decimal_places=3)),
                ),
            )
        else:
            qs = qs.prefetch_related("lines__variant__product__location__warehouse", "lines__packaging", "lines__tax1_type", "lines__tax2_type")
        p = self.request.query_params
        if p.get("status__in"):
            # a picker asks for the orders you can still receive: ?status__in=sent,partially_received
            qs = qs.filter(status__in=[x.strip() for x in p["status__in"].split(",") if x.strip()])
        if p.get("open") == "1":
            qs = qs.filter(status__in=["sent", "partially_received"])
        if p.get("date_from"):
            qs = qs.filter(date__gte=p["date_from"])
        if p.get("date_to"):
            qs = qs.filter(date__lte=p["date_to"])
        return warehouse_scope(self.request, qs)

    def perform_create(self, serializer):
        d = serializer.validated_data
        lines = [{"variant": l["variant"], "packaging": l.get("packaging"), "qty": l.get("qty") or 0, "unit_price": l["unit_price"], "discount_pct": l.get("discount_pct", 0), "price_per_packaging": l.get("price_per_packaging", False)} for l in d["lines"]]
        serializer.instance = PurchaseOrderService.create(supplier=d["supplier"], warehouse=d["warehouse"], lines=lines, date=d.get("date"), expected_date=d.get("expected_date"), notes=d.get("notes", ""), freight=d.get("freight", 0), supplier_ref=d.get("supplier_ref", ""), reference=d.get("reference", ""))

    def perform_update(self, serializer):
        po = serializer.instance
        if po.status not in ("draft", "sent"):
            raise DomainError("Only draft/sent POs can be edited.")
        lines = serializer.validated_data.pop("lines", None)
        super().perform_update(serializer)
        if lines is not None:
            PurchaseOrderService.write_lines(po, [{"variant": l["variant"], "packaging": l.get("packaging"), "qty": l.get("qty") or 0, "unit_price": l["unit_price"], "discount_pct": l.get("discount_pct", 0), "price_per_packaging": l.get("price_per_packaging", False)} for l in lines])
        else:
            PurchaseOrderService.recompute(po)

    @action(detail=True, methods=["post"])
    @require_perm("purchasing.approve_purchase_order")
    def send(self, request, pk=None):
        return Response(PurchaseOrderSerializer(PurchaseOrderService.send(self.get_object())).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return Response(PurchaseOrderSerializer(PurchaseOrderService.cancel(self.get_object(), request.data.get("reason", ""))).data)

    @action(detail=True, methods=["get"])
    def receiving(self, request, pk=None):
        """`GET /api/v1/purchase-orders/{id}/receiving` — owed per line, and a dated log of each delivery."""
        from .workflow import po_receiving

        return Response(po_receiving(self.get_object()))

    @action(detail=True, methods=["get"])
    def workflow(self, request, pk=None):
        """Order → approved → receiving → put away → billed → paid, plus what each line is still waiting for."""
        from .workflow import po_workflow

        return Response(po_workflow(self.get_object()))

    @action(detail=True, methods=["post"], url_path="close-line")
    def close_line(self, request, pk=None):
        """Stop waiting for the rest of one line (the supplier is not sending it)."""
        po = self.get_object()
        line = po.lines.filter(pk=request.data.get("line_id")).first()
        if line is None:
            raise DomainError("Which line? Send `line_id`.", status_code=400)
        if line.closed:
            raise DomainError("That line is already closed.")
        line.closed = True
        line.closed_reason = (request.data.get("reason") or "")[:200]
        line.save(update_fields=["closed", "closed_reason"])
        PurchaseOrderService.refresh_status(po)
        AuditService.log("po.line_closed", po, after={"line": str(line.pk), "reason": line.closed_reason})
        po.refresh_from_db()
        return Response(PurchaseOrderSerializer(po).data)

    @action(detail=True, methods=["post"], url_path="close-remaining")
    def close_remaining(self, request, pk=None):
        """Close every line that is still waiting — the order is finished with whatever arrived."""
        po = self.get_object()
        reason = (request.data.get("reason") or "")[:200]
        n = 0
        for line in po.lines.all():
            if not line.closed and line.qty_pending > 0:
                line.closed, line.closed_reason = True, reason
                line.save(update_fields=["closed", "closed_reason"])
                n += 1
        if not n:
            raise DomainError("Nothing is outstanding on this order.")
        PurchaseOrderService.refresh_status(po)
        AuditService.log("po.closed_remaining", po, after={"lines": n, "reason": reason})
        po.refresh_from_db()
        return Response(PurchaseOrderSerializer(po).data)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        from apps.core.documents import render_doc

        po = self.get_object()
        lines = list(po.lines.select_related("variant__product", "packaging").order_by("sequence"))
        return render_doc(request, "purchasing/po.html", {"po": po, "lines": lines}, po.number,
                          title="PURCHASE ORDER", ref=po.number)

    @action(detail=False, methods=["post"], url_path="from-reorder")
    @require_perm("purchasing.add_purchase_order")
    def from_reorder(self, request):
        from apps.partners.models import Supplier

        supplier = Supplier.objects.get(pk=request.data["supplier"])
        po = PurchaseOrderService.from_reorder(supplier, request.data.get("rows", []))
        return Response(PurchaseOrderSerializer(po).data, status=201)

    @action(detail=False, methods=["get"])
    def stats(self, request):
        qs = self.get_queryset()
        month = timezone.localdate().replace(day=1)
        return Response({"draft": qs.filter(status="draft").count(), "open": qs.filter(status__in=["sent", "partially_received"]).count(), "received_month": qs.filter(status="received", date__gte=month).count(), "value_month": str(qs.filter(date__gte=month).exclude(status="cancelled").aggregate(s=Sum("total"))["s"] or 0)})


class GoodsReceiptViewSet(DocumentViewSet):
    serializer_class = GoodsReceiptSerializer
    perm_map = page_perms("grns", {
        "post": "purchasing.add_grn",
        "cancel": "purchasing.delete_grn",
        "scans": "purchasing.change_grn",
        "pdf": "purchasing.view_grn",
    })
    filterset_fields = ["status", "supplier", "warehouse", "po", "billed"]
    search_fields = ["number", "supplier__name", "supplier_challan_no", "po__number"]
    ordering_fields = ["date", "number", "created_at"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return warehouse_scope(self.request, GoodsReceipt.objects.select_related("supplier", "warehouse", "po", "received_by", "journal").prefetch_related("lines__variant__product", "lines__packaging", "landed_costs"))

    def perform_create(self, serializer):
        """
        Record a delivery. Receiving again while a draft is still open against the same order fills that draft in,
        so an order being received in several goes does not end up with a row of half-keyed receipts beside it.
        """
        d = serializer.validated_data
        lines = [{k: v for k, v in l.items()} for l in d["lines"]]  # close_line / reject_reason are read by the service
        po = d.get("po")
        # the receiving screen never asks who the goods came from: the order says so
        supplier = d.get("supplier") or (po.supplier if po is not None else None)
        if supplier is None:
            raise DomainError("Pick the purchase order this delivery is against.", code="po_required", status_code=400)
        serializer.instance = GoodsReceiptService.create(supplier=supplier, warehouse=d["warehouse"], lines=lines, po=po, date=d.get("date"), supplier_challan_no=d.get("supplier_challan_no", ""), notes=d.get("notes", ""), landed_costs=d.get("landed_costs"), confirm_duplicate_challan=bool(self.request.data.get("confirm_duplicate_challan")))
        if self.request.data.get("post"):
            GoodsReceiptService.post(serializer.instance)

    def perform_update(self, serializer):
        """
        Correct a receipt before it is posted — the challan number, the date, and the rows: add an item, change a
        quantity, batch or expiry, drop a row that was keyed by mistake. A posted receipt is refused, because its
        stock is already in the warehouse; the refusal names the way back (cancel, then receive again).
        """
        grn = serializer.instance
        GoodsReceiptService.check_editable(grn)
        d = serializer.validated_data
        lines = d.pop("lines", None)
        lcs = d.pop("landed_costs", None)
        super().perform_update(serializer)
        GoodsReceiptService.update(grn, lines=[dict(l) for l in lines] if lines is not None else None, landed_costs=lcs)

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        return Response(GoodsReceiptSerializer(GoodsReceiptService.post(self.get_object())).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return Response(GoodsReceiptSerializer(GoodsReceiptService.cancel(self.get_object(), request.data.get("reason", ""))).data)

    @action(detail=True, methods=["post"])
    def scans(self, request, pk=None):
        """
        Scanner mode on receiving: POST {scans:[{code, qty?, batch_no?, expiry_date?}]}.

        One scan of a **unit** barcode counts one; one scan of a **box** (its own barcode, or the item code printed
        on the carton) counts a whole box. A box label that carries its batch — `16418.01#195654`, item code then
        batch — fills the batch number too. When the receipt belongs to a purchase order, the scan is matched
        against that order and refused if the item is not on it, so a wrong carton cannot be received quietly.
        """
        from apps.catalog.services import BarcodeParser

        from .models import GoodsReceiptLine

        grn = self.get_object()
        GoodsReceiptService.check_editable(grn)
        GoodsReceiptService.check_order_open(grn.po)
        results = []
        for s in request.data.get("scans", []):
            hit = BarcodeParser.parse(s["code"])
            variant, packaging = hit["variant"], hit["packaging"]
            # what one scan of this label means, as the parser read it — a carton code counts the carton
            qty = Decimal(str(s.get("qty", 1))) * hit["qty_per_scan"]
            po_line = None
            if grn.po_id:
                po_line = grn.po.lines.filter(variant=variant).first()
                if po_line is None:
                    raise DomainError(
                        f"{variant.name} is not on {grn.po.number}.",
                        code="not_on_order", status_code=400,
                        errors={"code": [s["code"]], "variant": [variant.name]},
                    )
            batch_no = (s.get("batch_no") or hit["batch_no"] or "")[:60]
            expiry = s.get("expiry_date") or hit["expiry_date"]
            line = grn.lines.filter(variant=variant, batch_no=batch_no).first()
            if line is None and not batch_no:
                # a loose piece scanned after its carton carries no batch of its own: it belongs on the row already
                # open for that item, so the tally goes up instead of a second row appearing. With several batch rows
                # open there is no telling which one it came out of, so it does get its own row.
                open_rows = list(grn.lines.filter(variant=variant))
                if len(open_rows) == 1:
                    line = open_rows[0]
            if line:
                line.qty_accepted += qty
                if expiry and not line.expiry_date:
                    line.expiry_date = expiry
                line.save(update_fields=["qty_accepted", "expiry_date"])
            else:
                line = GoodsReceiptLine.objects.create(
                    grn=grn, po_line=po_line, variant=variant, packaging=packaging, qty_accepted=qty,
                    batch_no=batch_no, expiry_date=expiry, bin=_default_bin(variant, grn.warehouse),
                    unit_cost=(po_line.unit_price if po_line else variant.cost_price), sequence=grn.lines.count(),
                )
            results.append({
                "line": str(line.id), "variant": str(variant.id), "name": variant.name,
                "item_code": variant.product.item_code, "counted": str(qty), "unit": hit["unit"],
                "batch_no": batch_no, "expiry_date": line.expiry_date, "needs_expiry": bool(batch_no) and not line.expiry_date,
                "po_line": str(po_line.id) if po_line else None,
            })
        GoodsReceiptService.allocate_landed_costs(grn)
        data = GoodsReceiptSerializer(grn).data
        data["scanned"] = results
        return Response(data)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        grn = self.get_object()
        return _pdf(request, "purchasing/grn.html", {"grn": grn}, grn.number)


class PurchaseBillViewSet(DocumentViewSet):
    serializer_class = PurchaseBillSerializer
    perm_map = page_perms("bills", {
        "post": extra_perm("bills", "post"),
        "cancel": "purchasing.delete_bill",
        "payments": "purchasing.view_bill",
    })
    filterset_fields = ["status", "supplier"]
    search_fields = ["number", "supplier__name", "supplier_invoice_no"]
    ordering_fields = ["bill_date", "due_date", "total", "created_at"]
    http_method_names = ["get", "post", "patch", "head", "options"]
    queryset = PurchaseBill.objects.select_related("supplier", "journal", "tax1_type", "tax2_type").prefetch_related("grns")

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get("overdue") == "1":
            qs = qs.filter(status__in=["posted", "partially_paid"], due_date__lt=timezone.localdate())
        return qs

    def perform_create(self, serializer):
        d = serializer.validated_data
        serializer.instance = PurchaseBillService.create(supplier=d["supplier"], grns=d.get("grns", []), bill_date=d.get("bill_date"), due_date=d.get("due_date"), supplier_invoice_no=d.get("supplier_invoice_no", ""), lines=self.request.data.get("manual_lines"), discount=d.get("discount", 0), freight=d.get("freight", 0), notes=d.get("notes", ""), tax1_type=d.get("tax1_type"), tax2_type=d.get("tax2_type"), tax1_total=self.request.data.get("tax1_total_override"), tax2_total=self.request.data.get("tax2_total_override"))
        if self.request.data.get("post"):
            PurchaseBillService.post(serializer.instance)

    def perform_update(self, serializer):
        if serializer.instance.status != "draft":
            raise DomainError("Posted bills cannot be edited.")
        super().perform_update(serializer)

    @action(detail=True, methods=["post"])
    @require_perm("purchasing.post_purchase_bill")
    def post(self, request, pk=None):
        return Response(PurchaseBillSerializer(PurchaseBillService.post(self.get_object())).data)

    @action(detail=True, methods=["post"])
    @require_perm("purchasing.post_purchase_bill")
    def cancel(self, request, pk=None):
        return Response(PurchaseBillSerializer(PurchaseBillService.cancel(self.get_object(), request.data.get("reason", ""))).data)

    @action(detail=True, methods=["get"])
    def payments(self, request, pk=None):
        from apps.accounting.models import PaymentAllocation
        from apps.accounting.serializers import PaymentSerializer

        pays = [a.payment for a in PaymentAllocation.objects.filter(document_type="purchase_bill", document_id=str(pk)).select_related("payment")]
        return Response(PaymentSerializer(pays, many=True).data)


class PurchaseReturnViewSet(DocumentViewSet):
    serializer_class = PurchaseReturnSerializer
    perm_map = page_perms("purchase_returns", {"post": "purchasing.add_purchase_return"})
    filterset_fields = ["status", "supplier", "warehouse", "is_debit_note_only"]
    search_fields = ["number", "supplier__name", "reason"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return warehouse_scope(self.request, PurchaseReturn.objects.select_related("supplier", "warehouse", "grn", "journal").prefetch_related("lines__variant", "lines__batch"))

    def perform_create(self, serializer):
        d = serializer.validated_data
        serializer.instance = PurchaseReturnService.create(supplier=d["supplier"], warehouse=d["warehouse"], lines=[dict(l) for l in d["lines"]], grn=d.get("grn"), date=d.get("date"), reason=d.get("reason", ""), reason_code=d.get("reason_code"), notes=d.get("notes", ""), is_debit_note_only=d.get("is_debit_note_only", False))
        if self.request.data.get("post"):
            PurchaseReturnService.post(serializer.instance)

    @action(detail=True, methods=["post"])
    @require_perm("purchasing.add_purchase_return")
    def post(self, request, pk=None):
        return Response(PurchaseReturnSerializer(PurchaseReturnService.post(self.get_object())).data)
