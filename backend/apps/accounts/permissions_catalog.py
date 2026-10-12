"""
Permission catalogue (§2.3). Codename = "<module>.<action>". These are created as
Django `Permission` rows attached to the core.Module content type so Groups can hold them.
"""

CATALOG = {
    "users": ("Users & roles", [
        ("view_user", "View users"), ("add_user", "Add users"), ("change_user", "Edit users"),
        ("deactivate_user", "Deactivate users"), ("reset_password", "Reset user passwords"),
        ("manage_roles", "Manage roles"), ("view_activity_log", "View activity log"),
    ]),
    "catalog": ("Product catalogue", [
        ("view_product", "View products"), ("add_product", "Add products"), ("change_product", "Edit products"),
        ("archive_product", "Archive products"), ("manage_category", "Manage categories"), ("manage_brand", "Manage brands"),
        ("manage_uom", "Manage units"), ("manage_price_list", "Manage price lists & schemes"), ("print_barcode", "Print barcodes/labels"),
    ]),
    "suppliers": ("Suppliers", [
        ("view_supplier", "View suppliers"), ("add_supplier", "Add suppliers"), ("change_supplier", "Edit suppliers"),
        ("manage_supplier_reps", "Manage supplier reps"), ("view_supplier_ledger", "View supplier ledger"),
    ]),
    "customers": ("Customers", [
        ("view_customer", "View customers"), ("add_customer", "Add customers"), ("change_customer", "Edit customers"),
        ("set_credit_limit", "Set credit limits"), ("block_customer", "Block customers"), ("view_customer_ledger", "View customer ledger"),
        ("manage_territories", "Manage territories & routes"),
    ]),
    "purchasing": ("Purchasing", [
        ("view_purchase_order", "View purchase orders"), ("add_purchase_order", "Add purchase orders"),
        ("change_purchase_order", "Edit purchase orders"), ("approve_purchase_order", "Approve/send purchase orders"),
        ("cancel_purchase_order", "Cancel purchase orders"), ("receive_goods", "Receive goods (GRN)"),
        ("post_purchase_bill", "Post purchase bills"), ("add_purchase_return", "Add purchase returns"),
    ]),
    "sales": ("Sales", [
        ("view_sales_order", "View sales orders"), ("add_sales_order", "Add sales orders"), ("change_sales_order", "Edit sales orders"),
        ("confirm_sales_order", "Confirm sales orders"), ("cancel_sales_order", "Cancel/reject sales orders"),
        ("override_price", "Override prices"), ("override_discount", "Override discounts"), ("approve_credit_hold", "Approve credit holds"),
        ("view_invoice", "View invoices"), ("add_credit_note", "Add credit notes / sales returns"),
    ]),
    "delivery": ("Delivery", [
        ("create_delivery", "Create pick lists & trips"), ("assign_driver", "Assign driver/vehicle"), ("start_trip", "Start trips (dispatch)"),
        ("complete_delivery", "Complete deliveries / mark delivered"), ("record_delivery_return", "Record delivery returns"),
        ("view_all_trips", "View all trips"), ("manage_vehicles", "Manage vehicles"), ("manage_drivers", "Manage drivers"),
    ]),
    "tracking": ("Rep tracking", [
        ("view_own", "See my own route"), ("view_team", "See anyone's route and the live map"),
        ("manage_policy", "Change the tracking policy and purge history"),
    ]),
    "visits": ("Shop visits", [
        ("record", "Check in at a shop"), ("view_own", "See my own visits"),
        ("view_team", "See everyone's visits"),
    ]),
    "inventory": ("Inventory", [
        ("view_stock", "View stock"), ("transfer_stock", "Transfer stock"), ("approve_transfer", "Approve transfers"),
        ("adjust_stock", "Adjust stock"), ("record_wastage", "Record wastage"), ("approve_adjustment", "Approve adjustments"),
        ("run_stock_count", "Run stock counts"), ("approve_stock_count", "Post stock counts"), ("view_valuation", "View valuation"),
    ]),
    "accounting": ("Accounting", [
        ("view_coa", "View chart of accounts"), ("manage_coa", "Manage chart of accounts"), ("add_journal", "Add journals"),
        ("post_journal", "Post journals"), ("reverse_journal", "Reverse journals"), ("manage_payments", "Manage payments"),
        ("reconcile_bank", "Reconcile bank"), ("close_period", "Close/reopen periods"), ("view_financial_reports", "View financial reports"),
        ("manage_tax", "Manage taxes"),
    ]),
    "cash": ("Cash", [
        ("view_cash", "View cash"), ("receive_payment", "Receive payments"), ("make_payment", "Make payments"),
        ("approve_expense", "Approve expenses"), ("manage_petty_cash", "Manage petty cash"), ("settle_rep_collection", "Settle rep collections"),
    ]),
    "reports": ("Reports", [
        ("view_operational_reports", "View operational reports"), ("view_financial_reports", "View financial reports"),
        ("export_reports", "Export reports"),
    ]),
    "settings": ("Settings", [
        ("manage_company", "Manage company profile"), ("manage_branding", "Manage branding"), ("manage_warehouses", "Manage warehouses"),
        ("manage_numbering", "Manage document numbering"), ("manage_notifications", "Manage notifications"),
        ("manage_api_keys", "Manage API keys"), ("manage_integrations", "Manage integrations & webhooks"),
    ]),
    "external": ("External (rep) access", [
        ("submit_order", "Submit orders"), ("cancel_own_order", "Cancel own orders"), ("view_own_orders", "View own orders"),
        ("view_assigned_customers", "View assigned customers"), ("add_customer_request", "Request new customers"),
        ("view_customer_outstanding", "View customer outstanding"), ("view_prices", "View prices"),
        ("view_stock_exact", "View exact stock"), ("view_stock_bands", "View stock bands (low/medium/high)"),
        ("view_delivery_status", "View delivery status"), ("record_collection", "Record collections"),
        ("view_own_collections", "View own collections"), ("view_targets", "View targets"),
    ]),
}

PLATFORM_CATALOG = {
    "platform": ("Platform", [
        ("manage_plans", "Manage plans"), ("manage_tenants", "Manage tenants"), ("manage_subscriptions", "Manage subscriptions"),
        ("manage_billing", "Record payments & invoices"), ("impersonate", "Impersonate tenant users"), ("view_audit", "View platform audit"),
        ("manage_settings", "Manage platform settings"), ("manage_platform_users", "Manage platform users"), ("view_dashboard", "View platform dashboard"),
    ]),
}

INTERNAL_MODULES = [m for m in CATALOG if m != "external"]
EXTERNAL_MODULES = ["external"]

# A rep's role is not only `external.*`. Checking in at a shop and sending GPS fixes are the same features the
# office uses, so the mobile app is gated on `visits.*` / `tracking.*` (apps/integrations/ext_views.py,
# apps/partners/tracking_views.py) and the seeded "Manufacturer Rep" below is created holding them. Only the
# rep's own slice belongs here: the team-wide and policy switches would let a rep watch every other rep.
EXTERNAL_EXTRAS = ["tracking.view_own", "visits.record", "visits.view_own"]


def all_codenames(include_external=True, include_platform=False):
    out = []
    for module, (_, perms) in CATALOG.items():
        if module == "external" and not include_external:
            continue
        out += [f"{module}.{code}" for code, _ in perms]
    if include_platform:
        for module, (_, perms) in PLATFORM_CATALOG.items():
            out += [f"{module}.{code}" for code, _ in perms]
    return out


def catalog_as_json(role_type="internal"):
    src = CATALOG if role_type != "platform" else PLATFORM_CATALOG
    modules = []
    for module, (label, perms) in src.items():
        if role_type == "internal" and module == "external":
            continue
        if role_type == "external" and module != "external":
            # A shared module appears in the rep catalogue trimmed to the switches a rep may hold, so the role
            # editor shows every permission the role really has instead of hiding some and losing them on save.
            perms = [(c, l) for c, l in perms if f"{module}.{c}" in EXTERNAL_EXTRAS]
            if not perms:
                continue
        modules.append({"module": module, "label": label, "permissions": [{"codename": f"{module}.{c}", "label": l} for c, l in perms]})
    if role_type == "external":
        modules.sort(key=lambda m: m["module"] != "external")  # the rep's own module reads first
    return modules


def external_role_codenames():
    """Every permission an external (rep) role may hold: its own module plus the rep slice of the shared ones."""
    return {f"external.{code}" for code, _ in CATALOG["external"][1]} | set(EXTERNAL_EXTRAS)


# Default role templates (§2.4)
DEFAULT_ROLES = {
    "Manager": {"type": "internal", "perms": [p for p in all_codenames(include_external=False) if not p.startswith("settings.manage_branding")]},
    "Accountant": {"type": "internal", "perms": [p for p in all_codenames(include_external=False) if p.split(".")[0] in ("accounting", "cash", "reports", "customers", "suppliers") or p in ("sales.view_sales_order", "sales.view_invoice", "purchasing.view_purchase_order", "inventory.view_valuation", "inventory.view_stock")]},
    "Cashier": {"type": "internal", "perms": ["cash.view_cash", "cash.receive_payment", "cash.settle_rep_collection", "cash.manage_petty_cash", "customers.view_customer", "customers.view_customer_ledger", "sales.view_invoice", "sales.view_sales_order"]},
    "Warehouse Keeper": {"type": "internal", "perms": ["inventory.view_stock", "inventory.transfer_stock", "inventory.adjust_stock", "inventory.record_wastage", "inventory.run_stock_count", "purchasing.receive_goods", "purchasing.view_purchase_order", "delivery.create_delivery", "catalog.view_product", "catalog.print_barcode", "sales.view_sales_order", "delivery.view_all_trips"]},
    "Store Keeper": {"type": "internal", "perms": ["inventory.view_stock", "inventory.run_stock_count", "catalog.view_product", "catalog.print_barcode", "purchasing.receive_goods", "delivery.create_delivery"]},
    "Sales Coordinator": {"type": "internal", "perms": ["tracking.view_own", "tracking.view_team", "visits.view_own", "visits.view_team", "sales.view_sales_order", "sales.add_sales_order", "sales.change_sales_order", "sales.confirm_sales_order", "sales.cancel_sales_order", "sales.view_invoice", "customers.view_customer", "customers.add_customer", "customers.change_customer", "customers.view_customer_ledger", "catalog.view_product", "inventory.view_stock", "delivery.create_delivery", "delivery.assign_driver", "delivery.view_all_trips", "suppliers.view_supplier", "reports.view_operational_reports"]},
    "Driver": {"type": "internal", "perms": ["delivery.complete_delivery", "delivery.record_delivery_return", "cash.receive_payment", "sales.view_sales_order", "customers.view_customer", "tracking.view_own", "visits.record", "visits.view_own"]},
    "Auditor": {"type": "internal", "perms": [p for p in all_codenames(include_external=False) if p.split(".")[1].startswith("view_")]},
    "Manufacturer Rep": {"type": "external", "perms": ["external.submit_order", "external.cancel_own_order", "external.view_own_orders", "external.view_assigned_customers", "external.add_customer_request", "external.view_customer_outstanding", "external.view_prices", "external.view_stock_bands", "external.view_stock_exact", "external.view_delivery_status", "external.record_collection", "external.view_own_collections", "external.view_targets", "tracking.view_own", "visits.record", "visits.view_own"]},
}
