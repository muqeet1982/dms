# Deployment guide — dms.invoiceflow.pk

Production runs natively on the Ubuntu 24.04 VPS (no Docker): nginx → gunicorn (unix socket) → Django; Celery worker + beat; PostgreSQL 16; Redis; Let's Encrypt via certbot.

## Layout

```
/var/www/dms
  backend/            Django project (.venv, .env, media/, staticfiles/)
  frontend/           React app (dist/ is what nginx serves)
  deploy/             systemd units, nginx site, deploy.sh, backup.sh, restore.sh
  docs/               this documentation
  mobile-app.md       mobile integration guide
```

| Service | Unit | Notes |
| --- | --- | --- |
| API | `gunicorn-dms.service` | socket `/run/gunicorn/dms.sock`, 3 workers × 2 threads |
| Worker | `celery-dms.service` | webhooks, imports, notifications |
| Scheduler | `celery-beat-dms.service` | subscription status/reminders, stock cache recompute, alerts, reservations, reconciliation, webhook retries |
| Web | nginx site `dms` | SPA from `frontend/dist`, `/api`, `/ext`, `/media`, `/static` proxied |
| TLS | certbot timer | cert at `/etc/letsencrypt/live/dms.invoiceflow.pk/`, auto-renews |
| Backup | cron 02:15 daily | `deploy/backup.sh` → `/var/backups/dms` (30 days) |

## First-time setup (already done on this host)

```bash
sudo -u postgres psql -c "CREATE ROLE dms_saas LOGIN PASSWORD '…' CREATEDB;" -c "CREATE DATABASE dms_saas OWNER dms_saas;"
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
cp backend/.env.example backend/.env   # then edit
(cd backend && .venv/bin/python manage.py migrate && .venv/bin/python manage.py seed_demo)
(cd backend && .venv/bin/python manage.py publish_legal privacy)   # /legal/privacy — the app store needs it
(cd frontend && npm ci && npm run build)
./deploy/deploy.sh                      # installs units + nginx site, restarts everything
```

## Updating

```bash
cd /var/www/dms && ./deploy/deploy.sh   # pip install, migrate, collectstatic, npm build, restart services, reload nginx
```

## Environment (`backend/.env`)

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL`, `REDIS_URL` | connections |
| `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0`, `DJANGO_ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS` | security |
| `PUBLIC_BASE_URL`, `FRONTEND_BASE_URL` | links in emails/invites |
| `CELERY_TASK_ALWAYS_EAGER=0` | run tasks in the worker (set 1 to run inline without a worker) |
| `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL` | email (console backend until SMTP is configured) |
| `SMS_PROVIDER=http`, `SMS_HTTP_URL`, `SMS_HTTP_TOKEN` | SMS gateway (POST `{to, text}` with bearer token) |
| `FCM_CREDENTIALS_FILE` | Firebase service-account JSON path for push notifications |
| `USE_S3=1`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `S3_ENDPOINT_URL` | object storage (MinIO/S3) instead of local media |
| `LOG_LEVEL` | logging |

## Operations

- Logs: `journalctl -u gunicorn-dms -f`, `journalctl -u celery-dms -f`, `/var/log/nginx/dms_error.log`.
- Health: `/api/v1/platform/health` (platform admin) — DB size, queue length, failed webhooks, scheduled jobs.
- Backups: `deploy/backup.sh` (set `OFFSITE=user@host:/path` in the crontab line to rsync off-site); restore with `deploy/restore.sh <dump>`; test a restore monthly into a staging DB.
- Re-seed demo data: `cd backend && .venv/bin/python manage.py seed_demo --reset`.
- Publish or update the privacy policy: edit `backend/apps/platform/legal/privacy.md`, bump the version line,
  then `cd backend && .venv/bin/python manage.py publish_legal privacy`. Without it `/legal/privacy` is empty,
  and Android refuses an app that asks for background location without a reachable policy.
- Tests: `cd backend && .venv/bin/python -m pytest -q`.
- Regenerate OpenAPI types for the frontend: `cd frontend && npm run gen:api`.

## Security checklist

HTTPS only (nginx redirects 80 → 443, HSTS on in production settings), Argon2 passwords, login lockout, rotating refresh tokens with reuse detection, API keys hashed, rate limits on auth/OTP/`/ext`, CORS allow-list, `.env` mode 600, tenant isolation tests, append-only audit log, nightly reconciliation job that alerts the owner.
