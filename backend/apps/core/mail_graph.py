"""
Sending mail through Microsoft Graph, with the credentials kept in the platform settings rather than in a file.

Why Graph rather than SMTP: Microsoft 365 is turning SMTP AUTH off, and an app registration can send as a
mailbox without that mailbox's password ever existing in this system. The flow is client credentials — the app
proves itself with its own secret and sends as the mailbox named in the settings — which needs the application
permission **Mail.Send** granted with admin consent in Azure. Without that consent Graph answers 403 and the
test button says so in those words, because that is the one thing nobody remembers to do.
"""
import logging

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
SEND_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"
TIMEOUT = 20


class GraphMailError(RuntimeError):
    """Something Graph (or Azure) refused, phrased for the person reading the settings page."""


def config():
    """The mail settings as the admin page holds them."""
    from apps.platform.views import PlatformSettingsView

    cfg = PlatformSettingsView.current()
    return {
        "tenant_id": (cfg.get("graph_tenant_id") or "").strip(),
        "client_id": (cfg.get("graph_client_id") or "").strip(),
        "client_secret": (cfg.get("graph_client_secret") or "").strip(),
        "sender": (cfg.get("email_from_address") or "").strip(),
        "from_name": (cfg.get("email_from_name") or "").strip(),
    }


def _missing(cfg):
    labels = {"tenant_id": "Directory (tenant) ID", "client_id": "Application (client) ID",
              "client_secret": "Client secret", "sender": "From address"}
    return [labels[k] for k in labels if not cfg.get(k)]


def token(cfg=None):
    """A bearer token for the app itself, cached until shortly before it expires."""
    cfg = cfg or config()
    gaps = _missing(cfg)
    if gaps:
        raise GraphMailError("Fill in " + ", ".join(gaps) + " before sending through Microsoft 365.")
    key = f"graph.token.{cfg['client_id']}"
    cached = cache.get(key)
    if cached:
        return cached
    try:
        r = requests.post(
            TOKEN_URL.format(tenant=cfg["tenant_id"]),
            data={"grant_type": "client_credentials", "client_id": cfg["client_id"],
                  "client_secret": cfg["client_secret"], "scope": "https://graph.microsoft.com/.default"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        raise GraphMailError(f"Could not reach Microsoft to sign in: {e}") from e
    if r.status_code != 200:
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        # never log or echo the secret; the description from Azure is enough to act on
        raise GraphMailError(_azure_reason(body) or f"Microsoft refused the sign-in ({r.status_code}).")
    data = r.json()
    cache.set(key, data["access_token"], max(int(data.get("expires_in", 3600)) - 120, 60))
    return data["access_token"]


def _azure_reason(body):
    desc = (body.get("error_description") or "").split("\r\n")[0]
    code = body.get("error") or ""
    if "AADSTS7000215" in desc:
        return "That client secret is not accepted — check it was copied from Value, not Secret ID, and has not expired."
    if "AADSTS700016" in desc or "unauthorized_client" in code:
        return "That Application (client) ID does not exist in this directory — check the two IDs are from the same app registration."
    if "AADSTS90002" in desc:
        return "That Directory (tenant) ID does not exist."
    return desc or code


def send(to, subject, body, cfg=None, html=False):
    """
    Send one message as the configured mailbox. Raises `GraphMailError` with something worth reading.

    Deliberately not silent: this is called by the test button, where an error is the whole point, and by
    `send_email`, which catches it so a failed notification never breaks the thing that triggered it.
    """
    cfg = cfg or config()
    access = token(cfg)
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML" if html else "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": a}} for a in ([to] if isinstance(to, str) else list(to))],
        },
        "saveToSentItems": True,
    }
    try:
        r = requests.post(SEND_URL.format(sender=cfg["sender"]), json=payload, timeout=TIMEOUT,
                          headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"})
    except requests.RequestException as e:
        raise GraphMailError(f"Could not reach Microsoft to send: {e}") from e
    if r.status_code in (200, 202):
        return True
    err = {}
    try:
        err = (r.json() or {}).get("error", {})
    except ValueError:
        pass
    raise GraphMailError(_send_reason(r.status_code, err, cfg))


def _send_reason(status, err, cfg):
    code, message = err.get("code", ""), err.get("message", "")
    if status == 403 or code in ("ErrorAccessDenied", "Authorization_RequestDenied"):
        return ("Microsoft accepted the app but will not let it send. In Azure → your app → API permissions, add the "
                "**application** permission Mail.Send for Microsoft Graph and click Grant admin consent, then try again.")
    if status == 404 or code == "ResourceNotFound":
        return f"There is no mailbox {cfg['sender']} in that directory. The From address must be a real mailbox the app may send as."
    if status == 401:
        return "Microsoft rejected the token. If the secret was just changed, try again in a minute."
    return message or f"Microsoft refused the message ({status})."
