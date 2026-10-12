# Server security — firewall (ufw) and fail2ban

Everything here is driven from **Admin → Security → Banned IPs / Firewall** once the helper is installed:

```bash
sudo bash /var/www/dms/deploy/security/install-security.sh
```

The installer is idempotent — re-run it whenever you change the jail files in this folder.

## What it installs

| File | Installed to | Purpose |
| --- | --- | --- |
| `dms-secops` | `/usr/local/bin/dms-secops` (root, 0755) | the only privileged command the web app may run |
| — | `/etc/sudoers.d/dms-secops` | `django ALL=(root) NOPASSWD: /usr/local/bin/dms-secops` — nothing else |
| `jail-dms.local` | `/etc/fail2ban/jail.d/dms.local` | the jails below, with **permanent bans** |
| `filter-dms-auth.conf` | `/etc/fail2ban/filter.d/` | failed DMS logins / OTP / QR-login attempts |
| `filter-dms-probe.conf` | `/etc/fail2ban/filter.d/` | vulnerability scanners (`/.env`, `wp-login`, phpMyAdmin, …) |
| `filter-dms-manual.conf` | `/etc/fail2ban/filter.d/` | empty jail behind the admin's "Block this IP" button |
| — | `/etc/fail2ban/jail.d/dms-ignoreip.local`, `/etc/dms-secops/trusted.json` | the trusted list the Trust button writes |

The web app never runs `ufw` or `fail2ban-client` itself: it calls `sudo dms-secops <command>`, which re-validates
every IP/port before touching anything and answers in JSON.

## Port policy (what the firewall allows)

| Port | Decision | Why |
| --- | --- | --- |
| 22/tcp | **allow, rate-limited** (`ufw limit`) | SSH administration. fail2ban's `sshd` jail bans after 4 failures — permanently. |
| 80/tcp | **allow** | Let's Encrypt HTTP-01 renewals and the redirect to HTTPS. |
| 443/tcp | **allow** | the web app, the API and the mobile app. |
| 8081/tcp | **allow** | the ZKTeco attendance device pushes over plain HTTP and cannot follow redirects. Remove it if that device is retired. |
| 5432 (PostgreSQL), 3306 (MariaDB), 6379 (Redis) | **deny** | they already listen on 127.0.0.1 only; the firewall is the second lock. |
| 631 (CUPS), 25/587 (SMTP), 3389, 5900, 9090, 3000, 5555, 8000 | **deny** | nothing on this server should expose them to the internet. |
| everything else inbound | **deny** (default policy) | outbound stays open so the app can reach SMTP/FCM/payment APIs. |

The Firewall page lists every listening socket, says whether the internet can reach it and flags anything that
should not be open (red) or that is expected but currently closed (amber).

## Jails (all bans are permanent — "ban for good")

| Jail | Watches | Trips after |
| --- | --- | --- |
| `sshd` (aggressive) | SSH auth failures, invalid users, bad protocol | 4 failures / 10 min |
| `nginx-http-auth` | HTTP basic-auth failures | 5 |
| `nginx-limit-req` | clients hammering rate-limited endpoints (e.g. `/api/v1/auth/`) | 10 |
| `nginx-botsearch` | known bot paths in the access log | 3 |
| `nginx-bad-request` | malformed/garbage requests | 10 |
| `dms-auth` | repeated 400/401/403/410/423 on `/api/v1/auth/*` and `/ext/v1/*` | 8 / 10 min |
| `dms-probe` | `/.env`, `.git`, `wp-admin`, `xmlrpc.php`, phpMyAdmin, `/actuator`, nginx 444 replies | 4 / 5 min |
| `recidive` | anything banned twice in 24 h → banned on **all ports** | 2 |
| `dms-manual` | nothing — holds IPs blocked by hand from the admin UI | — |

Bans are enforced through `ufw` so they survive a reboot together with the rest of the rules.

## Trusted IPs

**Trust** (admin → Banned IPs) does three things at once: unban the address everywhere, add it to fail2ban's
`ignoreip` (so it can never be banned again, including by `recidive`) and add a ufw allow rule. Trust your office
or warehouse IP before you tighten anything — that is the safety net against locking yourself out.

## Manual checks

```bash
sudo dms-secops status | python3 -m json.tool | head -40   # overview
sudo dms-secops bans --q 45.148                            # search bans
sudo dms-secops trust 203.0.113.10 --note "Lahore office"
sudo fail2ban-client status dms-probe                      # one jail
sudo ufw status numbered                                   # raw rules
```
