import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { PLATFORM_MENU, REP_MENU, TENANT_MENU } from "@/components/layout/menu";
import { HomeRedirect, RequireAuth, RequirePage } from "./guards";
import { ChangePasswordPage, ForgotPasswordPage, LoginPage, ResetPasswordPage, SelectCompanyPage } from "@/features/auth/pages";
import { LegalPublicPage } from "@/features/platform/LegalPublicPage";
import { DashboardPage } from "@/features/dashboard/DashboardPage";
import { OrderDetailPage, OrderFormPage, OrdersInboxPage, OrdersPage } from "@/features/sales/OrdersPages";
import { CreditNoteDetailPage, CreditNoteFormPage, CreditNotesPage, InvoiceDetailPage, InvoicesPage, RepCollectionsPage, RepSettlementsPage } from "@/features/sales/InvoicePages";
import { CompanyClaimsPage, CompanyClaimDetailPage } from "@/features/sales/CompanyClaimsPage";
import { CustomerDetailPage, CustomerFormPage, CustomersPage, RoutesPage, TerritoriesPage } from "@/features/partners/CustomerPages";
import { CoverageMapPage, GeofenceDetailPage, GeofenceFormPage, GeofencesPage } from "@/features/partners/GeofencePages";
import { StoresPage } from "@/features/partners/StorePages";
import { VisitsPage } from "@/features/partners/VisitsPage";
import { SupplierDetailPage, SupplierFormPage, SupplierRepDetailPage, SupplierRepsPage, SuppliersPage } from "@/features/partners/SupplierPages";
import { DeliveryNoteDetailPage, DeliveryNotesPage, DriversPage, PickListDetailPage, PickListFormPage, PickListsPage, TripDetailPage, TripFormPage, TripsPage, VehiclesPage } from "@/features/delivery/DeliveryPages";
import { BrandsPage, CategoriesPage, LabelsPage, PriceListsPage, ProductDetailPage, ProductFormPage, ProductsPage, SchemesPage, UomsPage, LocationsPage } from "@/features/catalog/CatalogPages";
import { AdjustmentDetailPage, AdjustmentFormPage, AdjustmentsPage, AlertsPage, BatchesPage, CountDetailPage, CountFormPage, CountsPage, MovementsPage, StockLookupPage, StockPage, TransferDetailPage, TransferFormPage, TransfersPage, ValuationPage } from "@/features/inventory/InventoryPages";
import { BillDetailPage, BillFormPage, BillsPage, GrnDetailPage, GrnFormPage, GrnsPage, PurchaseOrderDetailPage, PurchaseOrderFormPage, PurchaseOrdersPage, PurchaseReturnDetailPage, PurchaseReturnFormPage, PurchaseReturnsPage } from "@/features/purchasing/PurchasingPages";
import { AccountLedgerPage, AccountingDashboardPage, AccountsPage, BankCashPage, ChequeBooksPage, ChequesPage, ExpenseDetailPage, ExpenseFormPage, ExpensesPage, JournalDetailPage, JournalFormPage, JournalsPage, OpeningBalancesPage, PaymentDetailPage, PaymentFormPage, PaymentsPage, PeriodsPage, ReconciliationPage } from "@/features/accounting/AccountingPages";
import { ReportViewPage, ReportsPage } from "@/features/reports/ReportsPages";
import { BalanceSheetPage, GeneralLedgerPage, IncomeStatementPage, TrialBalancePage } from "@/features/accounting/FinancialPages";
import { ExpenseCategoriesPage } from "@/features/accounting/ExpenseCategoriesPage";
import { NotificationsPage } from "@/features/notifications/NotificationsPage";
import { ActivityLogPage, ProfilePage, RoleFormPage, RolesPage, UserFormPage, UsersPage } from "@/features/users/UsersPages";
import { ApiKeysPage, BrandingPage, CompanySettingsPage, DiscountContributionsPage, ImportsPage, NotificationSettingsPage, NumberingPage, PostingRulesPage, ReasonCodesPage, SubscriptionPage, SupportPage, TaxesPage, WarehousesPage, WebhooksPage } from "@/features/settings/SettingsPages";
import { RepCollectionsPage as RepPortalCollectionsPage, RepCustomerDetailPage, RepCustomersPage, RepDeliveriesPage, RepHomePage, RepNewOrderPage, RepOrderDetailPage, RepOrdersPage, RepStockPage, RepCheckInPage } from "@/features/rep/RepPages";
import { MobileAppPage } from "@/features/platform/MobileAppPage";
import { GetAppPage } from "@/features/platform/GetAppPage";
import { AppLoginPage } from "@/features/platform/AppLoginPage";
import { AnnouncementsPage, FeatureFlagsPage, HealthPage, LegalPage, NotificationLogPage, OutstandingPage, PlansPage, PlatformAuditPage, PlatformDashboardPage, PlatformInvoicesPage, PlatformPaymentsPage,  PlatformUsersPage, SubscriptionsPage, TemplatesPage, TenantDetailPage, TenantsPage, TicketsPage } from "@/features/platform/PlatformPages";
import { PlatformSettingsPage } from "@/features/platform/PlatformSettingsPage";
import { TenantCreatePage } from "@/features/platform/TenantCreatePage";
import { FirewallPage, SecurityBansPage } from "@/features/platform/SecurityPages";

function NotFound() {
  return <div className="text-center py-5"><i className="ti ti-error-404 fs-1 text-muted" /><h5 className="mt-2">Page not found</h5></div>;
}

export const router = createBrowserRouter([
  { path: "/", element: <HomeRedirect /> },
  { path: "/login", element: <LoginPage /> },
  { path: "/forgot-password", element: <ForgotPasswordPage /> },
  { path: "/reset-password", element: <ResetPasswordPage /> },
  { path: "/accept-invite", element: <ResetPasswordPage purpose="invite" title="Accept invitation" /> },
  { path: "/change-password", element: <ChangePasswordPage /> },
  { path: "/select-company", element: <SelectCompanyPage /> },
  { path: "/get-app", element: <GetAppPage /> },
  { path: "/legal/:kind", element: <LegalPublicPage /> },
  { path: "/app-login", element: <AppLoginPage /> },
  {
    element: <RequireAuth mode="tenant" />,
    children: [{
      path: "/app", element: <AppShell sections={TENANT_MENU} />,
      children: [
        { index: true, element: <DashboardPage /> }, { path: "notifications", element: <NotificationsPage /> }, { path: "profile", element: <ProfilePage /> },
        { path: "sales/inbox", element: <RequirePage page="sales_inbox"><OrdersInboxPage /></RequirePage> },
        { path: "sales/orders", element: <RequirePage page="sales_orders"><OrdersPage /></RequirePage> }, { path: "sales/orders/new", element: <RequirePage page="sales_orders" action="add"><OrderFormPage /></RequirePage> },
        { path: "sales/orders/:id", element: <RequirePage page="sales_orders"><OrderDetailPage /></RequirePage> }, { path: "sales/orders/:id/edit", element: <RequirePage page="sales_orders" action="edit"><OrderFormPage /></RequirePage> },
        { path: "sales/invoices", element: <RequirePage page="invoices"><InvoicesPage /></RequirePage> }, { path: "sales/invoices/:id", element: <RequirePage page="invoices"><InvoiceDetailPage /></RequirePage> },
        { path: "sales/credit-notes", element: <RequirePage page="credit_notes"><CreditNotesPage /></RequirePage> }, { path: "sales/credit-notes/new", element: <RequirePage page="credit_notes" action="add"><CreditNoteFormPage /></RequirePage> }, { path: "sales/credit-notes/:id", element: <RequirePage page="credit_notes"><CreditNoteDetailPage /></RequirePage> },
        { path: "sales/company-claims", element: <RequirePage page="company_claims"><CompanyClaimsPage /></RequirePage> }, { path: "sales/company-claims/:id", element: <RequirePage page="company_claims"><CompanyClaimDetailPage /></RequirePage> },
        { path: "sales/rep-collections", element: <RequirePage page="rep_collections"><RepCollectionsPage /></RequirePage> }, { path: "sales/rep-settlements", element: <RequirePage page="rep_settlements"><RepSettlementsPage /></RequirePage> },
        { path: "customers", element: <RequirePage page="customers"><CustomersPage /></RequirePage> }, { path: "customers/new", element: <RequirePage page="customers" action="add"><CustomerFormPage /></RequirePage> },
        { path: "customers/territories", element: <RequirePage page="territories"><TerritoriesPage /></RequirePage> }, { path: "customers/routes", element: <RequirePage page="territories"><RoutesPage /></RequirePage> },
        { path: "customers/stores", element: <RequirePage page="customers"><StoresPage /></RequirePage> }, { path: "tracking/visits", element: <RequirePage page="visits"><VisitsPage /></RequirePage> },
        { path: "customers/areas", element: <RequirePage page="geofences"><GeofencesPage /></RequirePage> }, { path: "customers/areas/map", element: <RequirePage page="geofences"><CoverageMapPage /></RequirePage> }, { path: "customers/areas/new", element: <RequirePage page="geofences" action="add"><GeofenceFormPage /></RequirePage> }, { path: "customers/areas/:id", element: <RequirePage page="geofences"><GeofenceDetailPage /></RequirePage> }, { path: "customers/areas/:id/edit", element: <RequirePage page="geofences" action="edit"><GeofenceFormPage /></RequirePage> },
        { path: "customers/:id", element: <RequirePage page="customers"><CustomerDetailPage /></RequirePage> }, { path: "customers/:id/edit", element: <RequirePage page="customers" action="edit"><CustomerFormPage /></RequirePage> },
        { path: "suppliers", element: <RequirePage page="suppliers"><SuppliersPage /></RequirePage> }, { path: "suppliers/new", element: <RequirePage page="suppliers" action="add"><SupplierFormPage /></RequirePage> },
        { path: "suppliers/reps", element: <RequirePage page="supplier_reps"><SupplierRepsPage /></RequirePage> }, { path: "suppliers/reps/:id", element: <RequirePage page="supplier_reps"><SupplierRepDetailPage /></RequirePage> },
        { path: "suppliers/:id", element: <RequirePage page="suppliers"><SupplierDetailPage /></RequirePage> }, { path: "suppliers/:id/edit", element: <RequirePage page="suppliers" action="edit"><SupplierFormPage /></RequirePage> },
        { path: "delivery/trips", element: <RequirePage page="trips"><TripsPage /></RequirePage> }, { path: "delivery/trips/new", element: <RequirePage page="trips" action="add"><TripFormPage /></RequirePage> }, { path: "delivery/trips/:id", element: <RequirePage page="trips"><TripDetailPage /></RequirePage> }, { path: "delivery/notes", element: <RequirePage page="delivery_notes"><DeliveryNotesPage /></RequirePage> }, { path: "delivery/notes/:id", element: <RequirePage page="delivery_notes"><DeliveryNoteDetailPage /></RequirePage> },
        { path: "delivery/pick-lists", element: <RequirePage page="load_forms"><PickListsPage /></RequirePage> }, { path: "delivery/pick-lists/new", element: <RequirePage page="load_forms" action="add"><PickListFormPage /></RequirePage> }, { path: "delivery/pick-lists/:id", element: <RequirePage page="load_forms"><PickListDetailPage /></RequirePage> }, { path: "delivery/vehicles", element: <RequirePage page="vehicles"><VehiclesPage /></RequirePage> }, { path: "delivery/drivers", element: <RequirePage page="drivers"><DriversPage /></RequirePage> },
        { path: "catalog/products", element: <RequirePage page="products"><ProductsPage /></RequirePage> }, { path: "catalog/products/new", element: <RequirePage page="products" action="add"><ProductFormPage /></RequirePage> },
        { path: "catalog/products/:id", element: <RequirePage page="products"><ProductDetailPage /></RequirePage> }, { path: "catalog/products/:id/edit", element: <RequirePage page="products" action="edit"><ProductFormPage /></RequirePage> },
        { path: "catalog/categories", element: <RequirePage page="categories"><CategoriesPage /></RequirePage> }, { path: "catalog/brands", element: <RequirePage page="brands"><BrandsPage /></RequirePage> }, { path: "catalog/uoms", element: <RequirePage page="uoms"><UomsPage /></RequirePage> },
        { path: "catalog/price-lists", element: <RequirePage page="price_lists"><PriceListsPage /></RequirePage> }, { path: "catalog/schemes", element: <RequirePage page="schemes"><SchemesPage /></RequirePage> }, { path: "catalog/labels", element: <RequirePage page="labels"><LabelsPage /></RequirePage> },
        { path: "inventory/stock", element: <RequirePage page="stock"><StockPage /></RequirePage> }, { path: "inventory/lookup", element: <RequirePage page="stock"><StockLookupPage /></RequirePage> },
        { path: "inventory/movements", element: <RequirePage page="movements"><MovementsPage /></RequirePage> }, { path: "inventory/batches", element: <RequirePage page="batches"><BatchesPage /></RequirePage> },
        { path: "inventory/alerts", element: <RequirePage page="stock_alerts"><AlertsPage /></RequirePage> }, { path: "inventory/valuation", element: <RequirePage page="valuation"><ValuationPage /></RequirePage> },
        { path: "inventory/transfers", element: <RequirePage page="transfers"><TransfersPage /></RequirePage> }, { path: "inventory/transfers/new", element: <RequirePage page="transfers" action="add"><TransferFormPage /></RequirePage> }, { path: "inventory/transfers/:id", element: <RequirePage page="transfers"><TransferDetailPage /></RequirePage> },
        { path: "inventory/adjustments", element: <RequirePage page="adjustments"><AdjustmentsPage /></RequirePage> }, { path: "inventory/adjustments/new", element: <RequirePage page="adjustments" action="add"><AdjustmentFormPage /></RequirePage> }, { path: "inventory/adjustments/:id", element: <RequirePage page="adjustments"><AdjustmentDetailPage /></RequirePage> },
        { path: "inventory/counts", element: <RequirePage page="counts"><CountsPage /></RequirePage> }, { path: "inventory/counts/new", element: <RequirePage page="counts" action="add"><CountFormPage /></RequirePage> }, { path: "inventory/counts/:id", element: <RequirePage page="counts"><CountDetailPage /></RequirePage> },
        { path: "purchasing/orders", element: <RequirePage page="purchase_orders"><PurchaseOrdersPage /></RequirePage> }, { path: "purchasing/orders/new", element: <RequirePage page="purchase_orders" action="add"><PurchaseOrderFormPage /></RequirePage> }, { path: "purchasing/orders/:id", element: <RequirePage page="purchase_orders"><PurchaseOrderDetailPage /></RequirePage> }, { path: "purchasing/orders/:id/edit", element: <RequirePage page="purchase_orders" action="edit"><PurchaseOrderFormPage /></RequirePage> },
        { path: "purchasing/grns", element: <RequirePage page="grns"><GrnsPage /></RequirePage> }, { path: "purchasing/grns/new", element: <RequirePage page="grns" action="add"><GrnFormPage /></RequirePage> }, { path: "purchasing/grns/:id", element: <RequirePage page="grns"><GrnDetailPage /></RequirePage> }, { path: "purchasing/grns/:id/edit", element: <RequirePage page="grns" action="edit"><GrnFormPage /></RequirePage> }, { path: "purchasing/bills", element: <RequirePage page="bills"><BillsPage /></RequirePage> }, { path: "purchasing/bills/new", element: <RequirePage page="bills" action="add"><BillFormPage /></RequirePage> }, { path: "purchasing/bills/:id", element: <RequirePage page="bills"><BillDetailPage /></RequirePage> },
        { path: "purchasing/returns", element: <RequirePage page="purchase_returns"><PurchaseReturnsPage /></RequirePage> }, { path: "purchasing/returns/new", element: <RequirePage page="purchase_returns" action="add"><PurchaseReturnFormPage /></RequirePage> }, { path: "purchasing/returns/:id", element: <RequirePage page="purchase_returns"><PurchaseReturnDetailPage /></RequirePage> },
        { path: "accounting", element: <RequirePage page="payments"><AccountingDashboardPage /></RequirePage> }, { path: "accounting/accounts", element: <RequirePage page="accounts"><AccountsPage /></RequirePage> }, { path: "accounting/accounts/:id", element: <RequirePage page="accounts"><AccountLedgerPage /></RequirePage> }, { path: "accounting/journals", element: <RequirePage page="journals"><JournalsPage /></RequirePage> }, { path: "accounting/journals/new", element: <RequirePage page="journals" action="add"><JournalFormPage /></RequirePage> }, { path: "accounting/journals/:id", element: <RequirePage page="journals"><JournalDetailPage /></RequirePage> }, { path: "accounting/journals/:id/edit", element: <RequirePage page="journals" action="edit"><JournalFormPage /></RequirePage> },
        { path: "accounting/payments", element: <RequirePage page="payments"><PaymentsPage /></RequirePage> }, { path: "accounting/payments/new", element: <RequirePage page="payments" action="add"><PaymentFormPage /></RequirePage> }, { path: "accounting/payments/:id", element: <RequirePage page="payments"><PaymentDetailPage /></RequirePage> }, { path: "accounting/cheques", element: <RequirePage page="cheques"><ChequesPage /></RequirePage> },
        { path: "accounting/expenses", element: <RequirePage page="expenses"><ExpensesPage /></RequirePage> }, { path: "accounting/expenses/new", element: <RequirePage page="expenses" action="add"><ExpenseFormPage /></RequirePage> }, { path: "accounting/expenses/:id", element: <RequirePage page="expenses"><ExpenseDetailPage /></RequirePage> }, { path: "accounting/expenses/:id/edit", element: <RequirePage page="expenses" action="edit"><ExpenseFormPage /></RequirePage> },
        { path: "accounting/cheque-books", element: <RequirePage page="cheque_books"><ChequeBooksPage /></RequirePage> }, { path: "accounting/expense-categories", element: <RequirePage page="accounts"><ExpenseCategoriesPage /></RequirePage> }, { path: "accounting/trial-balance", element: <RequirePage page="reports"><TrialBalancePage /></RequirePage> }, { path: "accounting/general-ledger", element: <RequirePage page="reports"><GeneralLedgerPage /></RequirePage> }, { path: "accounting/income-statement", element: <RequirePage page="reports"><IncomeStatementPage /></RequirePage> }, { path: "accounting/balance-sheet", element: <RequirePage page="reports"><BalanceSheetPage /></RequirePage> }, { path: "accounting/bank", element: <RequirePage page="bank_cash"><BankCashPage /></RequirePage> }, { path: "accounting/reconciliation", element: <RequirePage page="reconciliation"><ReconciliationPage /></RequirePage> }, { path: "accounting/periods", element: <RequirePage page="periods"><PeriodsPage /></RequirePage> }, { path: "accounting/opening-balances", element: <RequirePage page="opening_balances"><OpeningBalancesPage /></RequirePage> },
        { path: "reports", element: <RequirePage page="reports"><ReportsPage /></RequirePage> }, { path: "reports/:key", element: <RequirePage page="reports"><ReportViewPage /></RequirePage> },
        { path: "users", element: <RequirePage page="users"><UsersPage /></RequirePage> }, { path: "users/new", element: <RequirePage page="users" action="add"><UserFormPage /></RequirePage> },
        { path: "users/roles", element: <RequirePage page="roles"><RolesPage /></RequirePage> }, { path: "users/roles/new", element: <RequirePage page="roles" action="add"><RoleFormPage /></RequirePage> }, { path: "users/roles/:id", element: <RequirePage page="roles"><RoleFormPage /></RequirePage> },
        { path: "users/activity", element: <RequirePage page="activity"><ActivityLogPage /></RequirePage> }, { path: "users/:id", element: <RequirePage page="users"><UserFormPage /></RequirePage> },
        { path: "settings/company", element: <RequirePage page="settings_company"><CompanySettingsPage /></RequirePage> }, { path: "settings/branding", element: <RequirePage page="settings_branding"><BrandingPage /></RequirePage> }, { path: "settings/warehouses", element: <RequirePage page="settings_warehouses"><WarehousesPage /></RequirePage> }, { path: "settings/taxes", element: <RequirePage page="settings_taxes"><TaxesPage /></RequirePage> }, { path: "settings/numbering", element: <RequirePage page="settings_numbering"><NumberingPage /></RequirePage> }, { path: "settings/posting-rules", element: <RequirePage page="settings_posting_rules"><PostingRulesPage /></RequirePage> }, { path: "settings/reason-codes", element: <RequirePage page="settings_reason_codes"><ReasonCodesPage /></RequirePage> },
        { path: "settings/discount-contributions", element: <RequirePage page="settings_discount_contributions"><DiscountContributionsPage /></RequirePage> },
      { path: "settings/locations", element: <RequirePage page="settings_locations"><LocationsPage /></RequirePage> },
        { path: "settings/notifications", element: <NotificationSettingsPage /> }, { path: "settings/api-keys", element: <RequirePage page="settings_api_keys"><ApiKeysPage /></RequirePage> }, { path: "settings/webhooks", element: <RequirePage page="settings_webhooks"><WebhooksPage /></RequirePage> }, { path: "settings/imports", element: <RequirePage page="settings_imports"><ImportsPage /></RequirePage> }, { path: "settings/subscription", element: <RequirePage page="settings_subscription"><SubscriptionPage /></RequirePage> }, { path: "settings/support", element: <RequirePage page="settings_support"><SupportPage /></RequirePage> },
        { path: "*", element: <NotFound /> },
      ],
    }],
  },
  {
    element: <RequireAuth mode="rep" />,
    children: [{
      path: "/rep", element: <AppShell sections={REP_MENU} notificationsTo="/rep/notifications" />,
      children: [
        { index: true, element: <RepHomePage /> }, { path: "customers", element: <RepCustomersPage /> }, { path: "customers/:id", element: <RepCustomerDetailPage /> }, { path: "orders", element: <RepOrdersPage /> }, { path: "orders/new", element: <RepNewOrderPage /> }, { path: "orders/:id", element: <RepOrderDetailPage /> },
        { path: "deliveries", element: <RepDeliveriesPage /> }, { path: "collections", element: <RepPortalCollectionsPage /> }, { path: "stock", element: <RepStockPage /> }, { path: "check-in", element: <RepCheckInPage /> }, { path: "notifications", element: <NotificationsPage /> }, { path: "profile", element: <ProfilePage /> }, { path: "*", element: <NotFound /> },
      ],
    }],
  },
  {
    element: <RequireAuth mode="platform" />,
    children: [{
      path: "/admin", element: <AppShell sections={PLATFORM_MENU} notificationsTo="/admin/notifications" />,
      children: [
        { index: true, element: <PlatformDashboardPage /> }, { path: "tenants", element: <TenantsPage /> }, { path: "tenants/new", element: <TenantCreatePage /> }, { path: "tenants/:id", element: <TenantDetailPage /> }, { path: "subscriptions", element: <SubscriptionsPage /> }, { path: "plans", element: <PlansPage /> },
        { path: "invoices", element: <PlatformInvoicesPage /> }, { path: "payments", element: <PlatformPaymentsPage /> }, { path: "outstanding", element: <OutstandingPage /> }, { path: "announcements", element: <AnnouncementsPage /> }, { path: "templates", element: <TemplatesPage /> }, { path: "tickets", element: <TicketsPage /> }, { path: "notification-log", element: <NotificationLogPage /> },
        { path: "security", element: <SecurityBansPage /> }, { path: "firewall", element: <FirewallPage /> }, { path: "feature-flags", element: <FeatureFlagsPage /> }, { path: "mobile-app", element: <MobileAppPage /> }, { path: "users", element: <RequirePage page="users"><PlatformUsersPage /></RequirePage> }, { path: "settings", element: <PlatformSettingsPage /> }, { path: "legal", element: <LegalPage /> }, { path: "audit", element: <PlatformAuditPage /> }, { path: "health", element: <HealthPage /> }, { path: "notifications", element: <NotificationsPage /> }, { path: "profile", element: <ProfilePage /> }, { path: "*", element: <NotFound /> },
      ],
    }],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]);
