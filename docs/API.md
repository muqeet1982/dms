
# API reference

| Methods | Path |
| --- | --- |
| GET | `/api/v1/app/{platform}` |
| GET | `/api/v1/app/{platform}/download` |
| GET | `/api/v1/app/{platform}/qr.png` |
| GET | `/.well-known/assetlinks.json` |
| GET | `/api/v1/branding` |
| POST | `/api/v1/auth/login` |
| POST | `/api/v1/auth/refresh` |
| POST | `/api/v1/auth/logout` |
| POST | `/api/v1/auth/qr-login/create` |
| GET | `/api/v1/auth/qr-login/status` |
| POST | `/api/v1/auth/qr-login/redeem` |
| POST | `/api/v1/auth/otp/request` |
| POST | `/api/v1/auth/otp/verify` |
| POST | `/api/v1/auth/password/reset` |
| POST | `/api/v1/auth/password/change` |
| GET, PATCH | `/api/v1/me` |
| GET | `/api/v1/me/permissions` |
| GET | `/api/v1/permissions-catalog` |
| GET | `/api/v1/permission-pages` |
| GET | `/api/v1/me/sessions` |
| POST | `/api/v1/me/sessions/revoke-all` |
| GET | `/api/v1/me/sessions/{pk}` |
| POST | `/api/v1/me/sessions/{pk}/revoke` |
| GET, POST | `/api/v1/roles` |
| GET, PUT, PATCH, DELETE | `/api/v1/roles/{pk}` |
| POST | `/api/v1/roles/{pk}/clone` |
| GET, POST | `/api/v1/users` |
| POST | `/api/v1/users/transfer-ownership` |
| GET, PUT, PATCH, DELETE | `/api/v1/users/{pk}` |
| POST | `/api/v1/users/{pk}/activate` |
| GET | `/api/v1/users/{pk}/activity` |
| POST | `/api/v1/users/{pk}/resend-invite` |
| POST | `/api/v1/users/{pk}/reset-password` |
| POST | `/api/v1/users/{pk}/revoke-sessions` |
| GET | `/api/v1/` |
| GET, PUT | `/api/v1/me/notification-preferences` |
| GET | `/api/v1/history/{model}/{object_id}` |
| GET, POST, PATCH | `/api/v1/settings/company` |
| GET, POST, PATCH | `/api/v1/settings/company/logo` |
| GET, POST, PATCH | `/api/v1/settings/branding` |
| GET, POST, PATCH | `/api/v1/settings/branding/asset` |
| GET | `/api/v1/subscription` |
| GET | `/api/v1/announcements` |
| GET | `/api/v1/cities` |
| GET, POST | `/api/v1/attachments` |
| GET, DELETE | `/api/v1/attachments/{pk}` |
| GET | `/api/v1/audit-log` |
| GET | `/api/v1/audit-log/{pk}` |
| GET | `/api/v1/me/notifications` |
| POST | `/api/v1/me/notifications/read-all` |
| GET | `/api/v1/me/notifications/unread-count` |
| GET | `/api/v1/me/notifications/{pk}` |
| POST | `/api/v1/me/notifications/{pk}/read` |
| GET, POST | `/api/v1/me/devices` |
| GET, PUT, PATCH, DELETE | `/api/v1/me/devices/{pk}` |
| GET, POST | `/api/v1/settings/numbering` |
| GET, PUT, PATCH, DELETE | `/api/v1/settings/numbering/{pk}` |
| GET, POST | `/api/v1/support-tickets` |
| GET, PUT, PATCH, DELETE | `/api/v1/support-tickets/{pk}` |
| POST | `/api/v1/support-tickets/{pk}/reply` |
| GET | `/api/v1/platform/dashboard` |
| GET, POST, PATCH | `/api/v1/platform/settings` |
| GET, POST, PATCH | `/api/v1/platform/settings/asset` |
| GET | `/api/v1/platform/security/status` |
| GET, POST | `/api/v1/platform/security/bans` |
| GET, POST | `/api/v1/platform/security/firewall` |
| GET | `/api/v1/platform/health` |
| GET, POST | `/api/v1/platform/plans` |
| GET, PUT, PATCH, DELETE | `/api/v1/platform/plans/{pk}` |
| GET | `/api/v1/platform/plans/{pk}/versions` |
| GET, POST | `/api/v1/platform/addons` |
| GET, PUT, PATCH, DELETE | `/api/v1/platform/addons/{pk}` |
| GET, POST | `/api/v1/platform/coupons` |
| GET, PUT, PATCH, DELETE | `/api/v1/platform/coupons/{pk}` |
| GET, POST | `/api/v1/platform/tenants` |
| POST | `/api/v1/platform/tenants/end-impersonation` |
| GET, PUT, PATCH, DELETE | `/api/v1/platform/tenants/{pk}` |
| POST | `/api/v1/platform/tenants/{pk}/archive` |
| GET | `/api/v1/platform/tenants/{pk}/audit` |
| POST | `/api/v1/platform/tenants/{pk}/cancel` |
| GET | `/api/v1/platform/tenants/{pk}/export` |
| POST | `/api/v1/platform/tenants/{pk}/impersonate` |
| POST | `/api/v1/platform/tenants/{pk}/reactivate` |
| POST | `/api/v1/platform/tenants/{pk}/reset-owner-password` |
| POST | `/api/v1/platform/tenants/{pk}/revoke-sessions` |
| POST | `/api/v1/platform/tenants/{pk}/set-feature` |
| GET | `/api/v1/platform/tenants/{pk}/subscriptions` |
| POST | `/api/v1/platform/tenants/{pk}/suspend` |
| GET | `/api/v1/platform/tenants/{pk}/timeline` |
| POST | `/api/v1/platform/tenants/{pk}/unlock-owner` |
| GET | `/api/v1/platform/tenants/{pk}/usage` |
| GET, POST | `/api/v1/platform/subscriptions` |
| GET, PUT, PATCH, DELETE | `/api/v1/platform/subscriptions/{pk}` |
| POST | `/api/v1/platform/subscriptions/{pk}/addons` |
| POST | `/api/v1/platform/subscriptions/{pk}/change-plan` |
| POST | `/api/v1/platform/subscriptions/{pk}/change-plan/preview` |
| POST | `/api/v1/platform/subscriptions/{pk}/convert-trial` |
| POST, DELETE | `/api/v1/platform/subscriptions/{pk}/discount` |
| GET | `/api/v1/platform/subscriptions/{pk}/events` |
| POST | `/api/v1/platform/subscriptions/{pk}/extend-trial` |
| GET | `/api/v1/platform/subscriptions/{pk}/price` |
| POST | `/api/v1/platform/subscriptions/{pk}/renew` |
| GET, POST | `/api/v1/platform/invoices` |
| GET | `/api/v1/platform/invoices/outstanding` |
| GET, PUT, PATCH, DELETE | `/api/v1/platform/invoices/{pk}` |
| GET | `/api/v1/platform/invoices/{pk}/pdf` |
| POST | `/api/v1/platform/invoices/{pk}/void` |
| GET, POST | `/api/v1/platform/payments` |
| GET | `/api/v1/platform/payments/export` |
| GET, PUT, PATCH, DELETE | `/api/v1/platform/payments/{pk}` |
| POST | `/api/v1/platform/payments/{pk}/refund` |
| GET, POST | `/api/v1/platform/announcements` |
| GET, PUT, PATCH, DELETE | `/api/v1/platform/announcements/{pk}` |
| GET, POST | `/api/v1/platform/templates` |
| GET, PUT, PATCH, DELETE | `/api/v1/platform/templates/{pk}` |
| POST | `/api/v1/platform/templates/{pk}/preview` |
| GET | `/api/v1/platform/notification-log` |
| GET | `/api/v1/platform/notification-log/{pk}` |
| GET, POST | `/api/v1/platform/feature-flags` |
| GET, PUT, PATCH, DELETE | `/api/v1/platform/feature-flags/{pk}` |
| GET, POST | `/api/v1/platform/legal` |
| GET, PUT, PATCH, DELETE | `/api/v1/platform/legal/{pk}` |
| GET, POST | `/api/v1/platform/tickets` |
| GET, PUT, PATCH, DELETE | `/api/v1/platform/tickets/{pk}` |
| POST | `/api/v1/platform/tickets/{pk}/reply` |
| GET | `/api/v1/platform/audit` |
| GET | `/api/v1/platform/audit/{pk}` |
| GET, POST | `/api/v1/platform/users` |
| GET | `/api/v1/platform/users/permissions-catalog` |
| GET | `/api/v1/platform/users/roles` |
| GET, PUT, PATCH, DELETE | `/api/v1/platform/users/{pk}` |
| GET, POST | `/api/v1/platform/app-releases` |
| GET, PATCH | `/api/v1/platform/app-releases/config` |
| POST | `/api/v1/platform/app-releases/purge-old` |
| GET | `/api/v1/platform/app-releases/storage` |
| GET, PUT, PATCH, DELETE | `/api/v1/platform/app-releases/{pk}` |
| POST | `/api/v1/platform/app-releases/{pk}/set-current` |
| GET | `/api/v1/platform/` |
| POST | `/api/v1/labels/print` |
| GET, POST | `/api/v1/products` |
| GET | `/api/v1/products/stats` |
| GET, PUT, PATCH, DELETE | `/api/v1/products/{pk}` |
| POST | `/api/v1/products/{pk}/variants` |
| GET, POST | `/api/v1/variants` |
| GET, PUT, PATCH, DELETE | `/api/v1/variants/{pk}` |
| POST | `/api/v1/variants/{pk}/barcodes` |
| POST | `/api/v1/variants/{pk}/generate-barcode` |
| POST | `/api/v1/variants/{pk}/packagings` |
| GET | `/api/v1/variants/{pk}/price` |
| GET, POST | `/api/v1/packagings` |
| GET, PUT, PATCH, DELETE | `/api/v1/packagings/{pk}` |
| GET, POST | `/api/v1/barcodes` |
| GET | `/api/v1/barcodes/resolve` |
| GET, PUT, PATCH, DELETE | `/api/v1/barcodes/{pk}` |
| GET, POST | `/api/v1/categories` |
| GET, PUT, PATCH, DELETE | `/api/v1/categories/{pk}` |
| GET, POST | `/api/v1/brands` |
| GET, PUT, PATCH, DELETE | `/api/v1/brands/{pk}` |
| GET, POST | `/api/v1/uoms` |
| GET, PUT, PATCH, DELETE | `/api/v1/uoms/{pk}` |
| GET, POST | `/api/v1/price-lists` |
| GET, PUT, PATCH, DELETE | `/api/v1/price-lists/{pk}` |
| DELETE | `/api/v1/price-lists/{pk}/items/{item_id}` |
| GET, POST | `/api/v1/price-lists/{pk}/items` |
| GET, POST | `/api/v1/schemes` |
| GET, PUT, PATCH, DELETE | `/api/v1/schemes/{pk}` |
| GET, POST | `/api/v1/settings/tax-types` |
| GET, PUT, PATCH, DELETE | `/api/v1/settings/tax-types/{pk}` |
| DELETE | `/api/v1/settings/tax-types/{pk}/rates/{rate_id}` |
| POST | `/api/v1/settings/tax-types/{pk}/rates` |
| GET, POST | `/api/v1/settings/tax-classes` |
| GET, PUT, PATCH, DELETE | `/api/v1/settings/tax-classes/{pk}` |
| GET, POST | `/api/v1/suppliers` |
| GET, PUT, PATCH, DELETE | `/api/v1/suppliers/{pk}` |
| GET | `/api/v1/suppliers/{pk}/bills` |
| GET | `/api/v1/suppliers/{pk}/ledger` |
| GET, POST | `/api/v1/suppliers/{pk}/reps` |
| GET, POST | `/api/v1/supplier-reps` |
| GET, PUT, PATCH, DELETE | `/api/v1/supplier-reps/{pk}` |
| GET | `/api/v1/supplier-reps/{pk}/ledger` |
| GET | `/api/v1/supplier-reps/{pk}/performance` |
| GET, POST | `/api/v1/customers` |
| GET | `/api/v1/customers/stats` |
| GET, PUT, PATCH, DELETE | `/api/v1/customers/{pk}` |
| POST | `/api/v1/customers/{pk}/approve` |
| POST | `/api/v1/customers/{pk}/block` |
| GET | `/api/v1/customers/{pk}/credit-check` |
| GET | `/api/v1/customers/{pk}/ledger` |
| GET | `/api/v1/customers/{pk}/orders` |
| GET | `/api/v1/customers/{pk}/statement` |
| POST | `/api/v1/customers/{pk}/unblock` |
| GET, POST | `/api/v1/territories` |
| GET, PUT, PATCH, DELETE | `/api/v1/territories/{pk}` |
| GET, POST | `/api/v1/routes` |
| GET, PUT, PATCH, DELETE | `/api/v1/routes/{pk}` |
| GET | `/api/v1/inventory/valuation` |
| POST | `/api/v1/inventory/opening-stock` |
| GET, POST | `/api/v1/settings/warehouses` |
| GET, PUT, PATCH, DELETE | `/api/v1/settings/warehouses/{pk}` |
| POST | `/api/v1/settings/warehouses/{pk}/bins` |
| POST | `/api/v1/settings/warehouses/{pk}/set-default` |
| GET, POST | `/api/v1/settings/bins` |
| GET, PUT, PATCH, DELETE | `/api/v1/settings/bins/{pk}` |
| GET, POST | `/api/v1/settings/reason-codes` |
| GET, PUT, PATCH, DELETE | `/api/v1/settings/reason-codes/{pk}` |
| GET | `/api/v1/inventory/stock` |
| GET | `/api/v1/inventory/stock/fefo` |
| GET | `/api/v1/inventory/stock/lookup` |
| GET | `/api/v1/inventory/stock/summary` |
| GET | `/api/v1/inventory/stock/{pk}` |
| GET | `/api/v1/inventory/movements` |
| GET | `/api/v1/inventory/movements/{pk}` |
| GET, POST | `/api/v1/inventory/batches` |
| GET, PUT, PATCH, DELETE | `/api/v1/inventory/batches/{pk}` |
| GET, POST | `/api/v1/inventory/transfers` |
| GET, PUT, PATCH, DELETE | `/api/v1/inventory/transfers/{pk}` |
| POST | `/api/v1/inventory/transfers/{pk}/approve` |
| POST | `/api/v1/inventory/transfers/{pk}/cancel` |
| POST | `/api/v1/inventory/transfers/{pk}/dispatch` |
| POST | `/api/v1/inventory/transfers/{pk}/receive` |
| GET, POST | `/api/v1/inventory/adjustments` |
| GET | `/api/v1/inventory/adjustments/stats` |
| GET, PUT, PATCH, DELETE | `/api/v1/inventory/adjustments/{pk}` |
| POST | `/api/v1/inventory/adjustments/{pk}/approve` |
| POST | `/api/v1/inventory/adjustments/{pk}/reject` |
| POST | `/api/v1/inventory/adjustments/{pk}/submit` |
| GET, POST | `/api/v1/inventory/counts` |
| GET, PUT, PATCH, DELETE | `/api/v1/inventory/counts/{pk}` |
| POST | `/api/v1/inventory/counts/{pk}/cancel` |
| POST | `/api/v1/inventory/counts/{pk}/post` |
| POST | `/api/v1/inventory/counts/{pk}/record` |
| POST | `/api/v1/inventory/counts/{pk}/review` |
| GET | `/api/v1/inventory/counts/{pk}/variances` |
| GET | `/api/v1/inventory/alerts` |
| GET | `/api/v1/inventory/alerts/counts` |
| POST | `/api/v1/inventory/alerts/refresh` |
| GET | `/api/v1/inventory/alerts/reorder-suggestions` |
| GET, POST | `/api/v1/purchase-orders` |
| POST | `/api/v1/purchase-orders/from-reorder` |
| GET | `/api/v1/purchase-orders/stats` |
| GET, PUT, PATCH, DELETE | `/api/v1/purchase-orders/{pk}` |
| POST | `/api/v1/purchase-orders/{pk}/cancel` |
| GET | `/api/v1/purchase-orders/{pk}/pdf` |
| POST | `/api/v1/purchase-orders/{pk}/send` |
| GET, POST | `/api/v1/goods-receipts` |
| GET, PUT, PATCH, DELETE | `/api/v1/goods-receipts/{pk}` |
| POST | `/api/v1/goods-receipts/{pk}/cancel` |
| GET | `/api/v1/goods-receipts/{pk}/pdf` |
| POST | `/api/v1/goods-receipts/{pk}/post` |
| POST | `/api/v1/goods-receipts/{pk}/scans` |
| GET, POST | `/api/v1/purchase-bills` |
| GET, PUT, PATCH, DELETE | `/api/v1/purchase-bills/{pk}` |
| POST | `/api/v1/purchase-bills/{pk}/cancel` |
| GET | `/api/v1/purchase-bills/{pk}/payments` |
| POST | `/api/v1/purchase-bills/{pk}/post` |
| GET, POST | `/api/v1/purchase-returns` |
| GET, PUT, PATCH, DELETE | `/api/v1/purchase-returns/{pk}` |
| POST | `/api/v1/purchase-returns/{pk}/post` |
| GET, POST | `/api/v1/sales-orders` |
| GET | `/api/v1/sales-orders/inbox` |
| GET | `/api/v1/sales-orders/stats` |
| GET, PUT, PATCH, DELETE | `/api/v1/sales-orders/{pk}` |
| POST | `/api/v1/sales-orders/{pk}/allocate` |
| GET | `/api/v1/sales-orders/{pk}/availability` |
| POST | `/api/v1/sales-orders/{pk}/cancel` |
| POST | `/api/v1/sales-orders/{pk}/confirm` |
| POST | `/api/v1/sales-orders/{pk}/hold` |
| POST | `/api/v1/sales-orders/{pk}/invoice` |
| POST | `/api/v1/sales-orders/{pk}/load-form` |
| POST | `/api/v1/sales-orders/{pk}/mark-delivered` |
| GET | `/api/v1/sales-orders/{pk}/pdf` |
| POST | `/api/v1/sales-orders/{pk}/reject` |
| POST | `/api/v1/sales-orders/{pk}/review` |
| POST | `/api/v1/sales-orders/{pk}/submit` |
| GET | `/api/v1/sales-orders/{pk}/workflow` |
| GET | `/api/v1/invoices` |
| GET | `/api/v1/invoices/stats` |
| GET | `/api/v1/invoices/{pk}` |
| POST | `/api/v1/invoices/{pk}/cancel` |
| POST | `/api/v1/invoices/{pk}/discount` |
| GET | `/api/v1/invoices/{pk}/payments` |
| GET | `/api/v1/invoices/{pk}/pdf` |
| GET, POST | `/api/v1/credit-notes` |
| GET, PUT, PATCH, DELETE | `/api/v1/credit-notes/{pk}` |
| POST | `/api/v1/credit-notes/{pk}/post` |
| GET, POST | `/api/v1/rep-collections` |
| GET | `/api/v1/rep-collections/unsettled` |
| GET, PUT, PATCH, DELETE | `/api/v1/rep-collections/{pk}` |
| GET, POST | `/api/v1/rep-settlements` |
| GET, PUT, PATCH, DELETE | `/api/v1/rep-settlements/{pk}` |
| GET, POST | `/api/v1/vehicles` |
| GET, PUT, PATCH, DELETE | `/api/v1/vehicles/{pk}` |
| GET | `/api/v1/vehicles/{pk}/logs` |
| GET, POST | `/api/v1/drivers` |
| GET, PUT, PATCH, DELETE | `/api/v1/drivers/{pk}` |
| GET, POST | `/api/v1/pick-lists` |
| GET, PUT, PATCH, DELETE | `/api/v1/pick-lists/{pk}` |
| POST | `/api/v1/pick-lists/{pk}/complete` |
| POST | `/api/v1/pick-lists/{pk}/create-dispatch` |
| POST | `/api/v1/pick-lists/{pk}/issue` |
| GET | `/api/v1/pick-lists/{pk}/pdf` |
| POST | `/api/v1/pick-lists/{pk}/picks` |
| POST | `/api/v1/pick-lists/{pk}/scans` |
| POST | `/api/v1/pick-lists/{pk}/send` |
| GET, POST | `/api/v1/trips` |
| GET | `/api/v1/trips/stats` |
| GET, PUT, PATCH, DELETE | `/api/v1/trips/{pk}` |
| POST | `/api/v1/trips/{pk}/assign-orders` |
| POST | `/api/v1/trips/{pk}/cancel` |
| POST | `/api/v1/trips/{pk}/close` |
| GET | `/api/v1/trips/{pk}/dispatch-sheet` |
| POST | `/api/v1/trips/{pk}/load` |
| GET | `/api/v1/trips/{pk}/reconciliation` |
| POST | `/api/v1/trips/{pk}/remove-order` |
| POST | `/api/v1/trips/{pk}/reorder-stops` |
| POST | `/api/v1/trips/{pk}/start` |
| GET | `/api/v1/trips/{pk}/trip-sheet` |
| GET | `/api/v1/delivery-notes` |
| GET | `/api/v1/delivery-notes/{pk}` |
| POST | `/api/v1/delivery-notes/{pk}/deliver` |
| GET | `/api/v1/delivery-notes/{pk}/pdf` |
| GET | `/api/v1/accounting/dashboard` |
| GET, POST | `/api/v1/accounting/accounts` |
| GET | `/api/v1/accounting/accounts/tree` |
| GET, PUT, PATCH, DELETE | `/api/v1/accounting/accounts/{pk}` |
| GET | `/api/v1/accounting/accounts/{pk}/ledger` |
| GET, POST | `/api/v1/accounting/journals` |
| GET, PUT, PATCH, DELETE | `/api/v1/accounting/journals/{pk}` |
| POST | `/api/v1/accounting/journals/{pk}/post` |
| GET | `/api/v1/accounting/journals/{pk}/print` |
| POST | `/api/v1/accounting/journals/{pk}/reverse` |
| GET, POST | `/api/v1/accounting/fiscal-years` |
| GET, PUT, PATCH, DELETE | `/api/v1/accounting/fiscal-years/{pk}` |
| POST | `/api/v1/accounting/fiscal-years/{pk}/close` |
| GET | `/api/v1/accounting/periods` |
| GET | `/api/v1/accounting/periods/{pk}` |
| GET | `/api/v1/accounting/periods/{pk}/checks` |
| POST | `/api/v1/accounting/periods/{pk}/close` |
| POST | `/api/v1/accounting/periods/{pk}/reopen` |
| GET, POST | `/api/v1/accounting/cost-centres` |
| GET, PUT, PATCH, DELETE | `/api/v1/accounting/cost-centres/{pk}` |
| GET, POST | `/api/v1/accounting/bank-accounts` |
| GET, PUT, PATCH, DELETE | `/api/v1/accounting/bank-accounts/{pk}` |
| POST | `/api/v1/accounting/bank-accounts/{pk}/statement/import` |
| POST | `/api/v1/accounting/bank-accounts/{pk}/statement/{line_id}/match` |
| GET | `/api/v1/accounting/bank-accounts/{pk}/reconciliation` |
| GET | `/api/v1/accounting/bank-accounts/{pk}/statement` |
| GET, POST | `/api/v1/accounting/cash-accounts` |
| GET, PUT, PATCH, DELETE | `/api/v1/accounting/cash-accounts/{pk}` |
| GET, POST | `/api/v1/accounting/payments` |
| GET | `/api/v1/accounting/payments/cheques` |
| GET | `/api/v1/accounting/payments/open-documents` |
| GET, PUT, PATCH, DELETE | `/api/v1/accounting/payments/{pk}` |
| POST | `/api/v1/accounting/payments/{pk}/allocate` |
| POST | `/api/v1/accounting/payments/{pk}/cancel` |
| POST | `/api/v1/accounting/payments/{pk}/cheque/{event}` |
| GET | `/api/v1/accounting/payments/{pk}/print` |
| GET, POST | `/api/v1/accounting/expenses` |
| GET, PUT, PATCH, DELETE | `/api/v1/accounting/expenses/{pk}` |
| POST | `/api/v1/accounting/expenses/{pk}/approve` |
| POST | `/api/v1/accounting/expenses/{pk}/pay` |
| POST | `/api/v1/accounting/expenses/{pk}/reject` |
| POST | `/api/v1/accounting/expenses/{pk}/submit` |
| GET, POST | `/api/v1/accounting/opening-balances` |
| GET, PUT, PATCH, DELETE | `/api/v1/accounting/opening-balances/{pk}` |
| POST | `/api/v1/accounting/opening-balances/{pk}/post` |
| GET, POST | `/api/v1/settings/posting-rules` |
| GET, PUT, PATCH, DELETE | `/api/v1/settings/posting-rules/{pk}` |
| GET, POST | `/api/v1/reports/saved-filters` |
| GET, PUT, PATCH, DELETE | `/api/v1/reports/saved-filters/{pk}` |
| GET, POST | `/api/v1/reports/schedules` |
| GET, PUT, PATCH, DELETE | `/api/v1/reports/schedules/{pk}` |
| GET | `/api/v1/dashboard` |
| GET | `/api/v1/reports` |
| GET | `/api/v1/reports/{key}` |
| GET | `/api/v1/exports/{kind}` |
| GET, POST | `/api/v1/api-keys` |
| GET | `/api/v1/api-keys/scopes` |
| GET, PUT, PATCH, DELETE | `/api/v1/api-keys/{pk}` |
| POST | `/api/v1/api-keys/{pk}/rotate` |
| GET | `/api/v1/api-keys/{pk}/usage` |
| GET, POST | `/api/v1/webhooks` |
| GET | `/api/v1/webhooks/events` |
| GET, PUT, PATCH, DELETE | `/api/v1/webhooks/{pk}` |
| GET | `/api/v1/webhooks/{pk}/deliveries` |
| POST | `/api/v1/webhooks/{pk}/deliveries/{delivery_id}/retry` |
| POST | `/api/v1/webhooks/{pk}/test` |
| GET, POST | `/api/v1/imports` |
| GET | `/api/v1/imports/template/{kind}` |
| GET | `/api/v1/imports/{pk}` |
| GET | `/ext/v1/me` |
| GET, POST | `/ext/v1/orders` |
| GET | `/ext/v1/orders/{pk}` |
| POST | `/ext/v1/orders/{pk}/cancel` |
| GET, POST | `/ext/v1/customers` |
| GET | `/ext/v1/products` |
| GET | `/ext/v1/stock` |
| GET | `/ext/v1/deliveries` |
| GET, POST | `/ext/v1/collections` |
| GET, POST | `/ext/v1/webhooks` |
