from apps.core.routers import OptionalSlashRouter

from . import views

router = OptionalSlashRouter()
router.register("vehicles", views.VehicleViewSet, basename="vehicles")
router.register("drivers", views.DriverViewSet, basename="drivers")
router.register("pick-lists", views.PickListViewSet, basename="pick-lists")
router.register("trips", views.TripViewSet, basename="trips")
router.register("delivery-notes", views.DeliveryNoteViewSet, basename="delivery-notes")
urlpatterns = router.urls
