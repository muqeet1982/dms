import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { date as fmtDate, labelCase, money, startOfMonth, today } from "@/lib/format";
import { EntitySelect } from "@/components/ui/EntitySelect";
import { DateInput } from "@/components/ui/DateInput";
import { Alert, Field, PageHeader, Spinner, StatCard } from "@/components/ui/Bits";
import { ColumnSwitch, loadHidden, saveHidden } from "@/components/ui/ColumnSwitch";

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

/** Print · PDF · Excel · CSV, on every statement — without the columns switched off. Print goes straight to the dialog. */
function Exports({ k, params, hide = [] }: { k: string; params: Record<string, any>; hide?: string[] }) {
  const query = { ...params, ...(hide.length ? { hide_columns: hide.join(",") } : {}) };
  return (
    <>
      <button className="btn btn-outline-secondary" onClick={() => api.printDoc(`/api/v1/reports/${k}`, { ...query, format: "pdf" })}>
        <i className="ti ti-printer me-1" />Print
      </button>
      <button className="btn btn-outline-secondary" onClick={() => api.download(`/api/v1/reports/${k}`, `${k}.pdf`, { ...query, format: "pdf" })}>
        <i className="ti ti-file-type-pdf me-1" />PDF
      </button>
      <button className="btn btn-outline-success" onClick={() => api.download(`/api/v1/reports/${k}`, `${k}.xlsx`, { ...query, format: "xlsx" })}>
        <i className="ti ti-file-spreadsheet me-1" />Excel
      </button>
      <button className="btn btn-outline-secondary" onClick={() => api.download(`/api/v1/reports/${k}`, `${k}.csv`, { ...query, format: "csv" })}>
        <i className="ti ti-file-type-csv me-1" />CSV
      </button>
    </>
  );
}

/**
 * The Columns switch of a statement (owner, 27 Nov 2026: "column selection for all reports … and pdf or print or csv
 * excel"). Kept under the same name as the Reports page's, so the statement reads the same from either place.
 */
function useColumns(k: string, columns: { key: string; label: string; fileOnly?: boolean }[]) {
  const store = `report.hide.${k}`;
  const [picked, setPicked] = useState<string[] | null>(null);
  const hidden = (picked ?? loadHidden(store)).filter((c) => columns.some((x) => x.key === c));
  const setHidden = (h: string[]) => { setPicked(h); saveHidden(store, h); };
  const on = (c: string) => !hidden.includes(c);
  const bar = <div className="d-flex justify-content-end mb-2"><ColumnSwitch columns={columns} hidden={hidden} onChange={setHidden} /></div>;
  return { hidden, on, bar };
}

/** An account line, indented by its level so the chart's shape is visible at a glance. */
const nameCell = (r: any, code = true, name = true) => (
  <td style={{ paddingLeft: 8 + (Number(r.level || 1) - 1) * 18 }} className={r.is_group ? "fw-semibold" : ""}>
    {code && <span className="text-muted fs-11 me-2">{r.code}</span>}{name && r.name}
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

const TB_GROUPS: [string, string, string][] = [["Opening", "opening_debit", "opening_credit"], ["In this period", "debit", "credit"], ["Closing", "closing_debit", "closing_credit"]];

export function TrialBalancePage() {
  const [from, setFrom] = useState(startOfMonth());
  const [to, setTo] = useState(today());
  const params = { date_from: from, date_to: to };
  const q = useStatement("trial_balance", params);
  const t = q.data?.totals || {};
  const cols = useColumns("trial_balance", [{ key: "name", label: labelCase("account") }, { key: "code", label: labelCase("code") },
    ...TB_GROUPS.flatMap(([g, d, c]) => [{ key: d, label: labelCase(`${g === "In this period" ? "period" : g} debit`) }, { key: c, label: labelCase(`${g === "In this period" ? "period" : g} credit`) }])]);
  const account = cols.on("name") || cols.on("code");
  const groups = TB_GROUPS.map(([g, d, c]) => ({ g, keys: [d, c].filter(cols.on) })).filter((x) => x.keys.length);
  const figure = (r: any, k: string, first: boolean) => <td key={k} className={`num ${first ? "border-start" : ""}`}>{money(r[k])}</td>;
  return (
    <>
      <PageHeader title="Trial balance" crumbs={[{ label: "Financial Reports" }, { label: "Trial balance" }]}
        actions={<Exports k="trial_balance" params={params} hide={cols.hidden} />} />
      <Range from={from} to={to} setFrom={setFrom} setTo={setTo} />
      {q.isLoading ? <Spinner /> : (
        <>
          {t.balanced === false && <Alert kind="danger">The ledger does not balance for this range — debits {money(t.closing_debit)} against credits {money(t.closing_credit)}. That is a posting fault worth looking at before anything is filed.</Alert>}
          {cols.bar}
          <div className="card"><div className="card-body p-0"><div className="table-responsive">
            <table className="table table-sm align-middle mb-0 fs-13">
              <thead className="table-light">
                <tr>
                  {account && <th rowSpan={2} style={{ minWidth: 240 }}>Account</th>}
                  {groups.map(({ g, keys }) => <th key={g} colSpan={keys.length} className="text-center border-start">{g}</th>)}
                </tr>
                <tr>{groups.flatMap(({ keys }) => keys.map((k, i) => <th key={k} className={`num ${i === 0 ? "border-start" : ""}`}>{k.endsWith("debit") ? "Debit" : "Credit"}</th>))}</tr>
              </thead>
              <tbody>
                {(q.data?.rows || []).map((r: any, i: number) => (
                  <tr key={i} className={rowClass(r)}>
                    {account && nameCell(r, cols.on("code"), cols.on("name"))}
                    {groups.flatMap(({ keys }) => keys.map((k, j) => figure(r, k, j === 0)))}
                  </tr>
                ))}
              </tbody>
              <tfoot><tr className="fw-bold" style={{ borderTop: "2px solid #202C4B" }}>
                {account && <td>Total</td>}
                {groups.flatMap(({ keys }) => keys.map((k, j) => <td key={k} className={`num ${j === 0 ? "border-start" : ""}`}>{k.startsWith("closing") ? money(t[k]) : ""}</td>))}
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
  const gross = Number(t.gross_profit || 0);
  // the rows come in reading order, totals in their place: heading, accounts, its total ... gross profit ... net profit
  const line: Record<string, string> = {
    heading: "fw-semibold table-light",
    subtotal: "fw-semibold",
    profit: "fw-bold table-light",
    net: "fw-bold fs-14 table-light",
  };
  const cols = useColumns("profit_and_loss", [{ key: "name", label: labelCase("account") }, { key: "code", label: labelCase("code") }, { key: "amount", label: labelCase("amount") }]);
  const account = cols.on("name") || cols.on("code");
  return (
    <>
      <PageHeader title="Income statement" crumbs={[{ label: "Financial Reports" }, { label: "Income statement" }]}
        actions={<Exports k="profit_and_loss" params={params} hide={cols.hidden} />} />
      <Range from={from} to={to} setFrom={setFrom} setTo={setTo} />
      {q.isLoading ? <Spinner /> : (
        <>
          <div className="row g-3 mb-3">
            <div className="col-6 col-xl-3"><StatCard label="Revenue" value={money(t.revenue)} icon="ti-trending-up" color="primary" /></div>
            <div className="col-6 col-xl-3"><StatCard label="Cost of sales" value={money(t.cost_of_sales)} icon="ti-package" color="warning" /></div>
            <div className="col-6 col-xl-3"><StatCard label={gross >= 0 ? "Gross profit" : "Gross loss"} value={money(Math.abs(gross))} icon="ti-chart-line" color={gross >= 0 ? "info" : "danger"} /></div>
            <div className="col-6 col-xl-3"><StatCard label={profit >= 0 ? "Net profit" : "Net loss"} value={money(Math.abs(profit))} icon={profit >= 0 ? "ti-coin" : "ti-alert-triangle"} color={profit >= 0 ? "success" : "danger"} /></div>
          </div>
          {cols.bar}
          <div className="card"><div className="card-body p-0"><div className="table-responsive">
            <table className="table table-sm align-middle mb-0 fs-13">
              <thead className="table-light"><tr>{account && <th style={{ minWidth: 280 }}>Account</th>}{cols.on("amount") && <th className="num" style={{ width: 200 }}>Amount</th>}</tr></thead>
              <tbody>{(q.data?.rows || []).map((r: any, i: number) => {
                const heavy = r.kind === "profit" || r.kind === "net";
                const colour = r.kind === "net" ? (profit >= 0 ? "text-success" : "text-danger") : r.kind === "profit" && gross < 0 ? "text-danger" : "";
                return (
                  <tr key={i} className={line[r.kind] || ""} style={heavy ? { borderTop: "2px solid #202C4B", borderBottom: "2px solid #202C4B" } : undefined}>
                    {account && (r.kind === "account" ? nameCell(r, cols.on("code"), cols.on("name")) : <td style={{ paddingLeft: 8 }}>{r.name}</td>)}
                    {cols.on("amount") && <td className={`num ${colour}`} style={r.kind === "subtotal" ? { borderTop: "1px solid #6B7280" } : undefined}>{r.kind === "heading" ? "" : money(r.amount)}</td>}
                  </tr>
                );
              })}</tbody>
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
  const cols = useColumns("balance_sheet", [{ key: "name", label: labelCase("account") }, { key: "code", label: labelCase("code") }, { key: "amount", label: labelCase("amount") }]);
  const account = cols.on("name") || cols.on("code");
  const block = (title: string, list: any[], total: any) => (
    <div className="card mb-3"><div className="card-header py-2"><strong className="fs-13">{title}</strong></div>
      <div className="card-body p-0"><div className="table-responsive"><table className="table table-sm align-middle mb-0 fs-13">
        <tbody>{list.map((r, i) => <tr key={i} className={rowClass(r)}>{account && nameCell(r, cols.on("code"), cols.on("name"))}{cols.on("amount") && <td className="num" style={{ width: 180 }}>{money(r.amount)}</td>}</tr>)}</tbody>
        <tfoot><tr className="fw-bold" style={{ borderTop: "2px solid #202C4B" }}>{account && <td className="text-end">Total {title.toLowerCase()}</td>}{cols.on("amount") && <td className="num">{money(total)}</td>}</tr></tfoot>
      </table></div></div>
    </div>
  );
  return (
    <>
      <PageHeader title="Balance sheet" crumbs={[{ label: "Financial Reports" }, { label: "Balance sheet" }]}
        actions={<Exports k="balance_sheet" params={params} hide={cols.hidden} />} />
      <div className="card mb-3"><div className="card-body py-3"><div className="row align-items-end">
        <Field label="As at" col="col-6 col-md-3"><DateInput className="form-control" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></Field>
        <div className="col-md-9 text-md-end">
          {t.balanced === false
            ? <span className="badge bg-danger-transparent fs-12"><i className="ti ti-alert-triangle me-1" />Assets do not equal liabilities and equity</span>
            : <span className="badge bg-success-transparent fs-12"><i className="ti ti-circle-check me-1" />Balanced at {money(t.assets)}</span>}
        </div>
      </div></div></div>
      {!q.isLoading && cols.bar}
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
  // the screen's columns, then the one only the files carry (the voucher type)
  const cols = useColumns("general_ledger", [{ key: "date", label: labelCase("date") }, { key: "journal", label: labelCase("journal") }, { key: "account", label: labelCase("account") },
    { key: "description", label: labelCase("narration") }, { key: "source", label: labelCase("source") }, { key: "debit", label: labelCase("debit") }, { key: "credit", label: labelCase("credit") },
    { key: "balance", label: labelCase("balance") }, { key: "type", label: labelCase("type"), fileOnly: true }]);
  const narration = cols.on("description") || cols.on("source");
  const shown = ["date", "journal", "account"].filter(cols.on).length + (narration ? 1 : 0) + ["debit", "credit"].filter(cols.on).length;
  return (
    <>
      <PageHeader title="General ledger" crumbs={[{ label: "Financial Reports" }, { label: "General ledger" }]}
        actions={<Exports k="general_ledger" params={params} hide={cols.hidden} />} />
      <Range from={from} to={to} setFrom={setFrom} setTo={setTo} extra={
        <Field label="Account" col="col-md-4">
          <EntitySelect endpoint="/api/v1/accounting/accounts" query={{ is_group: false }} value={account} onChange={setAccount}
            mapOption={(a: any) => ({ id: a.id, label: `${a.code} ${a.name}`, sub: a.type })} placeholder="All accounts" />
        </Field>} />
      {q.isLoading ? <Spinner /> : (
        <>
        {cols.bar}
        <div className="card"><div className="card-body p-0"><div className="table-responsive">
          <table className="table table-sm align-middle mb-0 fs-13">
            <thead className="table-light"><tr>
              {cols.on("date") && <th style={{ width: 110 }}>Date</th>}{cols.on("journal") && <th style={{ width: 120 }}>Journal</th>}{cols.on("account") && <th>Account</th>}{narration && <th>Narration</th>}
              {cols.on("debit") && <th className="num" style={{ width: 130 }}>Debit</th>}{cols.on("credit") && <th className="num" style={{ width: 130 }}>Credit</th>}{cols.on("balance") && <th className="num" style={{ width: 150 }}>Balance</th>}
            </tr></thead>
            <tbody>
              <tr className="table-light fw-semibold"><td colSpan={Math.max(shown, 1)}>Opening balance</td>{cols.on("balance") && <td className="num">{money(t.opening)}</td>}</tr>
              {(q.data?.rows || []).map((r: any, i: number) => (
                <tr key={i}>
                  {cols.on("date") && <td>{fmtDate(r.date)}</td>}
                  {cols.on("journal") && <td>{r.journal_id ? <Link to={`/app/accounting/journals/${r.journal_id}`}>{r.journal}</Link> : r.journal}</td>}
                  {cols.on("account") && <td className="fs-12">{r.account}</td>}
                  {narration && <td className="fs-12 text-muted">{cols.on("description") && r.description}{cols.on("source") && <div className="fs-11">{r.source}</div>}</td>}
                  {cols.on("debit") && <td className="num">{Number(r.debit) ? money(r.debit) : "—"}</td>}
                  {cols.on("credit") && <td className="num">{Number(r.credit) ? money(r.credit) : "—"}</td>}
                  {cols.on("balance") && <td className="num fw-medium">{money(r.balance)}</td>}
                </tr>
              ))}
              {!(q.data?.rows || []).length && <tr><td colSpan={shown + (cols.on("balance") ? 1 : 0) || 1} className="text-center text-muted py-4">Nothing posted in this range.</td></tr>}
            </tbody>
            <tfoot><tr className="fw-bold" style={{ borderTop: "2px solid #202C4B" }}><td colSpan={Math.max(shown, 1)} className="text-end">Closing balance</td>{cols.on("balance") && <td className="num">{money(t.closing)}</td>}</tr></tfoot>
          </table>
        </div></div></div>
        </>
      )}
    </>
  );
}
