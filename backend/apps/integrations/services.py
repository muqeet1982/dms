import hashlib
import hmac
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from .models import WebhookDelivery, WebhookEndpoint

logger = logging.getLogger(__name__)


def _order_payload(o):
    return {"id": str(o.id), "number": o.number, "status": o.status, "external_ref": o.external_ref, "customer": {"id": str(o.customer_id), "code": o.customer.code, "name": o.customer.name}, "rep_code": o.rep.code if o.rep_id else None, "supplier_id": str(o.supplier_id) if o.supplier_id else None, "order_date": o.order_date, "total": str(o.total), "trip": o.trip.number if o.trip_id else None, "rejection_reason": o.rejection_reason, "lines": [{"sku": l.variant.sku, "qty": str(l.qty_ordered), "unit_price": str(l.unit_price), "delivered": str(l.qty_delivered)} for l in o.lines.select_related("variant")]}


class WebhookService:
    @staticmethod
    def build_payload(event, obj):
        from apps.delivery.models import DeliveryNote
        from apps.sales.models import RepCollection, SalesInvoice, SalesOrder

        supplier_id = None
        if isinstance(obj, SalesOrder):
            data, supplier_id = _order_payload(obj), obj.supplier_id
        elif isinstance(obj, DeliveryNote):
            data, supplier_id = {"id": str(obj.id), "number": obj.number, "status": obj.status, "order": _order_payload(obj.order), "delivered_at": obj.delivered_at, "receiver_name": obj.receiver_name, "cash_collected": str(obj.cash_collected)}, obj.order.supplier_id
        elif isinstance(obj, SalesInvoice):
            data, supplier_id = {"id": str(obj.id), "number": obj.number, "date": obj.date, "due_date": obj.due_date, "total": str(obj.total), "customer": {"id": str(obj.customer_id), "code": obj.customer.code, "name": obj.customer.name}, "order": obj.order.number if obj.order_id else None}, (obj.order.supplier_id if obj.order_id else None)
        elif isinstance(obj, RepCollection):
            data, supplier_id = {"id": str(obj.id), "number": obj.number, "amount": str(obj.amount), "customer": obj.customer.name, "rep_code": obj.rep.code, "date": obj.date}, obj.rep.supplier_id
        else:
            data = {"id": str(getattr(obj, "id", "")), "number": getattr(obj, "number", ""), "type": obj.__class__.__name__}
        return data, supplier_id

    @staticmethod
    def dispatch(event, obj):
        data, supplier_id = WebhookService.build_payload(event, obj)
        endpoints = WebhookEndpoint.objects.filter(is_active=True, events__contains=[event])
        for ep in endpoints:
            if ep.supplier_id and supplier_id and str(ep.supplier_id) != str(supplier_id):
                continue
            payload = {"event": event, "id": str(getattr(obj, "id", "")), "occurred_at": timezone.now().isoformat(), "data": json.loads(json.dumps(data, cls=DjangoJSONEncoder))}
            d = WebhookDelivery.objects.create(endpoint=ep, event=event, payload=payload, next_attempt_at=timezone.now())
            from .tasks import deliver_webhook

            deliver_webhook.delay(str(d.id))

    @staticmethod
    def sign(secret, body: bytes):
        return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    @staticmethod
    def attempt(delivery: WebhookDelivery):
        import requests

        body = json.dumps(delivery.payload, cls=DjangoJSONEncoder).encode()
        sig = WebhookService.sign(delivery.endpoint.secret, body)
        delivery.attempts += 1
        try:
            resp = requests.post(delivery.endpoint.url, data=body, headers={"Content-Type": "application/json", "X-DMS-Signature": f"sha256={sig}", "X-DMS-Event": delivery.event, "X-DMS-Delivery": str(delivery.id)}, timeout=10)
            delivery.last_status_code = resp.status_code
            if 200 <= resp.status_code < 300:
                delivery.status = "delivered"
                delivery.delivered_at = timezone.now()
                delivery.last_error = ""
            else:
                delivery.last_error = resp.text[:500]
                WebhookService._schedule_retry(delivery)
        except Exception as e:
            delivery.last_error = str(e)[:500]
            WebhookService._schedule_retry(delivery)
        delivery.save()
        return delivery

    @staticmethod
    def _schedule_retry(delivery):
        if delivery.attempts >= settings.DMS["WEBHOOK_MAX_ATTEMPTS"]:
            delivery.status = "failed"
            delivery.next_attempt_at = None
        else:
            delivery.status = "pending"
            delivery.next_attempt_at = timezone.now() + timedelta(minutes=2 ** delivery.attempts)


class ImportService:
    REQUIRED = {"products": ["sku", "name"], "customers": ["name"], "suppliers": ["name"], "opening_stock": ["sku", "warehouse", "qty"], "opening_balances": ["account", "debit", "credit"], "price_list": ["sku", "price"]}

    @staticmethod
    def run(job):
        from django.db import transaction

        from .importers import ProductImporter, PurchaseOrderImporter, SheetReader, read_flat

        job.status = "running"
        job.save(update_fields=["status"])
        filename = job.file.name
        importer = None
        try:
            if job.type in ("products", "purchase_order"):
                rows, meta = SheetReader.read(job.file, filename)
                if job.type == "products":
                    importer = ProductImporter(job.options or {}, meta)
                    if meta.get("date"):
                        job.summary["price_list_date"] = meta["date"].isoformat()
                else:
                    importer = PurchaseOrderImporter(job.options or {}, meta, filename=(job.options or {}).get("filename") or filename)
                    if meta.get("qty_column_guessed"):
                        job.summary["qty_column"] = "unlabeled column (guessed)"
                if meta.get("title_rows"):
                    job.summary["title"] = " · ".join(meta["title_rows"][:3])[:200]
            else:
                rows = read_flat(job.file, filename)
        except Exception as e:
            job.status, job.rows_total, job.errors = "failed", 0, [{"row": 0, "error": str(e)[:300], "data": {}}]
            job.save()
            return job
        job.rows_total = len(rows)
        errors, ok = [], 0
        for row in rows:
            line = row.pop("_row", 0)
            try:
                with transaction.atomic():
                    if importer is not None:
                        if importer.import_row(row) is None:  # e.g. an order-sheet row without a quantity
                            continue
                    else:
                        ImportService._import_row(job.type, row, job.options)
                ok += 1
            except Exception as e:
                errors.append({"row": line, "error": str(e)[:200], "data": {k: str(v)[:80] for k, v in row.items()}})
        if importer is not None and hasattr(importer, "finish"):
            try:
                importer.finish(job)
            except Exception as e:
                errors.insert(0, {"row": 0, "error": str(e)[:300], "data": {}})
                ok = 0
        job.rows_ok, job.rows_failed, job.errors = ok, len(errors), errors[:500]
        if importer is not None:
            job.summary.update(importer.summary)
            if "skipped_no_qty" in importer.summary:  # order sheets: rows without a quantity are not failures
                job.rows_total = max(0, job.rows_total - importer.summary["skipped_no_qty"])
        job.status = "done" if not errors or ok else "failed"
        job.save()
        return job

    @staticmethod
    def _import_row(kind, r, options):
        from decimal import Decimal

        if kind == "products":
            from apps.catalog.models import Brand, Category, TaxClass, UnitOfMeasure
            from apps.catalog.services import ProductService
            from apps.partners.models import Supplier

            uom, _ = UnitOfMeasure.objects.get_or_create(code=(r.get("uom") or "PC").upper(), defaults={"name": r.get("uom") or "Piece"})
            brand = Brand.objects.get_or_create(name=r["brand"])[0] if r.get("brand") else None
            cat = Category.objects.get_or_create(name=r["category"], parent=None)[0] if r.get("category") else None
            supplier = Supplier.objects.filter(name__iexact=r["supplier"]).first() if r.get("supplier") else None
            tax_class = TaxClass.objects.filter(name__iexact=r["tax_class"]).first() if r.get("tax_class") else None
            data = {"sku": r["sku"], "name": r["name"], "brand": brand, "category": cat, "supplier": supplier, "base_uom": uom, "tax_class": tax_class, "cost_price": Decimal(r.get("cost_price") or 0), "sale_price": Decimal(r.get("sale_price") or 0), "min_stock": Decimal(r.get("min_stock") or 0), "max_stock": Decimal(r.get("max_stock") or 0), "reorder_qty": Decimal(r.get("reorder_qty") or 0), "is_batch_tracked": r.get("batch_tracked", "").lower() in ("1", "yes", "true"), "is_expiry_tracked": r.get("expiry_tracked", "").lower() in ("1", "yes", "true")}
            from apps.core.tenancy import get_current_tenant

            product = ProductService.create_product(get_current_tenant(), data)
            v = product.variants.first()
            if r.get("barcode"):
                from apps.catalog.models import Barcode

                Barcode.objects.get_or_create(code=r["barcode"], defaults={"variant": v, "type": "EAN13" if len(r["barcode"]) == 13 else "CODE128", "is_primary": True})
            for pack, qty in (("box", r.get("box_qty")), ("carton", r.get("carton_qty"))):
                if qty:
                    from apps.catalog.models import Packaging

                    pu, _ = UnitOfMeasure.objects.get_or_create(code=pack[:3].upper(), defaults={"name": pack.title()})
                    Packaging.objects.get_or_create(variant=v, name=pack, defaults={"uom": pu, "qty_in_base": Decimal(qty), "barcode": r.get(f"{pack}_barcode", "")})
        elif kind == "customers":
            from apps.partners.models import Route, Territory
            from apps.partners.services import CustomerService

            terr = Territory.objects.get_or_create(name=r["territory"])[0] if r.get("territory") else None
            route = Route.objects.get_or_create(name=r["route"], defaults={"territory": terr})[0] if r.get("route") else None
            CustomerService.create({"code": r.get("code") or "", "name": r["name"], "type": r.get("type") or "retailer", "phone": r.get("phone", ""), "address": r.get("address", ""), "city": r.get("city", ""), "territory": terr, "route": route, "credit_limit": Decimal(r.get("credit_limit") or 0), "credit_days": int(r.get("credit_days") or 0), "opening_balance": Decimal(r.get("opening_balance") or 0), "contact_person": r.get("contact_person", "")})
        elif kind == "suppliers":
            from apps.core.numbering import next_number
            from apps.partners.models import Supplier

            Supplier.objects.create(code=r.get("code") or next_number("supplier"), name=r["name"], phone=r.get("phone", ""), email=r.get("email", ""), address=r.get("address", ""), city=r.get("city", ""), tax_no=r.get("tax_no", ""), payment_terms_days=int(r.get("payment_terms_days") or 30), contact_person=r.get("contact_person", ""))
        elif kind == "opening_stock":
            from apps.catalog.models import ProductVariant
            from apps.inventory.models import Warehouse
            from apps.inventory.services import StockService

            v = ProductVariant.objects.get(sku=r["sku"])
            wh = Warehouse.objects.get(code=r["warehouse"])
            batch = StockService.get_or_create_batch(v, r.get("batch_no"), r.get("expiry_date") or None) if r.get("batch_no") else None
            StockService.move(warehouse=wh, variant=v, qty=Decimal(r["qty"]), movement_type="opening", unit_cost=Decimal(r.get("unit_cost") or v.cost_price), batch=batch, note="Opening stock import")
        elif kind == "opening_balances":
            from apps.accounting.models import Account, OpeningBalanceBatch

            batch = OpeningBalanceBatch.objects.filter(posted=False, notes="import").first()
            if batch is None:
                from apps.core.numbering import next_number

                batch = OpeningBalanceBatch.objects.create(number=next_number("opening_balance"), notes="import")
            acct = Account.objects.get(code=r["account"])
            batch.lines.append({"account": str(acct.id), "debit": r.get("debit") or "0", "credit": r.get("credit") or "0", "partner_type": r.get("partner_type", ""), "partner_id": r.get("partner_id", "")})
            batch.save(update_fields=["lines"])
        elif kind == "price_list":
            from apps.catalog.models import PriceList, PriceListItem, ProductVariant

            pl = PriceList.objects.get(name=options.get("price_list") or r.get("price_list") or "Retail")
            v = ProductVariant.objects.get(sku=r["sku"])
            PriceListItem.objects.update_or_create(price_list=pl, variant=v, packaging=None, min_qty=Decimal(r.get("min_qty") or 0), defaults={"price": Decimal(r["price"])})
        else:
            raise ValueError(f"Unknown import type {kind}")
