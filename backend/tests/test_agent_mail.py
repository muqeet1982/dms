"""
The mailbox the teams building this system talk through.

What matters here is not the CRUD: it is that a sender cannot claim to be someone else, that a polled
message arrives once, that a retried send is not a second message, and that only the side being asked can
say a request is done.
"""
import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def mail(db):
    from apps.platform.models import AgentToken

    tokens = {}
    for role, name in (("mobile", "Mobile app AI"), ("server", "Server AI"), ("owner", "Business owner")):
        _, raw = AgentToken.issue(name, role)
        client = APIClient()
        client.credentials(HTTP_X_AGENT_TOKEN=raw)
        tokens[role] = client
    return tokens


def _post(client, **body):
    return client.post("/api/v1/agent/messages", body, format="json")


def test_a_message_needs_a_token(mail):
    anon = APIClient()
    assert anon.get("/api/v1/agent/messages").status_code == 401
    anon.credentials(HTTP_X_AGENT_TOKEN="agent_mobile_madeitup")
    r = anon.get("/api/v1/agent/messages")
    assert r.status_code == 401 and r.data["type"] == "bad_agent_token"


def test_the_sender_is_the_token_not_the_payload(mail):
    """A sender cannot claim to be someone else — that is the whole point of a credential per role."""
    r = _post(mail["mobile"], to="server", body="hello", **{"from": "server"})
    assert r.status_code == 201 and r.data["from"] == "mobile", r.content


def test_a_request_is_delivered_once_and_then_stops_coming_back(mail):
    sent = _post(mail["mobile"], to="server", kind="request", subject="Stock on products",
                 body="Can /ext/v1/products carry a stock band?")
    assert sent.status_code == 201 and sent.data["status"] == "open"

    first = mail["server"].get("/api/v1/agent/messages")
    assert first.data["count"] == 1 and first.data["open_for_me"] == 1
    assert first.data["results"][0]["needs_an_answer"] is True
    assert first.data["results"][0]["body"].startswith("Can /ext")

    again = mail["server"].get("/api/v1/agent/messages")
    assert again.data["count"] == 0, "an agent that polls must not reread the same message for ever"

    history = mail["server"].get("/api/v1/agent/messages?all=1")
    assert history.data["count"] == 1, "but nothing is lost — it is still there when asked for"


def test_peeking_does_not_consume(mail):
    _post(mail["mobile"], to="server", body="look but do not touch")
    peek = mail["server"].get("/api/v1/agent/messages?peek=1")
    assert peek.data["count"] == 1
    assert mail["server"].get("/api/v1/agent/messages").data["count"] == 1, "peeking left it unread"


def test_a_retried_send_is_the_same_message(mail):
    """The phone's queue retries anything it is unsure about; a handover must not arrive twice."""
    first = _post(mail["mobile"], to="server", body="one", client_id="abc-123")
    again = _post(mail["mobile"], to="server", body="one", client_id="abc-123")
    assert first.status_code == 201 and again.status_code == 200
    assert first.data["id"] == again.data["id"]

    from apps.platform.models import AgentMessage

    assert AgentMessage.objects.count() == 1


def test_replying_answers_the_request_and_keeps_the_thread(mail):
    sent = _post(mail["mobile"], to="server", kind="request", subject="Visits", body="Please build it", thread="visits")
    reply = _post(mail["server"], in_reply_to=sent.data["id"], body="Built, and here is where it differs")
    assert reply.status_code == 201
    assert reply.data["kind"] == "reply" and reply.data["to"] == "mobile"
    assert reply.data["thread"] == "visits", "a reply inherits the thread rather than starting one"

    one = mail["mobile"].get(f"/api/v1/agent/messages/{sent.data['id']}")
    assert one.data["status"] == "answered" and len(one.data["replies"]) == 1


def test_only_the_side_being_asked_can_say_it_is_done(mail):
    sent = _post(mail["mobile"], to="server", kind="request", subject="Stock", body="?")
    mine = mail["mobile"].patch(f"/api/v1/agent/messages/{sent.data['id']}", {"status": "closed"}, format="json")
    assert mine.status_code == 403, "the side that asked does not get to declare it answered"

    theirs = mail["server"].patch(f"/api/v1/agent/messages/{sent.data['id']}", {"status": "closed"}, format="json")
    assert theirs.status_code == 200 and theirs.data["status"] == "closed"


def test_the_threads_view_answers_anything_pending(mail):
    _post(mail["mobile"], to="server", kind="request", subject="One", body="a", thread="one")
    _post(mail["server"], to="mobile", kind="question", subject="Two", body="b", thread="two")
    _post(mail["mobile"], to="server", body="just a note", thread="one")

    out = mail["server"].get("/api/v1/agent/threads")
    rows = {r["thread"]: r for r in out.data["results"]}
    assert rows["one"]["messages"] == 2 and rows["one"]["open_for_me"] == 1
    assert rows["two"]["open_for_them"] == 1 and rows["two"]["open_for_me"] == 0


def test_a_message_with_no_body_or_a_silly_address_is_refused(mail):
    assert _post(mail["mobile"], to="server", body="   ").status_code == 400
    assert _post(mail["mobile"], to="nobody", body="x").status_code == 400
    assert _post(mail["mobile"], to="mobile", body="x").status_code == 400, "nobody writes to themselves"
    assert _post(mail["mobile"], to="server", body="x" * 200_001).status_code == 413


def test_the_subject_and_thread_fall_back_to_the_first_line(mail):
    r = _post(mail["mobile"], to="server", body="# Visits: check-in and no-order calls\n\nthe rest")
    assert r.data["subject"] == "Visits: check-in and no-order calls"
    assert r.data["thread"] == "visits-check-in-and-no-order-calls"


def test_the_owner_reads_the_whole_conversation(mail):
    """The human in the middle should be able to follow it without being in the loop."""
    sent = _post(mail["mobile"], to="server", kind="request", subject="Visits", body="please")
    _post(mail["server"], in_reply_to=sent.data["id"], body="done")
    out = mail["owner"].get("/api/v1/agent/messages?all=1&peek=1")
    assert out.data["count"] == 2 and {m["from"] for m in out.data["results"]} == {"mobile", "server"}
