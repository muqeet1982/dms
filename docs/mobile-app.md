# DMS Mobile App — Backend Integration Guide

Written for: the AI/developer building the React Native (Expo) mobile app for the Distribution Management SaaS.
Everything the app needs — authentication, tenant selection, every endpoint with payloads, push notifications,
offline sync rules and worked examples — is in this file. The backend is **live** and seeded with demo data.
**Document version: 10 Oct 2026 (rev 10)** — this is the single source of truth; §14 lists what changed since rev 1.

| Item | Value |
| --- | --- |
| Base URL (production) | `https://dms.invoiceflow.pk` |
| Tenant/staff API | `https://dms.invoiceflow.pk/api/v1/…` |
| External / rep API | `https://dms.invoiceflow.pk/ext/v1/…` |
| Interactive docs (Swagger) | `https://dms.invoiceflow.pk/api/docs/` |
| OpenAPI 3 schema (generate a client from it) | `https://dms.invoiceflow.pk/api/schema/` (needs a Bearer token) |
| Content type | `application/json` (multipart only for file uploads) |
| Auth | `Authorization: Bearer <access JWT>` + `X-Tenant-ID: <tenant uuid>` |

## 0. Demo logins (already seeded)

| Role | Identifier | Password | What they see |
| --- | --- | --- | --- |
| Company **Owner** | `owner@demo.pk` | `owner123` | Everything in the "Demo Distributors" tenant |
| Sales coordinator (staff) | `sales@demo.pk` | `staff123` | Orders inbox, customers, trips |
| Accountant (staff) | `accounts@demo.pk` | `staff123` | Payments, receivables, reports |
| Warehouse keeper (staff, scoped to MAIN warehouse) | `store@demo.pk` | `staff123` | Stock, GRN, pick lists, counts |
| Cashier (staff) | `cashier@demo.pk` | `staff123` | Receive payments, settle reps |
| **Driver** (has login) | `driver@demo.pk` | `driver123` | His trips + deliveries |
| **Manufacturer rep** (external) | `rep1@colormax.pk` / `rep2@colormax.pk` | `rep123` | Own customers, place orders, collections (ColorMax catalog) |
| **Keune rep** (external) | `rep1@keune.pk` | `rep123` | Same, but sees the real **Keune Pakistan** catalog (495 products imported from the Sept-2026 price list) |
| Platform admin (SaaS owner, web only) | `admin` | `pak1stan` | Plans, tenants, billing |

Demo data: 1 supplier (ColorMax) with 2 reps, 30 products with EAN-13 barcodes (`6291041500001` … `6291041500020`,
developers `…101-105`, hair-care `…201-205`), box/carton packaging, 20 customers, 12 sales orders, 2 trips (one
closed, one out for delivery), invoices, receipts, a rep collection + settlement, an expense and a wastage adjustment.
**Plus the real Keune Pakistan catalogue** (supplier `Keune Pakistan`, brand `Keune`, 495 products in 48 categories, no
barcodes/images yet, price list dated 2026-09-15) with its own rep login `rep1@keune.pk`, and a draft purchase order
**PO-00002** "Lahore Distribution Order" (229 lines) from Keune Pakistan — approve/send it from the web to test GRN receiving.

All of the above lives in tenant **Demo Distributors** (`tenant_id 01a0c76b-94c6-7f20-a779-d7f4ba615ce5`, slug `demo`).
A second, **real** tenant (`Trafco`, slug `trafco`) exists with the same Keune catalogue and its own PO — it belongs to a
customer: never create test data there, and never hard-code a tenant id (use `memberships[]` from the login response, §2.2).

Re-seed anytime on the server: `cd /var/www/dms/backend && .venv/bin/python manage.py seed_demo --reset`.

---

## 1. Which app screens for which user

One mobile app, three "modes", chosen from the login response (`user.user_type` + `/me`):

| Mode | Who | Decide by | Home screen |
| --- | --- | --- | --- |
| **Rep** | Manufacturer sales reps | `user.user_type == "EXTERNAL"` and `/me.supplier_rep != null` | My customers, place order, my orders, collections, target |
| **Driver** | Company drivers | `/me.permissions` contains `delivery.complete_delivery` and the user has a Driver record (trips list returns only theirs) | Today's trip, stops, mark delivered (POD) |
| **Staff / Owner** | Coordinator, cashier, warehouse, accountant, owner | `user.user_type == "TENANT"` | Dashboard, orders inbox, stock lookup (scan), receipts, approvals, notifications |

Show/hide every button from `/me.permissions` (list of codenames like `sales.confirm_sales_order`) — the backend
enforces the same codenames, so a hidden button is never a security decision, only UX.

---

## 2. Authentication

### 2.1 Login

`POST /api/v1/auth/login` — no auth header. Identifier may be **email, phone (E.164) or username**.

```json
{ "identifier": "rep1@colormax.pk", "password": "rep123", "device_name": "Pixel 7", "platform": "android" }
```

Response `200`:

```json
{
  "access": "<JWT, 15 min>",
  "refresh": "<JWT, 14 days, rotating>",
  "user": { "id": "uuid", "email": "...", "phone": null, "full_name": "Imran Rep", "user_type": "EXTERNAL",
            "is_platform_admin": false, "locale": "en", "timezone": "Asia/Karachi", "avatar": null,
            "mfa_enabled": false, "must_change_password": false, "last_login": "..." },
  "memberships": [ { "id": "uuid", "tenant_id": "uuid", "tenant_name": "Demo Distributors", "tenant_slug": "demo",
                     "tenant_status": "trial", "is_owner": false, "status": "active", "roles": ["Manufacturer Rep"] } ],
  "must_change_password": false
}
```

Errors: `400` validation (`errors.identifier` / `errors.password`), `423` account locked (10 failed attempts →
15 min lock), `403` inactive. Throttle: 20/min per IP.

### 2.2 Tenant selection

* `memberships` lists every company the user belongs to (reps often belong to several distributors).
* If exactly one → use it silently. If several → show a picker.
* Send `X-Tenant-ID: <tenant_id>` on **every** request afterwards (both `/api/v1` and `/ext/v1`).
  Missing header with multiple memberships ⇒ endpoints that need a tenant return 403.
* `tenant_status` `suspended` ⇒ show a billing notice; the API returns 403 on most calls.

### 2.3 Tokens, refresh, logout

* Access token: 15 minutes. Refresh token: 14 days, **rotates on every refresh**; the old refresh token is
  blacklisted and **reuse of an old refresh token revokes the whole session family** (`401`, `type: token_reuse`).
  Always persist the *newest* refresh token immediately (secure storage: Keychain / EncryptedSharedPreferences).
* `POST /api/v1/auth/refresh` `{ "refresh": "<token>" }` → `{ "access": "...", "refresh": "..." }`.
* **Grace window (rev 5):** a token that was rotated **less than 60 seconds ago** is still accepted once and answers
  with a fresh pair, so two parallel calls that both refresh — or a retried request — no longer sign the user out.
  Outside that window it is still treated as reuse (`401 token_reuse`) and the family is revoked.
* Still refresh **once per app**, not per request: serialise refreshes through a single mutex and let the other
  in-flight calls await it. Refresh *before* a call when the access token expires in < 45 s; that avoids a burst of
  401s when a screen loads several endpoints at once.
* On a `401` from an API call: refresh once, retry the request. Only sign the user out when the refresh endpoint
  itself answers `401`/`403` — on `429` (throttled), `5xx` or no network, keep the session and surface a retry.
* On a `403` whose `type` is `impersonation_expired` or `tenant_not_member`, the **tenant is stale, not the login**:
  drop the stored `tenant_id`, re-run `/me` and send the user back to company selection.
* `POST /api/v1/auth/logout` `{ "refresh": "<token>" }` (Bearer required) → `204`.
* Sessions: `GET /api/v1/me/sessions`, `POST /api/v1/me/sessions/{id}/revoke`, `POST /api/v1/me/sessions/revoke-all`.
* Role changes / password changes revoke all sessions server-side → expect a `401` and re-login.

### 2.4 First login & password reset (OTP)

* `must_change_password: true` ⇒ force `POST /api/v1/auth/password/change` `{ "current_password": "", "new_password": "..." }`
  (current password may be blank when the flag is set). Then re-login.
* Forgot password: `POST /api/v1/auth/otp/request` `{ "identifier": "...", "purpose": "password_reset", "channel": "sms" | "email" }`
  (always 200) → user gets a 6-digit code → `POST /api/v1/auth/password/reset`
  `{ "identifier": "...", "purpose": "password_reset", "code": "123456", "new_password": "..." }`.
* Invitation acceptance (new rep/staff invited by the company): same as reset with `"purpose": "invite"`.
  Invite links look like `https://dms.invoiceflow.pk/accept-invite?identifier=…&code=…` — register the deep link.

### 2.5 `/me` — the app bootstrap call

`GET /api/v1/me` (Bearer + X-Tenant-ID) returns everything needed to configure the UI:

```json
{
  "user": { ...same as login... },
  "memberships": [ ... ],
  "tenant": { "id": "...", "slug": "demo", "display_name": "Demo Distributors", "status": "trial",
              "base_currency": "PKR", "timezone": "Asia/Karachi", "logo": null,
              "settings": { "credit_policy": "warn", "two_step_dispatch": false, ... }, "branding": {},
              "plan": { "code": "growth", "name": "Growth", "status": "trial", "end_date": "2026-10-06", "days_left": 14 } },
  "permissions": ["external.submit_order", "external.view_assigned_customers", ...],
  "pages": { "sales_orders": { "view": true, "add": true, "edit": false, "delete": false,
                               "extras": { "confirm": true, "hold": false, "override_price": false,
                                           "override_discount": false, "approve_credit_hold": false } },
             "delivery_notes": { "view": true, "extras": { "complete": true, "returns": true } }, ... },
  "features": { "barcode": true, "external_api": true, "rep_portal": true, "branding": false, ... },
  "limits": { "max_users": 15, ... },
  "is_owner": false,
  "impersonating": false,
  "platform_permissions": [],
  "warehouses": ["<warehouse uuid>"],
  "supplier_rep": { "id": "...", "supplier_id": "...", "supplier_name": "ColorMax Cosmetics (Pvt) Ltd", "code": "REP-01", "territory": "Lahore North" }
}
```

#### `pages` — gate the UI on this, not on codenames (rev 6)

Staff permissions are set **per page** in the web app: four switches (**view · add · edit · delete**) plus the
extras that only make sense there. `/me` answers them resolved for the signed-in user, so the app asks a question
it can trust:

```ts
const can = (page: string, action: "view" | "add" | "edit" | "delete") => Boolean(me.pages?.[page]?.[action]);
const canExtra = (page: string, extra: string) => Boolean(me.pages?.[page]?.extras?.[extra]);

can("sales_orders", "add")            // show "New order"
canExtra("sales_orders", "confirm")   // show Confirm / Hold
canExtra("delivery_notes", "complete") // driver may record a delivery
```

Rules the server enforces identically — mirror them and you will never show a button that answers `403`:

* **`view` is the gate.** Without it the screen must not open at all; the endpoints behind it answer `403`.
* **`add` / `edit` / `delete`** decide the buttons. Delete does what the document allows (an order is cancelled, a
  product archived, a user suspended).
* A few actions are deliberately separate: giving a user roles or warehouse access needs
  `canExtra("users", "manage_access")`, and changing a dispatch's driver/vehicle/crew needs
  `canExtra("trips", "assign_driver")` — being allowed to edit the record is not enough.
* A document's **attachments and history follow that document**: listing files needs the owning page's `view`,
  uploading or deleting them needs its `edit`. `GET /api/v1/attachments` requires `?model=SalesOrder&object_id=…`.

Page keys the mobile app is likely to need: `sales_orders`, `sales_inbox`, `invoices`, `credit_notes`,
`rep_collections`, `customers`, `products`, `stock`, `movements`, `batches`, `load_forms`, `trips`,
`delivery_notes`, `grns`, `transfers`, `adjustments`, `counts`, `payments`, `expenses`, `reports`.
`GET /api/v1/permission-pages` returns the whole catalogue (groups → pages → switches) if you want to render it.

`permissions` is still there and still the raw codename list — rep (`external.*`) screens keep using it, since a
manufacturer rep's access is not page-shaped.

`warehouses` non-empty ⇒ the user is restricted to those warehouses (warehouse keeper). `supplier_rep` non-null ⇒ rep mode.
`PATCH /api/v1/me` updates `full_name`, `locale`, `timezone`, `avatar` (multipart).

---

### 2.6 Log in by QR code (no password) — see `mobile-app-qr-login.md`

The web app's profile menu has **Log in on phone (QR code)**: a one-time code (5 min). The app scans it (login screen →
**Scan QR to log in**) or receives it as a link (`dms://login?t=…` / `https://dms.invoiceflow.pk/app-login?t=…`) and calls
`POST /api/v1/auth/qr-login/redeem { token: <scanned string>, device_name, platform }` → the same payload as `/auth/login`
plus `tenant_id` (preselect that company) and `login_method: "qr"`. Errors: `410 type=used|expired`, `400 type=invalid`.
Full spec, deep-link config, App Links and test steps: **`mobile-app-qr-login.md`**.

## 3. Conventions (apply to every endpoint)

| Topic | Rule |
| --- | --- |
| IDs | UUID strings. Documents also have a human `number` (`SO-00013`, `INV-00005`, `TRIP-00002`). |
| Money | Strings with 4 decimals (`"1250.0000"`); display with 2. Quantities are strings with 3 decimals, **always in base units** (pieces); packaging (`box`, `carton`) is a convenience with `qty_in_base`. |
| Dates | `YYYY-MM-DD`; timestamps ISO-8601 UTC (`2026-09-22T04:45:00.026Z`). Show in `tenant.timezone`. |
| Lists | Page-number pagination: `?page=1&page_size=25` (max 500) → `{ count, page, pages, page_size, next, previous, results }`. Stock movements use cursor pagination → `{ next, previous, results }` (follow `next`). |
| Filters | `?search=` (text), `?ordering=field` / `-field`, plus the filter params listed per endpoint; `?date_from=&date_to=` on dated documents. |
| Trailing slash | Optional — `/api/v1/sales-orders` and `/api/v1/sales-orders/` both work. |
| Errors | RFC 7807 style, always JSON: `{ "type": "validation_error", "title": "...", "status": 400, "detail": "...", "errors": { "field": ["msg"] } }`. Show `detail`; show `errors.<field>` under inputs. |
| Business rules | `422` with `type: "domain_error"` (e.g. "Insufficient stock…", "Customer is blocked"). `type` may be a specific code: `insufficient_stock`, `credit_hold`, `period_closed`, `plan_limit`, `feature_disabled`, `token_reuse`, `locked`. |
| Conflicts | Documents carry `version`. PATCH must send the `version` you loaded; `409` ⇒ reload and retry. |
| Idempotency | Send `Idempotency-Key: <uuid>` on POSTs that create orders, collections, payments, GRNs, adjustments. A retry with the same key returns the original response with header `Idempotent-Replayed: true`. **Required for offline queues.** |
| Throttling | `429` on auth (20/min), OTP (5/min), `/ext/v1` (60/min per user/key). Back off with `Retry-After`. |
| Permissions | `403` with the missing codename in `detail`, e.g. `Missing permission: sales.confirm_sales_order`. |
| Files | Multipart uploads; downloads (PDF) are `application/pdf` at `…/pdf` endpoints — open with the Bearer header (use a download-with-headers helper, not a plain WebView URL). |
| Sync | Lists accept `?updated_after=<ISO>` (`/api/v1/sales-orders`) and `?since=<ISO>` (`/ext/v1/*`) for incremental refresh. |

---

## 4. Push notifications (FCM)

1. After login (and whenever the FCM token changes): `POST /api/v1/me/devices`
   `{ "platform": "android" | "ios", "token": "<fcm token>", "device_name": "Pixel 7", "app_version": "1.0.0" }` (upsert by token).
   Delete on logout: `DELETE /api/v1/me/devices/{id}`.
2. Preferences: `GET/PUT /api/v1/me/notification-preferences` — array of `{ type, label, push, email, sms }`.
3. In-app inbox: `GET /api/v1/me/notifications?unread=1`, `GET /api/v1/me/notifications/unread-count` (poll every 30 s
   when the app is foregrounded — there is no WebSocket yet), `POST /api/v1/me/notifications/{id}/read`, `POST …/read-all`.
4. Push `data` payload keys: `type`, `notification_id`, plus one of `order_id`, `trip_id`, `adjustment_id`, `expense_id`, and
   `screen` (`order` | `trip`). Deep-link: `order_id` → order detail, `trip_id` → trip detail.

Notification `type` values: `order.submitted` (staff), `order.confirmed`, `order.rejected`, `order.dispatched`,
`delivery.assigned` (driver + rep), `delivery.completed`, `stock.low`, `stock.expiry`, `payment.received`,
`rep.settlement_due` (owner), `approval.pending` (approvers), `platform.announcement`, `subscription.reminder`.

`GET /api/v1/announcements` → platform banners to show on the dashboard.

---

## 5. Rep app (`/ext/v1`) — manufacturer sales reps

All `/ext/v1` calls: Bearer + `X-Tenant-ID`. The rep's **external role** (set by the distributor's owner) decides
visibility; check `/me.permissions` (or `GET /ext/v1/me` → `permissions`):

| Permission | Enables |
| --- | --- |
| `external.view_assigned_customers` | Customer list |
| `external.view_customer_outstanding` | `outstanding`, `overdue`, `credit_limit` fields on customers |
| `external.add_customer_request` | "Add customer" (created as `pending` if the company requires approval) |
| `external.submit_order` / `external.cancel_own_order` / `external.view_own_orders` | Ordering |
| `external.view_prices` | `price` on products |
| `external.view_stock_exact` / `external.view_stock_bands` | `/ext/v1/stock` exact qty or `low/medium/high/out` bands |
| `external.view_delivery_status` | Deliveries screen |
| `external.record_collection` / `external.view_own_collections` | Collections |
| `external.view_targets` | Target widget (`/api/v1/dashboard` → `rep`) |

### 5.1 Endpoints

| Method & path | Purpose | Notes |
| --- | --- | --- |
| `GET /ext/v1/me` | Rep context | `{ tenant, supplier, rep{code,name,target}, permissions, auth }` |
| `GET /ext/v1/customers?search=&territory=&since=` | My customers | see fields below |
| `POST /ext/v1/customers` | Request a new customer. **`gps` is required** while `geofence_require_location` is on (the shipped default) — a shop with no pin never shows on a map and its orders are refused, so the app should keep Send disabled until there is a fix rather than let the rep meet a `403 location_required`. `type` must be one of `retailer`, `salon`, `wholesaler`, `other` (sent on `my-areas.customer_types`). Send `Idempotency-Key`: a replay answers `200` with the original row. `GET /ext/v1/customers/similar?name=&phone=` first to catch a shop that already exists | `{ name, type, phone, address, city, contact_person, gps:{lat,lng} }` → `{ id, code, name, status, type }` |
| `GET /ext/v1/products?search=&customer=<id>&since=` | Catalogue with packaging + barcodes (+ `price` for that customer's price list) | cache locally |
| `GET /ext/v1/stock?warehouse=` | Availability (exact or bands) | |
| `POST /ext/v1/orders` | Place order | payload below; send `Idempotency-Key` |
| `GET /ext/v1/orders?since=&status=submitted,confirmed&customer=&external_ref=&limit=` | My orders | |
| `GET /ext/v1/orders/{id or number}` | Order detail with lines, trip, driver, invoices | |
| `POST /ext/v1/orders/{id}/cancel` `{ reason }` | Cancel | only while `status == "submitted"` |
| `GET /ext/v1/deliveries?order=&since=` | Delivery status per order (driver, vehicle, delivered_at, receiver) | |
| `GET /ext/v1/collections?status=held_by_rep` | My collections + `held_total` | |
| `POST /ext/v1/collections` | Record cash collected from a customer | payload below |
| `GET /api/v1/dashboard` | `rep` block: orders this month, value, target, cash_held, customers, pending | |
| `GET /api/v1/customers/{id}/statement` | Customer statement (ledger + ageing + open invoices) | needs `external.view_customer_outstanding` |
| `GET /api/v1/invoices?customer=&open=1` | Open invoices of a customer (to allocate a collection) | |

Customer row (verified 10 Oct 2026): `{ id, code, name, type, phone, address, city, gps:{lat,lng}|null, territory, route, status, is_blocked, credit_limit, credit_days, price_list, outstanding, overdue, updated_at }`. `phone` is the number to call/SMS. The legal and alternate-number
fields (`business_name`, `alt_mobile`, `landline`, `ntn`, `strn`, `cnic`, `filer_status`) are **not** sent to reps — they
are on the staff API. The customer's standing discount is applied by the server to order lines that carry no discount of
their own, so the app does not send it (`apply_customer_discount: false` on an order skips it).

Product row (from `ProductVariant`): `{ id, sku, name, product_name, brand_name, category_name, base_uom, unit_size, packing, location_name,
sale_price?, retail_price?, price?, price_list?, is_batch_tracked, packagings:[{ id, name:"carton", qty_in_base:"24.000", barcode }],
barcodes:[{ code, type, is_primary, packaging }], image }`.

Price-list fields (Pakistani distribution terms, as on the supplier's "Packing & Price List"):
`sku` = the supplier **Code**; `unit_size` = **UNIT** (`"60ml"`, `"1L"`, `"pcs"`); `packing` = units per carton; `price` = **T.P** (trade
price the rep sells at — already resolved for the customer's price list, use this for the cart); `retail_price` = **R.P** (retail / MRP,
show it for reference); D.P (distributor cost) is never sent to reps. Reps only see products of their own supplier (plus products with no supplier).

### 5.2 Place an order

```json
POST /ext/v1/orders            Idempotency-Key: 0f3c…
{
  "external_ref": "APP-2026-09-22-0007",        // optional; unique per supplier → duplicates return the same order
  "customer": { "id": "uuid" },                 // or {"code": "C-00042"} or {"phone": "+92300…", "name": "…", "address": "…"} (creates)
  "requested_delivery_date": "2026-09-24",
  "notes": "deliver before 2pm",
  "lines": [
    { "barcode": "6291041500001", "qty": 2, "packaging": "box" },      // scanned carton/box barcode → 2 boxes = 24 pcs
    { "sku": "SC-1", "qty": 6 },                                        // base units
    { "variant_id": "uuid", "qty": 12, "unit_price": "300.00", "discount_pct": 2.5 }   // requested price is advisory
  ],
  "attachments": ["data:image/jpeg;base64,…"]   // optional photos of the shop order
}
```

Response `201` (or `200` when `external_ref` already existed) = full order:

```json
{ "id": "…", "number": "SO-00013", "status": "submitted", "customer_name": "…", "order_date": "2026-09-22",
  "subtotal": "12000.0000", "discount": "0.0000", "scheme_discount": "0.0000", "tax1_total": "2041.2000", "tax2_total": "0.0000",
  "total": "13834.8000", "price_flags": [ { "variant": "CM-1.0-60", "requested": "300.0000", "list": "310.0000" } ],
  "lines": [ { "id": "…", "variant_sku": "CM-1.0-60", "variant_name": "ColorMax 1.0 Black 60ml", "packaging_name": "box",
               "qty_ordered": "24.000", "free_qty": "2.000", "unit_price": "310.0000", "discount_amount": "0.0000",
               "scheme_discount": "0.0000", "scheme_names": ["Buy 12 get 1 free (ColorMax cream)"],
               "tax1_name": "Sales Tax", "tax1_rate": "18.000", "tax1_amount": "1339.2000", "tax2_amount": "0.0000",
               "line_total": "8779.2000", "qty_delivered": "0.000" } ],
  "invoices": [], "delivery_notes": [] }
```

Prices: the server prices every line from the customer's price list, applies quantity slabs and schemes
(`free_qty`, `scheme_discount`) and computes up to two taxes per line. A requested `unit_price` is accepted, replaced
or flagged (`price_flags`) depending on the company setting. Show the returned totals, never your own.

Order `status` lifecycle: `submitted → under_review → confirmed → dispatched → partially_delivered → delivered → invoiced`;
side states `on_hold_credit`, `on_hold_stock`, `rejected` (`rejection_reason`), `cancelled`.

### 5.3 Record a collection (cash received from a customer by the rep)

```json
POST /ext/v1/collections        Idempotency-Key: …
{ "customer": "uuid-or-code", "amount": "25000", "method": "cash" | "cheque" | "online", "reference": "CHQ 1122",
  "allocations": [ { "invoice": "uuid", "amount": "20000" }, { "invoice": "uuid", "amount": "5000" } ],   // optional → FIFO by default
  "notes": "", "gps": { "lat": 31.52, "lng": 74.35 } }
```

→ `201` `{ number: "RC-00003", status: "held_by_rep", allocations: [{invoice, number, amount}], … }`. The money sits in
"cash with rep" until company staff record a settlement (status becomes `settled`, `settlement_number` filled).
Show the rep their `held_total` prominently — the owner is alerted when it is older than N days.

---

## 6. Driver app (`/api/v1`) — trips and proof of delivery

Drivers log in with their company user. They only see trips assigned to their Driver record (unless they hold
`delivery.view_all_trips`).

| Method & path | Purpose |
| --- | --- |
| `GET /api/v1/trips?active=1` (or `?date=YYYY-MM-DD`, `?status=out_for_delivery`) | My trips (list rows: number, date, status, stops_count, delivered_count, vehicle_reg, cash_collected) |
| `GET /api/v1/trips/{id}` | Trip with `delivery_notes[]` (stops in `sequence` order) each with customer name/phone/address/`customer_gps`, order number/total, `lines[]`, `status`, `invoice_number`, `signature_url`, `photo_url` |
| `GET /api/v1/delivery-notes?trip=<id>&status=loaded` | Stops still to do |
| `GET /api/v1/delivery-notes/{id}` | One stop |
| `POST /api/v1/delivery-notes/{id}/deliver` | **Mark delivered / partial / failed** (below) |
| `GET /api/v1/delivery-notes/{id}/pdf` | Challan PDF |
| `GET /api/v1/trips/{id}/reconciliation` | loaded / delivered / returned / cash summary |
| `GET /api/v1/trips/{id}/dispatch-sheet` | Dispatch sheet PDF (the driver's sheet) |
| `GET /api/v1/trips/{id}/trip-sheet` | Same PDF under its old name |

Trip `status`: `planned → loading → out_for_delivery → completed → closed` (`cancelled`). Stops (`DeliveryNote.status`):
`pending`, `loaded` (= issued by the warehouse), `dispatched` (= on the vehicle, out for delivery), `delivered`,
`partially_delivered`, `failed`, `returned`. Treat `loaded` and `dispatched` the same way in the driver's "to do"
list (`?status=loaded` **and** `?status=dispatched`). When the trip starts stock has already left the warehouse;
the driver only reports what happened at each stop. Each stop also carries `picklist_number` (the load form it was
issued on) and `remarks`.

### 6.1 Deliver a stop (proof of delivery)

`POST /api/v1/delivery-notes/{id}/deliver` — **multipart/form-data** when sending files, JSON otherwise.

| Field | Type | Notes |
| --- | --- | --- |
| `delivered` | JSON string (multipart) or array (JSON) | `[{ "line_id": "…", "qty_delivered": "24", "qty_returned": "0", "return_reason": "rejected|damaged|shortage|not_ordered|other", "return_condition": "good|damaged" }]`. Omit ⇒ everything loaded is delivered. |
| `receiver_name` | string | Person who received |
| `signature` | file (PNG/JPG) | Signature capture image |
| `photo` | file | Photo of goods / shop |
| `gps` | JSON `{ "lat": 31.52, "lng": 74.35 }` (or `gps_lat`, `gps_lng` fields) | Device location at delivery |
| `cash_collected` | decimal string | Cash taken at the door → creates a receipt allocated to this stop's invoice, counted on the trip |
| `delivered_at` | ISO datetime | Optional (offline capture time) |
| `remarks` | string | What the customer or driver said (shown on the note and the order chain) |
| `failed` | `true` | Stop failed (shop closed etc.) → everything returns; also send `failure_reason` |

Response = the updated delivery note (`status`, `invoice_number`, `signature_url`, `photo_url`, `remarks`). A stop
that is not invoiced yet creates its invoice on delivery (one invoice per delivery note); when the office already
invoiced what the warehouse issued, that invoice stays and short/returned quantities are settled with a credit note.
Errors: `422` when delivered + returned > loaded.

Offline: queue the multipart request with an `Idempotency-Key`; store the signature file locally until sent.

---

## 7. Staff / owner app (`/api/v1`)

### 7.1 Dashboard & lookups

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/dashboard` | KPIs, permission-filtered: `orders{today,inbox,confirmed,dispatched,by_status}`, `sales{month,last_month,change_pct,today}`, `sales_trend[]`, `trips{out,planned,to_close}`, `inventory{low_stock,expiring,expired,negative}`, `cash{in_hand,with_reps,receivables,receivables_overdue,payables,collected_today}`, `top_customers[]`, `rep{…}` |
| `GET /api/v1/inventory/stock/lookup?code=<scanned>` | **Scan-to-stock**: variant + per-warehouse/batch levels + `total_on_hand` |
| `GET /api/v1/barcodes/resolve?code=<scanned>` | Scan-to-product: `{ variant, packaging, batch, batch_no, expiry_date, qty_per_scan, unit, item_code, packing, location, location_name, warehouse, warehouse_name }`. `unit` is `"box"` or `"unit"` and `qty_per_scan` is how many base units this one scan means — a unit UPC gives 1, a box UPC or the **item code** printed on the carton gives `packing`. A composite box label (`16418.01#195654` — item code before the dot, batch after it) comes back with `batch_no` filled and `expiry_date` null. A **14-digit carton code** (GTIN-14/ITF-14 — the unit's EAN-13 behind a packaging indicator digit) resolves to the same product with `unit: "box"`, so the barcode on a shipping carton counts a whole carton with nothing registered for it; indicator `0` is the same single article. **The check digit is not used for matching** — it exists so a scanner can catch its own misreads, and a catalogue typed in from a price list does not always agree (8 of 429 live codes do not check out, some stored as twelve digits with no check digit at all), so the article is matched on its digits and the last one is ignored. A 12-digit UPC-A and the 13-digit EAN of one article find each other either way round. The whole code is tried as an item code before any splitting, so a six- to eight-digit item code is never cut after the fifth digit. `location`/`warehouse` are where the product is normally kept. Unknown code ⇒ `404` |
| `GET /ext/v1/my-areas` | **Working areas** this rep covers: `{policy, location_required, areas:[{id,name,type,colour,centre:{lat,lng},radius_m,points:[{lat,lng}],area_km2}]}`. `type` is `circle` (centre + `radius_m`) or `polygon` (`points`). Draw them on the map and check a fix against them offline — same rule as the server: inside a circle = distance ≤ radius; inside a polygon = ray casting |
| `GET /api/v1/variants?search=&with_stock=1` | Product search (`stock_on_hand`) |
| `GET /api/v1/variants/{id}/price?customer=&packaging=&qty=` | Price preview with taxes and schemes |
| `GET /api/v1/inventory/stock?warehouse=&variant=&low_stock=1&search=` | Stock levels |
| `GET /api/v1/inventory/stock/summary?warehouse=` | In stock / dispatched / delivered per item |
| `GET /api/v1/inventory/stock/fefo?variant=&warehouse=&qty=` | Batch suggestion (first-expiry-first-out) |
| `GET /api/v1/inventory/alerts?type=low_stock|expiring|expired|negative|dead_stock` , `…/alerts/counts` | Alerts |
| `GET /api/v1/customers?search=&territory=&route=&status=` , `GET /api/v1/customers/{id}` , `…/ledger`, `…/statement`, `…/orders`, `…/credit-check?amount=` | Customers |
| `POST /api/v1/customers` | Create (`code` auto if omitted). Fields as in §5.1 plus `credit_limit`, `credit_days`, `price_list`, `territory`, `route`, `assigned_reps[]`, `tax_class_override` |

### 7.2 Orders (coordinator)

| Endpoint | Purpose / payload |
| --- | --- |
| `GET /api/v1/sales-orders/inbox` | Rep/API orders waiting: `submitted`, `under_review`, `on_hold_credit`, `on_hold_stock` |
| `GET /api/v1/sales-orders?status=&customer=&rep=&open=1&date_from=&updated_after=` | All orders (list rows) |
| `GET /api/v1/sales-orders/stats` | Counts by status, today, inbox, dispatched |
| `GET /api/v1/sales-orders/{id}` | Detail (lines, trip/driver/vehicle, invoices, delivery notes, `price_flags`, `hold_reason`) |
| `POST /api/v1/sales-orders` | Staff order: `{ customer, warehouse?, rep?, supplier?, order_date?, requested_delivery_date?, notes?, lines:[{ variant, packaging?, qty, unit_price?, discount_pct?, price_per_packaging? }] }` + `?confirm` body flag `"confirm": true` to confirm in one step |
| `PATCH /api/v1/sales-orders/{id}` | Edit while editable: `{ version, notes, lines:[…] }` (lines replace all) |
| `POST …/{id}/review` | Mark under review |
| `POST …/{id}/confirm` `{ "override_credit": false, "allow_partial": false }` | Credit + stock check, reserve stock. Returns the order; if it lands `on_hold_credit`/`on_hold_stock` read `hold_reason`. `override_credit` needs `sales.approve_credit_hold` |
| `GET …/{id}/availability` | Per-line available/short |
| `POST …/{id}/allocate` `{ allocations:[{ line_id, qty, batch? }] }` | Manual allocation |
| `POST …/{id}/hold` `{ reason }` / `…/reject` `{ reason }` / `…/cancel` `{ reason }` | |
| `POST …/{id}/mark-delivered` `{ receiver_name?, cash_collected?, lines?:[{ order_line, qty, batch? }] }` | Deliver without a trip (counter sale): stock out + delivery note + invoice in one step. Needs `delivery.complete_delivery` |
| `GET …/{id}/pdf` | Order PDF |

### 7.3 Order → delivery chain (load forms, warehouse issue, dispatch)

The whole sequence is **Sales order → Load form → Warehouse issue → Invoice → Dispatch (vehicle, driver, helper) →
Out for delivery → Delivery confirmation**. `GET /api/v1/sales-orders/{id}/workflow` returns it ready to render:

```json
{ "steps": [ { "key": "issue", "title": "Warehouse issue", "state": "current",
               "at": null, "by": "", "detail": "Sent to warehouse",
               "docs": [ { "label": "DN-00017", "to": "/app/delivery/notes/…" } ],
               "action": { "label": "Issue stock", "post": "/api/v1/pick-lists/…/issue", "perm": "delivery.create_delivery" } } ] }
```

`state` is `done | current | todo | blocked | skipped`; `action` is either `post` (call it) or `to` (open a screen),
and `perm` is the permission the user needs. Keys in order: `order, load_form, issue, invoice, dispatch,
out_for_delivery, delivered`.

| Endpoint | Purpose / payload |
| --- | --- |
| `GET /api/v1/vehicles` , `GET /api/v1/drivers` | Registry (needed for the dispatch form) |
| `POST /api/v1/sales-orders/{id}/load-form` `{ send?: true, notes? }` | **Load form for one confirmed order**, handed to its warehouse (`422 duplicate` if one is already open) |
| `POST /api/v1/pick-lists` `{ warehouse, orders:[ids], notes }` | Load form across several confirmed orders (FEFO batches) |
| `POST /api/v1/pick-lists/{id}/send` | Hand it to the warehouse (`open → sent`, notifies warehouse staff) |
| `POST /api/v1/pick-lists/{id}/issue` `{ issues:[{ line_id, qty_issued, batch? }], allow_short?: true }` | **Warehouse issue**: records what went out, **reduces stock**, opens a delivery note per order, order → `issued`. Short quantities ⇒ form `partially_issued`, the rest stays on the order |
| `POST /api/v1/pick-lists/{id}/scans` `{ scans:[{ code, qty }] }` | Scanner mode — batch of scans |
| `POST /api/v1/pick-lists/{id}/picks` `{ picks:[{ line_id, qty_picked, batch?, scanned? }] }` , `POST …/complete` | Pick quantities without issuing (old flow) |
| `POST /api/v1/sales-orders/{id}/invoice` | Invoice what the warehouse issued, before the vehicle leaves (one per delivery note) |
| `POST /api/v1/trips` `{ warehouse, date, driver, vehicle, helper?, rep?, delivery_rep?, loader?, route?, order_ids:[…], notes }` | **Dispatch**: vehicle + driver + helper; an already-issued delivery note is put on the vehicle instead of issuing again |
| `POST /api/v1/trips/{id}/assign-orders` `{ order_ids }` , `…/remove-order` `{ order_id }` , `…/reorder-stops` `{ note_ids }` | |
| `POST /api/v1/trips/{id}/load` `{ loaded:[{ line_id, qty_loaded, batch? }] }` | Loading step (optional) |
| `POST /api/v1/trips/{id}/start` `{ odometer_start }` | **Dispatch = stock out**; orders → `dispatched`; driver gets a push |
| `POST /api/v1/trips/{id}/close` `{ odometer_end, fuel_amount, expense_amount, override:false, damage_reason? }` | Returns go back to stock, damaged → adjustment, cash reconciled. `422` with `errors.reconciliation` / `errors.pending` if it doesn't reconcile (retry with `override: true` if the user has `inventory.approve_adjustment`) |
| `POST /api/v1/trips/{id}/cancel` `{ reason }` | Before start only |

### 7.4 Warehouse scanning screens

| Screen | Endpoints |
| --- | --- |
| Goods receipt (GRN) | **Pick the order:** `GET /api/v1/purchase-orders?open=1&search=` — the list is slim (`number, reference, supplier_name, warehouse, warehouse_name, date, status, lines_count, grns_count, units_due`) and **carries no `lines[]`**; read `GET /api/v1/purchase-orders/{id}` for the lines (`id, variant, variant_sku, item_code, upc, unit_size, packing, qty_ordered, qty_received, qty_pending, closed, default_bin, default_bin_name, unit_price`). **Receive:** `POST /api/v1/goods-receipts` `{ po, warehouse, date, supplier_challan_no, notes, lines:[{ po_line?, variant, qty_accepted, qty_damaged, qty_short, reject_reason, note, close_line?, batch_no?, expiry_date?, bin?, unit_cost? }], landed_costs:[{type,amount,allocation}], "post": true }`. **Or scan:** create an empty draft (`lines: []`) then `POST …/{id}/scans` `{ scans:[{ code, qty?, batch_no?, expiry_date? }] }` repeatedly and `POST …/{id}/post`. Each scan answers with the receipt plus `scanned:[{ name, item_code, counted, unit, batch_no, expiry_date, needs_expiry, po_line }]`; a box counts `packing` units, a loose scan with no batch joins the row already open for that item, and an item that is not on the order is refused with `400 not_on_order`. The **supplier is taken from the order** (send it only for a receipt without one; neither ⇒ `400 po_required`). **A batch is optional; an expiry date is not, for a product that is expiry-tracked** — posting is refused with `400 expiry_required` naming the item, because picking is first-expiry-first-out and the near-expiry alerts read that date. A batch-tracked *or* expiry-tracked product received with an empty batch takes the supplier challan number (or the receipt number), so the date has something to hang on. `my-areas.visit` is unrelated; the flag to read per line is `is_expiry_tracked` on the order line, and each scan answers with `needs_expiry`. A draft saves without it — only posting insists — so a receipt can be left open until someone has the carton in hand.

**Receiving twice against one order fills the receipt already open** rather than creating a second, and everything keyed into it adds up until it is posted (`reused: true` on the response says which happened). Correcting what is already on it is a `PATCH` with the full line set; *adding* is another `POST`. Sending one down the other's path either doubles a line or wipes the receipt, so keep them apart. The order must be **approved**: a draft answers `422 po_not_approved`, a cancelled one `422 po_cancelled`. `qty_damaged` goes to the Quarantine warehouse at zero cost; `qty_short` records what never came; `close_line: true` stops that order line waiting for the balance. |
| Stock transfer | `POST /api/v1/inventory/transfers` `{ from_warehouse, to_warehouse, date, lines:[{ variant, batch?, qty_requested }] }` → `…/approve` → `…/dispatch` `{ sent_qtys:{line_id: qty} }` → `…/receive` `{ received_qtys:{…}, lost_reason? }` |
| Stock count | `POST /api/v1/inventory/counts` `{ warehouse, type:"full"|"cycle", scope:{categories:[],bins:[],variants:[]} }` → `POST …/{id}/record` `{ entries:[{ line_id | variant, batch?, bin?, counted_qty, scanned:true }] }` (repeat) → `GET …/{id}/variances` → `POST …/{id}/post` |
| Adjustment / wastage | `POST /api/v1/inventory/adjustments` `{ warehouse, type:"decrease"|"increase", reason:<ReasonCode id>, date, notes, lines:[{ variant, batch?, bin?, qty, note }], "submit": true }`; reasons from `GET /api/v1/settings/reason-codes` (`requires_photo` ⇒ upload an attachment first, then send `photo_confirmed: true`) |
| Approvals | `GET /api/v1/inventory/adjustments?status=pending_approval` → `…/{id}/approve` / `…/reject {reason}`; `GET /api/v1/accounting/expenses?status=submitted` → `…/approve` / `…/reject`; credit holds: `GET /api/v1/sales-orders?status=on_hold_credit` → `…/confirm {override_credit:true}` |
| Labels | `POST /api/v1/labels/print` `{ size:"50x30", items:[{ variant, packaging?, batch?, qty, show_price }] }` → PDF |

### 7.5 Cash & receivables (cashier / accountant)

| Endpoint | Purpose / payload |
| --- | --- |
| `GET /api/v1/accounting/payments/open-documents?partner_type=customer&partner_id=<id>` | Open invoices with balances (for allocation UI) |
| `POST /api/v1/accounting/payments` | Receipt: `{ direction:"in", partner_type:"customer", partner_id, amount, method:"cash"|"bank_transfer"|"cheque"|"online", date, cash_account?, bank_account?, cheque_no?, cheque_date?, cheque_bank?, reference?, allocations:[{ document_type:"sales_invoice", document_id, amount }] }` (no allocations ⇒ FIFO). Supplier payment: `direction:"out", partner_type:"supplier"` with `document_type:"purchase_bill"`. Needs `cash.receive_payment` / `cash.make_payment` |
| `GET /api/v1/accounting/payments?direction=in&date_from=&collected_by=` , `GET …/{id}/print` | Receipts list / voucher PDF |
| `POST …/{id}/cheque/deposited|cleared|bounced` `{ bank_account?, charges? }` | Cheque lifecycle |
| `GET /api/v1/accounting/cash-accounts` , `GET /api/v1/accounting/bank-accounts` | Pick lists for the receipt form |
| `GET /api/v1/rep-collections/unsettled?rep=` | Cash held by each rep + collections |
| `POST /api/v1/rep-settlements` `{ rep, collections:[ids], cash_account?, bank_account?, date, notes }` | Rep hands cash to staff |
| `GET /api/v1/invoices?open=1&customer=&overdue=1` , `GET …/{id}`, `…/pdf`, `…/payments` | Invoices |
| `GET /api/v1/accounting/dashboard` | Cash/bank balances, receivables & payables due, pending cheques |
| `GET /api/v1/reports/ar_ageing` , `…/cash_with_reps` , `…/overdue_list` , `…/collection_report?date_from=&date_to=` | Reports (JSON); add `&format=pdf` for a PDF |

### 7.6 Reports (any role with the permission)

`GET /api/v1/reports` → catalogue `[ { key, title, area, params:[{name,type}], permission } ]`.
`GET /api/v1/reports/{key}?date_from=&date_to=&…&format=json|csv|xlsx|pdf` → `{ key, title, params, columns, rows, totals }`.
Useful on mobile: `sales_by_customer`, `sales_by_rep`, `pending_orders`, `dispatched_not_delivered`, `stock_summary`,
`low_stock`, `expiry_report`, `trip_register`, `driver_performance`, `ar_ageing`, `cash_with_reps`, `overdue_list`.

### 7.7 Attachments & document history (any document)

* Upload: `POST /api/v1/attachments` multipart `{ model:"SalesOrder"|"DeliveryNote"|"StockAdjustment"|"Payment"|…, object_id, kind:"pod_photo"|"receipt"|"cheque"|"document", file }` → `{ id, url, name, size }`.
  List: `GET /api/v1/attachments?model=&object_id=`.
* History tab: `GET /api/v1/history/{Model}/{object_id}` → audit rows `{ action, actor_name, before, after, at }`.

---

## 8. Offline strategy (recommended)

1. **Cache on login**: `/me`, `/ext/v1/customers`, `/ext/v1/products` (or `/api/v1/variants`), warehouses, reason codes,
   drivers/vehicles (staff). Refresh with `?since=` / `?updated_after=` on app resume.
2. **Queue writes** (orders, collections, deliveries, receipts, scans) with a client-generated `Idempotency-Key` per
   item. Replay in order when online; treat `Idempotent-Replayed: true` as success. Keep the local copy until the
   server response arrives, then replace it with the server's numbers/totals.
3. **Conflicts**: `409` (version) ⇒ refetch the document, re-apply the user's change, resend. `422` ⇒ show `detail`
   and let the user fix the item (do not retry blindly).
4. Prices/taxes/schemes are computed server-side; show local estimates as "approx." until synced.

---

## 9. Reference — status values

| Entity | Values |
| --- | --- |
| Sales order | `draft, submitted, under_review, confirmed, on_hold_credit, on_hold_stock, issued, dispatched, partially_delivered, delivered, invoiced, cancelled, rejected` (`issued` = warehouse handed the stock over, not yet on a vehicle) |
| Delivery note | `pending, loaded` (issued), `dispatched` (out for delivery), `delivered, partially_delivered, failed, returned` |
| Load form (pick list) | `open` (pending), `sent` (to warehouse), `picking`, `issued`, `partially_issued`, `cancelled` |
| Trip | `planned, loading, out_for_delivery, completed, closed, cancelled` |
| Invoice | `draft, posted, partially_paid, paid, overdue, cancelled` |
| Rep collection | `held_by_rep, settled, cancelled` |
| Purchase order | `draft, sent, partially_received, received, cancelled` |
| GRN / bill / return | `draft, posted, cancelled` (bill also `partially_paid, paid`) |
| Stock transfer | `requested, approved, dispatched, in_transit, received, cancelled` |
| Stock adjustment | `draft, pending_approval, approved, rejected, cancelled` |
| Stock count | `open, counting, review, posted, cancelled` |
| Payment (cheque_status) | `pending, deposited, cleared, bounced` |
| Customer | `active, pending, blocked, inactive` |
| Tenant | `trial, active, past_due, suspended, cancelled, archived` |

Movement types (`/api/v1/inventory/movements`): `opening, grn, purchase_return, sale_dispatch, sales_return,
transfer_out, transfer_in, adjustment, wastage, count_variance, delivery_return`.

---

## 10. Worked examples (curl — copy into the app's HTTP layer)

```bash
BASE=https://dms.invoiceflow.pk

# 1) Rep logs in
curl -s -X POST $BASE/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"identifier":"rep1@colormax.pk","password":"rep123","platform":"android","device_name":"Pixel 7"}'
# → access, refresh, memberships[0].tenant_id  (TID)

# 2) Bootstrap
curl -s $BASE/api/v1/me -H "Authorization: Bearer $ACCESS" -H "X-Tenant-ID: $TID"

# 3) Register push token
curl -s -X POST $BASE/api/v1/me/devices -H "Authorization: Bearer $ACCESS" -H "X-Tenant-ID: $TID" \
  -H 'Content-Type: application/json' -d '{"platform":"android","token":"<fcm>","device_name":"Pixel 7","app_version":"1.0.0"}'

# 4) Customers + catalogue for offline cache
curl -s "$BASE/ext/v1/customers" -H "Authorization: Bearer $ACCESS" -H "X-Tenant-ID: $TID"
curl -s "$BASE/ext/v1/products?customer=<customer uuid>" -H "Authorization: Bearer $ACCESS" -H "X-Tenant-ID: $TID"

# 5) Scan a barcode while building the cart (optional; the order endpoint resolves barcodes itself)
curl -s "$BASE/api/v1/barcodes/resolve?code=6291041500001" -H "Authorization: Bearer $ACCESS" -H "X-Tenant-ID: $TID"

# 6) Place the order (idempotent)
curl -s -X POST $BASE/ext/v1/orders -H "Authorization: Bearer $ACCESS" -H "X-Tenant-ID: $TID" \
  -H 'Content-Type: application/json' -H "Idempotency-Key: $(uuidgen)" \
  -d '{"external_ref":"APP-0001","customer":{"code":"C-00001"},"requested_delivery_date":"2026-09-24",
       "lines":[{"barcode":"6291041500001","qty":2,"packaging":"box"},{"sku":"SC-1","qty":6}]}'

# 7) Record a collection
curl -s -X POST $BASE/ext/v1/collections -H "Authorization: Bearer $ACCESS" -H "X-Tenant-ID: $TID" \
  -H 'Content-Type: application/json' -H "Idempotency-Key: $(uuidgen)" \
  -d '{"customer":"C-00001","amount":"5000","method":"cash","gps":{"lat":31.52,"lng":74.35}}'

# 8) Driver: today's trip and stops
curl -s "$BASE/api/v1/trips?active=1" -H "Authorization: Bearer $DRV" -H "X-Tenant-ID: $TID"
curl -s "$BASE/api/v1/delivery-notes?trip=<trip id>&status=loaded" -H "Authorization: Bearer $DRV" -H "X-Tenant-ID: $TID"

# 9) Driver: proof of delivery with signature + photo + cash
curl -s -X POST "$BASE/api/v1/delivery-notes/<dn id>/deliver" -H "Authorization: Bearer $DRV" -H "X-Tenant-ID: $TID" \
  -H "Idempotency-Key: $(uuidgen)" \
  -F 'delivered=[{"line_id":"<line id>","qty_delivered":"22","qty_returned":"2","return_reason":"rejected","return_condition":"good"}]' \
  -F 'receiver_name=Shop owner' -F 'gps={"lat":31.5204,"lng":74.3587}' -F 'cash_collected=5000' \
  -F 'signature=@sig.png' -F 'photo=@shop.jpg'

# 10) Staff: orders inbox → confirm
curl -s "$BASE/api/v1/sales-orders/inbox" -H "Authorization: Bearer $STAFF" -H "X-Tenant-ID: $TID"
curl -s -X POST "$BASE/api/v1/sales-orders/<id>/confirm" -H "Authorization: Bearer $STAFF" -H "X-Tenant-ID: $TID" \
  -H 'Content-Type: application/json' -d '{}'

# 11) Cashier: receipt against open invoices (FIFO)
curl -s -X POST $BASE/api/v1/accounting/payments -H "Authorization: Bearer $STAFF" -H "X-Tenant-ID: $TID" \
  -H 'Content-Type: application/json' -H "Idempotency-Key: $(uuidgen)" \
  -d '{"direction":"in","partner_type":"customer","partner_id":"<customer uuid>","amount":"10000","method":"cash","reference":"Counter"}'

# 12) Refresh
curl -s -X POST $BASE/api/v1/auth/refresh -H 'Content-Type: application/json' -d '{"refresh":"'$REFRESH'"}'
```

---

## 11. Distributing the Android build (APK)

* Platform admin uploads each build at **Platform → Mobile app (APK)** (`POST /api/v1/platform/app-releases`, multipart: `file`, `version`, `build_number`, `notes`, `is_current`).
* Public, no-login endpoints for the current build:
  `GET /api/v1/app/android` → `{ available, version, build_number, size_mb, sha256, notes, released_at, download_url, page_url, qr_url }`
  `GET /api/v1/app/android/download` → the APK (`application/vnd.android.package-archive`, `Content-Disposition: attachment`)
  `GET /api/v1/app/android/qr.png?scale=8` → QR code PNG pointing at `https://dms.invoiceflow.pk/get-app`.
* `https://dms.invoiceflow.pk/get-app` is the page the QR opens on the phone (Download button + install steps). Every web user has a QR icon in the top bar with **Copy link** and **Download APK**.
* **In-app update check**: on launch call `GET /api/v1/app/android` (no auth header); if `version` is newer than the installed one (or `min_supported_version` is above it), prompt the user to open `download_url`.
  Current state: build **v1.1** (`dms-release-arm64.apk`, 37.8 MB) is served, uploaded **without a `build_number`** (`null`).
  On the next upload pass `build_number` = Android `versionCode` and `version` = `versionName`, then compare integers:

  ```
  info = GET /api/v1/app/android
  if !info.available: return
  if info.min_supported_version && semverLess(installedVersionName, info.min_supported_version): force-update screen (block)
  else if info.build_number > installedVersionCode (fallback: semverGreater(info.version, installedVersionName)):
       "Update available (v{version}, {size_mb} MB)" → open info.download_url (Android installs the APK; user allows "install from this source" once)
  optional: verify sha256 == info.sha256 after download
  ```
* The web login page also shows **Download app / QR code / Copy link** for the current build (same public endpoints).

## 12. Recommended mobile stack (matches the web app)

* React Native + **Expo** (SDK 51+), TypeScript, React Navigation, **TanStack Query** (cache + background refetch),
  **Zustand** (auth/session), `expo-secure-store` (tokens), `expo-camera` / `expo-barcode-scanner` (EAN-13, Code-128, QR),
  `expo-location` (GPS at delivery/collection), `react-native-signature-canvas`, `expo-image-picker`,
  `@react-native-firebase/messaging` or `expo-notifications` (FCM), SQLite/MMKV for the offline queue, Zod for payload validation.
* Generate types from `GET /api/schema/` with `openapi-typescript`; wrap `fetch` with: base URL, Bearer + `X-Tenant-ID`
  injection, 401 → refresh-and-retry, RFC 7807 error parsing, `Idempotency-Key` on mutations.
* **Uploads (photos, signatures, sheets):** access tokens live 15 minutes. Before a multipart POST, refresh if the token
  expires in < 2 minutes; on a `401` from an upload refresh once and resend the same request (same `Idempotency-Key`);
  if the refresh fails, sign out (refresh tokens rotate — §2.3).
* One code base; route to Rep / Driver / Staff navigators from `/me` (see §1). Use the same colour palette as the web app:
  primary `#F26522`, headings `#202C4B`, body text `#6B7280`, background `#F8F9FA`, success `#03C95A`, danger `#E70D0D`, info `#1B84FF`.

## 13. Screen map (minimum viable)

**Rep:** Login → Company picker → Home (target, held cash, pending orders) → Customers (search, map pin, outstanding) →
Customer detail (statement, open invoices, orders) → New order (scan/search, cart with packaging, review totals) →
Order detail/timeline → Deliveries → Collections (new, list, held) → Notifications → Profile/sessions.

**Driver:** Login → Today's trip → Stop list (navigate, call) → Stop detail (lines, partial/return, signature, photo, cash) →
Failed stop → Trip summary/reconciliation → Notifications.

**Staff:** Login → Dashboard → Orders inbox (review/confirm/hold/reject) → Order detail (mark delivered) → Stock lookup (scan) →
Receipts (record, list, cheques) → Rep settlements → Approvals → GRN scan / Pick scan / Count scan → Reports → Notifications.

## 14. Changelog — what changed since the first version of this file (22 Sep 2026)

Everything is **additive**: no field or endpoint was renamed or removed. Sections above are already updated; this list is
the diff so an app built against the first version knows what to touch.

| Area | Change | Where it shows up |
| --- | --- | --- |
| Catalogue | Product rows carry **`unit_size`** (UNIT: `"60ml"`, `"1L"`, `"pcs"`), **`packing`** (units per carton), **`retail_price`** (R.P / MRP), `price_effective_date` (staff endpoints), and a **`carton`** packaging level (`qty_in_base` = `packing`) | `/ext/v1/products`, `/api/v1/variants`, `/api/v1/products`, barcode-resolve results — see §5.1 "Price-list fields" |
| Catalogue | Pricing vocabulary: `price` = **T.P** (trade price, resolved for the customer — use in the cart), `retail_price` = **R.P**; **D.P** (cost) is never sent to reps | rep catalogue, cart, product card |
| Catalogue | Real **Keune Pakistan** catalogue loaded (495 items, 48 categories, upper-case names that differ only by `unit_size`, no barcodes/images) | list design: always show `unit_size`; category filter; placeholder image |
| Scanning | Keune items have no barcodes yet — match a scanned/typed code locally against `barcodes[].code` **and `sku`** (`/api/v1/barcodes/resolve` is staff-only, 403 for reps) | rep scan screen |
| Ordering | Order by carton: `{ "sku": "62001", "qty": 2, "packaging": "carton" }` (packaging by *name*, qty in cartons, omit `unit_price`) | cart → `POST /ext/v1/orders` |
| Accounts | New rep login **`rep1@keune.pk` / `rep123`** (sees the Keune catalogue, no customers yet — create one with `POST /ext/v1/customers`); real tenant **Trafco** exists — never test there | test config (§0) |
| Purchasing | Purchase orders have a **`reference`** (title, e.g. "Lahore Distribution Order"); PO lines carry **`unit_size`** and **`packing`** | GRN screen (§7.4), any PO list/detail |
| Purchasing | Staff can turn a supplier order sheet into a draft PO: `POST /api/v1/imports` multipart `type=purchase_order`, `file`, `options` JSON (`supplier`, `warehouse`, `date`, `reference`, `qty_unit`, `filename`) → `summary.po_id / po_number / lines / total`; same endpoint with `type=products` imports a price list (`.xlsx` accepted) | optional staff feature (web has it) |
| Distribution | Public APK endpoints + update check; current build v1.1 has no `build_number` — send `versionCode` next time | §11 |
| **Auth (new)** | **QR-code login**: `POST /api/v1/auth/qr-login/redeem`, deep links `dms://login?t=…` + `https://…/app-login?t=…`, App Links config; `GET /api/v1/app/android` adds `package_name`, `scheme`, `app_login_url`, `app_links_ready` | `mobile-app-qr-login.md`, §2.5a |
| Catalogue | Products carry a **default location** (`location` id + `location_name`, e.g. `"MAIN / A-01 — Rack A shelf 1"`) and can be created with an **opening quantity**; locations are managed under Settings → Locations (`/api/v1/settings/bins`) | show the location on the product card / pick screens |
| Customers | New fields: `business_name`, `ntn`, `strn`, `cnic`, `filer_status`, `alt_mobile`, `landline`, `default_discount_pct`; `phone2` was renamed `alt_mobile`. Cities come from `GET /api/v1/cities?search=` | customer form/detail in the app |
| Errors | RFC 7807 `type` now carries the specific code when there is one (`used`, `expired`, `invalid`, `locked`, `inactive`, `integrity`) instead of the generic `domain_error` | error handling |
| HTTP layer | Refresh the token before multipart uploads; retry once after a 401 | §12 |
| **Auth (rev 5)** | **Refresh grace window**: a refresh token rotated < 60 s ago is honoured once more, so a parallel refresh or a retry no longer logs the user out. Refresh once per app behind a mutex, pre-emptively under 45 s of life, and sign out only when `/auth/refresh` itself answers `401`/`403` — not on `429`/`5xx`/offline | §2.3 |
| **Auth (rev 5)** | A `403` with `type: impersonation_expired` or `tenant_not_member` means the stored company is stale: clear `tenant_id`, re-run `/me`, back to company selection. The login itself is still valid | §2.3 |
| **Delivery (rev 5)** | The **order → delivery chain** is now explicit: `GET /api/v1/sales-orders/{id}/workflow` returns the seven steps with their state, documents and next action; `POST /api/v1/sales-orders/{id}/load-form`, `POST /api/v1/pick-lists/{id}/send`, `POST /api/v1/pick-lists/{id}/issue` (stock drops here), `POST /api/v1/sales-orders/{id}/invoice` | §7.3 |
| **Delivery (rev 5)** | New statuses: sales order `issued`, delivery note `dispatched`, load form `sent` / `issued` / `partially_issued`. A driver's "to do" list is `status=loaded` **or** `dispatched` | §6, §9 |
| **Permissions (rev 6)** | Staff permissions are **per page**: `/me` returns `pages` with `view/add/edit/delete` + extras per screen, and the API enforces the same switches. Gate the UI on `me.pages[...]`, not on codenames; `view` off ⇒ the screen must not open. A document's attachments and history follow that document's switches, and `GET /api/v1/attachments` now requires `?model=…&object_id=…` | §2.5 |
| **Permissions (rev 6)** | New per-page codenames (`<module>.view_<thing>` / `add_` / `change_` / `delete_`); the old coarse ones still work, so an app that checks `delivery.complete_delivery` or `external.*` is unaffected | §2.5 |
| **Accounts (rev 6)** | One email or mobile = one account (case-insensitive); creating or editing a user onto a taken one answers `400` with `type: "duplicate"`. Customers and suppliers allow one record per email per company | §7.x |
| **Delivery (rev 5)** | Dispatch carries a **`helper`** (helper / delivery person) next to `driver`, `delivery_rep` and `loader`; delivery confirmation accepts **`remarks`**; delivery notes carry `picklist_number` | §6.1, §7.3 |
| **Purchasing (rev 7)** | The purchase-order **list** no longer returns `lines[]` — it sends `lines_count`, `grns_count` and `units_due` instead (it was 200 KB on a tenant with a 229-line order). Read the detail endpoint for lines. `?open=1` lists only the orders that can still be received | §7.4 |
| **Purchasing (rev 7)** | A receipt is always against a purchase order and takes its **supplier** from it — the screen no longer asks. Receiving is **scan-first**: start a receipt with `lines: []` and post scans; batch and expiry are optional, a box label carries its batch, and the order is matched on every scan (`not_on_order`). Receiving against an unapproved order is refused (`po_not_approved`) | §7.4 |
| **Inventory (rev 7)** | Goods received are placed in the product's own rack (`bin`). An outward move that does not name a bin is now drawn from whichever rack holds the stock, so nothing has to know about bins to sell, transfer or return it | §7.4 |
| **Field areas (rev 8)** | A rep can be given **working areas**. When they have one, `POST /ext/v1/orders`, `/ext/v1/collections` and `/ext/v1/customers` are checked against it: **send `gps` `{lat,lng}` on all three**. Outside ⇒ `403 outside_area` (`detail` says how far and which area; `errors.distance_m`), no fix ⇒ `403 location_required`. A rep with no area is unrestricted — read `/ext/v1/my-areas` on login to know which applies | §7.1, §7.2 |
| **Field areas (rev 8)** | `policy` from `/ext/v1/my-areas` is `off` · `warn` (recorded, allowed) · `block` (refused). Check the fix **before** the user fills a long form and warn them early, rather than letting the server refuse the submit | §7.2 |
| **Stores (rev 9)** | A customer's pin can be dropped or moved from the app: `POST /api/v1/customers/{id}/pin` `{lat,lng}` (or `{clear:true}`). Staff screens read every store at once from `GET /api/v1/customers/map` → `{stores:[{id,name,code,phone,address,lat,lng,reps[],fences[]}], without_pin:[…], fences:[…]}` | §7.1 |
| **Stores (rev 9)** | A rep sees the stores **assigned** to them, not every store inside their area. Staff hand them over with `POST /api/v1/geofences/{id}/assign-customers` `{rep, customers:[…] | all:true, remove?}`; the app just reads its own list as before | §7.1 |
| **Visit (rev 10)** | An order a rep submits carries the visit: `gps {lat,lng}` **and** `attachments: ["data:image/jpeg;base64,…"]` — a photo taken with the camera, not picked from the gallery. It is filed against the order as `kind: "visit_photo"`, named with the time and the coordinates. The company setting can make it compulsory: without one the answer is `400 photo_required` | §7.2 |
| **Visit (rev 10)** | Read `/ext/v1/my-areas` on login and check the fix **on the phone** before the user fills a cart — circle: distance to centre ≤ `radius_m`; polygon: ray casting, and distance to the nearest edge for the "N km outside" message. The web rep portal does exactly this in `frontend/src/features/rep/Visit.tsx`; mirror it so the refusal never arrives as a surprise at submit time | §7.2 |
| **People (rev 10)** | `/api/v1/users` lists **memberships**: a trip's `driver`, `helper` and `loader` take the row's `user_id`, not its `id` | §7.3 |
| **Scanning (rev 11)** | The barcode **on a carton** now resolves: a 14-digit GTIN-14 answers with the unit's product and `unit: "box"` / `qty_per_scan: packing`. Nothing to register — it is read out of the number. Keep the existing rule for the rest: a unit UPC counts one piece, the **item code** printed on the carton counts one carton, and scanning the same thing again adds to the row already open for it | §7.4 |
| **Scanning (rev 11)** | `POST /goods-receipts/{id}/scans` and `POST /pick-lists/{id}/scans` take the quantity from the label itself, so one carton scan counts `packing` units in the app exactly as it does on the web. Sending `qty` multiplies that — `{code, qty: 3}` on a carton code is three cartons | §7.4 |
| **Dates (rev 11)** | Show and take dates as **DD/MM/YYYY**. The API is unchanged — every date in a request or a response is ISO `YYYY-MM-DD` (datetimes ISO-8601 with an offset) — this is a display and keypad matter only. The web app no longer uses a native date picker for this reason: an en-US device reads MM/DD/YYYY and silently swapped day and month. Format on the way out, parse on the way in, and never send what the user typed straight through | §3 |

Questions or a missing endpoint: check `https://dms.invoiceflow.pk/api/docs/` first — it is generated from the running code.
