# Privacy policy

**Version 1.0 — 10 October 2026**

This policy explains what the DMS app and website collect, why, who can see it, and how long it is kept.
It is written for the people whose information is actually in here: the sales representatives, drivers and
office staff who use DMS every day.

Please read the section on **location** carefully. If you are a representative or a driver, your employer may
record where you are during working hours, and you should know exactly what that means before you agree to it.

---

## Who holds your information

There are two parties, and the difference matters.

**Your employer** — the distribution company that gave you your login — decides what is collected about you and
what it is used for. They own the records in their company's account: your customers, your orders, your
collections, and your location history if they switch it on. Questions about *why* something is recorded, or
requests to correct or remove it, go to them first.

**The platform** — DMS, at `dms.invoiceflow.pk` — runs the software and stores the data on your employer's
behalf. We do not use it for our own purposes, we do not sell it, and we do not share it with advertisers.

Each company's data is kept separate. Your employer cannot see another company's records, and another company
cannot see yours.

---

## What is collected

**Your account.** Your name, email address or username, phone number, the role and permissions you were given,
the warehouses you are allowed to work in, and which company you belong to.

**How you sign in.** The time of each login, the device and browser, and the IP address it came from. Failed
attempts are recorded too, because that is how we spot someone trying to break into your account.

**Your work.** The orders you take, the cash you collect, the deliveries you complete, the customers you add or
visit, the stock you count, and every change you make to a record — with your name and the time against it.
This is an ordinary business record, and it is the reason the system exists.

**Where you are.** See the next section.

**Photos you take.** A photo of a shop, a signature at delivery, or a photo attached to a check-in. These are
stored with the time and the position they were taken at.

**Your device, for notifications.** A push token from Google Firebase, so the app can tell you an order was
confirmed or a delivery was assigned. The token identifies your app installation, not you personally.

**Nothing else.** DMS has no advertising, no analytics tracker, no third-party pixels, and no social-media
plug-ins. It does not read your contacts, your messages, your photo gallery, your call log or your other apps.

---

## Location — read this part

**It is your employer's switch, not ours.** Location recording is **off** unless your employer turns it on. When
it is on, the app shows you a notice before it asks for permission, and you can refuse. Refusing may mean you
cannot submit orders, because an order is checked against the area you cover — but that is a decision for your
employer to explain, not a technical trick.

**When it runs.** By default, only during the working hours your employer sets — typically 09:00 to 19:00,
Monday to Saturday. Outside that window the app stops sampling. Your employer can widen or narrow it, and the
app shows you the current setting.

**How often.** A position roughly every two minutes while you are moving, and only if you have moved far enough
to be worth recording. Each point holds the time, the latitude and longitude, how accurate the reading was, your
battery level, and whether the app was in the foreground.

**What it is used for.** Planning routes, confirming that a visit happened, settling a dispute about whether an
order was really taken at the shop, and refusing an order recorded far outside the area you cover.

**What it is not used for.** It is not used to monitor you outside working hours, and it is not used to track
your movements when the app is not sampling. We do not sell location data to anyone, for any purpose.

**Your position is stored with the documents you create.** An order, a cash collection, a delivery and a
check-in each keep the position they were made at, including how accurate that reading was. This is deliberate:
it is what lets an honest rep prove they were at the shop.

**You can always see your own history.** The app's "My route" screen shows your own day — where you went, how
far you travelled, where you stopped and for how long. No permission is needed for that. Your own track is
yours.

**Every time someone else looks at your location, it is recorded.** When a manager opens your route, the system
writes down who looked, whose track they looked at, and which day. That record cannot be edited.

**How long it is kept.** 90 days by default, then it is deleted automatically. Your employer can shorten that.
If they lengthen it, they should tell you.

---

## Permissions the app asks for, and why

| Permission | What it is for | If you refuse |
| --- | --- | --- |
| **Location**, including in the background | Recording your route during working hours; checking an order was taken inside your area | Location features stop; orders may be refused if your employer requires a position |
| **Camera** | Taking a photo of a shop at a visit or check-in, and capturing a signature | You cannot take photos; a check-in that requires one cannot be completed |
| **Notifications** | Telling you an order was confirmed, rejected or dispatched | The app still works; you just will not be told |
| **Storage / files** | Saving an invoice or a report you have asked for | You cannot download documents |

The camera is only opened when you tap to take a photo, and a check-in photo must be taken at that moment —
the app does not offer your gallery, because an old photo is not proof of a visit.

---

## Who can see it

- **You** — your own account, your own work, your own route.
- **Your employer's office staff**, each according to the permissions they were given. Seeing someone else's
  location requires a specific permission that most staff do not have, and every such view is logged.
- **The platform's support staff**, only when your employer asks for help and only as far as the problem
  requires. Support access is logged as well.
- **Google Firebase**, which delivers push notifications. Firebase receives the notification and your device
  token, not your location history. Google may process this outside your country.
- **Nobody else.** Your information is not sold, rented, or shared for advertising. If the law compels us to
  hand something over, we will tell your employer unless we are forbidden to.

---

## How long things are kept

| What | How long |
| --- | --- |
| Location history | 90 days by default, then deleted automatically; your employer may set it shorter |
| Orders, invoices, collections, deliveries | As long as the company's account is open, and after that as long as tax and company law require |
| Photos attached to documents | With the document they belong to |
| Login and audit records | At least a year, because they are the record of who did what |
| Your account | Until your employer removes it; after that your name stays on the documents you created, as an unchangeable business record |

---

## How it is protected

Everything travels over an encrypted connection. Passwords are stored as one-way hashes and cannot be read back
by anyone, including us. Access is limited by role, so most staff cannot reach most data. Failed logins are rate-
limited and repeated attacks are blocked at the server. Changes to records are written to an audit log with the
name of the person who made them.

No system is perfect. If information is ever exposed, we will tell the affected company promptly and explain
what happened.

---

## Your choices

- **See your own data.** Your route, your orders and your collections are all in the app.
- **Correct something.** Ask your employer — they can edit most records directly.
- **Turn off location.** You can withdraw the permission in your phone's settings at any time, and the app will
  stop recording. Tell your employer, because it may affect whether you can submit orders.
- **Ask for deletion.** Deletion requests go to your employer, who decides what their legal obligations allow
  them to remove. Business documents usually have to be kept; location history is deleted on the retention
  schedule regardless.
- **Uninstall.** Removing the app stops all collection from that device immediately.

---

## Children

DMS is a tool for business staff. It is not directed at children, and we do not knowingly collect information
from anyone under 18.

---

## Changes to this policy

If this policy changes in a way that affects you, the new version is published here with a new version number
and date, and the app shows it to you again before you continue. The version at the top of this page is the one
in force.

---

## Contact

Questions about your own information: ask your employer first — they decide what is collected in their company's
account.

Questions about the platform, or a problem your employer cannot resolve:
**support@dms.invoiceflow.pk**
