"""
seed_demo: platform admin (admin / pak1stan), four plans, one demo tenant with owner, warehouses, vehicles, drivers,
a supplier with two reps (external role + login), 30 products with barcodes, 20 customers, taxes, and a week of
sample transactions (PO → GRN → bill, orders, a trip, deliveries, receipts, a rep collection and a settlement).
"""
import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.core.tenancy import bypass_tenant, tenant_context


class Command(BaseCommand):
    help = "Seed demo data (platform admin, plans, demo tenant with a week of transactions)."

    def add_arguments(self, parser):
        parser.add_argument("--admin-user", default="admin")
        parser.add_argument("--admin-password", default="pak1stan")
        parser.add_argument("--owner-email", default="owner@demo.pk")
        parser.add_argument("--owner-password", default="owner123")
        parser.add_argument("--tenant-slug", default="demo")
        parser.add_argument("--skip-transactions", action="store_true")
        parser.add_argument("--reset", action="store_true", help="Delete the demo tenant first")

    def handle(self, *args, **o):
        random.seed(42)
        from apps.accounts.models import User
        from apps.accounts.services import ensure_permission_catalog
        from apps.platform.models import Plan, Tenant
        from apps.platform.services import TenantService, seed_platform_defaults, seed_platform_roles

        ensure_permission_catalog()
        seed_platform_defaults()
        seed_platform_roles()
        admin = User.objects.filter(username=o["admin_user"]).first()
        if admin is None:
            admin = User(username=o["admin_user"], email="admin@dms.invoiceflow.pk", full_name="Platform Admin", user_type="PLATFORM_ADMIN", is_staff=True, is_superuser=True)
            admin.set_password(o["admin_password"])
            admin.save()
            self.stdout.write(f"Platform admin: {o['admin_user']} / {o['admin_password']}")
        from apps.accounts.models import GroupProfile

        sa = GroupProfile.objects.filter(tenant=None, name="Super Admin").first()
        if sa:
            admin.groups.add(sa.group)

        with bypass_tenant():
            tenant = Tenant.objects.filter(slug=o["tenant_slug"]).first()
            if tenant is not None and o["reset"]:
                from apps.platform.services import purge_tenant

                purge_tenant(tenant)
                tenant = None
                self.stdout.write("Deleted existing demo tenant.")
        if tenant is not None:
            self.stdout.write(f"Tenant '{o['tenant_slug']}' already exists; skipping tenant seed.")
            return
        growth = Plan.objects.get(code="growth", is_current=True)
        tenant = TenantService.create_tenant(
            legal_name="Demo Distributors (Pvt) Ltd", display_name="Demo Distributors", slug=o["tenant_slug"], address="Plot 12, Industrial Area, Lahore", city="Lahore", phone="+92421234567", email="info@demo.pk",
            tax_numbers={"NTN": "1234567-8", "STRN": "03-01-2345-678-90"}, owner_name="Demo Owner", owner_email=o["owner_email"], owner_password=o["owner_password"], plan=growth, created_by=admin, send_invite=False,
        )
        owner = tenant.owner
        owner.must_change_password = False
        owner.save(update_fields=["must_change_password"])
        self.stdout.write(f"Tenant: {tenant.display_name} · Owner: {o['owner_email']} / {o['owner_password']}")
        with tenant_context(tenant, owner):
            self.seed_tenant(tenant, owner, skip_tx=o["skip_transactions"])
        self.stdout.write(self.style.SUCCESS("Demo data ready."))

    # ------------------------------------------------------------------ tenant
    @transaction.atomic
    def seed_tenant(self, tenant, owner, skip_tx=False):
        from apps.accounts.models import GroupProfile
        from apps.accounts.services import UserService
        from apps.catalog.models import Barcode, Brand, Category, Packaging, PriceList, PriceListItem, Scheme, TaxClass, TaxRate, TaxType, UnitOfMeasure
        from apps.catalog.services import ProductService
        from apps.delivery.models import Driver, Vehicle
        from apps.inventory.models import Warehouse
        from apps.partners.models import Customer, Route, Supplier, Territory
        from apps.partners.services import SupplierService

        today = timezone.localdate()
        # --- tax types & classes (§5.1)
        from apps.accounting.models import Account

        out1, out2 = Account.objects.get(code="2-01-002-0001"), Account.objects.get(code="2-01-002-0002")
        in1, in2 = Account.objects.get(code="1-01-005-0003"), Account.objects.get(code="1-01-005-0004")
        st = TaxType.objects.create(code="ST", name="Sales Tax", kind="sales_tax", applies_to="both", output_account=out1, input_account=in1)
        TaxRate.objects.create(tax_type=st, name="Standard", rate_pct=Decimal("18"), valid_from=today - timedelta(days=365))
        ft = TaxType.objects.create(code="FT", name="Further Tax", kind="further_tax", applies_to="sales", output_account=out2, input_account=in2)
        TaxRate.objects.create(tax_type=ft, name="Unregistered", rate_pct=Decimal("4"), valid_from=today - timedelta(days=365))
        exempt = TaxClass.objects.get(name="Tax exempt")
        exempt.is_default = False
        exempt.save()
        standard = TaxClass.objects.create(name="Standard (ST 18%)", tax1=st, is_default=True)
        unreg = TaxClass.objects.create(name="Unregistered (ST 18% + FT 4%)", tax1=st, tax2=ft)
        # --- warehouses
        main = Warehouse.objects.create(code="MAIN", name="Main Warehouse", type="main", address="Plot 12, Industrial Area, Lahore", manager=owner)
        sub = Warehouse.objects.create(code="SUB-1", name="Johar Town Store", type="sub", address="Johar Town, Lahore")
        Warehouse.objects.create(code="QUAR", name="Quarantine", type="quarantine")
        from apps.inventory.models import Bin

        for code in ("A-01", "A-02", "B-01"):
            Bin.objects.create(warehouse=main, code=code, name=f"Rack {code}")
        # --- vehicles & drivers
        v1 = Vehicle.objects.create(reg_no="LEA-1234", make_model="Suzuki Bolan", type="van", capacity_cartons=120, insurance_expiry=today + timedelta(days=200))
        v2 = Vehicle.objects.create(reg_no="LEB-5678", make_model="Suzuki Ravi", type="pickup", capacity_cartons=200, insurance_expiry=today + timedelta(days=90))
        driver_role = GroupProfile.objects.get(tenant=tenant, name="Driver")
        dm = UserService.create_tenant_user(tenant, "Ali Driver", email="driver@demo.pk", password="driver123", roles=[driver_role])
        dm.user.must_change_password = False
        dm.user.save()
        d1 = Driver.objects.create(user=dm.user, name="Ali Driver", phone="+923001111111", cnic="35202-1234567-1", license_no="LHR-998877", license_expiry=today + timedelta(days=400), default_vehicle=v1)
        d2 = Driver.objects.create(name="Bilal Driver", phone="+923002222222", cnic="35202-7654321-1", license_no="LHR-112233", license_expiry=today + timedelta(days=300), default_vehicle=v2)
        # --- staff users
        for name, email, role in (("Sara Coordinator", "sales@demo.pk", "Sales Coordinator"), ("Kamran Accounts", "accounts@demo.pk", "Accountant"), ("Usman Store", "store@demo.pk", "Warehouse Keeper"), ("Nadia Cashier", "cashier@demo.pk", "Cashier")):
            m = UserService.create_tenant_user(tenant, name, email=email, password="staff123", roles=[GroupProfile.objects.get(tenant=tenant, name=role)])
            m.user.must_change_password = False
            m.user.save()
            if role == "Warehouse Keeper":
                from apps.accounts.models import UserWarehouseAccess

                UserWarehouseAccess.objects.create(membership=m, warehouse=main)
        # --- supplier + reps
        supplier = Supplier.objects.create(code="S-00001", name="ColorMax Cosmetics (Pvt) Ltd", contact_person="Mr. Farhan", phone="+92213456789", email="orders@colormax.pk", address="SITE Area, Karachi", city="Karachi", tax_no="9876543-2", payment_terms_days=30, tax_class_override=standard)
        rep_role = GroupProfile.objects.get(tenant=tenant, name="Manufacturer Rep")
        rep1 = SupplierService.create_rep(supplier, name="Imran Rep", code="REP-01", phone="+923011111111", email="rep1@colormax.pk", territory="Lahore North", role=rep_role, create_login=True, password="rep123", monthly_target=Decimal("500000"))
        rep2 = SupplierService.create_rep(supplier, name="Faisal Rep", code="REP-02", phone="+923012222222", email="rep2@colormax.pk", territory="Lahore South", role=rep_role, create_login=True, password="rep123", monthly_target=Decimal("400000"))
        for r in (rep1, rep2):
            r.user.must_change_password = False
            r.user.save()
        # --- territories / routes
        north = Territory.objects.create(name="Lahore North")
        south = Territory.objects.create(name="Lahore South")
        routes = [Route.objects.create(name="Gulberg Mon", territory=north, day_of_week=0), Route.objects.create(name="Johar Town Tue", territory=south, day_of_week=1), Route.objects.create(name="DHA Wed", territory=south, day_of_week=2), Route.objects.create(name="Model Town Thu", territory=north, day_of_week=3)]
        rep1.territories.add(north)
        rep2.territories.add(south)
        # --- catalogue
        pc, box, ctn = UnitOfMeasure.objects.get(code="PC"), UnitOfMeasure.objects.get(code="BOX"), UnitOfMeasure.objects.get(code="CTN")
        brand = Brand.objects.create(name="ColorMax", supplier=supplier)
        brand2 = Brand.objects.create(name="SilkCare", supplier=supplier)
        cat_hair = Category.objects.create(name="Hair Colour")
        cat_cream = Category.objects.create(name="Hair Colour", parent=cat_hair) if False else Category.objects.create(name="Cream Colour", parent=cat_hair)
        cat_dev = Category.objects.create(name="Developer", parent=cat_hair)
        cat_care = Category.objects.create(name="Hair Care")
        shades = ["1.0 Black", "2.0 Darkest Brown", "3.0 Dark Brown", "4.0 Medium Brown", "4.3 Golden Brown", "5.0 Light Brown", "5.4 Copper Brown", "6.0 Dark Blonde", "6.1 Ash Blonde", "6.6 Red", "7.0 Blonde", "7.1 Ash Blonde", "7.3 Golden Blonde", "8.0 Light Blonde", "9.0 Very Light Blonde", "10.0 Lightest Blonde", "0.11 Ash Mixtone", "0.66 Red Mixtone", "Burgundy", "Mahogany"]
        variants = []
        retail = PriceList.objects.get(name="Retail")
        wholesale = PriceList.objects.get(name="Wholesale")
        ean = 6291041500000
        for i, shade in enumerate(shades, start=1):
            code = shade.split(" ")[0]
            p = ProductService.create_product(tenant, {"sku": f"CM-{code}", "name": f"ColorMax Cream Colour {shade} 60ml", "brand": brand, "category": cat_cream, "supplier": supplier, "base_uom": pc, "is_batch_tracked": True, "is_expiry_tracked": True, "shelf_life_days": 730, "tax_class": standard, "cost_price": Decimal("210"), "sale_price": Decimal("310"), "min_stock": Decimal("48"), "max_stock": Decimal("600"), "reorder_qty": Decimal("288"), "attributes": {"shade_code": code, "size_ml": 60}}, variants=[{"sku": f"CM-{code}-60", "name": f"ColorMax {shade} 60ml", "is_default": True, "attributes": {"shade_code": code, "size_ml": 60}}])
            v = p.variants.first()
            Packaging.objects.create(variant=v, uom=box, name="box", qty_in_base=Decimal("12"), barcode=f"BOX{ean + i:013d}"[:20])
            Packaging.objects.create(variant=v, uom=ctn, name="carton", qty_in_base=Decimal("144"), barcode=f"CTN{ean + i:013d}"[:20])
            Barcode.objects.create(variant=v, code=str(ean + i), type="EAN13", is_primary=True)
            PriceListItem.objects.create(price_list=retail, variant=v, price=Decimal("310"))
            PriceListItem.objects.create(price_list=wholesale, variant=v, price=Decimal("290"))
            PriceListItem.objects.create(price_list=wholesale, variant=v, min_qty=Decimal("144"), price=Decimal("280"))
            variants.append(v)
        for j, (name, cost, price) in enumerate([("Developer 20 Vol 1000ml", "350", "520"), ("Developer 30 Vol 1000ml", "350", "520"), ("Developer 40 Vol 1000ml", "360", "540"), ("Developer 20 Vol 120ml", "60", "95"), ("Developer 30 Vol 120ml", "60", "95")], start=1):
            p = ProductService.create_product(tenant, {"sku": f"CM-DEV-{j}", "name": f"ColorMax {name}", "brand": brand, "category": cat_dev, "supplier": supplier, "base_uom": pc, "is_batch_tracked": True, "is_expiry_tracked": True, "shelf_life_days": 1095, "tax_class": standard, "cost_price": Decimal(cost), "sale_price": Decimal(price), "min_stock": Decimal("24"), "max_stock": Decimal("300"), "reorder_qty": Decimal("120")})
            v = p.variants.first()
            Packaging.objects.create(variant=v, uom=box, name="box", qty_in_base=Decimal("12"))
            Barcode.objects.create(variant=v, code=str(ean + 100 + j), type="EAN13", is_primary=True)
            PriceListItem.objects.create(price_list=retail, variant=v, price=Decimal(price))
            PriceListItem.objects.create(price_list=wholesale, variant=v, price=(Decimal(price) * Decimal("0.93")).quantize(Decimal("0.01")))
            variants.append(v)
        for j, (name, cost, price) in enumerate([("Keratin Shampoo 400ml", "420", "650"), ("Keratin Conditioner 400ml", "430", "670"), ("Argan Hair Serum 100ml", "380", "590"), ("Colour Protect Mask 250ml", "500", "780"), ("Heat Protect Spray 200ml", "300", "480")], start=1):
            p = ProductService.create_product(tenant, {"sku": f"SC-{j}", "name": f"SilkCare {name}", "brand": brand2, "category": cat_care, "supplier": supplier, "base_uom": pc, "is_batch_tracked": False, "tax_class": standard, "cost_price": Decimal(cost), "sale_price": Decimal(price), "min_stock": Decimal("12"), "max_stock": Decimal("200"), "reorder_qty": Decimal("60")})
            v = p.variants.first()
            Packaging.objects.create(variant=v, uom=box, name="box", qty_in_base=Decimal("6"))
            Barcode.objects.create(variant=v, code=str(ean + 200 + j), type="EAN13", is_primary=True)
            PriceListItem.objects.create(price_list=retail, variant=v, price=Decimal(price))
            variants.append(v)
        Scheme.objects.create(name="Buy 12 get 1 free (ColorMax cream)", type="free_qty", rules={"buy_qty": 12, "free_qty": 1, "min_qty": 12}, valid_from=today - timedelta(days=30), valid_to=today + timedelta(days=60)).brands.add(brand)
        Scheme.objects.create(name="5% off SilkCare (wholesalers)", type="percent", rules={"value": "5", "min_qty": 6}, customer_types=["wholesaler"], valid_from=today - timedelta(days=30), valid_to=today + timedelta(days=60)).brands.add(brand2)
        # --- customers
        names = ["Glamour Salon", "Beauty Point", "Style Studio", "Hair Affair", "Elegance Parlour", "Mirror Mirror", "The Cut Lounge", "Bliss Beauty", "Royal Salon", "Urban Chic", "Sadia Beauty Parlour", "Al-Noor Cosmetics", "City Cosmetics Store", "Fashion Hub", "Zara Beauty", "Sheen Salon", "Pretty Please", "Crown Cosmetics (Wholesale)", "Shah Traders (Wholesale)", "Look Good Salon"]
        customers = []
        for i, n in enumerate(names, start=1):
            is_wh = "Wholesale" in n
            c = Customer.objects.create(code=f"C-{i:05d}", name=n, type="wholesaler" if is_wh else ("salon" if "Salon" in n or "Parlour" in n or "Studio" in n or "Lounge" in n else "retailer"), contact_person=f"Contact {i}", phone=f"+9230{random.randint(10000000, 99999999)}", address=f"Shop {i}, {random.choice(['Gulberg', 'Johar Town', 'DHA', 'Model Town'])}, Lahore", city="Lahore", gps_lat=Decimal("31.5") + Decimal(random.randint(0, 999)) / 10000, gps_lng=Decimal("74.3") + Decimal(random.randint(0, 999)) / 10000, territory=north if i % 2 else south, route=routes[i % 4], price_list=wholesale if is_wh else retail, credit_limit=Decimal("300000") if is_wh else Decimal("60000"), credit_days=30 if is_wh else 15, tax_class_override=None if is_wh else unreg)
            c.assigned_reps.add(rep1 if i % 2 else rep2)
            customers.append(c)
        if skip_tx:
            return
        self.seed_transactions(tenant, owner, supplier, main, sub, variants, customers, rep1, rep2, d1, d2, v1, v2, standard)

    # ------------------------------------------------------------- transactions
    def seed_transactions(self, tenant, owner, supplier, main, sub, variants, customers, rep1, rep2, d1, d2, v1, v2, standard):
        from apps.accounting.models import BankAccount, CashAccount
        from apps.accounting.services import ExpenseService, PaymentService
        from apps.delivery.services import TripService
        from apps.inventory.models import ReasonCode
        from apps.inventory.services import AdjustmentService, AlertService, TransferService
        from apps.purchasing.services import GoodsReceiptService, PurchaseBillService, PurchaseOrderService
        from apps.sales.services import OrderService, RepCollectionService

        today = timezone.localdate()
        day = lambda n: today - timedelta(days=n)  # noqa: E731
        # --- opening balances: capital into bank
        from apps.accounting.models import Account, OpeningBalanceBatch
        from apps.accounting.services import PostingService
        from apps.core.numbering import next_number

        bank = BankAccount.objects.first()
        cash = CashAccount.objects.filter(is_petty=False).first()
        capital = Account.objects.get(code="3-01-001-0001")
        ob = OpeningBalanceBatch.objects.create(number=next_number("opening_balance"), date=day(10), lines=[{"account": str(bank.account_id), "debit": "2000000", "credit": "0"}, {"account": str(cash.account_id), "debit": "150000", "credit": "0"}, {"account": str(capital.id), "debit": "0", "credit": "2150000"}], notes="Opening capital")
        PostingService.post_opening_balances(ob)
        # --- PO → GRN → bill (stock in)
        lines = [{"variant": v, "qty": Decimal("288") if i < 20 else Decimal("60"), "unit_price": v.cost_price} for i, v in enumerate(variants)]
        po = PurchaseOrderService.create(supplier=supplier, warehouse=main, lines=lines, date=day(9), expected_date=day(7), notes="Opening stock order")
        PurchaseOrderService.send(po)
        grn_lines = []
        for pol in po.lines.select_related("variant", "variant__product"):
            batch_no = f"B{day(30).strftime('%y%m')}-{pol.variant.sku[-4:].replace('-', '')}" if pol.variant.product.is_batch_tracked else ""
            grn_lines.append({"po_line": pol, "variant": pol.variant, "qty_accepted": pol.qty_ordered, "qty_damaged": Decimal("0"), "batch_no": batch_no, "mfg_date": day(30) if batch_no else None, "expiry_date": day(30) + timedelta(days=pol.variant.product.shelf_life_days or 730) if batch_no else None, "unit_cost": pol.unit_price})
        grn = GoodsReceiptService.create(supplier=supplier, warehouse=main, lines=grn_lines, po=po, date=day(7), supplier_challan_no="CM-CH-4471", landed_costs=[{"type": "freight", "description": "Karachi → Lahore", "amount": Decimal("18000"), "allocation": "by_qty"}])
        GoodsReceiptService.post(grn)
        bill = PurchaseBillService.create(supplier=supplier, grns=[grn], bill_date=day(7), supplier_invoice_no="CM-INV-20931", freight=Decimal("18000"))
        PurchaseBillService.post(bill)
        PaymentService.record(direction="out", partner_type="supplier", partner_id=str(supplier.id), amount=Decimal("800000"), method="bank_transfer", date_=day(5), bank_account=bank, reference="TT-77812", allocations=[{"document_type": "purchase_bill", "document_id": str(bill.id), "amount": Decimal("800000")}])
        # --- transfer some stock to the sub store
        tr = TransferService.create(from_warehouse=main, to_warehouse=sub, lines=[{"variant": variants[i], "qty": Decimal("48"), "batch": variants[i].batches.first()} for i in range(0, 8)], date=day(6))
        if tr.status == "requested":
            TransferService.approve(tr)
        TransferService.dispatch(tr)
        TransferService.receive(tr)
        # --- rep orders over the week
        from apps.catalog.models import Packaging

        orders = []
        for n in range(6, 0, -1):
            for k in range(2):
                c = customers[(n * 3 + k * 7) % len(customers)]
                rep = c.assigned_reps.first()
                picks = random.sample(variants[:25], 4)
                olines = []
                for v in picks:
                    box = Packaging.objects.filter(variant=v, name="box").first()
                    olines.append({"variant": v, "packaging": box, "qty": Decimal(random.choice([1, 2, 3])), "unit_price": None, "discount_pct": Decimal("0")})
                o = OrderService.create(customer=c, lines=olines, source="rep_app", rep=rep, supplier=supplier, warehouse=main, order_date=day(n), requested_delivery_date=day(n - 1), notes="Deliver before 2pm" if k else "", external_ref=f"SFA-{day(n):%Y%m%d}-{k + 1:03d}")
                orders.append(o)
        # confirm all but the last two (keep some in the inbox)
        for o in orders[:-2]:
            OrderService.review(o)
            OrderService.confirm(o)
        # --- trip 1 (closed): first 4 confirmed orders
        trip = TripService.create(warehouse=main, date=day(4), driver=d1, vehicle=v1, rep=rep1, orders=orders[:4], notes="Morning run")
        TripService.start(trip, odometer_start=45210)
        from apps.delivery.services import DeliveryService

        notes = list(trip.delivery_notes.order_by("sequence"))
        DeliveryService.deliver(notes[0], receiver_name="Salon manager", cash_collected=notes[0].order.total.quantize(Decimal("1")), gps={"lat": "31.5204", "lng": "74.3587"})
        DeliveryService.deliver(notes[1], receiver_name="Owner")
        l2 = list(notes[2].lines.all())
        DeliveryService.deliver(notes[2], delivered=[{"line_id": str(l2[0].id), "qty_delivered": str(l2[0].qty_loaded - 2), "qty_returned": "2", "return_reason": "rejected", "return_condition": "good"}], receiver_name="Staff")
        DeliveryService.deliver(notes[3], failed=True, failure_reason="Shop closed")
        TripService.close(trip, odometer_end=45262, fuel_amount=Decimal("2500"), expense_amount=Decimal("300"), override=True)
        # --- trip 2 (out for delivery): next 3 orders
        trip2 = TripService.create(warehouse=main, date=today, driver=d2, vehicle=v2, rep=rep2, orders=orders[4:7], notes="Afternoon run")
        TripService.start(trip2, odometer_start=12800)
        DeliveryService.deliver(trip2.delivery_notes.order_by("sequence").first(), receiver_name="Counter staff")
        # --- mark delivered without a trip (counter sale)
        counter = next((o for o in orders[7:] if o.status == "confirmed"), None)
        if counter is not None:
            OrderService.mark_delivered(counter, receiver_name="Walk-in", cash_collected=Decimal("0"))
        # --- receipts by cashier, a rep collection and a settlement
        from apps.sales.models import SalesInvoice

        inv_open = list(SalesInvoice.objects.filter(status__in=["posted", "partially_paid"]).order_by("date"))
        if inv_open:
            PaymentService.record(direction="in", partner_type="customer", partner_id=str(inv_open[0].customer_id), amount=inv_open[0].balance, method="cash", date_=day(2), cash_account=cash, reference="Counter")
        if len(inv_open) > 1:
            PaymentService.record(direction="in", partner_type="customer", partner_id=str(inv_open[1].customer_id), amount=(inv_open[1].balance / 2).quantize(Decimal("1")), method="cheque", date_=day(1), cheque_no="CHQ-100221", cheque_date=today + timedelta(days=5), cheque_bank="HBL")
        col = None
        for inv in inv_open[2:]:
            rep = inv.customer.assigned_reps.first()
            if rep and inv.balance > 0:
                col = RepCollectionService.record(rep=rep, customer=inv.customer, amount=inv.balance, date=day(1), method="cash", reference="Collected at shop")
                col2 = None
                break
        if col is not None:
            RepCollectionService.settle(rep=col.rep, collections=[col], cash_account=cash, date=today, notes="Handed over at office")
        # one more collection left unsettled (cash with reps report)
        for inv in SalesInvoice.objects.filter(status__in=["posted", "partially_paid"]).order_by("-date"):
            rep = inv.customer.assigned_reps.first()
            if rep and inv.balance > 0:
                RepCollectionService.record(rep=rep, customer=inv.customer, amount=(inv.balance / 2).quantize(Decimal("1")), date=today, method="cash")
                break
        # --- an expense and a wastage adjustment
        fuel = Account.objects.get(code="5-02-001-0001")
        from apps.accounting.models import Expense

        exp = Expense.objects.create(number=next_number("expense"), date=day(3), category=fuel, amount=Decimal("2500"), paid_from_cash=cash, vendor="PSO Pump", notes="Trip fuel")
        ExpenseService.approve(exp, force=True)
        expiry = ReasonCode.objects.get(code="EXP")
        v0 = variants[0]
        AdjustmentService.create(warehouse=main, type="decrease", reason=expiry, lines=[{"variant": v0, "batch": v0.batches.first(), "qty": Decimal("3"), "note": "Leaking tubes"}], date=day(2), notes="Found during shelf check")
        AlertService.compute(tenant)
        self.stdout.write(f"  orders={len(orders)} trips=2 invoices={SalesInvoice.objects.count()} grn={grn.number} bill={bill.number}")
