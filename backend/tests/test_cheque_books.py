"""
The books of blank cheques the business writes from.

The distinction that shapes all of this: a cheque we **write** comes out of our book and has to be accounted
for leaf by leaf, because the bank will ask about the stub. A cheque a customer **gives** us is their paper
with their numbering and belongs to no book of ours.
"""
import pytest
from decimal import Decimal

pytestmark = pytest.mark.django_db


@pytest.fixture
def bank(api, tenant):
    from apps.core.tenancy import tenant_context

    with tenant_context(tenant, tenant.owner):
        from apps.accounting.models import Account, BankAccount

        acct = Account.objects.filter(type="asset", is_group=False).first()
        return BankAccount.objects.create(name="Main current", bank="HBL", branch="Gulberg", account_no="0101-7788", account=acct)


@pytest.fixture
def parties(api, tenant):
    """A payment is always to or from somebody, and the API wants their id, not their name."""
    from .conftest import seed_basic

    _wh, supplier, _variant, customer = seed_basic(tenant)
    return {"supplier": str(supplier.id), "customer": str(customer.id)}


@pytest.fixture
def book(api, bank):
    r = api.post("/api/v1/accounting/cheque-books", {
        "bank_account": str(bank.id), "prefix": "A", "first_no": 101, "last_no": 125, "issued_on": "2026-10-01",
    }, format="json")
    assert r.status_code == 201, r.content
    return r.data


def test_a_book_lays_out_its_leaves(api, book):
    assert book["leaves_total"] == 25 and book["leaves_available"] == 25
    assert book["next_number"] == "A101", "the next cheque to write is the first one in the book"
    assert book["label"] == "A101–A125"

    leaves = api.get(f"/api/v1/accounting/cheque-books/{book['id']}/leaves")
    assert leaves.status_code == 200 and len(leaves.data) == 25
    assert [l["number"] for l in leaves.data[:3]] == ["A101", "A102", "A103"]
    assert all(l["status"] == "available" for l in leaves.data)


def test_a_range_that_is_not_a_book_is_refused(api, bank):
    backwards = api.post("/api/v1/accounting/cheque-books", {"bank_account": str(bank.id), "first_no": 50, "last_no": 10}, format="json")
    assert backwards.status_code in (400, 422) and "before the first" in str(backwards.data)

    huge = api.post("/api/v1/accounting/cheque-books", {"bank_account": str(bank.id), "first_no": 1, "last_no": 99999}, format="json")
    assert huge.status_code in (400, 422) and "check the numbers" in str(huge.data)


def test_paying_by_cheque_takes_the_next_leaf(api, tenant, bank, book, parties):
    """The owner's ask: the first unused number, already filled in, and changeable."""
    pay = api.post("/api/v1/accounting/payments", {
        "direction": "out", "partner_type": "supplier", "partner_id": parties["supplier"],
        "amount": "5000", "method": "cheque", "bank_account": str(bank.id), "date": "2026-10-12",
    }, format="json")
    assert pay.status_code == 201, pay.content
    assert pay.data["cheque_no"] == "A101", "the next cheque in the book was used without anyone typing it"

    after = api.get(f"/api/v1/accounting/cheque-books/{book['id']}").data
    assert after["leaves_available"] == 24 and after["leaves_issued"] == 1
    assert after["next_number"] == "A102", "and the next one moved on"

    leaf = next(l for l in api.get(f"/api/v1/accounting/cheque-books/{book['id']}/leaves").data if l["number"] == "A101")
    assert leaf["status"] == "issued" and leaf["payment_number"] == pay.data["number"]
    assert leaf["payee"] and Decimal(leaf["amount"]) == Decimal("5000")


def test_a_particular_cheque_can_be_chosen(api, bank, book, parties):
    pay = api.post("/api/v1/accounting/payments", {
        "direction": "out", "partner_type": "supplier", "partner_id": parties["supplier"], "amount": "100",
        "method": "cheque", "bank_account": str(bank.id), "cheque_no": "A110",
    }, format="json")
    assert pay.status_code == 201 and pay.data["cheque_no"] == "A110"

    leaves = {l["number"]: l for l in api.get(f"/api/v1/accounting/cheque-books/{book['id']}/leaves").data}
    assert leaves["A110"]["status"] == "issued" and leaves["A101"]["status"] == "available"

    # and the same leaf cannot be written twice
    again = api.post("/api/v1/accounting/payments", {
        "direction": "out", "partner_type": "supplier", "partner_id": parties["supplier"], "amount": "100",
        "method": "cheque", "bank_account": str(bank.id), "cheque_no": "A110",
    }, format="json")
    assert again.status_code in (400, 422) and "already written" in str(again.data)


def test_a_cheque_received_from_a_customer_touches_no_book(api, bank, book, parties):
    """Their paper, their numbering — recorded on the payment and nowhere else."""
    pay = api.post("/api/v1/accounting/payments", {
        "direction": "in", "partner_type": "customer", "partner_id": parties["customer"], "amount": "2500",
        "method": "cheque", "bank_account": str(bank.id), "cheque_no": "999999", "cheque_bank": "Meezan",
    }, format="json")
    assert pay.status_code == 201, pay.content
    assert pay.data["cheque_no"] == "999999"
    after = api.get(f"/api/v1/accounting/cheque-books/{book['id']}").data
    assert after["leaves_available"] == 25, "an incoming cheque must not consume one of ours"


def test_a_spoiled_leaf_is_voided_and_skipped(api, bank, book, parties):
    leaves = api.get(f"/api/v1/accounting/cheque-books/{book['id']}/leaves").data
    first = leaves[0]
    voided = api.post(f"/api/v1/accounting/cheque-books/{book['id']}/leaves/{first['id']}/void", {"reason": "Printer chewed it"}, format="json")
    assert voided.status_code == 200 and voided.data["status"] == "void"

    assert api.get(f"/api/v1/accounting/cheque-books/{book['id']}").data["next_number"] == "A102", "a spoiled leaf is skipped"

    pay = api.post("/api/v1/accounting/payments", {
        "direction": "out", "partner_type": "supplier", "partner_id": parties["supplier"], "amount": "10",
        "method": "cheque", "bank_account": str(bank.id),
    }, format="json")
    assert pay.data["cheque_no"] == "A102"

    # a leaf that has been written cannot then be called spoiled
    written = next(l for l in api.get(f"/api/v1/accounting/cheque-books/{book['id']}/leaves").data if l["number"] == "A102")
    refused = api.post(f"/api/v1/accounting/cheque-books/{book['id']}/leaves/{written['id']}/void", {"reason": "oops"}, format="json")
    assert refused.status_code in (400, 422) and "cancel that payment first" in str(refused.data)


def test_a_book_that_is_used_up_stops_offering_itself(api, bank, parties):
    small = api.post("/api/v1/accounting/cheque-books", {"bank_account": str(bank.id), "first_no": 1, "last_no": 2}, format="json").data
    for _ in range(2):
        assert api.post("/api/v1/accounting/payments", {
            "direction": "out", "partner_type": "supplier", "partner_id": parties["supplier"], "amount": "10",
            "method": "cheque", "bank_account": str(bank.id),
        }, format="json").status_code == 201
    after = api.get(f"/api/v1/accounting/cheque-books/{small['id']}").data
    assert after["status"] == "exhausted" and after["leaves_available"] == 0 and after["next_number"] == ""


def test_the_register_lists_every_cheque_and_what_is_free(api, bank, book):
    free = api.get("/api/v1/accounting/cheques-register", {"available": "1", "bank_account": str(bank.id)})
    assert free.status_code == 200 and len(free.data) == 25
    assert free.data[0]["number"] == "A101", "nearest number first, so the first row is the one to write"
    assert free.data[0]["bank_name"] == "Main current"


def test_a_book_with_cheques_written_cannot_be_deleted(api, bank, book, parties):
    api.post("/api/v1/accounting/payments", {"direction": "out", "partner_type": "supplier", "partner_id": parties["supplier"],
                                             "amount": "10", "method": "cheque", "bank_account": str(bank.id)}, format="json")
    refused = api.delete(f"/api/v1/accounting/cheque-books/{book['id']}")
    assert refused.status_code in (400, 422) and "part of the record" in str(refused.data)


def test_the_register_exports(api, bank, book, parties):
    api.post("/api/v1/accounting/payments", {"direction": "out", "partner_type": "supplier", "partner_id": parties["supplier"],
                                             "amount": "10", "method": "cheque", "bank_account": str(bank.id)}, format="json")
    csv = api.get("/api/v1/accounting/cheques-register/export", {"format": "csv"})
    assert csv.status_code == 200 and csv["Content-Type"].startswith("text/csv")
    text = csv.content.decode("utf-8-sig")
    assert "Cheque" in text and "A101" in text

    xlsx = api.get("/api/v1/accounting/cheques-register/export", {"format": "xlsx"})
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK", "an xlsx is a zip"
