"""
Load forms → New (owner, 12 Nov 2026): "when click on create load form it give 2 option save and send to warehouse. then i
should create load form not when click on create load form". Create load form only previews what the form will carry —
nothing is saved — and the form is made by Save (kept, not sent) or Send to warehouse, with the quantities corrected on
the preview.
"""
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

from .conftest import seed_basic

pytestmark = pytest.mark.django_db


def _order(api, wh, v, cust, qty="6"):
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": qty}]}, format="json")
    assert r.status_code == 201, r.content
    return r.data


def _forms(tenant):
    from apps.delivery.models import PickList

    with tenant_context(tenant):
        return PickList.objects.count()


def test_the_preview_saves_nothing(api, tenant):
    wh, _s, v, cust = seed_basic(tenant)
    o = _order(api, wh, v, cust)
    r = api.get("/api/v1/pick-lists/preview", {"warehouse": str(wh.id), "order": o["id"]})
    assert r.status_code == 200, r.content
    line = r.data["lines"][0]
    assert (D(line["pending"]), D(line["qty"]), D(line["available"])) == (6, 6, 94), line   # 100 on hand, 6 held for it
    assert r.data["order_number"] == o["number"] and line["batches"]
    assert _forms(tenant) == 0, "nothing is made by looking"
    assert api.get(f"/api/v1/sales-orders/{o['id']}").data["status"] == "confirmed"


def test_save_makes_it_with_the_corrected_quantities_and_send_hands_it_over(api, tenant):
    wh, _s, v, cust = seed_basic(tenant)
    o = _order(api, wh, v, cust)
    ol = api.get("/api/v1/pick-lists/preview", {"warehouse": str(wh.id), "order": o["id"]}).data["lines"][0]["order_line"]
    too_many = api.post("/api/v1/pick-lists", {"warehouse": str(wh.id), "orders": [o["id"]], "lines": [{"order_line": ol, "qty": "7"}]}, format="json")
    assert too_many.status_code == 422 and _forms(tenant) == 0, too_many.content
    none = api.post("/api/v1/pick-lists", {"warehouse": str(wh.id), "orders": [o["id"]], "lines": [{"order_line": ol, "qty": "0"}]}, format="json")
    assert none.status_code == 422 and _forms(tenant) == 0, none.content

    saved = api.post("/api/v1/pick-lists", {"warehouse": str(wh.id), "orders": [o["id"]], "notes": "two only", "lines": [{"order_line": ol, "qty": "2"}]}, format="json")
    assert saved.status_code == 201 and saved.data["status"] == "open", saved.content
    assert sum(D(l["qty_to_pick"]) for l in saved.data["lines"]) == 2 and saved.data["notes"] == "two only"

    o2 = _order(api, wh, v, cust, qty="3")
    sent = api.post("/api/v1/pick-lists", {"warehouse": str(wh.id), "orders": [o2["id"]], "send": True}, format="json")
    assert sent.status_code == 201 and sent.data["status"] == "sent", sent.content
    assert sum(D(l["qty_to_pick"]) for l in sent.data["lines"]) == 3, "no corrections: the whole order"
