# DMS — Distribution Management SaaS

Multi-tenant SaaS for city-level distributors (hair-colour and similar products): purchasing, inventory with barcodes and batches, rep-driven sales orders, trip-based delivery with proof of delivery, receivables, and full double-entry accounting with a 4-level chart of accounts. Built from `distribution-saas-spec.md`.

**Live**: https://dms.invoiceflow.pk · API docs: https://dms.invoiceflow.pk/api/docs/ · Mobile guide: [`mobile-app.md`](mobile-app.md)

| Login | Identifier | Password |
| --- | --- | --- |
| Platform admin (`/admin`) | `admin` | `pak1stan` |
| Company owner (`/app`) | `owner@demo.pk` | `owner123` |
| Manufacturer rep (`/rep`) | `rep1@colormax.pk` | `rep123` |
| Staff (coordinator / accounts / store / cashier) | `sales@demo.pk` `accounts@demo.pk` `store@demo.pk` `cashier@demo.pk` | `staff123` |
| Driver | `driver@demo.pk` | `driver123` |

Change these before inviting real users (Users & roles → user → Reset password; platform admin under `/admin/users`).

## Stack

Backend: Python 3.12, Django 5.2, DRF, PostgreSQL 16, Redis, Celery, WeasyPrint, drf-spectacular. Frontend: React 18, TypeScript, Vite, React Router, TanStack Query, Zustand, ApexCharts, on the SmartHR/DreamsPOS Bootstrap template (assets in `frontend/public/assets`). Deployment: nginx + gunicorn + systemd on the VPS (see `docs/DEPLOY.md`).

## Repository

```
backend/    Django project: config/, apps/{core,accounts,platform,catalog,partners,inventory,purchasing,sales,delivery,accounting,reports,integrations}, tests/
frontend/   React app: src/{app,api,lib,components,features}
deploy/     systemd units, nginx site, deploy/backup/restore scripts
docker/     optional Docker Compose for other environments
docs/       API.md, PERMISSIONS.md, DECISIONS.md, DEPLOY.md, CHANGELOG.md, mobile-app.md
```

## Run locally

```bash
# backend
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env    # set DATABASE_URL, DJANGO_DEBUG=1, CELERY_TASK_ALWAYS_EAGER=1
.venv/bin/python manage.py migrate && .venv/bin/python manage.py seed_demo
.venv/bin/python manage.py runserver 127.0.0.1:8765
# frontend (proxies /api to 8765)
cd frontend && npm install && npm run dev      # http://127.0.0.1:5173
# tests
cd backend && .venv/bin/python -m pytest -q
```

## Key design points

- Every tenant row carries `tenant_id`; the default manager filters by the current tenant at SQL-compile time; cross-tenant isolation is tested.
- Only services create `StockMovement` (append-only ledger) and `JournalEntry` (always balanced, DB-constrained); every document change is audited.
- Dispatch (trip start) removes stock and marks orders sold; one invoice per delivery note; reps collect cash into a clearing account until settled.
- Dynamic taxes: up to two per line, each posting to its own account; posting rules are remappable per tenant.
- Plans, limits and feature flags (incl. branding) are enforced server-side and exposed to the UI via `/api/v1/me`.
