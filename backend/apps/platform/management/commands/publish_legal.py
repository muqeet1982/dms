"""
Publish the bundled legal text so `/legal/<kind>` is a real page rather than the app shell.

The text lives in `apps/platform/legal/<kind>.md` so it is reviewable in the repo; this command loads it as the
current version. The business can edit it afterwards in platform admin → Legal, and a later run with a new
`--version` supersedes whatever is there.
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.platform.models import LegalDocument

HERE = Path(__file__).resolve().parents[2] / "legal"


class Command(BaseCommand):
    help = "Publish a bundled legal document (privacy, terms) from apps/platform/legal/<kind>.md"

    def add_arguments(self, parser):
        parser.add_argument("kind", nargs="?", default="privacy")
        parser.add_argument("--as-version", dest="as_version", default="", help="override the version in the file")
        parser.add_argument("--force", action="store_true", help="republish even if the same version is current")

    def handle(self, *args, **options):
        kind = options["kind"]
        source = HERE / f"{kind}.md"
        if not source.exists():
            raise CommandError(f"No bundled text for {kind!r} — expected {source}")
        body = source.read_text().strip()
        version = options["as_version"] or self._version_in(body) or "1.0"

        current = LegalDocument.objects.filter(kind=kind, is_current=True).first()
        if current and current.version == version and not options["force"]:
            self.stdout.write(f"{kind} {version} is already current — nothing to do (use --force to republish)")
            return
        LegalDocument.objects.filter(kind=kind).update(is_current=False)
        LegalDocument.objects.create(kind=kind, version=version, body=body, is_current=True)
        self.stdout.write(self.style.SUCCESS(f"published {kind} {version} — {len(body.split())} words, live at /legal/{kind}"))

    @staticmethod
    def _version_in(body):
        """`**Version 1.0 — 10 October 2026**` → `1.0`, so the file is the single source of its own version."""
        for line in body.splitlines()[:12]:
            if "version" in line.lower():
                for word in line.replace("*", " ").split():
                    if word[:1].isdigit() and "." in word:
                        return word.strip(" —-")
        return None
