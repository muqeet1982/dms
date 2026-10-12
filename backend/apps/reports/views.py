from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import DomainError
from apps.accounts.page_permissions import extra_perm
from apps.core.permissions import IsTenantUser, has_tenant_perm
from apps.core.plan_limits import effective_features
from apps.core.viewsets import TenantModelViewSet

from .exporters import to_csv, to_pdf, to_xlsx
from .models import ReportSchedule, SavedReportFilter
from .registry import REPORTS, catalog

DATE_PARAMS = {"date_from", "date_to", "as_of", "date", "compare_from", "compare_to"}


class ReportCatalogView(APIView):
    permission_classes = [IsTenantUser]
    required_perm = "reports.view_report"  # the Reports page's View switch; each report still has its own

    def get(self, request):
        return Response([r for r in catalog() if has_tenant_perm(request, r["permission"])])


class ReportRunView(APIView):
    """GET /reports/{key}?params...&format=json|csv|xlsx|pdf"""

    permission_classes = [IsTenantUser]
    required_perm = "reports.view_report"

    def get(self, request, key):
        spec = REPORTS.get(key)
        if spec is None:
            raise DomainError(f"Unknown report {key}", status_code=404)
        if not has_tenant_perm(request, spec["perm"]):
            raise DomainError("You cannot view this report.", status_code=403)
        from datetime import date

        params = {}
        for p in spec["params"]:
            v = request.query_params.get(p["name"])
            if v in (None, ""):
                if p.get("default") is not None:
                    params[p["name"]] = p["default"]
                continue
            if p["type"] == "date" or p["name"] in DATE_PARAMS:
                v = date.fromisoformat(v)
            elif p["type"] == "number":
                v = int(v)
            params[p["name"]] = v
        result = spec["query"](**params)
        fmt = request.query_params.get("format", "json")
        if fmt == "json":
            return Response({"key": key, "title": spec["title"], "params": {k: str(v) for k, v in params.items()}, **result})
        if not has_tenant_perm(request, "reports.export_reports"):
            raise DomainError("You cannot export reports.", status_code=403)
        if fmt == "xlsx" and not effective_features(request.tenant).get("excel_export"):
            raise DomainError("Excel export is not enabled for your plan.", status_code=403)
        if fmt == "csv":
            data, ct = to_csv(result, spec["title"]), "text/csv"
        elif fmt == "xlsx":
            data, ct = to_xlsx(result, spec["title"]), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif fmt == "pdf":
            data, ct = to_pdf(result, spec["title"], request.tenant, {k: str(v) for k, v in params.items()}), "application/pdf"
        else:
            raise DomainError("format must be json, csv, xlsx or pdf", status_code=400)
        resp = HttpResponse(data, content_type=ct)
        resp["Content-Disposition"] = f'{"inline" if fmt == "pdf" else "attachment"}; filename="{key}.{fmt}"'
        return resp


class SavedFilterViewSet(TenantModelViewSet):
    pagination_class = None

    def get_serializer_class(self):
        from rest_framework import serializers

        class S(serializers.ModelSerializer):
            class Meta:
                model = SavedReportFilter
                fields = ["id", "report_key", "name", "params"]

        return S

    def get_queryset(self):
        return SavedReportFilter.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class ReportScheduleViewSet(TenantModelViewSet):
    pagination_class = None
    required_perm = extra_perm("reports", "export")

    def get_serializer_class(self):
        from rest_framework import serializers

        class S(serializers.ModelSerializer):
            class Meta:
                model = ReportSchedule
                fields = ["id", "report_key", "name", "params", "frequency", "format", "recipients", "is_active", "last_run_at"]
                read_only_fields = ["id", "last_run_at"]

        return S

    def get_queryset(self):
        return ReportSchedule.objects.all()

    def perform_create(self, serializer):
        from apps.core.plan_limits import PlanLimitService

        PlanLimitService.require_feature(self.request.tenant, "report_scheduling")
        super().perform_create(serializer)


class TenantDashboardView(APIView):
    """Owner dashboard KPIs (§4 Dashboard)."""

    permission_classes = [IsTenantUser]

    def get(self, request):
        from decimal import Decimal

        from django.db.models import Count, Sum
        from django.utils import timezone

        from apps.accounting.models import CashAccount, Payment
        from apps.accounting.services import ReportQueries
        from apps.delivery.models import DeliveryTrip
        from apps.inventory.models import InventoryAlert
        from apps.purchasing.models import PurchaseBill
        from apps.sales.models import RepCollection, SalesInvoice, SalesOrder

        today = timezone.localdate()
        month_start = today.replace(day=1)
        last_month_start = (month_start - timezone.timedelta(days=1)).replace(day=1)
        m = request.membership
        can = lambda p: m.is_owner or m.has_perm(p)  # noqa: E731
        data = {"date": today}
        if can("sales.view_sales_order"):
            orders = SalesOrder.objects.all()
            data["orders"] = {"today": orders.filter(order_date=today).count(), "inbox": orders.filter(status__in=["submitted", "under_review", "on_hold_credit", "on_hold_stock"]).count(), "confirmed": orders.filter(status="confirmed").count(), "dispatched": orders.filter(status__in=["dispatched", "partially_delivered"]).count(), "by_status": dict(orders.filter(order_date__gte=month_start).values_list("status").annotate(c=Count("id")).values_list("status", "c"))}
            inv = SalesInvoice.objects.exclude(status="cancelled")
            this_m = inv.filter(date__gte=month_start).aggregate(s=Sum("total"))["s"] or Decimal("0")
            last_m = inv.filter(date__gte=last_month_start, date__lt=month_start).aggregate(s=Sum("total"))["s"] or Decimal("0")
            data["sales"] = {"month": str(this_m), "last_month": str(last_m), "change_pct": str(((this_m - last_m) / last_m * 100).quantize(Decimal("0.1")) if last_m else Decimal("0")), "today": str(inv.filter(date=today).aggregate(s=Sum("total"))["s"] or 0)}
            data["sales_trend"] = [{"date": r["date"].isoformat(), "total": str(r["s"] or 0)} for r in inv.filter(date__gte=today - timezone.timedelta(days=29)).values("date").annotate(s=Sum("total")).order_by("date")]
        if can("delivery.view_all_trips"):
            data["trips"] = {"out": DeliveryTrip.objects.filter(status="out_for_delivery").count(), "planned": DeliveryTrip.objects.filter(status__in=["planned", "loading"], date__gte=today).count(), "to_close": DeliveryTrip.objects.filter(status="completed").count()}
        if can("inventory.view_stock"):
            alerts = InventoryAlert.objects.filter(resolved_at__isnull=True)
            data["inventory"] = {"low_stock": alerts.filter(type="low_stock").count(), "expiring": alerts.filter(type="expiring").count(), "expired": alerts.filter(type="expired").count(), "negative": alerts.filter(type="negative").count()}
        if can("cash.view_cash"):
            cash = sum((ReportQueries.account_balance(c.account) for c in CashAccount.objects.select_related("account")), Decimal("0"))
            rec = SalesInvoice.objects.filter(status__in=["posted", "partially_paid", "overdue"]).aggregate(t=Sum("total"), p=Sum("paid_amount"))
            pay = PurchaseBill.objects.filter(status__in=["posted", "partially_paid"]).aggregate(t=Sum("total"), p=Sum("paid_amount"))
            data["cash"] = {"in_hand": str(cash), "with_reps": str(RepCollection.objects.filter(status="held_by_rep").aggregate(s=Sum("amount"))["s"] or 0), "receivables": str((rec["t"] or 0) - (rec["p"] or 0)), "receivables_overdue": str(sum((i.total - i.paid_amount for i in SalesInvoice.objects.filter(status__in=["posted", "partially_paid", "overdue"], due_date__lt=today)), Decimal("0"))), "payables": str((pay["t"] or 0) - (pay["p"] or 0)), "collected_today": str(Payment.objects.filter(direction="in", date=today, status="posted").aggregate(s=Sum("amount"))["s"] or 0)}
        if can("sales.view_sales_order"):
            top = SalesInvoice.objects.filter(date__gte=month_start).exclude(status="cancelled").values("customer__name").annotate(s=Sum("total")).order_by("-s")[:5]
            data["top_customers"] = [{"name": r["customer__name"], "total": str(r["s"])} for r in top]
        if m.supplier_rep_id:
            rep = m.supplier_rep
            my_orders = SalesOrder.objects.filter(rep=rep)
            data["rep"] = {"orders_month": my_orders.filter(order_date__gte=month_start).count(), "order_value_month": str(my_orders.filter(order_date__gte=month_start).exclude(status__in=["cancelled", "rejected"]).aggregate(s=Sum("total"))["s"] or 0), "target": str(rep.monthly_target), "cash_held": str(RepCollection.objects.filter(rep=rep, status="held_by_rep").aggregate(s=Sum("amount"))["s"] or 0), "customers": rep.customers.count(), "pending": my_orders.filter(status__in=SalesOrder.OPEN_STATUSES).count()}
        return Response(data)
