from django.urls import path
from apps.core.routers import OptionalSlashRouter

from . import views

router = OptionalSlashRouter()
router.register("me/sessions", views.SessionViewSet, basename="me-sessions")
router.register("roles", views.RoleViewSet, basename="roles")
router.register("users", views.TenantUserViewSet, basename="users")

urlpatterns = [
    path("auth/login", views.LoginView.as_view(), name="auth-login"),
    path("auth/refresh", views.RefreshView.as_view(), name="auth-refresh"),
    path("auth/logout", views.LogoutView.as_view(), name="auth-logout"),
    path("auth/qr-login/create", views.QrLoginCreateView.as_view(), name="auth-qr-login-create"),
    path("auth/qr-login/status", views.QrLoginStatusView.as_view(), name="auth-qr-login-status"),
    path("auth/qr-login/redeem", views.QrLoginRedeemView.as_view(), name="auth-qr-login-redeem"),
    path("auth/otp/request", views.OTPRequestView.as_view(), name="auth-otp-request"),
    path("auth/otp/verify", views.OTPVerifyView.as_view(), name="auth-otp-verify"),
    path("auth/password/reset", views.PasswordResetView.as_view(), name="auth-password-reset"),
    path("auth/password/change", views.ChangePasswordView.as_view(), name="auth-password-change"),
    path("me", views.MeView.as_view(), name="me"),
    path("me/permissions", views.MePermissionsView.as_view(), name="me-permissions"),
    path("permissions-catalog", views.PermissionsCatalogView.as_view(), name="permissions-catalog"),
    path("permission-pages", views.PermissionPagesView.as_view(), name="permission-pages"),
] + router.urls
