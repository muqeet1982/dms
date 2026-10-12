"""
Test settings: production settings, with the two things tests must not share with the live site.

The cache is in-memory so throttle counters never touch the live Redis. `MEDIA_ROOT` is a scratch directory
because tests that save a file — a visit photo, a signature, an import — would otherwise write into the
running site's media folder, as whichever user ran pytest. That actually happened: a test run as root created
`media/visits/` owned by root, and the live server (running as `django`) then got a 500 saving the first real
check-in photo.
"""
import tempfile
from pathlib import Path

from .settings import *  # noqa: F401,F403

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "dms-tests"}}

MEDIA_ROOT = Path(tempfile.gettempdir()) / "dms-test-media"
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
