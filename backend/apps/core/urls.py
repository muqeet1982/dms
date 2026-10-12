from django.urls import path
from apps.core.routers import OptionalSlashRouter

from . import tenant_settings, views

router = OptionalSlashRouter()
router.register("cities", views.CityViewSet, basename="cities")
router.register("attachments", views.AttachmentViewSet, basename="attachments")
router.register("audit-log", views.AuditLogViewSet, basename="audit-log")
router.register("me/notifications", views.MyNotificationViewSet, basename="me-notifications")
router.register("me/devices", views.MyDeviceViewSet, basename="me-devices")
router.register("settings/numbering", views.NumberingViewSet, basename="numbering")
router.register("support-tickets", views.TenantSupportTicketViewSet, basename="support-tickets")

urlpatterns = [
    path("me/notification-preferences", views.MyNotificationPreferencesView.as_view()),
    path("history/<str:model>/<str:object_id>", views.DocumentHistoryView.as_view()),
    path("settings/company", tenant_settings.CompanyProfileView.as_view()),
    path("settings/company/logo", tenant_settings.CompanyProfileView.as_view()),
    path("settings/branding", tenant_settings.BrandingView.as_view()),
    path("settings/branding/asset", tenant_settings.BrandingView.as_view()),
    path("subscription", views.TenantSubscriptionView.as_view()),
    path("announcements", views.TenantAnnouncementsView.as_view()),
] + router.urls
