"""
Stamp the tenant's company code onto every existing document number (SO-00002 → TDN-SO-00002), so each
company's numbering is unmistakably its own. Counters are kept as they are — only the prefix changes, so
every chain (SO → INV/DN/…) stays aligned. ChequeLeaf is left alone: those are the bank's printed numbers.

Dry-run by default; --commit applies inside one transaction.
"""
from django.apps import apps as django_apps
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

EXCLUDE = {"ChequeLeaf"}


class Command(BaseCommand):
    help = "Prefix every document number with the tenant's company code (dry-run unless --commit)."

    def add_arguments(self, parser):
        parser.add_argument("tenant", help="Tenant slug")
        parser.add_argument("--commit", action="store_true")

    def handle(self, *args, **opts):
        from apps.platform.models import Tenant

        tenant = Tenant.objects.filter(slug=opts["tenant"]).first()
        if tenant is None:
            raise CommandError(f"No tenant {opts['tenant']!r}")
        if not tenant.doc_code:
            raise CommandError(f"Tenant {tenant.slug} has no doc_code set.")
        code = f"{tenant.doc_code}-"
        models = [
            m for m in django_apps.get_models()
            if m.__name__ not in EXCLUDE and hasattr(m, "all_objects")
            and any(f.name == "number" for f in m._meta.fields) and any(f.name == "tenant" for f in m._meta.fields)
        ]
        plan = []
        for m in models:
            for pk, num in m.all_objects.filter(tenant_id=tenant.id).values_list("pk", "number"):
                if num and not num.startswith(code):
                    plan.append((m, pk, num, f"{code}{num}"))
        if not plan:
            self.stdout.write("Nothing to do — every document already carries the company code.")
            return
        per = {}
        for m, _pk, _old, _new in plan:
            per[m.__name__] = per.get(m.__name__, 0) + 1
        for name, n in sorted(per.items()):
            self.stdout.write(f"  {name:22} {n} documents")
        self.stdout.write(f"\n{len(plan)} documents get the prefix {code!r} for tenant {tenant.slug}.")
        with transaction.atomic():
            for m, pk, _old, new in plan:
                m.all_objects.filter(pk=pk).update(number=new)
            if not opts["commit"]:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("DRY RUN — rolled back. Re-run with --commit."))
            else:
                self.stdout.write(self.style.SUCCESS("COMMITTED."))
