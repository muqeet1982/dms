"""
Notification dispatch: in-app inbox + push (FCM) + email + SMS.
Providers are abstract; console/no-op in dev.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail

from .models import DeviceToken, Notification, NotificationPreference

logger = logging.getLogger(__name__)

NOTIFICATION_TYPES = [
    ("order.submitted", "New rep order"),
    ("order.confirmed", "Order confirmed"),
    ("order.rejected", "Order rejected"),
    ("order.dispatched", "Order dispatched"),
    ("delivery.assigned", "Delivery assigned"),
    ("delivery.completed", "Delivery completed"),
    ("stock.low", "Low stock"),
    ("stock.expiry", "Batch expiring"),
    ("payment.received", "Payment received"),
    ("rep.settlement_due", "Rep settlement due"),
    ("approval.pending", "Approval pending"),
    ("platform.announcement", "Announcement"),
    ("subscription.reminder", "Subscription reminder"),
]


def _prefs(user, ntype):
    pref = NotificationPreference.objects.filter(user=user, type=ntype).first()
    if pref is None:
        return {"push": True, "email": False, "sms": False}
    return {"push": pref.push, "email": pref.email, "sms": pref.sms}


def send_push(user, title, body, data=None):
    tokens = list(DeviceToken.objects.filter(user=user).values_list("token", "platform"))
    if not tokens:
        return 0
    if not settings.FCM_CREDENTIALS_FILE:
        logger.info("push (no FCM configured) to %s: %s", user.pk, title)
        return 0
    try:
        import firebase_admin
        from firebase_admin import credentials, messaging

        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.Certificate(settings.FCM_CREDENTIALS_FILE))
        msg = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            tokens=[t for t, _ in tokens],
        )
        resp = messaging.send_each_for_multicast(msg)
        return resp.success_count
    except Exception:  # pragma: no cover
        logger.exception("FCM push failed")
        return 0


def send_sms(phone, text):
    if not phone:
        return False
    if settings.SMS_PROVIDER == "http" and settings.SMS_HTTP_URL:
        try:
            import requests

            requests.post(settings.SMS_HTTP_URL, json={"to": phone, "text": text}, headers={"Authorization": f"Bearer {settings.SMS_HTTP_TOKEN}"}, timeout=10)
            return True
        except Exception:  # pragma: no cover
            logger.exception("SMS send failed")
            return False
    logger.info("SMS (console) to %s: %s", phone, text)
    return True


def send_email(to, subject, body):
    """
    One message, through whichever provider the platform settings name.

    Never raises: an invitation or an OTP is a side effect of something the user asked for, and failing to post
    it must not fail the thing itself. The failure is logged, and the settings page has a test button that does
    the same send *without* swallowing the reason — which is where somebody actually wants to see it.
    """
    if not to:
        return False
    from apps.core.plan_limits import platform_setting

    provider = platform_setting("email_provider", "smtp")
    try:
        if provider == "graph":
            from apps.core import mail_graph

            mail_graph.send(to, subject, body)
            return True
        if provider == "console":
            logger.info("email (console) to %s: %s", to, subject)
            return True
        send_mail(subject, body, platform_setting("email_from_address") or settings.DEFAULT_FROM_EMAIL, [to], fail_silently=True)
        return True
    except Exception as e:
        # the body may carry an OTP or an invite link, so only the subject and the reason are logged
        logger.warning("email to %s failed via %s: %s", to, provider, e)
        return False


def notify(user, ntype, title, body="", data=None, tenant_id=None):
    """Create an inbox row and fan out per the user's preferences."""
    from .tenancy import get_current_tenant_id

    tenant_id = tenant_id or get_current_tenant_id()
    n = Notification.all_objects.create(tenant_id=tenant_id, user=user, type=ntype, title=title, body=body, data=data or {})
    prefs = _prefs(user, ntype)
    if prefs["push"]:
        send_push(user, title, body, {**(data or {}), "type": ntype, "notification_id": str(n.id)})
    if prefs["email"] and user.email:
        send_email(user.email, title, body)
    if prefs["sms"] and user.phone:
        send_sms(user.phone, f"{title}: {body}"[:300])
    return n


def notify_permission(perm_codename, ntype, title, body="", data=None, tenant_id=None):
    """Notify every active tenant user holding a permission (e.g. approvals pending)."""
    from apps.accounts.models import TenantMembership
    from .tenancy import get_current_tenant_id

    tenant_id = tenant_id or get_current_tenant_id()
    for m in TenantMembership.objects.filter(tenant_id=tenant_id, status="active").select_related("user"):
        if m.has_perm(perm_codename):
            notify(m.user, ntype, title, body, data, tenant_id)
