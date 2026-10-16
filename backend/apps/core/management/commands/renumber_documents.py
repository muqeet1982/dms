"""
Renumber derived documents so a whole chain shares its root's counter:
    SO-00001 -> DN-00001, INV-00001, PL-00001, TRIP-00001
    PO-00001 -> GRN-00001, PB-00001, PR-00001
A second child of the same root (a partial second delivery/receipt/invoice) gets a -2, -3 … suffix.

Roots (sales orders, purchase orders) keep their own numbers. Documents with no root to inherit from
(a direct invoice with no order, a GRN with no PO) are left untouched.

Safe by default: prints the plan and rolls back unless --commit is given. Always runs in one transaction.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.numbering import _prefix_for, numeric_core
from apps.core.tenancy import tenant_context
from apps.delivery.models import DeliveryNote, DeliveryTrip, PickList
from apps.platform.models import Tenant
from apps.purchasing.models import GoodsReceipt, PurchaseBill, PurchaseReturn
from apps.sales.models import SalesInvoice


def _order_of(obj, *attrs):
    for a in attrs:
        v = getattr(obj, a, None)
        if v is not None:
            return v
    return None


class Command(BaseCommand):
    help = "Renumber derived documents to share their root order/PO's counter (dry-run unless --commit)."

    def add_arguments(self, parser):
        parser.add_argument("tenant", help="Tenant slug to renumber")
        parser.add_argument("--commit", action="store_true", help="Persist the changes (otherwise dry-run)")

    def handle(self, *args, **opts):
        tenant = Tenant.objects.filter(slug=opts["tenant"]).first()
        if tenant is None:
            raise CommandError(f"No tenant with slug {opts['tenant']!r}")

        from collections import defaultdict

        collected = []  # (model, obj, base_number)

        def add(model, doc_type, obj, parent_number):
            if not parent_number:
                return  # no root to inherit from — leave the document's own number alone
            prefix = _prefix_for(doc_type, tenant.id)
            collected.append((model, obj, f"{prefix}{numeric_core(parent_number)}"))

        with tenant_context(tenant, tenant.owner):
            # ---- sales ----
            for dn in DeliveryNote.all_objects.filter(tenant_id=tenant.id).select_related("order"):
                if dn.order_id:
                    add(DeliveryNote, "delivery_note", dn, dn.order.number)

            for inv in SalesInvoice.all_objects.filter(tenant_id=tenant.id).select_related("order", "delivery_note__order"):
                root = inv.order or (inv.delivery_note.order if inv.delivery_note_id and inv.delivery_note.order_id else None)
                if root is not None:
                    add(SalesInvoice, "sales_invoice", inv, root.number)

            for pl in PickList.all_objects.filter(tenant_id=tenant.id).prefetch_related("orders"):
                root = min(pl.orders.all(), key=lambda o: (o.created_at, str(o.pk)), default=None)
                if root is not None:
                    add(PickList, "pick_list", pl, root.number)

            for trip in DeliveryTrip.all_objects.filter(tenant_id=tenant.id).prefetch_related("delivery_notes__order"):
                dns = sorted(trip.delivery_notes.all(), key=lambda d: (d.sequence, d.created_at))
                root = next((d.order for d in dns if d.order_id), None)
                if root is not None:
                    add(DeliveryTrip, "trip", trip, root.number)

            # ---- purchasing ----
            for grn in GoodsReceipt.all_objects.filter(tenant_id=tenant.id).select_related("po"):
                if grn.po_id:
                    add(GoodsReceipt, "goods_receipt", grn, grn.po.number)

            for bill in PurchaseBill.all_objects.filter(tenant_id=tenant.id).prefetch_related("grns__po"):
                po = next((g.po for g in bill.grns.all() if g.po_id), None)
                if po is not None:
                    add(PurchaseBill, "purchase_bill", bill, po.number)

            for ret in PurchaseReturn.all_objects.filter(tenant_id=tenant.id).select_related("grn__po"):
                po = ret.grn.po if ret.grn_id and ret.grn.po_id else None
                if po is not None:
                    add(PurchaseReturn, "purchase_return", ret, po.number)

            # Numbers held by rows we are NOT renumbering (a doc with no root to inherit, e.g. a trip whose
            # orders are gone). A derived document may not take one of these — it skips to the next free suffix.
            moving_pks = defaultdict(set)
            for model, obj, _base in collected:
                moving_pks[model].add(obj.pk)
            fixed = defaultdict(set)
            for model in moving_pks:
                for num in model.all_objects.filter(tenant_id=tenant.id).exclude(pk__in=moving_pks[model]).values_list("number", flat=True):
                    fixed[model].add(num)

            # Group by the target base so every document that would land on one number is numbered together:
            # the live one takes the clean number, extras (a partial second doc, or a cancelled one) get -2, -3 …
            plan = []  # (model, obj, old, new)
            groups = defaultdict(list)
            for model, obj, base in collected:
                groups[(model, base)].append(obj)
            for (model, base), items in groups.items():
                n = 1
                for obj in sorted(items, key=lambda o: (o.deleted_at is not None, o.created_at, str(o.pk))):
                    while True:
                        cand = base if n == 1 else f"{base}-{n}"
                        n += 1
                        if cand not in fixed[model]:
                            break
                    if obj.number != cand:
                        plan.append((model, obj, obj.number, cand))

            # report
            if not plan:
                self.stdout.write("Nothing to renumber — every document already matches its root.")
                return
            for model, obj, old, new in plan:
                self.stdout.write(f"  {model.__name__:16} {old:18} -> {new}")
            self.stdout.write(f"\n{len(plan)} documents to renumber for tenant {tenant.slug}.")

            # Safety: a target must be unique within its model and must not clash with a number that is NOT
            # being moved out of the way (e.g. a soft-deleted row still occupying it). Abort rather than corrupt.
            renaming = {}  # model -> {pk being renumbered}
            targets = {}   # model -> {new number: pk}
            for model, obj, _old, new in plan:
                renaming.setdefault(model, set()).add(obj.pk)
                if new in targets.setdefault(model, {}):
                    raise CommandError(f"Two {model.__name__} rows both want {new} — aborting, nothing changed.")
                targets[model][new] = obj.pk
            for model, news in targets.items():
                freed = renaming[model]
                clash = list(model.all_objects.filter(tenant_id=tenant.id, number__in=list(news)).exclude(pk__in=freed).values_list("number", flat=True))
                if clash:
                    raise CommandError(f"{model.__name__} number(s) {clash} are already taken by rows not being renumbered — aborting.")

            # apply in two phases so no two rows ever collide mid-update (temp kept short for varchar(40))
            with transaction.atomic():
                for i, (model, obj, _old, _new) in enumerate(plan):
                    model.all_objects.filter(pk=obj.pk).update(number=f"~TMP~{i}")
                for model, obj, _old, new in plan:
                    model.all_objects.filter(pk=obj.pk).update(number=new)
                if not opts["commit"]:
                    transaction.set_rollback(True)
                    self.stdout.write(self.style.WARNING("\nDRY RUN — rolled back. Re-run with --commit to apply."))
                else:
                    self.stdout.write(self.style.SUCCESS("\nCOMMITTED."))
