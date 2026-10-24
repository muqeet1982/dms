"""
Page permissions — the single source of truth for "what can this role do on this page".

Every page of the app is one entry with up to four switches (`view`, `add`, `edit`, `delete`) plus the extras that
only make sense there (confirm an order, post a journal, reset a password…). The same registry is used three ways:

* the role editor renders it as a matrix of on/off switches (`pages_as_json`);
* `/me` answers what the signed-in user may do per page (`resolve_pages`), so the UI hides what is not allowed;
* each viewset takes its `perm_map` from it (`page_perms`), so the server enforces exactly what the matrix shows.

`legacy` keeps roles built before the matrix working: a list of coarse codenames grants every action on the page
(`delivery.manage_vehicles` ⇒ all four on Vehicles), and a dict grants only the actions named
(`{"delete": ["users.deactivate_user"]}`). `enforced` marks the pages already converted — the leak test in
`tests/test_permission_matrix.py` runs over those.
"""
from collections import OrderedDict

# Actions every page can have. A page only lists the ones that exist for it.
ACTIONS = ("view", "add", "edit", "delete")
ACTION_LABELS = {"view": "View", "add": "Add", "edit": "Edit", "delete": "Delete"}


def _p(module, action, thing):
    return f"{module}.{action}_{thing}"


def page(key, label, group, path, module, thing, *, endpoint=None, actions=ACTIONS, extras=(), legacy=(), detail_endpoint=None, enforced=False, note="", skip_probe=()):
    """One page. `thing` drives the codenames: view_<thing>, add_<thing>, change_<thing>, delete_<thing>."""
    verb = {"view": "view", "add": "add", "edit": "change", "delete": "delete"}
    return {
        "key": key,
        "label": label,
        "group": group,
        "path": path,
        "module": module,
        "endpoint": endpoint,
        "detail_endpoint": detail_endpoint,
        "actions": OrderedDict((a, _p(module, verb[a], thing)) for a in actions),
        "extras": [{"key": k, "label": lbl, "perm": perm if isinstance(perm, str) else list(perm)} for k, lbl, perm in extras],
        "legacy": dict(legacy) if isinstance(legacy, dict) else list(legacy),
        "enforced": enforced,
        "note": note,
        # actions whose endpoint is not this page's own (the leak probe cannot reach them here; they have their
        # own test instead) — e.g. an invoice is created from an order, not by POSTing to /invoices
        "skip_probe": list(skip_probe),
    }


PAGES = [
    # ── Users Management ────────────────────────────────────────────────────────────────────────────────────
    page("users", "Users", "Users Management", "/app/users", "users", "user",
         endpoint="/api/v1/users", enforced=True,
         extras=(("reset_password", "Reset passwords", "users.reset_password"),
                 ("manage_access", "Change roles & warehouse access", "users.change_user_access")),
         legacy={"delete": ["users.deactivate_user"], "edit": ["users.change_user"]},
         note="Delete suspends the account — user records are never erased, their documents stay."),
    page("roles", "Roles & permissions", "Users Management", "/app/users/roles", "users", "role",
         endpoint="/api/v1/roles", enforced=True, legacy=("users.manage_roles",)),
    page("activity", "Activity log", "Users Management", "/app/users/activity", "users", "activity",
         endpoint="/api/v1/audit-log", actions=("view",), enforced=True, legacy=("users.view_activity_log",)),

    # ── Sales ──────────────────────────────────────────────────────────────────────────────────────────────
    page("sales_orders", "Sales orders", "Sales", "/app/sales/orders", "sales", "sales_order",
         endpoint="/api/v1/sales-orders", enforced=True,
         extras=(("confirm", "Confirm orders", "sales.confirm_sales_order"),
                 ("hold", "Hold / reject orders", "sales.cancel_sales_order"),
                 ("edit_confirmed", "Edit after confirmation (before invoice) — owner only unless granted", "sales.edit_confirmed_order"),
                 ("cancel_confirmed", "Cancel after confirmation (before invoice) — owner only unless granted", "sales.cancel_confirmed_order"),
                 ("override_price", "Override prices", "sales.override_price"),
                 ("override_discount", "Override discounts", "sales.override_discount"),
                 ("approve_credit_hold", "Approve credit holds", "sales.approve_credit_hold")),
         legacy={"delete": ["sales.cancel_sales_order"]},
         note="Delete cancels the order; a confirmed order is cancelled, never removed."),
    page("sales_inbox", "Orders inbox", "Sales", "/app/sales/inbox", "sales", "order_inbox",
         endpoint="/api/v1/sales-orders/inbox", actions=("view",), enforced=True,
         extras=(("confirm", "Confirm orders", "sales.confirm_sales_order"),),
         note="The queue of orders waiting to be reviewed and confirmed."),
    page("company_claims", "Company discount claims", "Sales", "/app/sales/company-claims", "sales", "company_claim",
         endpoint="/api/v1/company-claims", actions=("view", "edit", "delete"), enforced=True, skip_probe=("edit", "delete"),
         extras=(("settle", "Record company payment / net-off", "sales.settle_company_claim"),),
         note="The manufacturer's share of discounts given. 'edit' settles a claim; 'delete' cancels one."),
    page("invoices", "Invoices", "Sales", "/app/sales/invoices", "sales", "invoice",
         endpoint="/api/v1/invoices", actions=("view", "add", "edit", "delete"), legacy={"view": ["sales.view_invoice"]}, enforced=True, skip_probe=("add", "edit", "delete"),
         extras=(("discount", "Change invoice discount", "sales.override_discount"),
                 ("edit_posted", "Edit a posted invoice (reopen, correct, re-post) — owner only unless granted", "sales.edit_posted_invoice")),
         note="Invoices come from deliveries; Add covers invoicing an issued order, Edit is the due date and the notes only (amounts are corrected with a credit note), Delete cancels a draft."),
    page("credit_notes", "Credit notes & returns", "Sales", "/app/sales/credit-notes", "sales", "credit_note",
         endpoint="/api/v1/credit-notes", legacy={"add": ["sales.add_credit_note"]}, enforced=True),
    page("rep_collections", "Rep collections", "Sales", "/app/sales/rep-collections", "cash", "rep_collection",
         endpoint="/api/v1/rep-collections", actions=("view", "add"), enforced=True,
         extras=(("settle", "Settle collections", "cash.settle_rep_collection"),)),
    page("rep_settlements", "Rep settlements", "Sales", "/app/sales/rep-settlements", "cash", "rep_settlement",
         endpoint="/api/v1/rep-settlements", actions=("view", "add"), enforced=True),

    # ── Customers ──────────────────────────────────────────────────────────────────────────────────────────
    page("customers", "Customers", "Customers", "/app/customers", "customers", "customer",
         endpoint="/api/v1/customers", enforced=True,
         extras=(("credit_limit", "Set credit limits", "customers.set_credit_limit"),
                 ("block", "Block customers", "customers.block_customer"),
                 ("ledger", "View ledger & outstanding", "customers.view_customer_ledger")),
         legacy=("customers.manage_customer",)),
    page("store_locations", "Store locations (map)", "Customers", "/app/customers/stores", "customers", "store_location",
         endpoint="/api/v1/customers/map", actions=("view", "edit"), enforced=True, skip_probe=("edit",),
         legacy=("customers.manage_customer",),
         note="The customer map: `view` opens it, `edit` drops or moves a store's pin."),
    page("territories", "Territories & routes", "Customers", "/app/customers/territories", "customers", "territory",
         endpoint="/api/v1/territories", enforced=True, legacy=("customers.manage_territories",)),
    page("visits", "Shop visits", "Customers", "/app/tracking/visits", "visits", "own",
         endpoint="/api/v1/visits", actions=("view",), enforced=False,
         extras=(("team", "Everyone's visits, not just my own", "visits.view_team"),),
         note="`view` is the person's own visits (visits.view_own); `extras.team` opens the office list."),
    page("tracking", "Rep tracking", "Delivery", "/app/tracking", "tracking", "own",
         endpoint="/api/v1/tracking/users", actions=("view", "edit"), enforced=False,
         extras=(("live", "Live map — where everyone is now", "tracking.view_team"),
                 ("team", "Anyone's route and the team's day", "tracking.view_team"),
                 ("policy", "Change the tracking policy", "tracking.manage_policy")),
         note="`view` is the person's own route (tracking.view_own); the extras open the office screens."),
    page("timesheets", "Rep shifts & timesheets", "Delivery", "/app/tracking/timesheets", "tracking", "timesheet",
         endpoint="/api/v1/shifts", actions=("view", "edit"), enforced=True,
         note="`view` opens the timesheet; `edit` lets the office correct a rep's times, with every change audited."),
    page("geofences", "Working areas (map)", "Customers", "/app/customers/areas", "customers", "geofence",
         endpoint="/api/v1/geofences", enforced=True,
         extras=(("assign", "Assign areas to reps", "customers.assign_geofence"),),
         legacy=("customers.manage_territories",),
         note="Draw the area a rep covers; their work is checked against it."),

    # ── Suppliers ──────────────────────────────────────────────────────────────────────────────────────────
    page("suppliers", "Suppliers", "Suppliers", "/app/suppliers", "suppliers", "supplier",
         endpoint="/api/v1/suppliers", enforced=True,
         extras=(("ledger", "View supplier ledger", "suppliers.view_supplier_ledger"),)),
    page("supplier_reps", "Supplier reps", "Suppliers", "/app/suppliers/reps", "suppliers", "supplier_rep",
         endpoint="/api/v1/supplier-reps", enforced=True, legacy=("suppliers.manage_supplier_reps",)),

    # ── Catalogue ──────────────────────────────────────────────────────────────────────────────────────────
    page("products", "Products", "Catalogue", "/app/catalog/products", "catalog", "product",
         endpoint="/api/v1/products", enforced=True,
         extras=(("print_barcode", "Print barcodes & labels", "catalog.print_barcode"),),
         legacy={"delete": ["catalog.archive_product"]},
         note="Delete archives the product so its history stays intact."),
    page("categories", "Categories", "Catalogue", "/app/catalog/categories", "catalog", "category",
         endpoint="/api/v1/categories", legacy=("catalog.manage_category",), enforced=True),
    page("brands", "Brands", "Catalogue", "/app/catalog/brands", "catalog", "brand",
         endpoint="/api/v1/brands", legacy=("catalog.manage_brand",), enforced=True),
    page("uoms", "Units", "Catalogue", "/app/catalog/uoms", "catalog", "uom",
         endpoint="/api/v1/uoms", legacy=("catalog.manage_uom",), enforced=True),
    page("price_lists", "Price lists", "Catalogue", "/app/catalog/price-lists", "catalog", "price_list",
         endpoint="/api/v1/price-lists", legacy=("catalog.manage_price_list",), enforced=True),
    page("schemes", "Schemes", "Catalogue", "/app/catalog/schemes", "catalog", "scheme",
         endpoint="/api/v1/schemes", legacy=("catalog.manage_price_list",), enforced=True),
    page("labels", "Labels", "Catalogue", "/app/catalog/labels", "catalog", "label",
         endpoint="/api/v1/labels/print", actions=("view",), enforced=True, skip_probe=("view",),
         legacy=("catalog.print_barcode",),
         note="Printing labels uses the Products page's “Print barcodes & labels” switch."),

    # ── Inventory ──────────────────────────────────────────────────────────────────────────────────────────
    page("stock", "Stock", "Inventory", "/app/inventory/stock", "inventory", "stock",
         endpoint="/api/v1/inventory/stock", actions=("view",), enforced=True),
    page("movements", "Stock movements", "Inventory", "/app/inventory/movements", "inventory", "movement",
         endpoint="/api/v1/inventory/movements", actions=("view",), enforced=True),
    page("batches", "Batches & expiry", "Inventory", "/app/inventory/batches", "inventory", "batch",
         endpoint="/api/v1/inventory/batches", actions=("view", "edit"), enforced=True),
    page("stock_alerts", "Stock alerts", "Inventory", "/app/inventory/alerts", "inventory", "alert",
         endpoint="/api/v1/inventory/alerts", actions=("view",), enforced=True),
    page("valuation", "Valuation", "Inventory", "/app/inventory/valuation", "inventory", "valuation",
         endpoint="/api/v1/inventory/valuation", actions=("view",), enforced=True, skip_probe=("view",),
         legacy={"view": ["inventory.view_valuation"]},
         note="Stock value and the GL reconciliation; still carried by inventory.view_valuation."),
    page("transfers", "Stock transfers", "Inventory", "/app/inventory/transfers", "inventory", "transfer",
         endpoint="/api/v1/inventory/transfers", enforced=True,
         extras=(("approve", "Approve transfers", "inventory.approve_transfer"),
                 ("dispatch", "Dispatch & receive", "inventory.transfer_stock")),
         legacy=("inventory.transfer_stock",)),
    page("adjustments", "Adjustments & wastage", "Inventory", "/app/inventory/adjustments", "inventory", "adjustment",
         endpoint="/api/v1/inventory/adjustments", enforced=True,
         extras=(("approve", "Approve adjustments", "inventory.approve_adjustment"),
                 ("wastage", "Record wastage", "inventory.record_wastage")),
         legacy=("inventory.adjust_stock",)),
    page("counts", "Stock counts", "Inventory", "/app/inventory/counts", "inventory", "count",
         endpoint="/api/v1/inventory/counts", enforced=True,
         extras=(("post", "Post counts", "inventory.approve_stock_count"),),
         legacy=("inventory.run_stock_count",)),

    # ── Purchasing ─────────────────────────────────────────────────────────────────────────────────────────
    page("purchase_orders", "Purchase orders", "Purchasing", "/app/purchasing/orders", "purchasing", "purchase_order",
         endpoint="/api/v1/purchase-orders", enforced=True,
         extras=(("approve", "Approve & send", "purchasing.approve_purchase_order"),),
         legacy={"delete": ["purchasing.cancel_purchase_order"]},
         note="Delete cancels the order."),
    page("grns", "Goods receipts", "Purchasing", "/app/purchasing/grns", "purchasing", "grn",
         endpoint="/api/v1/goods-receipts", legacy=("purchasing.receive_goods",), enforced=True),
    page("bills", "Purchase bills", "Purchasing", "/app/purchasing/bills", "purchasing", "bill",
         endpoint="/api/v1/purchase-bills", enforced=True,
         extras=(("post", "Post bills", "purchasing.post_purchase_bill"),),
         legacy=("purchasing.post_purchase_bill",)),
    page("purchase_returns", "Purchase returns", "Purchasing", "/app/purchasing/returns", "purchasing", "purchase_return",
         endpoint="/api/v1/purchase-returns", legacy={"add": ["purchasing.add_purchase_return"]}, enforced=True),

    # ── Dispatch ───────────────────────────────────────────────────────────────────────────────────────────
    page("load_forms", "Load forms", "Dispatch", "/app/delivery/pick-lists", "delivery", "load_form",
         endpoint="/api/v1/pick-lists", actions=("view", "add", "edit"), enforced=True,
         extras=(("issue", "Issue stock", "delivery.issue_stock"),),
         legacy=("delivery.create_delivery",)),
    page("trips", "Dispatches (trips)", "Dispatch", "/app/delivery/trips", "delivery", "trip",
         endpoint="/api/v1/trips", enforced=True,
         extras=(("assign_driver", "Assign driver & vehicle", "delivery.assign_driver"),
                 ("start", "Start & close trips", "delivery.start_trip"),
                 ("view_all", "See every trip, not only my own", "delivery.view_all_trips")),
         legacy=("delivery.create_delivery",)),
    page("delivery_notes", "Delivery notes", "Dispatch", "/app/delivery/notes", "delivery", "delivery_note",
         endpoint="/api/v1/delivery-notes", actions=("view",), enforced=True,
         extras=(("complete", "Confirm deliveries", "delivery.complete_delivery"),
                 ("returns", "Record returns", "delivery.record_delivery_return")),
         legacy=("delivery.complete_delivery",)),
    page("vehicles", "Vehicles", "Dispatch", "/app/delivery/vehicles", "delivery", "vehicle",
         endpoint="/api/v1/vehicles", legacy=("delivery.manage_vehicles",), enforced=True),
    page("drivers", "Drivers", "Dispatch", "/app/delivery/drivers", "delivery", "driver",
         endpoint="/api/v1/drivers", legacy=("delivery.manage_drivers",), enforced=True),

    # ── Accounting ─────────────────────────────────────────────────────────────────────────────────────────
    page("accounts", "Chart of accounts", "Accounting", "/app/accounting/accounts", "accounting", "account",
         endpoint="/api/v1/accounting/accounts", legacy=("accounting.manage_coa", "accounting.view_coa"), enforced=True),
    page("journals", "Journals", "Accounting", "/app/accounting/journals", "accounting", "journal",
         endpoint="/api/v1/accounting/journals", enforced=True,
         extras=(("post", "Post journals", "accounting.post_journal"),
                 ("reverse", "Reverse journals", "accounting.reverse_journal")),
         legacy={"view": ["accounting.view_coa"]}),
    page("payments", "Payments & receipts", "Accounting", "/app/accounting/payments", "accounting", "payment",
         endpoint="/api/v1/accounting/payments", enforced=True,
         extras=(("receive", "Receive money", "cash.receive_payment"),
                 ("pay", "Pay out", "cash.make_payment")),
         legacy=("accounting.manage_payments",)),
    page("cheques", "Cheques", "Accounting", "/app/accounting/cheques", "accounting", "cheque",
         endpoint="/api/v1/accounting/payments/cheques", actions=("view", "edit"), legacy=("accounting.manage_payments",), enforced=True, skip_probe=("edit",)),
    page("cheque_books", "Cheque books", "Accounting", "/app/accounting/cheque-books", "accounting", "cheque_book",
         endpoint="/api/v1/accounting/cheque-books", enforced=True, skip_probe=("add", "edit", "delete"),
         note="The books of blank cheques the business writes from. A cheque received from a customer is their paper and lives on the payment, not here."),
    page("expenses", "Expenses", "Accounting", "/app/accounting/expenses", "accounting", "expense",
         endpoint="/api/v1/accounting/expenses", enforced=True,
         extras=(("approve", "Approve expenses", "cash.approve_expense"),),
         legacy=("cash.make_payment",)),
    page("bank_cash", "Bank & cash", "Accounting", "/app/accounting/bank", "accounting", "bank_account",
         endpoint="/api/v1/accounting/bank-accounts", legacy=("cash.view_cash",), enforced=True,
         extras=(("petty_cash", "Manage petty cash", "cash.manage_petty_cash"),)),
    page("reconciliation", "Bank reconciliation", "Accounting", "/app/accounting/reconciliation", "accounting", "reconciliation",
         endpoint="/api/v1/accounting/bank-accounts", actions=("view", "add"), legacy=("accounting.reconcile_bank",), enforced=True, skip_probe=("view", "add"),
         note="Lives on a bank account: Settings → Bank & cash → reconcile."),
    page("periods", "Periods & year end", "Accounting", "/app/accounting/periods", "accounting", "period",
         endpoint="/api/v1/accounting/fiscal-years", actions=("view", "add", "edit"), legacy=("accounting.close_period",), enforced=True),
    page("opening_balances", "Opening balances", "Accounting", "/app/accounting/opening-balances", "accounting", "opening_balance",
         endpoint="/api/v1/accounting/opening-balances", legacy=("accounting.manage_coa",), enforced=True),

    # ── Reports ────────────────────────────────────────────────────────────────────────────────────────────
    page("reports", "Reports", "Reports", "/app/reports", "reports", "report",
         endpoint="/api/v1/reports", actions=("view",), enforced=True,
         extras=(("financial", "Financial reports", ("reports.view_financial_reports", "accounting.view_financial_reports")),
                 ("export", "Export reports", "reports.export_reports"),
                 ("schedule", "Schedule reports", "reports.schedule_reports")),
         legacy=("reports.view_operational_reports",)),

    # ── Settings ───────────────────────────────────────────────────────────────────────────────────────────
    page("settings_company", "Company profile", "Settings", "/app/settings/company", "settings", "company",
         endpoint="/api/v1/settings/company", actions=("view", "edit"), legacy=("settings.manage_company",), enforced=True, skip_probe=("edit",)),
    page("settings_branding", "Branding", "Settings", "/app/settings/branding", "settings", "branding",
         endpoint="/api/v1/settings/branding", actions=("view", "edit"), legacy=("settings.manage_branding",), enforced=True, skip_probe=("edit",)),
    page("settings_warehouses", "Warehouses", "Settings", "/app/settings/warehouses", "settings", "warehouse",
         endpoint="/api/v1/settings/warehouses", legacy=("settings.manage_warehouses",), enforced=True),
    page("settings_locations", "Locations (bins)", "Settings", "/app/settings/locations", "settings", "bin",
         endpoint="/api/v1/settings/bins", legacy=("settings.manage_warehouses",), enforced=True),
    page("settings_taxes", "Taxes", "Settings", "/app/settings/taxes", "settings", "tax",
         endpoint="/api/v1/settings/tax-types", legacy=("accounting.manage_tax",), enforced=True),
    page("settings_discount_contributions", "Discount contributions", "Settings", "/app/settings/discount-contributions", "settings", "discount_contribution",
         endpoint="/api/v1/settings/discount-contributions", enforced=True,
         note="The co-funded discount table: each total % split into the distributor's and the manufacturer's share."),
    page("settings_numbering", "Document numbering", "Settings", "/app/settings/numbering", "settings", "numbering",
         endpoint="/api/v1/settings/numbering", actions=("view", "edit"), legacy=("settings.manage_numbering",), enforced=True, skip_probe=("edit",)),
    page("settings_posting_rules", "Posting rules", "Settings", "/app/settings/posting-rules", "settings", "posting_rule",
         endpoint="/api/v1/settings/posting-rules", actions=("view", "edit"), legacy=("accounting.manage_coa",), enforced=True),
    page("settings_reason_codes", "Reason codes", "Settings", "/app/settings/reason-codes", "settings", "reason_code",
         endpoint="/api/v1/settings/reason-codes", legacy=("settings.manage_company",), enforced=True),
    page("settings_api_keys", "API keys", "Settings", "/app/settings/api-keys", "settings", "api_key",
         endpoint="/api/v1/api-keys", legacy=("settings.manage_api_keys",), enforced=True),
    page("settings_webhooks", "Webhooks", "Settings", "/app/settings/webhooks", "settings", "webhook",
         endpoint="/api/v1/webhooks", legacy=("settings.manage_integrations",), enforced=True),
    page("settings_imports", "Imports", "Settings", "/app/settings/imports", "settings", "import",
         endpoint="/api/v1/imports", actions=("view", "add"), legacy=("settings.manage_company",), enforced=True),
    page("settings_subscription", "Subscription", "Settings", "/app/settings/subscription", "settings", "subscription",
         endpoint="/api/v1/subscription", actions=("view",), legacy=("settings.manage_company",), enforced=True, skip_probe=("view",),
         note="The plan, usage and invoices for this company."),
    page("settings_support", "Support", "Settings", "/app/settings/support", "settings", "ticket",
         endpoint="/api/v1/support-tickets", actions=("view", "add"), enforced=True),
]

# A role saved before the matrix that holds the key here should also hold the values: the old codename covered
# several pages that now have their own switch. Applied once by accounts migration 0007.
BACKFILL = {
    "inventory.view_stock": ["inventory.view_movement", "inventory.view_batch", "inventory.view_alert"],
    # whoever could add journals could also edit and delete their drafts
    "accounting.add_journal": ["accounting.change_journal", "accounting.delete_journal"],
    "sales.view_sales_order": ["sales.view_order_inbox"],
    # the Store locations map used to ride on the plain customer switches
    "customers.view_customer": ["customers.view_store_location"],
    "customers.change_customer": ["customers.change_store_location"],
    "sales.view_invoice": ["sales.view_credit_note"],
    "purchasing.view_purchase_order": ["purchasing.view_purchase_return"],
}

BY_KEY = {p["key"]: p for p in PAGES}
GROUPS = list(OrderedDict((p["group"], None) for p in PAGES))


def _perms_of(extra):
    return [extra["perm"]] if isinstance(extra["perm"], str) else list(extra["perm"])


# Where a document model's name does not match its page's codenames (`Bill` vs `PurchaseBill`).
MODEL_PAGES = {
    "PurchaseBill": "bills",
    "GoodsReceipt": "grns",
    "PurchaseReturn": "purchase_returns",
    "SalesInvoice": "invoices",
    "StockAdjustment": "adjustments",
    "StockTransfer": "transfers",
    "StockCount": "counts",
    "PickList": "load_forms",
    "DeliveryTrip": "trips",
    "JournalEntry": "journals",
    "RepSettlement": "rep_settlements",
    "OpeningBalanceBatch": "opening_balances",
}


def page_for_model(model_name):
    """
    "SalesOrder" → the page that owns it. Attachments and the history tab hang off a document, so they follow that
    document's own switches instead of being open to anyone with a login.
    """
    if model_name in MODEL_PAGES:
        return MODEL_PAGES[model_name]
    wanted = (model_name or "").replace("_", "").lower()
    for p in PAGES:
        thing = p["actions"].get("view", "").split("_", 1)[-1] if p["actions"].get("view") else ""
        if thing and thing.replace("_", "").lower() == wanted:
            return p["key"]
    return None


def page_codenames(key):
    p = BY_KEY[key]
    out = list(p["actions"].values())
    for e in p["extras"]:
        out += _perms_of(e)
    return out


def all_page_codenames():
    out = []
    for p in PAGES:
        for c in page_codenames(p["key"]):
            if c not in out:
                out.append(c)
    return out


def implied_by_legacy():
    """{old codename: [the granular codenames it grants]} so roles made before the matrix keep working."""
    out = {}

    def add(old, codes):
        out.setdefault(old, [])
        for c in codes:
            if c and c not in out[old]:
                out[old].append(c)

    for p in PAGES:
        legacy = p["legacy"]
        if isinstance(legacy, dict):
            for action, olds in legacy.items():
                for old in olds:
                    add(old, [p["actions"].get(action)])
        else:
            for old in legacy:
                add(old, list(p["actions"].values()))
    return out


def narrow(codenames):
    """
    Drop a coarse codename that grants more than was actually ticked.

    The permission editor is seeded from a role's *expanded* rights and sends back everything still ticked. A
    role holding an old coarse codename — `customers.manage_territories`, which grants nine page switches —
    therefore submits that codename **and** its expansion. Turning one switch off then did nothing at all: the
    coarse codename survived the save and re-granted it on the next expansion, so "delete" came back every
    time. This is the fix: if some of what a codename grants is present and some is not, the person deselected
    part of it, so the coarse codename goes and the explicit switches they kept stand on their own.

    A caller that sends *only* coarse codenames — role seeding, or an API client written against them — is
    untouched, because none of the expansion is present to contradict.

    Turning *every* switch of a coarse codename off cannot be told apart here from that bare coarse-only
    payload — both carry the codename and none of its grants — so the editor does the all-off drop itself,
    where it knows a switch was clicked (see `legacy_map` on the permission-pages endpoint and the matrix).
    """
    chosen = set(codenames)
    kept = set(chosen)
    for code in chosen:
        grants = set(expand([code])) - {code}
        if not grants:
            continue
        present = grants & chosen
        if present and present != grants:
            kept.discard(code)
    return sorted(kept)


def expand(codenames):
    """Grant the granular codenames implied by any coarse ones the role still holds."""
    granted = set(codenames)
    implies = implied_by_legacy()
    for old, new in implies.items():
        if old in granted:
            granted.update(new)
    return granted


def page_perms(key, extra_actions=None, also=None):
    """
    `perm_map` for a viewset: list/retrieve → view, create → add, update/partial_update → edit, destroy → delete.
    `extra_actions` adds custom viewset actions; `also` appends alternatives for an action (a rep endpoint shared
    with staff accepts the `external.*` codename as well).
    """
    acts = BY_KEY[key]["actions"]
    view = acts.get("view")
    out = {
        "list": view,
        "retrieve": view,
        "create": acts.get("add") or view,
        "update": acts.get("edit") or acts.get("add") or view,
        "partial_update": acts.get("edit") or acts.get("add") or view,
        "destroy": acts.get("delete") or acts.get("edit") or view,
        # exporting a list is reading it, in a file — same switch, so nobody can download what they cannot see
        "export": view,
    }
    out.update(extra_actions or {})
    for action, alternatives in (also or {}).items():
        current = out.get(action)
        current = [] if current is None else ([current] if isinstance(current, str) else list(current))
        out[action] = current + [a for a in alternatives if a not in current]
    return out


def extra_perm(key, extra_key):
    """The codename(s) behind one of a page's extras — used by perm_map / @require_perm on custom actions."""
    for e in BY_KEY[key]["extras"]:
        if e["key"] == extra_key:
            return e["perm"]
    raise KeyError(f"{key} has no extra {extra_key!r}")


def extra_perm_or(key, action):
    """The page's codename for an action — handy when a custom viewset action is really "add" or "edit"."""
    return BY_KEY[key]["actions"].get(action)


def resolve_pages(codenames, is_owner=False):
    """What the signed-in user may do, page by page — the shape `/me` returns and the UI gates on."""
    granted = expand(codenames)
    out = {}
    for p in PAGES:
        allow = {a: True for a in p["actions"]} if is_owner else {a: c in granted for a, c in p["actions"].items()}
        extras = {e["key"]: True for e in p["extras"]} if is_owner else {e["key"]: any(c in granted for c in _perms_of(e)) for e in p["extras"]}
        out[p["key"]] = {**allow, "extras": extras}
    return out


def pages_as_json():
    """The matrix for the role editor: groups → pages → the switches each page has."""
    implies = implied_by_legacy()
    # Codenames the matrix shows a control for — every page switch and every extra button. A coarse codename
    # that is one of these is toggled directly by its own control, and its partial-deselect is handled by
    # narrow(), so it must NOT be in the legacy list: pruning it when a sibling switch goes off would turn its
    # own button off too (e.g. sales.cancel_sales_order is both the Delete legacy and the "Hold / reject" extra).
    visible = set(all_page_codenames())
    for p in PAGES:
        for e in p["extras"]:
            visible.update(e["perm"] if isinstance(e["perm"], list) else [e["perm"]])
    groups = OrderedDict()
    for p in PAGES:
        page_codes = set(p["actions"].values())
        # The *invisible* coarse codenames an old role may still hold that grant this page's switches. The editor
        # needs them so that switching every grant off drops the coarse one too — it rides along unseen otherwise
        # and re-grants the lot on the next expansion (the Taxes "turn all off, it comes back" bug).
        legacy = [{"code": old, "grants": sorted(grants)} for old, grants in implies.items()
                  if old not in visible and page_codes & set(grants)]
        groups.setdefault(p["group"], []).append({
            "key": p["key"], "label": p["label"], "path": p["path"], "note": p["note"],
            "actions": [{"action": a, "label": ACTION_LABELS[a], "codename": c} for a, c in p["actions"].items()],
            "extras": p["extras"], "legacy": legacy,
        })
    return [{"group": g, "pages": pages} for g, pages in groups.items()]
