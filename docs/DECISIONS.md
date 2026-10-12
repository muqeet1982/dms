# Decisions where the spec was silent or left a choice

| # | Topic | Decision | Why |
| --- | --- | --- | --- |
| 1 | Hosting | Native services on the VPS (systemd gunicorn + celery worker/beat, nginx, PostgreSQL 16, Redis), Let's Encrypt cert already on the host. Docker Compose files are provided in `docker/` for other environments but are **not** used on `dms.invoiceflow.pk`. | The server already runs the stack natively; Docker is not installed and memory is limited (4 GB). |
| 2 | Platform audit log | Reuses `core.AuditLog` with `tenant=NULL` and `platform.*` action names instead of a separate `PlatformAuditLog` table. | One append-only log, one query path; exposed at `/api/v1/platform/audit`. |
| 3 | Tenant resolution | `X-Tenant-ID` header (subdomains not used). A user with exactly one membership needs no header. Platform admins act inside a tenant only through an impersonation session. | Simplest for one domain + mobile clients. |
| 4 | Tenant filtering | `TenantManager` adds `tenant_id = CurrentTenantId()` where the value is resolved at SQL-compile time from a contextvar. | Class-level ViewSet querysets and subqueries are always scoped to the request's tenant; no `.none()` traps. |
| 5 | Permissions | Catalogue codenames (`module.action`) stored as Django `Permission` rows on a dummy `core.Module` content type; Groups per tenant via `GroupProfile`. Owner = all internal permissions implicitly. | Matches §2.3 without 300 auto-generated model permissions. |
| 6 | Stock cost | Weighted average per variant tenant-wide, quantized to 4 dp; outgoing movements always valued at the current average from the DB. Quarantine inflows at zero cost do not affect the average. | §7.1; avoids stale-instance valuation drift. |
| 7 | Dispatch = stock out | One-step by default: trip start writes `sale_dispatch` movements from the warehouse; COGS posts at invoice. `Inventory valuation vs GL` report shows "dispatched, not yet invoiced" as a reconciling item. Two-step (van warehouse + Stock in Transit) is a tenant setting. | §6.3 / §8.3 open question resolved as recommended. |
| 8 | Van warehouses | Created automatically per vehicle (`VAN-<reg>`) when two-step dispatch is on; a shared `TRANSIT` van warehouse (no vehicle) routes inter-warehouse transfers through Stock in Transit. | §15 recommendation (per-vehicle). |
| 9 | Credit policy | Tenant setting `credit_policy`: `warn` (default) puts the order `on_hold_credit` for approval; `hard` refuses confirmation. Blocked customers are always refused. | §15 open question. |
| 10 | External prices | Tenant setting `accept_external_prices`: `flag` (default, use price list and record differences in `price_flags`), `price_list`, `accept`. | §9.3. |
| 11 | Free-goods COGS | COGS is posted on paid **and** free quantities (both leave stock). | Keeps stock valuation = GL. |
| 12 | Invoicing | Exactly one invoice per delivery note; "Mark delivered" on an order creates DN + stock-out (if needed) + invoice in one transaction. | §6.3 point 8. |
| 13 | Payments | `Payment` is immutable (cancel = reverse journal + undo allocations). FIFO allocation when none given. Cheques: pending → deposited → cleared/bounced with journals per step. | §6.4. |
| 14 | Idempotency | `Idempotency-Key` stored per (tenant, user, key, path) with the original response; replay returns it with `Idempotent-Replayed: true`. Required for the mobile offline queue. | §9.1. |
| 15 | Trailing slashes | All routes accept both forms. | Mobile clients. |
| 16 | Realtime | Polling (`/me/notifications/unread-count` every 30 s). Django Channels deferred. | §9.4 allows fallback polling; keeps memory low. |
| 17 | PDF | WeasyPrint for invoices, challans, trip sheets, vouchers, statements, labels, reports. | Host has pango libs. |
| 18 | Reports | `ReportRegistry` (`apps/reports/registry.py`); every report returns `{columns, rows, totals}` and is exportable as CSV/XLSX/PDF through one endpoint. | §12. |
| 19 | Frontend data layer | Hand-written TS types + a thin fetch client; `npm run gen:api` generates full OpenAPI types when needed. Plain tables with template markup instead of TanStack Table. | Pixel-faithful to the template; less indirection. |
| 20 | Template assets | The SmartHR/DreamsPOS CSS, icon fonts and images are copied into `frontend/public/assets` from the existing digiinvoice static folder; jQuery/Bootstrap JS behaviours are re-implemented in React (sidebar, dropdowns, modals). | Same look; no jQuery. |
| 21 | Seeding | `manage.py seed_demo [--reset]` creates the platform admin (`admin`/`pak1stan`), plans, roles and a full demo tenant with a week of transactions. | §14 working method 3 + user request. |
| 22 | FBR / e-invoicing | Not implemented; tax registration numbers are stored per tenant/customer/supplier and each tax type posts to its own account so a filing report is straightforward later. | §15 open question. |
| 23 | Manufacturer rebates | Not tracked in v1 (an "Scheme/Incentive from Manufacturer" income account exists for manual journals). | §15 open question. |
