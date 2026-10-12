# Design brief — DMS mobile app

**For the AI designing the app.** Every feature, every screen, every state. No API details — those live in
`guide.md` next to this file. Where a number or a label is quoted here, it is what the product actually says
today; keep the wording unless you have a better one.

---

## 1. The product, and who is holding the phone

A distribution company in Lahore buys hair-colour and cosmetics in bulk and sells them to shops and salons across
the city. Three people use this app, and only one of them is sitting down.

**The sales rep** — the main user. On a motorbike, moving between 15–25 shops a day. Phone in one hand, order pad
replaced. Bright sunlight, dusty screen, patchy 3G, a shopkeeper waiting. Every tap must be big, every screen must
survive a lost connection, and nothing may need two hands.

**The driver** — delivering the day's orders. Same conditions, fewer decisions: a list of stops, and at each one a
signature, a photo and sometimes cash.

**The office** (owner, coordinator, cashier) — mostly on the web; on the phone they want to approve an order or
check a number while away from the desk.

Design for the rep. The other two inherit the same system.

---

## 2. Foundations

**Colour.** The product's accent is already set and should carry over:

| Token | Value | Use |
| --- | --- | --- |
| Primary | `#F26522` (orange) | Actions, the active tab, the selected shape on a map |
| Ink | `#202C4B` (navy) | Headings, primary text |
| Success | `#16A34A` | Inside the area, delivered, paid |
| Warning | `#F59E0B` | Short, pending, no pin |
| Danger | `#B91C1C` | Outside the area, failed, overdue |
| Muted | `#6B7280` | Secondary text |
| Surface | `#FFFFFF` on `#F8F9FA` | Cards on background |

Orange is for *what the user does*. Status colour is for *what is true*. Never use orange to mean "good".

**Type.** Roboto (the web app uses it). Sizes that survive sunlight: body 15–16, secondary 13, never below 11.
Numbers that matter — amounts, quantities, distances — get weight 600 and a size above the label next to them.

**Touch.** 48 dp minimum. The primary action of any screen sits in a bottom bar within thumb reach, full width,
never a floating pill in a corner.

**Density.** One card per idea. A shop, an order, a stop, a line item — each is a row you can tap. No tables; the
web app has tables, the phone has rows with a label above and detail below.

**Icons.** Tabler (the web app uses `ti-*`), outline style, 20–24 dp.

**Dark mode.** Optional, but if you do it: surfaces `#111827`/`#1F2937`, keep the same accent and status colours.

**Urdu / RTL.** The data is English (place names were deliberately switched to English). Shop names may contain
Urdu characters — make sure the font stack renders them and rows don't clip. Full RTL layout is not required.

---

## 3. Navigation

Three shells, chosen by who signed in. The user never picks.

**Rep** — bottom tabs, five at most:

```
  Today        Shops        [ + Order ]        Money        Me
```

`+ Order` is the centre action, raised and orange. "Today" is the home screen.

**Driver** — bottom tabs: `Trip · Stops · Me`. If there is one active trip, open straight onto its stop list.

**Staff** — bottom tabs: `Inbox · Orders · Cash · Me`.

Above the tabs, every screen has a plain title bar: title on the left, at most one icon on the right. No hamburger.

---

## 4. The rep's screens

### 4.1 Sign in

One field for email **or** mobile, one for the password, one big button. Below it: **Scan QR to sign in** (the web
app shows a QR the rep scans — no typing on a phone keyboard in the sun). If the account belongs to more than one
company, a simple list follows: company name, a tick on the current one.

First sign-in may force a password change — a plain two-field screen, rules shown as they are met, not as red
errors after the fact.

### 4.2 Today (home)

The day in one glance, top to bottom:

1. **The position strip** (see §5.1) — always first, always visible.
2. **Three numbers**, side by side: *Orders today* · *Value today* · *Cash in hand*. Cash in hand is amber when
   above zero — it is money the rep is carrying and must hand in.
3. **Target** — a thin progress bar: "PKR 240,000 of 400,000 this month".
4. **Shops nearby** — the three or four assigned shops closest to the current position, each with its distance and
   when it was last visited. Tapping one opens the shop.
5. **Waiting to send** — only appears when the offline queue is not empty: "3 orders waiting · tap to retry".

### 4.3 Shops (list)

A searchable list of the rep's own shops. Each row: name, code, area or route, outstanding balance (red when
overdue), distance from here. Filters as chips along the top: *Nearby · Has dues · Not visited this week*.

A **map toggle** in the title bar flips the list into a map: the rep's working areas drawn as translucent shapes,
shops as dots — filled green inside an area, hollow when not. Tapping a dot opens the shop card.

Empty state: "No shops assigned yet. Your office assigns the shops in your area."

### 4.4 Shop (detail)

Header: name, code, phone (tap to call, tap to WhatsApp), address, "x m away".

Then, in order of how often they are needed:

- **Take an order** — primary button.
- **Collect cash** — secondary, with the outstanding amount on it: "Collect — PKR 42,300 due".
- **Outstanding**: a compact ageing strip (current / 30 / 60 / 90+) and the open invoices, each tappable.
- **History**: the last orders with status chips, the last visit date.
- **Location**: a small map card showing the shop's pin and whether it falls inside the rep's area. If the shop has
  no pin: "No location saved — tap to drop a pin here" (one tap sets it from the current position).

### 4.5 New order — the most important screen in the app

Four parts, in one scroll, with a fixed bottom bar.

1. **The position strip** (§5.1), pinned at the top. If it is red, the rep must see it before they start.
2. **Shop** — pre-filled when they came from a shop, otherwise a search field.
3. **Add items** — a big scan field first ("Scan a barcode"), with a camera button that opens the scanner
   full-screen; below it, search by name or code. Results are rows: name, pack size, price for *this* shop, and
   `+1` / `+carton` buttons. One tap adds; a second tap on the row opens a quantity stepper.
4. **The cart** — rows with quantity steppers, the line value, and a swipe to remove. The bar at the bottom shows
   the running total and the single action: **Submit order**.

Before submitting: if the company requires a shop photo and none has been taken, the camera opens instead of
submitting — do not refuse, just ask for the thing that is missing.

After submitting: a short confirmation sheet with the order number and two choices — *Done* and *New order for
another shop*. Never bounce straight back to an empty form; the rep needs to see that it worked.

**When it is refused** (outside the area) the sheet is red and quotes the server: *"You are 1044.6 km outside
Lahore — DHA Phase 3-5. This can only be recorded inside the area you cover."* with *Retry location* and *Keep the
cart*. The cart must survive — losing a basket of 20 items is the worst thing this app can do.

### 4.6 Collect cash

A sheet, not a page: shop, amount (big numeric keypad), method (Cash / Cheque / Transfer chips), reference when it
is not cash, and a note. Below the amount, the open invoices it will settle, oldest first, with ticks — so the rep
can see "this clears INV-00018 and part of INV-00021".

Same position rule as an order: collecting from outside the area is refused with the same wording.

On success: a receipt sheet with the number, the amount, and *Share* (WhatsApp the shopkeeper a receipt).

### 4.7 Money

Two tabs.

- **Cash in hand** — the total the rep is carrying, then each collection: shop, amount, time, status chip
  (`held by rep` → `settled`). A note at the top: "Hand this to the office to clear it."
- **My orders** — their orders with status chips and value; tap for the order, its lines, and where the delivery
  has reached.

### 4.8 Me

Name, code, supplier, the areas they cover (as named chips with sizes), the device's location permission state,
*Sign out*, app version. Nothing else.

---

## 5. The components that carry the product

### 5.1 The position strip

The single most important component. A full-width card, one line of status and one of detail, with the camera
button on the right when a photo is wanted.

| State | Border / icon | Line 1 | Line 2 |
| --- | --- | --- | --- |
| Inside | green | `You are inside Lahore — Gulberg` | `31.51800, 74.34800 · ±12 m` |
| Outside | red | `1044.6 km outside Lahore — DHA Phase 3-5` | `24.86070, 67.00110 · orders here will be refused` |
| Locating | grey | `Finding where you are…` | spinner |
| Denied | amber | `Location is off — turn it on so the order counts for your area` | `Try again` button |
| No areas | hidden | — | (a rep with no assigned area is unrestricted — show nothing) |

It must never be a dialog, never block the screen, and never be dismissible.

### 5.2 The shop photo button

Collapsed: an orange outline button, `Shop photo`. Taken: a 44 dp thumbnail with a *Retake* link. It opens the
**camera**, never the gallery. After capture, show the thumbnail immediately — the upload happens in the
background and must not hold up the order.

### 5.3 Scan field

Full-width input with a barcode icon that opens a full-screen scanner: a dimmed frame, a line that sweeps, the
last scanned item shown as a toast at the bottom ("ColorMax 7.1 Ash Blonde · +1 carton (24)"). Scanning does not
close the scanner — a rep scans several items in a row. A `Done` button closes it.

### 5.4 Status chips

Small, filled, lower case. `submitted` grey · `confirmed` blue · `issued` indigo · `invoiced` green ·
`out for delivery` purple · `delivered` green · `on hold` amber · `rejected` red · `held by rep` amber ·
`settled` green.

### 5.5 Quantity stepper

`−` | value | `+`, 48 dp each, with the unit under it ("cartons of 24"). Tapping the value opens a numeric pad.

### 5.6 Map card

Used on the shop screen and the areas screen: rounded, 160 dp tall, the working areas as translucent orange/blue/
green shapes, shops as dots, the current position as a blue dot. Tapping opens a full-screen map. Labels are in
English.

### 5.7 Offline banner

A thin amber strip under the title bar: `Offline — 3 orders waiting`. Tapping opens the queue: each item with what
it is, when it was captured, and *Retry* / *Discard*. Items the server rejected sit here in red with the reason.

---

## 6. The driver's screens

### 6.1 Trip

A header card: trip number, vehicle registration, the driver's own name and the helper riding with them, and a
progress ring — "6 of 11 stops done". Below it, *Start trip* when it has not started.

### 6.2 Stops

An ordered list. Each row: sequence number in a circle, shop name, address, order value, status chip. Done stops
grey out and sink to the bottom. A map toggle shows the route.

### 6.3 Stop (proof of delivery)

The screen that must work with one hand and dirty fingers:

1. Shop name, phone (tap to call), address, *Navigate* (hands off to Google Maps).
2. **What is on board** — the lines, each with a quantity stepper pre-filled to the full amount. Reducing one
   reveals a reason chip row: *rejected · damaged · shortage · not ordered*.
3. **Received by** — a name field, then a **signature pad** (full-width, clear button, white on a light grey
   frame).
4. **Photo** — of the goods or the shop front.
5. **Cash collected** — numeric, optional.
6. Bottom bar: **Delivered**, with a smaller *Could not deliver* link that asks for a reason.

Everything here must queue offline with the time it actually happened.

---

## 7. The office screens (phone)

Keep these few and obvious.

- **Inbox** — orders waiting: shop, rep, value, age ("2 h ago"). Swipe right to confirm, left to hold. Tapping
  opens the order with its lines and the rep's **shop photo** — the photo is the reason this screen is worth
  having on a phone.
- **Order chain** — the seven steps as a vertical stepper: *Order · Load form · Warehouse issue · Invoice ·
  Dispatch · Out for delivery · Delivered*. Each step shows its state, who did it, when, the document number, and
  the one action available now. Done steps are green with a tick, the current one is orange, later ones grey.
- **Cash** — today's receipts, reps' unsettled cash with a *Settle* action per rep.
- **Areas** (read-only on the phone): the coverage map, each area with its shop count and rep.

---

## 8. States — design all four, every time

| State | What it looks like |
| --- | --- |
| **Loading** | Skeleton rows, never a centred spinner on a full screen. Lists keep their shape. |
| **Empty** | One line of plain language plus the action that fills it: "No orders yet. Take one from a shop." |
| **Error** | Inline card with the server's own sentence (it is written for the user), and a *Try again*. Never "Error 422". |
| **Offline** | The amber strip, and every action still works — queued, with a count. |

Success is a sheet or a toast, never a dialog that must be dismissed before the next tap.

---

## 9. Field conditions — the tests the design must pass

- Readable at arm's length in direct sunlight (contrast ≥ 4.5:1, no grey-on-grey).
- Every primary action reachable with the right thumb on a 6.7" phone.
- Works with a cracked screen protector and damp fingers: no long-press-only actions, no small close buttons.
- Nothing destructive without confirmation, and nothing confirmed by a tiny checkbox.
- A dropped connection never loses typed work.
- A whole order can be placed in under 60 seconds: open shop → scan 3 items → photo → submit.

---

## 10. What to deliver

Screens, in build order (match `guide.md`):

1. Sign in · company picker · forced password change
2. Rep: Today · Shops list · Shops map · Shop detail
3. Rep: New order (with scanner, cart, photo, position strip) · order confirmed · order refused
4. Rep: Collect cash · receipt · Money (cash in hand, my orders) · Me
5. Offline queue
6. Driver: Trip · Stops · Stop with signature and photo
7. Office: Inbox · Order chain · Cash
8. The component set from §5, as a small system: strip, chip, stepper, scan field, map card, photo button,
   signature pad, offline banner, empty/error states

Light theme required, dark optional. Deliver at 1x with a 4 dp spacing grid, and name the screens as they are
named here so the build maps one-to-one.

## Check-in screens

**Rep — "I am at the shop".** One tap opens it; the shop is pre-selected when the rep came from a customer
page. The screen has three decisions and nothing else: which shop, what happened (*No order* / *Took an
order*), and why not. The camera opens from the screen itself — there is no gallery option, by design — and
the thumbnail with a **Retake** button is the confirmation that the photo was taken. Below the form, today's
calls as a short list with each photo, so a rep can see the beat they have walked. Location state rides on
the same strip as the order screen: inside the area in green, outside in red with the distance.

**Office — Check-ins.** Three numbers across the top: calls, productive, no-order. Then the row a
distributor actually reads — **calls per person**: rep, calls, orders, no-order, and the reasons spelled out
("Still has stock 2 · Shop was closed 3"). Below it the calls themselves, with the photo as a 36 px
thumbnail that opens full size, the shop, the rep, the outcome, **metres from the shop's own pin** (green
under 100 m, amber to 500 m, red beyond) and whether it was inside their area. The distance column is the
point of the screen: it is what separates "was there" from "says they were there".
