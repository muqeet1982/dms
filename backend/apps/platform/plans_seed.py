"""Recommended starting plans (§3.1). Prices are placeholders."""

UNLIMITED = -1

RECOMMENDED_PLANS = [
    {
        "code": "starter", "name": "Starter", "price": "5000", "trial_days": 14, "sort_order": 1,
        "limits": {"max_users": 5, "max_warehouses": 1, "max_suppliers": 1, "max_reps": 5, "max_products": 500, "max_orders_month": 500, "max_storage_mb": 2048, "max_custom_roles": 3, "api_rate_per_min": 0, "backup_retention_days": 7, "data_retention_months": 24},
        "features": {"barcode": True, "external_api": False, "advanced_accounting": False, "branding": False, "report_scheduling": False, "excel_export": False, "rep_portal": True, "multi_supplier": False, "support": "email"},
    },
    {
        "code": "growth", "name": "Growth", "price": "12000", "trial_days": 14, "sort_order": 2,
        "limits": {"max_users": 15, "max_warehouses": 3, "max_suppliers": 3, "max_reps": 25, "max_products": 3000, "max_orders_month": 3000, "max_storage_mb": 10240, "max_custom_roles": 10, "api_rate_per_min": 60, "backup_retention_days": 30, "data_retention_months": 36},
        "features": {"barcode": True, "external_api": True, "advanced_accounting": True, "branding": False, "report_scheduling": True, "excel_export": True, "rep_portal": True, "multi_supplier": True, "support": "email_chat"},
    },
    {
        "code": "pro", "name": "Pro", "price": "25000", "trial_days": 14, "sort_order": 3,
        "limits": {"max_users": 40, "max_warehouses": 10, "max_suppliers": UNLIMITED, "max_reps": 100, "max_products": 15000, "max_orders_month": 15000, "max_storage_mb": 51200, "max_custom_roles": UNLIMITED, "api_rate_per_min": 300, "backup_retention_days": 90, "data_retention_months": 60},
        "features": {"barcode": True, "external_api": True, "advanced_accounting": True, "branding": True, "report_scheduling": True, "excel_export": True, "rep_portal": True, "multi_supplier": True, "support": "priority"},
    },
    {
        "code": "enterprise", "name": "Enterprise", "price": "0", "trial_days": 30, "sort_order": 4, "is_public": False,
        "limits": {"max_users": UNLIMITED, "max_warehouses": UNLIMITED, "max_suppliers": UNLIMITED, "max_reps": UNLIMITED, "max_products": UNLIMITED, "max_orders_month": UNLIMITED, "max_storage_mb": UNLIMITED, "max_custom_roles": UNLIMITED, "api_rate_per_min": 1000, "backup_retention_days": 365, "data_retention_months": UNLIMITED},
        "features": {"barcode": True, "external_api": True, "advanced_accounting": True, "branding": True, "report_scheduling": True, "excel_export": True, "rep_portal": True, "multi_supplier": True, "support": "dedicated"},
    },
]

ADDONS = [
    {"code": "extra-users-5", "name": "+5 users", "unit_price": "1500", "unit_label": "pack of 5", "limit_key": "max_users", "units_per_qty": 5},
    {"code": "extra-warehouse", "name": "+1 warehouse", "unit_price": "2000", "unit_label": "warehouse", "limit_key": "max_warehouses", "units_per_qty": 1},
    {"code": "extra-storage-10gb", "name": "+10 GB storage", "unit_price": "1000", "unit_label": "10 GB", "limit_key": "max_storage_mb", "units_per_qty": 10240},
    {"code": "extra-reps-25", "name": "+25 reps", "unit_price": "2500", "unit_label": "pack of 25", "limit_key": "max_reps", "units_per_qty": 25},
]

MESSAGE_TEMPLATES = [
    {"key": "invite", "channel": "email", "subject": "You're invited to {{company}}", "body": "Hello {{name}},\n\nYou have been invited to {{company}}. Use code {{code}} or open {{link}} to set your password.", "variables": ["name", "company", "code", "link"]},
    {"key": "invite", "channel": "sms", "subject": "", "body": "{{company}} invite code: {{code}}. Open {{link}}", "variables": ["name", "company", "code", "link"]},
    {"key": "renewal_reminder", "channel": "email", "subject": "Your {{plan}} subscription {{when}}", "body": "Hello {{name}},\n\nYour {{company}} subscription on the {{plan}} plan {{when}} ({{end_date}}). Please renew to avoid interruption.", "variables": ["name", "company", "plan", "when", "end_date"]},
    {"key": "renewal_reminder", "channel": "sms", "subject": "", "body": "{{company}}: your {{plan}} subscription {{when}} ({{end_date}}). Please renew.", "variables": ["company", "plan", "when", "end_date"]},
    {"key": "payment_receipt", "channel": "email", "subject": "Payment received – {{amount}}", "body": "We received {{amount}} for invoice {{invoice}}. Thank you.", "variables": ["amount", "invoice"]},
    {"key": "suspension", "channel": "email", "subject": "{{company}} account suspended", "body": "Your account has been suspended: {{reason}}. Contact support to reactivate.", "variables": ["company", "reason"]},
    {"key": "order_status", "channel": "push", "subject": "Order {{number}} {{status}}", "body": "{{customer}}: order {{number}} is now {{status}}.", "variables": ["number", "status", "customer"]},
]
