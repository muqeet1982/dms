"""
StockService is the ONLY place that creates StockMovement rows (§10.2).
Every movement updates the StockLevel cache inside the same transaction with row locks
and maintains the weighted-average cost per variant (tenant-wide, §7.1).
"""
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from apps.core.audit import AuditService
from apps.core.events import emit
from apps.core.exceptions import DomainError
from apps.core.numbering import next_number
from apps.core.tenancy import get_current_tenant, get_current_user

from .models import Batch, InventoryAlert, ReasonCode, StockAdjustment, StockAdjustmentLine, StockCount, StockCountLine, StockLevel, StockMovement, StockTransfer, StockTransferLine, Warehouse

Q3 = Decimal("0.001")
Q4 = Decimal("0.0001")

ADJ = ["adjustment"]
PRET = ["purchase_return"]
SRET = ["sales_return"]
# code, name, category, expense account prefix, where it can be used
DEFAULT_REASON_CODES = [
    ("DMG", "Damaged", "damage", "5-01-006", ADJ + PRET + SRET),
    ("EXP", "Expired", "expiry", "5-01-005", ADJ + PRET + SRET),
    ("NEXP", "Near expiry / short shelf life", "near_expiry", "5-01-005", PRET + SRET),
    ("THF", "Theft", "theft", "5-01-004", ADJ),
    ("LST", "Lost", "lost", "5-01-004", ADJ),
    ("SMP", "Free sample", "sample", "5-02-004", ADJ),
    ("PRM", "Promotion", "promotion", "5-02-004", ADJ),
    ("CNT", "Count variance", "count", "5-01-004", ADJ),
    ("COR", "Correction", "correction", "5-01-004", ADJ),
    ("TRD", "Transit damage", "transit_damage", "5-01-007", ADJ + PRET + SRET),
    ("QLT", "Quality complaint", "quality", "5-01-006", PRET + SRET),
    ("WRG", "Wrong / unordered item", "wrong_item", "5-01-004", PRET + SRET),
    ("SHT", "Short supply", "short_supply", "5-01-004", PRET),
    ("EXS", "Excess supply / overstock", "excess_supply", "5-01-004", PRET),
    ("RCL", "Supplier recall", "recall", "5-01-006", PRET),
    ("PRD", "Price difference", "price_difference", "5-01-004", PRET),
    ("CRT", "Customer return", "customer_return", "5-01-004", SRET),
    ("OTH", "Other", "other", "5-01-004", ADJ + PRET + SRET),
]


def seed_reason_codes(tenant):
    from apps.accounting.models import Account

    for code, name, cat, acct_prefix, used_for in DEFAULT_REASON_CODES:
        acct = Account.all_objects.filter(tenant=tenant, code__startswith=acct_prefix, is_group=False).first()
        row, created = ReasonCode.all_objects.get_or_create(
            tenant=tenant, code=code,
            defaults={"name": name, "category": cat, "expense_account": acct, "requires_approval": cat in ("theft", "lost", "count", "correction", "other"), "requires_photo": cat in ("damage", "transit_damage"), "used_for": used_for},
        )
        if not created and not row.used_for:  # existing tenants: fill in where the reason applies
            row.used_for = used_for
            row.save(update_fields=["used_for"])


class StockService:
    @staticmethod
    def _level(warehouse, variant, batch=None, bin=None, lock=True):
        qs = StockLevel.all_objects.filter(tenant_id=warehouse.tenant_id, warehouse=warehouse, variant=variant, batch=batch, bin=bin)
        if lock:
            qs = qs.select_for_update()
        level = qs.first()
        if level is None:
            level = StockLevel.all_objects.create(tenant_id=warehouse.tenant_id, warehouse=warehouse, variant=variant, batch=batch, bin=bin, avg_cost=variant.avg_cost or variant.cost_price)
            level = StockLevel.all_objects.select_for_update().get(pk=level.pk)
        return level

    @staticmethod
    def _draw_from(warehouse, variant, batch, need):
        """
        Which rows an outward move should come out of: ([(bin, qty)], short). The un-binned pool goes first, then
        the racks the goods were put away in — stock in a rack is still the same stock.
        """
        rows = StockLevel.all_objects.filter(
            tenant_id=warehouse.tenant_id, warehouse=warehouse, variant=variant, batch=batch, qty_on_hand__gt=0
        ).order_by(F("bin__code").asc(nulls_first=True))
        plan, left = [], Decimal(need)
        for lv in rows:
            take = min(left, lv.qty_on_hand)
            if take <= 0:
                continue
            plan.append((lv.bin, take))
            left -= take
            if left <= 0:
                break
        return plan, left

    @staticmethod
    def _check_batch(variant, batch, movement_type):
        if variant.product.is_batch_tracked and batch is None and movement_type not in ("opening",):
            raise DomainError(f"{variant.name} is batch-tracked: a batch is required.")

    @staticmethod
    def _negative_allowed(warehouse, allow_negative=None):
        return allow_negative if allow_negative is not None else (warehouse.allow_negative_stock or warehouse.tenant.setting("allow_negative_stock"))

    @staticmethod
    @transaction.atomic
    def move(*, warehouse, variant, qty, movement_type, unit_cost=None, batch=None, bin=None, reference=None, note="", occurred_at=None, allow_negative=None):
        """
        qty > 0 = stock in, qty < 0 = stock out (base unit).
        Returns the StockMovement. Raises DomainError on negative stock (unless allowed).

        An outward move that does not say which rack to take from is drawn from wherever the stock sits, so goods
        put away on receipt can still be sold, transferred or returned. When that has to be split across racks the
        last movement is returned; they all share the same cost.
        """
        qty = Decimal(qty).quantize(Q3)
        if qty == 0:
            raise DomainError("Quantity must not be zero.")
        StockService._check_batch(variant, batch, movement_type)
        if qty < 0 and bin is None:
            plan, short = StockService._draw_from(warehouse, variant, batch, -qty)
            if short > 0:
                if not StockService._negative_allowed(warehouse, allow_negative):
                    have = sum((t for _, t in plan), Decimal("0"))
                    raise DomainError(
                        f"Insufficient stock for {variant.name}{' batch ' + batch.batch_no if batch else ''} in {warehouse.name}: on hand {have}, requested {abs(qty)}.",
                        code="insufficient_stock",
                    )
                plan.append((None, short))  # allowed to go negative: the shortfall sits on the un-binned row
            if len(plan) > 1 or plan[0][0] is not None:
                mv = None
                for src, take in plan:
                    mv = StockService._move_one(
                        warehouse=warehouse, variant=variant, qty=-take, movement_type=movement_type, unit_cost=unit_cost,
                        batch=batch, bin=src, reference=reference, note=note, occurred_at=occurred_at, allow_negative=allow_negative,
                    )
                return mv
        return StockService._move_one(
            warehouse=warehouse, variant=variant, qty=qty, movement_type=movement_type, unit_cost=unit_cost, batch=batch,
            bin=bin, reference=reference, note=note, occurred_at=occurred_at, allow_negative=allow_negative,
        )

    @staticmethod
    @transaction.atomic
    def _move_one(*, warehouse, variant, qty, movement_type, unit_cost=None, batch=None, bin=None, reference=None, note="", occurred_at=None, allow_negative=None):
        """One row of the ledger, against one (warehouse, batch, bin) level."""
        qty = Decimal(qty).quantize(Q3)
        tenant = warehouse.tenant
        StockService._check_batch(variant, batch, movement_type)
        if batch is not None and batch.is_expired and qty < 0 and movement_type == "sale_dispatch":
            raise DomainError(f"Batch {batch.batch_no} is expired and cannot be dispatched.")
        level = StockService._level(warehouse, variant, batch, bin)
        new_qty = level.qty_on_hand + qty
        if new_qty < 0:
            allowed = allow_negative if allow_negative is not None else (warehouse.allow_negative_stock or tenant.setting("allow_negative_stock"))
            if not allowed:
                raise DomainError(f"Insufficient stock for {variant.name}{' batch ' + batch.batch_no if batch else ''} in {warehouse.name}: on hand {level.qty_on_hand}, requested {abs(qty)}.", code="insufficient_stock")
        # cost handling (§7.1 weighted average per variant, tenant-wide)
        if qty > 0:
            unit_cost = Decimal(unit_cost if unit_cost is not None else (StockService.current_avg_cost(variant))).quantize(Q4)
            if movement_type in ("grn", "opening", "sales_return", "adjustment", "count_variance", "delivery_return") and unit_cost > 0 and warehouse.type != "quarantine":
                StockService._update_avg_cost(variant, qty, unit_cost)
        else:
            # outgoing stock is always valued at the current average cost (never a stale caller value),
            # except explicit zero-cost moves (quarantine write-offs)
            unit_cost = Decimal("0") if (unit_cost is not None and Decimal(unit_cost) == 0) else StockService.current_avg_cost(variant)
        level.qty_on_hand = new_qty
        level.avg_cost = variant.avg_cost or unit_cost
        level.save(update_fields=["qty_on_hand", "avg_cost", "updated_at"])
        mv = StockMovement.all_objects.create(
            tenant=tenant, warehouse=warehouse, bin=bin, variant=variant, batch=batch, qty=qty, unit_cost=unit_cost, movement_type=movement_type,
            reference_type=getattr(reference, "__class__", type(None)).__name__ if reference is not None else "", reference_id=str(getattr(reference, "pk", "") or ""),
            reference_number=str(getattr(reference, "number", "") or ""), occurred_at=occurred_at or timezone.now(), performed_by=get_current_user() if getattr(get_current_user(), "is_authenticated", False) else None, note=note[:200],
        )
        return mv

    @staticmethod
    def current_avg_cost(variant):
        """Fresh weighted-average cost from the DB (callers may hold stale instances)."""
        from apps.catalog.models import ProductVariant

        row = ProductVariant.all_objects.filter(pk=variant.pk).values_list("avg_cost", "cost_price").first()
        cost = (row[0] or row[1] or Decimal("0")) if row else (variant.avg_cost or variant.cost_price or Decimal("0"))
        variant.avg_cost = Decimal(cost).quantize(Q4)
        return variant.avg_cost

    @staticmethod
    def _update_avg_cost(variant, qty_in, unit_cost):
        """Weighted average per variant across all warehouses (§7.1)."""
        from apps.catalog.models import ProductVariant

        v = ProductVariant.all_objects.select_for_update().get(pk=variant.pk)
        on_hand = StockLevel.all_objects.filter(variant=v).aggregate(s=Sum("qty_on_hand"))["s"] or Decimal("0")
        if on_hand <= 0:
            new_avg = unit_cost
        else:
            new_avg = ((on_hand * (v.avg_cost or unit_cost)) + qty_in * unit_cost) / (on_hand + qty_in)
        v.avg_cost = Decimal(new_avg).quantize(Q4, ROUND_HALF_UP)
        v.save(update_fields=["avg_cost"])
        variant.avg_cost = v.avg_cost

    @staticmethod
    def on_hand(variant, warehouse=None, batch=None):
        qs = StockLevel.objects.filter(variant=variant)
        if warehouse is not None:
            qs = qs.filter(warehouse=warehouse)
        if batch is not None:
            qs = qs.filter(batch=batch)
        agg = qs.aggregate(h=Sum("qty_on_hand"), r=Sum("qty_reserved"))
        return (agg["h"] or Decimal("0")), (agg["r"] or Decimal("0"))

    @staticmethod
    def available(variant, warehouse=None):
        h, r = StockService.on_hand(variant, warehouse)
        return h - r

    @staticmethod
    def fefo_batches(variant, warehouse, qty_needed, min_shelf_life_days=None):
        """First-expiry-first-out suggestion: list of (batch, qty) covering qty_needed."""
        today = timezone.localdate()
        levels = StockLevel.objects.filter(variant=variant, warehouse=warehouse, qty_on_hand__gt=0).select_related("batch").order_by(F("batch__expiry_date").asc(nulls_last=True))
        plan, remaining = [], Decimal(qty_needed)
        for lv in levels:
            if remaining <= 0:
                break
            if lv.batch and lv.batch.expiry_date:
                if lv.batch.expiry_date < today:
                    continue
                if min_shelf_life_days and (lv.batch.expiry_date - today).days < min_shelf_life_days:
                    continue
            take = min(lv.qty_available, remaining)
            if take <= 0:
                continue
            plan.append((lv.batch, take, lv.bin))
            remaining -= take
        return plan, remaining

    @staticmethod
    @transaction.atomic
    def reserve(variant, warehouse, qty, batch=None):
        level = StockService._level(warehouse, variant, batch)
        level.qty_reserved = level.qty_reserved + Decimal(qty)
        level.save(update_fields=["qty_reserved", "updated_at"])

    @staticmethod
    @transaction.atomic
    def unreserve(variant, warehouse, qty, batch=None):
        level = StockService._level(warehouse, variant, batch)
        level.qty_reserved = max(Decimal("0"), level.qty_reserved - Decimal(qty))
        level.save(update_fields=["qty_reserved", "updated_at"])

    @staticmethod
    def get_or_create_batch(variant, batch_no, expiry_date=None, mfg_date=None, supplier=None, grn_line=None):
        """
        The one place a batch is born — receiving, an adjustment, opening stock, an import.

        A product marked expiry-tracked must arrive with a date, and this is where that is held: the
        near-expiry alerts, the expiry report and shelf-life picking all read `Batch.expiry_date`, so a batch
        created without one is invisible to every one of them. Only a *new* batch is refused; stock already on
        the shelf under an old undated batch keeps moving, and a date supplied later is backfilled.
        """
        if not batch_no:
            return None
        batch = Batch.objects.filter(variant=variant, batch_no=batch_no).first()
        if batch is None:
            if variant.product.is_expiry_tracked and not expiry_date:
                from apps.core.exceptions import DomainError

                raise DomainError(
                    f"{variant.name} is expiry-tracked: batch {batch_no} needs an expiry date.",
                    code="expiry_required", status_code=400,
                )
            return Batch.objects.create(variant=variant, batch_no=batch_no, expiry_date=expiry_date,
                                        mfg_date=mfg_date, supplier=supplier, grn_line=grn_line)
        if expiry_date and not batch.expiry_date:
            batch.expiry_date = expiry_date
            batch.save(update_fields=["expiry_date"])
        return batch

    @staticmethod
    def recompute_levels(tenant):
        """Nightly drift check: StockLevel = SUM(StockMovement)."""
        drift = []
        sums = StockMovement.all_objects.filter(tenant=tenant).values("warehouse_id", "bin_id", "variant_id", "batch_id").annotate(s=Sum("qty"))
        seen = set()
        for row in sums:
            key = (row["warehouse_id"], row["bin_id"], row["variant_id"], row["batch_id"])
            seen.add(key)
            lv = StockLevel.all_objects.filter(tenant=tenant, warehouse_id=key[0], bin_id=key[1], variant_id=key[2], batch_id=key[3]).first()
            if lv is None or lv.qty_on_hand != row["s"]:
                drift.append({"key": [str(k) for k in key], "cached": str(lv.qty_on_hand if lv else "missing"), "ledger": str(row["s"])})
                if lv is not None:
                    lv.qty_on_hand = row["s"]
                    lv.save(update_fields=["qty_on_hand"])
        for lv in StockLevel.all_objects.filter(tenant=tenant):
            if (lv.warehouse_id, lv.bin_id, lv.variant_id, lv.batch_id) not in seen and lv.qty_on_hand != 0:
                drift.append({"key": [str(lv.warehouse_id), str(lv.bin_id), str(lv.variant_id), str(lv.batch_id)], "cached": str(lv.qty_on_hand), "ledger": "0"})
                lv.qty_on_hand = Decimal("0")
                lv.save(update_fields=["qty_on_hand"])
        if drift:
            InventoryAlert.all_objects.create(tenant=tenant, type="drift", message=f"{len(drift)} stock level(s) drifted from the ledger and were corrected.", data={"rows": drift[:50]})
        return drift

    @staticmethod
    def valuation(warehouse=None):
        qs = StockLevel.objects.filter(qty_on_hand__gt=0).select_related("variant", "variant__product", "warehouse", "batch")
        if warehouse is not None:
            qs = qs.filter(warehouse=warehouse)
        rows, total = [], Decimal("0")
        for lv in qs:
            cost = lv.variant.avg_cost or lv.variant.cost_price
            value = (lv.qty_on_hand * cost).quantize(Q4)
            total += value
            rows.append({"warehouse": lv.warehouse.name, "warehouse_id": str(lv.warehouse_id), "variant_id": str(lv.variant_id), "sku": lv.variant.sku, "name": lv.variant.name, "batch": lv.batch.batch_no if lv.batch else "", "qty": str(lv.qty_on_hand), "avg_cost": str(cost), "value": str(value)})
        return {"rows": rows, "total": str(total)}


class TransferService:
    @staticmethod
    @transaction.atomic
    def create(*, from_warehouse, to_warehouse, lines, date=None, notes=""):
        if from_warehouse.pk == to_warehouse.pk:
            raise DomainError("Source and destination warehouses must differ.")
        tr = StockTransfer.objects.create(number=next_number("stock_transfer"), from_warehouse=from_warehouse, to_warehouse=to_warehouse, date=date or timezone.localdate(), notes=notes, requested_by=get_current_user())
        for l in lines:
            StockTransferLine.objects.create(transfer=tr, variant=l["variant"], batch=l.get("batch"), qty_requested=l["qty"], qty_sent=l.get("qty_sent") or l["qty"], unit_cost=l["variant"].avg_cost or l["variant"].cost_price)
        tenant = get_current_tenant()
        if not tenant.setting("transfer_requires_approval"):
            tr.status = "approved"
            tr.save(update_fields=["status"])
        AuditService.log("transfer.created", tr, after={"from": from_warehouse.code, "to": to_warehouse.code, "lines": len(lines)})
        return tr

    @staticmethod
    @transaction.atomic
    def approve(tr):
        if tr.status != "requested":
            raise DomainError("Only requested transfers can be approved.")
        tr.status = "approved"
        tr.approved_by = get_current_user()
        tr.save(update_fields=["status", "approved_by", "updated_at"])
        AuditService.log("status.approved", tr, before={"status": "requested"}, after={"status": "approved"})
        return tr

    @staticmethod
    @transaction.atomic
    def dispatch(tr, sent_qtys=None):
        """−qty from source, +qty into the tenant's In-Transit (van) warehouse; if none, straight to destination on receive."""
        if tr.status not in ("approved", "requested"):
            raise DomainError("Transfer must be approved before dispatch.")
        transit = Warehouse.objects.filter(type="van", vehicle__isnull=True, code="TRANSIT").first()
        for line in tr.lines.select_related("variant", "variant__product", "batch"):
            qty = Decimal((sent_qtys or {}).get(str(line.id), line.qty_sent or line.qty_requested))
            line.qty_sent = qty
            line.save(update_fields=["qty_sent"])
            if qty <= 0:
                continue
            mv = StockService.move(warehouse=tr.from_warehouse, variant=line.variant, qty=-qty, movement_type="transfer_out", batch=line.batch, reference=tr, unit_cost=line.unit_cost)
            line.unit_cost = mv.unit_cost
            line.save(update_fields=["unit_cost"])
            if transit is not None:
                StockService.move(warehouse=transit, variant=line.variant, qty=qty, movement_type="transfer_in", batch=line.batch, reference=tr, unit_cost=mv.unit_cost)
        tr.status = "in_transit"
        tr.dispatched_at = timezone.now()
        tr.save(update_fields=["status", "dispatched_at", "updated_at"])
        from apps.accounting.services import PostingService

        PostingService.post_transfer_dispatch(tr)
        AuditService.log("status.dispatched", tr, after={"status": "in_transit"})
        return tr

    @staticmethod
    @transaction.atomic
    def receive(tr, received_qtys=None, lost_reason=None):
        if tr.status != "in_transit":
            raise DomainError("Transfer is not in transit.")
        transit = Warehouse.objects.filter(type="van", vehicle__isnull=True, code="TRANSIT").first()
        lost_lines = []
        for line in tr.lines.select_related("variant", "variant__product", "batch"):
            rec = Decimal((received_qtys or {}).get(str(line.id), line.qty_sent))
            lost = line.qty_sent - rec
            line.qty_received, line.qty_lost = rec, lost
            line.save(update_fields=["qty_received", "qty_lost"])
            if transit is not None and line.qty_sent > 0:
                StockService.move(warehouse=transit, variant=line.variant, qty=-line.qty_sent, movement_type="transfer_out", batch=line.batch, reference=tr, unit_cost=line.unit_cost)
            if rec > 0:
                StockService.move(warehouse=tr.to_warehouse, variant=line.variant, qty=rec, movement_type="transfer_in", batch=line.batch, reference=tr, unit_cost=line.unit_cost)
            if lost > 0:
                lost_lines.append({"variant": line.variant, "batch": line.batch, "qty": lost, "unit_cost": line.unit_cost})
        tr.status = "received"
        tr.received_at = timezone.now()
        tr.save(update_fields=["status", "received_at", "updated_at"])
        from apps.accounting.services import PostingService

        PostingService.post_transfer_receive(tr)
        if lost_lines:
            reason = lost_reason or ReasonCode.objects.filter(category="transit_damage").first() or ReasonCode.objects.first()
            # shortage: stock was already removed from transit; book the loss as an approved adjustment with no stock movement (value only)
            adj = AdjustmentService.create(warehouse=tr.to_warehouse, type="decrease", reason=reason, lines=lost_lines, notes=f"Shortage on transfer {tr.number}", source=tr, skip_stock=True, auto_approve=True)
            AuditService.log("transfer.shortage", tr, after={"adjustment": adj.number})
        AuditService.log("status.received", tr, after={"status": "received"})
        return tr

    @staticmethod
    @transaction.atomic
    def cancel(tr, reason=""):
        if tr.status in ("in_transit", "received"):
            raise DomainError("Dispatched transfers cannot be cancelled; receive them instead.")
        tr.status = "cancelled"
        tr.notes = (tr.notes + f"\nCancelled: {reason}").strip()
        tr.save(update_fields=["status", "notes", "updated_at"])
        AuditService.log("status.cancelled", tr, after={"reason": reason})
        return tr


class AdjustmentService:
    @staticmethod
    @transaction.atomic
    def create(*, warehouse, type, reason, lines, date=None, notes="", source=None, skip_stock=False, auto_approve=False, submit=True):
        adj = StockAdjustment.objects.create(number=next_number("stock_adjustment"), warehouse=warehouse, type=type, reason=reason, date=date or timezone.localdate(), notes=notes, source_type=source.__class__.__name__ if source else "", source_id=str(source.pk) if source else "")
        total = Decimal("0")
        for l in lines:
            variant = l["variant"]
            unit_cost = Decimal(l.get("unit_cost") or variant.avg_cost or variant.cost_price)
            value = (Decimal(l["qty"]) * unit_cost).quantize(Q4)
            StockAdjustmentLine.objects.create(adjustment=adj, variant=variant, batch=l.get("batch"), bin=l.get("bin"), qty=l["qty"], unit_cost=unit_cost, value=value, note=l.get("note", ""))
            total += value
        adj.total_value = total
        adj.save(update_fields=["total_value"])
        if auto_approve:
            AdjustmentService.approve(adj, skip_stock=skip_stock, force=True)
        elif submit:
            AdjustmentService.submit(adj)
        return adj

    @staticmethod
    @transaction.atomic
    def submit(adj):
        if adj.status != "draft":
            raise DomainError("Only draft adjustments can be submitted.")
        membership = None
        from apps.accounts.models import TenantMembership

        user = get_current_user()
        if user is not None and getattr(user, "is_authenticated", False):
            membership = TenantMembership.objects.filter(user=user, tenant=adj.tenant).first()
        needs_approval = adj.reason.requires_approval
        if needs_approval and membership is not None and membership.has_perm("inventory.approve_adjustment"):
            limit = membership.approval_limit("stock_adjustment")
            if limit is None or adj.total_value <= Decimal(str(limit)):
                return AdjustmentService.approve(adj, force=True)
        if not needs_approval:
            return AdjustmentService.approve(adj, force=True)
        adj.status = "pending_approval"
        adj.save(update_fields=["status", "updated_at"])
        from apps.core.notifications import notify_permission

        notify_permission("inventory.approve_adjustment", "approval.pending", f"Stock adjustment {adj.number} needs approval", f"{adj.reason.name} · value {adj.total_value:.2f}", {"adjustment_id": str(adj.id)})
        AuditService.log("status.submitted", adj, after={"status": "pending_approval"})
        return adj

    @staticmethod
    @transaction.atomic
    def approve(adj, skip_stock=False, force=False):
        if adj.status not in ("draft", "pending_approval"):
            raise DomainError("Adjustment already processed.")
        if not force:
            from apps.accounts.models import TenantMembership

            user = get_current_user()
            m = TenantMembership.objects.filter(user=user, tenant=adj.tenant).first() if user else None
            if m is None or not m.has_perm("inventory.approve_adjustment"):
                raise DomainError("You cannot approve adjustments.", status_code=403)
            limit = m.approval_limit("stock_adjustment")
            if limit is not None and adj.total_value > Decimal(str(limit)):
                raise DomainError(f"Adjustment value {adj.total_value:.2f} exceeds your approval limit {limit:.2f}.")
        sign = 1 if adj.type == "increase" else -1
        if not skip_stock:
            total = Decimal("0")
            for line in adj.lines.select_related("variant", "variant__product", "batch", "bin"):
                mtype = "wastage" if adj.reason.category in ("damage", "expiry", "transit_damage") and sign < 0 else ("count_variance" if adj.reason.category == "count" else "adjustment")
                mv = StockService.move(warehouse=adj.warehouse, variant=line.variant, qty=sign * line.qty, movement_type=mtype, batch=line.batch, bin=line.bin, unit_cost=line.unit_cost if sign > 0 else None, reference=adj, note=adj.reason.name)
                line.unit_cost = mv.unit_cost
                line.value = (line.qty * mv.unit_cost).quantize(Q4)
                line.save(update_fields=["unit_cost", "value"])
                total += line.value
            adj.total_value = total
        adj.status = "approved"
        adj.approved_by = get_current_user() if getattr(get_current_user(), "is_authenticated", False) else None
        adj.approved_at = timezone.now()
        adj.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
        from apps.accounting.services import PostingService

        PostingService.post_stock_adjustment(adj)
        AuditService.log("status.approved", adj, after={"status": "approved", "value": str(adj.total_value)})
        emit("adjustment.approved", adjustment=adj)
        return adj

    @staticmethod
    @transaction.atomic
    def reject(adj, reason=""):
        if adj.status != "pending_approval":
            raise DomainError("Only pending adjustments can be rejected.")
        adj.status = "rejected"
        adj.rejection_reason = reason
        adj.save(update_fields=["status", "rejection_reason", "updated_at"])
        AuditService.log("status.rejected", adj, after={"reason": reason})
        return adj


class CountService:
    @staticmethod
    @transaction.atomic
    def open(*, warehouse, type="full", scope=None, tolerance_pct=None, notes=""):
        tenant = get_current_tenant()
        count = StockCount.objects.create(number=next_number("stock_count"), warehouse=warehouse, type=type, scope=scope or {}, started_by=get_current_user(), tolerance_pct=tolerance_pct if tolerance_pct is not None else Decimal(str(tenant.setting("count_tolerance_pct") or 2)), notes=notes)
        levels = StockLevel.objects.filter(warehouse=warehouse).select_related("variant", "variant__product")
        scope = scope or {}
        if scope.get("categories"):
            levels = levels.filter(variant__product__category_id__in=scope["categories"])
        if scope.get("bins"):
            levels = levels.filter(bin_id__in=scope["bins"])
        if scope.get("variants"):
            levels = levels.filter(variant_id__in=scope["variants"])
        for lv in levels:
            StockCountLine.objects.create(count=count, variant=lv.variant, batch=lv.batch, bin=lv.bin, system_qty=lv.qty_on_hand)
        count.status = "counting"
        count.save(update_fields=["status"])
        AuditService.log("count.opened", count, after={"lines": count.lines.count()})
        return count

    @staticmethod
    @transaction.atomic
    def record(count, entries):
        """entries: [{line_id?|variant, batch?, bin?, counted_qty, scanned?}]"""
        if count.status not in ("counting", "review"):
            raise DomainError("Count is not open for entry.")
        for e in entries:
            line = None
            if e.get("line_id"):
                line = count.lines.filter(pk=e["line_id"]).first()
            elif e.get("variant"):
                line = count.lines.filter(variant_id=e["variant"], batch_id=e.get("batch"), bin_id=e.get("bin")).first()
                if line is None:
                    line = StockCountLine.objects.create(count=count, variant_id=e["variant"], batch_id=e.get("batch"), bin_id=e.get("bin"), system_qty=Decimal("0"))
            if line is None:
                raise DomainError("Count line not found.")
            line.counted_qty = Decimal(e["counted_qty"])
            line.counted_by = get_current_user()
            line.counted_at = timezone.now()
            line.scanned = bool(e.get("scanned", False))
            line.save()
        return count

    @staticmethod
    def review(count):
        if count.status == "counting":
            count.status = "review"
            count.save(update_fields=["status"])
        return count

    @staticmethod
    @transaction.atomic
    def post(count, reason=None):
        if count.status not in ("counting", "review"):
            raise DomainError("Count cannot be posted from this status.")
        reason = reason or ReasonCode.objects.filter(category="count").first() or ReasonCode.objects.first()
        inc, dec = [], []
        for line in count.lines.select_related("variant", "batch", "bin"):
            if line.counted_qty is None:
                continue
            var = line.counted_qty - line.system_qty
            if var == 0:
                continue
            (inc if var > 0 else dec).append({"variant": line.variant, "batch": line.batch, "bin": line.bin, "qty": abs(var), "note": f"Count {count.number}"})
        if dec:
            count.adjustment = AdjustmentService.create(warehouse=count.warehouse, type="decrease", reason=reason, lines=dec, notes=f"Variance from count {count.number}", source=count, auto_approve=True)
        if inc:
            count.adjustment_increase = AdjustmentService.create(warehouse=count.warehouse, type="increase", reason=reason, lines=inc, notes=f"Variance from count {count.number}", source=count, auto_approve=True)
        count.status = "posted"
        count.posted_by = get_current_user()
        count.posted_at = timezone.now()
        count.save()
        AuditService.log("status.posted", count, after={"decrease": count.adjustment.number if count.adjustment else None, "increase": count.adjustment_increase.number if count.adjustment_increase else None})
        return count


class AlertService:
    @staticmethod
    def compute(tenant):
        from apps.catalog.models import ProductVariant

        InventoryAlert.all_objects.filter(tenant=tenant, resolved_at__isnull=True).exclude(type="drift").update(resolved_at=timezone.now())
        today = timezone.localdate()
        out = []
        for v in ProductVariant.all_objects.filter(tenant=tenant, is_active=True).select_related("product"):
            on_hand = StockLevel.all_objects.filter(tenant=tenant, variant=v).aggregate(s=Sum("qty_on_hand"))["s"] or Decimal("0")
            p = v.product
            if p.min_stock and on_hand < p.min_stock:
                out.append(InventoryAlert(tenant=tenant, type="low_stock", variant=v, message=f"{v.name}: {on_hand} on hand, below minimum {p.min_stock}", data={"on_hand": str(on_hand), "min": str(p.min_stock), "reorder": str(max(p.max_stock - on_hand, p.reorder_qty))}))
            if on_hand < 0:
                out.append(InventoryAlert(tenant=tenant, type="negative", variant=v, message=f"{v.name}: negative stock {on_hand}", data={"on_hand": str(on_hand)}))
            last = StockMovement.all_objects.filter(tenant=tenant, variant=v).order_by("-occurred_at").values_list("occurred_at", flat=True).first()
            if on_hand > 0 and last and (today - last.date()).days > 90:
                out.append(InventoryAlert(tenant=tenant, type="dead_stock", variant=v, message=f"{v.name}: no movement for {(today - last.date()).days} days", data={"days": (today - last.date()).days, "on_hand": str(on_hand)}))
        for lv in StockLevel.all_objects.filter(tenant=tenant, qty_on_hand__gt=0, batch__expiry_date__isnull=False).select_related("batch", "variant", "warehouse"):
            days = (lv.batch.expiry_date - today).days
            if days < 0:
                out.append(InventoryAlert(tenant=tenant, type="expired", variant=lv.variant, warehouse=lv.warehouse, batch=lv.batch, message=f"{lv.variant.name} batch {lv.batch.batch_no} expired {-days} days ago ({lv.qty_on_hand} in {lv.warehouse.name})", data={"qty": str(lv.qty_on_hand)}))
            elif days <= 90:
                out.append(InventoryAlert(tenant=tenant, type="expiring", variant=lv.variant, warehouse=lv.warehouse, batch=lv.batch, message=f"{lv.variant.name} batch {lv.batch.batch_no} expires in {days} days ({lv.qty_on_hand} in {lv.warehouse.name})", data={"days": days, "qty": str(lv.qty_on_hand)}))
        InventoryAlert.all_objects.bulk_create(out)
        return len(out)

    @staticmethod
    def reorder_suggestions():
        from apps.catalog.models import ProductVariant

        rows = []
        for v in ProductVariant.objects.filter(is_active=True).select_related("product", "product__supplier"):
            on_hand = StockLevel.objects.filter(variant=v).aggregate(s=Sum("qty_on_hand"))["s"] or Decimal("0")
            p = v.product
            if p.min_stock and on_hand < p.min_stock:
                qty = max(p.max_stock - on_hand, p.reorder_qty, Decimal("0"))
                rows.append({"variant_id": str(v.id), "sku": v.sku, "name": v.name, "supplier_id": str(p.supplier_id) if p.supplier_id else None, "supplier": p.supplier.name if p.supplier else "", "on_hand": str(on_hand), "min_stock": str(p.min_stock), "max_stock": str(p.max_stock), "suggested_qty": str(qty), "cost": str(v.avg_cost or v.cost_price)})
        return rows
