from django.urls import path
from apps.core.routers import OptionalSlashRouter

from . import views

router = OptionalSlashRouter()
router.register("api-keys", views.ApiKeyViewSet, basename="api-keys")
router.register("webhooks", views.WebhookEndpointViewSet, basename="webhooks")
router.register("imports", views.ImportJobViewSet, basename="imports")

urlpatterns = [path("exports/<str:kind>", views.ExportView.as_view())] + router.urls
