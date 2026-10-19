"""
The office's view of where the field force has been: one person's day, the team's day, and who is where now.

Location history is employee data, so every read of somebody else's track is written to the audit log with the
date that was looked at and who looked.
"""
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import AuditService
from apps.core.exceptions import DomainError
from apps.core.permissions import IsTenantUser, has_tenant_perm

from .tracking import TrackingService, day_for, policy, purge

VIEW_TEAM = "tracking.view_team"
VISITS_OWN = "visits.view_own"
VISITS_TEAM = "visits.view_team"
VIEW_OWN = "tracking.view_own"
MANAGE = "tracking.manage_policy"


def _date(request):
    return parse_date(request.query_params.get("date") or "") or timezone.localdate()


def _may_see(request, user_id):
    """Your own track needs nothing; anyone else's needs `tracking.view_team`."""
    if str(request.user.pk) == str(user_id):
        return
    if not has_tenant_perm(request, VIEW_TEAM):
        raise DomainError("You can only see your own route.", code="forbidden", status_code=403)


def _field_users():
    """Everyone who has ever sent a fix — the list the office picks from."""
    from apps.accounts.models import User

    from .models import LocationPing

    ids = LocationPing.objects.values_list("user_id", flat=True).distinct()
    return User.objects.filter(pk__in=list(ids))


class TrackingUserDayView(APIView):
    """`GET /api/v1/tracking/users/{user_id}?date=&precision=full` — one person's route, stops and events."""

    permission_classes = [IsTenantUser]

    def get(self, request, user_id=None):
        from apps.accounts.models import User

        from .models import SupplierRep

        _may_see(request, user_id)
        user = User.objects.filter(pk=user_id).first()
        if user is None:
            raise DomainError("No such person.", status_code=404)
        rep = SupplierRep.objects.filter(user=user).first()
        on = _date(request)
        day = day_for(user, on, rep=rep, full=request.query_params.get("precision") == "full")
        if str(request.user.pk) != str(user_id):
            AuditService.log("tracking.viewed", user, after={"date": str(on), "points": day["points_total"]}, request=request)
        return Response(day)


class TrackingTeamView(APIView):
    """`GET /api/v1/tracking/users?date=` — one row per person, no point arrays."""

    permission_classes = [IsTenantUser]

    def get(self, request):
        if not has_tenant_perm(request, VIEW_TEAM):
            raise DomainError("You can only see your own route.", code="forbidden", status_code=403)
        from .models import SupplierRep

        on = _date(request)
        reps = {r.user_id: r for r in SupplierRep.objects.filter(user__isnull=False)}
        rows = []
        for user in _field_users():
            points = TrackingService.points_for(user, on)
            if not points:
                continue
            rep = reps.get(user.pk)
            events = TrackingService.events_for(user, rep, on)
            rows.append({
                "user_id": str(user.pk), "user_name": user.full_name or user.username,
                "role": "rep" if rep else "staff",
                "first_at": points[0].at, "last_at": points[-1].at,
                "distance_m": TrackingService.distance_of(points),
                "stops": len(TrackingService.stops_of(points, TrackingService.customers_with_pins(rep))),
                "orders": sum(1 for e in events if e["type"] == "order"),
                "visits": sum(1 for e in events if e["type"] == "visit"),
                "last_lat": float(points[-1].lat), "last_lng": float(points[-1].lng),
                "last_seen_at": points[-1].at,
            })
        AuditService.log("tracking.viewed_team", request.user, after={"date": str(on), "people": len(rows)}, request=request)
        return Response({"date": on, "results": sorted(rows, key=lambda r: r["user_name"])})


class TrackingLiveView(APIView):
    """`GET /api/v1/tracking/live` — the latest fix per person, for the office map. Poll it every 20–30 seconds."""

    permission_classes = [IsTenantUser]

    def get(self, request):
        if not has_tenant_perm(request, VIEW_TEAM):
            raise DomainError("You can only see your own route.", code="forbidden", status_code=403)
        from .models import LocationPing

        now = timezone.now()
        since = now - timezone.timedelta(hours=12)
        latest = {}
        for ping in LocationPing.objects.filter(at__gte=since).select_related("user").order_by("at"):
            latest[ping.user_id] = ping          # ordered by time, so the last one wins
        users = []
        for ping in latest.values():
            users.append({
                "user_id": str(ping.user_id), "user_name": ping.user.full_name or ping.user.username,
                "lat": float(ping.lat), "lng": float(ping.lng), "at": ping.at,
                "accuracy_m": ping.accuracy_m, "battery_pct": ping.battery_pct,
                "stale_s": int((now - ping.at).total_seconds()),
                "moving": bool(ping.speed_mps and ping.speed_mps > 1.0),
                # which build this phone last ran — the owner reads this list to know when every rep is on a
                # shift-capable app and the order gate can be switched on
                "app_version": ping.app_version or None,
            })
        return Response({"as_of": now, "users": sorted(users, key=lambda u: u["user_name"])})


class VisitsView(APIView):
    """
    `GET /api/v1/visits?date=&user=&rep=&customer=&outcome=&page=` — who was visited and what came of it.

    It sits with tracking because it answers the same question — where was the rep — and is gated the same
    way: your own visits need nothing, everyone else's needs `visits.view_team`.

    `summary` is the row a distributor actually reads: calls, orders, no-orders and the reasons behind them,
    per person. A beat of twelve calls with four orders and eight "still has stock" is a different day from
    four calls with four orders, and both produce four orders.
    """

    permission_classes = [IsTenantUser]
    PAGE_SIZE = 100

    def get(self, request):
        from apps.partners.models import CustomerVisit, SupplierRep
        from apps.partners.visits import VisitService

        from .tracking import TrackingService

        qs = CustomerVisit.objects.select_related("customer", "order", "rep", "user")
        team = has_tenant_perm(request, VISITS_TEAM)
        if not team:
            if not has_tenant_perm(request, VISITS_OWN):
                raise DomainError("You cannot see shop visits.", code="forbidden", status_code=403)
            qs = qs.filter(user=request.user)
        for field, param in (("user_id", "user"), ("rep_id", "rep"), ("customer_id", "customer"), ("outcome", "outcome")):
            value = request.query_params.get(param)
            if not value:
                continue
            try:
                qs = qs.filter(**{field: value})
            except (ValidationError, ValueError):
                # a filter the caller typed wrong is a 400, not a 500 on an unparseable id
                raise DomainError(f"{param} is not valid.", status_code=400)
        on = _date(request) if request.query_params.get("date") else None
        if on:
            start, end = TrackingService.day_range(on)
            qs = qs.filter(at__gte=start, at__lt=end)

        total = qs.count()
        try:
            page = max(1, int(request.query_params.get("page") or 1))
        except ValueError:
            page = 1
        window = qs[(page - 1) * self.PAGE_SIZE: page * self.PAGE_SIZE]
        rows = [VisitService.serialize(v) for v in window]
        if team:
            AuditService.log("visits.viewed", request.user, after={"date": str(on or ""), "rows": len(rows)}, request=request)
        return Response({
            "date": on, "count": total, "page": page, "page_size": self.PAGE_SIZE,
            "results": rows,
            "no_order": sum(1 for r in rows if r["outcome"] == "no_order"),
            "summary": self._summary(qs),
            "reps": [{"id": str(r.pk), "name": r.name} for r in SupplierRep.objects.filter(user__isnull=False)[:200]],
        })

    @staticmethod
    def _summary(qs):
        """One row per person: calls, orders, no-orders, and the reasons — computed in the database."""
        from django.db.models import Count, Q

        from apps.partners.models import CustomerVisit

        people = (qs.values("user_id", "user__full_name", "user__username", "rep__name")
                    .annotate(visits=Count("id"),
                              orders=Count("id", filter=Q(outcome="order")),
                              no_orders=Count("id", filter=Q(outcome="no_order")))
                    .order_by("-visits"))
        reasons = {}
        for row in qs.exclude(reason="").values("user_id", "reason").annotate(n=Count("id")):
            reasons.setdefault(row["user_id"], {})[row["reason"]] = row["n"]
        labels = dict(CustomerVisit.REASONS)
        return [{
            "user_id": str(p["user_id"]) if p["user_id"] else None,
            "user_name": p["rep__name"] or p["user__full_name"] or p["user__username"] or "",
            "visits": p["visits"], "orders": p["orders"], "no_orders": p["no_orders"],
            "by_reason": reasons.get(p["user_id"], {}),
            "by_reason_label": {labels.get(k, k): n for k, n in reasons.get(p["user_id"], {}).items()},
        } for p in people]


class TrackingPolicyView(APIView):
    """`GET/PATCH /api/v1/tracking/policy` — what the office tells the phones to do."""

    permission_classes = [IsTenantUser]

    def get(self, request):
        from apps.core.tenancy import get_current_tenant

        return Response(policy(get_current_tenant()))

    def patch(self, request):
        from apps.core.tenancy import get_current_tenant
        from apps.partners.tracking import DEFAULTS

        if not has_tenant_perm(request, MANAGE):
            raise DomainError("You cannot change the tracking policy.", code="forbidden", status_code=403)
        tenant = get_current_tenant()
        changed = {k: v for k, v in request.data.items() if k in DEFAULTS}
        tenant.settings = {**(tenant.settings or {}), **changed}
        tenant.save(update_fields=["settings"])
        AuditService.log("tracking.policy_changed", tenant, after=changed, request=request)
        return Response(policy(tenant))


class TrackingPurgeView(APIView):
    """`POST /api/v1/tracking/purge-old` — drop history past the retention the company set."""

    permission_classes = [IsTenantUser]

    def post(self, request):
        from apps.core.tenancy import get_current_tenant

        if not has_tenant_perm(request, MANAGE):
            raise DomainError("You cannot purge location history.", code="forbidden", status_code=403)
        tenant = get_current_tenant()
        out = purge(tenant, request.data.get("days"))
        AuditService.log("tracking.purged", tenant, after=out, request=request)
        return Response(out)


from apps.core.tenancy import get_current_tenant  # noqa: E402


class ShiftTimesheetView(APIView):
    """
    `GET /api/v1/shifts?date=&rep=` — one row per shift: the rep's day as the office reads it (effective
    times, breaks, working minutes, distance, visits, orders, how it ended, and whether it was corrected).
    `PATCH /api/v1/shifts/{id}` is `ShiftCorrectView` below.
    """

    permission_classes = [IsTenantUser]

    def get(self, request):
        if not has_tenant_perm(request, "tracking.view_timesheet"):
            raise DomainError("Timesheets are an office screen.", code="forbidden", status_code=403)
        from apps.sales.models import SalesOrder

        from .models import CustomerVisit, RepShift
        from .shifts import ShiftService
        from .tracking import TrackingService

        on = parse_date(request.query_params.get("date") or "") or timezone.localdate()
        start, end = TrackingService.day_range(on)
        qs = RepShift.objects.filter(started_at__gte=start, started_at__lt=end).select_related("rep", "corrected_by").prefetch_related("breaks")
        if request.query_params.get("rep"):
            qs = qs.filter(rep_id=request.query_params["rep"])
        tenant = get_current_tenant()
        rows = []
        for s in qs.order_by("rep__name", "started_at"):
            window_end = s.ended_at or timezone.now()
            rows.append({
                "id": str(s.id), "rep": str(s.rep_id), "rep_name": s.rep.name, "rep_code": s.rep.code,
                "status": s.status, "tracking_state": ShiftService.tracking_state(s, tenant),
                "started_at": s.started_at, "ended_at": s.ended_at, "end_reason": s.end_reason or None,
                "recorded_started_at": s.recorded_started_at, "recorded_ended_at": s.recorded_ended_at,
                "breaks": [{"id": str(b.id), "started_at": b.started_at, "ended_at": b.ended_at,
                            "recorded_started_at": b.recorded_started_at, "recorded_ended_at": b.recorded_ended_at} for b in s.breaks.all()],
                "working_minutes": ShiftService.working_minutes(s),
                "distance_m": int(TrackingService.distance_of(list(s.pings.order_by("at")))),
                "visits": CustomerVisit.objects.filter(rep=s.rep, at__gte=s.started_at, at__lt=window_end).count(),
                "orders": SalesOrder.objects.filter(rep=s.rep, created_at__gte=s.started_at, created_at__lt=window_end).count(),
                "mocked_pings": s.pings.filter(mocked=True).count(),
                "checks": s.checks, "device": s.device,
                "corrected": s.corrected, "corrected_by": getattr(s.corrected_by, "full_name", None),
                "corrected_at": s.corrected_at, "correction_reason": s.correction_reason,
            })
        return Response({"date": on, "results": rows})


class ShiftCorrectView(APIView):
    """`PATCH /api/v1/shifts/{id}` — the office corrects a rep's times. Reason required; originals kept."""

    permission_classes = [IsTenantUser]

    def patch(self, request, pk):
        if not has_tenant_perm(request, "tracking.change_timesheet"):
            raise DomainError("Correcting times needs the timesheet edit right.", code="forbidden", status_code=403)
        from .models import RepShift
        from .shifts import ShiftService

        shift = RepShift.objects.filter(pk=pk).select_related("rep").first()
        if shift is None:
            raise DomainError("Shift not found.", status_code=404)
        d = request.data or {}
        ShiftService.correct(shift, started_at=d.get("started_at"), ended_at=d.get("ended_at"),
                             breaks=d.get("breaks") or [], reason=d.get("reason") or "", actor=request.user)
        return Response({"id": str(shift.id), "started_at": shift.started_at, "ended_at": shift.ended_at,
                         "corrected": shift.corrected, "correction_reason": shift.correction_reason})
