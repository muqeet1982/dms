# DMS mobile app — change request: **Log in by scanning a QR code** (no username / password)

**Date:** 22 Sep 2026 · **Server:** `https://dms.invoiceflow.pk` (backend + web already live) · **Base doc:** `mobile-app.md`
(§2 authentication, §11 APK distribution). This file only describes what the **app** has to add.

## 1. What the user experiences

1. On the computer, in the DMS web app, the user opens the profile menu (top-right avatar) → **Log in on phone (QR code)**.
   A one-time QR code is shown (valid **5 minutes**, single use).
2. On the phone:
   * **In the app** — login screen → **Scan QR to log in** → camera → the app is signed in immediately (no password).
   * **With the phone camera** (app not open / not installed) — the QR opens `https://dms.invoiceflow.pk/app-login?t=…`.
     That page tries to open the app (deep link / Android intent). If the app is installed it opens **and signs in**;
     if not, the page offers **Download APK**, and after installing the user taps *Scan QR to log in* in the app (or
     returns to the page and taps *Open in the DMS app*).
3. The computer screen shows "Signed in on your phone (Pixel 7)" the moment the phone redeems the code.

## 2. What the QR code contains

A URL — always send the **whole scanned string** to the server, it extracts the token itself:

```
https://dms.invoiceflow.pk/app-login?t=27jlygCMqAcdccbQiDmNV…      (43-char url-safe token)
```

The same token also arrives as a deep link when the landing page hands over to the app:

```
dms://login?t=27jlygCMqAcdccbQiDmNV…
```

## 3. New endpoints (all public unless noted)

| Method & path | Auth | Purpose |
| --- | --- | --- |
| `POST /api/v1/auth/qr-login/redeem` | none | **The app calls this.** Body `{ "token": "<scanned string or deep link>", "device_name": "Pixel 7", "platform": "android" }` → same payload as `POST /auth/login` (`access`, `refresh`, `user`, `memberships`, `must_change_password`) **plus** `tenant_id` (the company the user was working in on the web — preselect it) and `login_method: "qr"`. Marks the code used. Throttled 20/min per IP. |
| `GET /api/v1/auth/qr-login/status?t=<token>` | none | `{ status: "pending" \| "used" \| "expired" \| "invalid", expires_at, redeemed_at, redeemed_device }` — optional, e.g. to show "this code was already used". |
| `POST /api/v1/auth/qr-login/create` | web user (Bearer + X-Tenant-ID) | Used by the web app to make a code: `{ id, url, deep_link, qr_png (data URI), expires_at, expires_in: 300, tenant_id }`. Handy for **automated tests** of the app (see §7). Platform admins and users who must change their password get `403`. |
| `GET /api/v1/app/android` | none | Now also returns `package_name`, `scheme` (`"dms"`), `app_login_url`, `app_links_ready` — read `scheme`/`package_name` instead of hard-coding if you like. |

Redeem errors (RFC 7807 body; **`type` now carries the specific code** — this is new for all errors, e.g. `locked`, `inactive`):

| HTTP | `type` | Show |
| --- | --- | --- |
| 400 | `invalid` | "This QR code is not valid — scan the code from the DMS web app (profile → Log in on phone)." |
| 410 | `used` | "This code was already used. Generate a new one on the computer." |
| 410 | `expired` | "This code has expired (codes last 5 minutes). Generate a new one on the computer." |
| 403 | `inactive` | "Your account is inactive." |
| 429 | `throttled` | "Too many attempts — wait a moment." |

Example:

```bash
curl -s -X POST https://dms.invoiceflow.pk/api/v1/auth/qr-login/redeem -H 'Content-Type: application/json' \
  -d '{"token":"https://dms.invoiceflow.pk/app-login?t=27jly…","device_name":"Pixel 7","platform":"android"}'
# → { "access": "…", "refresh": "…", "user": {…}, "memberships": [ { "tenant_id": "01a0c76b-…", … } ],
#     "must_change_password": false, "session_id": "…", "tenant_id": "01a0c76b-…", "login_method": "qr" }
```

## 4. App changes

### 4.1 Login screen — "Scan QR to log in"

* Add a secondary button under the password form: **Scan QR to log in**.
* Open the camera (`expo-camera` `CameraView` with `barcodeScannerSettings: { barcodeTypes: ["qr"] }`), read the first QR,
  stop scanning, call `redeem` with the raw string.
* On success run **exactly the same post-login code path** as the password login (store tokens in SecureStore, create the
  session, register the FCM device token, load `/me`), with one addition: if `tenant_id` is in `memberships`, select that
  company and skip the picker.
* On error show the message from the table above with **Scan again** / **Cancel**.
* Reject anything that is not a DMS code before calling the server: accept only strings containing `/app-login?t=` or
  starting with `<scheme>://login?t=` (scheme from `/api/v1/app/android`, default `dms`).

### 4.2 Deep links / App Links — open the app from the landing page

Register both link forms so the phone can hand the token to the app:

```jsonc
// app.json / app.config.js
{
  "expo": {
    "scheme": "dms",
    "android": {
      "package": "pk.invoiceflow.dms",                       // ← your real applicationId; tell the platform admin
      "intentFilters": [
        { "action": "VIEW", "autoVerify": true,
          "data": [ { "scheme": "https", "host": "dms.invoiceflow.pk", "pathPrefix": "/app-login" } ],
          "category": ["BROWSABLE", "DEFAULT"] }
      ]
    }
  }
}
```

Handle the link on **cold start** and while **running**:

```ts
import * as Linking from "expo-linking";

function tokenFromUrl(url: string | null): string | null {
  if (!url) return null;
  const { queryParams, path, hostname } = Linking.parse(url);
  const isLogin = url.includes("/app-login") || hostname === "login" || path === "login";
  const t = queryParams?.t;
  return isLogin && typeof t === "string" && t.length > 20 ? t : null;
}

useEffect(() => {
  Linking.getInitialURL().then((u) => { const t = tokenFromUrl(u); if (t) handleQrToken(t); });
  const sub = Linking.addEventListener("url", ({ url }) => { const t = tokenFromUrl(url); if (t) handleQrToken(t); });
  return () => sub.remove();
}, []);
```

`handleQrToken(t)`:

* **Not signed in** → `redeem` → same success path as 4.1.
* **Signed in as the same user** → toast "Already signed in as {name}" (do not redeem; the code stays unused).
* **Signed in as someone else** → confirm "Sign out {current} and sign in as the person on the computer?" → logout
  (`POST /auth/logout`) → `redeem`.
* Expired/used codes are common here (the user installed the APK first, which takes more than 5 minutes): show the
  error and a **Scan QR to log in** button so they can scan the (regenerated) code from the computer.

### 4.3 Android App Links verification (so the https link opens the app without the chooser)

Give the platform admin two values; they enter them in **Platform → Mobile app (APK) → App identity** (or
`PATCH /api/v1/platform/app-releases/config {android_package, sha256_fingerprints}`), after which
`https://dms.invoiceflow.pk/.well-known/assetlinks.json` is served automatically:

* the **package name** (`android.package`), and
* the **SHA-256 fingerprint of the signing certificate** of the release build:
  `eas credentials` → Android → keystore → "SHA256 Fingerprint", or `keytool -list -v -keystore release.keystore -alias <alias> | grep SHA256`.
  (Debug builds have a different key — add both fingerprints, one per line, if you want App Links to verify on debug builds.)

Until this is configured the landing page still works: it uses an Android **intent URL**
(`intent://login?t=…#Intent;scheme=dms;package=<package>;S.browser_fallback_url=…/get-app;end`) when the package name
is known, and the plain `dms://login?t=…` scheme otherwise; if the app is not installed the page shows the download button.

### 4.4 Session management (nothing new, just make sure)

* The QR session is an ordinary refresh-token family: it appears in **My profile → sessions** on the web as
  `platform: android`, `device: <device_name>` and can be revoked there → the app must handle a failing refresh by
  returning to the login screen (already required by §2.3 of the base doc). Send a meaningful `device_name`
  (e.g. `${Device.brand} ${Device.modelName}`).
* Do **not** cache the token; it is dead after one redeem.

## 5. UI copy (suggested)

* Button: **Scan QR to log in** · sub-text: "On the computer: profile menu → *Log in on phone*."
* Scanner screen title: **Point the camera at the code on the computer screen**.
* Success toast: **Signed in as {full_name}** ({tenant_name}).

## 6. Security notes (why the rules are what they are)

* Codes are random (256 bit), stored **hashed**, **single use**, expire after **5 min**; making a new code invalidates the
  previous pending one. Anyone who scans a live code signs in as that user — the web modal says so.
* Blocked for platform admins (they never use the app) and for users who must still change their password.
* Everything is audit-logged (`auth.qr_login.issued`, `auth.qr_login`) with IP + device.

## 7. How to test without a second screen

```bash
# 1) make a code as a web user (owner of the demo company)
TOK=$(curl -s -X POST https://dms.invoiceflow.pk/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"identifier":"owner@demo.pk","password":"owner123","platform":"web"}' | jq -r .access)
curl -s -X POST https://dms.invoiceflow.pk/api/v1/auth/qr-login/create \
  -H "Authorization: Bearer $TOK" -H "X-Tenant-ID: 01a0c76b-94c6-7f20-a779-d7f4ba615ce5" -d '{}' -H 'Content-Type: application/json'
# → copy "url" (or "deep_link"); render it as a QR (any online QR generator) and scan it with the app,
#   or open the deep_link on the phone: adb shell am start -a android.intent.action.VIEW -d "dms://login?t=…"
# 2) status while waiting:  GET /api/v1/auth/qr-login/status?t=<token>   → pending → used
```

Test users: `owner@demo.pk / owner123`, `rep1@keune.pk / rep123`, `rep1@colormax.pk / rep123`, `driver@demo.pk / driver123`
(all in tenant Demo Distributors). The real tenant *Trafco* must not be used for tests.

## 8. Acceptance checklist

- [ ] Login screen has **Scan QR to log in**; scanning a fresh code signs in and lands on the right company/home.
- [ ] Used / expired / invalid codes show the right message and offer to scan again.
- [ ] `dms://login?t=…` and `https://dms.invoiceflow.pk/app-login?t=…` open the app (cold start + running) and sign in.
- [ ] Already-signed-in cases (same user / different user) behave as in 4.2.
- [ ] `device_name` is sent; the session shows up on the web profile page and can be revoked.
- [ ] Package name + signing SHA-256 handed to the platform admin; `assetlinks.json` returns the statement; `adb shell pm get-app-links <package>` shows `verified`.
