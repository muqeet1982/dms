from django.urls import path

from . import ext_views as v

urlpatterns = [
    path("me", v.ExtMeView.as_view()),
    path("me/sales-summary", v.ExtSalesSummaryView.as_view()),
    path("orders", v.ExtOrdersView.as_view()),
    path("orders/preview", v.ExtOrderPreviewView.as_view()),  # before <pk>: "preview" is not an order id
    path("orders/<str:pk>", v.ExtOrderDetailView.as_view()),
    path("orders/<str:pk>/pdf", v.ExtOrderPdfView.as_view()),
    path("orders/<str:pk>/invoice-pdf", v.ExtOrderPdfView.as_view(), {"kind": "invoice"}),
    path("orders/<str:pk>/cancel", v.ExtOrderCancelView.as_view()),
    path("customers", v.ExtCustomersView.as_view()),
    path("customers/<str:pk>/orders", v.ExtShopOrdersView.as_view()),
    path("customers/<str:pk>/location", v.ExtCustomerPinView.as_view()),
    path("customers/similar", v.ExtCustomerMatchView.as_view()),
    path("products", v.ExtProductsView.as_view()),
    path("stock", v.ExtStockView.as_view()),
    path("deliveries", v.ExtDeliveriesView.as_view()),
    path("collections", v.ExtCollectionsView.as_view()),
    path("my-areas", v.ExtMyAreasView.as_view()),
    path("shifts", v.ExtShiftsView.as_view()),
    path("shifts/current", v.ExtShiftsView.as_view()),
    path("shifts/<str:pk>/<str:action>", v.ExtShiftActionView.as_view()),
    path("visits", v.ExtVisitsView.as_view()),
    path("tracking/policy", v.ExtTrackingPolicyView.as_view()),
    path("tracking/pings", v.ExtTrackingPingsView.as_view()),
    path("tracking/me", v.ExtTrackingMeView.as_view()),
    path("webhooks", v.ExtWebhooksView.as_view()),
]
