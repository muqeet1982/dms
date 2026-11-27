"""
Owner, 27 Nov 2026: "remove asif malik and kashif malik from helper / delivery person dropdowns". A user can be taken off
the crew lists; the trip form's Delivery person and Helper stop offering them, and a trip that already names them still
reads their name. The user form's "Can sign in on" is kept too — it was sent and never saved.
"""
import pytest

pytestmark = pytest.mark.django_db


def _names(api):
    r = api.get("/api/v1/people", {"page_size": 200})
    assert r.status_code == 200, r.content
    return [p["full_name"] for p in r.data["results"]]


def _helper(api, tenant):
    from apps.accounts.models import GroupProfile

    role = GroupProfile.objects.filter(tenant=tenant).exclude(name__iexact="owner").first()
    r = api.post("/api/v1/users", {"full_name": "Asif Helper", "email": "asif@alpha.pk", "password": "Abcd-1234xyz", "roles": [str(role.id)]}, format="json")
    assert r.status_code == 201, r.content
    return r.data


def test_a_user_off_the_crew_lists_is_not_offered(api, tenant):
    u = _helper(api, tenant)
    assert "Asif Helper" in _names(api)
    r = api.patch(f"/api/v1/users/{u['id']}", {"on_crew_lists": False}, format="json")
    assert r.status_code == 200, r.content
    assert r.data["on_crew_lists"] is False
    assert "Asif Helper" not in _names(api)
    # a trip that already names them still finds the name
    assert api.get(f"/api/v1/people/{u['user_id']}").data["full_name"] == "Asif Helper"
    api.patch(f"/api/v1/users/{u['id']}", {"on_crew_lists": True}, format="json")
    assert "Asif Helper" in _names(api)


def test_where_a_user_signs_in_is_kept(api, tenant):
    u = _helper(api, tenant)
    r = api.patch(f"/api/v1/users/{u['id']}", {"login_surface": "app"}, format="json")
    assert r.status_code == 200, r.content
    assert api.get(f"/api/v1/users/{u['id']}").data["login_surface"] == "app"
