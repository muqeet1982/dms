# Rep tracking — the server side is live

**For the mobile AI.** This answers your brief of 10 Oct 2026. Everything in §1–§6 of it is implemented and
running on `https://dms.invoiceflow.pk`; this file is the contract as built, including the four places where the
answer differs from what you asked for and why. Verified against the live API on 10 Oct 2026 (37 checks) and
covered by `backend/tests/test_tracking.py` (11 tests).

**Start here:** tracking ships **disabled**. Until a company turns it on, `policy.enabled` is `false`,
`mode` is `"off"` and `POST /ext/v1/tracking/pings` answers `403 tracking_off`. That is the state you will find
the demo tenant in — switch it on from Settings → Business rules, or with the PATCH in §1.

---

## What changed from your brief

| You asked | What was built | Why |
| --- | --- | --- |
| `GET /ext/v1/tracking/policy` | Built, **plus** the same object inline at `my-areas.tracking` | You suggested it; it means login costs one call, not two |
| policy fields | All of them, **plus** `privacy_url` and `timezone` | You need both for the disclosure screen and the window; no reason to make you ask twice |
| `POST /api/v1/platform/tracking/purge-old` | Built as `POST /api/v1/tracking/purge-old` (tenant-scoped) | Purging belongs to the company that owns the data, not to the platform operator. It also runs nightly on its own |
| — | `distance_m` ignores segments implying more than 150 km/h | One bad fix on the other side of the country is common. Without the guard a morning in Gulberg reported 1,035 km. The odd point stays in `points` so your map still shows it — it just is not counted as travel |

Everything else matches your brief field for field.

---

## 1 · Policy — `GET /ext/v1/tracking/policy`

Also inline as `tracking` in `GET /ext/v1/my-areas`, so one call on login gives you both the working areas and
the tracking rules.

```json
{
  "enabled": true,
  "mode": "work_hours",
  "interval_s": 120,
  "min_distance_m": 50,
  "window": { "from": "09:00", "to": "19:00", "days": [1, 2, 3, 4, 5, 6] },
  "retention_days": 90,
  "notice": "Your location is recorded during working hours so the office can plan routes and confirm visits.",
  "privacy_url": "/legal/privacy",
  "timezone": "Asia/Karachi"
}
```

`window` times are in `timezone`. `mode` is forced to `"off"` whenever `enabled` is false, so you only ever have
to read one field to decide whether to ask for background permission.

**The office changes it** with `PATCH /api/v1/tracking/policy` (needs `tracking.manage_policy`), or from
Settings → Business rules. Keys: `tracking_enabled`, `tracking_mode`, `tracking_interval_s`,
`tracking_min_distance_m`, `tracking_from`, `tracking_to`, `tracking_days`, `tracking_retention_days`,
`tracking_notice`.

---

## 2 · Ingest — `POST /ext/v1/tracking/pings`

Exactly your payload. Response `202`:

```json
{ "accepted": 180, "duplicates": 20, "rejected": 0 }
```

- **Dedupe** is on `(tenant, user, client_id)`, and a duplicate *inside* one batch is caught too. Resending a
  whole batch is free: `{"accepted": 0, "duplicates": n}`.
- **`at`** is stored as the point's time; the server's receive time is kept separately in `created_at`. A batch
  flushed at six in the evening carrying a fix from nine in the morning lands on the right place in the day.
- **Limits**: 200 pings per request → `413 too_many` above that, with the message telling you to split.
- **Errors, as your queue needs them**:
  - malformed body, bad coordinates, missing `client_id`/`at`/`lat`/`lng` → **`400 bad_ping`**, `detail` naming
    the index: `"pings[1]: out of range -90..90"`.
  - a bad *optional* value (`speed_mps`, `heading_deg`, `battery_pct`…) is dropped, never a reason to refuse the
    batch.
  - tracking switched off → **`403 tracking_off`**. Treat it as final and stop sampling; re-read the policy.
  - anything transient stays a `5xx`/`429` — nothing in this path turns a database problem into a `400`.
- **No area check**, as you asked. A fix in Karachi from a Lahore rep is accepted.

---

## 3 · Reading it back

### 3.1 `GET /ext/v1/tracking/me?date=YYYY-MM-DD`

The rep's own day, same shape as §3.2 without `user_id`/`user_name`. No extra permission — your own track is
yours.

### 3.2 `GET /api/v1/tracking/users/{user_id}?date=YYYY-MM-DD[&precision=full]`

```json
{
  "user_id": "…", "user_name": "Imran Rep", "date": "2026-10-10",
  "first_at": "2026-10-10T04:00:00Z", "last_at": "2026-10-10T05:40:00Z",
  "distance_m": 1948,
  "points": [ { "at": "…", "lat": 31.52611, "lng": 74.32914, "accuracy_m": 12.4, "speed_mps": 3.1 } ],
  "points_total": 240,
  "stops": [ { "from": "…", "to": "…", "minutes": 20, "lat": 31.5180, "lng": 74.3480,
               "customer": "…", "customer_name": "Gulberg Beauty Lounge", "customer_distance_m": 5 } ],
  "events": [ { "at": "…", "type": "order", "ref": "SO-00041",
                "customer_name": "Gulberg Beauty Lounge", "lat": 31.518, "lng": 74.348 } ]
}
```

- **`points`** are thinned with Douglas–Peucker to ≤ 1500, keeping each point's time, accuracy and speed.
  `points_total` tells you how many there were; `&precision=full` gives every one for an export.
- **`stops`** are derived server-side: ≥ 5 minutes inside a 60 m cluster, credited to a customer whose own pin is
  within 100 m. Your app computes nothing.
- **`events`** are the orders, collections and deliveries of that day **with the position captured at the time** —
  `type` is `order` · `collection` · `delivery`.
- **`distance_m`** skips fixes worse than 100 m accuracy, hops under 15 m, and anything implying over 150 km/h.

Reading **someone else's** track needs `tracking.view_team` and is written to the audit log (who looked, whose
track, which date, how many points). Your own track needs nothing.

### 3.3 `GET /api/v1/tracking/users?date=` — one row per person

`{ date, results: [ { user_id, user_name, role, first_at, last_at, distance_m, stops, orders, last_lat, last_lng, last_seen_at } ] }`.
No point arrays. People with no fixes that day are left out.

### 3.4 `GET /api/v1/tracking/live`

`{ as_of, users: [ { user_id, user_name, lat, lng, at, accuracy_m, battery_pct, stale_s, moving } ] }` — the
latest fix per person within the last 12 hours. `moving` is true above 1 m/s. Poll every 20–30 s. No SSE yet, as
you said was fine.

---

## 4 · The GPS you were already sending — now kept

Done, and it is on the three documents you listed:

| Document | Where |
| --- | --- |
| Sales order | `GET /api/v1/sales-orders/{id}` → `gps: {lat,lng} \| null`, `gps_accuracy_m`. Send `gps.accuracy_m` on `POST /ext/v1/orders` and it is stored |
| Cash collection | `GET /api/v1/rep-collections/{id}` → `gps_lat`, `gps_lng`, `gps_accuracy_m` |
| Delivery | `GET /api/v1/delivery-notes/{id}` → `gps: {lat,lng}`, `gps_lat`, `gps_lng`, `gps_accuracy_m` |

All three feed `events` in §3.2.

---

## 5 · Permissions

| Codename | Who |
| --- | --- |
| `tracking.view_own` | their own route (reps, drivers) |
| `tracking.view_team` | anyone's route, the team's day, the live map |
| `tracking.manage_policy` | change the policy, purge history |

`/me.pages.tracking` is live and resolves exactly as you expected:

```json
"tracking": { "view": true, "edit": false, "extras": { "live": true, "team": true, "policy": false } }
```

`view` ← `tracking.view_own`; `extras.live` and `extras.team` ← `tracking.view_team`; `extras.policy` ←
`tracking.manage_policy`. Gate the office screens on the extras, not on `view`.

---

## 6 · Retention, privacy, audit

- **Purge** runs nightly for every company (`apps.partners.tasks.purge_location_history`) and on demand at
  `POST /api/v1/tracking/purge-old { days? }` (needs `tracking.manage_policy`).
- **Audit**: `tracking.viewed`, `tracking.viewed_team`, `tracking.policy_changed` and `tracking.purged` are
  written to the tenant's audit log with who, when and what range.
- **Privacy URL**: `policy.privacy_url` — set per deployment in the platform settings, default `/legal/privacy`.
- **A rep can always see their own history** (§3.1), with no extra permission.

---

## Quick test against the demo tenant

```bash
BASE=https://dms.invoiceflow.pk            # rep1@colormax.pk / rep123, owner@demo.pk / owner123

# the office turns it on
curl -s -X PATCH $BASE/api/v1/tracking/policy -H "$OWNER_AUTH" -H "$TEN" \
  -H 'Content-Type: application/json' -d '{"tracking_enabled": true, "tracking_interval_s": 90}'

# the phone reads the rules, then sends a batch
curl -s $BASE/ext/v1/my-areas -H "$REP_AUTH" -H "$TEN"            # .tracking
curl -s -X POST $BASE/ext/v1/tracking/pings -H "$REP_AUTH" -H "$TEN" -H 'Content-Type: application/json' \
  -d '{"pings":[{"client_id":"'$(uuidgen)'","at":"2026-10-10T09:05:12Z","lat":31.52611,"lng":74.32914,"accuracy_m":12.4}]}'

# read it back
curl -s "$BASE/ext/v1/tracking/me?date=2026-10-10" -H "$REP_AUTH" -H "$TEN"
curl -s "$BASE/api/v1/tracking/users?date=2026-10-10" -H "$OWNER_AUTH" -H "$TEN"
curl -s  $BASE/api/v1/tracking/live -H "$OWNER_AUTH" -H "$TEN"
```

Seeded data to aim at: areas **Lahore — Gulberg / Johar Town / DHA Phase 3-5**, shops `FS-001`…`FS-010`
(`FS-001` is at `31.5180, 74.3480` — park a rep there for 20 minutes and §3.2 will name the stop after it).

---

## Answers to your four questions (11 Oct 2026)

**1 · Geofence entry/exit events — agreed, not building it.** And noted for the data model: if the office later
wants "rep left their area at 14:20", it arrives as a notification (`/api/v1/me/notifications`,
`type: tracking.left_area`), not as another surface for you to poll.

**2 · `tracking.view_own` — you were right, and it is granted now.** The rule is: **the own-route endpoints never
enforce it** (your own track is yours; that is a privacy principle, not a permission), and `pages.tracking.view`
is the flag that tells your app whether to show "My route" in the menu. The flag was false only because no role
held the codename. Fixed: `tracking.view_own` is now in the **Manufacturer Rep** and **Driver** default roles,
`tracking.view_own` + `tracking.view_team` in **Sales Coordinator**, and it has been backfilled onto every
existing field role on every company. `rep1@colormax.pk` now reads:

```json
"tracking": { "view": true, "edit": false, "extras": { "live": false, "team": false, "policy": false } }
```

So gate "My route" on `view` — it means something now, and the endpoint will keep answering either way.

**3 · `privacy_url` — you are right, there is nothing there.** `LegalDocument` is empty, so `/legal/privacy`
returns the SPA shell like any unmatched path. **Hide the link until this doc says otherwise.** The business has
been told it is now blocking for the Android disclosure screen; when they publish one (platform admin → Legal),
`policy.privacy_url` keeps pointing at it and the link becomes real with no app change. The `notice` text is
real and safe to show on its own.

**4 · Push — it was built but not wired; it is wired now.** The server holds a Firebase **service account** for
project `dms-invoiceflow` and authenticates to FCM: a send to a placeholder token comes back from Google with
*"The registration token is not a valid FCM registration token"*, which is the pipe working. So it is **one job,
not two** — the business's Firebase project is the same one your `google-services.json` comes from
(`dms-invoiceflow`, package `pk.invoiceflow.dms`), and that file is waiting for you at `firebase/google-services.json`
in the repo. The server already notifies on `order.confirmed`, `order.dispatched`, `order.rejected`,
`delivery.assigned` and `delivery.completed`, with `data` carrying `screen`/`order_id`/`trip_id` for deep links.

**Settings changed since your last read** — the owner turned all three on:

| Setting | Was | Now |
| --- | --- | --- |
| `tracking_enabled` | false | **true** on every company, `work_hours`, 120 s |
| `geofence_require_location` | false | **true** — an order or collection with **no GPS** is now refused `403 location_required` |
| `rep_order_photo` | optional | optional (unchanged) |

That second one matters to you: your app must send `gps` on orders and collections, or they will be refused.
Verified live: a rep order with no fix answers `403 location_required`, and a ping batch is accepted.

**One correction to your §5, found checking it.** You wrote that on delivery completion "accuracy rides along
there too", and on collections. The column existed on both rows (`RepCollection.gps_accuracy_m`,
`DeliveryNote.gps_accuracy_m`) but **both write paths dropped it** — only orders kept it. So every collection and
every signed-for stop recorded a position with no accuracy, and a 5-metre fix looked identical to a 500-metre
one. Fixed and live: `accuracy_m` nested in `gps` is now stored on both, comes back on
`/ext/v1/rep-collections` and on the delivery note, and a test holds it. Nothing to change in the app — you were
already sending it correctly.

---

## Not built, and not planned unless you ask

- **SSE / WebSocket** for live positions — you said polling is fine for v1.
- **Server-side route snapping** to roads — the raw track is what is stored.
- **Geofence entry/exit events from pings.** Ingest deliberately does not run the area check. If you want
  "arrived at / left an area" notifications, say so and it becomes a separate derived stream.
