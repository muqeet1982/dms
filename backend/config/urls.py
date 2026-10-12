from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.platform.app_release import AssetLinksView, PublicAppDownloadView, PublicAppInfoView, PublicAppQrView
from apps.platform import agent_mail
from apps.platform.views import PublicBrandingView, PublicLegalView

urlpatterns = [
    path("api/v1/app/<str:platform>", PublicAppInfoView.as_view(), name="app-info"),
    path("api/v1/app/<str:platform>/download", PublicAppDownloadView.as_view(), name="app-download"),
    path("api/v1/app/<str:platform>/qr.png", PublicAppQrView.as_view(), name="app-qr"),
    path(".well-known/assetlinks.json", AssetLinksView.as_view(), name="assetlinks"),
    path("api/v1/branding", PublicBrandingView.as_view(), name="public-branding"),
    path("api/v1/legal/<str:kind>", PublicLegalView.as_view(), name="public-legal"),
    # the mailbox the teams building this system talk through (apps/platform/agent_mail.py)
    path("api/v1/agent/messages", agent_mail.AgentMessagesView.as_view(), name="agent-messages"),
    path("api/v1/agent/messages/<str:pk>", agent_mail.AgentMessageView.as_view(), name="agent-message"),
    path("api/v1/agent/threads", agent_mail.AgentThreadsView.as_view(), name="agent-threads"),
    path("api/v1/", include("apps.accounts.urls")),
    path("api/v1/", include("apps.core.urls")),
    path("api/v1/platform/", include("apps.platform.urls")),
    path("api/v1/", include("apps.catalog.urls")),
    path("api/v1/", include("apps.partners.urls")),
    path("api/v1/", include("apps.inventory.urls")),
    path("api/v1/", include("apps.purchasing.urls")),
    path("api/v1/", include("apps.sales.urls")),
    path("api/v1/", include("apps.delivery.urls")),
    path("api/v1/", include("apps.accounting.urls")),
    path("api/v1/", include("apps.reports.urls")),
    path("api/v1/", include("apps.integrations.urls")),
    path("ext/v1/", include("apps.integrations.ext_urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
