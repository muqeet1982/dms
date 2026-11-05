"""
The income statement in its proper order (owner, 5 Nov 2026): "After deducting the Cost of Sales from Revenue, Gross
Profit should be presented. Selling and Distribution Expenses should then be deducted from Gross Profit, followed by
the presentation of Net Profit."

The page, the report screen, the PDF and the spreadsheet all print the same rows, so what is checked here is what they
all show. Net profit is what it always was, all income less all expenses, so the balance sheet does not move.
"""
import io
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db


def _post(tenant, *lines):
    """lines: (account code, debit, credit); the cash account takes the other side."""
    from apps.accounting.models import Account
    from apps.accounting.services import Line, PostingService, account_for

    with tenant_context(tenant, tenant.owner):
        for code, debit, credit in lines:
            a = Account.objects.get(code=code)
            PostingService.post([Line(a, debit=D(debit), credit=D(credit)), Line(account_for("cash"), debit=D(credit), credit=D(debit))], memo="test")


def _statement(api, **params):
    r = api.get("/api/v1/reports/profit_and_loss", params)
    assert r.status_code == 200, r.content
    return r.data


def _trade(tenant):
    _post(tenant,
          ("4-01-001-0001", "0", "1000"),   # sales
          ("4-01-001-0002", "50", "0"),     # sales returns
          ("4-02-001-0001", "0", "30"),     # other income
          ("5-01-001-0001", "600", "0"),    # cost of goods sold
          ("5-02-005-0001", "40", "0"),     # sales commission: selling and distribution
          ("5-03-001-0006", "100", "0"),    # bank charges, in the administrative group
          ("5-04-001-0001", "20", "0"))     # interest: financial


def test_it_reads_revenue_cost_gross_profit_selling_and_distribution_then_net_profit(api, tenant):
    _trade(tenant)
    data = _statement(api)
    names = [r["name"] for r in data["rows"] if r["kind"] != "account"]
    assert names == [
        "Revenue", "Total Revenue",
        "Less: Cost of Sales", "Total Cost of Sales",
        "Gross Profit",
        "Less: Selling and Distribution Expenses", "Total Selling and Distribution Expenses",
        "Less: Administrative Expenses", "Total Administrative Expenses",
        "Less: Financial Expenses", "Total Financial Expenses",
        "Add: Other Income", "Total Other Income",
        "Net Profit",
    ]
    amount = {r["name"]: D(r["amount"]) for r in data["rows"] if r["kind"] != "heading"}
    assert amount["Total Revenue"] == D("950"), "1000 sales less 50 returned"
    assert amount["Total Cost of Sales"] == D("600")
    assert amount["Gross Profit"] == D("350"), "revenue less cost of sales: other income is not in it"
    assert amount["Total Selling and Distribution Expenses"] == D("40")
    assert amount["Net Profit"] == D("220"), "350 - 40 - 100 - 20 + 30"

    t = data["totals"]
    assert D(t["gross_profit"]) == D("350") and D(t["net_profit"]) == D("220")
    assert D(t["net_profit"]) == D(t["income"]) - D(t["expenses"]), "still all income less all expenses"


def test_each_account_is_listed_under_its_own_section(api, tenant):
    _trade(tenant)
    section, under = None, {}
    for r in _statement(api)["rows"]:
        if r["kind"] == "heading":
            section = r["name"]
        elif r["kind"] == "account":
            under[r["code"]] = section
            assert not r["code"].count("-") < 3, "accounts only: the chart's own group rows are not repeated"
    assert under == {
        "4-01-001-0001": "Revenue", "4-01-001-0002": "Revenue",
        "5-01-001-0001": "Less: Cost of Sales",
        "5-02-005-0001": "Less: Selling and Distribution Expenses",
        "5-03-001-0006": "Less: Administrative Expenses",
        "5-04-001-0001": "Less: Financial Expenses",
        "4-02-001-0001": "Add: Other Income",
    }


def test_an_account_belongs_where_it_sits_in_the_chart_not_where_its_code_points(api, tenant):
    """TDN's Packing Material reads 5-01-007-0001 but sits under Selling & Distribution. The old statement read the code:
    its gross profit said one thing and its Cost of Sales group another."""
    from apps.accounting.models import Account

    with tenant_context(tenant, tenant.owner):
        moved = Account.objects.get(code="5-01-007-0001")
        moved.parent = Account.objects.get(code="5-02-001")
        moved.save(update_fields=["parent"])
    _post(tenant, ("4-01-001-0001", "0", "1000"), ("5-01-001-0001", "600", "0"), ("5-01-007-0001", "70", "0"))
    t = _statement(api)["totals"]
    assert D(t["cost_of_sales"]) == D("600") and D(t["selling_distribution"]) == D("70")
    assert D(t["gross_profit"]) == D("400") and D(t["net_profit"]) == D("330")


def test_a_head_the_company_added_reads_as_its_own_section_and_nothing_goes_missing(api, tenant):
    from apps.accounting.models import Account

    with tenant_context(tenant, tenant.owner):
        root = Account.objects.get(code="5")
        head = Account.objects.create(code="5-05", name="Depreciation", type="expense", is_group=True, level=2, parent=root)
        Account.objects.create(code="5-05-001-0001", name="Vehicles", type="expense", is_group=False, level=4, parent=head)
        Account.objects.create(code="5-06-001-0001", name="Under no group", type="expense", is_group=False, level=2, parent=root)
    _post(tenant, ("4-01-001-0001", "0", "1000"), ("5-05-001-0001", "90", "0"), ("5-06-001-0001", "10", "0"))
    data = _statement(api)
    names = [r["name"] for r in data["rows"] if r["kind"] == "heading"]
    assert names == ["Revenue", "Less: Cost of Sales", "Less: Selling and Distribution Expenses", "Less: Other Expenses", "Less: Depreciation"]
    assert D(data["totals"]["other_expenses"]) == D("100") and D(data["totals"]["net_profit"]) == D("900")


def test_with_nothing_posted_the_named_sections_still_read_at_nothing(api, tenant):
    data = _statement(api)
    assert [r["name"] for r in data["rows"]] == [
        "Revenue", "Total Revenue", "Less: Cost of Sales", "Total Cost of Sales", "Gross Profit",
        "Less: Selling and Distribution Expenses", "Total Selling and Distribution Expenses", "Net Profit"]
    assert all(D(r["amount"]) == 0 for r in data["rows"] if r["kind"] != "heading")


def test_a_loss_is_called_a_loss_and_the_totals_keep_their_sign(api, tenant):
    _post(tenant, ("4-01-001-0001", "0", "100"), ("5-01-001-0001", "150", "0"), ("5-02-005-0001", "25", "0"))
    data = _statement(api)
    amount = {r["name"]: D(r["amount"]) for r in data["rows"] if r["kind"] in ("profit", "net")}
    assert amount == {"Gross Loss": D("50"), "Net Loss": D("75")}
    assert D(data["totals"]["gross_profit"]) == D("-50") and D(data["totals"]["net_profit"]) == D("-75")


def test_the_balance_sheet_takes_the_same_net_profit_and_still_balances(api, tenant):
    _trade(tenant)
    net = D(_statement(api)["totals"]["net_profit"])
    sheet = api.get("/api/v1/reports/balance_sheet").data
    assert next(D(r["amount"]) for r in sheet["rows"] if r["name"].startswith("Current year profit")) == net
    assert D(sheet["totals"]["assets"]) == D(sheet["totals"]["liabilities_and_equity"])
    assert api.get("/api/v1/reports/cash_flow").status_code == 200


def test_a_date_range_and_a_comparison_period_read_the_same_way(api, tenant):
    _trade(tenant)
    data = _statement(api, date_from="2000-01-01", date_to="2000-12-31", compare_from="2000-01-01", compare_to="2099-12-31")
    assert D(data["totals"]["net_profit"]) == 0, "nothing was posted in 2000"
    assert D(data["compare"]["totals"]["gross_profit"]) == D("350") and D(data["compare"]["totals"]["net_profit"]) == D("220")
    assert [r["name"] for r in data["compare"]["rows"] if r["kind"] == "profit"] == ["Gross Profit"]


@pytest.fixture
def excel_on(tenant):
    with tenant_context(tenant, tenant.owner):
        sub = getattr(tenant, "subscription", None)
        if sub is not None:
            sub.plan_snapshot = {**sub.plan_snapshot, "features": {**(sub.plan_snapshot.get("features") or {}), "excel_export": True}}
            sub.save(update_fields=["plan_snapshot"])
    return tenant


def test_the_downloads_read_in_the_same_order_with_each_total_once(api, excel_on):
    from openpyxl import load_workbook

    _trade(excel_on)
    params = {"format": "csv"}
    text = api.get("/api/v1/reports/profit_and_loss", params).content.decode("utf-8-sig")
    order = ["Total Revenue", "Total Cost of Sales", "Gross Profit", "Total Selling and Distribution Expenses", "Net Profit"]
    positions = [text.index(n) for n in order]
    assert positions == sorted(positions), text
    assert "gross_profit" not in text and "net_profit" not in text, "the totals are in the rows, not again underneath"

    sheet = load_workbook(io.BytesIO(api.get("/api/v1/reports/profit_and_loss", {"format": "xlsx"}).content)).active
    rows = [[c.value for c in row] for row in sheet.iter_rows(values_only=False)]
    by_name = {row[1]: row[2] for row in rows}
    assert by_name["Gross Profit"] == 350 and by_name["Net Profit"] == 220
    assert "net_profit" not in [row[0] for row in rows]
    bold = {row[1].value: row[1].font.bold for row in sheet.iter_rows()}
    assert bold["Gross Profit"] and bold["Net Profit"] and not bold["Sales – Products"]

    pdf = api.get("/api/v1/reports/profit_and_loss", {"format": "pdf"})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")


def test_other_reports_still_print_their_totals_underneath(api, excel_on):
    text = api.get("/api/v1/reports/trial_balance", {"format": "csv"}).content.decode("utf-8-sig")
    assert api.get("/api/v1/reports/trial_balance").data["totals"]
    assert any(k in text for k in api.get("/api/v1/reports/trial_balance").data["totals"])
