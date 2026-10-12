from django.urls import path
from apps.core.routers import OptionalSlashRouter

from . import views

router = OptionalSlashRouter()
router.register("settings/warehouses", views.WarehouseViewSet, basename="warehouses")
router.register("settings/bins", views.BinViewSet, basename="bins")
router.register("settings/reason-codes", views.ReasonCodeViewSet, basename="reason-codes")
router.register("inventory/stock", views.StockLevelViewSet, basename="stock")
router.register("inventory/movements", views.StockMovementViewSet, basename="movements")
router.register("inventory/batches", views.BatchViewSet, basename="batches")
router.register("inventory/transfers", views.StockTransferViewSet, basename="transfers")
router.register("inventory/adjustments", views.StockAdjustmentViewSet, basename="adjustments")
router.register("inventory/counts", views.StockCountViewSet, basename="counts")
router.register("inventory/alerts", views.InventoryAlertViewSet, basename="alerts")

urlpatterns = [
    path("inventory/valuation", views.ValuationView.as_view()),
    path("inventory/opening-stock", views.OpeningStockView.as_view()),
] + router.urls
