from django.urls import path
from apps.core.routers import OptionalSlashRouter

from . import views

router = OptionalSlashRouter()
router.register("reports/saved-filters", views.SavedFilterViewSet, basename="saved-filters")
router.register("reports/schedules", views.ReportScheduleViewSet, basename="report-schedules")

urlpatterns = router.urls + [
    path("dashboard", views.TenantDashboardView.as_view()),
    path("reports", views.ReportCatalogView.as_view()),
    path("reports/<str:key>", views.ReportRunView.as_view()),
]
