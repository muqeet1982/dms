from django.apps import AppConfig


class ReportsConfig(AppConfig):
    name = "apps.reports"
    label = "reports"

    def ready(self):
        from . import registry  # noqa: F401
