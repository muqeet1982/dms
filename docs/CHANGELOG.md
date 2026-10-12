# Changelog

## 1.0.0 — 2026-09-22

Phases 0–8 of the specification delivered in one pass on `dms.invoiceflow.pk`.

- **Foundation**: Django 5.2 / DRF / PostgreSQL 16 / Redis / Celery; custom `User`, `TenantModel` with compile-time tenant filtering, permission catalogue, audit log, numbering, attachments, notifications (in-app + FCM/email/SMS abstractions), OpenAPI at `/api/docs/`, pytest suite (isolation, accounting integrity, order-to-invoice, external API).
- **Platform admin**: plans (versioned, grandfathered), add-ons, coupons, tenants + owner creation, suspend/reactivate/cancel/archive, impersonation, usage, subscriptions (renew, upgrade/downgrade with pro-rata + limit check, trial extend/convert), invoices (PDF) & payments & refunds, outstanding/ageing, announcements, message templates, feature flags (incl. branding), platform roles/users, settings, health, support tickets, audit.
- **Tenant setup & masters**: company profile & business rules, branding (flag-gated), users/roles builder (internal + external), warehouses/bins, suppliers + reps with logins, customers/territories/routes, catalogue (variants, packaging, barcodes, internal barcode generation), price lists with slabs, schemes, tax types/rates/classes, vehicles, drivers, CSV imports/exports.
- **Inventory**: append-only stock ledger, cached levels, batches/expiry, opening stock, transfers (request → approve → dispatch → receive), adjustments with reason codes/approval limits, stock counts with variance posting, alerts & reorder suggestions → draft PO, barcode resolve/scan lookup, label PDFs, valuation & GL reconciliation, nightly drift check.
- **Purchasing**: PO → GRN (scan, batch, expiry, damaged/short, landed cost) → bill (two taxes, PPV) → payment; purchase returns and supplier debit notes.
- **Sales & delivery**: orders (staff, rep portal, `/ext/v1`, API keys), inbox with confirm/hold/reject, credit & stock checks, reservations, pick lists, trips (assign, load, start = stock out, close with returns/damage/cash reconciliation), delivery notes with POD, mark-delivered, invoice per delivery, credit notes/returns, receipts, cheques, rep collections & settlements.
- **Accounting**: 4-level COA template, posting rules, auto journals for every document, manual journals, reversals, periods & year-end, cash/bank books, bank reconciliation, expenses with approval, opening balances, financial reports (TB, GL, P&L, BS, cash flow, ageing, tax summary, cash with reps, inventory vs GL).
- **Reports & dashboards**: report registry with JSON/CSV/XLSX/PDF, saved filters, schedules; owner, accounts, rep and platform dashboards.
- **Frontend**: React 18 + TypeScript + Vite on the SmartHR template; tenant app (`/app`), rep portal (`/rep`, mobile-first), platform admin (`/admin`); HID barcode scanning; verified with a headless-browser smoke test over 127 pages and end-to-end write flows.
- **Mobile**: `mobile-app.md` integration guide for the React Native app.

## 2026-10-09

- **Staying signed in.** Refresh tokens now have a 60-second grace window (`DMS["REFRESH_GRACE_SECONDS"]`): a token
  that was just rotated is honoured once more instead of killing the whole session family, so two tabs (or a retried
  request) refreshing at the same moment no longer log the user out. Reuse outside the window still revokes the
  family — and now actually persists, because the 401 is returned rather than raised (`ATOMIC_REQUESTS` was rolling
  the revocation back). The web client refreshes pre-emptively below 45 s of token life, serialises refreshes across
  tabs through `localStorage`, adopts tokens another tab rotated, and signs out only when `/auth/refresh` itself
  rejects the token — a `429`, a `5xx` or a dropped connection keeps the session and reports one failed call.
- **Impersonation no longer drops an admin mid-task.** The window slides forward while the admin is working
  (`impersonation_max_hours`, default 8, caps it), `/me` returns `impersonation_expires_at`, and an expired window
  answers `403 impersonation_expired` (membership gone: `tenant_not_member`) so the app clears the stale company,
  retries and lands back in the admin panel instead of looking logged out. A failed `/me` shows a retry card rather
  than bouncing to `/login`.
- **Order → delivery workflow.** The chain is explicit end to end: sales order → load form → warehouse issue →
  invoice → dispatch (vehicle, driver, helper) → out for delivery → delivery confirmation.
  `GET /api/v1/sales-orders/{id}/workflow` returns the seven steps with state, documents and the next action, and
  the order, load form and delivery note pages render it as a stepper.
  New: `POST /sales-orders/{id}/load-form`, `POST /pick-lists/{id}/send`, `POST /pick-lists/{id}/issue`
  (records issued quantities, **reduces stock**, opens a delivery note per order), `POST /sales-orders/{id}/invoice`
  (invoices what was issued). Load forms gained `sent`/`issued`/`partially_issued` with who and when; sales orders
  gained `issued`; delivery notes gained `dispatched`, `picklist` and `remarks`; dispatches gained `helper`.
  An already-issued delivery note is put on the vehicle instead of being issued twice.
- **Tenant branding.** The company logo (invoices, delivery notes, POs) can be uploaded and cleared from
  Settings → Branding on every plan, and when white-label is off the tenant can ask for it with one button
  (opens a support ticket). White-label itself stays a platform switch per tenant.

## 2026-10-09 (later) — permissions as page switches

- **Per-page permissions.** Roles are now set as four switches per page — View · Add · Edit · Delete — plus the
  extras that only make sense there (confirm an order, issue stock, post a journal, reset a password, set a credit
  limit…). `apps/accounts/page_permissions.py` holds all 60 pages and is the single source of truth: the role editor
  renders it (`GET /api/v1/permission-pages`), the app gates on it (`GET /api/v1/me` → `pages`, `RequirePage`,
  `ListPage page=…`, `CrudPage page=…`), and every viewset takes its `perm_map` from it (`page_perms("<key>")`).
- **View is the gate**: with it off the route itself is refused, and the other switches on that row clear. Add also
  gates the create screen, so a typed URL is not a way around the matrix. Delete does what the document allows —
  users suspend, products archive, orders cancel — and the editor says so per page.
- **No privilege escalation**: handing out roles and warehouse access is its own switch
  (`users.change_user_access`), so a user admin cannot promote themselves.
- **Old roles keep working**: coarse codenames (`users.manage_roles`, `delivery.manage_vehicles`, …) map onto the
  switches, `/api/v1/roles` returns `permissions_effective`, and the first save converts a role to switches without
  changing what it can do. Migration `accounts/0007` tops up the four codenames that used to cover several pages.
- **Leak test**: `tests/test_permission_matrix.py` grants exactly one switch per page and asserts every other action
  answers 403, and that a role with nothing on cannot read the page at all. Verified in the browser too: a role with
  no switches reaches none of the 60 pages, and a view-everything role reaches all 60 with no add/edit/delete
  control anywhere.
- **One email, one person.** A user's email or mobile can only belong to one account (case and spacing ignored), and
  customers and suppliers each allow one record per email inside a company — all three refuse a duplicate by name
  instead of silently reusing or creating a second record.

## 2026-10-09 (audit)

Full re-audit of the three flows end to end on the live site, as the owner, against the API and in the browser:

- **Sales order → delivery (46 checks):** order confirmed → load form created and sent → warehouse issue (stock fell
  by exactly the issued quantity, order → `issued`) → invoice posted with its journal and links back to order and
  note → dispatch with vehicle, driver and helper reusing the issued note → trip started (stop → `dispatched`) →
  delivery confirmed partially with a return reason and remarks → all seven workflow steps `done`.
- **Purchase order → GRN → bill → payment → return (42 checks):** PO totals, approve/send, receipt with batch,
  expiry, damaged and short quantities, stock up by the accepted amount, PO `partially_received` with received
  quantities, bill built from the GRN with its journal, supplier payable up then back to zero after payment, bill
  `paid`, and a batch-tracked return posting stock back out.
- **Barcode scanning (42 checks + 6 new tests):** resolve by barcode and by typed SKU, unknown code refused, scan
  lookup with per-warehouse levels, scanning into a goods receipt (repeat scans add up, posting moves stock),
  scanning into a load form (unknown code reported, not silently counted), internal barcode generation for the
  Keune items that have none, and a real label PDF. In the browser a keyboard-wedge scanner was simulated on the
  scan-lookup page, the sales order form (scan fills a line) and a load form (picked column moved 4 → 5).
- **Fixed:** `@require_perm("delivery.create_trip")` on "create dispatch from load form" referenced a codename that
  no longer exists, which would have refused the action for everyone including the owner. A static audit now checks
  every `perm_map` / `required_perm` / `require_perm` reference against the catalogue.
- `tests/test_barcode_scanning.py` added so the scanning paths stay covered.

Every document the audit created was removed afterwards: the demo tenant's trial balance is back to its exact
pre-audit figure and stock drift is zero.

## 2026-10-09 (buying flow, product codes, boxes)

**Product codes.** A product now carries the three codes a distributor actually uses: **UPC** (the barcode on the
unit — one scan counts one), **Box UPC** (the barcode on the outer box — one scan counts a whole box, i.e. `packing`
units) and **Item code** (the supplier's catalogue code, e.g. Keune's "62001"). The product form asks for the UPC
instead of "SKU / code"; `sku` stays as the internal key and is filled from the UPC (or item code) when not given,
so every existing document, import and report keeps working. Box UPC only appears once a box size is set, and the
codes are kept in step with the `Barcode` rows that make scanning work — a cleared code only removes the barcode
that field owned, never one that came from an import or the barcode editor. Migration `catalog/0006` backfills
`item_code` from the old SKU and `upc` / `box_upc` from existing barcodes.

**Buying flow.** `GET /api/v1/purchase-orders/{id}/workflow` returns the chain — order → **approved** → receiving →
**received & put away** → billed → paid — with per-line ordered / received / rejected / not-received / still-due
figures **in boxes and units**, the reasons given, and where each quantity was placed. The PO page renders it as a
stepper (same component as the sales chain) above a line table with those columns.

**Receiving screen** rebuilt around what a receiver actually does: per line it shows ordered and still-due in boxes,
takes **received (boxes + loose)**, **rejected** and **not received** with a reason, batch and expiry, and the
**location** to put it in (pre-filled from the product's own location). A scan box accepts unit or box barcodes —
scanning a box adds a whole box. When less arrives than was ordered, each line offers "don't expect the rest",
which closes that order line (`PurchaseOrderLine.closed`); `POST /purchase-orders/{id}/close-remaining` does the
same for everything still outstanding. Rejected goods keep going to the Quarantine warehouse at zero cost, and
rejected quantities stay *owed* by the supplier unless the line is closed.

**Boxes everywhere.** A shared `boxes(qty, packing)` formatter ("3 box + 4") is used on the PO lines, the receiving
screen, the goods receipt and the stock list (new "Boxes + loose" column). `stock_summary` gained
`item_code`, `upc`, `per_box`, `boxes` and `loose` columns, and a new report **Purchase receipts by item** answers
"how many boxes came in": ordered / received / rejected / not received / still due per item, with the location and
the reason.

**Imports** learned the new columns: a price list's "Code" becomes the `item_code` (and still the internal key), and
`UPC` / `Box UPC` columns — with the usual aliases, e.g. "unit barcode", "carton barcode", "case upc" — fill the two
barcodes, so an imported product is scannable by unit and by box straight away.

Tests: `tests/test_purchase_flow.py` (the chain, approve, receive with a rejection and a put-away location, closing
short, and the new report), two more in `tests/test_barcode_scanning.py` (unit vs box codes, and a box scan counting
twelve) and one in `tests/test_price_list_import.py` (an import that fills the item code and both barcodes). Fixed along the way: `@require_perm("delivery.create_trip")` referenced a codename that no longer
exists, and the PO form no longer asks for supplier ref or freight (freight belongs on the receipt, where it is
allocated across the lines).

## 2026-10-09 (quick receiving, and a complete audit of the buying flow)

**Quick receiving.** Goods received no longer opens with the order's items listed. It opens with a scan box and
nothing else: every scan adds what the label says — a unit barcode counts one, a box barcode or the **item code**
printed on the carton counts a whole box, and a composite box label such as `16418.01#195654` is read as *item code
before the dot, batch after it*, puts the batch on the row and asks for an expiry (empty by default). Batch, expiry
and a per-line note are all optional; a batch-tracked product received with the batch left blank takes the supplier
challan number (or the receipt number) so it stays traceable without asking the receiver for anything. Each scan is
matched against the purchase order and refused if the item is not on it. A running tally shows scans · items · units,
and a reconciliation panel — **"Against PO-000xx"** — lists ordered (due) · scanned · rejected/missing · difference
per item in boxes, so what arrived against what was ordered, and any difference, is visible before confirming;
confirming with a difference asks first and offers to close the short lines.

The header was trimmed to what the receipt actually needs: the **purchase order** is a searchable dropdown (type a
number, supplier, reference or item code — it lists only approved orders with their supplier and units still due),
the **supplier** comes from that order instead of being asked for, **Warehouse** defaults to the order's warehouse
(or, with no order, the warehouse the scanned product is kept in) and can still be changed, and the receipt note is
just **Notes**.

**Fixed by the audit** (144 API checks over codes, scanning, create, approve, receive, put-away, closing short,
bill, payment, return, the chain and the reports — plus a browser pass over both screens):

- **Receiving no longer bypasses approval.** A receipt could be posted against a *draft* purchase order, which
  dragged the order to "partially received" and left it un-approvable. `GoodsReceiptService.check_order_open`
  refuses a draft (`po_not_approved`) or cancelled (`po_cancelled`) order on create, on post and on scan.
- **Stock put away in a rack is still sellable.** `StockLevel` is keyed per bin, so once receiving started placing
  goods in the product's own location, every outward move that did not name a bin — a sale, a transfer, a purchase
  return — reported "on hand 0". `StockService.move` now draws an un-binned outward move from wherever the stock
  sits (un-binned pool first, then the racks, splitting across them when needed, each movement recording the rack it
  left), and an impossible quantity is still refused with the true total on hand.
- **A loose piece joins the carton's row.** Scanning a unit after its cartons opened a second row (and, for a
  batch-tracked item, would have split the stock across two batches). It now adds to the row already open for that
  item, both on the server and on the screen, unless several batch rows are open — then attribution is genuinely
  unknown and it gets its own row.
- **Picking an order from the dropdown loaded nothing.** The receiving screen read the order from the address bar
  only, so choosing one in the picker left the supplier, the order's lines and the reconciliation empty.
- **The order dropdown was slow.** The purchase-order list serialized every line of every order (202 KB and 774 ms
  on the demo tenant, where one imported order has 229 lines). The list and the pickers now use a slim serializer
  with SQL counts — `lines_count`, `grns_count` and `units_due` — and answer in 38–73 ms with 3.7 KB.

Tests: `test_receiving_needs_an_approved_order`, `test_a_loose_scan_adds_to_the_row_already_open` and
`test_stock_put_away_in_a_rack_is_still_sellable` (198 passing). Everything the audit created was removed
afterwards: the demo trial balance is back to 6,814,778.6634 with zero stock drift.

**Long documents scroll in the table, not down the page.** A purchase order with 229 lines made the page ~12,000 px
tall. From ten lines on, a document's line table now sits in a fixed-height box (`.table-scroll`, 60vh) that scrolls
on its own, with the column headings pinned at the top and the grand total pinned at the bottom — the page itself
stays at ~1,500 px. Applied to the purchase order's lines, the goods receipt's lines, the supplier bill's lines, the
two tables on the receiving screen (scanned rows and the "Against PO-000xx" comparison) and the shared line editor,
so sales orders, bills and returns behave the same way when they get long. Short documents are untouched.

**No supplier on the receiving screen.** Goods are received against a purchase order, and the order says who they
came from, so the Supplier field is gone: the header is now Purchase order · Warehouse · Date · Supplier challan #.
The order is required — the scan box and "Add by hand" stay disabled until one is picked, and the order field's hint
names the supplier once it is. `POST /api/v1/goods-receipts` takes the supplier from the order when the payload
leaves it out, and answers `400 po_required` when there is neither. Test:
`test_a_receipt_takes_its_supplier_from_the_order`.

## 2026-10-10 — Working areas (geofencing)

A rep can now be given the area they cover, drawn on a map, and their work is checked against it.

**Drawing an area** (Customers → Working areas → New area). One shape, three ways in:

- **Radius** — drop a pin (click the map, type coordinates, or "use my location") and drag the edge handle or the
  slider: 200 m to 50 km.
- **Area name** — type "Clifton, Karachi" and the outline comes back from OpenStreetMap. Coverage is uneven in
  Pakistan, and the screen says which you got: a town like Clifton has a published outline (135 corners), while
  Gulberg or Johar Town only have a point, so those become a circle you can resize. The name you typed is kept as
  the area's name — OSM answers in the local script.
- **Draw it** — click to drop corners, drag a corner to move it, right-click one to remove it. Switching from a
  radius starts from that circle's box, so nothing is lost.

As the shape changes the map says **how many customers fall inside**, which is the number that tells you the area
is right. A traced outline is thinned to 200 corners on save (an OSM district carries thousands); the bounding box
is stored so checks stay cheap.

**Assigning and checking.** An area is handed to one or more reps (tick-boxes on the area, and a *Working areas*
tab on the rep). From then on, every **order**, **cash collection** and **new shop** that rep records is checked
against their areas: inside it goes through, outside it is **refused** with how far out they were and which area
they were nearest (`403 outside_area`), and a missing location is refused too (`403 location_required`) so it
cannot be used as a way around the rule. A rep with **no** area is never restricted — handing someone an area is
what switches checks on for them, and an assignment can be paused for a planned trip. The policy is a company
setting (`geofence_policy`: `off` · `warn` — recorded but allowed · `block`, the default; with
`geofence_require_location` to relax the missing-fix rule while apps catch up).

Every check is recorded — inside, outside, refused, no location — with the point, the nearest area and the
distance. Three reports read it: **Out-of-area activity**, **Working areas — coverage**, and **Customers in no
working area** (which also finds customers with no pin at all). The coverage map shows every area at once with
customer pins, filled when an area covers them and hollow when none does.

For the phone: `GET /ext/v1/my-areas` returns the rep's areas, the policy and whether a location is required, so
the app can draw them and check a point without a round trip; `POST /ext/v1/orders` now accepts `gps`.

**Notes for the next person in this code.** DRF marks a request for rollback whenever an API error is *raised*, so
a refusal that raised would take the record of itself with it — `GeoFenceService.guard()` returns the 403 instead,
and the endpoints do `blocked = guard(...); if blocked: return blocked`. The geometry (`apps/partners/geofence.py`)
is plain Python — haversine for circles, ray casting for polygons, with a bounding-box pre-filter in SQL — because
this host has no PostGIS and a distribution business has tens of areas, not millions. Tests: `tests/test_geofence.py`.

Fixed along the way: the rep detail page answered **500** because its performance figures summed the UUID primary
key (`sum(uuid) does not exist`); the count it was computing was already taken separately.

## 2026-10-10 — Store locations

Where every shop is, on one map — and how the shops inside a working area reach the rep who covers it.

**Customers → Store locations.** Every customer with a pin is a dot: green inside a working area, hollow when no
area reaches it, ringed when selected. The areas are drawn over them, faded. The counters say what matters —
stores on the map, how many the current filter shows, how many fall in **no area**, and how many have **no pin at
all** (those can never fall inside an area, so they are listed on their own with a *Place on map* button).

- **Add a store** — click the button, click the map, and give it a name and a mobile. It is a customer record like
  any other, created where you pointed.
- **Fix a location** — click a store, then drag its orange handle. The move is saved as you drop it
  (`POST /api/v1/customers/{id}/pin`) and recorded in that customer's history.
- **Filter** by working area, by rep, or by name, code, phone and address.

**The pin is now set on a map, not typed.** The customer form's two GPS number boxes are replaced by a picker:
search an address, use the browser's own location, click the map, or drag the pin. The longitude box stays for when
someone has exact coordinates to paste.

**Assigning the stores in an area to a rep.** From the area page, the store screen, or the rep's own *Working
areas* tab: pick the area and only the stores **inside it** are listed, with who looks after each one today and a
tick against the ones already theirs. Then hand over **one, some, or all of them** — or take them back off
(`POST /api/v1/geofences/{id}/assign-customers` `{rep, customers:[…] | all:true, remove?}`). The rep's own tab adds
the number that matters: how many stores sit in each of their areas and how many are actually theirs, with a
warning while any are not, because a rep only sees the stores assigned to them.

Tests: `tests/test_geofence.py` — the map and pin move, that only the stores inside an area are offered and
assigned (one, all, and removal), and that a rep sees a store in their area only once it has been handed over.

## 2026-10-10 — Server security layer installed

`deploy/security/install-security.sh` has been run on the production host, so Admin → Security → **Bans** and
**Firewall** are live instead of showing the "helper not installed" card.

**What was already right:** ufw was active with 22, 80, 443 and 8081 open and everything else denied, and fail2ban
was running. The firewall part of the script changed nothing.

**What it added:** `/usr/local/bin/dms-secops` and a sudoers rule letting the app user run *only* that command as
root (every IP, port, jail and note it takes is re-validated inside the helper), plus six more jails — nginx
botsearch and bad-request, failed app logins (`dms-auth` on `dms_access.log`), vulnerability scanners
(`dms-probe`), repeat offenders (`recidive`) and the manual "block this IP" jail. Nine jails run now, up from
three. **New bans are permanent**: four bad SSH passwords or eight failed app logins in ten minutes, lifted from
the Bans page or by trusting the IP. 21 addresses were banned on the first pass, all of them SSH brute-forcers
(1,503 blocked all-time).

**Three things were wrong and are fixed:**

- **The jails watched nothing.** This host sets `backend = systemd` globally, so every file-based jail — including
  the two nginx ones that were already enabled — was reading the journal, where nginx never writes. `jail-dms.local`
  now uses the file backend for everything that names a log file, and the journal only for SSH. `dms-auth` now
  follows `/var/log/nginx/dms_access.log`, `recidive` follows `/var/log/fail2ban.log`.
- **The app could not call the helper**: the service unit sets `PATH` to the virtualenv, so `sudo` was not on it
  (`FileNotFoundError: 'sudo'`). It is now called by its full path.
- **The Bans page answered 502**: `secops("bans", "--jail", None)` dropped the empty value but kept the flag, and
  argparse rejected the dangling `--jail`. Optional flags with nothing to say are now left out entirely, which
  also fixes `trust` with an empty note.

**Rollout settings after the install.** Three addresses are now **trusted** (never banned, allowed through ufw):
the two the owner's platform-admin account and root SSH both come from, and the server's own public IP so the box
can never ban itself. Trusting is per-IP from Admin → Security → Bans; a trusted address is allowed on every port,
so it suits a static office line better than a dynamic broadband one.

**Working areas start in a transitional mode.** `geofence_require_location` now defaults to **false**: a rep
standing outside their area is still refused, but a rep whose app sends no location is let through and the gap is
recorded as `no_fix` on the out-of-area report. Turn it on (per tenant, or back in `DEFAULT_SETTINGS`) once every
app in the field sends `gps` on orders and collections — until then it would lock out the ones that do not.
`tests/test_geofence.py` covers both modes.

## 2026-10-10 — Maps in English, and areas you stretch side by side

**The map speaks English.** Plain OpenStreetMap labels Pakistani places in Urdu, which is what the map screens
showed. Admin → Settings now has a **Maps** section with four choices, and the live site is on **Esri World Street
Map** — the same roads with English labels (verified over Lahore: Ring Road, Grand Trunk Road, Harbanspura, Allama
Iqbal Road), no key needed. The others are plain OpenStreetMap (local language), **MapTiler** with your own key
and style, and a **custom tile URL** for anything else. The setting is served with the public branding, so a map
already on screen swaps its tiles the moment it loads rather than keeping the fallback.

*Rejected on the way:* Wikimedia's "osm-intl" style renders English names and needs no key, but it answers **403
to every site except Wikimedia's own** — it would have left the map blank, and working around that block is not
something to ship. Both CARTO and Esri's modern endpoints now want a key as well; Esri's legacy tile service is
open, so check Esri's terms before heavy commercial use, or point the setting at MapTiler.

**Place searches come back in English too** — the lookup now asks OpenStreetMap for English names, so "گلبرگ,
لاہور کینٹ" reads "Gulberg, Lahore Cant, Lahore Cantonment Tehsil, Lahore District". As a side effect "Johar Town"
now resolves to the suburb rather than a road of the same name.

**An area found by name is now the area, not a circle.** Searching a place used to drop a guessed circle with a
radius slider. The lookup returns the box every place occupies (Gulberg is 3.8 × 4.5 km), so that is what you get:
the real extent, drawn as a box with

- a **handle on each side** — drag it to move that side alone,
- a **handle on each corner** — moves the two sides that meet there,
- a **handle in the middle** — moves the whole area without resizing it,
- and **− / + buttons per side** in the panel, 500 m a press, for when you want it exact rather than dragged.

The size in kilometres updates as you go, next to the count of customers inside. Where OpenStreetMap does have a
published outline (Clifton's 135 corners, Gulberg neighbourhood's 28) that is still used instead, and the search
results say which you are getting before you pick one. "Radius" remains its own option for "15 km around the
depot".

## 2026-10-10 — The rep's visit: location, a photo of the shop, and a full audit

**A rep's order now carries proof of the visit.** The rep screen opens with a strip that says where they are and
whether that is inside an area they cover — *"You are inside Lahore — Gulberg"* or *"1044.6 km outside Lahore —
DHA Phase 3-5 · orders here will be refused"* — worked out on the phone with the same maths the server uses, so
they are told **before** filling a cart rather than refused at the end. Next to it is a **Shop photo** button that
opens the camera (`capture="environment"`). The photo is filed against the order and named with when and where it
was taken: *"Shop photo — 10 Oct 2026 07:45 at 31.51800,74.34800"*. `POST /ext/v1/orders` takes `gps` and
`attachments`, and a new company setting **Photo of the shop with a rep's order** (off · optional · **required**)
turns it into a rule — without one, `400 photo_required`.

**The field rules are now on the Settings screen.** Settings → Business rules gained *When a rep works outside
their working area* (refuse · record · don't check), *Refuse when the app sends no location at all*, and the photo
rule above. They were only reachable from the database before, which made the whole geofence feature invisible to
the people meant to run it.

**Demo data for the field**: `manage.py seed_field --tenant demo` draws three real Lahore areas (Gulberg, Johar
Town, DHA Phase 3-5), ten shops with pins inside them — and one, Shahdara Corner Shop, deliberately outside every
area — then hands each area and the shops in it to the rep who covers it. `--clear` takes it back out.

**The audit — 70 checks, end to end, as the people who do the work.** The rep's phone reads its areas, sees only
the shops handed to it, orders from inside the area with a photo, and is refused from Karachi, from a shop outside
every area, and without a photo when one is required. Then the office: confirm → load form → warehouse issue (stock
out, delivery note) → invoice (journal posted) → trip with vehicle, driver **and helper** → out for delivery →
delivered and signed for → the rep collects part of the cash on the next visit (which lands straight on the
invoice) → the office settles the rep's cash → the shop pays the balance → the invoice is paid, the chain shows
all seven steps done, and the customer's ledger reconciles. Reports close the loop: the refusals appear on
*Out-of-area activity* with the distance, the check that **allowed** an order points at that order, and *Working
areas — coverage* counts the shops per area per rep.

Found and fixed along the way: the field settings had no screen (above); two "Audit item" products left behind by
an earlier aborted run were still in the demo catalogue; and the rep's warning said only "Outside" for a drawn
area because the phone could not measure the distance to a polygon — it now measures it exactly as the server
does. Worth knowing for the app: `/api/v1/users` lists **memberships**, so a trip's `driver`, `helper` and
`loader` take the row's `user_id`, not its `id`.

**Two briefs for the app work** (`guide.md` and `ui.md`, in the project root). `guide.md` is the build guide for
whoever writes the mobile app: the three app modes and how the signed-in user picks one, session handling
(pre-emptive refresh, one refresh at a time, the 60-second grace window, and the only two responses that justify a
logout), the `/me` bootstrap and how to gate on it, the conventions, then rep mode in full — the areas call, the
on-device inside/outside check with the maths, the shop photo, the order payload and every refusal it can answer —
followed by driver mode, the staff screens worth having on a phone, the offline queue rules, a build order and a
test checklist against the demo tenant. `ui.md` is the design brief: who holds the phone and in what conditions,
the colour and type foundations, the navigation for each mode, every screen with its states, and the components
that carry the product (the position strip, the scan field, the signature pad, the offline banner).

Writing them found a documentation bug: `mobile-app.md` described a `/ext/v1/customers` row with
`business_name`, `alt_mobile`, `landline`, `ntn`, `strn`, `cnic`, `filer_status` and `default_discount_pct` —
fields the rep endpoint has never sent. Corrected to the row the API actually returns, and the push-registration
path (`POST /api/v1/me/devices`) was wrong in the new guide until it was checked. Every endpoint quoted in
`guide.md` is now verified against the live OpenAPI schema.

## 2026-10-10 — Rep tracking (the mobile app's contract, built)

The app team sent a contract for location tracking; the server side is live and `tracking.md` in the project root
is the reply, section by section, including where the answer differs from the ask.

**Tracking ships switched off.** It is employee location data, so a company turns it on deliberately — until then
`policy.enabled` is false and the ingest answers `403 tracking_off`.

- **Policy** — `GET /ext/v1/tracking/policy`, and inline as `tracking` in `/ext/v1/my-areas` so login costs one
  call. Sampling interval, minimum distance, the working-hours window, retention, the notice staff are shown, the
  privacy URL and the tenant's timezone. The office edits it on Settings → Business rules or with
  `PATCH /api/v1/tracking/policy`.
- **Ingest** — `POST /ext/v1/tracking/pings`, batched up to 200, deduped on `(tenant, user, client_id)` so a
  retried batch is free. The device's own time is kept as the point's time and the server's receive time
  separately, because a day out of signal is flushed hours later. A malformed point is a `400` naming its index;
  a bad *optional* value is dropped rather than refusing the batch; transient trouble stays a `5xx` so the app's
  queue retries instead of discarding the day. **No working-area check** — a rep driving home is outside every
  area, and those points are the route.
- **Reading back** — one person's day (`/api/v1/tracking/users/{user_id}`) with the route thinned by
  Douglas–Peucker to 1500 points, **stops** derived server-side (5 minutes inside 60 m, credited to a customer
  whose pin is within 100 m), and **events** — the orders, cash and deliveries of that day with the position
  captured at the time. Plus the team's day, the live map (`stale_s`, `moving`, battery) and the rep's own day at
  `/ext/v1/tracking/me` with no extra permission, because being tracked without being able to see what was
  recorded is what makes people uninstall an app.
- **The GPS that was being thrown away is now kept** (the app's §4): orders gained `gps_lat`/`gps_lng`/
  `gps_accuracy_m` and all three documents expose `gps` and feed the day's events.
- **Permissions** `tracking.view_own` · `tracking.view_team` · `tracking.manage_policy`, with the
  `/me.pages.tracking` flags the app gates on. Reading someone else's track is written to the audit log.
- **Retention** runs nightly per company and on demand at `POST /api/v1/tracking/purge-old`.

Two deliberate departures from the brief, both in `tracking.md`: purging is tenant-scoped rather than a platform
endpoint (the data belongs to the company, and it runs nightly anyway), and `distance_m` ignores segments
implying more than 150 km/h — one bad fix on the other side of the country is common, and without the guard a
morning in Gulberg reported 1,035 km. The odd point still appears on the map; it just is not counted as travel.

Found while building: `bulk_create` bypasses `Model.save()`, so the tenant has to be stamped explicitly — the
first ingest answered "Tenant is required". Tests: `backend/tests/test_tracking.py` (11), plus 37 live checks of
the contract.

**Documents add up at the end of the table.** A purchase order, a goods receipt and a supplier bill now finish
with a totals row under the columns it belongs to: the number of lines, and each quantity column summed — ordered,
received, rejected, not received, still pending — so an imported order can be tied out against the supplier's own
document without a calculator (their challan says "TOTAL : 18,395"; the order's own row says 18,382, and the
difference is the three items that were left off). Quantities total in **units**, because boxes cannot be added up
across items with different box sizes.

Fixed at the same time: those footers spanned the wrong number of columns — the purchase order's totals row
covered 11 columns in a 10-column table and the bill's mixed 8 and 10 in a 9-column table, so the amounts never
sat under the Total column.

## 2026-10-11 — Field settings on, UPCs imported, push wired

**The three field switches are on, at the owner's instruction.** `tracking_enabled` and
`geofence_require_location` now default to **true**, and the stale per-company overrides that said otherwise were
cleared. Live on all three companies: tracking records during working hours every 120 s, and an order or cash
collection that arrives with **no GPS at all** is refused `403 location_required`. Tests now assert the shipped
defaults and cover the off/relaxed modes as the exception.

**Keune UPCs imported** from `All Inventory UPC Codes.xlsx` into both Trafco companies: **429 of 495** products
now carry a barcode and a scannable `Barcode` row, up from 1 and 0. Held back deliberately: four products whose
UPC the supplier's file gives to two different codes (8719281111443 → 62635 + 62635UC; 8719281058533 → 40131
*SEMI COLOR NO. 10.31* + 40201 *SEMI COLOR HONEY*) — setting a barcode that resolves to the wrong product is
worse than having none. 71 items in the file carry no UPC at all, and one placeholder (`T-21537`) was replaced by
the real code. Receiving DC 501348 by scanner now resolves **164 of its 179 ordered lines** to the right product.

**Push is wired.** The service-account key for Firebase project `dms-invoiceflow` is installed (owner-supplied),
referenced by `FCM_CREDENTIALS_FILE`, and the server authenticates to FCM — a send to a placeholder token comes
back from Google with "the registration token is not a valid FCM registration token", which is the path working.
The key file is `chmod 600` and outside the web root; a check confirmed the nginx catch-all serves the SPA shell
there, not the file.

**`tracking.view_own` is granted**, answering the app team's question: the own-route endpoints never enforce it —
your own track is yours — but `pages.tracking.view` is the flag the app shows "My route" on, and it was false
because no role held the codename. It is now in the Manufacturer Rep, Driver and Sales Coordinator defaults and
backfilled onto every existing field role.

**Still missing: a privacy policy.** `LegalDocument` is empty, so `/legal/privacy` returns the app shell. Android
requires a prominent disclosure before background location is requested, and that screen links here — the app team
has been told to hide the link until there is real text at that route.

**GPS accuracy was being dropped on collections and deliveries.** The mobile app reported that it sends
`accuracy_m` nested in `gps` on cash collections and on delivery completion, as it does on orders. Both rows
carried the column and both write paths ignored it — `RepCollectionService.record` and `DeliveryService.deliver`
stored lat and lng only, so a 5-metre fix was indistinguishable from a 500-metre one on exactly the two documents
where "was the rep really there?" gets asked. Both now keep it; a test signs for a stop and records a collection
and asserts the value survives.

## 2026-10-11 — A privacy policy, and visits

**There is a privacy policy.** `/legal/privacy` used to return the app shell like any unmatched path, which
blocked the Android background-location disclosure — Google requires a prominent disclosure before the
permission is asked for, and that screen links here. Now: the text lives in the repo at
`apps/platform/legal/privacy.md` (1,620 words, version 1.0), `python manage.py publish_legal privacy` loads it
as the current `LegalDocument`, `GET /api/v1/legal/<kind>` serves it with **no authentication** (and `404`s a
kind that was never published, so a machine can tell real content from a catch-all), and `/legal/:kind` renders
it for a human with no login. The business can edit it afterwards in platform admin → Legal.

It is written for the people whose data is in here rather than for lawyers, and it is accurate to what the code
does: the employer owns the data and the platform processes it, location is the employer's switch, the
working-hours window, the two-minute interval, the 90-day retention, that every view of someone else's track is
logged, that a rep can always read their own, and what refusing each of the four app permissions costs them.

**Visits: the call that produced no order.** The office could see orders and nothing else, so a rep who walked
twelve shops for four orders looked identical to one who visited four shops and went home — and the eight
"still has stock" calls are what tell you whether a beat is worth walking. New `CustomerVisit`:
`POST /ext/v1/visits` (one object, or a batch of up to 100 answering `202 {accepted, duplicates, rejected}` on
the phone's offline queue, deduped on `(tenant, user, client_id)` exactly like pings),
`GET /ext/v1/visits?date=` for the rep's own beat, and `GET /api/v1/visits` for the office — paged, with a
per-person summary (calls, orders, no-orders, and the reasons behind them) computed in two grouped queries.
New permissions `visits.record` / `visits.view_own` / `visits.view_team` and a `visits` page flag; granted to
the field and office roles on every company, backfilled.

**A photo is required by default**, at the owner's instruction, and the camera-only rule is stated in the
contract (`visit.photo_source: "camera"`). The server cannot tell a camera byte from a gallery byte, so it
enforces the part it can: `photo_taken_at` older than 600 s is refused `photo_not_live`, and a capture time in
the future is refused too rather than silently passing.

**A visit outside the rep's area is kept, not refused** — the app team argued for this and they were right. An
order is a transaction and the area rule guards it; a visit is an observation, and refusing it would destroy the
evidence the record exists to keep. `GeoFenceService.check` gained `enforce=False`, which records the verdict
and never raises; each visit carries `inside_area` and `customer_distance_m` (metres from the shop's own pin),
and the office judges. An order from the same spot is still refused — verified live from Karachi against a
Lahore shop.

**Tests now write to their own media root.** `settings_test` pointed `MEDIA_ROOT` at the live site's media
folder, so a suite run as root created `media/visits/` owned by root and the server — running as `django` — got
a 500 saving the first real check-in photo. Found by running the flow against the live site rather than only in
tests, which is the lesson worth keeping.

## 2026-10-11 — A mailbox for the teams building this

Two AIs and the owner have been building this system from three sides, and every message between them was
copied by hand from one terminal into another. There is now an address: `AgentMessage` + `AgentToken`, three
endpoints under `/api/v1/agent/`, and a `manage.py agent_mail` command for the server side.

- **`POST /api/v1/agent/messages`** — `to`, `kind` (`request` | `question` | `reply` | `note`), `subject`,
  `thread`, `body` (markdown, up to 200,000 characters so a whole handover fits), `in_reply_to`, `client_id`.
- **`GET /api/v1/agent/messages`** — what has not reached you, oldest first, **and marks it delivered**: an
  agent that polls should act on each message once rather than rereading the backlog for ever. `?peek=1`,
  `?all=1`, `?thread=`, `?since=`, `?mine=1` for everything else.
- **`PATCH /api/v1/agent/messages/{id}`** — the status, and **only the recipient may change it**: the side
  doing the work says when a request is done, not the side that asked.
- **`GET /api/v1/agent/threads`** — one row per thread with `open_for_me` / `open_for_them`, which is
  "anything pending?" answered without reading the history.

Credentials are `AgentToken` rows, SHA-256 hashed with a readable prefix exactly as `ApiKey` is, sent as
`X-Agent-Token`. **A token's role is the sender**: `from` is taken from the credential and ignored in the
payload, so neither side can post as the other. Replying to a request marks it answered; a retried send with
the same `client_id` returns the original row rather than a second message. 120 requests/minute per token.

It is deliberately outside the business API: no tenant, no customer data, its own authentication. The guide
handed to the mobile team is `agent-api.md`, and the first two messages — the visits reply and an
introduction — are already in the mailbox waiting to be collected.

## 2026-10-11 — Photo freshness was measured against the wrong clock

The mobile team found this and brought the numbers. `photo_taken_at` was checked against **server receive
time**, and the app is offline-first by design — so a rep who works a beat with no coverage and flushes the
queue at six in the evening had **every visit of the day refused**, each one carrying a real photo taken at the
real shop at the real time. It failed online too: 600 s is ten minutes, and writing an order takes longer, so
a photo on arrival plus a twenty-minute order was refused with the rep standing in the shop on full signal.

The rule is now **the photo must fall inside the visit** — from `checked_in_at - 600 s` to
`(closed_at or checked_in_at) + 600 s`, on the device's own clock, which was already load-bearing for
`checked_in_at` and for tracking pings. Receive time no longer enters into it. Loose wall-clock bounds remain
(14 days old, 1 day ahead) to refuse something backfilled next month.

The same change closed a hole in the other direction: the old rule **accepted** a photo taken hours *after* the
rep left, because it was recent by our clock. One rule, both directions.

**`photo_verified` is now on the row.** A build that sends no `photo_taken_at` is still accepted — breaking
field builds with no way to update them would be worse — but it comes back `photo_verified: false` and the
office list marks the thumbnail, so omitting the field is no longer a silent way round the control. The mobile
team spotted that loophole themselves and declined to use it.

**A batch error was flattening its code.** Naming the index (`visits[0]: …`) replaced the `ErrorDetail` that
carries `code` with a plain string, so `type` came back `domain_error` instead of `photo_not_live` — and the
phone's queue branches on that field. Fixed, with a test.

Also: `no_order_reason_label` was missing from the read shape, `visits.md` now prints the row field for field
instead of describing it, and both teams' probe rows were cleared from the demo company.

## 2026-10-11 — Reps see real stock; a retried new shop no longer becomes two

**Reps see the available quantity.** At the owner's decision, `external.view_stock_exact` is granted to the
Manufacturer Rep role on every company and in the default for new ones, so `/ext/v1/stock` returns
`available` and `on_hand` beside the band. The capability already existed — it needed a permission, not an
endpoint. The app caps an order line at `available` (a hard stop, also the owner's call); the server still
accepts an over-committed order and holds it `on_hold_stock`, because a cached cap is sometimes wrong in both
directions and nothing should be lost because a phone was out of date.

**An out-of-stock item was vanishing from the rep's list.** `/ext/v1/stock` was built from `StockLevel` rows,
so a variant with nothing in the warehouse had no row to build from and was simply absent — the app had
nothing to grey out, and the one thing a rep must not order looked identical to something it had never heard
of. It now starts from the catalogue and returns `band: "out"`, `available: "0"`. `band` also comes on every
row for roles that see the number, so one call serves both the colour and the cap.

**`stock_on_hand` on `/ext/v1/products` was null on every row, always** — declared on a serializer that only
the staff views annotate. A permanently empty field is worse than no field, because a client cannot tell
"none" from "not told". Filled for a role that may see numbers, absent for one that may not.

**`POST /ext/v1/customers` ignored `Idempotency-Key`.** The app queues a new shop and retries a lost
connection, so a rep at the back of a market created the same shop twice — two codes, two ledgers, one
shopkeeper. It now behaves like `/ext/v1/orders`: a replayed key answers `200` with the original row. Found by
the mobile team, who kept the retry and reported it rather than papering over it.

Alongside it: `type` is validated against the model's own vocabulary (`"kiryana"` was being accepted and
stored), the vocabulary is published on `my-areas.customer_types` so the app ships no copy of the labels, and
**the payload is validated before the area check** — a request missing both a fix and a name used to report
only `location_required`, so a rep would walk outside for a signal to be told the name was blank.

**New: `GET /ext/v1/customers/similar?name=&phone=&city=`.** The rep most likely to add a shop is the one
search just failed, so the duplicate arrives through the one door search does not cover. It matches on the
name as typed (case, punctuation and spacing ignored, `&` and "and" alike) and on the last nine digits of the
phone, across every customer on the company rather than only the rep's own, and reports `matched_on` so the
app can word a likely match differently from a possible one. It creates nothing and blocks nothing — the rep
decides, because two salons with the same name in different areas are a real thing.

Documented for the app team: `gps` is **required** on a new customer while `geofence_require_location` is on
(the doc said optional), `name` is the only required text field, and a **`pending` shop can be sold to
immediately** — only `blocked` stops a sale, so a rep adds a shop and takes its first order on the same visit.

## 2026-10-11 — Fourteen owner requests, in parallel

**Catalogue display.** D.P is off the products list, and off the product view entirely — the price card and the
variant rows, which were still printing `cost … · avg cost …`. R.P is off the card too. The packaging and
barcode trash icons are gone from the view; the rows are **click-to-edit** instead, because removing the icons
closed the only route in the whole UI to correct a mistyped barcode, and a wrong code resolves to the wrong
product on every scan path — receiving, rep order entry, stock lookup. Deleting a barcode is still unreachable
by design.

**A bug the editable rows exposed:** edits go through the flat `/api/v1/barcodes/{id}` route, which — unlike the
nested create — did not demote the variant's other primary barcode. Ticking "primary" while correcting a code
left two primaries on one variant, and the printed label then carried whichever the database returned first.
Scanning was never affected (`resolve` matches the code, not the flag). Fixed with `perform_update`, and tested
with the real collision: two Keune item codes shipped sharing one UPC.

**Three product-form fields now carry hints**, written from what the code does rather than what the labels
suggest — and the audit found that **`is_expiry_tracked` does nothing at all**. No business logic branches on
it: the expiry input on a receipt is always shown, the near-expiry alerts and report filter on
`batch.expiry_date`, and FEFO reads the batch date. Ticking it changes one label. Either wire it up (require an
expiry on a flagged product) or drop it from the form; left as-is and flagged to the owner.

**Customer page.** The price list field is gone from the form and the detail card, at the owner's request. That
left **no way at all** for staff to see or change a customer's list — it is in no other screen, no CSV, and this
project has no Django admin — while pricing still silently honours it. `price_list` added to the customers CSV
export as the safety valve. The tax-class override that rode on the deleted card moved beside Tax numbers.

**Purchase orders.** The upload result no longer goes into the order's **notes** (the importer stamped
`"Imported from <file>"` on every one): uploading now opens a **result modal** — lines, units, value, a table of
what was left off with its own totals row, and the zero-quantity rows. A failed import is a red alert in the
modal instead of a vanishing toast. The hand-written omissions list on the live Keune PO-00001 was **moved to
the audit log**, not deleted, so the record of the three uncatalogued items and four zero-quantity lines
survives on that order's History tab. First supplier is pre-selected on a new order, without overriding an
edit or a `?supplier=` link.

**"Product search not working" was the search correctly returning nothing.** The picker was already a
searchable dropdown, scoped to the order's supplier — and all 495 products in Trafco Distribution Network had
**no supplier recorded**, because a price-list import has no supplier column. So the filter matched zero rows.
Fixed at the cause: the 495 products were assigned to Keune Pakistan (the company's only supplier; the other
two companies already had this), and the filter became `for_supplier`, which matches the order's supplier *plus*
anything unassigned — an orphan is better shown than silently missing from an order. That company went from 0
findable products to 495; Demo still correctly scopes to 30 rather than showing another supplier's catalogue.

**Goods receipts: the two requests were one bug from both ends.** Many receipts per order is deliberate and
load-bearing (partial deliveries: `partially_received`, `qty_pending`, "the rest is not coming"). The live
duplicate was something else — `trafco-distribution-network` PO-00001 held two posted receipts for the same item
93 seconds apart, 24 units with a batch then 25 with none, because the first was already **posted and therefore
not editable**, so correcting it meant receiving again. Now: a **draft is fully editable**, receiving twice
against one order **fills the receipt already open** (rows replaced, never appended — appending would double
stock), a partial unique index enforces one open receipt per order, and a posted receipt refuses the edit
naming the way back (cancel, which reverses stock and the journal, then receive again). The workflow step says
**"Continue GRN-00024"** and links to the draft instead of opening a blank screen.

Three real bugs fell out of unifying that line-writing path: editing a draft silently erased `reject_reason`
and `note` — the only record behind a supplier claim — dropped the default-bin fallback, and ignored
"close this line".

**And a status bug found in passing:** `refresh_status` only ever stepped an order *forward*, so cancelling its
only receipt left it reading "partially received" with nothing received. An order with no goods on it is waiting
on the supplier again.

**The owner's duplicate was cancelled** on their instruction: stock 49 → 24, the journal reversed by JV-00003,
the PO line back to 24 of 288, both documents still on file marked cancelled with the reason.

**Supplier rep role page — this one was ours, from earlier the same day.** Granting `tracking.view_own`,
`visits.record` and `visits.view_own` to the Manufacturer Rep role made every tenant's rep role **unsavable**:
`RoleSerializer` required every permission on an external role to start with `external.`. The owner's three
clicks are in the log at 18:00:42, 18:00:52 and 18:01:00. The editor could not even show the problem, because
the rep permission catalogue returned only the `external` module. Fixed with an explicit list of the shared
permissions a rep role may hold, which the editor now displays; office-only switches are still refused, by name.
**Second bug in the same screen:** saving a role revoked every holder's session even when nothing changed, so a
harmless click logged every rep out of the mobile app mid-round.

**Platform admin: reset a company's data.** Deletes trading history (46 models) and keeps the setup — users,
roles, warehouses, catalogue, customers, suppliers, areas, settings — restarts document numbering, and reopens
closed periods. Platform-admin only, the slug must be typed, a paying company needs a second explicit
confirmation, one transaction, audit-logged with per-model counts, and a read-only preview that counts the
damage first. 19 tests including one that counts another company's rows before and after.

**Sidebar** is an accordion: opening one group closes the others, the group you are in stays open on navigation
and reload, and the chevron follows the open group rather than the active page.

**Brand:** the 495 unbranded Trafco Distribution Network products now carry Keune.

**Test isolation:** `settings_test` pointed `MEDIA_ROOT` at the live site's media folder, so a suite run as root
created `media/visits/` owned by root and the server (running as `django`) 500'd on the first real check-in
photo. Tests now write to a scratch directory.

## 2026-10-11 — "Expiry tracked" now means something

The flag did nothing: no code branched on it, the expiry input on a receipt was always shown, and the
near-expiry alerts, the expiry report and shelf-life picking all read `Batch.expiry_date` whether or not the
product was flagged. Ticking it changed one label. At the owner's instruction it is wired up.

**The rule lives in `StockService.get_or_create_batch`** — the one funnel every new batch passes through
(receiving, an adjustment, opening stock, an import): a **new** batch of an expiry-tracked product needs a
date, because a batch created without one is invisible to every report that exists to catch it. An existing
undated batch still moves, since blocking that would strand stock already in the racks, and a date supplied
later is still backfilled.

**Receiving refuses at posting, not at saving** — the draft saves fine, so the dock can come back with the
carton in hand — and names the product. An expiry-tracked product is also given a batch to hang the date on
even when it is not batch-tracked (the challan or receipt number, as for batch tracking), because a date needs
something to belong to.

**The screen asks before anyone reaches the refusal:** the column header reads "expiry required where marked"
instead of "both optional", the field carries an amber border and *"expiry needed — read it off the box"*, and
Confirm names the item that is missing one rather than handing over a server error. `po_lines_progress` now
carries `is_expiry_tracked` / `is_batch_tracked` so the form knows which lines those are.

One existing test asserted *"no expiry is asked for"* on an expiry-tracked product — true only because the
flag was dead. Rewritten to the behaviour that was asked for, with its docstring recording why it changed and
keeping the half that is still true: a **batch** number is still substituted silently, because that one can be.

Zero products are expiry-tracked in either Trafco company, so nothing changes for the live business until
someone ticks the box; Demo has 25.

Barcode deletion stays unreachable from the UI, at the owner's decision — editing a code covers the real need.

Full suite: **302 passed**.

## 2026-10-11 — Pakistan time, and the UTC dates it uncovered

The owner asked for everything on Karachi time. The host and PostgreSQL already were; **Django and Celery were
the only pieces left on UTC**, which is exactly the mismatch that had just bitten the rep order-edit window.
`TIME_ZONE` and `CELERY_TIMEZONE` are now `Asia/Karachi`. Stored timestamps are unchanged — they are kept in
UTC and always were — so no history moved; what changed is what "today" and "local time" mean, and every
document date is derived from that. Before the change `timezone.localdate()` returned 10 Oct while it was
already 02:00 on the 11th in Lahore, so an order taken after 7pm was dated the previous day and the daily
figures cut over at 5am local.

**It uncovered a real bug in 24 places.** `timezone.now().date()` gives the *UTC* date whatever `TIME_ZONE`
says, and it was used for subscription end dates, trial expiry, discount end dates, plan changes and pro-rata
invoices, batch expiry and the near-expiry reports, stock-count and adjustment month filters, fiscal-year
creation and rep settlement ageing. For the five hours between 7pm and midnight local, all of them were a day
behind — a discount past its end date went on applying all evening. Every one is now `timezone.localdate()`.

Found by two suite failures after the switch: `discount_active` comparing an end date against the UTC date
(the real bug), and a tracking test that wrote pings "three hours ago" and then asked for *today's* route,
which breaks whenever it runs between midnight and 3am — it now reads the day its own data falls in.

`OrderService.edit_window_end` is deliberately computed from the order's creation **instant** rather than its
date, so the rep's edit window cannot depend on this setting again.

Nothing else on the host was touched — DigiInvoice shares the box and keeps its own settings.

Full suite: **310 passed**.

## 2026-10-11 — Four asks relayed from the owner by the app team

**NTN, STRN and CNIC on a new shop were accepted with a `201` and silently dropped** — the fields existed on
the customer, the rep endpoint simply never passed them through, which is the worst of the three possible
behaviours. Stored and returned now, on create and in the list, kept as the rep typed them off the licence.

**A shop added outside the rep's working area is kept, not refused.** The same distinction this system keeps
arriving at: an order is a transaction and the area rule guards it, while a new shop is an *observation* —
a rep is told about one in the next market, or adds it from the office at the end of the day, or the beat has
grown and nobody has redrawn the polygon. Refusing destroyed the record, and the shop arrives `pending`
anyway, so the office has the final say. `Customer.added_inside_area` / `added_distance_m` record the verdict
so whoever approves it can see it was 1,044 km out. An order from that spot is still refused.

**`qty_base` and `packaging_qty_in_base` on an order line.** `qty_ordered` was *already* in base units —
`_base_qty()` converts on the way in — so a client reading it beside `packaging_name` rendered "36 box" for 36
pieces, overstating by the carton size. The explicit name kills the ambiguity and the pack factor lets a
carton figure be shown deliberately.

**`PATCH /ext/v1/orders/{id}`** — a rep can correct their own order instead of cancelling and rebuilding the
basket. Allowed while **Submitted or Under review** (once confirmed, stock is reserved and the warehouse may
be picking) and until **midnight of the day it was taken**, on the company's clock. **No area check:** the rep
notices in the next street or back at the office, which is the ordinary case, not the suspicious one — and
where the order was *taken* is already on it and is not touched. The row carries `can_edit` and
`editable_until` so the app shows the button only when it will work. One shared line resolver now serves both
creating and correcting an order, so a scanned carton cannot come to mean different quantities on the two
paths.

## 2026-10-12 — Fourteen more owner items, and four export endpoints that had never worked

**Reports could not be exported at all.** `?format=pdf|csv|xlsx` 404'd *before the view body ran*: DRF's
`URL_FORMAT_OVERRIDE` defaults to `"format"`, and content negotiation raises `Http404` when no installed
renderer matches — only JSON is configured. So all 61 reports and the customer statement were dead in every
format. One line (`"URL_FORMAT_OVERRIDE": None`) fixed all four endpoints; verified live at 13,064 B PDF,
2,078 B CSV, 6,186 B XLSX and a 10,534 B statement.

**Why it went unnoticed for months is the more important fix.** Every print and export button was
`onClick={() => api.download(...)}` with no `catch`, so the rejected promise was dropped — a dead endpoint
looked exactly like a button that does nothing. `download`, `postBlob` and `printDoc` now report failures from
inside the client, so every call site is covered at once.

**Dispatch** has a driver's sheet: stops in sequence with phone, address, order value and terms, item lines
with batch, blank Loaded/Delivered/Returned columns, a per-stop signature box, crew grid and a close-out block
for odometer, fuel and cash returned. `api.printDoc` prints it into a hidden iframe, because `window.open`
after an `await` loses the click's user activation on a slow render and is silently blocked. Print and PDF were
added to the challan and load form too, and `trip-sheet` is kept as an alias since the mobile app publishes it.

**Goods receipts.** Continuing a receipt now opens with an **empty** keying area, with what is already counted
in a separate read-only card above it and a single "Fix a row" button. Adding and correcting take different
paths and both hazards were proved live first: a correction sent down the adding path doubled a line (5 → 10),
and an empty keying area sent down the replacing path wiped 15 counted units. Each scan now reports what is
left on that order line — mind that the order's `received` counts **posted** deliveries only, so the unposted
count must be subtracted on top. A receipt opens on its History, shows every line, and names the bill it is on
(`bills` is a field now; the screen had been fetching a hundred of the supplier's bills to match numbers).

**The stock ledger was reporting a purchase return that never happened.** Cancelling a posted receipt reversed
the stock using the `purchase_return` movement type, so the owner saw a return with no document behind it and
asked where it came from — fairly. New `grn_cancelled` type ("Receipt cancelled"), and the existing rows were
relabelled by the migration, identifiable by their `GRN cancelled:` note and receipt reference.

**Role saving: a no-op save was signing everybody out.** The editor is seeded from a role's *expanded* rights
while the server compared that against the shorter list it had *stored* — and every internal role differs that
way (Manager: 199 stored, 227 granted). So an untouched Save counted as a change and revoked every holder's
sessions, the mobile app included. The comparison is now like-for-like. Saving an edit no longer redirects, the
confirmation says whether anyone was signed out, and seven cache keys refresh instead of one — only
`["roles-all"]` was being invalidated, never the key the role form itself reads, so for 15 seconds after saving
the form re-seeded from the pre-save copy. That is what "changes not saving" was.

**"Should be searchable dropdown" was a bug in eleven pickers.** The filter was written into the endpoint
string (`/accounts?is_group=true`) while the component appends its own query, producing `?is_group=true?search=…`
— both silently discarded. So the picker returned the whole chart of accounts, unsearchable, posting accounts
included, and a user could pick "ASSETS" on a journal line and only fail at posting. Fixed in accounting,
settings and reports; the reports picker also never passed its filter through at all.

**Chart of accounts**: levels 1–2 have code and name locked — the balance sheet and P&L print those names as
their headings and the posting rules resolve by code prefix — and the rule is enforced in the serializer, not
only hidden in the form. Deeper accounts stay fully editable, so "Main Bank Account" can still become "HBL
Current 1234". While fixing this, a second `validate()` added to the same serializer silently replaced the
original; the test caught it.

**Accounting menu** is now Accounts → Chart of accounts → Journals → Opening balances → Periods & year end,
with Receipts & payments renamed **Transactions**. Gates and routes unchanged.

**Reset data was not broken.** Proved end to end on a throwaway company: orders deleted, catalogue, customers
and users kept, numbering restarted at SO-00001. The dialog was at fault — the red button sat disabled with no
reason given, and if the row-count request failed it could not tell whether the company was being billed, so it
hid the required confirmation and then let the user press a button the server would refuse. It now says what is
missing and assumes billed when it cannot prove otherwise.

Also: the helper text under each page name in the permission matrix is gone.

**Two tracking tests were time-of-day fragile** — they wrote pings "two hours ago" and then asked for *today*,
which fails between midnight and 02:00. The fixture now anchors inside the current local day. This is the
second time this pattern has bitten since the move to Karachi time.

## 2026-10-12 — User administration redesigned, and a bug that was wiping forms every 14 minutes

### A form being keyed into was destroyed roughly every fourteen minutes

Reported as "goods receive page refreshes after some time and clears the form". It was not a refresh: the page
was being unmounted and rebuilt. `RequireAuth` keys its `/me` query on the access token
(`["me", tenantId, access.slice(-12)]`), the token lives 15 minutes and is renewed just before expiry, and the
new key started empty — so the guard fell through to `if (!q.data || !me) return <Spinner/>`, which replaces
`<Outlet/>` and takes every form on the page with it. On a thousand-item goods receipt that is an afternoon of
counting. It applied to every form in the app.

Three changes, because there turned out to be two doors:

1. `placeholderData` on the `/me` query, so a renewal never starts from empty, and the data gate is now
   `!q.data && !me` with `cur = (q.data || me)` — the stored copy is accurate during a rotation, so the page
   stays mounted.
2. The placeholder is **scoped to the tenant** (`prevQuery.queryKey[1] === tenantId`). It belongs to the
   observer rather than to the shape of the key, so it would otherwise carry an answer across a *company*
   switch and leave the guard reading the rights of the company just left. Narrow (the header's company picker
   only) and cosmetic, but introduced by the fix above.
3. **The error branch was the second door.** `if (q.isError)` is tested before the data gate, so a failed
   refetch at the rotation mark put the retry card up in place of the page — same destroyed form. One dropped
   request is enough, because `retry` does not retry a network error (status 0). It is now `q.isError && !me`:
   with a workspace already loaded, a failed background read is not worth throwing the user's work away for.

Worth recording: that token in the key is the **only** thing in the app that re-reads a user's permissions —
nothing else refetches `/me`. So a role change reaches an open session at the next renewal rather than needing
a reload, which is a reason to keep it now that the renewal is invisible.

### Any role can be assigned to any user

The permission engine was already right: `/me` resolves a user's pages from the roles they hold, and both front
ends read it. Three things blocked the owner from using it.

**The API refused outright.** Creating a user with a field role answered "External roles are assigned through
Suppliers → Reps", so a field role could be created and then not used. Replaced by a rule: a field role
requires a rep record — an existing one, or a new one created from the supplier named (`rep_for_supplier`), so
granting field access stays one form. Removing the last field role unlinks the rep.

**A security hole this would have opened.** The rep endpoints resolve whose customers, areas and targets the
caller sees from `membership.supplier_rep`. With field permissions and no rep, `customers_qs` fell through to
**every customer in the company** — rep permissions with no rep identity is an unscoped view, not a narrow one.
Unreachable before, because field roles could only come from the rep screen; refused now, while office staff
reaching the same endpoints through `INTERNAL_EQUIVALENT` keep the company view they are meant to have.

**Being a rep locked you out of the office app.** `guards.tsx` force-redirected anyone with a rep link to
`/rep`, whatever office roles they held — which is why assigning an office role to a rep appeared to do
nothing. A user with both now lands on the side their account was created for, with a switch in both shells and
the choice remembered. A rep with no office role still cannot reach `/app`, a non-rep still cannot reach
`/rep`, and platform admins are untouched. `/me` gained `role_types` and `has_office_role` so the client reads
that as a fact instead of inferring it by subtracting the rep-grantable pages.

**New: `GET /api/v1/users/{id}/access`** (what a saved user can reach) and **`POST /api/v1/users/preview-access`**
(what a set of roles *would* grant). The screen first re-implemented `resolve_pages` in the browser to preview
before saving; the preview endpoint replaced it, because two copies of the permission rules in two languages
drift, and the screen whose job is to state the truth about access is the worst place for a second opinion.

The user form now shows both role kinds in labelled groups, reveals the rep link only when a field role is
selected, and shows **"Sees 15 of 63 pages"** with the pages grouped as they appear in the menu, each with
view/add/edit/delete — live as the selection changes, badged green when it matches what is saved and amber when
it does not.

### Bugs found on the way

- **"My customers" was broken for every rep.** The rep portal's route was gated on the *office* `customers`
  page, which no rep holds (`pages.customers.view: false`), so it rendered "Not allowed". Now gated on
  `external.view_assigned_customers`, which is what the endpoint checks. A rep with nothing assigned gets an
  empty state instead of a blank area. This would have been *masked* by the new switcher for a dual user, who
  does hold the office permission.
- **An existing user's roles never showed as ticked.** Role ids come back as numbers from `/api/v1/roles` and
  strings from the membership, so the comparison deciding each checkbox was always false — an administrator
  opening a user saw a blank slate.
- `GET /api/v1/supplier-reps` now takes `has_login=false`, so the rep picker stops offering reps that are
  already taken and can only fail validation.

## 2026-10-12 — Dates the way they are written here, and the barcode on the carton

### Every date reads DD/MM/YYYY

The app showed dates in three different shapes depending on where you looked, and the one that mattered most —
a date you *type* — was drawn by the browser, not by us. A native `<input type="date">` follows the locale the
browser was installed with, so on an en-US laptop in Karachi it read and expected **MM/DD/YYYY**: typing 03/04
for the fourth of March saved the third of April, with nothing on screen to say so.

- **A new date field** (`DateInput`) replaces all 32 native date inputs across the web app — order dates, bill
  dates, expiry on the goods-receipt rows, report filters, the lot. It takes digits (the slashes type
  themselves), accepts a two-digit year off a carton, refuses a date that does not exist (31/02) instead of
  rolling it over, and still opens the browser's calendar behind the icon. What it stores is unchanged: the API
  keeps receiving ISO `YYYY-MM-DD`.
- **Printed documents** — invoice, GRN, PO, statement, voucher, receipt, dispatch sheet, platform invoice —
  render day-first through a format module (`config/formats/en/formats.py`), so templates written later get it
  without being told. Django's own `en` locale was producing "Oct. 12, 2026" and 10/12/2026.
- **Report exports** (CSV, Excel, PDF) format dates the same way. A date in a download used to disagree with the
  page it came from, and an export of a datetime showed the UTC day — a receipt entered at nine in the evening
  appeared under tomorrow's date.
- The few remaining displays that formatted dates themselves (stock movements, the ledger tab, the legal page)
  now go through the shared helper.

Month names stay in prose, where "12 Oct 2026" reads better than a row of digits: "was taken on 12 Oct 2026 and
can no longer be changed".

### A carton's own barcode now scans

The receiving screen was already built to tell a carton from a piece — **the item code printed on the carton
counts one carton, the product's UPC counts one piece, and scanning either again adds to the same row.** What
failed was the barcode *on* the carton. A shipping carton carries a GTIN-14: the unit's EAN-13 behind a
packaging indicator digit, with its own check digit. Nobody registers those by hand, and nobody had: of 495
products in the live catalogue, 429 carry a unit UPC and **not one** carried a carton code. Scanning the carton
answered "no product matches".

- A 14-digit scan is now read for what it is — the unit's number inside a case code. Indicator 1–8 means a case,
  so one scan counts a whole carton; indicator 0 is the same single article. No data entry, and it works for
  every product that already has a UPC.
- A 12-digit UPC-A and the 13-digit EAN of the same article now find each other, because which one a scanner
  reports depends on the scanner.
- **The check digit is not used for matching**, which the app team argued for and the data settled: of 429 codes
  in the live catalogue **8 do not check out**, and some are stored as twelve digits with the check digit missing
  altogether. That digit exists so a scanner can catch its own misreads, not so our records can be checked
  against it — recomputing it would have refused a perfectly good carton because somebody typed a price list by
  hand. The article is matched on its digits and the last one is ignored, so the server and the phone's own
  offline implementation now agree.
- **A long item code is no longer cut short.** Item codes here run from five to eight digits, and a label with no
  separator was split after the fifth — so scanning `215370` could quietly resolve to the *different* product
  coded `21537`, putting a wrong line on a receipt. The whole code is tried first; the documented box label
  (`16418.01#195654`) still splits as before.
- Receiving and pick-list scanning now both take the quantity from the parser, so a carton scanned in the mobile
  app counts the same as one scanned on the web.

### Turning a right off now sticks

Unticking **Delete** for a role, saving, and coming back to find it ticked again — reported three times, and the
same for other switches. The role editor is seeded from what a role *effectively* grants, so a role still
holding an old coarse codename (`customers.manage_territories` — nine page switches in one) sent that codename
back along with the eight switches left ticked, and the expansion handed the ninth straight back.

A submitted set is now narrowed before it is stored: a coarse codename whose expansion is only *partly* ticked
is dropped, and the explicit switches stand on their own. A caller that sends the coarse codename alone — role
seeding, an older API client — still means all of it, and is untouched.

**That alone would have been dangerous**, and finding out why was the more valuable half of the work. Ten
endpoints still *demanded* a coarse codename — `purchasing.receive_goods`, `delivery.create_delivery`,
`accounting.manage_payments` among them — and the check is literal: `codename in permission_codenames()`. So
dropping the bundle on save would have locked the role out of receiving goods altogether, which is far worse
than a switch that would not turn off. Every one of those 22 decorators turned out to be a *second* gate on top
of the page matrix, which already covered the same action with the right switch (`scans` →
`purchasing.change_grn`). They are gone, and two that the matrix under-gated now ask for the correct switch
instead: creating a bin wanted only `settings.view_bin`, and reviewing a stock count only
`inventory.view_count`. The same three "Create load form" / "Send to warehouse" / "Issue stock" buttons in the
order workflow, and the "load form to issue" notification audience, now name the switch their endpoint checks.

**The page matrix is now the single gate** — nothing in the app asks for a permission that is not a page switch
(or a page's named extra). Saving a role untouched changes nothing for any of the 36 live roles; it was checked
against every one of them.

The sidebar had the same fault: the **Dispatch**, **Accounting**, **Settings** and **Reports** groups were shown
or hidden by coarse codenames, and a group is an *and* — lose the codename and the whole section disappears
though every page inside it is still permitted. A group is now visible when something inside it is, which is
what the menu already computed for its children, and the two direct links name the page their route checks
(`payments` for the accounts dashboard, `reports` for reports). All 62 page keys and the 9 remaining menu
permissions were checked against what the server can actually grant.

One deliberate consequence: **Settings → Notifications** is a personal page with no permission at all (its route
never had one), so staff who previously saw no Settings group now see it holding that one item. Their own
notification preferences are a reasonable thing for them to reach.

Also: the accounting pages now carry the names the menu uses (**Accounts**, **Transactions**).

### A company change left the previous company's lists in the cache

Chasing the owner's unexplained `Invalid pk "…" - object does not exist` on a receiving form turned this up, and
the app team had independently reported the same shape of bug from the phone ("Trafco wrong-areas report is a
client cache bug").

Of 263 query keys in the web app, **12** name the company. The rest are `["customers"]`, not
`["customers", tenantId]`, because every request carries the company in a header — which is fine until the
company changes. The cache then answers from the company just left: a dropdown offers the old company's
customers or bins, and the id picked out of it belongs to a company the server will not look in. The form looks
perfectly filled in and the save fails on an id the user never typed.

The cache is now emptied at the three moments it can go stale — signing in as a different person, changing
company, and signing out — rather than by trusting 263 keys to remember. Token rotation does **not** empty it
(refresh goes through `setTokens`, not `setLogin`), because that is the path that was destroying open forms
every fourteen minutes.

### A user cannot be created, or left, with no role

"Until role assign, user should not create or save" — the owner's words. A login with no role signs in perfectly
well and then opens nothing, which the person reads as a broken account rather than as a missing right, and it
is the state an interrupted "add user" left behind.

Roles is now a required field on the user form, with the reason under the picker, and the API refuses it too —
on create, and on an edit that would take the last role away. Access is removed by suspending the login, which
says what it means. The owner is exempt: ownership is not a role and carries everything by itself.

One consequence worth knowing: somebody who holds *add user* but not the users page's *manage access* right can
no longer create a login at all, because they cannot give it a role. Whoever creates logins should be able to
say what they open.

### The history of a document is a day at a time

A goods receipt opens on its History tab, and a receipt keyed over three days, corrected and then posted,
carries a long tail of entries — the page was mostly history before anybody had scrolled. Each day is now one
line you click to open ("12/10/2026 — 7 changes · Ali and Usman"), the most recent already open, with the time
on each entry instead of the whole date. Every document page gets this, not only receipts.

## 2026-10-12 — The two documents a customer actually receives

The sales order and the invoice are the only paperwork that leaves the building with the company's name on it,
and they were printing from a sixteen-line template inherited from the purchase order: no logo, totals crammed
into the last rows of the line table with colspans, no page numbers, nothing to sign.

**A house style for printed documents** (`templates/_doc.html`), written for A4 and for the office laser
printer — hairline rules rather than filled blocks so a draft does not drink toner, a table header that repeats
when the lines run over a page, rows that never split across one, and a page number in the margin so a customer
can tell when a sheet is missing. The letterhead carries the company logo when one is uploaded.

**The invoice** now shows: bill-to with the customer's own tax registration beside it, the order and delivery
note it came from, the salesman, batch and expiry per line, tax per line and again in the totals, the balance
still due in its own emphasised row, **the amount in words** (lakh and crore — "Rupees Three Lakh Twenty Five
Thousand Only"), and three signature lines. A draft or cancelled invoice carries a watermark so it can never be
mistaken for the real thing.

**The sales order** gained the same treatment, and shows delivery progress columns *only once something has
moved* — a fresh order prints as an order rather than as a report with empty columns.

### Print, beside PDF

Both documents already had a PDF button that downloaded a file. They now also have **Print**, which sends the
document straight to the print dialog (`api.printDoc`, the helper the dispatch sheet already used), and the PDF
button says PDF rather than wearing a printer icon.

### Editing an invoice, honestly

The owner asked for Edit on invoices. An invoice here is **posted to the ledger the moment it is raised** —
there is no draft state in practice — so its figures are owed by the customer and sitting in receivables.
Editing them behind the journal would put the two out of step with nothing to show why.

So `PATCH /api/v1/invoices/{id}` takes **the due date and the notes**, and nothing else: the amounts are
corrected with a credit note, which is a document the customer also holds, and the invoice date is the journal's
date and moves the entry between accounting periods. The dialog says exactly this rather than offering fields
that silently do nothing. The change is audited (`invoice.edited`), a cancelled invoice is refused, and a due
date before the invoice date is refused with the reason.

The invoices page gained an **Edit** switch in the permission matrix to go with it; `PUT` is refused, because a
full replace would blank what it does not carry.

### A draft stage, so a wrong invoice can be fixed before it is owed

Following on from the above: the honest answer to "edit the amounts" was that an invoice is posted the moment
it is raised, so there is nothing to edit that is not already in the ledger. The owner asked for the
recommendation, so the missing stage now exists.

- `POST /sales-orders/{id}/invoice` takes `{"post": false}` and leaves the invoice a **draft**. The default is
  unchanged — the invoice goes out with the vehicle as it always has, and nobody has a new step in their day.
- On the order's workflow strip, under **Create invoice**, there is now a quieter *"Raise as draft to check
  first"*.
- A draft's **date, prices and per-line discounts** can be corrected (`PATCH`), and the tax is **recomputed**
  from the corrected figures rather than scaled, because a price change can cross a slab and the slab is what a
  tax authority checks. Quantities are not editable even in draft: what is on the invoice is what the warehouse
  issued and the delivery note records, and letting those two disagree would leave the stock that left and the
  stock that was billed as different numbers with nothing to say which is right.
- **Post to ledger** on the invoice page sends it; after that the same correction is refused and the refusal
  names the credit note.

Found on the way: the workflow strip's **Create invoice** button was gated on `sales.add_sales_invoice`, which
is not a codename anybody can hold — so the button was invisible to everyone except the owner. It now names
`sales.add_invoice`, the switch the endpoint actually checks. That is the third button found hiding behind a
permission name that does not exist; the app team found two more of the same shape in their routing today.

## 2026-10-12 — Accounts: cheque books, expense categories, and the four statements

### Cheque books — the cheques you write

A cheque **you write** comes out of a book the bank gave you, and the bank will ask about every stub. A cheque a
customer **gives you** is their paper with their numbering. The system only tracked the second kind: a number
typed onto a payment. So:

- **Accounting → Cheque books.** Add the book your bank handed over — first number, last number, any prefix —
  and its leaves are laid out individually. Each book shows how far through it you are, and the **next cheque to
  write** in plain sight, which is the only thing anybody comes to the page to find out.
- **Paying by cheque now offers the book.** On a payment going *out* from a bank account, the cheque number is a
  dropdown of what is actually unused, with **the next one already chosen**. It is a dropdown rather than a
  fixed next number because cheques genuinely do get written out of order. Money coming *in* still gets a plain
  box — that is the customer's cheque, and their numbering is none of our business.
- **A spoiled leaf** (torn, misprinted, cancelled before it went out) is marked as such with a reason, and the
  next-cheque-to-write skips it. A leaf that has already been written cannot be called spoiled; the refusal says
  to cancel the payment first.
- A book empties itself out of the picker when its last leaf is gone, and cannot be deleted once cheques from it
  have been written — it is part of the record by then.
- The whole register exports to CSV or Excel.

### Expense categories

An expense category **is** an expense account in the chart — that is why every rupee spent lands in the ledger
and appears in the income statement without anybody keeping a second list. The new page is the chart filtered to
the part a cashier deals with, with **what has actually been spent against each category** beside it for a
period you choose, and Print / PDF / Excel.

### The four statements, each on its own page

**General ledger**, **Trial balance**, **Income statement** and **Balance sheet** were only reachable through
the generic report runner. Each now has its own page under Accounting, shaped like the statement it is rather
than as a grid of columns: accounts indented by their level with groups in bold, the trial balance's debit and
credit columns grouped under Opening / In this period / Closing with a visible check that it balances, the
income statement's gross and net profit called out above the detail, and the balance sheet set out as assets
against liabilities and equity with a balanced badge.

All four carry **Print**, **PDF** and **Excel**, served by the same endpoint that draws the screen — so the page,
the printout and the spreadsheet cannot disagree.

### Printed reports now use the house style

Every report PDF — all 61 of them, not only the four above — now prints on the same letterhead as the invoice
and the order: company details, page numbers, repeating table headers, and the figures grouped (`4,328,496.20`
rather than `4328496.2018`). Column headings read as words (`Opening Debit`, not `OPENINGDEBIT` — Django's `cut`
filter *deletes* the underscore rather than replacing it), account codes are left alone instead of being
formatted as money, and a `balanced` flag prints as Yes rather than True.

### A bug this found: every Excel export was broken

Today's date work added a `cell()` formatter to the exporters, and `to_xlsx` already had `for cell in ws[1]:`
styling its header row — so by the time the first data row was written, `cell` was a header cell rather than the
function, and every `.xlsx` download raised `TypeError: 'Cell' object is not callable`. **No test covered the
Excel path at all**, which is how it shipped. There is now one over every financial statement in all four
formats, plus a test that no ISO date leaks into an export.

### Print, PDF and Excel on every list

Asked for across the Sales menu, built once where it belongs: `GET <list endpoint>/export?format=csv|xlsx|pdf`
exists on **54 list endpoints**, and the shared `ListPage` now carries a Print · PDF · Excel group, so every list
in the app gained it rather than five pages in Sales.

What makes it trustworthy is that the file is built from the same request the screen made — the filters, the
search and the ordering go with it, and only the page number is dropped (nobody wants page 2 of their export).
Exporting needs the same permission as reading the list, so nothing can be downloaded that cannot be opened.

The five sales documents name the columns they want (`export_fields`), because a serializer's field list is
right for a settings table and noisy for a document — an export of orders had GPS coordinates and cost centres
in it. Everything else falls back to the serializer minus the plumbing: ids, versions, nested rows.

## 2026-10-12 — Email through Microsoft 365, configured from the admin page

The app had never actually sent an email: `EMAIL_BACKEND` was Django's console backend, so every invitation,
OTP and alert was written to the log and nowhere else. It now sends through **Microsoft Graph**, configured in
Admin → Settings → Messaging rather than in a file.

- **Why Graph rather than SMTP.** Microsoft 365 is turning SMTP AUTH off, and an app registration can send as a
  mailbox without that mailbox's password ever existing in this system. The flow is client credentials, which
  needs the **application** permission `Mail.Send` granted with admin consent in Azure.
- **Configured in the admin page**: provider, Directory (tenant) ID, Application (client) ID, client secret and
  the From address, all editable without a deploy. The secret is **never sent back to the browser** — the page
  shows `••••••••2aVs` and a `graph_client_secret_set` flag, and saving the mask back leaves the stored secret
  alone, so editing another field cannot wipe it.
- **A test button that sends a real message.** Settings can be filled in perfectly and still not deliver: a
  secret copied from Secret ID instead of Value, consent never granted, a From address that is not a mailbox.
  Each of those now comes back as a sentence saying what to do — *"In Azure → your app → API permissions, add
  the application permission Mail.Send and click Grant admin consent"* — rather than as a status code.
- `send_email()` routes on the configured provider and still **never raises**: an invitation is a side effect of
  creating a user, and a mail server having a bad day must not fail the thing that triggered it. The failure is
  logged with the subject and the reason, never the body, which may carry an OTP or an invite link.

Live: the connection was proved by sending a real message to info@apitsnetwork.com, both directly and through
the admin test button.

## 2026-10-12 — Sales invoice and PO: no tax columns, no STRN; print opens a tab; PO gains print

By the owner's instruction, two documents were simplified and brought onto the house style.

- **Sales invoice**: the per-line Sales Tax / Further Tax columns and the tax rows above the total are gone, and
  so is STRN — both the company's in the letterhead and the customer's in the tax box (NTN stays). Each line's
  Amount is already tax-inclusive, so the column now sums exactly to the Total and the page reconciles with a
  pencil; the old "Subtotal before tax" line had quietly disagreed with the amounts above it.
- **Purchase order**: the same — Tax 1 / Tax 2 columns and rows removed, STRN removed from the letterhead — and
  the PO was moved onto the same letterhead as the invoice (logo, page numbers, amount in words, signatures, a
  draft/cancelled watermark). This also fixed a long-standing inconsistency: the PO's line Amounts are
  tax-inclusive and summed to the Total, but the old template printed a pre-tax "Subtotal" beneath them.

- **Print now opens the document in a new tab** rather than a hidden iframe, with the print dialog following once
  the viewer has laid the page out. The tab is opened on the click (a tab opened after the fetch is what browsers
  block) and falls back to showing the file if a popup blocker refuses.
- **The purchase order gained Print and PDF** on its detail page — it never had them — and a per-row Print button
  in the PO list.
- **New sales orders start with the discount column hidden.** A small Discount switch by the scan box brings it
  back when a deal needs one, and it reveals itself automatically when an order already carries a discount.

## 2026-10-12 — STRN and tax columns removed from every document (view, PDF, print)

Extending the invoice/PO change to all documents, by the owner's instruction:

- **STRN** is gone from every document letterhead — stripped at the source in both base templates
  (`_doc.html` and `purchasing/_doc_base.html`), so the sales invoice, sales order, PO, GRN, delivery challan,
  dispatch sheet and pick list all drop it at once while NTN stays. It is kept only where it is *captured* —
  the customer, supplier and tenant forms — because that is master data, not a document.
- **Tax columns and tax total rows** removed from the on-screen views as well as the PDFs: sales invoice, sales
  order, credit note and purchase order. Each document's line Amounts are tax-inclusive, so the amount column
  reconciles to the Total without a pre-tax subtotal.
- **Not touched: the purchase bill.** A bill records the supplier's tax invoice and its input tax is what an
  accountant claims, so stripping tax from it would remove a real accounting figure. Flagged for the owner to
  decide rather than removed silently.

Guard tests assert STRN and tax never reappear on the invoice, order or PO, even when the company has an STRN.

## 2026-10-12 — GRN print; chart-of-accounts top two levels fully locked

- **The GRN view gained Print and PDF** (it had neither), matching the PO and invoice. The GRN totals footer
  was also collapsed to a single line — line count, unit sums and Total value on one row instead of two.
- **The first two levels of the chart of accounts are now fully non-editable.** The five classes (ASSETS,
  LIABILITIES, …) and their groups (Current Assets, Bank Accounts, …) are the standard chart itself: the
  balance sheet and P&L print their names as headings, and the automatic journals resolve accounts by code
  prefix. The API previously locked only their code/name/parent/type — now it refuses *any* change to a level
  1–2 account (subtype, active flag, description included). The screen hides the edit pencil for these rows and
  shows a lock, while "Add an account beneath it" stays available. A no-op save is still accepted, and level
  3–4 accounts remain fully editable.
