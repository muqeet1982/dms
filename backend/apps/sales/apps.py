from django.apps import AppConfig


class SalesConfig(AppConfig):
    name = "apps.sales"
    label = "sales"

    def ready(self):
        from . import handlers  # noqa: F401
