# Changelog

## 1.0.0 — 2026-09-22

Phases 0–8 of the specification delivered in one pass on `dms.invoiceflow.pk`.

- **Foundation**: Django 5.2 / DRF / PostgreSQL 16 / Redis / Celery; custom `User`, `TenantModel` with compile-time tenant filtering, permission catalogue, audit log, numbering, attachments, notifications (in-app + FCM/email/SMS abstractions), OpenAPI at `/api/docs/`, pytest suite (isolation, accounting integrity, order-to-invoice, external API).
- **Platform admin**: plans (versioned, grandfathered), add-ons, coupons, tenants + owner creation, suspend/reactivate/cancel/archive, impersonation, usage, subscriptions (renew, upgrade/downgrade with pro-rata + limit check, trial extend/convert), invoices (PDF) & payments & refunds, outstanding/ageing, announcements, message templates, feature flags (incl. branding), platform roles/users, settings, health, support tickets, audit.
- **Tenant setup & masters**: company profile & business rules, branding (flag-gated), users/roles builder (internal + external), warehouses/bins, suppliers + reps with logins, customers/territories/routes, catalogue (variants, packaging, barcodes, internal barcode generation), price lists with slabs, schemes, tax types/rates/classes, vehicles, drivers, CSV imports/exports.
- **Inventory**: append-only stock ledger, cached levels, batches/expiry, opening stock, transfers (request → approve → dispatch → receive), adjustments with reason codes/approval limits, stock counts with variance posting, alerts & reorder suggestions → draft PO, barcode resolve/scan lookup, label PDFs, valuation & GL reconciliation, nightly drift check.
- **Purchasing**: PO → GRN (scan, batch, expiry, damaged/short, landed cost) → bill (two taxes, PPV) → payment; purchase returns and supplier debit notes.
- **Sales & delivery**: orders (staff, rep portal, `/ext/v1`, API keys), inbox with confirm/hold/reject, credit & stock checks, reservations, pick lists, trips (assign, load, start = stock out, close with returns/damage/cash reconciliation), delivery notes with POD, mark-delivered, invoice per delivery, credit notes/returns, receipts, cheques, rep collections & settlements.
- **Accounting**: 4-level COA template, posting rules, auto journals for every document, manual journals, reversals, periods & year-end, cash/bank books, bank reconciliation, expenses with approval, opening balances, financial reports (TB, GL, P&L, BS, cash flow, ageing, tax summary, cash with reps, inventory vs GL).
- **Reports & dashboards**: report registry with JSON/CSV/XLSX/PDF, saved filters, schedules; owner, accounts, rep and platform dashboards.
- **Frontend**: React 18 + TypeScript + Vite on the SmartHR template; tenant app (`/app`), rep portal (`/rep`, mobile-first), platform admin (`/admin`); HID barcode scanning; verified with a headless-browser smoke test over 127 pages and end-to-end write flows.
- **Mobile**: `mobile-app.md` integration guide for the React Native app.
