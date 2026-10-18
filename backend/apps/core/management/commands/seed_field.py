"""
Demo data for the field: working areas over real Lahore neighbourhoods, shops inside them, and each rep given
the area they cover plus the shops in it.

    manage.py seed_field --tenant demo          # add or refresh
    manage.py seed_field --tenant demo --clear  # take it back out

Idempotent: run it as often as you like. Areas are matched by name, shops by code.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.core.tenancy import tenant_context
from apps.platform.models import Tenant

# Reps who need to exist before an area can be handed to them: (code, name, email, supplier name).
# Password is rep123 like the other demo rep logins.
REPS = [
    ("REP-03", "Bilal Rep", "rep3@colormax.pk", "ColorMax Cosmetics (Pvt) Ltd"),
]

# Real neighbourhoods, drawn as the boxes OpenStreetMap reports for them — or, when OSM publishes a real
# outline (Shah Jamal does, way/612407784), that outline itself as "points".
AREAS = [
    {"name": "Lahore — Gulberg", "rep": "REP-01", "colour": "#F26522",
     "box": {"south": 31.5000, "north": 31.5400, "west": 74.3300, "east": 74.3750}},
    {"name": "Lahore — Johar Town", "rep": "REP-02", "colour": "#2563EB",
     "box": {"south": 31.4550, "north": 31.4900, "west": 74.2600, "east": 74.3050}},
    {"name": "Lahore — DHA Phase 3-5", "rep": "REP-01", "colour": "#16A34A",
     "box": {"south": 31.4600, "north": 31.5000, "west": 74.3700, "east": 74.4300}},
    {"name": "Lahore — Shah Jamal", "rep": "REP-03", "colour": "#9333EA",
     "points": [
         {"lat": 31.5301106, "lng": 74.3217893}, {"lat": 31.5240995, "lng": 74.3239119},
         {"lat": 31.5210388, "lng": 74.3253478}, {"lat": 31.5199669, "lng": 74.3264275},
         {"lat": 31.5276192, "lng": 74.3333494}, {"lat": 31.5292761, "lng": 74.3297788},
         {"lat": 31.5298727, "lng": 74.3270177}, {"lat": 31.5302209, "lng": 74.323039},
     ]},
]

# Shops that sit inside those areas, so a rep's day has somewhere to go.
SHOPS = [
    ("FS-001", "Gulberg Beauty Lounge", "salon", 31.5180, 74.3480, "12-A Main Boulevard, Gulberg III", "+923004451201"),
    ("FS-002", "Liberty Cosmetics", "retailer", 31.5245, 74.3466, "Liberty Market, Gulberg", "+923004451202"),
    ("FS-003", "MM Alam Hair Studio", "salon", 31.5162, 74.3545, "MM Alam Road, Gulberg II", "+923004451203"),
    ("FS-004", "Johar Town Mega Mart", "wholesaler", 31.4712, 74.2795, "Block G1, Johar Town", "+923004451204"),
    ("FS-005", "Expo Beauty Store", "retailer", 31.4680, 74.2712, "Near Expo Centre, Johar Town", "+923004451205"),
    ("FS-006", "Emporium Salon Supplies", "retailer", 31.4795, 74.2640, "Abdul Haque Road, Johar Town", "+923004451206"),
    ("FS-007", "DHA Y-Block Cosmetics", "retailer", 31.4745, 74.4005, "Y Block Commercial, DHA Phase 3", "+923004451207"),
    ("FS-008", "Phase 5 Glamour Salon", "salon", 31.4690, 74.4120, "CCA Phase 5, DHA", "+923004451208"),
    ("FS-009", "Sui Gas Road Traders", "wholesaler", 31.4835, 74.3890, "Sui Gas Road, DHA Phase 3", "+923004451209"),
    # deliberately outside every area — the one a rep must not be able to order from on site
    ("FS-010", "Shahdara Corner Shop", "retailer", 31.6050, 74.2790, "GT Road, Shahdara", "+923004451210"),
    # Shah Jamal — Bilal Rep's patch
    ("FS-011", "Shah Jamal Beauty Palace", "salon", 31.5262, 74.3270, "Shah Jamal Road, near the darbar", "+923004451211"),
    ("FS-012", "Jamal General Store", "retailer", 31.5284, 74.3258, "Main Bazar, Shah Jamal", "+923004451212"),
    ("FS-013", "Noor Cosmetics Corner", "retailer", 31.5238, 74.3268, "Street 9, Shah Jamal", "+923004451213"),
]


class Command(BaseCommand):
    help = "Seed working areas, shops inside them and the reps who cover them."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", default="demo")
        parser.add_argument("--clear", action="store_true", help="remove what this command created")

    def handle(self, *args, **opts):
        tenant = Tenant.objects.get(slug=opts["tenant"])
        with tenant_context(tenant, tenant.owner):
            from apps.partners.geofence import GeoFenceService
            from apps.partners.models import Customer, GeoFence, GeoFenceEvent, GeoFenceRep, SupplierRep

            if opts["clear"]:
                for name in [a["name"] for a in AREAS]:
                    for fence in GeoFence.objects.filter(name=name):
                        GeoFenceRep.objects.filter(fence=fence).delete()
                        GeoFenceEvent.objects.filter(fence=fence).update(fence=None)
                        fence.delete()
                        self.stdout.write(f"removed area {name}")
                gone = Customer.objects.filter(code__startswith="FS-").delete()
                self.stdout.write(f"removed {gone[0]} shop rows")
                return

            # ── the reps and their logins ──────────────────────────────────────────────────────────
            from apps.accounts.models import GroupProfile, TenantMembership, User
            from apps.accounts.services import UserService
            from apps.partners.models import Supplier

            role = GroupProfile.objects.filter(tenant=tenant, name="Manufacturer Rep").first()
            for code, name, email, supplier_name in REPS:
                supplier = Supplier.objects.filter(name=supplier_name).first()
                if supplier is None or role is None:
                    self.stdout.write(f"skipped rep {code}: missing supplier or Manufacturer Rep role")
                    continue
                rep, created = SupplierRep.objects.get_or_create(code=code, defaults={"supplier": supplier, "name": name})
                user = User.objects.filter(email=email).first()
                if user is None:
                    UserService.create_tenant_user(tenant, name, email=email, password="rep123",
                                                   roles=[role], warehouses=[], supplier_rep=rep)
                    user = User.objects.get(email=email)
                    User.objects.filter(pk=user.pk).update(must_change_password=False)
                if rep.user_id != user.pk or rep.role_id != role.pk:
                    rep.user, rep.role = user, role
                    rep.save(update_fields=["user", "role"])
                TenantMembership.objects.filter(user=user, tenant=tenant).update(supplier_rep=rep)
                self.stdout.write(f"{'added' if created else 'kept'} rep {code} {name} · login {email} / rep123")

            # ── the shops ──────────────────────────────────────────────────────────────────────────
            shops = {}
            for code, name, kind, lat, lng, address, phone in SHOPS:
                shop, created = Customer.objects.update_or_create(
                    code=code,
                    defaults={"name": name, "type": kind, "address": address, "city": "Lahore", "phone": phone,
                              "gps_lat": Decimal(str(lat)), "gps_lng": Decimal(str(lng)), "status": "active",
                              "credit_limit": Decimal("150000"), "credit_days": 15},
                )
                shops[code] = shop
                self.stdout.write(f"{'added' if created else 'updated'} shop {code} {name}")

            # ── the areas, and who covers them ─────────────────────────────────────────────────────
            for spec in AREAS:
                rep = SupplierRep.objects.filter(code=spec["rep"]).first()
                if "points" in spec:
                    points = spec["points"]
                else:
                    box = spec["box"]
                    points = [
                        {"lat": box["north"], "lng": box["west"]}, {"lat": box["north"], "lng": box["east"]},
                        {"lat": box["south"], "lng": box["east"]}, {"lat": box["south"], "lng": box["west"]},
                    ]
                fence, created = GeoFence.objects.update_or_create(
                    name=spec["name"],
                    defaults={"type": "polygon", "points": points, "colour": spec["colour"], "is_active": True,
                              "source": "manual", "notes": "Demo working area"},
                )
                GeoFenceService.normalise(fence)
                fence.save()
                inside = GeoFenceService.customers_in(fence)
                if rep is not None:
                    GeoFenceRep.objects.get_or_create(fence=fence, rep=rep)
                    for shop in inside:
                        shop.assigned_reps.add(rep)
                self.stdout.write(
                    f"{'added' if created else 'updated'} area {spec['name']}: {len(inside)} shop(s) inside"
                    f"{f' → {rep.name}' if rep else ' (no rep)'}"
                )

            loose = [c.code for c in Customer.objects.filter(code__startswith="FS-")
                     if not any(GeoFenceService.customers_in(f) and c in GeoFenceService.customers_in(f) for f in GeoFence.objects.all())]
            if loose:
                self.stdout.write(f"outside every area (on purpose): {', '.join(loose)}")
