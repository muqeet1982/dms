from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import MONEY, TenantModel


class Territory(TenantModel):
    name = models.CharField(max_length=80)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")

    class Meta:
        unique_together = [("tenant", "name")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class Route(TenantModel):
    DAYS = [(0, "Monday"), (1, "Tuesday"), (2, "Wednesday"), (3, "Thursday"), (4, "Friday"), (5, "Saturday"), (6, "Sunday")]
    name = models.CharField(max_length=80)
    territory = models.ForeignKey(Territory, null=True, blank=True, on_delete=models.SET_NULL, related_name="routes")
    day_of_week = models.PositiveSmallIntegerField(choices=DAYS, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("tenant", "name")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class Supplier(TenantModel):
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=200)
    business_name = models.CharField(max_length=200, blank=True, help_text="Registered business name when it differs from the trading name")
    ntn = models.CharField(max_length=20, blank=True, verbose_name="NTN")
    strn = models.CharField(max_length=20, blank=True, verbose_name="STRN")
    cnic = models.CharField(max_length=20, blank=True, verbose_name="CNIC")
    filer_status = models.CharField(max_length=12, choices=[("unknown", "Not known"), ("filer", "Filer"), ("non_filer", "Non-filer")], default="unknown", help_text="FBR active taxpayer status — drives withholding")
    contact_person = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=30, blank=True, help_text="Mobile")
    alt_mobile = models.CharField(max_length=30, blank=True)
    landline = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=80, blank=True)
    tax_no = models.CharField(max_length=60, blank=True, help_text="Legacy single tax number — NTN/STRN above are preferred")
    tax_numbers = models.JSONField(default=dict, blank=True)
    payment_terms_days = models.PositiveIntegerField(default=30)
    tax_class_override = models.ForeignKey("catalog.TaxClass", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    withholding_tax = models.ForeignKey("catalog.TaxType", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", help_text="Withholding tax applied on payments to this supplier")
    account = models.ForeignKey("accounting.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", help_text="AP control account (defaults to the posting rule)")
    opening_balance = models.DecimalField(**MONEY, help_text="What we owed them when the company started using DMS — positive = we owe them")
    opening_balance_date = models.DateField(null=True, blank=True)
    opening_journal = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    contacts = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [("tenant", "code")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class SupplierRep(TenantModel):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="reps")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="rep_profiles")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    territory = models.CharField(max_length=120, blank=True)
    territories = models.ManyToManyField(Territory, blank=True, related_name="reps")
    role = models.ForeignKey("accounts.GroupProfile", null=True, blank=True, on_delete=models.SET_NULL, related_name="reps", help_text="External role controlling what the rep sees")
    monthly_target = models.DecimalField(**MONEY)
    is_active = models.BooleanField(default=True)
    # where this rep may take an order: blank = the company's geofence_policy; a value overrides it for them
    GEOFENCE_CHOICES = [("off", "Anywhere"), ("warn", "Anywhere, flag outside"), ("block", "Only inside their areas")]
    geofence_policy = models.CharField(max_length=5, blank=True, choices=GEOFENCE_CHOICES)

    class Meta:
        unique_together = [("tenant", "code")]
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.supplier.name})"


class Customer(TenantModel):
    TYPES = [("retailer", "Retailer"), ("salon", "Salon"), ("wholesaler", "Wholesaler"), ("lmt", "LMT"), ("other", "Other")]
    STATUS = [("active", "Active"), ("pending", "Pending approval"), ("blocked", "Blocked"), ("inactive", "Inactive")]
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=200)
    type = models.CharField(max_length=12, choices=TYPES, default="retailer")
    business_name = models.CharField(max_length=200, blank=True, help_text="Registered business name when it differs from the trading name")
    ntn = models.CharField(max_length=20, blank=True, verbose_name="NTN", help_text="National Tax Number, e.g. 1234567-8")
    strn = models.CharField(max_length=20, blank=True, verbose_name="STRN", help_text="Sales tax registration number")
    cnic = models.CharField(max_length=20, blank=True, verbose_name="CNIC", help_text="For sole proprietors / unregistered buyers")
    filer_status = models.CharField(max_length=12, choices=[("unknown", "Not known"), ("filer", "Filer"), ("non_filer", "Non-filer")], default="unknown", help_text="FBR active taxpayer status — drives withholding")
    contact_person = models.CharField(max_length=120, blank=True)
    # Where the rep stood when they added this shop, and whether that was inside the area they cover. A shop
    # added from outside is kept rather than refused — refusing destroys the record, and a new shop arrives
    # `pending` anyway — so the office approving it can see the fact instead of being told nothing. Null on a
    # shop the office typed in itself.
    added_inside_area = models.BooleanField(null=True, blank=True)
    added_distance_m = models.FloatField(null=True, blank=True)
    # how the shop's PIN was set (5.1 app: gps = standing there, search/map = placed on the map,
    # coordinates = typed, link = a shared maps/WhatsApp link; blank = the office, or an old build) and how
    # far the rep stood from the pin they placed — a pin set from 3 km away is legitimate but worth seeing
    # before approval, because every later order is measured against it
    PIN_SOURCES = [("gps", "At the shop"), ("search", "Searched on the map"), ("map", "Placed on the map"),
                   ("coordinates", "Typed coordinates"), ("link", "From a shared link")]
    pin_source = models.CharField(max_length=12, blank=True, default="")
    pin_rep_distance_m = models.FloatField(null=True, blank=True)
    phone = models.CharField(max_length=30, blank=True, help_text="Mobile — the number reps call and SMS")
    alt_mobile = models.CharField(max_length=30, blank=True)
    landline = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=80, blank=True)
    PROVINCES = [("Punjab", "Punjab"), ("Sindh", "Sindh"), ("Khyber Pakhtunkhwa", "Khyber Pakhtunkhwa"),
                 ("Balochistan", "Balochistan"), ("Gilgit-Baltistan", "Gilgit-Baltistan"),
                 ("Azad Jammu & Kashmir", "Azad Jammu & Kashmir"), ("Islamabad Capital Territory", "Islamabad Capital Territory")]
    province = models.CharField(max_length=40, blank=True, default="Punjab")
    # FBR: "Registered"/"Unregistered", set from Get_Reg_Type when checked — drives the DI payload's
    # buyerRegistrationType and the sandbox scenario; guessing it wrong is a rejection
    fbr_registration_type = models.CharField(max_length=30, blank=True, default="")
    gps_lat = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    gps_lng = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    # how close a rep must be to take an order here; blank = the company's order_proximity_m (a kiosk and a
    # cash-and-carry are not the same size)
    order_radius_m = models.PositiveIntegerField(null=True, blank=True)
    territory = models.ForeignKey(Territory, null=True, blank=True, on_delete=models.SET_NULL, related_name="customers")
    route = models.ForeignKey(Route, null=True, blank=True, on_delete=models.SET_NULL, related_name="customers")
    price_list = models.ForeignKey("catalog.PriceList", null=True, blank=True, on_delete=models.SET_NULL, related_name="customers")
    credit_limit = models.DecimalField(**MONEY)
    credit_days = models.PositiveIntegerField(default=0)
    tax_class_override = models.ForeignKey("catalog.TaxClass", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    tax_numbers = models.JSONField(default=dict, blank=True)
    account = models.ForeignKey("accounting.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    assigned_reps = models.ManyToManyField(SupplierRep, blank=True, related_name="customers")
    status = models.CharField(max_length=10, choices=STATUS, default="active")
    is_blocked = models.BooleanField(default=False)
    blocked_reason = models.CharField(max_length=200, blank=True)
    default_discount_pct = models.DecimalField(max_digits=7, decimal_places=3, default=Decimal("0"), help_text="Standing discount on every line of this customer's orders and invoices (can be dropped per document)")
    opening_balance = models.DecimalField(**MONEY, help_text="What they owed when the company started using DMS — positive = they owe us")
    opening_balance_date = models.DateField(null=True, blank=True)
    opening_journal = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    contacts = models.JSONField(default=list, blank=True)
    requested_by_rep = models.ForeignKey(SupplierRep, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    min_shelf_life_days = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [("tenant", "code")]
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def gps(self):
        return {"lat": self.gps_lat, "lng": self.gps_lng} if self.gps_lat is not None else None


class CustomerLocation(TenantModel):
    """
    One shop of a multi-shop customer (21 Oct 2026). The Customer stays the ACCOUNT — who owes, credit,
    NTN/CNIC, discount, sale persons — and locations are its places: each with a name, its own pin and its
    own order radius. A customer with no rows here behaves exactly as before (its own pin is the shop).
    """

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="locations")
    name = models.CharField(max_length=120)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=80, blank=True)
    gps_lat = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    gps_lng = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    order_radius_m = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.customer_id} · {self.name}"

    @property
    def gps(self):
        return {"lat": float(self.gps_lat), "lng": float(self.gps_lng)} if self.gps_lat is not None else None


class GeoFence(TenantModel):
    """
    A working area on the map, drawn once and handed to the reps who cover it.

    Two shapes cover every way of describing an area: a **circle** (a pin and a radius — "15 km around the depot")
    and a **polygon** (a drawn outline, or one borrowed from OpenStreetMap when the place has a published boundary).
    Typing an area name is an entry point, not a third shape: it resolves to one of these two and can be nudged
    afterwards. `min_lat`/`max_lat`/`min_lng`/`max_lng` are the shape's bounding box, kept so the database can throw
    away the fences that cannot possibly contain a point before the exact test runs in Python.
    """

    TYPES = [("circle", "Radius around a point"), ("polygon", "Drawn area")]
    SOURCES = [("manual", "Drawn by hand"), ("osm", "From a place name"), ("import", "Imported")]

    name = models.CharField(max_length=120)
    code = models.CharField(max_length=30, blank=True)
    type = models.CharField(max_length=10, choices=TYPES, default="circle")
    centre_lat = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    centre_lng = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    radius_m = models.PositiveIntegerField(default=0, help_text="Circle only: metres from the centre")
    points = models.JSONField(default=list, blank=True, help_text='Polygon only: [{"lat":…,"lng":…}, …] in order')
    source = models.CharField(max_length=10, choices=SOURCES, default="manual")
    place_name = models.CharField(max_length=300, blank=True, help_text="What the place was called where the shape came from")
    osm_ref = models.CharField(max_length=60, blank=True)
    colour = models.CharField(max_length=9, default="#F26522")
    min_lat = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    max_lat = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    min_lng = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    max_lng = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    reps = models.ManyToManyField(SupplierRep, through="GeoFenceRep", blank=True, related_name="geofences")
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        unique_together = [("tenant", "name")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class GeoFenceRep(TenantModel):
    """Who covers this area. A rep with no area is never restricted — assigning one is what switches checks on."""

    fence = models.ForeignKey(GeoFence, on_delete=models.CASCADE, related_name="assignments")
    rep = models.ForeignKey(SupplierRep, on_delete=models.CASCADE, related_name="fence_assignments")
    paused_until = models.DateField(null=True, blank=True, help_text="Checks are off for this rep until this date — for a planned trip outside the area")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("fence", "rep")]
        ordering = ["-assigned_at"]


class GeoFenceEvent(TenantModel):
    """
    Every location that was checked, inside or out: what the rep was doing, where they were, which area was nearest
    and how far outside it they stood. This is the evidence behind the out-of-area report — and behind a refusal,
    so a rep who says "it blocked me and I was standing right there" can be answered with metres.
    """

    VERDICTS = [("inside", "Inside the area"), ("outside", "Outside every area"), ("no_fix", "No location given"), ("skipped", "Not checked")]
    ACTIONS = [("order", "Order"), ("collection", "Cash collection"), ("customer", "New customer"), ("delivery", "Delivery"), ("visit", "Check-in"), ("check", "Location check")]

    rep = models.ForeignKey(SupplierRep, null=True, blank=True, on_delete=models.SET_NULL, related_name="fence_events")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=12, choices=ACTIONS)
    verdict = models.CharField(max_length=10, choices=VERDICTS)
    blocked = models.BooleanField(default=False)
    gps_lat = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    gps_lng = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    fence = models.ForeignKey(GeoFence, null=True, blank=True, on_delete=models.SET_NULL, related_name="events", help_text="The area it fell in, or the nearest one when outside")
    distance_m = models.PositiveIntegerField(null=True, blank=True, help_text="How far outside the nearest area")
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    document_type = models.CharField(max_length=40, blank=True)
    document_id = models.CharField(max_length=40, blank=True)
    document_number = models.CharField(max_length=40, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [models.Index(fields=["tenant", "occurred_at"]), models.Index(fields=["tenant", "verdict"])]


class LocationPing(TenantModel):
    """
    One position fix from someone's phone.

    Reps and drivers work out of signal for hours, so `at` is when the device took the fix and `created_at` is
    when it reached us — a batch flushed at six o'clock can carry a point from nine. `client_id` is generated on
    the phone so a retried batch cannot create the same point twice.

    No area check is applied here on purpose: a rep driving home is outside every area, and those points are
    exactly what makes a day's route readable.
    """

    SOURCES = [("background", "Background"), ("foreground", "In the app"), ("manual", "Sent by hand")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="location_pings")
    rep = models.ForeignKey(SupplierRep, null=True, blank=True, on_delete=models.SET_NULL, related_name="location_pings")
    client_id = models.CharField(max_length=64, help_text="The phone's own id for this point — the dedupe key")
    at = models.DateTimeField(help_text="When the fix was taken on the device")
    lat = models.DecimalField(max_digits=10, decimal_places=7)
    lng = models.DecimalField(max_digits=10, decimal_places=7)
    accuracy_m = models.FloatField(null=True, blank=True)
    speed_mps = models.FloatField(null=True, blank=True)
    heading_deg = models.FloatField(null=True, blank=True)
    altitude_m = models.FloatField(null=True, blank=True)
    battery_pct = models.PositiveSmallIntegerField(null=True, blank=True)
    is_charging = models.BooleanField(null=True, blank=True)
    source = models.CharField(max_length=12, choices=SOURCES, default="background")
    app_version = models.CharField(max_length=20, blank=True)
    shift = models.ForeignKey("partners.RepShift", null=True, blank=True, on_delete=models.SET_NULL, related_name="pings")
    mocked = models.BooleanField(default=False, help_text="The device said this fix came from a fake-GPS app")

    class Meta:
        ordering = ["at"]
        unique_together = [("tenant", "user", "client_id")]
        indexes = [models.Index(fields=["tenant", "user", "at"]), models.Index(fields=["tenant", "at"])]

    def __str__(self):
        return f"{self.user_id} @ {self.at:%Y-%m-%d %H:%M}"


class CustomerVisit(TenantModel):
    """
    A rep standing at a shop, recorded whether or not anything was sold.

    Before this, a visit that produced no order left nothing behind: the route showed a stop, but not who was
    visited, nor why there was no order. A rep who drove all day and found four shutters down had nothing to show
    for it, and the office had no way to tell that from a rep who stayed home.

    The photo is the evidence, so it is taken at the shop and not chosen from a gallery. The server cannot see
    which camera a byte came from, so it enforces what it can: the photo must arrive with the check-in, the
    device's own capture time must be recent, and the position is checked against the rep's area the same way an
    order is. `photo_taken_at` is what the device claims; `created_at` is when it reached us.
    """

    OUTCOMES = [("order", "Order taken"), ("no_order", "No order")]
    # Why a visit produced nothing — a short, fixed list, because free text is never reported on. The keys are
    # the mobile app's own, kept as they shipped; the office may relabel them, and the app renders whatever
    # labels `my-areas.visit.reasons` sends.
    REASONS = [
        ("closed", "Shop was closed"),
        ("owner_away", "Owner not there"),
        ("has_stock", "Still has stock"),
        ("payment_due", "Payment pending"),
        ("come_later", "Asked me to come later"),
        ("not_interested", "Not interested today"),
        ("price_issue", "Price or scheme problem"),
        ("quality_issue", "Complaint about the product"),
        ("other", "Something else"),
        # the app closes a call the rep walked away from (next day, or checking in elsewhere) — filterable,
        # so "walked away" is never mistaken for "not interested"
        ("no_outcome", "Walked away without recording"),
    ]

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="visits")
    rep = models.ForeignKey(SupplierRep, null=True, blank=True, on_delete=models.SET_NULL, related_name="visits")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="customer_visits")
    client_id = models.CharField(max_length=64, blank=True, help_text="The phone's own id — so a retry cannot check in twice")
    at = models.DateTimeField(help_text="Checked in: when the rep arrived, by the device's clock")
    closed_at = models.DateTimeField(null=True, blank=True, help_text="When they finished at that shop")
    outcome = models.CharField(max_length=10, choices=OUTCOMES, default="no_order")
    reason = models.CharField(max_length=20, choices=REASONS, blank=True)
    note = models.CharField(max_length=300, blank=True)
    order = models.ForeignKey("sales.SalesOrder", null=True, blank=True, on_delete=models.SET_NULL, related_name="visits")
    gps_lat = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    gps_lng = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    gps_accuracy_m = models.FloatField(null=True, blank=True)
    # how far the rep was from the shop's own pin — the number that answers "were they really there?"
    customer_distance_m = models.FloatField(null=True, blank=True)
    inside_area = models.BooleanField(null=True, blank=True)
    photo = models.FileField(upload_to="visits/%Y/%m/", null=True, blank=True)
    photo_taken_at = models.DateTimeField(null=True, blank=True, help_text="The device's capture time, used to refuse a gallery pick")
    # True only when the device said when the photo was taken and that time fell inside the visit. A build that
    # sends no capture time is still accepted — but the office can see that nothing vouches for the photo, so
    # omitting the field is not a way round the rule. (Raised by the mobile team, who declined to use it.)
    #
    # This is permanent, not scaffolding for old builds. Builds are sideloaded and nobody can say when the last
    # phone updates — and showing the office weak evidence beats making the visit vanish, for the same reason
    # `inside_area` is recorded rather than the write refused.
    photo_verified = models.BooleanField(default=False)
    app_version = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["-at"]
        unique_together = [("tenant", "user", "client_id")]
        indexes = [models.Index(fields=["tenant", "at"]), models.Index(fields=["tenant", "rep", "at"]),
                   models.Index(fields=["tenant", "customer", "at"])]

    def __str__(self):
        return f"{self.customer_id} {self.outcome} @ {self.at:%Y-%m-%d %H:%M}"

    @property
    def gps(self):
        return {"lat": float(self.gps_lat), "lng": float(self.gps_lng)} if self.gps_lat is not None else None


class RepShift(TenantModel):
    """
    A rep's working day as the rep declares it: started on the phone, broken for lunch, ended — and the owner's
    rules hang off it: tracking runs exactly while a shift is on (never during a break), and no shift means no
    order. The `checks` the app ran before allowing the start (permissions, battery, location services) are
    stored as the evidence that tracking *could* run when the shift began.

    Times come in two layers: `recorded_*` is what the phone said and is never touched; the unprefixed fields
    are the effective times, which the office may correct (who, when and why are kept, because corrected
    attendance is the kind of record people dispute later).
    """

    STATUS = [("on", "On shift"), ("break", "On break"), ("ended", "Ended")]
    END_REASONS = [
        ("rep", "Ended by the rep"), ("tracking_off", "Tracking switched off"), ("location_off", "Location switched off"),
        ("permission_revoked", "Location permission revoked"), ("tracking_lost", "Tracking went silent"),
        ("auto_cutoff", "Still open at the daily cutoff"), ("office", "Ended by the office"),
    ]

    rep = models.ForeignKey(SupplierRep, on_delete=models.CASCADE, related_name="shifts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="rep_shifts")
    client_id = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=6, choices=STATUS, default="on", db_index=True)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=20, choices=END_REASONS, blank=True)
    recorded_started_at = models.DateTimeField()
    recorded_ended_at = models.DateTimeField(null=True, blank=True)
    start_lat = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    start_lng = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    end_lat = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    end_lng = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    device = models.JSONField(default=dict, blank=True)
    checks = models.JSONField(default=dict, blank=True, help_text="What the app verified before allowing the start")
    last_ping_at = models.DateTimeField(null=True, blank=True)
    lost_alerted_at = models.DateTimeField(null=True, blank=True)
    corrected_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    corrected_at = models.DateTimeField(null=True, blank=True)
    correction_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-started_at"]
        unique_together = [("tenant", "rep", "client_id")]
        indexes = [models.Index(fields=["tenant", "status"]), models.Index(fields=["tenant", "rep", "started_at"])]

    @property
    def corrected(self):
        if self.started_at != self.recorded_started_at or (self.recorded_ended_at and self.ended_at != self.recorded_ended_at):
            return True
        return any(b.corrected for b in self.breaks.all())


class RepShiftBreak(TenantModel):
    shift = models.ForeignKey(RepShift, on_delete=models.CASCADE, related_name="breaks")
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    recorded_started_at = models.DateTimeField()
    recorded_ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["started_at"]

    @property
    def corrected(self):
        return self.started_at != self.recorded_started_at or (self.recorded_ended_at and self.ended_at != self.recorded_ended_at)
