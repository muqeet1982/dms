from django.urls import path

from . import views

urlpatterns = [
    path("settings", views.FBRSettingsView.as_view()),
    path("sync", views.FBRSyncView.as_view()),
    path("lookups", views.FBRLookupView.as_view()),
    path("check-registration", views.FBRCheckRegistrationView.as_view()),
]
