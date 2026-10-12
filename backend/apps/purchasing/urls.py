from apps.core.routers import OptionalSlashRouter

from . import views

router = OptionalSlashRouter()
router.register("purchase-orders", views.PurchaseOrderViewSet, basename="purchase-orders")
router.register("goods-receipts", views.GoodsReceiptViewSet, basename="goods-receipts")
router.register("purchase-bills", views.PurchaseBillViewSet, basename="purchase-bills")
router.register("purchase-returns", views.PurchaseReturnViewSet, basename="purchase-returns")
urlpatterns = router.urls
