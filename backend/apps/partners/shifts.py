"""
The rep's working day as a shift — the owner's attendance model (20 Oct 2026).

The rules, in his words: Start shift / Break / Back / End on the home screen; tracking runs while the shift is
on (never during a break — his explicit call); if tracking cannot run the shift cannot start (the app enforces
that and sends its `checks` as evidence); no shift, no order; tracking switched off ends the shift.

The server's unique job is the silence: a phone whose tracker was killed cannot report its own death, so a
sweep watches open shifts for a gap with no ping — measured only across the *on* segments, because a silent
phone on a break is the rule working, not a dead tracker — alerts the office and the rep, and ends the shift
if the silence goes on. A client `at` is accepted up to 12 hours old (a rep in a dead zone still starts work;
the app queues and replays) and never meaningfully in the future.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.core.audit import AuditService
from apps.core.exceptions import DomainError
from apps.core.tenancy import get_current_tenant, get_current_user

from .models import RepShift, RepShiftBreak

MAX_AT_AGE_H = 12
FUTURE_SKEW_S = 300


def _client_at(at):
    """The device's own time for the event, bounded so a wrong clock cannot write fiction."""
    from django.utils.dateparse import parse_datetime

    now = timezone.now()
    if not at:
        return now
    t = parse_datetime(str(at)) if not hasattr(at, "tzinfo") else at
    if t is None:
        raise DomainError("`at` is not a datetime.", code="bad_at", status_code=400)
    if t.tzinfo is None:
        t = timezone.make_aware(t)
    if t > now + timedelta(seconds=FUTURE_SKEW_S):
        raise DomainError("`at` is in the future.", code="bad_at", status_code=400)
    if t < now - timedelta(hours=MAX_AT_AGE_H):
        raise DomainError(f"`at` is more than {MAX_AT_AGE_H} h old — too old to replay.", code="bad_at", status_code=400)
    return t


def _point(gps):
    from .geofence import coords

    p = coords(gps)
    return (p[0], p[1]) if p else (None, None)


class ShiftService:
    @staticmethod
    def current(rep):
        """The rep's open shift, or None. A rep has at most one."""
        return RepShift.objects.filter(rep=rep, status__in=("on", "break")).order_by("-started_at").first()

    @staticmethod
    @transaction.atomic
    def start(*, rep, user=None, client_id="", at=None, gps=None, device=None, checks=None):
        if client_id:
            replay = RepShift.objects.filter(rep=rep, client_id=client_id).first()
            if replay:
                return replay
        open_ = ShiftService.current(rep)
        if open_ is not None:
            raise DomainError("A shift is already on — end it before starting another.", code="shift_open", status_code=409)
        t = _client_at(at)
        lat, lng = _point(gps)
        shift = RepShift.objects.create(
            rep=rep, user=user or rep.user, client_id=client_id, status="on",
            started_at=t, recorded_started_at=t, start_lat=lat, start_lng=lng,
            device=device or {}, checks=checks or {}, last_ping_at=t,
        )
        AuditService.log("shift.started", shift, after={"rep": rep.code, "at": str(t), "checks": checks or {}})
        return shift

    @staticmethod
    @transaction.atomic
    def break_(shift, *, at=None, gps=None):
        if shift.status == "break":
            return shift
        if shift.status != "on":
            raise DomainError("This shift has ended.", code="shift_ended", status_code=409)
        t = _client_at(at)
        RepShiftBreak.objects.create(shift=shift, started_at=t, recorded_started_at=t)
        shift.status = "break"
        shift.save(update_fields=["status", "updated_at"])
        return shift

    @staticmethod
    @transaction.atomic
    def resume(shift, *, at=None, gps=None):
        if shift.status == "on":
            return shift
        if shift.status != "break":
            raise DomainError("This shift has ended.", code="shift_ended", status_code=409)
        t = _client_at(at)
        b = shift.breaks.filter(ended_at__isnull=True).order_by("-started_at").first()
        if b is not None:
            b.ended_at = b.recorded_ended_at = max(t, b.started_at)
            b.save(update_fields=["ended_at", "recorded_ended_at"])
        shift.status = "on"
        # the break was deliberate silence; the gap clock restarts from the moment work resumes
        shift.last_ping_at = max(shift.last_ping_at or t, t)
        shift.save(update_fields=["status", "last_ping_at", "updated_at"])
        return shift

    @staticmethod
    @transaction.atomic
    def end(shift, *, at=None, gps=None, reason="rep"):
        if shift.status == "ended":
            return shift
        t = _client_at(at)
        b = shift.breaks.filter(ended_at__isnull=True).order_by("-started_at").first()
        if b is not None:
            b.ended_at = b.recorded_ended_at = max(t, b.started_at)
            b.save(update_fields=["ended_at", "recorded_ended_at"])
        lat, lng = _point(gps)
        shift.status = "ended"
        shift.ended_at = shift.recorded_ended_at = max(t, shift.started_at)
        shift.end_reason = reason
        shift.end_lat, shift.end_lng = lat, lng
        shift.save(update_fields=["status", "ended_at", "recorded_ended_at", "end_reason", "end_lat", "end_lng", "updated_at"])
        AuditService.log("shift.ended", shift, after={"reason": reason, "at": str(shift.ended_at)})
        return shift

    # ------------------------------------------------------------- office correction
    @staticmethod
    @transaction.atomic
    def correct(shift, *, started_at=None, ended_at=None, breaks=None, reason="", actor=None):
        """
        The office fixes a rep's times. The phone's own record (`recorded_*`) is never touched; the effective
        fields move, and who/when/why ride on the shift — corrected attendance gets disputed later.
        """
        if not (reason or "").strip():
            raise DomainError("Give a reason — corrected attendance needs one.", code="reason_required", status_code=400)
        before = {"started_at": str(shift.started_at), "ended_at": str(shift.ended_at)}
        if started_at:
            shift.started_at = _client_at_office(started_at)
        if ended_at:
            shift.ended_at = _client_at_office(ended_at)
            if shift.status != "ended":
                shift.status, shift.end_reason = "ended", "office"
        if shift.ended_at and shift.ended_at < shift.started_at:
            raise DomainError("A shift cannot end before it starts.", code="bad_times", status_code=400)
        for row in breaks or []:
            b = shift.breaks.filter(pk=row.get("id")).first()
            if b is None:
                continue
            if row.get("started_at"):
                b.started_at = _client_at_office(row["started_at"])
            if row.get("ended_at"):
                b.ended_at = _client_at_office(row["ended_at"])
            if b.ended_at and b.ended_at < b.started_at:
                raise DomainError("A break cannot end before it starts.", code="bad_times", status_code=400)
            b.save(update_fields=["started_at", "ended_at"])
        shift.corrected_by = actor or get_current_user()
        shift.corrected_at = timezone.now()
        shift.correction_reason = reason.strip()
        shift.save()
        AuditService.log("shift.corrected", shift, before=before,
                         after={"started_at": str(shift.started_at), "ended_at": str(shift.ended_at), "reason": shift.correction_reason})
        if shift.user_id:
            from apps.core.notifications import notify

            notify(shift.user, "shift.corrected", "Your shift times were corrected by the office",
                   f"{shift.started_at:%d %b}: {shift.correction_reason}"[:200], {"shift_id": str(shift.id), "screen": "shift"})
        return shift

    # ------------------------------------------------------------- derived
    @staticmethod
    def working_minutes(shift):
        end = shift.ended_at or timezone.now()
        total = (end - shift.started_at).total_seconds()
        for b in shift.breaks.all():
            b_end = b.ended_at or end
            total -= max(0.0, (min(b_end, end) - max(b.started_at, shift.started_at)).total_seconds())
        return max(0, int(total // 60))

    @staticmethod
    def tracking_state(shift, tenant=None):
        """live | stale | lost while on, paused on a break — the office map's word for each rep."""
        if shift.status == "break":
            return "paused"
        if shift.status == "ended":
            return "off"
        tenant = tenant or get_current_tenant()
        interval = int(tenant.setting("tracking_interval_s") or 120)
        gap_min = int(tenant.setting("shift_gap_minutes") or 15)
        silent_s = (timezone.now() - (shift.last_ping_at or shift.started_at)).total_seconds()
        if silent_s > gap_min * 60:
            return "lost"
        if silent_s > interval * 2:
            return "stale"
        return "live"


def _client_at_office(value):
    """Office corrections: any parseable datetime, no replay-age bound."""
    from django.utils.dateparse import parse_datetime

    t = parse_datetime(str(value)) if not hasattr(value, "tzinfo") else value
    if t is None:
        raise DomainError(f"Not a datetime: {value}", code="bad_times", status_code=400)
    return timezone.make_aware(t) if t.tzinfo is None else t


def sweep_tenant(tenant):
    """
    The dead-tracker watch and the daily cutoff for one tenant. Runs inside tenant_context.

    Silence only counts across *on* time: `last_ping_at` is bumped on every ping and again at `/resume`, and a
    shift on break is skipped entirely. A ping arriving after an alert clears it (`lost_alerted_at`).
    """
    from apps.core.notifications import notify, notify_permission

    now = timezone.now()
    gap = timedelta(minutes=int(tenant.setting("shift_gap_minutes") or 15))
    lost_end = timedelta(minutes=int(tenant.setting("shift_lost_end_minutes") or 30))
    cutoff = str(tenant.setting("shift_auto_end_at") or "23:59")
    acted = {"alerted": 0, "ended_lost": 0, "ended_cutoff": 0}

    for shift in RepShift.objects.filter(status__in=("on", "break")).select_related("rep", "user"):
        # daily cutoff: a shift still open from an earlier day, or past today's cutoff time, is closed
        local = timezone.localtime(now)
        h, m = (int(x) for x in cutoff.split(":"))
        cut_today = local.replace(hour=h, minute=m, second=0, microsecond=0)
        started_local = timezone.localtime(shift.started_at)
        if started_local.date() < local.date() or local >= cut_today:
            ShiftService.end(shift, at=now, reason="auto_cutoff")
            if shift.user_id:
                notify(shift.user, "shift.ended", "Your shift was closed at the daily cutoff",
                       f"Started {started_local:%d %b %H:%M} and never ended — closed automatically.", {"shift_id": str(shift.id), "screen": "shift"})
            acted["ended_cutoff"] += 1
            continue
        if shift.status == "break":
            continue
        silence = now - (shift.last_ping_at or shift.started_at)
        if silence > gap + lost_end:
            ShiftService.end(shift, at=now, reason="tracking_lost")
            if shift.user_id:
                notify(shift.user, "shift.ended", "Your shift ended — tracking went silent",
                       "No position reached the office for too long. Start a new shift when you are back.", {"shift_id": str(shift.id), "screen": "shift"})
            acted["ended_lost"] += 1
        elif silence > gap and shift.lost_alerted_at is None:
            shift.lost_alerted_at = now
            shift.save(update_fields=["lost_alerted_at"])
            if shift.user_id:
                notify(shift.user, "shift.tracking_lost", "Your tracking stopped — open DMS",
                       "The office has not heard from your phone. Open the app so your shift stays on.", {"shift_id": str(shift.id), "screen": "shift"})
            notify_permission("tracking.view_team", "shift.tracking_lost", f"Tracking lost: {shift.rep.name}",
                              f"No ping for {int(silence.total_seconds() // 60)} min while on shift.", {"shift_id": str(shift.id)})
            acted["alerted"] += 1
    return acted
