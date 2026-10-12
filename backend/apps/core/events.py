"""
Tiny in-process domain event bus. Services emit events; handlers do notifications,
webhooks and cache updates. Handlers must never raise into the caller.
"""
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)
_handlers = defaultdict(list)


def on(event_name):
    def deco(fn):
        _handlers[event_name].append(fn)
        return fn

    return deco


def emit(event_name, **payload):
    for fn in _handlers.get(event_name, []):
        try:
            fn(**payload)
        except Exception:  # pragma: no cover
            logger.exception("event handler %s failed for %s", fn.__name__, event_name)
    for fn in _handlers.get("*", []):
        try:
            fn(event_name, **payload)
        except Exception:  # pragma: no cover
            logger.exception("wildcard handler failed for %s", event_name)
