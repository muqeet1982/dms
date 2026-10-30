"""
ReportRegistry (§12): key → {title, area, params, query, permission}. Every report gets JSON, CSV, XLSX, PDF for free.
"""
from apps.accounting import reports as fin
from apps.reports import operational as ops

FIN = "accounting.view_financial_reports"
# The Reports page's View switch opens the operational reports. They asked for the old coarse
# "view_operational_reports", which the role editor has no switch for — so a role with Reports → View on still saw
# only the financial ones (TDN, 30 Oct 2026: asif.malik saw 16 of 64). A role holding the old codename still gets
# them: expand() turns it into this one.
OPS = "reports.view_report"
# the Financial switch may store either codename — either one opens the financial reports
ALTERNATIVES = {FIN: (FIN, "reports.view_financial_reports")}


def may_see(request, perm):
    """Whether this caller may open a report needing `perm` (its alternatives count)."""
    from apps.core.permissions import has_tenant_perm

    return any(has_tenant_perm(request, p) for p in ALTERNATIVES.get(perm, (perm,)))

DATE_RANGE = [{"name": "date_from", "type": "date"}, {"name": "date_to", "type": "date"}]
AS_OF = [{"name": "as_of", "type": "date"}]
WAREHOUSE = [{"name": "warehouse", "type": "warehouse"}]

REPORTS = {
    # ---- sales
    "sales_by_product": {"title": "Sales by product", "area": "sales", "params": DATE_RANGE, "query": ops.sales_by_product, "perm": OPS},
    "sales_by_brand": {"title": "Sales by brand", "area": "sales", "params": DATE_RANGE, "query": ops.sales_by_brand, "perm": OPS},
    "sales_by_supplier": {"title": "Sales by supplier (manufacturer)", "area": "sales", "params": DATE_RANGE, "query": ops.sales_by_supplier, "perm": OPS},
    "sales_by_customer": {"title": "Sales by customer", "area": "sales", "params": DATE_RANGE, "query": ops.sales_by_customer, "perm": OPS},
    "sales_by_territory": {"title": "Sales by territory", "area": "sales", "params": DATE_RANGE, "query": ops.sales_by_territory, "perm": OPS},
    "sales_by_route": {"title": "Sales by route", "area": "sales", "params": DATE_RANGE, "query": ops.sales_by_route, "perm": OPS},
    "sales_by_rep": {"title": "Sales by rep", "area": "sales", "params": DATE_RANGE, "query": ops.sales_by_rep, "perm": OPS},
    "out_of_area_activity": {"title": "Out-of-area activity (working areas)", "area": "sales", "params": DATE_RANGE + [{"name": "rep", "type": "rep"}], "query": ops.out_of_area_activity, "perm": OPS},
    "area_coverage": {"title": "Working areas — coverage", "area": "sales", "params": [], "query": ops.area_coverage, "perm": OPS},
    "customers_without_area": {"title": "Customers in no working area", "area": "sales", "params": [], "query": ops.customers_without_area, "perm": OPS},
    "sales_by_period": {"title": "Sales by day / month", "area": "sales", "params": DATE_RANGE + [{"name": "granularity", "type": "select", "options": ["day", "month"]}], "query": ops.sales_by_period, "perm": OPS},
    "order_fulfilment": {"title": "Order fulfilment rate", "area": "sales", "params": DATE_RANGE, "query": ops.order_fulfilment, "perm": OPS},
    "pending_orders": {"title": "Pending orders / backorders", "area": "sales", "params": [], "query": ops.pending_orders, "perm": OPS},
    "dispatched_not_delivered": {"title": "Dispatched not delivered", "area": "sales", "params": [], "query": ops.dispatched_not_delivered, "perm": OPS},
    "rejected_orders": {"title": "Rejected / cancelled orders", "area": "sales", "params": DATE_RANGE, "query": ops.rejected_orders, "perm": OPS},
    "scheme_cost": {"title": "Scheme cost", "area": "sales", "params": DATE_RANGE, "query": ops.scheme_cost, "perm": OPS},
    # ---- purchasing
    "purchase_register": {"title": "Purchase register", "area": "purchasing", "params": DATE_RANGE + [{"name": "supplier", "type": "supplier"}], "query": ops.purchase_register, "perm": OPS},
    "grn_register": {"title": "GRN register", "area": "purchasing", "params": DATE_RANGE, "query": ops.grn_register, "perm": OPS},
    "receipts_by_item": {"title": "Purchase receipts by item (boxes received, rejected, still due)", "area": "purchasing", "params": DATE_RANGE + [{"name": "supplier", "type": "supplier"}], "query": ops.receipts_by_item, "perm": OPS},
    "purchases_by_supplier": {"title": "Supplier-wise purchases", "area": "purchasing", "params": DATE_RANGE, "query": ops.purchases_by_supplier, "perm": OPS},
    "pending_pos": {"title": "Pending purchase orders", "area": "purchasing", "params": [], "query": ops.pending_pos, "perm": OPS},
    "price_variance": {"title": "Purchase price variance", "area": "purchasing", "params": DATE_RANGE, "query": ops.price_variance, "perm": OPS},
    "returns_to_supplier": {"title": "Returns to supplier", "area": "purchasing", "params": DATE_RANGE, "query": ops.returns_to_supplier, "perm": OPS},
    # ---- inventory
    "stock_summary": {"title": "Stock summary (in stock / dispatched / delivered)", "area": "inventory", "params": WAREHOUSE, "query": ops.stock_summary, "perm": OPS},
    "stock_report": {"title": "Stock report — opening, purchase, issuance, wastage, return, closing", "area": "inventory", "params": DATE_RANGE + [{"name": "category", "type": "category"}, {"name": "search", "type": "text"}, {"name": "show_zero", "type": "checkbox"}], "query": ops.stock_report, "perm": OPS},
    "stock_ledger": {"title": "Stock ledger per item", "area": "inventory", "params": DATE_RANGE + WAREHOUSE + [{"name": "variant", "type": "variant"}], "query": ops.stock_ledger, "perm": OPS},
    "movement_summary": {"title": "Movement summary by type", "area": "inventory", "params": DATE_RANGE + WAREHOUSE, "query": ops.movement_summary, "perm": OPS},
    "stock_valuation": {"title": "Stock valuation", "area": "inventory", "params": WAREHOUSE, "query": ops.stock_valuation, "perm": "inventory.view_valuation"},
    "stock_ageing": {"title": "Stock ageing (days in stock)", "area": "inventory", "params": WAREHOUSE, "query": ops.stock_ageing, "perm": OPS},
    "expiry_report": {"title": "Expiry report", "area": "inventory", "params": WAREHOUSE + [{"name": "days", "type": "number", "default": 90}], "query": ops.expiry_report, "perm": OPS},
    "low_stock": {"title": "Low stock / reorder", "area": "inventory", "params": [], "query": ops.low_stock, "perm": OPS},
    "dead_stock": {"title": "Dead stock", "area": "inventory", "params": [], "query": ops.dead_stock, "perm": OPS},
    "wastage_by_reason": {"title": "Wastage & loss by reason", "area": "inventory", "params": DATE_RANGE, "query": ops.wastage_by_reason, "perm": OPS},
    "transfer_register": {"title": "Transfer register", "area": "inventory", "params": DATE_RANGE, "query": ops.transfer_register, "perm": OPS},
    "count_variance_history": {"title": "Count variance history", "area": "inventory", "params": DATE_RANGE, "query": ops.count_variance_history, "perm": OPS},
    # ---- delivery
    "trip_register": {"title": "Trip register", "area": "delivery", "params": DATE_RANGE, "query": ops.trip_register, "perm": OPS},
    "driver_performance": {"title": "Driver performance", "area": "delivery", "params": DATE_RANGE, "query": ops.driver_performance, "perm": OPS},
    "vehicle_usage": {"title": "Vehicle usage & running cost", "area": "delivery", "params": DATE_RANGE, "query": ops.vehicle_usage, "perm": OPS},
    "delivery_success": {"title": "Delivery success rate & undelivered reasons", "area": "delivery", "params": DATE_RANGE, "query": ops.delivery_success, "perm": OPS},
    "cash_per_trip": {"title": "Cash collected per trip", "area": "delivery", "params": DATE_RANGE, "query": ops.cash_per_trip, "perm": OPS},
    # ---- receivables / payables
    "ar_ageing": {"title": "Receivables ageing", "area": "receivables", "params": AS_OF + [{"name": "customer", "type": "customer"}], "query": fin.ar_ageing, "perm": OPS},
    "collection_report": {"title": "Collection report by staff / rep", "area": "receivables", "params": DATE_RANGE, "query": ops.collection_report, "perm": OPS},
    "cash_with_reps": {"title": "Cash with reps", "area": "receivables", "params": AS_OF, "query": fin.cash_with_reps, "perm": OPS},
    "overdue_list": {"title": "Overdue invoices", "area": "receivables", "params": [], "query": ops.overdue_list, "perm": OPS},
    "cheque_register": {"title": "Cheque register", "area": "receivables", "params": [{"name": "cheque_status", "type": "select", "options": ["", "pending", "deposited", "cleared", "bounced"]}], "query": ops.cheque_register, "perm": OPS},
    "credit_utilisation": {"title": "Credit limit utilisation", "area": "receivables", "params": [], "query": ops.credit_utilisation, "perm": OPS},
    "ap_ageing": {"title": "Payables ageing", "area": "payables", "params": AS_OF + [{"name": "supplier", "type": "supplier"}], "query": fin.ap_ageing, "perm": OPS},
    "company_claims": {"title": "Company discount claims", "area": "sales", "params": DATE_RANGE + [{"name": "supplier", "type": "supplier"}, {"name": "status", "type": "select", "options": ["posted", "partially_paid", "paid"]}], "query": fin.company_claims, "perm": FIN},
    "payment_register": {"title": "Payment register", "area": "payables", "params": DATE_RANGE + [{"name": "direction", "type": "select", "options": ["", "in", "out"]}], "query": ops.payment_register, "perm": OPS},
    "due_payments": {"title": "Due payments calendar", "area": "payables", "params": [], "query": ops.due_payments_calendar, "perm": OPS},
    # ---- financial
    "trial_balance": {"title": "Trial balance", "area": "financial", "params": DATE_RANGE + [{"name": "level", "type": "number", "default": 4}], "query": fin.trial_balance, "perm": FIN},
    "general_ledger": {"title": "General ledger", "area": "financial", "params": DATE_RANGE + [{"name": "account", "type": "account"}], "query": fin.general_ledger, "perm": FIN},
    "journal_register": {"title": "Journal register", "area": "financial", "params": DATE_RANGE + [{"name": "type", "type": "select", "options": ["", "manual", "auto", "opening", "closing"]}], "query": fin.journal_register, "perm": FIN},
    "day_book": {"title": "Day book", "area": "financial", "params": [{"name": "date", "type": "date"}], "query": fin.day_book, "perm": FIN},
    "profit_and_loss": {"title": "Profit & loss", "area": "financial", "params": DATE_RANGE + [{"name": "cost_centre", "type": "cost_centre"}, {"name": "compare_from", "type": "date"}, {"name": "compare_to", "type": "date"}], "query": fin.profit_and_loss, "perm": FIN},
    "balance_sheet": {"title": "Balance sheet", "area": "financial", "params": AS_OF, "query": fin.balance_sheet, "perm": FIN},
    "cash_flow": {"title": "Cash flow (indirect)", "area": "financial", "params": DATE_RANGE, "query": fin.cash_flow, "perm": FIN},
    "cash_bank_book": {"title": "Cash & bank book", "area": "financial", "params": DATE_RANGE + [{"name": "account", "type": "account"}], "query": fin.cash_bank_book, "perm": FIN},
    "tax_summary": {"title": "Tax summary per tax type", "area": "financial", "params": DATE_RANGE, "query": fin.tax_summary, "perm": FIN},
    "sales_tax_register": {"title": "Sales tax register (FBR tax invoices & credit notes)", "area": "financial", "params": DATE_RANGE, "query": ops.sales_tax_register, "perm": FIN},
    "expense_by_category": {"title": "Expense by category", "area": "financial", "params": DATE_RANGE, "query": fin.expense_by_category, "perm": FIN},
    "inventory_reconciliation": {"title": "Inventory valuation vs GL", "area": "financial", "params": AS_OF, "query": fin.inventory_reconciliation, "perm": FIN},
    "gross_margin_by_supplier": {"title": "Gross margin by supplier / brand", "area": "financial", "params": DATE_RANGE, "query": ops.gross_margin_by_supplier, "perm": FIN},
    "audit_trail": {"title": "Audit trail of journals", "area": "financial", "params": DATE_RANGE, "query": fin.audit_trail, "perm": FIN},
}


def catalog():
    return [{"key": k, "title": v["title"], "area": v["area"], "params": v["params"], "permission": v["perm"]} for k, v in REPORTS.items()]
