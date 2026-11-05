from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.core.audit import AuditService
from apps.core.plan_limits import platform_setting
from apps.core.exceptions import DomainError
from apps.core.tenancy import tenant_context

from .models import AddOn, Coupon, MessageTemplate, Plan, PlatformInvoice, PlatformPayment, PlatformRefund, Subscription, SubscriptionAddOn, SubscriptionEvent, Tenant
from .plans_seed import ADDONS, MESSAGE_TEMPLATES, RECOMMENDED_PLANS


def seed_platform_defaults():
    for spec in RECOMMENDED_PLANS:
        if not Plan.objects.filter(code=spec["code"]).exists():
            Plan.objects.create(**{k: v for k, v in spec.items()})
    for spec in ADDONS:
        AddOn.objects.get_or_create(code=spec["code"], defaults=spec)
    for spec in MESSAGE_TEMPLATES:
        MessageTemplate.objects.get_or_create(key=spec["key"], channel=spec["channel"], defaults=spec)


class PlanService:
    @staticmethod
    @transaction.atomic
    def create_plan(**data):
        if Plan.objects.filter(code=data["code"], is_current=True).exists():
            raise DomainError("A plan with this code already exists. Edit it to create a new version.")
        plan = Plan.objects.create(**data)
        AuditService.log("platform.plan.created", plan, after=plan.snapshot())
        return plan

    @staticmethod
    @transaction.atomic
    def new_version(plan, **changes):
        """Editing a plan creates a new version; existing subscriptions keep their snapshot (grandfathering)."""
        before = plan.snapshot()
        Plan.objects.filter(code=plan.code, is_current=True).update(is_current=False)
        data = {f.name: getattr(plan, f.name) for f in Plan._meta.fields if f.name not in ("id", "created_at", "updated_at", "version", "parent_plan", "is_current")}
        data.update(changes)
        data["version"] = plan.version + 1
        data["parent_plan"] = plan
        data["is_current"] = True
        new = Plan.objects.create(**data)
        AuditService.log("platform.plan.versioned", new, before=before, after=new.snapshot())
        return new


class TenantService:
    @staticmethod
    def derive_doc_code(name):
        """Short company code for document numbers: initials of the name (TDN), deduped across tenants."""
        words = [w for w in "".join(c if c.isalnum() or c.isspace() else " " for c in (name or "")).split() if w]
        code = ("".join(w[0] for w in words)[:4] if len(words) >= 2 else (words[0][:4] if words else "CO")).upper()
        taken = set(Tenant.objects.exclude(doc_code="").values_list("doc_code", flat=True))
        base, n = code, 2
        while code in taken:
            code = f"{base}{n}"
            n += 1
        return code

    @staticmethod
    @transaction.atomic
    def create_tenant(*, legal_name, display_name, owner_name, owner_email=None, owner_phone=None, owner_password=None, plan=None, slug=None, created_by=None, send_invite=True, **fields):
        from apps.accounts.services import RoleService, UserService

        slug = slug or slugify(display_name)[:50]
        base, i = slug, 1
        while Tenant.objects.filter(slug=slug).exists():
            i += 1
            slug = f"{base}-{i}"
        fields.setdefault("doc_code", TenantService.derive_doc_code(display_name or slug))
        tenant = Tenant.objects.create(slug=slug, legal_name=legal_name, display_name=display_name, created_by=created_by, **fields)
        plan = plan or Plan.objects.filter(code=settings.DMS["DEFAULT_PLAN_CODE"], is_current=True).first() or Plan.objects.filter(is_current=True).first()
        if plan is None:
            seed_platform_defaults()
            plan = Plan.objects.filter(code=settings.DMS["DEFAULT_PLAN_CODE"], is_current=True).first()
        sub = SubscriptionService.assign(tenant, plan, trial=True, actor=created_by)
        tenant.status = "trial" if sub.status == "trial" else "active"
        tenant.save(update_fields=["status"])
        with tenant_context(tenant, created_by):
            roles = RoleService.seed_default_roles(tenant)
            owner_role = roles.get("Owner") or tenant.roles.get(name="Owner")
            membership = UserService.create_tenant_user(tenant, owner_name, email=owner_email, phone=owner_phone, password=owner_password, roles=[owner_role], is_owner=True, send_invite=send_invite and not owner_password)
            tenant.owner = membership.user
            tenant.save(update_fields=["owner"])
            TenantService.seed_tenant_defaults(tenant)
        AuditService.log("platform.tenant.created", tenant, after={"slug": slug, "owner": str(membership.user)}, actor=created_by)
        return tenant

    @staticmethod
    def seed_tenant_defaults(tenant):
        """Chart of accounts, posting rules, reason codes, UoMs, default price list, tax class, numbering."""
        from apps.accounting.coa_template import seed_chart_of_accounts
        from apps.catalog.services import seed_catalog_defaults
        from apps.inventory.services import seed_reason_codes

        seed_chart_of_accounts(tenant)
        seed_reason_codes(tenant)
        seed_catalog_defaults(tenant)
        TenantService._seed_discount_contributions(tenant)

    @staticmethod
    def _seed_discount_contributions(tenant):
        from decimal import Decimal

        from apps.sales.models import DiscountContribution

        if DiscountContribution.all_objects.filter(tenant=tenant).exists():
            return
        for total, dist, comp in [(3, 2, 1), (4, 2, 2), (5, 3, 2), (6, 3, 3), (7, 3, 4)]:
            DiscountContribution.all_objects.create(tenant=tenant, total_pct=Decimal(total), distributor_pct=Decimal(dist), company_pct=Decimal(comp), is_active=True)

    @staticmethod
    @transaction.atomic
    def suspend(tenant, reason, actor=None):
        if tenant.status in ("suspended", "cancelled", "archived"):
            raise DomainError(f"Tenant is already {tenant.status}.")
        before = tenant.status
        tenant.status = "suspended"
        tenant.suspended_at = timezone.now()
        tenant.suspended_reason = reason
        tenant.save()
        sub = tenant.active_subscription
        if sub:
            SubscriptionEvent.objects.create(subscription=sub, event_type="suspended", from_data={"status": before}, to_data={"status": "suspended"}, actor=actor, reason=reason)
        AuditService.log("platform.tenant.suspended", tenant, before={"status": before}, after={"status": "suspended", "reason": reason}, actor=actor)

    @staticmethod
    @transaction.atomic
    def reactivate(tenant, actor=None, reason=""):
        before = tenant.status
        sub = tenant.active_subscription
        today = timezone.localdate()
        if sub and sub.end_date >= today:
            tenant.status = "trial" if sub.status == "trial" else "active"
            sub.status = "trial" if sub.status == "trial" else "active"
            sub.save(update_fields=["status"])
        else:
            tenant.status = "past_due"
        tenant.suspended_at = None
        tenant.suspended_reason = ""
        tenant.save()
        if sub:
            SubscriptionEvent.objects.create(subscription=sub, event_type="reactivated", from_data={"status": before}, to_data={"status": tenant.status}, actor=actor, reason=reason)
        AuditService.log("platform.tenant.reactivated", tenant, before={"status": before}, after={"status": tenant.status}, actor=actor)

    @staticmethod
    @transaction.atomic
    def cancel(tenant, actor=None, reason=""):
        before = tenant.status
        tenant.status = "cancelled"
        tenant.save(update_fields=["status", "updated_at"])
        for sub in tenant.subscriptions.exclude(status="cancelled"):
            sub.status = "cancelled"
            sub.cancelled_at = timezone.now()
            sub.save(update_fields=["status", "cancelled_at"])
            SubscriptionEvent.objects.create(subscription=sub, event_type="cancelled", from_data={"status": before}, to_data={"status": "cancelled"}, actor=actor, reason=reason)
        AuditService.log("platform.tenant.cancelled", tenant, before={"status": before}, after={"status": "cancelled"}, actor=actor)

    @staticmethod
    @transaction.atomic
    def archive(tenant, actor=None):
        if tenant.status != "cancelled":
            raise DomainError("Cancel the tenant before archiving.")
        tenant.status = "archived"
        tenant.deleted_at = timezone.now()
        tenant.save(update_fields=["status", "deleted_at", "updated_at"])
        AuditService.log("platform.tenant.archived", tenant, actor=actor)

    @staticmethod
    def usage(tenant):
        from apps.core.models import Attachment
        from apps.integrations.models import ApiKey, ApiUsage

        month_start = timezone.localdate().replace(day=1)
        storage = Attachment.all_objects.filter(tenant=tenant).aggregate(s=__import__("django.db.models", fromlist=["Sum"]).Sum("size"))["s"] or 0
        last = tenant.audit_logs.order_by("-at").values_list("at", flat=True).first()
        from apps.core.plan_limits import PlanLimitService

        count = PlanLimitService.COUNTERS   # the very counters the limits are checked against
        return {
            "users": count["max_users"](tenant),
            "reps": count["max_reps"](tenant),
            "warehouses": count["max_warehouses"](tenant),
            "suppliers": count["max_suppliers"](tenant),
            "products": count["max_products"](tenant),
            "orders_month": count["max_orders_month"](tenant),
            "custom_roles": count["max_custom_roles"](tenant),
            "storage_mb": round(storage / 1024 / 1024, 1),
            "api_calls_month": ApiUsage.objects.filter(api_key__tenant=tenant, at__date__gte=month_start).count(),
            "api_keys": ApiKey.all_objects.filter(tenant=tenant, is_active=True).count(),
            "last_activity": last,
        }

    @staticmethod
    @transaction.atomic
    def start_impersonation(admin, tenant, reason="", minutes=None):
        from apps.accounts.models import ImpersonationSession

        if not tenant.impersonation_allowed:
            raise DomainError("This tenant has disabled impersonation.")
        if tenant.owner is None:
            raise DomainError("Tenant has no owner.")
        # Replace only a previous impersonation of THIS tenant. Ending every tenant's sessions here meant two
        # people sharing the admin login signed each other out over and over — one working in demo, the other
        # in a trafco tenant, each new impersonation killing the other's ("it keeps getting session out").
        # Requests resolve their impersonation by (admin, tenant header), so sessions in different tenants
        # coexist without touching each other.
        ImpersonationSession.objects.filter(admin=admin, tenant=tenant, ended_at__isnull=True).update(ended_at=timezone.now())
        session = ImpersonationSession.objects.create(admin=admin, target=tenant.owner, tenant=tenant, reason=reason, expires_at=timezone.now() + timedelta(minutes=minutes or platform_setting("impersonation_minutes", settings.DMS["IMPERSONATION_MINUTES"])))
        AuditService.log("platform.impersonation.started", tenant, after={"target": str(tenant.owner), "reason": reason, "expires_at": session.expires_at.isoformat()}, actor=admin)
        return session

    @staticmethod
    def end_impersonation(admin, tenant=None):
        from apps.accounts.models import ImpersonationSession

        qs = ImpersonationSession.objects.filter(admin=admin, ended_at__isnull=True)
        if tenant is not None:
            # leave the admin's impersonations of OTHER tenants running — another person on the same admin
            # login may be working in one of them right now
            qs = qs.filter(tenant=tenant)
        n = qs.update(ended_at=timezone.now())
        AuditService.log("platform.impersonation.ended", model="ImpersonationSession", actor=admin)
        return n


class SubscriptionService:
    @staticmethod
    def _period_end(start: date, plan: Plan, periods=1):
        return start + timedelta(days=plan.period_days * periods)

    @staticmethod
    @transaction.atomic
    def assign(tenant, plan, *, trial=False, start=None, actor=None, coupon=None, notes="", billing_cycle=None, discount=None):
        start = start or timezone.localdate()
        if trial and plan.trial_days:
            end = start + timedelta(days=plan.trial_days)
            status = "trial"
            trial_end = end
        else:
            end = SubscriptionService._period_end(start, plan)
            status = "active"
            trial_end = None
        sub = Subscription.objects.create(tenant=tenant, plan=plan, plan_version=plan.version, plan_snapshot=plan.snapshot(), status=status, start_date=start, end_date=end, trial_end=trial_end, grace_days=plan.grace_days, coupon=coupon, notes=notes, billing_cycle=billing_cycle or plan.billing_cycle)
        if discount:
            SubscriptionService.set_discount(sub, actor=actor, **discount)
        SubscriptionEvent.objects.create(subscription=sub, event_type="created", to_data=sub.plan_snapshot | {"status": status, "end_date": end.isoformat()}, actor=actor)
        tenant._active_sub = sub
        return sub

    @staticmethod
    @transaction.atomic
    def set_discount(sub, *, type=None, value=0, cycles=0, until=None, reason="", actor=None):
        """
        A discount negotiated with one tenant: a percentage or a fixed amount off every invoice, optionally
        limited to N invoices or an end date. type=None (or value 0) removes it.
        """
        before = {"type": sub.discount_type, "value": str(sub.discount_value), "cycles": sub.discount_cycles}
        if not type or Decimal(str(value or 0)) <= 0:
            sub.discount_type, sub.discount_value, sub.discount_cycles, sub.discount_until, sub.discount_reason = "", Decimal("0"), 0, None, ""
            sub.discount_used = 0
        else:
            if type not in ("percent", "fixed"):
                raise DomainError("Discount type must be 'percent' or 'fixed'.")
            value = Decimal(str(value))
            if type == "percent" and value > 100:
                raise DomainError("A percentage discount cannot be more than 100%.")
            price = Decimal(str(sub.plan_snapshot.get("price", "0") or 0))
            if type == "fixed" and price and value > price:
                raise DomainError(f"A fixed discount cannot be more than the plan price ({price:,.0f}).")
            sub.discount_type, sub.discount_value = type, value
            sub.discount_cycles, sub.discount_until, sub.discount_reason = int(cycles or 0), until or None, (reason or "")[:200]
            sub.discount_used = 0
        sub.discount_set_by = actor if actor is not None and getattr(actor, "is_authenticated", False) else None
        sub.discount_set_at = timezone.now()
        sub.save(update_fields=["discount_type", "discount_value", "discount_cycles", "discount_until", "discount_reason", "discount_used", "discount_set_by", "discount_set_at", "updated_at"])
        SubscriptionEvent.objects.create(subscription=sub, event_type="discount", from_data=before, to_data={"type": sub.discount_type, "value": str(sub.discount_value), "cycles": sub.discount_cycles, "until": str(sub.discount_until or ""), "reason": sub.discount_reason}, actor=actor, reason=reason or "")
        AuditService.log("platform.subscription.discount", sub, before=before, after={"type": sub.discount_type, "value": str(sub.discount_value)}, actor=actor)
        return sub

    @staticmethod
    def price_breakdown(sub, *, periods=1):
        """What the next invoice looks like: plan + add-ons − discount. Used by the admin UI and by invoicing."""
        yearly = sub.billing_cycle == "yearly"
        plan_price = Decimal(str(sub.plan_snapshot.get("price_annual" if yearly else "price", "0") or 0))
        if yearly and plan_price <= 0:  # the plan has no yearly price → 12 × monthly
            plan_price = Decimal(str(sub.plan_snapshot.get("price", "0") or 0)) * 12
        plan_total = plan_price * periods
        lines = [{"label": f"{sub.plan_snapshot.get('name')} ({'yearly' if yearly else 'monthly'})" + (f" x {periods}" if periods > 1 else ""), "amount": str(plan_total)}]
        addons_total = Decimal("0")
        for a in sub.addons.select_related("addon"):
            amount = a.addon.unit_price * a.quantity * (periods if a.addon.kind == "recurring" else 1)
            addons_total += amount
            lines.append({"label": f"{a.addon.name} x {a.quantity}" + ("" if a.addon.kind == "recurring" else " (one-off)"), "amount": str(amount)})
        subtotal = plan_total + addons_total
        discount = sub.discount_for(subtotal)
        return {
            "currency": sub.plan_snapshot.get("currency", "PKR"), "cycle": sub.billing_cycle, "periods": periods,
            "lines": lines, "plan_total": str(plan_total), "addons_total": str(addons_total), "subtotal": str(subtotal),
            "discount": str(discount), "discount_label": sub.discount_label, "discount_active": sub.discount_active,
            "total": str(subtotal - discount),
        }

    @staticmethod
    @transaction.atomic
    def renew(sub, *, periods=1, from_today=False, actor=None, record_payment=None, coupon_code=None, reason="", charge=None, invoice=None):
        """
        `periods` more billing cycles. How it is paid (owner, 1 Nov 2026: "admin can renew a tenant with or without
        payment"): charge="paid" invoices it and records the payment now, "invoice" leaves the invoice open to pay
        later, "free" renews without charging at all. `invoice` is the renewal invoice an approved payment settled.
        An open renewal invoice the company was already asked to pay is reused, never billed a second time.
        """
        plan = sub.plan
        today = timezone.localdate()
        base = today if (from_today or sub.end_date < today) else sub.end_date
        new_end = SubscriptionService._period_end(base, plan, periods)
        before = {"status": sub.status, "end_date": sub.end_date.isoformat()}
        charge = charge or ("paid" if record_payment else "invoice")
        sub.end_date = new_end
        sub.status = "active"
        sub.trial_end = None
        sub.save()
        tenant = sub.tenant
        tenant.status = "active"
        tenant.suspended_at = None
        tenant.suspended_reason = ""
        tenant.save()
        open_inv = invoice or BillingService.open_renewal_invoice(sub)
        if charge == "free":
            invoice = None
            if open_inv is not None and open_inv.paid_amount <= 0:
                open_inv.status = "void"   # renewed free of charge: nothing is owed for it any more
                open_inv.save(update_fields=["status"])
        elif open_inv is not None and (invoice is not None or (open_inv.periods == periods and not coupon_code)):
            invoice = open_inv
            invoice.period_start, invoice.period_end = base, new_end
            invoice.save(update_fields=["period_start", "period_end"])
        else:
            if open_inv is not None and open_inv.paid_amount <= 0:
                open_inv.status = "void"
                open_inv.save(update_fields=["status"])
            invoice = BillingService.create_invoice(tenant, sub, base, new_end, periods=periods, coupon_code=coupon_code, actor=actor, purpose="renewal")
        if charge == "paid" and invoice is not None and invoice.balance > 0:
            BillingService.record_payment(tenant, invoice=invoice, actor=actor, **BillingService.clean_payment_input(record_payment or {}, invoice))
        SubscriptionEvent.objects.create(subscription=sub, event_type="renewed", from_data=before,
                                         to_data={"status": "active", "end_date": new_end.isoformat(), "invoice": invoice.number if invoice else None, "charge": charge},
                                         actor=actor, reason=reason)
        AuditService.log("platform.subscription.renewed", sub, before=before, after={"end_date": new_end.isoformat(), "charge": charge}, actor=actor)
        return sub, invoice

    @staticmethod
    @transaction.atomic
    def extend(sub, *, days=None, until=None, amount=None, record_payment=None, actor=None, reason=""):
        """
        Move the expiry — by `days` from the later of its end and today, or to the date `until` — with or without
        a charge (owner, 1 Nov 2026: "he can extend expiry with or without payment"). `amount` invoices the
        extension; `record_payment` also records it paid. A suspended or lapsed company is live again at once.
        """
        today = timezone.localdate()
        if until:
            new_end = date.fromisoformat(str(until)) if not isinstance(until, date) else until
        elif days:
            new_end = max(sub.end_date, today) + timedelta(days=int(days))
        else:
            raise DomainError("Give the number of days or the new expiry date.", status_code=400)
        if new_end < today:
            raise DomainError("The new expiry date has already passed.", status_code=400)
        before = {"status": sub.status, "end_date": sub.end_date.isoformat()}
        sub.end_date = new_end
        if sub.status == "trial":
            sub.trial_end = new_end
        else:
            sub.status = "active"
        sub.save()
        tenant = sub.tenant
        if tenant.status in ("trial", "active", "past_due", "suspended"):
            tenant.status = sub.status
            tenant.suspended_at = None
            tenant.suspended_reason = ""
            tenant.save()
        invoice = None
        if amount not in (None, "") and Decimal(str(amount)) > 0:
            invoice = BillingService.create_invoice(tenant, sub, max(date.fromisoformat(before["end_date"]), today), new_end,
                                                    description=f"Subscription extended to {new_end:%d %b %Y}", override_total=amount, actor=actor, purpose="extension")
            if record_payment:
                BillingService.record_payment(tenant, invoice=invoice, actor=actor, **BillingService.clean_payment_input(record_payment, invoice))
        SubscriptionEvent.objects.create(subscription=sub, event_type="extended", from_data=before,
                                         to_data={"status": sub.status, "end_date": new_end.isoformat(), "invoice": invoice.number if invoice else None},
                                         actor=actor, reason=reason)
        AuditService.log("platform.subscription.extended", sub, before=before, after={"end_date": new_end.isoformat()}, actor=actor)
        return sub, invoice

    @staticmethod
    def prorata(sub, new_plan):
        """Credit for the unused part of the current period vs. cost of the new plan for the same days."""
        today = timezone.localdate()
        days_left = max((sub.end_date - today).days, 0)
        old_price = Decimal(sub.plan_snapshot.get("price", "0"))
        old_days = int(sub.plan_snapshot.get("period_days", 30)) or 30
        credit = (old_price / old_days * days_left).quantize(Decimal("0.01")) if sub.status != "trial" else Decimal("0")
        charge = (new_plan.price / new_plan.period_days * days_left).quantize(Decimal("0.01"))
        return {"days_left": days_left, "credit": str(credit), "charge": str(charge), "net": str(charge - credit)}

    @staticmethod
    def over_limits(tenant, new_plan):
        usage = TenantService.usage(tenant)
        limits = new_plan.limits
        mapping = {"users": "max_users", "warehouses": "max_warehouses", "suppliers": "max_suppliers", "reps": "max_reps", "products": "max_products", "storage_mb": "max_storage_mb"}
        over = []
        for ukey, lkey in mapping.items():
            lim = limits.get(lkey)
            if lim not in (None, -1) and usage[ukey] > lim:
                over.append({"limit": lkey, "current": usage[ukey], "allowed": lim})
        return over

    @staticmethod
    @transaction.atomic
    def change_plan(sub, new_plan, *, when="immediate", actor=None, net_amount=None, record_payment=None, reason=""):
        old_price = Decimal(sub.plan_snapshot.get("price", "0"))
        kind = "upgraded" if new_plan.price >= old_price else "downgraded"
        if kind == "downgraded":
            over = SubscriptionService.over_limits(sub.tenant, new_plan)
            if over:
                raise DomainError("Tenant exceeds the new plan's limits.", errors={"over_limits": over})
        before = sub.plan_snapshot | {"end_date": sub.end_date.isoformat()}
        pr = SubscriptionService.prorata(sub, new_plan)
        if when == "period_end":
            sub.notes = (sub.notes + f"\nScheduled {kind} to {new_plan.code} at period end {sub.end_date:%d/%m/%Y}.").strip()
            sub.plan_snapshot = {**sub.plan_snapshot, "scheduled_plan": new_plan.code}
            sub.save()
            SubscriptionEvent.objects.create(subscription=sub, event_type=kind, from_data=before, to_data={"scheduled_plan": new_plan.code, "at": "period_end"}, actor=actor, reason=reason)
            return sub, None
        sub.plan = new_plan
        sub.plan_version = new_plan.version
        sub.plan_snapshot = new_plan.snapshot()
        sub.grace_days = new_plan.grace_days
        if sub.status == "trial":
            sub.status = "active"
            sub.trial_end = None
            sub.end_date = SubscriptionService._period_end(timezone.localdate(), new_plan)
        sub.save()
        invoice = None
        amount = Decimal(net_amount) if net_amount is not None else Decimal(pr["net"])
        if amount != 0:
            invoice = BillingService.create_invoice(sub.tenant, sub, timezone.localdate(), sub.end_date, description=f"Plan change to {new_plan.name} (pro-rata)", override_total=amount, actor=actor, purpose="plan_change")
            if record_payment and amount > 0:
                BillingService.record_payment(sub.tenant, invoice=invoice, actor=actor, **BillingService.clean_payment_input(record_payment, invoice))
        SubscriptionEvent.objects.create(subscription=sub, event_type=kind, from_data=before, to_data=sub.plan_snapshot | {"prorata": pr}, actor=actor, reason=reason)
        AuditService.log(f"platform.subscription.{kind}", sub, before=before, after=sub.plan_snapshot, actor=actor)
        return sub, invoice

    @staticmethod
    @transaction.atomic
    def extend_trial(sub, days, actor=None, reason=""):
        before = {"end_date": sub.end_date.isoformat()}
        sub.end_date = sub.end_date + timedelta(days=days)
        sub.trial_end = sub.end_date
        sub.status = "trial"
        sub.save()
        sub.tenant.status = "trial"
        sub.tenant.save(update_fields=["status"])
        SubscriptionEvent.objects.create(subscription=sub, event_type="trial_extended", from_data=before, to_data={"end_date": sub.end_date.isoformat()}, actor=actor, reason=reason)
        return sub

    @staticmethod
    @transaction.atomic
    def set_addons(sub, items, actor=None):
        before = [{"addon": a.addon.code, "qty": a.quantity} for a in sub.addons.all()]
        sub.addons.all().delete()
        for it in items:
            SubscriptionAddOn.objects.create(subscription=sub, addon=it["addon"], quantity=it["quantity"])
        # add-ons raise limits via tenant limit_overrides
        overrides = dict(sub.tenant.limit_overrides or {})
        for a in sub.addons.select_related("addon"):
            if a.addon.limit_key:
                base = sub.plan_snapshot.get("limits", {}).get(a.addon.limit_key, 0)
                if base not in (None, -1):
                    overrides[a.addon.limit_key] = int(base) + a.addon.units_per_qty * a.quantity
        sub.tenant.limit_overrides = overrides
        sub.tenant.save(update_fields=["limit_overrides"])
        SubscriptionEvent.objects.create(subscription=sub, event_type="addon_changed", from_data=before, to_data=[{"addon": a.addon.code, "qty": a.quantity} for a in sub.addons.all()], actor=actor)
        return sub

    @staticmethod
    def apply_status(sub, today=None):
        """
        Where one subscription stands today: past its end → past due; past its grace days too → suspended, and the
        company with it (owner, 1 Nov 2026: "after grace period, tenant suspended all user logins"). Idempotent —
        the daily job runs it, and so does every login, so a missed run never leaves a lapsed company open.
        """
        today = today or timezone.localdate()
        tenant = sub.tenant
        if not sub.auto_status_change or sub.status not in ("trial", "active", "past_due") or tenant.status in ("cancelled", "archived"):
            return None
        new = None
        if sub.end_date < today:
            new = "suspended" if (today - sub.end_date).days > sub.grace_days else "past_due"
        if new and new != sub.status:
            before = sub.status
            sub.status = new
            sub.save(update_fields=["status"])
            tenant.status = new
            if new == "suspended":
                tenant.suspended_at = timezone.now()
                tenant.suspended_reason = "Subscription payment overdue"
            tenant.save()
            SubscriptionEvent.objects.create(subscription=sub, event_type="status_changed", from_data={"status": before}, to_data={"status": new}, reason="automatic")
            if new == "suspended":
                BillingService.tell_owner(tenant, "suspension", f"{tenant.display_name} is suspended",
                                          f"The subscription ended on {sub.end_date:%d %b %Y} and the {sub.grace_days} grace days have passed. "
                                          "Sign in as the owner to pay and reactivate it.")
            return new
        if not new and tenant.status in ("trial", "active", "past_due") and tenant.status != sub.status:
            tenant.status = sub.status   # the company reads what its subscription is (TDN read "trial" on an active plan)
            tenant.save(update_fields=["status", "updated_at"])
        return None

    @staticmethod
    def run_status_checks():
        """Daily job: trial/active → past_due after end_date, → suspended after grace."""
        today = timezone.localdate()
        changed = []
        for sub in Subscription.objects.filter(status__in=["trial", "active", "past_due"], auto_status_change=True).select_related("tenant"):
            new = SubscriptionService.apply_status(sub, today)
            if new:
                changed.append((sub.tenant.slug, new))
        # the renewal invoice is issued when the company is first told its payment is due
        for sub in Subscription.objects.filter(status__in=["trial", "active", "past_due"]).select_related("tenant"):
            if BillingService.notice(sub.tenant):
                BillingService.renewal_invoice(sub)
        return changed

    @staticmethod
    def refresh_tenant(tenant):
        """Bring one company's status up to date (at login): the daily job may not have run yet today."""
        sub = tenant.active_subscription
        if sub is not None:
            SubscriptionService.apply_status(sub)

    @staticmethod
    def send_reminders():
        from apps.core.notifications import send_email, send_sms

        today = timezone.localdate()
        sent = []
        for offset in (-7, -3, 0, 3):
            target = today - timedelta(days=offset)  # end_date such that today = end + offset
            for sub in Subscription.objects.filter(end_date=target, status__in=["trial", "active", "past_due"]).select_related("tenant", "tenant__owner"):
                owner = sub.tenant.owner
                if owner is None:
                    continue
                when = {-7: "expires in 7 days", -3: "expires in 3 days", 0: "expires today", 3: "expired 3 days ago"}[offset]
                ctx = {"name": owner.full_name, "company": sub.tenant.display_name, "plan": sub.plan.name, "when": when, "end_date": sub.end_date.isoformat()}
                for tpl in MessageTemplate.objects.filter(key="renewal_reminder", is_active=True):
                    subject, body = tpl.render(ctx)
                    if tpl.channel == "email" and owner.email:
                        send_email(owner.email, subject, body)
                    elif tpl.channel == "sms" and owner.phone:
                        send_sms(owner.phone, body)
                sent.append(sub.tenant.slug)
        return sent


class BillingService:
    OPEN = ("issued", "partial", "overdue")

    # ------------------------------------------------- the company's side: what it owes, and paying it
    @staticmethod
    def open_renewal_invoice(sub):
        if sub is None:
            return None
        return sub.invoices.filter(purpose="renewal", status__in=BillingService.OPEN, is_credit_note=False).order_by("-created_at").first()

    @staticmethod
    def renewal_invoice(sub, periods=1):
        """The invoice the next renewal is paid against: issued when the company is first told its payment is
        due, from its expiry (or today, once lapsed) for one billing cycle, due on the expiry date."""
        inv = BillingService.open_renewal_invoice(sub)
        if inv is not None:
            return inv
        today = timezone.localdate()
        base = sub.end_date if sub.end_date >= today else today
        return BillingService.create_invoice(sub.tenant, sub, base, SubscriptionService._period_end(base, sub.plan, periods), periods=periods,
                                             purpose="renewal", due_date=max(sub.end_date, today))

    @staticmethod
    def open_balance(tenant):
        rows = tenant.platform_invoices.filter(status__in=BillingService.OPEN, is_credit_note=False)
        return sum((i.balance for i in rows), Decimal("0"))

    @staticmethod
    def notice(tenant):
        """
        The payment message a company is shown (owner, 1 Nov 2026: "message on login for payment 3 days before
        payment due and until grace period"): `due` from N days before expiry, `overdue` through the grace days,
        `suspended` after. None the rest of the time.
        """
        sub = tenant.active_subscription
        if sub is None or tenant.status in ("cancelled", "archived"):
            return None
        today = timezone.localdate()
        days = int(platform_setting("payment_notice_days", 3) or 3)
        grace_until = sub.end_date + timedelta(days=sub.grace_days)
        left = (sub.end_date - today).days
        if tenant.status == "suspended":
            level = "suspended"
        elif sub.end_date < today:
            level = "overdue"
        elif left <= days:
            level = "due"
        else:
            return None
        inv = BillingService.open_renewal_invoice(sub)
        owed = BillingService.open_balance(tenant)
        amount = owed if owed > 0 else Decimal(SubscriptionService.price_breakdown(sub)["total"])
        currency = sub.plan_snapshot.get("currency", "PKR")
        pending = list(tenant.platform_payments.filter(status="pending"))
        what = "trial" if sub.status == "trial" else f"{sub.plan.name} subscription"
        money = f"{currency} {amount:,.0f}"
        if level == "suspended":
            text = f"{tenant.display_name} is suspended for non-payment. Pay {money} to reactivate every user's access."
        elif level == "overdue":
            text = f"Your {what} expired on {sub.end_date:%d %b %Y}. Pay {money} by {grace_until:%d %b %Y}, or access for every user stops."
        else:
            when = "today" if left == 0 else f"on {sub.end_date:%d %b %Y} ({left} day{'s' if left != 1 else ''} left)"
            text = f"Your {what} ends {when}. Pay {money} to continue without interruption."
        if pending:
            text += f" Your payment of {currency} {sum((p.amount for p in pending), Decimal('0')):,.0f} is awaiting approval."
        return {"level": level, "message": text, "end_date": sub.end_date, "grace_until": grace_until, "days_left": left,
                "amount_due": str(amount), "currency": currency, "invoice": inv.number if inv else None,
                "pending_payments": len(pending), "pending_amount": str(sum((p.amount for p in pending), Decimal("0")))}

    @staticmethod
    def payment_methods():
        """How the platform is paid — bank accounts, wallets, anything else — as set in Platform settings → Billing."""
        methods = [m for m in (platform_setting("payment_methods", []) or []) if isinstance(m, dict) and m.get("active", True)]
        note = (platform_setting("payment_instructions", "") or "").strip()
        return methods + ([{"type": "note", "name": "How to pay", "instructions": note}] if note else [])

    @staticmethod
    @transaction.atomic
    def submit_payment(tenant, user, *, amount, method, reference="", paid_at=None, note="", receipt_file=None, invoice=None):
        """
        The company's owner says it has paid, with the screenshot to prove it (owner, 1 Nov 2026). It waits as
        "awaiting approval" until a platform admin checks it; nothing renews before that.
        """
        if receipt_file is None:
            raise DomainError("Attach the payment screenshot or receipt.", code="receipt_required", status_code=400)
        if receipt_file.size > 10 * 1024 * 1024:
            raise DomainError("The screenshot is over 10 MB — send a smaller picture or PDF.", status_code=400)
        name = (receipt_file.name or "").lower()
        if not name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".heic")):
            raise DomainError("Send the screenshot as a picture (PNG, JPG) or a PDF.", status_code=400)
        amount = Decimal(str(amount or 0))
        if amount <= 0:
            raise DomainError("Enter the amount you paid.", status_code=400)
        if method not in dict(PlatformPayment.METHODS):
            raise DomainError("Choose how you paid.", status_code=400)
        import os
        import uuid

        receipt_file.name = f"{uuid.uuid4().hex}{os.path.splitext(name)[1]}"   # a screenshot of a bank slip: no guessable link
        sub = tenant.active_subscription
        if invoice is None and sub is not None:
            invoice = BillingService.renewal_invoice(sub)
        pay = PlatformPayment.objects.create(tenant=tenant, invoice=invoice, amount=amount, currency=tenant.base_currency, method=method, reference=reference,
                                             paid_at=paid_at or timezone.localdate(), receipt_file=receipt_file, note=note, status="pending", submitted_by=user)
        if sub is not None:
            SubscriptionEvent.objects.create(subscription=sub, event_type="payment_submitted", to_data={"amount": str(amount), "method": method, "reference": reference}, actor=user)
        AuditService.log("platform.payment.submitted", pay, after={"amount": str(amount), "method": method, "invoice": invoice.number if invoice else None}, actor=user)
        BillingService.tell_platform(f"Payment to approve: {tenant.display_name} — {pay.currency} {amount:,.0f}",
                                     f"{tenant.display_name} submitted a {pay.get_method_display()} payment of {pay.currency} {amount:,.2f}"
                                     f"{' (ref ' + reference + ')' if reference else ''}{' for ' + invoice.number if invoice else ''}. "
                                     "Check the screenshot under Platform → Payments → Awaiting approval.")
        return pay

    @staticmethod
    @transaction.atomic
    def approve_payment(pay, *, actor=None, periods=None, note=""):
        """
        A platform admin accepts a submitted payment: it counts on its invoice, and a renewal invoice paid in full
        renews the subscription — the company is live again (owner, 1 Nov 2026: "superadmin approves it to
        reactivate the subscription plan").
        """
        if pay.status != "pending":
            raise DomainError(f"This payment is {pay.get_status_display().lower()}, not awaiting approval.")
        tenant = pay.tenant
        sub = tenant.active_subscription
        inv = pay.invoice
        if inv is None and sub is not None:
            inv = BillingService.renewal_invoice(sub, periods=int(periods or 1))
            pay.invoice = inv
        pay.status, pay.reviewed_by, pay.reviewed_at, pay.review_note = "paid", actor, timezone.now(), note
        pay.save(update_fields=["invoice", "status", "reviewed_by", "reviewed_at", "review_note"])
        renewed_to = None
        if inv is not None:
            inv.paid_amount += pay.amount
            inv.status = "paid" if inv.paid_amount >= inv.total else "partial"
            inv.save(update_fields=["paid_amount", "status"])
            if inv.status == "partial":
                pay.status = "partial"
                pay.save(update_fields=["status"])
            elif inv.purpose == "renewal" and sub is not None:
                sub, _ = SubscriptionService.renew(sub, periods=inv.periods, actor=actor, charge="invoice", invoice=inv,
                                                   reason=f"payment {pay.reference or pay.id} approved")
                renewed_to = sub.end_date
        if sub is not None:
            SubscriptionEvent.objects.create(subscription=sub, event_type="payment_approved", to_data={"amount": str(pay.amount), "renewed_to": renewed_to.isoformat() if renewed_to else None}, actor=actor, reason=note)
        AuditService.log("platform.payment.approved", pay, after={"amount": str(pay.amount), "renewed_to": str(renewed_to) if renewed_to else None}, actor=actor)
        if renewed_to:
            BillingService.tell_owner(tenant, "payment_receipt", "Payment approved — subscription renewed",
                                      f"Your payment of {pay.currency} {pay.amount:,.2f} is approved. {tenant.display_name} is renewed until {renewed_to:%d %b %Y}.")
        else:
            left = inv.balance if inv is not None else Decimal("0")
            BillingService.tell_owner(tenant, "payment_receipt", "Payment approved",
                                      f"Your payment of {pay.currency} {pay.amount:,.2f} is approved." + (f" {pay.currency} {left:,.2f} is still due on {inv.number}." if left > 0 else ""))
        return pay

    @staticmethod
    @transaction.atomic
    def reject_payment(pay, *, actor=None, reason=""):
        if pay.status != "pending":
            raise DomainError(f"This payment is {pay.get_status_display().lower()}, not awaiting approval.")
        if not (reason or "").strip():
            raise DomainError("Say why the payment is turned down — the company is told.", status_code=400)
        pay.status, pay.reviewed_by, pay.reviewed_at, pay.review_note = "rejected", actor, timezone.now(), reason.strip()
        pay.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note"])
        sub = pay.tenant.active_subscription
        if sub is not None:
            SubscriptionEvent.objects.create(subscription=sub, event_type="payment_rejected", to_data={"amount": str(pay.amount)}, actor=actor, reason=reason)
        AuditService.log("platform.payment.rejected", pay, after={"reason": reason}, actor=actor)
        BillingService.tell_owner(pay.tenant, "payment_rejected", "Payment not approved",
                                  f"Your payment of {pay.currency} {pay.amount:,.2f}{' (ref ' + pay.reference + ')' if pay.reference else ''} was not approved: {reason.strip()}. "
                                  "Check the details and submit it again.")
        return pay

    @staticmethod
    def report(date_from=None, date_to=None):
        """
        The platform's billing record (owner, 1 Nov 2026: "keep record of payments and report"): money received in
        the window by method, month and company, what waits for approval, what was turned down or refunded, what
        is still owed, which renewals fall due in the next 30 days and which companies have lapsed.
        """
        today = timezone.localdate()
        date_from = date.fromisoformat(str(date_from)) if date_from else today.replace(day=1)
        date_to = date.fromisoformat(str(date_to)) if date_to else today
        pays = list(PlatformPayment.objects.filter(paid_at__gte=date_from, paid_at__lte=date_to).select_related("tenant", "invoice", "recorded_by", "reviewed_by", "submitted_by").order_by("paid_at", "created_at"))
        got = [p for p in pays if p.status in ("paid", "partial", "refunded")]
        refunds = sum((r.amount for p in got for r in p.refunds.all() if date_from <= r.at.date() <= date_to), Decimal("0"))
        by = lambda key: {k: str(sum((p.amount for p in got if key(p) == k), Decimal("0"))) for k in sorted({key(p) for p in got})}  # noqa: E731
        invoiced = PlatformInvoice.objects.filter(created_at__date__gte=date_from, created_at__date__lte=date_to, is_credit_note=False).exclude(status="void")
        pending = PlatformPayment.objects.filter(status="pending").select_related("tenant")
        outstanding = {}
        for inv in PlatformInvoice.objects.filter(status__in=BillingService.OPEN, is_credit_note=False).select_related("tenant"):
            row = outstanding.setdefault(str(inv.tenant_id), {"tenant": inv.tenant.display_name, "tenant_id": str(inv.tenant_id), "invoices": 0, "balance": Decimal("0"), "oldest_due": inv.due_date})
            row["invoices"] += 1
            row["balance"] += inv.balance
            if inv.due_date and (row["oldest_due"] is None or inv.due_date < row["oldest_due"]):
                row["oldest_due"] = inv.due_date
        soon, lapsed = [], []
        for sub in Subscription.objects.filter(status__in=["trial", "active", "past_due", "suspended"]).select_related("tenant", "plan").order_by("end_date"):
            if sub.tenant.status in ("cancelled", "archived") or sub != sub.tenant.active_subscription:
                continue
            row = {"tenant": sub.tenant.display_name, "tenant_id": str(sub.tenant_id), "plan": sub.plan.name, "status": sub.tenant.status, "end_date": sub.end_date,
                   "days_left": (sub.end_date - today).days, "grace_until": sub.end_date + timedelta(days=sub.grace_days),
                   "price": SubscriptionService.price_breakdown(sub)["total"]}
            if sub.end_date < today or sub.tenant.status == "suspended":
                lapsed.append(row)
            elif (sub.end_date - today).days <= 30:
                soon.append(row)
        return {
            "date_from": date_from, "date_to": date_to,
            "summary": {"received": str(sum((p.amount for p in got), Decimal("0"))), "refunded": str(refunds), "payments": len(got),
                        "invoiced": str(sum((i.total for i in invoiced), Decimal("0"))), "pending_count": pending.count(),
                        "pending_amount": str(sum((p.amount for p in pending), Decimal("0"))),
                        "rejected": sum(1 for p in pays if p.status == "rejected"),
                        "outstanding": str(sum((r["balance"] for r in outstanding.values()), Decimal("0")))},
            "by_method": {dict(PlatformPayment.METHODS).get(k, k): v for k, v in by(lambda p: p.method).items()},
            "by_month": by(lambda p: p.paid_at.strftime("%Y-%m")),
            "by_tenant": by(lambda p: p.tenant.display_name),
            "payments": [{"id": str(p.id), "date": p.paid_at, "tenant": p.tenant.display_name, "tenant_id": str(p.tenant_id), "invoice": p.invoice.number if p.invoice_id else "",
                          "method": p.get_method_display(), "reference": p.reference, "amount": str(p.amount), "status": p.status, "status_label": p.get_status_display(),
                          "by": (p.reviewed_by or p.recorded_by).full_name if (p.reviewed_by_id or p.recorded_by_id) else "", "submitted_by": p.submitted_by.full_name if p.submitted_by_id else "",
                          "note": p.review_note or p.note} for p in pays],
            "pending": [{"id": str(p.id), "tenant": p.tenant.display_name, "amount": str(p.amount), "method": p.get_method_display(), "date": p.paid_at} for p in pending],
            "outstanding": [{**r, "balance": str(r["balance"])} for r in sorted(outstanding.values(), key=lambda r: -r["balance"])],
            "renewals_due": soon, "lapsed": lapsed,
        }

    @staticmethod
    def tell_owner(tenant, ntype, title, body):
        """The owner hears about their own billing: an inbox message and an email. Never raises."""
        from apps.core.notifications import notify, send_email

        owner = tenant.owner
        if owner is None:
            return
        try:
            notify(owner, f"billing.{ntype}", title, body, {"screen": "subscription"}, tenant_id=tenant.id)
        except Exception:  # noqa: BLE001 — a notification must never undo the billing step it reports
            pass
        if owner.email:
            send_email(owner.email, title, body)

    @staticmethod
    def tell_platform(title, body):
        """The platform's billing desk: the address in Platform settings → Billing, and every admin who may bill."""
        from apps.accounts.models import User
        from apps.core.notifications import send_email

        to = {platform_setting("billing_email", "") or ""}
        for u in User.objects.filter(user_type="PLATFORM_ADMIN", is_active=True):
            if u.email and "platform.manage_billing" in u.platform_perms():
                to.add(u.email)
        for addr in filter(None, to):
            send_email(addr, title, body)

    @staticmethod
    def clean_payment_input(data, invoice):
        """Payment dict from the API: drop blanks, default amount to the invoice total, keep only known fields."""
        allowed = {"amount", "method", "reference", "paid_at", "note", "status"}
        out = {k: v for k, v in (data or {}).items() if k in allowed and v not in (None, "")}
        out.setdefault("amount", invoice.total)
        return out

    @staticmethod
    def _next_invoice_number(prefix="PINV-"):
        year = timezone.now().year
        n = PlatformInvoice.objects.filter(number__startswith=f"{prefix}{year}-").count() + 1
        return f"{prefix}{year}-{n:05d}"

    @staticmethod
    @transaction.atomic
    def create_invoice(tenant, sub, period_start, period_end, *, periods=1, description="", coupon_code=None, override_total=None, actor=None, is_credit_note=False, purpose="manual", due_date=None):
        breakdown = SubscriptionService.price_breakdown(sub, periods=periods)
        lines = [{"description": description or line["label"], "amount": line["amount"]} for line in breakdown["lines"]]
        subtotal = Decimal(breakdown["subtotal"])
        discount = Decimal(breakdown["discount"])  # the tenant's own negotiated discount
        if discount:
            lines.append({"description": f"Discount ({sub.discount_label})", "amount": f"-{discount}"})
            sub.discount_used += 1
            sub.save(update_fields=["discount_used"])
        coupon = None
        if coupon_code:
            coupon = Coupon.objects.filter(code__iexact=coupon_code).first()
            if coupon is None or not coupon.is_valid(sub.plan):
                raise DomainError("Invalid or expired coupon.")
            discount += coupon.discount_for(subtotal - discount)
            coupon.used_count += 1
            coupon.save(update_fields=["used_count"])
        if override_total is not None:
            subtotal = Decimal(override_total)
            lines = [{"description": description, "amount": str(subtotal)}]
            discount = Decimal("0")
        total = subtotal - discount
        if total < 0:
            is_credit_note = True
            total = -total
            subtotal = total
        inv = PlatformInvoice.objects.create(
            tenant=tenant, subscription=sub, number=BillingService._next_invoice_number("PCN-" if is_credit_note else "PINV-"), period_start=period_start, period_end=period_end,
            description=description, subtotal=subtotal, discount=discount, tax=Decimal("0"), total=total, currency=sub.plan_snapshot.get("currency", "PKR"),
            status="issued" if total > 0 else "paid", due_date=due_date or period_start + timedelta(days=7), lines=lines, is_credit_note=is_credit_note,
            purpose=purpose, periods=periods,
        )
        AuditService.log("platform.invoice.created", inv, after={"number": inv.number, "total": str(total)}, actor=actor)
        return inv

    @staticmethod
    @transaction.atomic
    def record_payment(tenant, *, amount, method="bank_transfer", reference="", paid_at=None, note="", invoice=None, receipt_file=None, actor=None, status="paid"):
        amount = Decimal(amount)
        if amount <= 0:
            raise DomainError("Amount must be positive.")
        pay = PlatformPayment.objects.create(tenant=tenant, invoice=invoice, amount=amount, currency=tenant.base_currency, method=method, reference=reference, paid_at=paid_at or timezone.localdate(), recorded_by=actor, receipt_file=receipt_file, note=note, status=status)
        if invoice is not None and status == "paid":
            invoice.paid_amount += amount
            invoice.status = "paid" if invoice.paid_amount >= invoice.total else "partial"
            invoice.save(update_fields=["paid_amount", "status"])
            if invoice.status == "partial":
                pay.status = "partial"
                pay.save(update_fields=["status"])
        AuditService.log("platform.payment.recorded", pay, after={"amount": str(amount), "method": method, "invoice": invoice.number if invoice else None}, actor=actor)
        return pay

    @staticmethod
    @transaction.atomic
    def refund(payment, amount, reason, actor=None):
        amount = Decimal(amount)
        refunded = sum((r.amount for r in payment.refunds.all()), Decimal("0"))
        if amount <= 0 or refunded + amount > payment.amount:
            raise DomainError("Refund exceeds payment amount.")
        r = PlatformRefund.objects.create(payment=payment, amount=amount, reason=reason, recorded_by=actor)
        if refunded + amount >= payment.amount:
            payment.status = "refunded"
            payment.save(update_fields=["status"])
        if payment.invoice:
            inv = payment.invoice
            inv.paid_amount -= amount
            inv.status = "paid" if inv.paid_amount >= inv.total else ("partial" if inv.paid_amount > 0 else "issued")
            inv.save(update_fields=["paid_amount", "status"])
        AuditService.log("platform.payment.refunded", payment, after={"amount": str(amount), "reason": reason}, actor=actor)
        return r

    @staticmethod
    def invoice_pdf(invoice):
        from django.template.loader import render_to_string

        html = render_to_string("platform/invoice.html", {"inv": invoice, "tenant": invoice.tenant})
        from weasyprint import HTML

        return HTML(string=html).write_pdf()


class DashboardService:
    @staticmethod
    def metrics():
        from django.db.models import Count, Sum

        today = timezone.localdate()
        month_start = today.replace(day=1)
        subs = Subscription.objects.filter(status__in=["active", "past_due"]).select_related("plan")
        mrr = Decimal("0")
        by_plan = {}
        for s in subs:
            price = Decimal(s.plan_snapshot.get("price", "0"))
            days = int(s.plan_snapshot.get("period_days", 30)) or 30
            monthly = price * 30 / days
            mrr += monthly
            by_plan[s.plan_snapshot.get("name", s.plan.name)] = by_plan.get(s.plan_snapshot.get("name", s.plan.name), Decimal("0")) + monthly
        tenants_by_status = dict(Tenant.objects.values_list("status").annotate(c=Count("id")).values_list("status", "c"))
        payments_month = PlatformPayment.objects.filter(paid_at__gte=month_start, status__in=["paid", "partial"]).aggregate(s=Sum("amount"))["s"] or 0
        past_due = PlatformInvoice.objects.filter(status__in=["issued", "partial", "overdue"], due_date__lt=today).aggregate(s=Sum("total") - Sum("paid_amount"))["s"] or 0
        churn = SubscriptionEvent.objects.filter(event_type="cancelled", at__date__gte=month_start).count()
        from apps.integrations.models import ApiUsage
        from apps.sales.models import SalesOrder

        top = list(SalesOrder.all_objects.filter(created_at__date__gte=month_start).values("tenant__display_name").annotate(c=Count("id")).order_by("-c")[:5])
        return {
            "mrr": str(mrr.quantize(Decimal("0.01"))), "arr": str((mrr * 12).quantize(Decimal("0.01"))),
            "active_tenants": tenants_by_status.get("active", 0), "trial_tenants": tenants_by_status.get("trial", 0), "past_due_tenants": tenants_by_status.get("past_due", 0), "suspended_tenants": tenants_by_status.get("suspended", 0),
            "churn_month": churn, "revenue_by_plan": {k: str(v.quantize(Decimal("0.01"))) for k, v in by_plan.items()},
            "payments_month": str(payments_month), "past_due_amount": str(past_due), "top_tenants_by_orders": top,
            "api_calls_month": ApiUsage.objects.filter(at__date__gte=month_start).count(),
            "tenants_by_status": tenants_by_status,
            # the billing desk's to-do (owner, 1 Nov 2026): payments to approve, renewals coming up
            "pending_payments": PlatformPayment.objects.filter(status="pending").count(),
            "pending_amount": str(PlatformPayment.objects.filter(status="pending").aggregate(s=Sum("amount"))["s"] or 0),
            "expiring_7_days": Subscription.objects.filter(status__in=["trial", "active"], end_date__gte=today, end_date__lte=today + timedelta(days=7)).count(),
        }


def seed_platform_roles():
    from apps.accounts.models import GroupProfile
    from apps.accounts.permissions_catalog import PLATFORM_CATALOG
    from apps.accounts.services import RoleService, ensure_permission_catalog

    ensure_permission_catalog()
    all_p = [f"platform.{c}" for c, _ in PLATFORM_CATALOG["platform"][1]]
    specs = {
        "Super Admin": all_p,
        "Billing Admin": ["platform.manage_billing", "platform.manage_subscriptions", "platform.view_dashboard", "platform.manage_tenants"],
        "Support": ["platform.impersonate", "platform.view_dashboard", "platform.view_audit"],
        "Viewer": ["platform.view_dashboard", "platform.view_audit"],
    }
    for name, perms in specs.items():
        if not GroupProfile.objects.filter(tenant=None, name=name).exists():
            RoleService.create_role(None, name, perms, role_type="platform", description="Platform role", is_system=True)


def purge_tenant(tenant):
    """Hard-delete every row belonging to a tenant (archive purge job / demo reset). Runs in one transaction so
    PROTECT/ordering does not matter (FK constraints are deferred until commit)."""
    from django.apps import apps as django_apps
    from django.db import transaction

    from apps.accounts.models import GroupProfile, ImpersonationSession, TenantMembership
    from apps.core.models import AuditLog, IdempotencyKey, Notification, TenantModel

    with transaction.atomic():
        AuditLog.objects.filter(tenant=tenant).delete()
        IdempotencyKey.objects.filter(tenant=tenant).delete()
        Notification.all_objects.filter(tenant=tenant)._raw_delete(Notification.all_objects.db)
        for model in django_apps.get_models():
            if issubclass(model, TenantModel):
                for m2m in model._meta.many_to_many:
                    through = m2m.remote_field.through
                    through.objects.filter(**{f"{m2m.m2m_field_name()}__tenant": tenant}).delete()
                qs = model.all_objects.filter(tenant=tenant)
                qs._raw_delete(qs.db)
        ImpersonationSession.objects.filter(tenant=tenant).delete()
        TenantMembership.objects.filter(tenant=tenant).delete()
        for gp in GroupProfile.objects.filter(tenant=tenant):
            gp.group.delete()
        tenant.subscriptions.all().delete()
        tenant.platform_invoices.all().delete()
        tenant.platform_payments.all().delete()
        tenant.tickets.all().delete()
        Tenant.objects.filter(pk=tenant.pk).delete()


# Transactional rows wiped by `reset_tenant_data`, written out model by model on purpose. A rule such as
# "every DocumentModel" would quietly start deleting whatever setup model someone adds next, and the thing on
# the other side of this list is a product catalogue and a customer book that took weeks to build.
RESET_TRANSACTIONAL_MODELS = [
    # selling
    "sales.SalesOrderLine", "sales.SalesInvoiceLine", "sales.CreditNoteLine", "sales.StockReservation",
    "sales.CreditNote", "sales.SalesInvoice", "sales.SalesOrder", "sales.RepCollection", "sales.RepSettlement",
    # buying
    "purchasing.PurchaseOrderLine", "purchasing.GoodsReceiptLine", "purchasing.PurchaseReturnLine", "purchasing.LandedCost",
    "purchasing.PurchaseBill", "purchasing.PurchaseReturn", "purchasing.GoodsReceipt", "purchasing.PurchaseOrder",
    # getting it to the shop
    "delivery.DeliveryNoteLine", "delivery.PickListLine", "delivery.DeliveryNote", "delivery.PickList",
    "delivery.DeliveryTrip", "delivery.VehicleLog",
    # stock
    "inventory.StockAdjustmentLine", "inventory.StockCountLine", "inventory.StockTransferLine", "inventory.StockMovement",
    "inventory.StockLevel", "inventory.StockAdjustment", "inventory.StockCount", "inventory.StockTransfer",
    "inventory.Batch", "inventory.InventoryAlert",
    # books
    "accounting.PaymentAllocation", "accounting.JournalLine", "accounting.Payment", "accounting.Expense",
    "accounting.OpeningBalanceBatch", "accounting.BankStatementLine", "accounting.JournalEntry",
    # field force
    "partners.CustomerVisit", "partners.LocationPing", "partners.GeoFenceEvent",
    # per-document odds and ends
    "core.Attachment", "core.Notification", "integrations.ImportJob", "integrations.WebhookDelivery",
]

# Values that live on a *kept* row but are produced by the history being deleted. Blanked before the deletes,
# both because `_raw_delete` skips Django's SET_NULL pass (a kept row still pointing at a deleted journal entry
# would break the transaction at commit) and because a balance with no entries behind it is not a clean slate.
RESET_KEPT_ROW_FIELDS = [
    # the running weighted average cost, maintained by stock movements; `cost_price`, which the owner types, stays
    ("catalog.ProductVariant", {"avg_cost": Decimal("0")}),
    # an opening balance is money owed on day one — a balance, not a setting, so it goes with the rest of the ledger
    ("partners.Customer", {"opening_balance": Decimal("0"), "opening_balance_date": None, "opening_journal": None}),
    ("partners.Supplier", {"opening_balance": Decimal("0"), "opening_balance_date": None, "opening_journal": None}),
    ("accounting.Account", {"opening_balance": Decimal("0")}),
    ("accounting.BankAccount", {"opening_balance": Decimal("0")}),
    # a period left closed over the old books would refuse the first order after the reset ("period is closed")
    ("accounting.FiscalYear", {"status": "open", "closed_by": None, "closing_entry": None}),
    ("accounting.AccountingPeriod", {"status": "open", "closed_by": None, "closed_at": None}),
]


def reset_tenant_data(tenant, *, actor=None, request=None, dry_run=False):
    """
    Wipe one company's trading history and leave its setup standing, so a distributor who has been trying the
    system out — or whose data import went wrong — can start from zero without rebuilding the catalogue, the
    customer book and the user accounts.

    Deleted: sales orders, invoices, credit notes, deliveries, trips and pick lists, purchase orders, goods
    receipts, bills and returns, payments and receipts, rep collections and settlements, stock movements and
    levels, batches, adjustments, counts and transfers, inventory alerts, journal entries and lines, expenses,
    opening-balance batches, bank statement lines, customer visits, location pings, geofence events,
    attachments, notifications, import jobs and webhook deliveries.

    Kept: the company, its users, roles and warehouse access, warehouses and bins, products, variants,
    barcodes, brands, categories, units, packagings, price lists, schemes, tax classes and rates, customers,
    suppliers, reps, territories, routes, working areas, drivers and vehicles, the chart of accounts, cash and
    bank accounts, cost centres, posting rules, fiscal calendar, reason codes, API keys, webhook endpoints,
    saved reports, every setting — and the audit trail, which is the only record that this ever happened.

    Document numbering restarts at 1 while each sequence keeps the company's own prefix and padding.
    `dry_run=True` counts what would go and changes nothing: it is what the admin screen shows before it asks
    for the slug to be typed.
    """
    from django.apps import apps as django_apps

    from apps.core.models import IdempotencyKey, NumberSequence

    if tenant is None or tenant.pk is None:
        raise DomainError("Pick a company to reset.", code="tenant_required", status_code=400)

    deleted = {}
    with transaction.atomic():
        # Every query names `tenant=tenant` through `all_objects` instead of leaning on the tenant context:
        # a context that silently failed to set would turn this into a wipe of every company on the server.
        if not dry_run:
            for label, fields in RESET_KEPT_ROW_FIELDS:
                django_apps.get_model(label).all_objects.filter(tenant=tenant).update(**fields)

        # Join rows first, in their own pass: a pick list's orders are deleted in a later block than the
        # pick list itself, and a join row left pointing at a deleted order fails the whole transaction.
        # The preview counts them too, or its total is short by one per load form.
        for label in RESET_TRANSACTIONAL_MODELS:
            model = django_apps.get_model(label)
            for m2m in model._meta.many_to_many:
                through = m2m.remote_field.through
                rows = through.objects.filter(**{f"{m2m.m2m_field_name()}__tenant": tenant})
                n = rows.count() if dry_run else rows.delete()[0]
                if n:
                    deleted[through._meta.label] = n

        for label in RESET_TRANSACTIONAL_MODELS:
            qs = django_apps.get_model(label).all_objects.filter(tenant=tenant)
            # `_raw_delete` skips the cascade collector; FK constraints are deferred to commit, so one
            # statement per table is enough and the order above is only for a reader's benefit.
            n = qs.count() if dry_run else (qs._raw_delete(qs.db) or 0)
            if n:
                deleted[label] = n

        keys = IdempotencyKey.objects.filter(tenant=tenant)
        # Cached POST responses naming documents that no longer exist: a phone retrying an old key would be
        # handed back an invoice id that has been deleted.
        n = keys.count() if dry_run else keys.delete()[0]
        if n:
            deleted["core.IdempotencyKey"] = n

        seqs = NumberSequence.all_objects.filter(tenant=tenant)
        numbering = seqs.count() if dry_run else seqs.update(next_number=1, last_reset_year=None)
        result = {"deleted": deleted, "total": sum(deleted.values()), "numbering_reset": numbering, "dry_run": dry_run}
        if not dry_run:
            # Stamped with the tenant so the reset shows on the company's own audit tab, not only the platform log.
            AuditService.log("platform.tenant.data_reset", tenant, after=result, actor=actor, request=request, tenant_id=tenant.id)
    return result


def tenant_is_paying(tenant):
    """
    A company we are taking money from right now. Resetting one of these by a misclick is the failure that
    matters, so the API asks for a second, explicit acknowledgement on top of the typed slug. Past due counts:
    they are a paying customer who is late, and their data is no less real.
    """
    sub = tenant.active_subscription
    if sub is None or sub.status not in ("active", "past_due"):
        return False
    price = sub.plan_snapshot.get("price") if sub.plan_snapshot else None
    if price is None:
        price = sub.plan.price if sub.plan_id else 0
    return Decimal(str(price or 0)) > 0
