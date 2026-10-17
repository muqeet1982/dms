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
        from apps.accounts.models import TenantMembership
        from apps.catalog.models import ProductVariant
        from apps.core.models import Attachment
        from apps.inventory.models import Warehouse
        from apps.integrations.models import ApiKey, ApiUsage
        from apps.partners.models import Supplier, SupplierRep
        from apps.sales.models import SalesOrder

        month_start = timezone.localdate().replace(day=1)
        storage = Attachment.all_objects.filter(tenant=tenant).aggregate(s=__import__("django.db.models", fromlist=["Sum"]).Sum("size"))["s"] or 0
        last = tenant.audit_logs.order_by("-at").values_list("at", flat=True).first()
        return {
            "users": TenantMembership.objects.filter(tenant=tenant, status="active", user__user_type="TENANT").count(),
            "reps": SupplierRep.all_objects.filter(tenant=tenant, is_active=True).count(),
            "warehouses": Warehouse.all_objects.filter(tenant=tenant, is_active=True).count(),
            "suppliers": Supplier.all_objects.filter(tenant=tenant, is_active=True).count(),
            "products": ProductVariant.all_objects.filter(tenant=tenant).count(),
            "orders_month": SalesOrder.all_objects.filter(tenant=tenant, created_at__date__gte=month_start).count(),
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
        ImpersonationSession.objects.filter(admin=admin, ended_at__isnull=True).update(ended_at=timezone.now())
        session = ImpersonationSession.objects.create(admin=admin, target=tenant.owner, tenant=tenant, reason=reason, expires_at=timezone.now() + timedelta(minutes=minutes or platform_setting("impersonation_minutes", settings.DMS["IMPERSONATION_MINUTES"])))
        AuditService.log("platform.impersonation.started", tenant, after={"target": str(tenant.owner), "reason": reason, "expires_at": session.expires_at.isoformat()}, actor=admin)
        return session

    @staticmethod
    def end_impersonation(admin):
        from apps.accounts.models import ImpersonationSession

        n = ImpersonationSession.objects.filter(admin=admin, ended_at__isnull=True).update(ended_at=timezone.now())
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
    def renew(sub, *, periods=1, from_today=False, actor=None, record_payment=None, coupon_code=None, reason=""):
        plan = sub.plan
        today = timezone.localdate()
        base = today if (from_today or sub.end_date < today) else sub.end_date
        new_end = SubscriptionService._period_end(base, plan, periods)
        before = {"status": sub.status, "end_date": sub.end_date.isoformat()}
        sub.end_date = new_end
        sub.status = "active"
        sub.trial_end = None
        sub.save()
        tenant = sub.tenant
        tenant.status = "active"
        tenant.suspended_at = None
        tenant.suspended_reason = ""
        tenant.save()
        invoice = BillingService.create_invoice(tenant, sub, base, new_end, periods=periods, coupon_code=coupon_code, actor=actor)
        if record_payment:
            BillingService.record_payment(tenant, invoice=invoice, actor=actor, **BillingService.clean_payment_input(record_payment, invoice))
        SubscriptionEvent.objects.create(subscription=sub, event_type="renewed", from_data=before, to_data={"status": "active", "end_date": new_end.isoformat(), "invoice": invoice.number}, actor=actor, reason=reason)
        AuditService.log("platform.subscription.renewed", sub, before=before, after={"end_date": new_end.isoformat()}, actor=actor)
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
            invoice = BillingService.create_invoice(sub.tenant, sub, timezone.localdate(), sub.end_date, description=f"Plan change to {new_plan.name} (pro-rata)", override_total=amount, actor=actor)
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
    def run_status_checks():
        """Daily job: trial/active → past_due after end_date, → suspended after grace."""
        today = timezone.localdate()
        changed = []
        for sub in Subscription.objects.filter(status__in=["trial", "active", "past_due"], auto_status_change=True).select_related("tenant"):
            tenant = sub.tenant
            if tenant.status in ("cancelled", "archived"):
                continue
            new = None
            if sub.end_date < today:
                if (today - sub.end_date).days > sub.grace_days:
                    new = "suspended"
                else:
                    new = "past_due"
            if new and new != sub.status:
                before = sub.status
                sub.status = new
                sub.save(update_fields=["status"])
                tenant.status = new
                if new == "suspended":
                    tenant.suspended_at = timezone.now()
                    tenant.suspended_reason = "Subscription expired"
                tenant.save()
                SubscriptionEvent.objects.create(subscription=sub, event_type="status_changed", from_data={"status": before}, to_data={"status": new}, reason="automatic")
                changed.append((tenant.slug, new))
        return changed

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
    def create_invoice(tenant, sub, period_start, period_end, *, periods=1, description="", coupon_code=None, override_total=None, actor=None, is_credit_note=False):
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
            status="issued" if total > 0 else "paid", due_date=period_start + timedelta(days=7), lines=lines, is_credit_note=is_credit_note,
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
            for label in RESET_TRANSACTIONAL_MODELS:
                model = django_apps.get_model(label)
                for m2m in model._meta.many_to_many:
                    through = m2m.remote_field.through
                    n = through.objects.filter(**{f"{m2m.m2m_field_name()}__tenant": tenant}).delete()[0]
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
