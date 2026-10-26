"""Default 4-level chart of accounts (§8.1) + posting rule mapping + default fiscal year."""
from datetime import date

from django.utils import timezone

COA = [
    ("1", "ASSETS", "asset", None),
    ("1-01", "Current Assets", "asset", "1"),
    ("1-01-001", "Cash & Cash Equivalents", "asset", "1-01"),
    ("1-01-001-0001", "Cash in Hand", "asset", "1-01-001", "cash"),
    ("1-01-001-0002", "Petty Cash", "asset", "1-01-001", "cash"),
    ("1-01-001-0003", "Rep Cash Clearing", "asset", "1-01-001", "rep_clearing"),
    ("1-01-002", "Bank Accounts", "asset", "1-01"),
    ("1-01-002-0001", "Main Bank Account", "asset", "1-01-002", "bank"),
    ("1-01-003", "Accounts Receivable", "asset", "1-01"),
    ("1-01-003-0001", "Trade Receivables", "asset", "1-01-003", "receivable"),
    ("1-01-003-0002", "Post-dated Cheques Received", "asset", "1-01-003", "pdc"),
    ("1-01-003-0003", "Manufacturer Claims Receivable", "asset", "1-01-003", "receivable"),
    ("1-01-004", "Inventory", "asset", "1-01"),
    ("1-01-004-0001", "Stock in Hand", "asset", "1-01-004", "inventory"),
    ("1-01-004-0002", "Stock in Transit", "asset", "1-01-004", "inventory"),
    ("1-01-004-0003", "Damaged / Quarantine Stock", "asset", "1-01-004", "inventory"),
    ("1-01-005", "Advances & Prepayments", "asset", "1-01"),
    ("1-01-005-0001", "Advance to Suppliers", "asset", "1-01-005", "advance"),
    ("1-01-005-0002", "Prepaid Expenses", "asset", "1-01-005", ""),
    ("1-01-005-0003", "Input Tax 1 Receivable", "asset", "1-01-005", "tax"),
    ("1-01-005-0004", "Input Tax 2 Receivable", "asset", "1-01-005", "tax"),
    ("1-01-005-0005", "Withholding Tax Receivable", "asset", "1-01-005", "tax"),
    ("1-02", "Non-current Assets", "asset", "1"),
    ("1-02-001", "Fixed Assets", "asset", "1-02"),
    ("1-02-001-0001", "Vehicles", "asset", "1-02-001", "fixed_asset"),
    ("1-02-001-0002", "Equipment", "asset", "1-02-001", "fixed_asset"),
    ("1-02-001-0003", "Furniture", "asset", "1-02-001", "fixed_asset"),
    ("1-02-002", "Accumulated Depreciation", "asset", "1-02"),
    ("1-02-002-0001", "Accumulated Depreciation – Vehicles", "asset", "1-02-002", "contra"),
    ("2", "LIABILITIES", "liability", None),
    ("2-01", "Current Liabilities", "liability", "2"),
    ("2-01-001", "Accounts Payable", "liability", "2-01"),
    ("2-01-001-0001", "Trade Payables", "liability", "2-01-001", "payable"),
    ("2-01-001-0002", "GRNI (Goods Received Not Invoiced)", "liability", "2-01-001", "grni"),
    ("2-01-002", "Tax Payable", "liability", "2-01"),
    ("2-01-002-0001", "Output Tax 1 (Sales Tax)", "liability", "2-01-002", "tax"),
    ("2-01-002-0002", "Output Tax 2 (Further / Other Tax)", "liability", "2-01-002", "tax"),
    ("2-01-002-0003", "Withholding Tax Payable", "liability", "2-01-002", "tax"),
    ("2-01-003", "Accrued Expenses & Other", "liability", "2-01"),
    ("2-01-003-0001", "Accrued Expenses", "liability", "2-01-003", ""),
    ("2-01-003-0002", "Advances from Customers", "liability", "2-01-003", "advance"),
    ("2-01-003-0003", "Cheques Payable", "liability", "2-01-003", "pdc"),
    ("2-02", "Long-term Liabilities", "liability", "2"),
    ("2-02-001", "Loans", "liability", "2-02"),
    ("2-02-001-0001", "Bank Loans", "liability", "2-02-001", ""),
    ("3", "EQUITY", "equity", None),
    ("3-01", "Owner's Equity", "equity", "3"),
    ("3-01-001", "Capital & Reserves", "equity", "3-01"),
    ("3-01-001-0001", "Capital", "equity", "3-01-001", ""),
    ("3-01-001-0002", "Drawings", "equity", "3-01-001", ""),
    ("3-01-001-0003", "Retained Earnings", "equity", "3-01-001", "retained"),
    ("3-01-001-0004", "Current Year Profit", "equity", "3-01-001", "current_profit"),
    ("3-01-001-0005", "Opening Balance Equity", "equity", "3-01-001", "opening"),
    ("4", "INCOME", "income", None),
    ("4-01", "Sales Revenue", "income", "4"),
    ("4-01-001", "Sales", "income", "4-01"),
    ("4-01-001-0001", "Sales – Products", "income", "4-01-001", "sales"),
    ("4-01-001-0002", "Sales Returns", "income", "4-01-001", "contra"),
    ("4-01-001-0003", "Discounts Allowed", "income", "4-01-001", "contra"),
    ("4-02", "Other Income", "income", "4"),
    ("4-02-001", "Other Income", "income", "4-02"),
    ("4-02-001-0001", "Scheme / Incentive from Manufacturer", "income", "4-02-001", ""),
    ("4-02-001-0002", "Delivery Charges Income", "income", "4-02-001", ""),
    ("4-02-001-0003", "Misc Income", "income", "4-02-001", ""),
    ("5", "EXPENSES", "expense", None),
    ("5-01", "Cost of Sales", "expense", "5"),
    ("5-01-001", "Cost of Goods Sold", "expense", "5-01"),
    ("5-01-001-0001", "COGS", "expense", "5-01-001", "cogs"),
    ("5-01-002", "Purchase Variances", "expense", "5-01"),
    ("5-01-002-0001", "Purchase Price Variance", "expense", "5-01-002", ""),
    ("5-01-003", "Freight", "expense", "5-01"),
    ("5-01-003-0001", "Freight Inward", "expense", "5-01-003", ""),
    ("5-01-004", "Inventory Losses", "expense", "5-01"),
    ("5-01-004-0001", "Inventory Shrinkage", "expense", "5-01-004", ""),
    ("5-01-005", "Expiry", "expense", "5-01"),
    ("5-01-005-0001", "Expiry Loss", "expense", "5-01-005", ""),
    ("5-01-006", "Damages", "expense", "5-01"),
    ("5-01-006-0001", "Damaged Goods", "expense", "5-01-006", ""),
    ("5-01-007", "Transit", "expense", "5-01"),
    ("5-01-007-0001", "Transit Damage", "expense", "5-01-007", ""),
    ("5-02", "Selling & Distribution", "expense", "5"),
    ("5-02-001", "Distribution Expenses", "expense", "5-02"),
    ("5-02-001-0001", "Fuel", "expense", "5-02-001", ""),
    ("5-02-001-0002", "Vehicle Maintenance", "expense", "5-02-001", ""),
    ("5-02-001-0003", "Driver Wages", "expense", "5-02-001", ""),
    ("5-02-004", "Marketing", "expense", "5-02"),
    ("5-02-004-0001", "Samples & Promotion", "expense", "5-02-004", ""),
    ("5-02-005", "Commission", "expense", "5-02"),
    ("5-02-005-0001", "Sales Commission", "expense", "5-02-005", ""),
    ("5-03", "Administrative", "expense", "5"),
    ("5-03-001", "Admin Expenses", "expense", "5-03"),
    ("5-03-001-0001", "Salaries", "expense", "5-03-001", ""),
    ("5-03-001-0002", "Rent", "expense", "5-03-001", ""),
    ("5-03-001-0003", "Utilities", "expense", "5-03-001", ""),
    ("5-03-001-0004", "Communication", "expense", "5-03-001", ""),
    ("5-03-001-0005", "Office Supplies", "expense", "5-03-001", ""),
    ("5-03-001-0006", "Bank Charges", "expense", "5-03-001", ""),
    ("5-03-001-0007", "Software Subscription", "expense", "5-03-001", ""),
    ("5-04", "Financial", "expense", "5"),
    ("5-04-001", "Financial Expenses", "expense", "5-04"),
    ("5-04-001-0001", "Interest Expense", "expense", "5-04-001", ""),
    ("5-04-001-0002", "Bad Debts", "expense", "5-04-001", ""),
]

POSTING_MAP = {
    "inventory": "1-01-004-0001", "in_transit": "1-01-004-0002", "quarantine": "1-01-004-0003", "cogs": "5-01-001-0001", "ar": "1-01-003-0001", "ap": "2-01-001-0001",
    "sales": "4-01-001-0001", "sales_return": "4-01-001-0002", "discount": "4-01-001-0003", "tax1_out": "2-01-002-0001", "tax2_out": "2-01-002-0002", "tax1_in": "1-01-005-0003", "tax2_in": "1-01-005-0004",
    "grni": "2-01-001-0002", "ppv": "5-01-002-0001", "freight_in": "5-01-003-0001", "rep_clearing": "1-01-001-0003", "cash": "1-01-001-0001", "petty_cash": "1-01-001-0002", "bank": "1-01-002-0001",
    "pdc_received": "1-01-003-0002", "manufacturer_claim": "1-01-003-0003", "cheques_payable": "2-01-003-0003", "shrinkage": "5-01-004-0001", "expiry_loss": "5-01-005-0001", "damaged_goods": "5-01-006-0001", "transit_damage": "5-01-007-0001",
    "samples": "5-02-004-0001", "withholding_payable": "2-01-002-0003", "withholding_receivable": "1-01-005-0005", "advance_customer": "2-01-003-0002", "advance_supplier": "1-01-005-0001",
    "opening_equity": "3-01-001-0005", "retained_earnings": "3-01-001-0003", "current_year_profit": "3-01-001-0004", "bank_charges": "5-03-001-0006", "other_income": "4-02-001-0003", "bad_debts": "5-04-001-0002",
    "manufacturer_incentive": "4-02-001-0001",
    "fed_out": "2-01-002-0002", "extra_tax_out": "2-01-002-0002", "further_tax_out": "2-01-002-0002", "st_withheld": "1-01-005-0005",
}

SYSTEM_CODES = set(POSTING_MAP.values())


def seed_chart_of_accounts(tenant):
    from .models import Account, BankAccount, CashAccount, FiscalYear, PostingRule

    if Account.all_objects.filter(tenant=tenant).exists():
        return
    by_code = {}
    for row in COA:
        code, name, typ, parent_code = row[:4]
        subtype = row[4] if len(row) > 4 else ""
        level = code.count("-") + 1
        acct = Account.all_objects.create(tenant=tenant, code=code, name=name, level=level, parent=by_code.get(parent_code), type=typ, subtype=subtype, is_group=level < 4, is_system=(code in SYSTEM_CODES or level < 4), currency=tenant.base_currency, allow_manual_posting=level == 4)
        by_code[code] = acct
    for role, code in POSTING_MAP.items():
        PostingRule.all_objects.create(tenant=tenant, role=role, document_type="*", account=by_code[code])
    CashAccount.all_objects.create(tenant=tenant, name="Cash in Hand", account=by_code["1-01-001-0001"])
    CashAccount.all_objects.create(tenant=tenant, name="Petty Cash", account=by_code["1-01-001-0002"], is_petty=True)
    BankAccount.all_objects.create(tenant=tenant, name="Main Bank Account", account=by_code["1-01-002-0001"])
    ensure_fiscal_year(tenant, timezone.localdate())


def ensure_fiscal_year(tenant, on_date):
    from .models import AccountingPeriod, FiscalYear

    fy = FiscalYear.all_objects.filter(tenant=tenant, start__lte=on_date, end__gte=on_date).first()
    if fy:
        return fy
    m = tenant.fiscal_year_start_month or 7
    start = date(on_date.year if on_date.month >= m else on_date.year - 1, m, 1)
    end_year = start.year + 1 if m > 1 else start.year
    end_month = m - 1 if m > 1 else 12
    import calendar

    end = date(end_year, end_month, calendar.monthrange(end_year, end_month)[1])
    name = f"FY{start.year}-{str(end.year)[2:]}" if start.year != end.year else f"FY{start.year}"
    fy = FiscalYear.all_objects.create(tenant=tenant, name=name, start=start, end=end)
    cur = start
    while cur <= end:
        last = date(cur.year, cur.month, calendar.monthrange(cur.year, cur.month)[1])
        AccountingPeriod.all_objects.create(tenant=tenant, fiscal_year=fy, start=cur, end=last)
        cur = last + timezone.timedelta(days=1)
    return fy
