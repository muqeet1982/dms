from apps.core.routers import OptionalSlashRouter

from . import views

router = OptionalSlashRouter()
router.register("sales-orders", views.SalesOrderViewSet, basename="sales-orders")
router.register("invoices", views.SalesInvoiceViewSet, basename="invoices")
router.register("credit-notes", views.CreditNoteViewSet, basename="credit-notes")
router.register("rep-collections", views.RepCollectionViewSet, basename="rep-collections")
router.register("rep-settlements", views.RepSettlementViewSet, basename="rep-settlements")
urlpatterns = router.urls
