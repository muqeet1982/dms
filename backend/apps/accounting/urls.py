from django.urls import path
from apps.core.routers import OptionalSlashRouter

from . import views

router = OptionalSlashRouter()
router.register("accounting/accounts", views.AccountViewSet, basename="accounts")
router.register("accounting/journals", views.JournalEntryViewSet, basename="journals")
router.register("accounting/fiscal-years", views.FiscalYearViewSet, basename="fiscal-years")
router.register("accounting/periods", views.PeriodViewSet, basename="periods")
router.register("accounting/cost-centres", views.CostCentreViewSet, basename="cost-centres")
router.register("accounting/bank-accounts", views.BankAccountViewSet, basename="bank-accounts")
router.register("accounting/cash-accounts", views.CashAccountViewSet, basename="cash-accounts")
router.register("accounting/payments", views.PaymentViewSet, basename="payments")
router.register("accounting/expenses", views.ExpenseViewSet, basename="expenses")
router.register("accounting/opening-balances", views.OpeningBalanceViewSet, basename="opening-balances")
router.register("settings/posting-rules", views.PostingRuleViewSet, basename="posting-rules")
router.register("accounting/cheque-books", views.ChequeBookViewSet, basename="cheque-books")
router.register("accounting/cheques-register", views.ChequeLeafViewSet, basename="cheque-leaves")

urlpatterns = [path("accounting/dashboard", views.AccountingDashboardView.as_view())] + router.urls
