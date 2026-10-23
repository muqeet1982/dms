# Setting up DMS on a fresh server — a runbook for an AI agent

Written for an AI agent (Claude Code or similar) standing up this repository on a new Ubuntu 24.04 host,
with shell access as root. Every phase ends with a check — run it, and do not move on until it passes.
Nothing here requires Docker; production runs natively: nginx → gunicorn (unix socket) → Django,
Celery worker + beat, PostgreSQL 16, Redis, certbot TLS.

Conventions in this guide:
- `DOMAIN` = the site's hostname (e.g. `dms.example.com`). Replace it everywhere, including in
  `deploy/nginx-dms.conf`, the `.env` origins, and the systemd descriptions.
- Secrets are generated on the host and live only in `backend/.env` (chmod 600). Never commit them.
- The app's code expects the checkout at `/var/www/dms`. Keep that path unless you also edit every
  unit file and `deploy/deploy.sh`.

## Phase 0 — What this system is (read before touching anything)

- Multi-tenant distribution SaaS: Django 5.2 + DRF backend (`backend/`, 12 apps), React 18 + Vite
  frontend (`frontend/`, built to `frontend/dist`, served by nginx), one PostgreSQL database for all
  tenants (rows carry `tenant_id`; the default manager scopes queries, see `apps/core/managers.py`).
- Three API surfaces: `/api/v1` (office web), `/ext/v1` (rep mobile app), `/api/v1/platform` (host admin).
- Celery beat is load-bearing (reservation expiry, subscription states, alerts). A setup without the
  worker + beat units is incomplete even if the site renders.
- PDFs are WeasyPrint — it needs system libraries (Phase 1), not just pip.
- Docs worth reading after this one: `docs/DEPLOY.md` (ops reference), `docs/PERMISSIONS.md`,
  `docs/API.md`, `mobile-app.md` (the app contract), `agent-api.md` (AI-to-AI mailbox).

## Phase 1 — System packages

```bash
apt-get update && apt-get install -y \
  python3.12-venv python3-pip git nginx postgresql postgresql-contrib redis-server \
  libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi-dev shared-mime-info \
  certbot python3-certbot-nginx ufw fail2ban
# Node 22 (Vite needs >= 20). NodeSource is fine:
curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs
```

Check: `python3.12 --version`, `node --version` (v22+), `psql --version` (16+), `redis-cli ping` → PONG.

## Phase 2 — User, code, database

```bash
useradd -r -m -s /usr/sbin/nologin django || true
mkdir -p /var/www && cd /var/www
git clone <REPO_URL> dms && cd dms
sudo -u postgres psql -c "CREATE ROLE dms_saas LOGIN PASSWORD '<GENERATED_DB_PASSWORD>' CREATEDB;" \
                      -c "CREATE DATABASE dms_saas OWNER dms_saas;"
```

`CREATEDB` is required: the test suite creates throwaway databases.

Check: `sudo -u postgres psql -l | grep dms_saas` shows the database with owner `dms_saas`.

## Phase 3 — Backend environment

```bash
cd /var/www/dms/backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env
```

Edit `.env` — every `CHANGE_ME` must change:
- `DATABASE_URL=postgres://dms_saas:<GENERATED_DB_PASSWORD>@127.0.0.1:5432/dms_saas`
- `DJANGO_SECRET_KEY` — generate: `.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(50))"`
- `DJANGO_ALLOWED_HOSTS=DOMAIN,127.0.0.1,localhost`; `CORS_ALLOWED_ORIGINS` / `CSRF_TRUSTED_ORIGINS` /
  `PUBLIC_BASE_URL` / `FRONTEND_BASE_URL` → `https://DOMAIN`
- `REDIS_URL=redis://127.0.0.1:6379/2` (db 2 by convention; any free db works)
- Leave `FCM_CREDENTIALS_FILE` empty unless you have a Firebase service-account JSON (push
  notifications). If you have one, put it OUTSIDE the web root, chmod 600, and point this at it.
- Email starts on the console backend; production email is configured later from
  Admin → Settings → Messaging (Microsoft Graph), not in `.env`.

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_cities          # 103 Pakistani cities for the city pickers
.venv/bin/python manage.py publish_legal privacy  # serves /legal/privacy — app stores require it
```

Decide with the owner, then run ONE of:
- `.venv/bin/python manage.py seed_demo` — platform admin + plans + a demo tenant full of sample data
  (`--admin-user/--admin-password` to override the defaults; CHANGE the default password on a real host), or
- `.venv/bin/python manage.py seed_demo --admin-user <name> --admin-password <strong>` and then delete or
  reset the demo tenant from the platform admin if only the platform shell is wanted.

Check: `.venv/bin/python manage.py check --deploy` has no errors (warnings are reviewable);
`.venv/bin/python -m pytest tests/ -q` is green (takes ~15 min; run it — it is the proof the host works).

## Phase 4 — Frontend

```bash
cd /var/www/dms/frontend && npm ci && npm run build
```

Check: `frontend/dist/index.html` exists.

## Phase 5 — Services and web

Edit before installing: `deploy/nginx-dms.conf` (replace the `server_name` and certificate paths with
DOMAIN), and read the three unit files once so you know what they run.

```bash
cd /var/www/dms
chown -R django:www-data /var/www/dms && chmod 600 backend/.env
./deploy/deploy.sh        # installs units + nginx site, migrates, builds, restarts, reloads
certbot --nginx -d DOMAIN # after DNS for DOMAIN points at this host
```

`deploy.sh` is also the ONLY update path from now on: every code change ships with
`cd /var/www/dms && ./deploy/deploy.sh`. It ensures the permission catalog — skipping it after adding
permissions breaks role saves silently.

Check: `systemctl is-active gunicorn-dms celery-dms celery-beat-dms` → three times `active`;
`curl -s https://DOMAIN/api/v1/branding | head -c 100` returns JSON; the SPA loads in a browser;
`curl -s https://DOMAIN/legal/privacy` renders.

## Phase 6 — Hardening and jobs

```bash
ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw --force enable
./deploy/security/install-security.sh   # fail2ban jails (bans are PERMANENT — see docs/DEPLOY.md), dms-secops
crontab -l | { cat; echo "15 2 * * * /var/www/dms/deploy/backup.sh"; } | crontab -
```

Check: `fail2ban-client status` lists the dms jails; run `deploy/backup.sh` once and see a dated dump in
`/var/backups/dms`. Also restore it once into a scratch database — a backup nobody has restored is a hope,
not a backup.

## Phase 7 — First login and tenant setup

1. Log in at `https://DOMAIN` as the platform admin → create the real tenant(s) from Admin → Tenants
   (the create wizard asks for company, plan, owner login).
2. As the tenant owner: Settings → Warehouses (first one becomes default), Users & Roles, Customers,
   and import the product price list (Products → Import price list, .xlsx).
3. Tenant business rules live in Settings → Business rules (order proximity, photo rules, claim
   settlement mode…). Platform-wide settings (branding, email, map tiles) in Admin → Settings.
4. If a mobile AI / app team will integrate: issue mailbox tokens with
   `manage.py agent_mail token --name <who> --role mobile` (shown once) and hand over `mobile-app.md`.

## Traps that have bitten before (do not rediscover them)

- **`all_objects` is not tenant-scoped.** In any management script, filter by `tenant_id` yourself.
- **`tenant_context` runs in autocommit.** Wrap destructive one-off scripts in `transaction.atomic()`.
- **Never `timezone.now().date()`** — always `timezone.localdate()` (`TIME_ZONE` is Asia/Karachi).
- **GL balances read `entry__status__in=GL_LIVE`** ("posted","reversed") — never filter `="posted"`.
- **Tests as root once wrote into live `media/`** — `config/settings_test.py` now isolates MEDIA_ROOT;
  keep it that way.
- The SPA catch-all answers 200 with index.html for ANY unknown path — a 200 is not proof an endpoint
  exists; check the response body.
- nginx `client_max_body_size` is large (APK uploads); if you trim it, platform Admin → Mobile app breaks.
- Snap Chromium (if installed for headless checks) cannot read `/tmp`; serve files over `127.0.0.1` instead.

## What NOT to do

- Do not run two instances against one database, and do not point a second host at a restored copy while
  the first still runs — numbering sequences and Celery beat will fight.
- Do not commit `.env`, anything in `firebase/`, or `.github/workflows/` additions with secrets.
- Do not hand the platform admin password to a tenant; tenants get owner logins, nothing more.
