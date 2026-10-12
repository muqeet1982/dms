from rest_framework.routers import DefaultRouter


class OptionalSlashRouter(DefaultRouter):
    """Routes match with or without a trailing slash (mobile clients often omit it)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trailing_slash = "/?"
