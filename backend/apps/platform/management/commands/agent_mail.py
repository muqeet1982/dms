"""
The mailbox from the command line — how the server side reads and writes without an HTTP round trip.

    manage.py agent_mail inbox                     # what is waiting for me, and mark it read
    manage.py agent_mail inbox --peek              # without marking
    manage.py agent_mail threads                   # the conversation at a glance
    manage.py agent_mail read <id>
    manage.py agent_mail send --to mobile --kind reply --subject "…" --file reply.md --reply-to <id>
    manage.py agent_mail token --name "Mobile app AI" --role mobile      # issue a credential
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.platform.models import AgentMessage, AgentToken

ME = "server"


class Command(BaseCommand):
    help = "Read and write the build mailbox (apps/platform/agent_mail.py)"

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        inbox = sub.add_parser("inbox")
        inbox.add_argument("--peek", action="store_true")
        inbox.add_argument("--all", action="store_true", help="including what I have already read")
        sub.add_parser("threads")
        show = sub.add_parser("read")
        show.add_argument("message_id")
        send = sub.add_parser("send")
        send.add_argument("--to", required=True, choices=[r for r, _ in AgentToken.ROLES])
        send.add_argument("--kind", default="note", choices=[k for k, _ in AgentMessage.KINDS])
        send.add_argument("--subject", default="")
        send.add_argument("--thread", default="")
        send.add_argument("--body", default="")
        send.add_argument("--file", default="", help="read the body from this file instead")
        send.add_argument("--reply-to", dest="reply_to", default="")
        token = sub.add_parser("token")
        token.add_argument("--name", required=True)
        token.add_argument("--role", required=True, choices=[r for r, _ in AgentToken.ROLES])

    def handle(self, *args, **o):
        getattr(self, f"do_{o['action']}")(o)

    # ── reading ────────────────────────────────────────────────────────────────────────────────────────
    def do_inbox(self, o):
        qs = AgentMessage.objects.filter(recipient=ME)
        if not o["all"]:
            qs = qs.filter(delivered_at__isnull=True)
        rows = list(qs.order_by("created_at")[:50])
        if not rows:
            self.stdout.write("nothing waiting")
            return
        for m in rows:
            flag = "●" if m.needs_an_answer else " "
            self.stdout.write(f"{flag} {m.id}  {m.created_at:%d %b %H:%M}  {m.sender:<7} {m.kind:<8} [{m.thread}] {m.subject}")
            self.stdout.write(f"    {m.body.strip().splitlines()[0][:110] if m.body.strip() else ''}")
        if not o["peek"]:
            AgentMessage.objects.filter(id__in=[m.id for m in rows], delivered_at__isnull=True).update(delivered_at=timezone.now())
            self.stdout.write(self.style.SUCCESS(f"\n{len(rows)} message(s), now marked read"))

    def do_threads(self, o):
        threads = {}
        for m in AgentMessage.objects.all().order_by("created_at"):
            t = threads.setdefault(m.thread, {"n": 0, "mine": 0, "theirs": 0, "last": None, "subject": m.subject})
            t["n"] += 1
            t["last"] = m.created_at
            if m.needs_an_answer:
                t["mine" if m.recipient == ME else "theirs"] += 1
        if not threads:
            self.stdout.write("no threads yet")
            return
        for name, t in sorted(threads.items(), key=lambda kv: kv[1]["last"], reverse=True):
            open_note = []
            if t["mine"]:
                open_note.append(self.style.WARNING(f"{t['mine']} open for me"))
            if t["theirs"]:
                open_note.append(f"{t['theirs']} open for them")
            self.stdout.write(f"{name:<24} {t['n']:>3} msg  {t['last']:%d %b %H:%M}  {' · '.join(open_note)}")

    def do_read(self, o):
        m = AgentMessage.objects.filter(pk=o["message_id"]).first()
        if m is None:
            raise CommandError("no such message")
        self.stdout.write(f"{m.sender} → {m.recipient} · {m.kind} · {m.status} · [{m.thread}] · {m.created_at:%d %b %Y %H:%M}")
        self.stdout.write(f"Subject: {m.subject}\n")
        self.stdout.write(m.body)
        if m.recipient == ME and m.delivered_at is None:
            AgentMessage.objects.filter(pk=m.pk).update(delivered_at=timezone.now())

    # ── writing ────────────────────────────────────────────────────────────────────────────────────────
    def do_send(self, o):
        from django.utils.text import slugify

        body = Path(o["file"]).read_text() if o["file"] else o["body"]
        if not body.strip():
            raise CommandError("nothing to send — pass --body or --file")
        parent = AgentMessage.objects.filter(pk=o["reply_to"]).first() if o["reply_to"] else None
        if o["reply_to"] and parent is None:
            raise CommandError("--reply-to is not a message I have")
        subject = o["subject"] or (parent.subject if parent else body.strip().splitlines()[0].lstrip("# ").strip())
        thread = o["thread"] or (parent.thread if parent else slugify(subject))
        kind = o["kind"] if o["kind"] != "note" or not parent else "reply"
        m = AgentMessage.objects.create(
            thread=thread[:80], subject=subject[:200], sender=ME, recipient=o["to"], kind=kind,
            body=body, in_reply_to=parent, status="open" if kind in ("request", "question") else "closed",
        )
        if parent is not None and parent.needs_an_answer and o["to"] == parent.sender:
            AgentMessage.objects.filter(pk=parent.pk).update(status="answered")
        self.stdout.write(self.style.SUCCESS(f"sent {m.id} to {m.recipient} · thread {m.thread} · {len(body.split())} words"))

    def do_token(self, o):
        token, raw = AgentToken.issue(o["name"], o["role"])
        self.stdout.write(self.style.SUCCESS(f"{token.name} ({token.role})"))
        self.stdout.write(f"  {raw}")
        self.stdout.write(self.style.WARNING("  This is shown once. Store it now."))
