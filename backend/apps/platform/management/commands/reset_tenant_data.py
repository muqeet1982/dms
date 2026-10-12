"""
reset_tenant_data: the platform admin screen's "Reset data" from the server, for when a company has to be put
back to a clean start without a browser. Same guards as the API — the slug must be typed back, and a company
we are currently billing needs --i-understand on top of it.

    manage.py reset_tenant_data <slug> --dry-run
    manage.py reset_tenant_data <slug> --confirm <slug>
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Delete one tenant's transactional data, keep its setup, and restart document numbering at 1."

    def add_arguments(self, parser):
        parser.add_argument("slug", help="Tenant slug, e.g. demo")
        parser.add_argument("--confirm", default="", help="Type the slug again to go ahead (required unless --dry-run)")
        parser.add_argument("--dry-run", action="store_true", help="Count what would be deleted and change nothing")
        parser.add_argument("--i-understand", action="store_true", help="Required when the tenant is on a paid, active subscription")

    def handle(self, *a, **o):
        from apps.platform.models import Tenant
        from apps.platform.services import reset_tenant_data, tenant_is_paying

        tenant = Tenant.objects.filter(slug=o["slug"]).first()
        if tenant is None:
            raise CommandError(f"No tenant with slug '{o['slug']}'.")
        paying = tenant_is_paying(tenant)
        if not o["dry_run"]:
            if o["confirm"] != tenant.slug:
                raise CommandError(f"Pass --confirm {tenant.slug} to reset {tenant.display_name}. Nothing was changed.")
            if paying and not o["i_understand"]:
                raise CommandError(f"{tenant.display_name} is a paying customer on an active subscription. Pass --i-understand as well. Nothing was changed.")

        result = reset_tenant_data(tenant, dry_run=o["dry_run"])
        verb = "Would delete" if o["dry_run"] else "Deleted"
        for label, count in sorted(result["deleted"].items(), key=lambda kv: -kv[1]):
            self.stdout.write(f"  {count:>9,}  {label}")
        self.stdout.write(f"{verb} {result['total']:,} rows from {tenant.display_name} ({tenant.slug}){' — paying customer' if paying else ''}.")
        self.stdout.write(f"{'Would reset' if o['dry_run'] else 'Reset'} {result['numbering_reset']} numbering sequence(s) to 1.")
        if not o["dry_run"]:
            self.stdout.write(self.style.SUCCESS("Setup kept: users, warehouses, catalogue, customers, suppliers, reps, accounts, settings."))
