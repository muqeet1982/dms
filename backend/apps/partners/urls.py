from apps.core.routers import OptionalSlashRouter

from . import views

router = OptionalSlashRouter()
router.register("suppliers", views.SupplierViewSet, basename="suppliers")
router.register("supplier-reps", views.SupplierRepViewSet, basename="supplier-reps")
router.register("customers", views.CustomerViewSet, basename="customers")
router.register("territories", views.TerritoryViewSet, basename="territories")
router.register("routes", views.RouteViewSet, basename="routes")
router.register("geofences", views.GeoFenceViewSet, basename="geofences")
from django.urls import path  # noqa: E402

from . import tracking_views  # noqa: E402

urlpatterns = router.urls + [
    path("visits", tracking_views.VisitsView.as_view()),
    path("tracking/policy", tracking_views.TrackingPolicyView.as_view()),
    path("tracking/purge-old", tracking_views.TrackingPurgeView.as_view()),
    path("tracking/live", tracking_views.TrackingLiveView.as_view()),
    path("tracking/users", tracking_views.TrackingTeamView.as_view()),
    path("tracking/users/<str:user_id>", tracking_views.TrackingUserDayView.as_view()),
]
