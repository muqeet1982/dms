"""Regenerate docs/API.md from the URL configuration:  cd backend && .venv/bin/python manage.py shell < ../docs/gen_api.py > ../docs/API.md"""
import re

from django.urls import get_resolver
from django.urls.resolvers import URLResolver

out = []


def walk(patterns, prefix=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            walk(p.url_patterns, prefix + str(p.pattern))
        else:
            cb, methods = p.callback, ""
            actions = getattr(cb, "actions", None)
            if actions:
                methods = ", ".join(m.upper() for m in actions)
            elif hasattr(cb, "view_class"):
                methods = ", ".join(m.upper() for m in ("get", "post", "put", "patch", "delete") if hasattr(cb.view_class, m))
            out.append((prefix + str(p.pattern), methods))


walk(get_resolver().url_patterns)
print("# API reference\n\n| Methods | Path |\n| --- | --- |")
seen = set()
for r, m in out:
    r = re.sub(r"\(\?P<(\w+)>[^)]+\)", r"{\1}", r.replace("^", "").replace("$", "").replace("/?", "")).replace("<str:", "{").replace(">", "}")
    if "format" in r or "schema" in r or "docs" in r or r in seen:
        continue
    seen.add(r)
    print(f"| {m} | `/{r}` |")
