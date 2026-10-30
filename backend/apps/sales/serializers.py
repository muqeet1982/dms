from decimal import Decimal

from rest_framework import serializers

from .models import CompanyClaim, CompanyClaimLine, CreditNote, CreditNoteLine, DiscountContribution, RepCollection, RepSettlement, SalesInvoice, SalesInvoiceLine, SalesOrder, SalesOrderLine


class CompanyClaimLineSerializer(serializers.ModelSerializer):
    variant_name = serializers.CharField(source="variant.name", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)

    class Meta:
        model = CompanyClaimLine
        fields = ["id", "variant", "variant_name", "variant_sku", "qty", "base_amount", "company_amount"]


class CompanyClaimSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    customer_name = serializers.CharField(source="invoice.customer.name", read_only=True)
    journal_number = serializers.CharField(source="journal.number", read_only=True, default="")
    balance = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)
    lines = CompanyClaimLineSerializer(many=True, read_only=True)

    class Meta:
        model = CompanyClaim
        fields = ["id", "number", "invoice", "invoice_number", "customer_name", "supplier", "supplier_name", "date", "discount_pct", "company_pct", "total", "paid_amount", "balance", "status", "journal", "journal_number", "lines", "created_at"]


class DiscountContributionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiscountContribution
        fields = ["id", "total_pct", "distributor_pct", "company_pct", "is_active"]

    def validate(self, attrs):
        inst = self.instance
        total = attrs.get("total_pct", getattr(inst, "total_pct", None))
        dist = attrs.get("distributor_pct", getattr(inst, "distributor_pct", None))
        comp = attrs.get("company_pct", getattr(inst, "company_pct", None))
        if total is None or dist is None or comp is None:
            raise serializers.ValidationError("Total, distributor and company percentages are all required.")
        if any(v < 0 for v in (total, dist, comp)):
            raise serializers.ValidationError("Percentages cannot be negative.")
        if Decimal(dist) + Decimal(comp) != Decimal(total):
            raise serializers.ValidationError(f"Distributor ({dist}%) + company ({comp}%) must equal the total ({total}%).")
        return attrs


class OrderLineSerializer(serializers.ModelSerializer):
    variant_name = serializers.CharField(source="variant.name", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)
    packaging_name = serializers.CharField(source="packaging.name", read_only=True, default="")
    uom = serializers.CharField(source="variant.product.base_uom.code", read_only=True)
    qty = serializers.DecimalField(max_digits=18, decimal_places=3, write_only=True, required=False)
    price_per_packaging = serializers.BooleanField(write_only=True, required=False, default=False)
    qty_pending = serializers.DecimalField(max_digits=18, decimal_places=3, read_only=True)
    # `qty_ordered` has always been in base units — `_base_qty()` converts on the way in — but a client that
    # read it next to `packaging_name` could reasonably render "36 box" for 36 pieces. `qty_base` says what it
    # is in its name, and `packaging_qty_in_base` is the pack factor, so a carton figure can be shown too.
    qty_base = serializers.DecimalField(source="qty_ordered", max_digits=18, decimal_places=3, read_only=True)
    # the product's own Tax % and FBR rate ride along so the invoice create page can preview each line's
    # tax before the invoice exists (the server recomputes the real figures at creation)
    product_tax_pct = serializers.DecimalField(source="variant.product.tax_pct", max_digits=7, decimal_places=3, read_only=True, default=None)
    product_fbr_rate = serializers.CharField(source="variant.product.fbr_rate_desc", read_only=True, default="")
    hs_code = serializers.CharField(source="variant.product.fbr_hs_code", read_only=True, default="")
    # the preview mirrors engine.compute_line, and these two decide the 3rd Schedule MRP base (guide §5)
    product_fbr_sale_type = serializers.CharField(source="variant.product.fbr_sale_type", read_only=True, default="")
    product_mrp = serializers.DecimalField(source="variant.product.mrp_ex_tax", max_digits=14, decimal_places=4, read_only=True, default=None)
    # the FED / Extra / Further / WHT rates on the product's FBR card — previews mirror line_taxes with them
    product_fed_rate = serializers.DecimalField(source="variant.product.fbr_fed_rate", max_digits=7, decimal_places=3, read_only=True, default=None)
    product_extra_tax_rate = serializers.DecimalField(source="variant.product.fbr_extra_tax_rate", max_digits=7, decimal_places=3, read_only=True, default=None)
    product_further_tax_rate = serializers.DecimalField(source="variant.product.fbr_further_tax_rate", max_digits=7, decimal_places=3, read_only=True, default=None)
    product_wht_rate = serializers.DecimalField(source="variant.product.fbr_wht_rate", max_digits=7, decimal_places=3, read_only=True, default=None)
    packaging_qty_in_base = serializers.DecimalField(source="packaging.qty_in_base", max_digits=18, decimal_places=3, read_only=True, default=None)
    tax1_name = serializers.CharField(source="tax1_type.name", read_only=True, default="")
    tax2_name = serializers.CharField(source="tax2_type.name", read_only=True, default="")

    class Meta:
        model = SalesOrderLine
        fields = ["id", "variant", "variant_name", "variant_sku", "packaging", "packaging_name", "packaging_qty_in_base", "uom", "qty", "qty_ordered", "qty_base", "qty_allocated", "qty_dispatched", "qty_delivered", "qty_returned", "qty_pending", "free_qty", "unit_price", "requested_price", "price_per_packaging", "discount_pct", "product_discount_pct", "product_tax_pct", "product_fbr_rate", "hs_code", "product_fbr_sale_type", "product_mrp", "product_fed_rate", "product_extra_tax_rate", "product_further_tax_rate", "product_wht_rate", "discount_amount", "scheme_discount", "scheme_names", "tax1_type", "tax1_name", "tax1_rate", "tax1_amount", "tax2_type", "tax2_name", "tax2_rate", "tax2_amount", "fed_amount", "extra_tax_amount", "further_tax_amount", "wht_amount", "line_total", "sequence"]
        read_only_fields = ["id", "qty_ordered", "qty_allocated", "qty_dispatched", "qty_delivered", "qty_returned", "free_qty", "discount_amount", "scheme_discount", "scheme_names", "tax1_type", "tax1_rate", "tax1_amount", "tax2_type", "tax2_rate", "tax2_amount", "fed_amount", "extra_tax_amount", "further_tax_amount", "wht_amount", "line_total", "sequence"]
        extra_kwargs = {"unit_price": {"required": False}}


class SalesOrderListSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    customer_code = serializers.CharField(source="customer.code", read_only=True)
    rep_name = serializers.CharField(source="rep.name", read_only=True, default="")
    supplier_name = serializers.CharField(source="supplier.name", read_only=True, default="")
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True, default="")
    trip_number = serializers.CharField(source="trip.number", read_only=True, default="")
    delivery_status = serializers.SerializerMethodField()
    delivery_state = serializers.SerializerMethodField()
    location_name = serializers.CharField(source="location.name", read_only=True, default="")
    location_check = serializers.SerializerMethodField()

    def get_location_check(self, obj):
        """
        Was the order taken AT the shop? (owner, 27 Oct 2026). From the location check the rep app's order went
        through: inside (at the shop pin, with the distance), outside (away, with how far), no_fix (no GPS sent),
        skipped (not checked — the shop has no pin, or the rep is not fenced). An order typed in the office has no
        position: "office". A field order with no check on record (before the checks existed): "unknown".
        """
        if hasattr(obj, "loc_verdict"):
            verdict, distance = obj.loc_verdict, obj.loc_distance
        else:
            from apps.partners.models import GeoFenceEvent

            e = GeoFenceEvent.objects.filter(action="order", document_id=str(obj.pk)).order_by("-occurred_at").first()
            verdict, distance = (e.verdict, e.distance_m) if e else (None, None)
        if verdict is None and obj.source != "staff" and obj.gps_lat is not None:
            # a field order with its position but no check (the rep may order anywhere, or checking is off):
            # say it was not checked, and how far from the shop it was taken anyway
            from apps.partners.geofence import distance_m

            pin = obj.location if obj.location_id and obj.location.gps_lat is not None else obj.customer
            if pin is not None and pin.gps_lat is not None and pin.gps_lng is not None:
                verdict = "unchecked"
                distance = int(distance_m((float(obj.gps_lat), float(obj.gps_lng)), (float(pin.gps_lat), float(pin.gps_lng))))
        if verdict is None:
            verdict = "office" if obj.source == "staff" else "unknown"
        return {"verdict": verdict, "distance_m": distance,
                "gps": {"lat": float(obj.gps_lat), "lng": float(obj.gps_lng)} if obj.gps_lat is not None else None}

    def get_delivery_state(self, obj):
        """The dispatch stage as a code — "", "on_trip" or "out_for_delivery" — for counting, not reading.
        The prose `delivery_status` is derived from this, so the two cannot disagree."""
        if not obj.trip_id or obj.status in ("delivered", "invoiced", "cancelled", "rejected"):
            return ""
        if obj.trip.status in ("planned", "loading"):
            return "on_trip"
        if obj.trip.status == "out_for_delivery":
            return "out_for_delivery"
        return ""

    def get_delivery_status(self, obj):
        """The dispatch stage, where it says more than the order status does: a created-but-not-started trip."""
        state = self.get_delivery_state(obj)
        if state == "on_trip":
            return f"On trip {obj.trip.number}"
        if state == "out_for_delivery":
            return f"Out for delivery · {obj.trip.number}"
        return ""
    lines_count = serializers.IntegerField(source="lines.count", read_only=True)
    sales_person = serializers.SerializerMethodField()

    def get_sales_person(self, obj):
        # the rep who took it, or the sales rep on its trip — never created_by (whoever imported it)
        if obj.rep_id:
            return obj.rep.name
        if obj.trip_id and obj.trip.rep_id:
            return obj.trip.rep.name
        return ""

    class Meta:
        model = SalesOrder
        fields = ["id", "number", "customer", "customer_name", "customer_code", "rep", "rep_name", "sales_person", "supplier", "supplier_name", "source", "warehouse", "warehouse_name", "order_date", "requested_delivery_date", "status", "delivery_status", "delivery_state", "location", "location_name", "location_check", "payment_mode", "charge_tax", "total", "trip", "trip_number", "external_ref", "lines_count", "hold_reason", "created_at"]


class SalesOrderSerializer(SalesOrderListSerializer):
    gst_pct = serializers.SerializerMethodField()   # "18%" — the GST % beside GST (owner, 27 Oct 2026)

    def get_gst_pct(self, obj):
        from .services import gst_pct

        return gst_pct(l.tax1_rate for l in obj.lines.all())

    lines = OrderLineSerializer(many=True)
    gps = serializers.SerializerMethodField()   # where the rep was standing when they took it
    confirmed_by_name = serializers.CharField(source="confirmed_by.full_name", read_only=True, default="")
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default="")
    customer_phone = serializers.CharField(source="customer.phone", read_only=True)
    customer_address = serializers.CharField(source="customer.address", read_only=True)
    driver_name = serializers.CharField(source="trip.driver.name", read_only=True, default="")
    vehicle_reg = serializers.CharField(source="trip.vehicle.reg_no", read_only=True, default="")
    delivery_person = serializers.CharField(source="trip.delivery_rep.full_name", read_only=True, default="")
    payment_terms = serializers.SerializerMethodField()
    invoices = serializers.SerializerMethodField()
    delivery_notes = serializers.SerializerMethodField()

    def get_payment_terms(self, obj):
        days = getattr(obj.customer, "credit_days", 0) or 0
        return f"{days} days credit" if days else "Cash on delivery"

    class Meta(SalesOrderListSerializer.Meta):
        fields = SalesOrderListSerializer.Meta.fields + ["gst_pct", "payment_terms", "delivery_person", "gps", "gps_accuracy_m", "version", "price_list", "currency", "subtotal", "discount", "scheme_discount", "tax1_total", "tax2_total", "fed_total", "extra_tax_total", "further_tax_total", "wht_total", "rejection_reason", "confirmed_by", "confirmed_by_name", "confirmed_at", "dispatched_at", "delivered_at", "price_flags", "cost_centre", "apply_customer_discount", "discount_pct", "notes", "lines", "created_by_name", "customer_phone", "customer_address", "driver_name", "vehicle_reg", "invoices", "delivery_notes", "updated_at"]
        read_only_fields = ["id", "number", "version", "status", "currency", "subtotal", "discount", "scheme_discount", "tax1_total", "tax2_total", "fed_total", "extra_tax_total", "further_tax_total", "wht_total", "total", "trip", "rejection_reason", "confirmed_by", "confirmed_at", "dispatched_at", "delivered_at", "price_flags", "hold_reason", "gps", "gps_accuracy_m", "created_at", "updated_at"]

    def get_gps(self, obj):
        return {"lat": float(obj.gps_lat), "lng": float(obj.gps_lng)} if obj.gps_lat is not None else None

    def get_invoices(self, obj):
        return [{"id": str(i.id), "number": i.number, "status": i.status, "total": str(i.total), "paid_amount": str(i.paid_amount)} for i in obj.invoices.all()]

    def get_delivery_notes(self, obj):
        return [{"id": str(d.id), "number": d.number, "status": d.status, "trip": d.trip.number if d.trip_id else None, "delivered_at": d.delivered_at, "invoiced": getattr(d, "invoice", None) is not None, "invoice_number": d.invoice.number if getattr(d, "invoice", None) is not None else ""} for d in obj.delivery_notes.all()]


class InvoiceLineSerializer(serializers.ModelSerializer):
    hs_code = serializers.CharField(source="variant.product.fbr_hs_code", read_only=True, default="")
    fbr_rate = serializers.CharField(source="variant.product.fbr_rate_desc", read_only=True, default="")
    # with fbr_rate, these let the draft editor preview GST live the way engine.compute_line will charge it
    fbr_sale_type = serializers.CharField(source="variant.product.fbr_sale_type", read_only=True, default="")
    mrp = serializers.DecimalField(source="variant.product.mrp_ex_tax", max_digits=14, decimal_places=4, read_only=True, default=None)
    product_fed_rate = serializers.DecimalField(source="variant.product.fbr_fed_rate", max_digits=7, decimal_places=3, read_only=True, default=None)
    product_extra_tax_rate = serializers.DecimalField(source="variant.product.fbr_extra_tax_rate", max_digits=7, decimal_places=3, read_only=True, default=None)
    product_further_tax_rate = serializers.DecimalField(source="variant.product.fbr_further_tax_rate", max_digits=7, decimal_places=3, read_only=True, default=None)
    product_wht_rate = serializers.DecimalField(source="variant.product.fbr_wht_rate", max_digits=7, decimal_places=3, read_only=True, default=None)
    variant_name = serializers.CharField(source="variant.name", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True, default="")
    tax1_name = serializers.CharField(source="tax1_type.name", read_only=True, default="")
    tax2_name = serializers.CharField(source="tax2_type.name", read_only=True, default="")

    class Meta:
        model = SalesInvoiceLine
        fields = ["id", "variant", "variant_name", "variant_sku", "batch", "batch_no", "description", "qty", "free_qty", "unit_price", "unit_cost", "discount_amount", "product_discount_pct", "tax_pct_override", "hs_code", "fbr_rate", "fbr_sale_type", "mrp", "product_fed_rate", "product_extra_tax_rate", "product_further_tax_rate", "product_wht_rate", "tax1_name", "tax1_rate", "tax1_amount", "tax2_name", "tax2_rate", "tax2_amount", "fed_amount", "extra_tax_amount", "further_tax_amount", "wht_amount", "line_total"]


class SalesInvoiceEditSerializer(serializers.ModelSerializer):
    """
    The parts of a posted invoice that may still be corrected: when it falls due, and what is written on it.

    Not the amounts. An invoice is posted to the ledger the moment it is raised, and its figures are owed by the
    customer and sitting in receivables — changing them behind the journal would put the two out of step with
    nothing to show why. A wrong amount is corrected with a credit note, which is a document the customer also
    holds. Not the date either: that is the journal's date and moves the entry between accounting periods.
    """

    # a draft may also have its date and its prices corrected; a posted one may not, and says why
    date = serializers.DateField(required=False)
    lines = serializers.ListField(child=serializers.DictField(), required=False, write_only=True)

    class Meta:
        model = SalesInvoice
        fields = ["due_date", "notes", "date", "lines"]

    def validate(self, attrs):
        draft_only = [f for f in ("date", "lines") if f in attrs]
        if draft_only and self.instance and self.instance.status != "draft":
            raise serializers.ValidationError({
                draft_only[0]: [
                    f"{self.instance.number} is already posted, so its {'date' if draft_only[0] == 'date' else 'figures'} "
                    "cannot change — the entry is in the ledger and the amount is owed by the customer. Raise a credit "
                    "note instead; the due date and the notes can still be corrected here."
                ]
            })
        return attrs

    def validate_due_date(self, value):
        inv = self.instance
        if value and inv and value < inv.date:
            raise serializers.ValidationError(f"The due date cannot be before the invoice date ({inv.date:%d/%m/%Y}).")
        return value


class SalesInvoiceSerializer(serializers.ModelSerializer):
    gst_pct = serializers.SerializerMethodField()   # "18%" — the GST % beside GST (owner, 27 Oct 2026)

    def get_gst_pct(self, obj):
        from .services import gst_pct

        return gst_pct(l.tax1_rate for l in obj.lines.all())

    fbr = serializers.SerializerMethodField()

    fbr_setup = serializers.SerializerMethodField()

    def get_fbr_setup(self, inv):
        """Whether this company can file with FBR right now, and where — never the tokens themselves."""
        from apps.fbr.models import FBRSettings

        if "_fbr_setup" not in self.context:   # one read per list, not one per invoice
            s = FBRSettings.objects.first()
            self.context["_fbr_setup"] = ({"configured": False, "sandbox": True} if s is None else
                                          {"configured": bool(s.sandbox_token if s.use_sandbox else s.production_token), "sandbox": bool(s.use_sandbox)})
        return self.context["_fbr_setup"]

    def get_fbr(self, inv):
        f = getattr(inv, "fbr_filing", None)
        if f is None:
            return None
        return {"status": f.status, "sandbox": f.sandbox, "fbr_invoice_number": f.fbr_invoice_number,
                "validated_at": f.validated_at, "submitted_at": f.submitted_at, "error": f.error_text}

    cash_paid = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)
    credit_applications = serializers.SerializerMethodField()

    def get_credit_applications(self, obj):
        """The returns that settled this invoice (owner, 30 Oct 2026: a return is not a payment)."""
        return [{"credit_note": str(a.credit_note_id), "number": a.credit_note.number, "amount": str(a.amount), "date": a.date,
                 "own": a.credit_note.invoice_id == obj.pk}   # a return of this invoice's goods, not credit brought from another
                for a in obj.credit_applications.all()]

    returns_count = serializers.SerializerMethodField()

    def get_returns_count(self, obj):
        """Live returns (drafts included) against this invoice — while any stands, it cannot be reversed."""
        n = getattr(obj, "live_returns", None)
        return n if n is not None else obj.credit_notes.exclude(status="cancelled").count()

    lines = InvoiceLineSerializer(many=True, read_only=True)
    company_claims_total = serializers.SerializerMethodField()
    delivery_status = serializers.SerializerMethodField()
    customer_name = serializers.CharField(source="customer.name", read_only=True)

    def get_delivery_status(self, obj):
        """Where the goods actually are — the invoice's own status stays the payment state."""
        dn = obj.delivery_note
        if dn is None:
            return ""
        if dn.status in ("delivered", "partially_delivered", "returned", "failed"):
            return dn.get_status_display()
        if dn.trip_id:
            return f"On trip {dn.trip.number}" if dn.trip.status in ("planned", "loading") else f"Out for delivery · {dn.trip.number}" if dn.trip.status == "out_for_delivery" else dn.trip.get_status_display()
        return "Issued, awaiting dispatch"

    def get_company_claims_total(self, obj):
        return str(sum((c.total for c in obj.company_claims.exclude(status="cancelled")), __import__("decimal").Decimal("0")))

    customer_code = serializers.CharField(source="customer.code", read_only=True)
    order_number = serializers.CharField(source="order.number", read_only=True, default="")
    delivery_note_number = serializers.CharField(source="delivery_note.number", read_only=True, default="")
    # the load form the stock was issued on — keeps the chain order → load form → issue → invoice readable
    picklist = serializers.UUIDField(source="delivery_note.picklist_id", read_only=True, default=None)
    picklist_number = serializers.CharField(source="delivery_note.picklist.number", read_only=True, default="")
    trip = serializers.UUIDField(source="delivery_note.trip_id", read_only=True, default=None)
    trip_number = serializers.CharField(source="delivery_note.trip.number", read_only=True, default="")
    rep_name = serializers.CharField(source="rep.name", read_only=True, default="")
    sales_person = serializers.SerializerMethodField()
    balance = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)
    journal_number = serializers.CharField(source="journal.number", read_only=True, default="")
    tax1_name = serializers.CharField(source="tax1_type.name", read_only=True, default="")
    tax2_name = serializers.CharField(source="tax2_type.name", read_only=True, default="")

    
    def get_sales_person(self, obj):
        if obj.rep_id:
            return obj.rep.name
        order = obj.order
        if order:
            if order.rep_id:
                return order.rep.name
            if order.trip_id and order.trip.rep_id:
                return order.trip.rep.name
        dn = obj.delivery_note
        if dn and dn.trip_id and dn.trip.rep_id:
            return dn.trip.rep.name
        return ""

    class Meta:
        model = SalesInvoice
        fields = ["id", "number", "version", "gst_pct", "credited_amount", "cash_paid", "credit_applications", "returns_count", "customer", "customer_name", "customer_code", "order", "order_number", "delivery_note", "delivery_note_number", "picklist", "picklist_number", "trip", "trip_number", "rep", "rep_name", "sales_person", "date", "due_date", "subtotal", "discount", "discount_pct", "company_claims_total", "tax1_type", "tax1_name", "tax1_total", "tax2_type", "tax2_name", "tax2_total", "fed_total", "extra_tax_total", "further_tax_total", "wht_total", "force_tax_type", "force_tax_rate", "fbr", "fbr_setup", "total", "paid_amount", "balance", "status", "delivery_status", "journal", "journal_number", "notes", "lines", "created_at"]
        read_only_fields = fields


class CreditNoteLineSerializer(serializers.ModelSerializer):
    variant_name = serializers.CharField(source="variant.name", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)

    class Meta:
        model = CreditNoteLine
        fields = ["id", "variant", "variant_name", "variant_sku", "batch", "qty", "unit_price", "unit_cost", "reason", "restock", "condition", "discount_amount", "stock_back", "tax1_rate", "tax1_amount", "tax2_amount", "fed_amount", "extra_tax_amount", "further_tax_amount", "wht_amount", "line_total"]
        read_only_fields = ["id", "discount_amount", "stock_back", "tax1_rate", "tax1_amount", "tax2_amount", "fed_amount", "extra_tax_amount", "further_tax_amount", "wht_amount", "line_total"]
        extra_kwargs = {"unit_cost": {"required": False}}


class CreditNoteSerializer(serializers.ModelSerializer):
    gst_pct = serializers.SerializerMethodField()   # "18%" — the GST % beside GST (owner, 27 Oct 2026)

    def get_gst_pct(self, obj):
        from .services import gst_pct

        return gst_pct(l.tax1_rate for l in obj.lines.all())

    available_credit = serializers.DecimalField(max_digits=18, decimal_places=4, read_only=True)
    applications = serializers.SerializerMethodField()
    refunds = serializers.SerializerMethodField()

    def get_applications(self, obj):
        return [{"invoice": str(a.invoice_id), "invoice_number": a.invoice.number, "amount": str(a.amount), "date": a.date}
                for a in obj.applications.select_related("invoice")]

    def get_refunds(self, obj):
        return [{"id": str(p.id), "number": p.number, "date": p.date, "amount": str(p.amount), "method": p.method, "status": p.status}
                for p in obj.refunds.exclude(status="cancelled")]

    lines = CreditNoteLineSerializer(many=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    invoice_number = serializers.CharField(source="invoice.number", read_only=True, default="")
    journal_number = serializers.CharField(source="journal.number", read_only=True, default="")

    class Meta:
        model = CreditNote
        fields = ["id", "number", "version", "gst_pct", "customer", "customer_name", "invoice", "invoice_number", "warehouse", "date", "reason", "subtotal", "tax1_total", "tax2_total", "fed_total", "extra_tax_total", "further_tax_total", "wht_total", "total", "status", "journal", "journal_number", "applied_to_invoice", "notes", "lines", "created_at",
                  "discount", "applied_amount", "refunded_amount", "available_credit", "applications", "refunds"]
        read_only_fields = ["id", "number", "version", "subtotal", "tax1_total", "tax2_total", "fed_total", "extra_tax_total", "further_tax_total", "wht_total", "total", "status", "journal", "created_at", "discount", "applied_amount", "refunded_amount"]


class RepCollectionSerializer(serializers.ModelSerializer):
    rep_name = serializers.CharField(source="rep.name", read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    settlement_number = serializers.CharField(source="settlement.number", read_only=True, default="")
    recorded_by_name = serializers.CharField(source="recorded_by.full_name", read_only=True, default="")
    gps = serializers.DictField(write_only=True, required=False)

    class Meta:
        model = RepCollection
        fields = ["id", "number", "rep", "rep_name", "customer", "customer_name", "date", "amount", "method", "reference", "allocations", "status", "journal", "settlement", "settlement_number", "recorded_by_name", "gps_lat", "gps_lng", "gps_accuracy_m", "gps", "notes", "created_at"]
        read_only_fields = ["id", "number", "status", "journal", "settlement", "gps_lat", "gps_lng", "gps_accuracy_m", "created_at"]
        extra_kwargs = {"rep": {"required": False}, "allocations": {"required": False}}


class RepSettlementSerializer(serializers.ModelSerializer):
    rep_name = serializers.CharField(source="rep.name", read_only=True)
    received_by_name = serializers.CharField(source="received_by.full_name", read_only=True, default="")
    collections = serializers.PrimaryKeyRelatedField(queryset=RepCollection.objects.all(), many=True, write_only=True)
    collection_numbers = serializers.SerializerMethodField()
    journal_number = serializers.CharField(source="journal.number", read_only=True, default="")

    class Meta:
        model = RepSettlement
        fields = ["id", "number", "rep", "rep_name", "date", "amount", "received_by", "received_by_name", "cash_account", "bank_account", "journal", "journal_number", "collections", "collection_numbers", "notes", "created_at"]
        read_only_fields = ["id", "number", "amount", "received_by", "journal", "created_at"]

    def get_collection_numbers(self, obj):
        return [{"id": str(c.id), "number": c.number, "customer": c.customer.name, "amount": str(c.amount), "date": c.date} for c in obj.collections.select_related("customer")]
