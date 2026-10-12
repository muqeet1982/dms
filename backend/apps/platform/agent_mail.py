"""
The mailbox the teams building this system talk through.

Two AIs — one on the phone, one on the server — and a business owner have been passing messages by hand,
copied from one terminal into another. This is the same conversation with an address: post a message, read
what is addressed to you, close a request when it is done.

It is deliberately small and deliberately not part of the business API. There is no tenant here and no
customer data; the credential is an `AgentToken` sent as `X-Agent-Token`, and a token's `role` decides who a
message is *from* — the sender cannot claim to be somebody else.
"""
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from apps.core.exceptions import DomainError

from .models import AgentMessage, AgentToken

MAX_BODY = 200_000          # a long handover document is fine; a log dump is not
PAGE_SIZE = 50


class AgentThrottle(SimpleRateThrottle):
    """Per token, not per IP: two agents behind one NAT must not throttle each other."""

    scope = "agent_mail"
    rate = "120/min"

    def get_cache_key(self, request, view):
        token = getattr(request, "agent_token", None)
        return self.cache_format % {"scope": self.scope, "ident": str(token.pk) if token else self.get_ident(request)}


class AgentBase(APIView):
    """Authenticates the token and hangs it on the request. No login, no tenant."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [AgentThrottle]

    def initial(self, request, *args, **kwargs):
        raw = (request.headers.get("X-Agent-Token") or "").strip()
        if not raw:
            auth = request.headers.get("Authorization") or ""
            if auth.lower().startswith("bearer "):
                raw = auth[7:].strip()
        token = AgentToken.resolve(raw)
        if token is None:
            raise DomainError("Send a valid X-Agent-Token.", code="bad_agent_token", status_code=401)
        request.agent_token = token
        super().initial(request, *args, **kwargs)

    @staticmethod
    def row(m, *, full=True):
        out = {
            "id": str(m.id), "thread": m.thread, "subject": m.subject,
            "from": m.sender, "to": m.recipient, "kind": m.kind, "status": m.status,
            "in_reply_to": str(m.in_reply_to_id) if m.in_reply_to_id else None,
            "created_at": m.created_at, "delivered_at": m.delivered_at,
            "needs_an_answer": m.needs_an_answer,
        }
        if full:
            out["body"] = m.body
        return out


class AgentMessagesView(AgentBase):
    """
    `GET  /api/v1/agent/messages` — what is addressed to you.
    `POST /api/v1/agent/messages` — send one.

    **GET** returns the undelivered messages for your role, oldest first, and marks them delivered. That is
    the one surprising thing here, and it is the right default for an agent that polls: read it once, act on
    it, and it stops coming back. Pass `?peek=1` to read without marking, `?all=1` for the whole history,
    `?thread=<slug>` for one conversation, `?since=<iso>` for everything after a moment.

    **POST** takes:

        {"to": "server", "kind": "request", "subject": "Visits: check-in and no-order calls",
         "body": "<markdown>", "thread": "visits", "in_reply_to": "<id>", "client_id": "<uuid>"}

    `from` is your token's role and cannot be set. `thread` defaults to a slug of the subject, or to the
    parent's thread when `in_reply_to` is given. Replying to a request marks it **answered**.
    """

    def get(self, request):
        me = request.agent_token.role
        qs = AgentMessage.objects.all()
        if request.query_params.get("all") not in ("1", "true"):
            qs = qs.filter(Q(recipient=me) | Q(sender=me)) if request.query_params.get("mine") else qs.filter(recipient=me)
        if request.query_params.get("thread"):
            qs = qs.filter(thread=request.query_params["thread"])
        since = parse_datetime(request.query_params.get("since") or "")
        if since:
            qs = qs.filter(created_at__gt=since)
        unread_only = not any(request.query_params.get(k) in ("1", "true") for k in ("all", "thread", "history"))
        if unread_only and not since:
            qs = qs.filter(recipient=me, delivered_at__isnull=True)
        rows = list(qs.select_related("in_reply_to")[:PAGE_SIZE])

        peeking = request.query_params.get("peek") in ("1", "true")
        if not peeking:
            ids = [m.id for m in rows if m.recipient == me and m.delivered_at is None]
            if ids:
                AgentMessage.objects.filter(id__in=ids).update(delivered_at=timezone.now())
        waiting = AgentMessage.objects.filter(recipient=me, delivered_at__isnull=True).exclude(
            id__in=[m.id for m in rows]).count() if not peeking else None
        return Response({
            "me": me, "count": len(rows), "still_waiting": waiting,
            "open_for_me": AgentMessage.objects.filter(recipient=me, kind__in=("request", "question"), status="open").count(),
            "open_from_me": AgentMessage.objects.filter(sender=me, kind__in=("request", "question"), status="open").count(),
            "results": [self.row(m) for m in rows],
        })

    def post(self, request):
        from django.utils.text import slugify

        me = request.agent_token.role
        d = request.data if isinstance(request.data, dict) else {}
        body = (d.get("body") or "").strip()
        subject = (d.get("subject") or "").strip()
        if not body:
            raise DomainError("A message needs a body.", code="bad_message", status_code=400)
        if len(body) > MAX_BODY:
            raise DomainError(f"That message is too long ({len(body)} characters, limit {MAX_BODY}).",
                              code="too_long", status_code=413)

        client_id = (d.get("client_id") or "").strip()
        if client_id:
            existing = AgentMessage.objects.filter(sender=me, client_id=client_id).first()
            if existing:                       # a retried send is the same message, not a second one
                return Response(self.row(existing), status=200)

        parent = None
        if d.get("in_reply_to"):
            parent = AgentMessage.objects.filter(pk=d["in_reply_to"]).first()
            if parent is None:
                raise DomainError("in_reply_to is not a message I have.", code="bad_message", status_code=400)

        to = (d.get("to") or (parent.sender if parent else "")).strip()
        if to not in dict(AgentToken.ROLES):
            raise DomainError(f"`to` must be one of: {', '.join(dict(AgentToken.ROLES))}", code="bad_message", status_code=400)
        if to == me:
            raise DomainError("You cannot address a message to yourself.", code="bad_message", status_code=400)

        kind = d.get("kind") or ("reply" if parent else "note")
        if kind not in dict(AgentMessage.KINDS):
            raise DomainError(f"`kind` must be one of: {', '.join(dict(AgentMessage.KINDS))}", code="bad_message", status_code=400)
        if not subject:
            subject = (parent.subject if parent else body.splitlines()[0].lstrip("# ").strip())[:200]
        thread = (d.get("thread") or (parent.thread if parent else "") or slugify(subject))[:80] or "general"

        message = AgentMessage.objects.create(
            thread=thread, subject=subject, sender=me, recipient=to, kind=kind, body=body,
            in_reply_to=parent, client_id=client_id,
            status="open" if kind in ("request", "question") else "closed",
        )
        # answering a request closes the loop without anyone having to remember to
        if parent is not None and parent.needs_an_answer and to == parent.sender:
            AgentMessage.objects.filter(pk=parent.pk).update(status="answered")
        return Response(self.row(message), status=201)


class AgentMessageView(AgentBase):
    """
    `GET   /api/v1/agent/messages/{id}` — one message, whoever it was for.
    `PATCH /api/v1/agent/messages/{id}` — `{"status": "answered" | "closed" | "open"}`.

    Only the **recipient** of a request may change its status: the point of the field is that the side doing
    the work says when it is done, not the side that asked.
    """

    def get(self, request, pk=None):
        message = AgentMessage.objects.filter(pk=pk).select_related("in_reply_to").first()
        if message is None:
            raise DomainError("No such message.", status_code=404)
        if message.recipient == request.agent_token.role and message.delivered_at is None:
            AgentMessage.objects.filter(pk=message.pk).update(delivered_at=timezone.now())
        out = self.row(message)
        out["replies"] = [self.row(r) for r in message.replies.all()]
        return Response(out)

    def patch(self, request, pk=None):
        message = AgentMessage.objects.filter(pk=pk).first()
        if message is None:
            raise DomainError("No such message.", status_code=404)
        status = (request.data or {}).get("status")
        if status not in dict(AgentMessage.STATUSES):
            raise DomainError(f"`status` must be one of: {', '.join(dict(AgentMessage.STATUSES))}",
                              code="bad_message", status_code=400)
        if message.recipient != request.agent_token.role:
            raise DomainError("Only the side a request was addressed to can close it.", code="forbidden", status_code=403)
        message.status = status
        message.save(update_fields=["status"])
        return Response(self.row(message))


class AgentThreadsView(AgentBase):
    """
    `GET /api/v1/agent/threads` — the conversation at a glance: one row per thread, newest activity first.

    This is the answer to "anything pending?" without reading the history.
    """

    def get(self, request):
        me = request.agent_token.role
        threads = {}
        for m in AgentMessage.objects.all().order_by("created_at"):
            t = threads.setdefault(m.thread, {"thread": m.thread, "subject": m.subject, "messages": 0,
                                              "open_for_me": 0, "open_for_them": 0, "last_at": None, "last_from": ""})
            t["messages"] += 1
            t["last_at"], t["last_from"] = m.created_at, m.sender
            if m.needs_an_answer:
                t["open_for_me" if m.recipient == me else "open_for_them"] += 1
        rows = sorted(threads.values(), key=lambda r: r["last_at"], reverse=True)
        return Response({"me": me, "count": len(rows), "results": rows})
