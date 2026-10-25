from django.urls import path
from apps.core.routers import OptionalSlashRouter

from . import views

router = OptionalSlashRouter()
router.register("products", views.ProductViewSet, basename="products")
router.register("product-offers", views.ProductOfferViewSet, basename="product-offers")
router.register("variants", views.VariantViewSet, basename="variants")
router.register("packagings", views.PackagingViewSet, basename="packagings")
router.register("barcodes", views.BarcodeViewSet, basename="barcodes")
router.register("categories", views.CategoryViewSet, basename="categories")
router.register("brands", views.BrandViewSet, basename="brands")
router.register("uoms", views.UnitOfMeasureViewSet, basename="uoms")
router.register("price-lists", views.PriceListViewSet, basename="price-lists")
router.register("schemes", views.SchemeViewSet, basename="schemes")
router.register("settings/tax-types", views.TaxTypeViewSet, basename="tax-types")
router.register("settings/tax-classes", views.TaxClassViewSet, basename="tax-classes")

urlpatterns = [path("labels/print", views.LabelPrintView.as_view())] + router.urls
