/**
 * Sidebar menu definitions per shell. `page` = a key from the permission matrix (the entry shows only when that
 * page's View switch is on — see apps/accounts/page_permissions.py); `perm` = any of these codenames (older style);
 * `feature` = tenant feature flag. A parent with children shows when at least one child does.
 */
export type MenuItem = { label: string; to?: string; icon?: string; page?: string; perm?: string | string[]; feature?: string; children?: MenuItem[]; badgeKey?: string; end?: boolean };
export type MenuSection = { title: string; items: MenuItem[] };

export const TENANT_MENU: MenuSection[] = [
  { title: "MAIN MENU", items: [{ label: "Dashboard", to: "/app", end: true, icon: "ti-smart-home" }, { label: "Notifications", to: "/app/notifications", icon: "ti-bell", badgeKey: "unread" }] },
  {
    title: "SALES", items: [
      { label: "Sales", icon: "ti-shopping-cart-dollar", children: [
        { label: "Orders inbox", to: "/app/sales/inbox", page: "sales_inbox", badgeKey: "inbox" }, { label: "Sales orders", to: "/app/sales/orders", page: "sales_orders" },
        { label: "Invoices", to: "/app/sales/invoices", page: "invoices" }, { label: "Credit notes / returns", to: "/app/sales/credit-notes", page: "credit_notes" },
        { label: "Rep collections", to: "/app/sales/rep-collections", page: "rep_collections" }, { label: "Rep settlements", to: "/app/sales/rep-settlements", page: "rep_settlements" },
      ] },
      { label: "Customers", icon: "ti-users-group", children: [
        { label: "All customers", to: "/app/customers", end: true, page: "customers" }, { label: "Territories", to: "/app/customers/territories", page: "territories" }, { label: "Routes", to: "/app/customers/routes", page: "territories" }, { label: "Store locations", to: "/app/customers/stores", page: "customers" }, { label: "Working areas", to: "/app/customers/areas", page: "geofences" }, { label: "Check-ins", to: "/app/tracking/visits", page: "visits" },
      ] },
    ],
  },
  {
    title: "DELIVERY", items: [
      { label: "Dispatch", icon: "ti-truck-delivery", children: [
        { label: "Load forms", to: "/app/delivery/pick-lists", page: "load_forms" }, { label: "Dispatches (trips)", to: "/app/delivery/trips", page: "trips" }, { label: "Delivery notes", to: "/app/delivery/notes", page: "delivery_notes" },
        { label: "Vehicles", to: "/app/delivery/vehicles", page: "vehicles" }, { label: "Drivers", to: "/app/delivery/drivers", page: "drivers" },
      ] },
    ],
  },
  {
    title: "INVENTORY", items: [
      { label: "Products", icon: "ti-package", children: [
        { label: "All products", to: "/app/catalog/products", page: "products" }, { label: "Categories", to: "/app/catalog/categories", page: "categories" }, { label: "Brands", to: "/app/catalog/brands", page: "brands" }, { label: "Units", to: "/app/catalog/uoms", page: "uoms" },
        { label: "Price lists", to: "/app/catalog/price-lists", page: "price_lists" }, { label: "Schemes & discounts", to: "/app/catalog/schemes", page: "schemes" }, { label: "Barcode labels", to: "/app/catalog/labels", page: "labels" },
      ] },
      { label: "Stock", icon: "ti-chart-arrows", children: [
        { label: "Stock levels", to: "/app/inventory/stock", page: "stock" }, { label: "Scan lookup", to: "/app/inventory/lookup", page: "stock" }, { label: "Stock ledger", to: "/app/inventory/movements", page: "movements" }, { label: "Batches & expiry", to: "/app/inventory/batches", page: "batches" },
        { label: "Transfers", to: "/app/inventory/transfers", page: "transfers" }, { label: "Adjustments & wastage", to: "/app/inventory/adjustments", page: "adjustments" }, { label: "Stock counts", to: "/app/inventory/counts", page: "counts" },
        { label: "Alerts & reorder", to: "/app/inventory/alerts", page: "stock_alerts" }, { label: "Valuation", to: "/app/inventory/valuation", page: "valuation" },
      ] },
      { label: "Purchasing", icon: "ti-shopping-bag", perm: "purchasing.view_purchase_order", children: [
        { label: "Purchase orders", to: "/app/purchasing/orders", page: "purchase_orders" }, { label: "Goods receipts (GRN)", to: "/app/purchasing/grns", page: "grns" }, { label: "Purchase bills", to: "/app/purchasing/bills", page: "bills" }, { label: "Purchase returns", to: "/app/purchasing/returns", page: "purchase_returns" },
      ] },
      { label: "Suppliers", icon: "ti-building-factory-2", children: [{ label: "All suppliers", to: "/app/suppliers", end: true, page: "suppliers" }, { label: "Supplier reps", to: "/app/suppliers/reps", page: "supplier_reps" }] },
    ],
  },
  {
    title: "ACCOUNTS", items: [
      { label: "Accounting", icon: "ti-report-money", children: [
        { label: "Accounts", to: "/app/accounting", end: true, page: "payments" }, { label: "Chart of accounts", to: "/app/accounting/accounts", page: "accounts" }, { label: "Journals", to: "/app/accounting/journals", page: "journals" },
        { label: "Opening balances", to: "/app/accounting/opening-balances", page: "opening_balances" }, { label: "Periods & year end", to: "/app/accounting/periods", page: "periods" },
        { label: "Transactions", to: "/app/accounting/payments", page: "payments" }, { label: "Cheques", to: "/app/accounting/cheques", page: "cheques" }, { label: "Cheque books", to: "/app/accounting/cheque-books", page: "cheque_books" }, { label: "Expenses", to: "/app/accounting/expenses", page: "expenses" }, { label: "Expense categories", to: "/app/accounting/expense-categories", page: "accounts" },
        { label: "Cash & bank", to: "/app/accounting/bank", page: "bank_cash" }, { label: "Bank reconciliation", to: "/app/accounting/reconciliation", page: "reconciliation" },
        { label: "General ledger", to: "/app/accounting/general-ledger", page: "reports" }, { label: "Trial balance", to: "/app/accounting/trial-balance", page: "reports" }, { label: "Income statement", to: "/app/accounting/income-statement", page: "reports" }, { label: "Balance sheet", to: "/app/accounting/balance-sheet", page: "reports" },
      ] },
      { label: "Reports", icon: "ti-chart-bar", to: "/app/reports", page: "reports" },
    ],
  },
  {
    title: "ADMINISTRATION", items: [
      { label: "Users Management", icon: "ti-user-shield", children: [{ label: "Users", to: "/app/users", end: true, page: "users" }, { label: "Roles", to: "/app/users/roles", page: "roles" }, { label: "Activity log", to: "/app/users/activity", page: "activity" }] },
      { label: "Settings", icon: "ti-settings", children: [
        { label: "Company profile", to: "/app/settings/company", page: "settings_company" }, { label: "Branding", to: "/app/settings/branding", page: "settings_branding", feature: "branding" },
        { label: "Warehouses & bins", to: "/app/settings/warehouses", page: "settings_warehouses" }, { label: "Locations (racks/bins)", to: "/app/settings/locations", page: "settings_locations" }, { label: "Taxes", to: "/app/settings/taxes", page: "settings_taxes" },
        { label: "Document numbering", to: "/app/settings/numbering", page: "settings_numbering" }, { label: "Posting rules", to: "/app/settings/posting-rules", page: "settings_posting_rules" },
        { label: "Reason codes", to: "/app/settings/reason-codes", page: "settings_reason_codes" }, { label: "Notifications", to: "/app/settings/notifications" },
        { label: "API keys", to: "/app/settings/api-keys", page: "settings_api_keys" }, { label: "Webhooks", to: "/app/settings/webhooks", page: "settings_webhooks" },
        { label: "Imports & exports", to: "/app/settings/imports", page: "settings_imports" }, { label: "Subscription", to: "/app/settings/subscription", page: "settings_subscription" }, { label: "Support", to: "/app/settings/support", page: "settings_support" },
      ] },
    ],
  },
];

export const PLATFORM_MENU: MenuSection[] = [
  { title: "PLATFORM", items: [{ label: "Dashboard", to: "/admin", end: true, icon: "ti-smart-home" }, { label: "Tenants", to: "/admin/tenants", icon: "ti-building" }, { label: "Subscriptions", to: "/admin/subscriptions", icon: "ti-refresh" }, { label: "Plans & add-ons", to: "/admin/plans", icon: "ti-stack-2" }] },
  { title: "TENANT BILLING", items: [{ label: "Subscription invoices", to: "/admin/invoices", icon: "ti-file-invoice" }, { label: "Payments received", to: "/admin/payments", icon: "ti-cash" }, { label: "Outstanding", to: "/admin/outstanding", icon: "ti-alert-circle" }] },
  { title: "COMMUNICATION", items: [{ label: "Announcements", to: "/admin/announcements", icon: "ti-speakerphone" }, { label: "Message templates", to: "/admin/templates", icon: "ti-template" }, { label: "Support tickets", to: "/admin/tickets", icon: "ti-lifebuoy" }, { label: "Notification log", to: "/admin/notification-log", icon: "ti-mail" }] },
  { title: "SYSTEM", items: [{ label: "Feature flags", to: "/admin/feature-flags", icon: "ti-flag" }, { label: "Banned IPs", to: "/admin/security", icon: "ti-shield-lock" }, { label: "Firewall", to: "/admin/firewall", icon: "ti-shield-check" }, { label: "Mobile app (APK)", to: "/admin/mobile-app", icon: "ti-device-mobile" }, { label: "Platform users", to: "/admin/users", icon: "ti-user-shield" }, { label: "Settings", to: "/admin/settings", icon: "ti-settings" }, { label: "Legal documents", to: "/admin/legal", icon: "ti-gavel" }, { label: "Audit log", to: "/admin/audit", icon: "ti-history" }, { label: "Health", to: "/admin/health", icon: "ti-heartbeat" }] },
];

export const REP_MENU: MenuSection[] = [
  { title: "REP PORTAL", items: [
    { label: "Home", to: "/rep", end: true, icon: "ti-smart-home" }, { label: "My customers", to: "/rep/customers", icon: "ti-users", perm: "external.view_assigned_customers" }, { label: "New order", to: "/rep/orders/new", icon: "ti-shopping-cart-plus", perm: "external.submit_order" },
    { label: "Check in", to: "/rep/check-in", icon: "ti-map-pin-check" }, { label: "My orders", to: "/rep/orders", end: true, icon: "ti-clipboard-list", perm: "external.view_own_orders" }, { label: "Deliveries", to: "/rep/deliveries", icon: "ti-truck", perm: "external.view_delivery_status" },
    { label: "Collections", to: "/rep/collections", icon: "ti-cash", perm: ["external.record_collection", "external.view_own_collections"] }, { label: "Stock", to: "/rep/stock", icon: "ti-box", perm: ["external.view_stock_exact", "external.view_stock_bands"] },
    { label: "Notifications", to: "/rep/notifications", icon: "ti-bell", badgeKey: "unread" },
  ] },
];

/**
 * The other side of the house. AppShell appends whichever of these is not the current shell, and only for the
 * few logins that hold both an office role and a rep record (`sidesOf` in app/guards.tsx) — for everyone else
 * the menu is unchanged.
 */
export const SIDE_SWITCH: Record<"office" | "rep", MenuSection> = {
  office: { title: "OFFICE", items: [{ label: "Office app", to: "/app", end: true, icon: "ti-building" }] },
  rep: { title: "FIELD", items: [{ label: "Rep view", to: "/rep", end: true, icon: "ti-device-mobile" }] },
};
