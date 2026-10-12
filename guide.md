# Build guide — DMS mobile app

**For the AI building the mobile app.** Everything you need to ship it is here: what to build, the exact HTTP
contracts, the rules the server enforces, and the order to build in. Where this guide says "see `mobile-app.md`",
that file is the exhaustive endpoint catalogue — this one is the working brief.

- **API base**: `https://dms.invoiceflow.pk`
- **Interactive schema**: `https://dms.invoiceflow.pk/api/docs/` (generated from the running code — if this guide
  and the schema ever disagree, the schema is right)
- **Design brief**: `ui.md` in this folder
- **Last verified against the live API**: 10 Oct 2026

---

## 1. What you are building

One app, three modes. The signed-in user decides which one opens — never ask them.

| Mode | Who | How you know | What it does |
| --- | --- | --- | --- |
| **Rep** | A manufacturer's sales rep, in the field all day | `/me` → `supplier_rep` is not null | Visits shops, takes orders, collects cash |
| **Driver** | Delivers the orders | `/me` → `pages.delivery_notes.extras.complete` is true, `supplier_rep` is null | Runs a trip, proves each delivery |
| **Staff** | Owner, coordinator, warehouse, cashier | neither of the above | Approves orders, issues stock, takes payments |

The rep mode is the one that matters most: it is used standing in a shop, on mobile data, in sunlight, one-handed.
Build it first and build it well. The other two are simpler.

---

## 2. Start here (15 minutes to a first order)

```bash
BASE=https://dms.invoiceflow.pk

# 1 · log in (identifier = email or mobile)
curl -s -X POST $BASE/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"identifier":"rep1@colormax.pk","password":"rep123","device_name":"Pixel 8","platform":"android"}'
# → { access, refresh, user, memberships:[{tenant_id, tenant_name, …}], must_change_password }

# 2 · every later call carries both headers
AUTH="Authorization: Bearer $ACCESS"; TEN="X-Tenant-ID: $TENANT_ID"

# 3 · bootstrap
curl -s $BASE/api/v1/me -H "$AUTH" -H "$TEN"

# 4 · the rep's working areas and the rule they are held to
curl -s $BASE/ext/v1/my-areas -H "$AUTH" -H "$TEN"

# 5 · their shops, then an order
curl -s "$BASE/ext/v1/customers?search=Gulberg" -H "$AUTH" -H "$TEN"
```

**Demo logins** (tenant `demo` — never write to tenant `trafco`, it is a real customer):

| Role | Identifier | Password |
| --- | --- | --- |
| Rep — Gulberg + DHA areas | `rep1@colormax.pk` | `rep123` |
| Rep — Johar Town area | `rep2@colormax.pk` | `rep123` |
| Driver | `driver@demo.pk` | `driver123` |
| Owner (staff mode) | `owner@demo.pk` | `owner123` |
| Warehouse keeper | `store@demo.pk` | `staff123` |
| Cashier | `cashier@demo.pk` | `staff123` |

Seeded field data to test against: three Lahore working areas (Gulberg, Johar Town, DHA Phase 3-5), shops
`FS-001`…`FS-009` inside them, and `FS-010` (Shahdara) deliberately **outside every area**.

---

## 3. Session handling — get this right or the app logs people out

```
POST /api/v1/auth/login     { identifier, password, device_name, platform } → { access, refresh, user, memberships }
POST /api/v1/auth/refresh   { refresh } → { access, refresh }        ← tokens ROTATE: store the new refresh
POST /api/v1/auth/logout    { refresh }
```

Rules, all enforced server-side:

1. **Access tokens are short-lived. Refresh *before* expiry**, when under ~45 seconds of life remain — not after a
   401.
2. **Refresh once at a time.** Put refreshes behind a mutex. Two parallel refreshes with the same token are the
   classic cause of mass logouts.
3. There is a **60-second grace window**: a token rotated moments ago is accepted once more, so a retry is safe.
   Reuse outside that window revokes the whole session family.
4. **Only log out when `/auth/refresh` itself answers 401 or 403.** A `429`, a `5xx` or a dropped connection means
   *keep the session* and report one failed call.
5. `must_change_password: true` ⇒ send them to a change-password screen before anything else.
6. One account can belong to several companies. If `memberships.length > 1`, show a company picker and remember the
   choice; `X-Tenant-ID` must be sent on **every** call. Never hard-code a tenant id.
7. `403` with `type: "impersonation_expired"` or `"tenant_not_member"` ⇒ the stored company is stale: clear it,
   re-run `/me`, return to the company picker. The login itself is still good.

**QR login** (no typing): `POST /api/v1/auth/qr-login/redeem { token, device_name, platform }` → the same payload
plus `tenant_id`. Full spec in `mobile-app-qr-login.md`.

---

## 4. `/me` — the bootstrap call

`GET /api/v1/me` returns the user, their companies, the tenant (currency, timezone, settings, plan), `features`,
and two permission shapes:

- **`pages`** — for staff screens. Four switches per screen plus extras:
  ```ts
  const can      = (page, action) => Boolean(me.pages?.[page]?.[action]);       // view | add | edit | delete
  const canExtra = (page, extra)  => Boolean(me.pages?.[page]?.extras?.[extra]);
  can("sales_orders", "add");             // show "New order"
  canExtra("delivery_notes", "complete"); // driver may record a delivery
  ```
  **`view` is the gate** — without it the screen must not open. Mirror these and you will never show a button that
  answers 403.
- **`permissions`** — the raw codename list; rep screens use this (`external.submit_order`,
  `external.view_assigned_customers`, `external.record_collection`, `external.view_prices`, …).

Also on `/me`: `supplier_rep` (non-null ⇒ rep mode), `warehouses` (non-empty ⇒ this user is restricted to those
warehouses), `is_owner`, `tenant.settings` (business rules you should respect in the UI).

---

## 5. Conventions that apply everywhere

| Topic | Rule |
| --- | --- |
| IDs | UUID strings. Documents also carry a human `number` (`SO-00013`, `INV-00005`, `TRIP-00002`) — show that, never the UUID. |
| Money | Strings with 4 decimals (`"1250.0000"`). Display 2. Never parse into a float for arithmetic you then send back. |
| Quantities | Strings with 3 decimals, **always in base units** (pieces). A `packaging` (box/carton) carries `qty_in_base`; multiply yourself when showing cartons. |
| Dates | `YYYY-MM-DD`; timestamps ISO-8601 UTC. Display in `tenant.timezone` (Asia/Karachi). |
| Lists | `?page=1&page_size=25` → `{ count, page, pages, results }`. Stock movements use cursor pagination — follow `next`. |
| Sync | `?since=<ISO>` on `/ext/v1/*`, `?updated_after=<ISO>` on `/api/v1/*`. |
| Errors | Always JSON: `{ type, title, status, detail, errors:{field:[msg]} }`. **Show `detail`**; put `errors.<field>` under the input. |
| Business rules | `422`, with `type` naming the rule: `insufficient_stock`, `credit_hold`, `period_closed`, `po_not_approved`, … |
| Conflicts | Documents carry `version`; a PATCH must send the version you loaded. `409` ⇒ reload and retry. |
| Idempotency | `Idempotency-Key: <uuid>` on every POST that creates something. A retry returns the original response with `Idempotent-Replayed: true`. **Mandatory for the offline queue.** |
| Throttling | `429` on auth (20/min) and `/ext/v1` (60/min). Back off using `Retry-After`. |
| Files | Multipart for uploads. PDFs come from `…/pdf` endpoints and need the Bearer header — download with headers, don't hand a URL to a WebView. |

---

## 6. Rep mode — the visit is the centre of the app

A rep's day is a list of shops. At each one they may take an order and collect cash. The server ties both to
**where the rep is standing** and (optionally) **a photo of the shop**. Build the app around that.

### 6.1 Load the rules once, at login

```
GET /ext/v1/my-areas
→ { "policy": "block",              // "off" | "warn" (recorded) | "block" (refused)
    "location_required": false,     // true ⇒ no GPS fix, no order
    "areas": [ { "id", "name": "Lahore — Gulberg", "type": "polygon",
                 "colour": "#F26522", "centre": {"lat","lng"}, "radius_m": 0,
                 "points": [{"lat","lng"}, …], "area_km2": 18.97 } ] }
```

Cache it. A rep with **no** areas is unrestricted — never block them.

### 6.2 Check the position on the device, before the user does any work

Run the same test the server runs, so the rep is warned up front instead of refused at submit time:

- **circle**: inside when `haversine(fix, centre) ≤ radius_m`
- **polygon**: ray casting; when outside, the distance to the nearest edge gives the "N km outside" message

```ts
const R = 6371008.8, rad = (d: number) => d * Math.PI / 180;
function metres(a, b) {                       // haversine
  const h = Math.sin(rad(b.lat - a.lat) / 2) ** 2 +
            Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(rad(b.lng - a.lng) / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
function inRing(p, ring) {                    // ray casting
  let hit = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[i], b = ring[j];
    if ((a.lat > p.lat) !== (b.lat > p.lat) &&
        p.lng < ((b.lng - a.lng) * (p.lat - a.lat)) / (b.lat - a.lat) + a.lng) hit = !hit;
  }
  return hit;
}
```

The web rep portal implements exactly this in `frontend/src/features/rep/Visit.tsx` — read it and mirror it.

A persistent strip at the top of every visit screen says one of:

- `You are inside Lahore — Gulberg` (green)
- `1044.6 km outside Lahore — DHA Phase 3-5 · orders here will be refused` (red)
- `Location is off — turn it on so the order counts for your area` (amber, with a retry button)

### 6.3 The shop photo

`tenant.settings.rep_order_photo` is `off` · `optional` · `required`. When it is not `off`, offer a camera button
on the visit strip; when it is `required`, no photo means no order (the server answers `400 photo_required`).

Use the **camera**, not the gallery (`capture="environment"` on web; the camera intent on native). Downscale to
roughly 1280 px on the long edge and JPEG ~70% before encoding — a 6 MB photo on a 3G connection in a shop is a
failed order. Send as a data URL in `attachments`.

### 6.4 Place the order

```json
POST /ext/v1/orders          Idempotency-Key: <uuid>
{
  "external_ref": "APP-2026-10-10-0007",
  "customer": { "id": "uuid" },
  "gps": { "lat": 31.5180, "lng": 74.3480 },
  "attachments": ["data:image/jpeg;base64,…"],
  "requested_delivery_date": "2026-10-12",
  "notes": "Deliver before noon",
  "lines": [
    { "variant_id": "uuid", "qty": 6 },
    { "sku": "CM-7.1-60", "qty": 2, "packaging": "carton" },
    { "barcode": "6291041500001", "qty": 1 }
  ]
}
```

- A line is identified by `variant_id`, `sku` **or** `barcode` — whichever the screen has.
- `qty` is in the packaging named, or base units when `packaging` is omitted.
- Do **not** send prices. The server applies the customer's price list, standing discount and schemes.
- `external_ref` makes the order idempotent across retries even if the key is lost.

Replies you must handle:

| Status | `type` | What it means | What the app does |
| --- | --- | --- | --- |
| 201 | — | Order taken | Show the number, clear the cart |
| 403 | `outside_area` | Standing outside every area they cover. `errors.distance_m`, `errors.fence` | Show `detail` as-is; offer "retry location" |
| 403 | `location_required` | No fix, and the company requires one | Prompt for location permission |
| 400 | `photo_required` | Photo compulsory | Open the camera |
| 422 | `credit_hold` | Customer over limit — order held for approval, not lost | Tell them the office will approve |
| 422 | `insufficient_stock` | Not enough on hand | Offer to reduce the quantity |

### 6.5 The rest of rep mode

| Call | Purpose |
| --- | --- |
| `GET /ext/v1/customers?search=&since=` | Their shops only. The row is exactly `{ id, code, name, type, phone, address, city, gps:{lat,lng}|null, territory, route, status, is_blocked, credit_limit, credit_days, price_list, outstanding, overdue, updated_at }` — nothing else, whatever older docs say. The customer's standing discount is applied by the server; the app cannot show it |
| `POST /ext/v1/customers` | Add a shop: `{ name, type, phone, address, city, gps:{lat,lng} }` — also area-checked, and usually needs office approval |
| `GET /ext/v1/products?search=&customer=<id>&since=` | Catalogue with `price` already resolved for that customer, `packagings[]`, `barcodes[]`. Cache it |
| `GET /ext/v1/stock?warehouse=` | Availability |
| `GET /ext/v1/orders?since=&status=` | Their orders; `GET /ext/v1/orders/{id}` for lines, trip, driver, invoices |
| `POST /ext/v1/orders/{id}/cancel` | Only while `submitted` |
| `GET /ext/v1/deliveries?order=` | Where a delivery has got to |
| `POST /ext/v1/collections` | `{ customer, amount, method, reference, gps }` — **also area-checked**. Allocates to the oldest open invoices unless you pass `allocations` |
| `GET /ext/v1/collections?status=held_by_rep` | Cash in their pocket, with `held_total` — show it prominently; it is money they must hand in |
| `GET /api/v1/customers/{id}/statement` | Ledger, ageing, open invoices (needs `external.view_customer_outstanding`) |

**Two known gaps, worth designing around:**

1. `/ext/v1/products` returns `stock_on_hand: null` for reps — they cannot see availability, so an order can be
   taken for stock that is not there and will land on hold. Either call `/ext/v1/stock` alongside the catalogue,
   or show the office's reply plainly when it happens.
2. **A company may have no barcodes at all.** Scanning is the first thing on your order screen, but the catalogue
   is often imported from a price list that carries none: of the three companies on the server today, one has
   barcodes on 31 of 526 products, one on 1 of 495, and one on **0 of 495**. Check
   `GET /ext/v1/products` for a non-empty `barcodes[]` once at login; where there are none, lead with search
   instead of the scanner and keep scanning as the secondary path. A scan that finds nothing must say
   *"that barcode is not on any product yet"* and drop into search with the digits already typed — never a dead
   end.

---

## 7. Driver mode

```
GET  /api/v1/trips?active=1                     my trips
GET  /api/v1/trips/{id}                         stops in `delivery_notes[]`, in `sequence` order
GET  /api/v1/delivery-notes?trip=<id>&status=loaded   (treat `dispatched` the same — both are "to do")
POST /api/v1/delivery-notes/{id}/deliver        proof of delivery
GET  /api/v1/trips/{id}/reconciliation          loaded / delivered / returned / cash
GET  /api/v1/delivery-notes/{id}/pdf            challan
```

Proof of delivery is **multipart** when it carries files:

| Field | Notes |
| --- | --- |
| `delivered` | `[{ line_id, qty_delivered, qty_returned, return_reason, return_condition }]`. Omit ⇒ everything loaded was delivered |
| `receiver_name` | who signed |
| `signature`, `photo` | files |
| `gps` | `{lat,lng}` at the door |
| `cash_collected` | creates a receipt against this stop's invoice |
| `delivered_at` | the time it actually happened (important when queued offline) |
| `failed: true` + `failure_reason` | shop closed etc. — everything returns |

Stock has already left the warehouse when the trip starts; the driver only reports what happened. An uninvoiced
stop creates its invoice on delivery. `422` when delivered + returned exceeds what was loaded.

---

## 8. Staff mode

Only build what the customer asks for — the web app covers everything. The screens that earn their place on a
phone:

| Screen | Calls |
| --- | --- |
| Orders inbox | `GET /api/v1/sales-orders?status=submitted`, then `POST …/{id}/confirm` · `/hold` · `/reject` |
| Order chain | `GET /api/v1/sales-orders/{id}/workflow` → seven steps, each with its state, documents and next action. Render it; it is the clearest screen in the product |
| Load form → issue | `POST /sales-orders/{id}/load-form` → `POST /pick-lists/{id}/send` → `POST /pick-lists/{id}/issue { issues:[{ line_id, qty_issued }] }` (lines carry `qty_to_pick`) |
| Invoice | `POST /sales-orders/{id}/invoice` — **returns a list** (one invoice per delivery note) |
| Dispatch | `POST /api/v1/trips { warehouse, vehicle, driver, helper, order_ids:[…] }`. `driver` is a Driver id; **`helper` and `loader` are user ids** — `/api/v1/users` lists memberships, so use the row's `user_id`, not its `id` |
| Warehouse scanning | `GET /api/v1/barcodes/resolve?code=…` → `{ variant, packaging, qty_per_scan, unit, batch_no, item_code, packing, location }`. A unit barcode counts 1; a box barcode or the printed **item code** counts a whole box; a label like `16418.01#195654` carries its batch (item code before the dot) |
| Goods received | Create an empty receipt, then `POST /goods-receipts/{id}/scans { scans:[{code}] }` repeatedly, then `/post`. Batch and expiry are optional |
| Cash | `POST /api/v1/accounting/payments { direction:"in", partner_type:"customer", partner_id, amount, method, allocations:[{document_type:"sales_invoice", document_id, amount}] }` |
| Rep settlement | `POST /api/v1/rep-settlements { rep, collections:[…], cash_account }` |

---

## 9. Offline

Field staff lose signal constantly. Design for it from the first commit.

**Cache (read-only, refresh with `?since=`)**: customers, products with prices and barcodes, the rep's areas, open
orders, today's trip and its stops.

**Queue (writes)**: orders, collections, new customers, deliveries. Each queued item carries:

```
{ id: uuid, method, path, body, idempotencyKey: uuid, capturedAt: ISO, attempts: 0, files: [localPath] }
```

Rules:

1. One `Idempotency-Key` per queued item, generated when it is **created**, reused on every retry. Never generate
   a new one on retry.
2. Send in order, one at a time. Stop on the first `5xx`/network error and retry with backoff.
3. A `4xx` is **final** — do not retry. Move the item to a "needs attention" list with the server's `detail`, and
   let the user fix or discard it. An order refused with `outside_area` must not silently vanish.
4. Keep the captured time (`delivered_at`, and the photo's own timestamp) so the office sees when it really
   happened.
5. Show the queue depth in the UI. A rep must be able to see "3 orders waiting to send".

---

## 10. Build order

1. **Shell**: login, company picker, token refresh with the mutex, `/me`, mode routing, error toasts that render
   `detail`.
2. **Rep: visit + order.** Areas cached, the position strip, customer list, catalogue with search and barcode
   scan, cart, photo, submit with an idempotency key. This is the product — polish it before moving on.
3. **Rep: money.** Collections with the cash-held total, customer statement, order history and status.
4. **Offline queue** behind everything built so far.
5. **Driver**: trip list, stop list, proof of delivery with signature and photo.
6. **Staff**: inbox and the order chain screen, then whatever the customer asks for.
7. **Push** (FCM): register the handset with `POST /api/v1/me/devices { token, platform, device_name }` (list and
   remove with the same path). Notifications: order confirmed, trip assigned, payment received.

---

## 11. Test it against the demo tenant

Each of these is verified working on the live API — if one fails, the app is wrong, not the server.

- [ ] Log in as `rep1@colormax.pk`, land in rep mode, see 19 shops and **not** `FS-010`.
- [ ] Order from `FS-001` (Gulberg) with the device at `31.5180, 74.3480` and a photo → **201**.
- [ ] Same order with the device at `24.8607, 67.0011` (Karachi) → **403 `outside_area`**, detail reads
      "1044.6 km outside Lahore — DHA Phase 3-5".
- [ ] Turn location off → the strip says so; with `location_required: true` the order is refused
      **403 `location_required`**.
- [ ] Set the company's photo rule to `required` → an order without a photo is **400 `photo_required`**.
- [ ] Collect cash at `FS-001` → `held_total` rises; the same from Karachi → **403**.
- [ ] Queue an order with the radio off, restore signal → it sends once, and sending twice does not duplicate it.
- [ ] Log in as `driver@demo.pk` → today's trip, deliver a stop with a signature and `cash_collected`.
- [ ] Kill the app mid-refresh, reopen → still signed in.

---

## 12. Stack

React Native (Expo) or Flutter — either is fine. What matters:

- a single HTTP layer that owns the two headers, the refresh mutex, idempotency keys and the offline queue;
- a typed API client generated from `https://dms.invoiceflow.pk/api/schema/` (OpenAPI);
- a real local database (SQLite/WatermelonDB/Isar) — not AsyncStorage — for the cache and the queue;
- camera, geolocation and barcode scanning as first-class permissions, each with a "why we need this" screen.

Full endpoint catalogue: **`mobile-app.md`**. QR login: **`mobile-app-qr-login.md`**. Screen and visual design:
**`ui.md`**. Where the field force has been — the policy, the ping ingest, the day's route, stops and live map:
**`tracking.md`**.

## 10. Check-in — the call that produced no order

`POST /ext/v1/visits` (batched, up to 100), `GET /ext/v1/visits?date=`, office at `GET /api/v1/visits`.
Permissions `visits.record` / `visits.view_own` / `visits.view_team`; page flags at `/me.pages.visits`.

Three rules the server holds you to:

1. **A photo is required** unless the company says otherwise — read `my-areas.visit.photo`
   (`required` | `optional` | `off`). Open the **camera**, never the gallery: a no-order call has no other
   evidence behind it, and a shuttered shop is a photograph.
2. **Send `photo_taken_at`.** Older than 600 s is refused `400 photo_not_live`. This is the only part of
   "live photo" a server can enforce.
3. **A visit is never refused for being outside the area** — unlike an order. The position and the verdict
   are recorded (`inside_area`, `customer_distance_m`) and the office judges.

Reason keys come from `my-areas.visit.reasons`; render the labels you are sent rather than shipping a copy.
The full contract, and where it differs from what the app team specified, is in `visits.md`.

