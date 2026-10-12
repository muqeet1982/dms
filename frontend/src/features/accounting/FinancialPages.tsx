import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { date as fmtDate, money, startOfMonth, today } from "@/lib/format";
import { EntitySelect } from "@/components/ui/EntitySelect";
import { DateInput } from "@/components/ui/DateInput";
import { Alert, Field, PageHeader, Spinner, StatCard } from "@/components/ui/Bits";

/**
 * The four statements an accountant asks for, each on its own page rather than buried in the report runner.
 *
 * They are all served by `/api/v1/reports/<key>`, which also writes the PDF and the spreadsheet — so what is on
 * screen, what prints and what downloads are the same figures by construction, and the layouts here can be
 * shaped like the statements they are (indented accounts, groups in bold, the one number that matters called
 * out) instead of a grid of columns.
 */
function useStatement(key: string, params: Record<string, any>, enabled = true) {
  return useQuery({
    queryKey: ["statement", key, params],
    enabled,
    queryFn: () => api.get(`/api/v1/reports/${key}`, params),
  });
}

/** Print · PDF · Excel, on every statement. Print goes straight to the dialog; the others save a file. */
function Exports({ k, params }: { k: string; params: Record<string, any> }) {
  return (
    <>
      <button className="btn btn-outline-secondary" onClick={() => api.printDoc(`/api/v1/reports/${k}`, { ...params, format: "pdf" })}>
        <i className="ti ti-printer me-1" />Print
      </button>
      <button className="btn btn-outline-secondary" onClick={() => api.download(`/api/v1/reports/${k}`, `${k}.pdf`, { ...params, format: "pdf" })}>
        <i className="ti ti-file-type-pdf me-1" />PDF
      </button>
      <button className="btn btn-outline-success" onClick={() => api.download(`/api/v1/reports/${k}`, `${k}.xlsx`, { ...params, format: "xlsx" })}>
        <i className="ti ti-file-spreadsheet me-1" />Excel
      </button>
    </>
  );
}

/** An account line, indented by its level so the chart's shape is visible at a glance. */
const nameCell = (r: any) => (
  <td style={{ paddingLeft: 8 + (Number(r.level || 1) - 1) * 18 }} className={r.is_group ? "fw-semibold" : ""}>
    <span className="text-muted fs-11 me-2">{r.code}</span>{r.name}
  </td>
);
const rowClass = (r: any) => (r.is_group ? (Number(r.level) === 1 ? "table-light fw-bold" : "fw-semibold") : "");

function Range({ from, to, setFrom, setTo, extra }: { from: string; to: string; setFrom: (v: string) => void; setTo: (v: string) => void; extra?: React.ReactNode }) {
  return (
    <div className="card mb-3"><div className="card-body py-3"><div className="row align-items-end">
      <Field label="From" col="col-6 col-md-3"><DateInput className="form-control" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
      <Field label="To" col="col-6 col-md-3"><DateInput className="form-control" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
      {extra}
    </div></div></div>
  );
}

export function TrialBalancePage() {
  const [from, setFrom] = useState(startOfMonth());
  const [to, setTo] = useState(today());
  const params = { date_from: from, date_to: to };
  const q = useStatement("trial_balance", params);
  const t = q.data?.totals || {};
  return (
    <>
      <PageHeader title="Trial balance" crumbs={[{ label: "Accounting", to: "/app/accounting" }, { label: "Trial balance" }]}
        actions={<Exports k="trial_balance" params={params} />} />
      <Range from={from} to={to} setFrom={setFrom} setTo={setTo} />
      {q.isLoading ? <Spinner /> : (
        <>
          {t.balanced === false && <Alert kind="danger">The ledger does not balance for this range — debits {money(t.closing_debit)} against credits {money(t.closing_credit)}. That is a posting fault worth looking at before anything is filed.</Alert>}
          <div className="card"><div className="card-body p-0"><div className="table-responsive">
            <table className="table table-sm align-middle mb-0 fs-13">
              <thead className="table-light">
                <tr>
                  <th rowSpan={2} style={{ minWidth: 240 }}>Account</th>
                  <th colSpan={2} className="text-center border-start">Opening</th>
                  <th colSpan={2} className="text-center border-start">In this period</th>
                  <th colSpan={2} className="text-center border-start">Closing</th>
                </tr>
                <tr>
                  <th className="num border-start">Debit</th><th className="num">Credit</th>
                  <th className="num border-start">Debit</th><th className="num">Credit</th>
                  <th className="num border-start">Debit</th><th className="num">Credit</th>
                </tr>
              </thead>
              <tbody>
                {(q.data?.rows || []).map((r: any, i: number) => (
                  <tr key={i} className={rowClass(r)}>
                    {nameCell(r)}
                    <td className="num border-start">{money(r.opening_debit)}</td><td className="num">{money(r.opening_credit)}</td>
                    <td className="num border-start">{money(r.debit)}</td><td className="num">{money(r.credit)}</td>
                    <td className="num border-start">{money(r.closing_debit)}</td><td className="num">{money(r.closing_credit)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot><tr className="fw-bold" style={{ borderTop: "2px solid #202C4B" }}>
                <td>Total</td><td colSpan={4} />
                <td className="num border-start">{money(t.closing_debit)}</td><td className="num">{money(t.closing_credit)}</td>
              </tr></tfoot>
            </table>
          </div></div></div>
          <div className="fs-12 text-muted mt-2">{t.balanced ? <><i className="ti ti-circle-check text-success me-1" />Debits and credits agree.</> : null}</div>
        </>
      )}
    </>
  );
}

export function IncomeStatementPage() {
  const [from, setFrom] = useState(startOfMonth());
  const [to, setTo] = useState(today());
  const params = { date_from: from, date_to: to };
  const q = useStatement("profit_and_loss", params);
  const t = q.data?.totals || {};
  const profit = Number(t.net_profit || 0);
  return (
    <>
      <PageHeader title="Income statement" crumbs={[{ label: "Accounting", to: "/app/accounting" }, { label: "Income statement" }]}
        actions={<Exports k="profit_and_loss" params={params} />} />
      <Range from={from} to={to} setFrom={setFrom} setTo={setTo} />
      {q.isLoading ? <Spinner /> : (
        <>
          <div className="row g-3 mb-3">
            <div className="col-6 col-xl-3"><StatCard label="Income" value={money(t.income)} icon="ti-trending-up" color="primary" /></div>
            <div className="col-6 col-xl-3"><StatCard label="Cost of sales" value={money(t.cost_of_sales)} icon="ti-package" color="warning" /></div>
            <div className="col-6 col-xl-3"><StatCard label="Gross profit" value={money(t.gross_profit)} icon="ti-chart-line" color="info" /></div>
            <div className="col-6 col-xl-3"><StatCard label={profit >= 0 ? "Net profit" : "Net loss"} value={money(Math.abs(profit))} icon={profit >= 0 ? "ti-coin" : "ti-alert-triangle"} color={profit >= 0 ? "success" : "danger"} /></div>
          </div>
          <div className="card"><div className="card-body p-0"><div className="table-responsive">
            <table className="table table-sm align-middle mb-0 fs-13">
              <thead className="table-light"><tr><th style={{ minWidth: 280 }}>Account</th><th className="num" style={{ width: 200 }}>Amount</th></tr></thead>
              <tbody>{(q.data?.rows || []).map((r: any, i: number) => (
                <tr key={i} className={rowClass(r)}>{nameCell(r)}<td className="num">{money(r.amount)}</td></tr>
              ))}</tbody>
              <tfoot>
                <tr className="fw-semibold"><td className="text-end">Gross profit</td><td className="num">{money(t.gross_profit)}</td></tr>
                <tr className="fw-semibold"><td className="text-end">Expenses</td><td className="num">{money(t.expenses)}</td></tr>
                <tr className="fw-bold fs-14" style={{ borderTop: "2px solid #202C4B" }}>
                  <td className="text-end">{profit >= 0 ? "Net profit" : "Net loss"}</td>
                  <td className={`num ${profit >= 0 ? "text-success" : "text-danger"}`}>{money(Math.abs(profit))}</td>
                </tr>
              </tfoot>
            </table>
          </div></div></div>
        </>
      )}
    </>
  );
}

export function BalanceSheetPage() {
  const [asOf, setAsOf] = useState(today());
  const params = { as_of: asOf };
  const q = useStatement("balance_sheet", params);
  const t = q.data?.totals || {};
  const rows: any[] = q.data?.rows || [];
  const side = (types: string[]) => rows.filter((r) => types.includes(r.type));
  const block = (title: string, list: any[], total: any) => (
    <div className="card mb-3"><div className="card-header py-2"><strong className="fs-13">{title}</strong></div>
      <div className="card-body p-0"><div className="table-responsive"><table className="table table-sm align-middle mb-0 fs-13">
        <tbody>{list.map((r, i) => <tr key={i} className={rowClass(r)}>{nameCell(r)}<td className="num" style={{ width: 180 }}>{money(r.amount)}</td></tr>)}</tbody>
        <tfoot><tr className="fw-bold" style={{ borderTop: "2px solid #202C4B" }}><td className="text-end">Total {title.toLowerCase()}</td><td className="num">{money(total)}</td></tr></tfoot>
      </table></div></div>
    </div>
  );
  return (
    <>
      <PageHeader title="Balance sheet" crumbs={[{ label: "Accounting", to: "/app/accounting" }, { label: "Balance sheet" }]}
        actions={<Exports k="balance_sheet" params={params} />} />
      <div className="card mb-3"><div className="card-body py-3"><div className="row align-items-end">
        <Field label="As at" col="col-6 col-md-3"><DateInput className="form-control" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></Field>
        <div className="col-md-9 text-md-end">
          {t.balanced === false
            ? <span className="badge bg-danger-transparent fs-12"><i className="ti ti-alert-triangle me-1" />Assets do not equal liabilities and equity</span>
            : <span className="badge bg-success-transparent fs-12"><i className="ti ti-circle-check me-1" />Balanced at {money(t.assets)}</span>}
        </div>
      </div></div></div>
      {q.isLoading ? <Spinner /> : (
        <div className="row">
          <div className="col-lg-6">{block("Assets", side(["asset"]), t.assets)}</div>
          <div className="col-lg-6">
            {block("Liabilities", side(["liability"]), t.liabilities)}
            {block("Equity", side(["equity"]), t.equity)}
            <div className="card"><div className="card-body py-2 d-flex justify-content-between fw-bold">
              <span>Liabilities and equity</span><span>{money(t.liabilities_and_equity)}</span>
            </div></div>
          </div>
        </div>
      )}
    </>
  );
}

export function GeneralLedgerPage() {
  const [from, setFrom] = useState(startOfMonth());
  const [to, setTo] = useState(today());
  const [account, setAccount] = useState<string | null>(null);
  const params = { date_from: from, date_to: to, ...(account ? { account } : {}) };
  const q = useStatement("general_ledger", params);
  const t = q.data?.totals || {};
  return (
    <>
      <PageHeader title="General ledger" crumbs={[{ label: "Accounting", to: "/app/accounting" }, { label: "General ledger" }]}
        actions={<Exports k="general_ledger" params={params} />} />
      <Range from={from} to={to} setFrom={setFrom} setTo={setTo} extra={
        <Field label="Account" col="col-md-4" hint="Leave empty for every account">
          <EntitySelect endpoint="/api/v1/accounting/accounts" query={{ is_group: false }} value={account} onChange={setAccount}
            mapOption={(a: any) => ({ id: a.id, label: `${a.code} ${a.name}`, sub: a.type })} placeholder="All accounts" />
        </Field>} />
      {q.isLoading ? <Spinner /> : (
        <div className="card"><div className="card-body p-0"><div className="table-responsive">
          <table className="table table-sm align-middle mb-0 fs-13">
            <thead className="table-light"><tr>
              <th style={{ width: 110 }}>Date</th><th style={{ width: 120 }}>Journal</th><th>Account</th><th>Narration</th>
              <th className="num" style={{ width: 130 }}>Debit</th><th className="num" style={{ width: 130 }}>Credit</th><th className="num" style={{ width: 150 }}>Balance</th>
            </tr></thead>
            <tbody>
              <tr className="table-light fw-semibold"><td colSpan={6}>Opening balance</td><td className="num">{money(t.opening)}</td></tr>
              {(q.data?.rows || []).map((r: any, i: number) => (
                <tr key={i}>
                  <td>{fmtDate(r.date)}</td>
                  <td>{r.journal_id ? <Link to={`/app/accounting/journals/${r.journal_id}`}>{r.journal}</Link> : r.journal}</td>
                  <td className="fs-12">{r.account}</td>
                  <td className="fs-12 text-muted">{r.description}<div className="fs-11">{r.source}</div></td>
                  <td className="num">{Number(r.debit) ? money(r.debit) : "—"}</td>
                  <td className="num">{Number(r.credit) ? money(r.credit) : "—"}</td>
                  <td className="num fw-medium">{money(r.balance)}</td>
                </tr>
              ))}
              {!(q.data?.rows || []).length && <tr><td colSpan={7} className="text-center text-muted py-4">Nothing posted in this range.</td></tr>}
            </tbody>
            <tfoot><tr className="fw-bold" style={{ borderTop: "2px solid #202C4B" }}><td colSpan={6} className="text-end">Closing balance</td><td className="num">{money(t.closing)}</td></tr></tfoot>
          </table>
        </div></div></div>
      )}
    </>
  );
}
