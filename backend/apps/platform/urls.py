from django.urls import path
from apps.core.routers import OptionalSlashRouter

from . import security, views
from .app_release import MobileAppReleaseViewSet

router = OptionalSlashRouter()
router.register("plans", views.PlanViewSet, basename="platform-plans")
router.register("addons", views.AddOnViewSet, basename="platform-addons")
router.register("coupons", views.CouponViewSet, basename="platform-coupons")
router.register("tenants", views.TenantViewSet, basename="platform-tenants")
router.register("subscriptions", views.SubscriptionViewSet, basename="platform-subscriptions")
router.register("invoices", views.PlatformInvoiceViewSet, basename="platform-invoices")
router.register("payments", views.PlatformPaymentViewSet, basename="platform-payments")
router.register("announcements", views.AnnouncementViewSet, basename="platform-announcements")
router.register("templates", views.MessageTemplateViewSet, basename="platform-templates")
router.register("notification-log", views.NotificationLogViewSet, basename="platform-notification-log")
router.register("feature-flags", views.FeatureFlagViewSet, basename="platform-feature-flags")
router.register("legal", views.LegalDocumentViewSet, basename="platform-legal")
router.register("tickets", views.SupportTicketViewSet, basename="platform-tickets")
router.register("audit", views.PlatformAuditViewSet, basename="platform-audit")
router.register("users", views.PlatformUserViewSet, basename="platform-users")
router.register("app-releases", MobileAppReleaseViewSet, basename="platform-app-releases")

urlpatterns = [
    path("dashboard", views.PlatformDashboardView.as_view()),
    path("settings", views.PlatformSettingsView.as_view()),
    path("settings/email-test", views.EmailTestView.as_view(), name="platform-email-test"),
    path("settings/asset", views.PlatformSettingsView.as_view()),
    path("security/status", security.SecurityStatusView.as_view()),
    path("security/bans", security.BanListView.as_view()),
    path("security/firewall", security.FirewallView.as_view()),
    path("health", views.PlatformHealthView.as_view()),
] + router.urls
