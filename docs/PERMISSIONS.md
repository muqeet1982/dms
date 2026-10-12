# Permissions

## How it works (9 Oct 2026 — page switches)

Permissions are set **per page**, as four switches: **View · Add · Edit · Delete**, plus the extras that only make
sense on that page (confirm an order, post a journal, reset a password…). `apps/accounts/page_permissions.py` is the
single source of truth; everything else reads from it:

| Who | Reads | For |
| --- | --- | --- |
| Role editor (`/app/users/roles/:id`) | `GET /api/v1/permission-pages` | renders the matrix of switches |
| The app | `GET /api/v1/me` → `pages` | hides the Add button, the Save button, the row actions, the menu entry; `RequirePage` blocks the route |
| The API | `perm_map = page_perms("<key>")` on each ViewSet | refuses anything that is off with `403` |

Rules the UI and the server both follow:

* **View is the gate.** With View off the page does not open at all (`RequirePage`), and the other three switches on
  that row are cleared. Switching Add, Edit or Delete on switches View on.
* **Add also gates the create screen**, so typing `/app/users/new` is not a way around the matrix.
* **Delete means what the document allows**: users are suspended, products archived, orders cancelled — the registry
  says so per page, and the role editor shows that note.
* **Roles & access is its own switch.** Someone who may add or edit users still cannot hand out roles or warehouse
  access unless `users.change_user_access` is on — otherwise a user admin could promote themselves.
* **Old roles keep working.** A role saved before the matrix holds coarse codenames (`users.manage_roles`,
  `delivery.manage_vehicles`); `legacy` in the registry maps them onto the switches, `/api/v1/roles` returns
  `permissions_effective`, and the first save converts the role to switches without changing what it can do.

`tests/test_permission_matrix.py` is the guard against leaks: for every page marked `enforced`, it builds a role with
exactly one switch on and asserts the other actions answer `403` — and that a role with nothing on cannot even read
the page. Flip `enforced=True` as each page is converted.

### Coverage

All **60 pages** are on the matrix and armed in the leak test: Users Management (3), Sales (6), Customers (2),
Suppliers (2), Catalogue (7), Inventory (8), Purchasing (4), Dispatch (5), Accounting (9), Reports (1),
Settings (13). Personal screens (profile, my notifications, my alerts) are not in the matrix — everyone manages
their own.

Four old codenames covered pages that now have a switch of their own (`inventory.view_stock` also opened the stock
ledger, batches and alerts; `sales.view_sales_order` the orders inbox; `sales.view_invoice` credit notes;
`purchasing.view_purchase_order` purchase returns). Accounts migration `0007_page_permission_backfill` gives those
roles the new codenames once, so nobody lost a page on the day the matrix arrived.

### Codenames per page

`<module>.view_<thing>`, `add_<thing>`, `change_<thing>`, `delete_<thing>` — e.g. the Users page is
`users.view_user`, `users.add_user`, `users.change_user`, `users.delete_user`, with extras
`users.reset_password` and `users.change_user_access`.

---

## Legacy catalogue (still honoured)

Every codename below is a Django `Permission` attached to the `core.Module` content type. Roles are Django Groups scoped to a tenant (`GroupProfile`). The **Owner** (system role) implicitly holds every internal permission. External roles may only contain `external.*` codenames and are assigned to manufacturer reps.


## Users & roles (`users.*`)

Screens: Users & roles → Users, Roles, Activity log

| Codename | Label |
| --- | --- |
| `users.view_user` | View users |
| `users.add_user` | Add users |
| `users.change_user` | Edit users |
| `users.deactivate_user` | Deactivate users |
| `users.reset_password` | Reset user passwords |
| `users.manage_roles` | Manage roles |
| `users.view_activity_log` | View activity log |

## Product catalogue (`catalog.*`)

Screens: Products (list/detail/form, categories, brands, units, price lists, schemes, labels)

| Codename | Label |
| --- | --- |
| `catalog.view_product` | View products |
| `catalog.add_product` | Add products |
| `catalog.change_product` | Edit products |
| `catalog.archive_product` | Archive products |
| `catalog.manage_category` | Manage categories |
| `catalog.manage_brand` | Manage brands |
| `catalog.manage_uom` | Manage units |
| `catalog.manage_price_list` | Manage price lists & schemes |
| `catalog.print_barcode` | Print barcodes/labels |

## Suppliers (`suppliers.*`)

Screens: Suppliers, Supplier reps

| Codename | Label |
| --- | --- |
| `suppliers.view_supplier` | View suppliers |
| `suppliers.add_supplier` | Add suppliers |
| `suppliers.change_supplier` | Edit suppliers |
| `suppliers.manage_supplier_reps` | Manage supplier reps |
| `suppliers.view_supplier_ledger` | View supplier ledger |

## Customers (`customers.*`)

Screens: Customers, Territories, Routes

| Codename | Label |
| --- | --- |
| `customers.view_customer` | View customers |
| `customers.add_customer` | Add customers |
| `customers.change_customer` | Edit customers |
| `customers.set_credit_limit` | Set credit limits |
| `customers.block_customer` | Block customers |
| `customers.view_customer_ledger` | View customer ledger |
| `customers.manage_territories` | Manage territories & routes |

## Purchasing (`purchasing.*`)

Screens: Purchase orders, GRNs, Purchase bills, Purchase returns

| Codename | Label |
| --- | --- |
| `purchasing.view_purchase_order` | View purchase orders |
| `purchasing.add_purchase_order` | Add purchase orders |
| `purchasing.change_purchase_order` | Edit purchase orders |
| `purchasing.approve_purchase_order` | Approve/send purchase orders |
| `purchasing.cancel_purchase_order` | Cancel purchase orders |
| `purchasing.receive_goods` | Receive goods (GRN) |
| `purchasing.post_purchase_bill` | Post purchase bills |
| `purchasing.add_purchase_return` | Add purchase returns |

## Sales (`sales.*`)

Screens: Orders inbox, Sales orders, Invoices, Credit notes

| Codename | Label |
| --- | --- |
| `sales.view_sales_order` | View sales orders |
| `sales.add_sales_order` | Add sales orders |
| `sales.change_sales_order` | Edit sales orders |
| `sales.confirm_sales_order` | Confirm sales orders |
| `sales.cancel_sales_order` | Cancel/reject sales orders |
| `sales.override_price` | Override prices |
| `sales.override_discount` | Override discounts |
| `sales.approve_credit_hold` | Approve credit holds |
| `sales.view_invoice` | View invoices |
| `sales.add_credit_note` | Add credit notes / sales returns |

## Delivery (`delivery.*`)

Screens: Trips, Delivery notes, Pick lists, Vehicles, Drivers; order → Mark delivered

| Codename | Label |
| --- | --- |
| `delivery.create_delivery` | Create pick lists & trips |
| `delivery.assign_driver` | Assign driver/vehicle |
| `delivery.start_trip` | Start trips (dispatch) |
| `delivery.complete_delivery` | Complete deliveries / mark delivered |
| `delivery.record_delivery_return` | Record delivery returns |
| `delivery.view_all_trips` | View all trips |
| `delivery.manage_vehicles` | Manage vehicles |
| `delivery.manage_drivers` | Manage drivers |

## Inventory (`inventory.*`)

Screens: Stock levels, Scan lookup, Ledger, Batches, Transfers, Adjustments, Counts, Alerts, Valuation

| Codename | Label |
| --- | --- |
| `inventory.view_stock` | View stock |
| `inventory.transfer_stock` | Transfer stock |
| `inventory.approve_transfer` | Approve transfers |
| `inventory.adjust_stock` | Adjust stock |
| `inventory.record_wastage` | Record wastage |
| `inventory.approve_adjustment` | Approve adjustments |
| `inventory.run_stock_count` | Run stock counts |
| `inventory.approve_stock_count` | Post stock counts |
| `inventory.view_valuation` | View valuation |

## Accounting (`accounting.*`)

Screens: Chart of accounts, Journals, Periods, Taxes, Reports (financial)

| Codename | Label |
| --- | --- |
| `accounting.view_coa` | View chart of accounts |
| `accounting.manage_coa` | Manage chart of accounts |
| `accounting.add_journal` | Add journals |
| `accounting.post_journal` | Post journals |
| `accounting.reverse_journal` | Reverse journals |
| `accounting.manage_payments` | Manage payments |
| `accounting.reconcile_bank` | Reconcile bank |
| `accounting.close_period` | Close/reopen periods |
| `accounting.view_financial_reports` | View financial reports |
| `accounting.manage_tax` | Manage taxes |

## Cash (`cash.*`)

Screens: Transactions, Cheques, Expenses, Rep settlements, Cash & bank

| Codename | Label |
| --- | --- |
| `cash.view_cash` | View cash |
| `cash.receive_payment` | Receive payments |
| `cash.make_payment` | Make payments |
| `cash.approve_expense` | Approve expenses |
| `cash.manage_petty_cash` | Manage petty cash |
| `cash.settle_rep_collection` | Settle rep collections |

## Reports (`reports.*`)

Screens: Reports catalogue and exports

| Codename | Label |
| --- | --- |
| `reports.view_operational_reports` | View operational reports |
| `reports.view_financial_reports` | View financial reports |
| `reports.export_reports` | Export reports |

## Settings (`settings.*`)

Screens: Company, Branding, Warehouses, Numbering, Notifications, API keys, Webhooks, Imports

| Codename | Label |
| --- | --- |
| `settings.manage_company` | Manage company profile |
| `settings.manage_branding` | Manage branding |
| `settings.manage_warehouses` | Manage warehouses |
| `settings.manage_numbering` | Manage document numbering |
| `settings.manage_notifications` | Manage notifications |
| `settings.manage_api_keys` | Manage API keys |
| `settings.manage_integrations` | Manage integrations & webhooks |

## External (rep) access (`external.*`)

Screens: Rep portal (/rep) and /ext/v1 API

| Codename | Label |
| --- | --- |
| `external.submit_order` | Submit orders |
| `external.cancel_own_order` | Cancel own orders |
| `external.view_own_orders` | View own orders |
| `external.view_assigned_customers` | View assigned customers |
| `external.add_customer_request` | Request new customers |
| `external.view_customer_outstanding` | View customer outstanding |
| `external.view_prices` | View prices |
| `external.view_stock_exact` | View exact stock |
| `external.view_stock_bands` | View stock bands (low/medium/high) |
| `external.view_delivery_status` | View delivery status |
| `external.record_collection` | Record collections |
| `external.view_own_collections` | View own collections |
| `external.view_targets` | View targets |

## Platform (`platform.*`) — platform admins only

| Codename | Label |
| --- | --- |
| `platform.manage_plans` | Manage plans |
| `platform.manage_tenants` | Manage tenants |
| `platform.manage_subscriptions` | Manage subscriptions |
| `platform.manage_billing` | Record payments & invoices |
| `platform.impersonate` | Impersonate tenant users |
| `platform.view_audit` | View platform audit |
| `platform.manage_settings` | Manage platform settings |
| `platform.manage_platform_users` | Manage platform users |
| `platform.view_dashboard` | View platform dashboard |

## Default role templates

Seeded for every new tenant (editable / cloneable):

| Role | Type | Permissions |
| --- | --- | --- |
| Owner | internal (system) | all internal permissions |
| Manager | internal | 88: `users.view_user`, `users.add_user`, `users.change_user`, `users.deactivate_user`, `users.reset_password`, `users.manage_roles`, `users.view_activity_log`, `catalog.view_product` … |
| Accountant | internal | 36: `suppliers.view_supplier`, `suppliers.add_supplier`, `suppliers.change_supplier`, `suppliers.manage_supplier_reps`, `suppliers.view_supplier_ledger`, `customers.view_customer`, `customers.add_customer`, `customers.change_customer` … |
| Cashier | internal | 8: `cash.view_cash`, `cash.receive_payment`, `cash.settle_rep_collection`, `cash.manage_petty_cash`, `customers.view_customer`, `customers.view_customer_ledger`, `sales.view_invoice`, `sales.view_sales_order` |
| Warehouse Keeper | internal | 12: `inventory.view_stock`, `inventory.transfer_stock`, `inventory.adjust_stock`, `inventory.record_wastage`, `inventory.run_stock_count`, `purchasing.receive_goods`, `purchasing.view_purchase_order`, `delivery.create_delivery` … |
| Store Keeper | internal | 6: `inventory.view_stock`, `inventory.run_stock_count`, `catalog.view_product`, `catalog.print_barcode`, `purchasing.receive_goods`, `delivery.create_delivery` |
| Sales Coordinator | internal | 17: `sales.view_sales_order`, `sales.add_sales_order`, `sales.change_sales_order`, `sales.confirm_sales_order`, `sales.cancel_sales_order`, `sales.view_invoice`, `customers.view_customer`, `customers.add_customer` … |
| Driver | internal | 5: `delivery.complete_delivery`, `delivery.record_delivery_return`, `cash.receive_payment`, `sales.view_sales_order`, `customers.view_customer` |
| Auditor | internal | 18: `users.view_user`, `users.view_activity_log`, `catalog.view_product`, `suppliers.view_supplier`, `suppliers.view_supplier_ledger`, `customers.view_customer`, `customers.view_customer_ledger`, `purchasing.view_purchase_order` … |
| Manufacturer Rep | external | 12: `external.submit_order`, `external.cancel_own_order`, `external.view_own_orders`, `external.view_assigned_customers`, `external.add_customer_request`, `external.view_customer_outstanding`, `external.view_prices`, `external.view_stock_bands` … |

## Data scoping (enforced in querysets)

- **Warehouse scope**: `UserWarehouseAccess` rows limit a user to those warehouses (stock, movements, GRNs, transfers, adjustments, counts, trips, POs).
- **Supplier/rep scope**: an external rep only sees customers assigned to them, their own orders/collections/deliveries; an API key bound to a supplier sees that supplier's data.
- **Driver scope**: a user linked to a Driver record sees only their trips and delivery notes unless they hold `delivery.view_all_trips`.
- **Approval limits**: per-role `approval_limits` (`stock_adjustment`, `expense`) — approvals above the limit escalate to a role without a limit (e.g. Owner).

