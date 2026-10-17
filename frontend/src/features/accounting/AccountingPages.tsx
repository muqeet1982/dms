import { DateInput } from "@/components/ui/DateInput";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { date, datetime, money, titleCase, today } from "@/lib/format";
import { useAction, useDoc, useOptions } from "@/lib/hooks";
import { pageRights, usePage, usePerms } from "@/lib/perms";
import { toast } from "@/lib/toast";
import { Alert, Field, InfoItem, PageHeader, Spinner, StatCard, StatusBadge, Tabs } from "@/components/ui/Bits";
import { CrudPage } from "@/components/ui/CrudPage";
import { Column, DataTable } from "@/components/ui/DataTable";
import { EntitySelect } from "@/components/ui/EntitySelect";
import { ListPage } from "@/components/ui/ListPage";
import { ConfirmDialog, Modal } from "@/components/ui/Modal";
import { FieldDef, ResourceForm, ResourceModal } from "@/components/ui/ResourceForm";
import { DocTabs, JournalTab } from "@/components/documents/DocTabs";
import { LedgerTable } from "../partners/CustomerPages";

const acctOption = (a: any) => ({ id: a.id, label: `${a.code} ${a.name}`, sub: a.type });
const GROUP_LEVELS = ["", "class", "group", "sub-group"];
/** Parent rows name the level as well as the code: "1-01 Current Assets" and "1-01-001 Cash & Cash Equivalents" sit a level apart. */
const groupOption = (a: any) => ({ id: a.id, label: `${a.code} ${a.name}`, sub: `level ${a.level} ${GROUP_LEVELS[a.level] || "group"} · ${a.type}` });

export function AccountingDashboardPage() {
  const q = useQuery({ queryKey: ["acc-dash"], queryFn: () => api.get("/api/v1/accounting/dashboard") });
  if (q.isLoading) return <Spinner />;
  const d = q.data;
  if (!d) return null;
  return (
    <>
      <PageHeader title="Accounts" crumbs={[{ label: "Accounting" }]} actions={<><Link to="/app/accounting/payments/new?direction=in" className="btn btn-success"><i className="ti ti-cash me-1" />Receive</Link><Link to="/app/accounting/payments/new?direction=out" className="btn btn-outline-primary"><i className="ti ti-cash-off me-1" />Pay</Link><Link to="/app/accounting/expenses/new" className="btn btn-outline-secondary"><i className="ti ti-receipt me-1" />Expense</Link></>} />
      <div className="row g-3 mb-3">
        {d.cash.map((c: any) => <div className="col-6 col-md-3" key={c.name}><StatCard label={c.name} value={money(c.balance, 0)} icon="ti-wallet" color="success" /></div>)}
        {d.bank.map((b: any) => <div className="col-6 col-md-3" key={b.name}><StatCard label={b.name} value={money(b.balance, 0)} icon="ti-building-bank" color="info" /></div>)}
        <div className="col-6 col-md-3"><StatCard label="Cash with reps" value={money(d.cash_with_reps, 0)} icon="ti-user-dollar" color="warning" to="/app/sales/rep-collections" /></div>
      </div>
      <div className="row g-3 mb-3">
        <div className="col-md-6"><div className="card h-100"><div className="card-header"><strong>Receivables</strong></div><div className="card-body"><div className="row g-2 text-center">{[["Total", d.receivables.total], ["Due today", d.receivables.due_today], ["Due this week", d.receivables.due_week], ["Overdue", d.receivables.overdue]].map(([k, v]) => <div className="col-3" key={k as string}><div className="fs-11 text-muted">{k}</div><div className={`fw-bold ${k === "Overdue" && Number(v) > 0 ? "text-danger" : ""}`}>{money(v as any, 0)}</div></div>)}</div><div className="mt-3"><Link to="/app/reports/ar_ageing" className="fs-13">Ageing report →</Link></div></div></div></div>
        <div className="col-md-6"><div className="card h-100"><div className="card-header"><strong>Payables</strong></div><div className="card-body"><div className="row g-2 text-center">{[["Total", d.payables.total], ["Due today", d.payables.due_today], ["Due this week", d.payables.due_week], ["Overdue", d.payables.overdue]].map(([k, v]) => <div className="col-3" key={k as string}><div className="fs-11 text-muted">{k}</div><div className={`fw-bold ${k === "Overdue" && Number(v) > 0 ? "text-danger" : ""}`}>{money(v as any, 0)}</div></div>)}</div><div className="mt-3"><Link to="/app/reports/ap_ageing" className="fs-13">Payables ageing →</Link></div></div></div></div>
      </div>
      <div className="row g-3">
        <div className="col-6 col-md-3"><StatCard label="Pending cheques" value={d.pending_cheques} icon="ti-file-dollar" color="secondary" to="/app/accounting/cheques" /></div>
        <div className="col-6 col-md-3"><StatCard label="Expenses to approve" value={d.pending_expenses} icon="ti-receipt" color="warning" to="/app/accounting/expenses?status=submitted" /></div>
        <div className="col-6 col-md-3"><StatCard label="Draft journals" value={d.draft_journals} icon="ti-book" color="info" to="/app/accounting/journals?status=draft" /></div>
        <div className="col-6 col-md-3"><StatCard label="Unreconciled bank lines" value={d.unreconciled_bank_lines} icon="ti-building-bank" color="danger" to="/app/accounting/reconciliation" /></div>
      </div>
    </>
  );
}

export function AccountsPage() {
  const qc = useQueryClient();
  const { can } = usePerms();
  const q = useQuery({ queryKey: ["coa-tree"], queryFn: () => api.get("/api/v1/accounting/accounts/tree") });
  const [modal, setModal] = useState<{ open: boolean; row?: any; parent?: any }>({ open: false });
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const toggle = (id: string) => setOpen((o) => ({ ...o, [id]: !(o[id] ?? true) }));
  const render = (nodes: any[], depth = 0): JSX.Element[] => nodes.flatMap((a) => [
    <div className="tree-row" key={a.id} style={{ paddingLeft: 8 + depth * 22 }}>
      {a.children?.length ? <i className={`ti ${open[a.id] ?? true ? "ti-chevron-down" : "ti-chevron-right"} cursor-pointer`} onClick={() => toggle(a.id)} /> : <span style={{ width: 16, display: "inline-block" }} />}
      <span className="font-monospace fs-12 text-muted" style={{ minWidth: 110 }}>{a.code}</span>
      <span className={`flex-grow-1 ${a.is_group ? "fw-semibold" : ""}`}>{a.is_group ? a.name : <Link to={`/app/accounting/accounts/${a.id}`}>{a.name}</Link>}{a.is_system && <i className="ti ti-lock ms-1 fs-11 text-muted" title="system account" />}{!a.is_active && <span className="badge bg-secondary-light text-secondary ms-1 fs-10">inactive</span>}</span>
      <span className="fs-11 text-muted">{a.subtype}</span>
      {!a.is_group && a.balance !== null && <span className="num fs-12" style={{ minWidth: 120 }}>{money(a.balance)}</span>}
      {pageRights("accounts").edit && <span className="no-row-click">{a.is_group && a.level < 4 && <button className="btn btn-sm btn-link p-0 me-2" title="Add child" onClick={() => setModal({ open: true, parent: a })}><i className="ti ti-plus" /></button>}{a.level > 2 ? <button className="btn btn-sm btn-link p-0" title="Edit" onClick={() => setModal({ open: true, row: a })}><i className="ti ti-edit" /></button> : <i className="ti ti-lock text-muted fs-12" title="Standard chart of accounts — add an account beneath it instead" />}</span>}
    </div>,
    ...(open[a.id] ?? true ? render(a.children || [], depth + 1) : []),
  ]);
  // Levels 1-2 are the standard chart: the five classes and the groups under them. The balance sheet and P&L
  // print their names as headings (and keep them even at zero), and automatic postings find accounts by code,
  // so editing either up there rewrites the reports rather than one account. Levels 3-4 stay fully editable.
  const standard = Boolean(modal.row && modal.row.level <= 2);
  const fixedValue = (text: string) => <span className="form-control-plaintext fs-13">{text}</span>;
  const fields: FieldDef[] = [
    { name: "code", label: "Code", required: true, col: "col-md-4", readOnly: standard, icon: standard ? "ti-lock" : undefined, hint: standard ? "Fixed — reports and automatic postings find this account by its code." : "e.g. 1-01-006-0001" },
    { name: "name", label: "Name", required: true, col: "col-md-8", readOnly: standard, icon: standard ? "ti-lock" : undefined, hint: standard ? `Standard chart of accounts — a level ${modal.row?.level} name is a report heading and cannot be changed.` : undefined },
    standard
      ? { name: "parent_name", label: "Parent", type: "custom", col: "col-md-6", render: () => fixedValue(modal.row?.parent_name || "— none (top of the chart) —") }
      : { name: "parent", label: "Parent", type: "entity", endpoint: "/api/v1/accounting/accounts", query: { is_group: true }, mapOption: groupOption, col: "col-md-6", placeholder: "Search by code or name…" },
    standard
      ? { name: "type", label: "Type", type: "custom", col: "col-md-3", render: (v) => fixedValue(titleCase(v)) }
      : { name: "type", label: "Type", type: "select", options: ["asset", "liability", "equity", "income", "expense"].map((t) => ({ value: t, label: titleCase(t) })), required: true, col: "col-md-3" },
    { name: "subtype", label: "Subtype", col: "col-md-3", placeholder: "cash, bank, receivable…" },
    standard
      ? { name: "is_group", label: "Group (no posting)", type: "custom", col: "col-md-3", render: (v) => fixedValue(v ? "Yes" : "No") }
      : { name: "is_group", label: "Group (no posting)", type: "checkbox", col: "col-md-3" },
    { name: "allow_manual_posting", label: "Allow manual journals", type: "checkbox", default: true, col: "col-md-3" }, { name: "is_active", label: "Active", type: "checkbox", default: true, col: "col-md-3" },
    { name: "opening_balance", label: "Opening balance (debit +)", type: "money", col: "col-md-3", default: 0 }, { name: "description", label: "Description", col: "col-md-12" },
  ];
  return (
    <>
      <PageHeader title="Chart of accounts" crumbs={[{ label: "Accounting", to: "/app/accounting" }, { label: "Chart of accounts" }]} actions={<>{pageRights("accounts").edit && <button className="btn btn-primary" onClick={() => setModal({ open: true })}><i className="ti ti-plus me-1" />Add level-1 account</button>}<Link to="/app/reports/trial_balance" className="btn btn-outline-secondary">Trial balance</Link></>} />
      <Alert kind="info">4-level structure: <strong>1</strong> class → <strong>1-01</strong> group → <strong>1-01-001</strong> sub-group → <strong>1-01-001-0001</strong> posting account. Levels 1–3 are groups (no posting). System accounts (🔒) are used by automatic postings and cannot be deleted. The class and group levels (1–2) are the standard chart, so their code and name are fixed.</Alert>
      <div className="card"><div className="card-body p-2">{q.isLoading ? <Spinner /> : render(q.data || [])}</div></div>
      <ResourceModal open={modal.open} onClose={() => setModal({ open: false })} title={modal.row ? `Edit ${modal.row.code}` : modal.parent ? `New account under ${modal.parent.code} ${modal.parent.name}` : "New level-1 account"} endpoint="/api/v1/accounting/accounts" id={modal.row?.id} initial={modal.row || (modal.parent ? { parent: modal.parent.id, type: modal.parent.type, code: `${modal.parent.code}-`, is_group: modal.parent.level < 3 } : { is_group: true })} onSaved={() => qc.invalidateQueries({ queryKey: ["coa-tree"] })}
        fields={fields} />
    </>
  );
}

export function AccountLedgerPage() {
  const { id } = useParams();
  const [range, setRange] = useState({ date_from: "", date_to: "" });
  const acct = useDoc("/api/v1/accounting/accounts", id);
  const q = useQuery({ queryKey: ["acct-ledger", id, range], queryFn: () => api.get(`/api/v1/accounting/accounts/${id}/ledger`, range) });
  return (
    <>
      <PageHeader title={acct.data ? `${acct.data.code} ${acct.data.name}` : "Account ledger"} crumbs={[{ label: "Chart of accounts", to: "/app/accounting/accounts" }, { label: "Ledger" }]} actions={<div className="d-flex gap-2"><DateInput className="form-control" value={range.date_from} onChange={(e) => setRange({ ...range, date_from: e.target.value })} /><DateInput className="form-control" value={range.date_to} onChange={(e) => setRange({ ...range, date_to: e.target.value })} /></div>} />
      <div className="card"><div className="card-body p-0">{q.isLoading ? <Spinner /> : q.data && <LedgerTable data={{ opening_balance: q.data.totals.opening, closing_balance: q.data.totals.closing, rows: q.data.rows.map((r: any) => ({ ...r, account: r.description ? r.account : r.account })) }} />}</div></div>
    </>
  );
}

export function JournalsPage() {
  const { can } = usePerms();
  const cols: Column<any>[] = [{ key: "number", header: "Journal #", sortKey: "number", render: (j) => <span className="fw-semibold text-dark">{j.number}</span> }, { key: "date", header: "Date", sortKey: "date", render: (j) => date(j.date) }, { key: "type", header: "Type", render: (j) => titleCase(j.type) }, { key: "memo", header: "Memo", render: (j) => <>{j.memo}<div className="text-muted fs-11">{j.source_type} {j.source_number}</div></> }, { key: "total_debit", header: "Debit", num: true, render: (j) => money(j.total_debit) }, { key: "total_credit", header: "Credit", num: true, render: (j) => money(j.total_credit) }, { key: "status", header: "Status", render: (j) => <StatusBadge status={j.status} /> }];
  return <ListPage page="journals" title="Journal entries" crumbs={[{ label: "Accounting", to: "/app/accounting" }, { label: "Journals" }]} endpoint="/api/v1/accounting/journals" columns={cols} rowLink={(j) => `/app/accounting/journals/${j.id}`} createTo="/app/accounting/journals/new" createLabel="Manual journal" defaultOrdering="-date" searchPlaceholder="Search number, memo, source…" filters={[{ name: "status", label: "Status", type: "select", options: ["draft", "posted", "reversed"].map((s) => ({ value: s, label: s })) }, { name: "type", label: "Type", type: "select", options: ["manual", "auto", "opening", "closing"].map((s) => ({ value: s, label: s })) }, { name: "account", label: "Account", type: "entity", endpoint: "/api/v1/accounting/accounts", query: { is_group: false }, mapOption: acctOption }, { name: "date_from", label: "From", type: "date" }, { name: "date_to", label: "To", type: "date" }]} />;
}

export function JournalDetailPage() {
  const { id } = useParams();
  const { can } = usePerms();
  const q = useDoc("/api/v1/accounting/journals", id);
  const { run, busy } = useAction("/api/v1/accounting/journals", id);
  const [rev, setRev] = useState(false);
  if (q.isLoading) return <Spinner />;
  const j = q.data;
  if (!j) return null;
  const src: Record<string, string> = { SalesInvoice: "/app/sales/invoices/", SalesOrder: "/app/sales/orders/", PurchaseBill: "/app/purchasing/bills/", GoodsReceipt: "/app/purchasing/grns/", Payment: "/app/accounting/payments/", StockAdjustment: "/app/inventory/adjustments/", Expense: "/app/accounting/expenses/", CreditNote: "/app/sales/credit-notes/", StockTransfer: "/app/inventory/transfers/", DeliveryTrip: "/app/delivery/trips/", PurchaseReturn: "/app/purchasing/returns/" };
  return (
    <>
      <PageHeader title={j.number} badge={<StatusBadge status={j.status} />} crumbs={[{ label: "Journals", to: "/app/accounting/journals" }, { label: j.number }]}
        actions={<>{j.status === "draft" && pageRights("journals").add && <Link to={`/app/accounting/journals/${j.id}/edit`} className="btn btn-outline-primary"><i className="ti ti-edit me-1" />Edit</Link>}{j.status === "draft" && pageRights("journals").extras.post && <button className="btn btn-success" disabled={!!busy} onClick={() => run("post")}><i className="ti ti-check me-1" />Post</button>}{j.status === "posted" && pageRights("journals").extras.reverse && <button className="btn btn-outline-danger" onClick={() => setRev(true)}>Reverse</button>}<button className="btn btn-outline-secondary" onClick={() => api.download(`/api/v1/accounting/journals/${j.id}/print`)}><i className="ti ti-printer me-1" />Voucher</button></>} />
      <div className="card mb-3"><div className="card-body">
        <div className="row g-3 mb-3"><InfoItem label="Date / type">{date(j.date)} · {titleCase(j.type)}</InfoItem><InfoItem label="Memo">{j.memo}</InfoItem><InfoItem label="Source">{j.source_type && src[j.source_type] ? <Link to={`${src[j.source_type]}${j.source_id}`}>{j.source_type} {j.source_number}</Link> : j.source_type ? `${j.source_type} ${j.source_number}` : "manual"}</InfoItem><InfoItem label="Posted">{j.posted_by_name} {datetime(j.posted_at)}{j.reversed_by_number && <div className="fs-12 text-danger">reversed by {j.reversed_by_number}</div>}</InfoItem></div>
        <table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>Account</th><th>Description</th><th>Partner</th><th>Cost centre</th><th className="num">Debit</th><th className="num">Credit</th></tr></thead><tbody>{j.lines.map((l: any) => <tr key={l.id}><td><Link to={`/app/accounting/accounts/${l.account}`}>{l.account_code} {l.account_name}</Link></td><td>{l.description}</td><td>{l.partner_name || (l.partner_type ? `${l.partner_type}` : "")}</td><td>{l.cost_centre_name}</td><td className="num">{Number(l.debit) ? money(l.debit) : ""}</td><td className="num">{Number(l.credit) ? money(l.credit) : ""}</td></tr>)}</tbody><tfoot><tr className="fw-bold"><td colSpan={4} className="text-end">Total</td><td className="num">{money(j.total_debit)}</td><td className="num">{money(j.total_credit)}</td></tr></tfoot></table>
      </div></div>
      <DocTabs model="JournalEntry" id={j.id} showJournal={false} />
      <ConfirmDialog open={rev} title="Reverse journal" message="A mirror entry will be posted today and this entry marked reversed." withReason confirmText="Reverse" onClose={() => setRev(false)} onConfirm={async (r) => { await run("reverse", { memo: r || undefined }); setRev(false); }} />
    </>
  );
}

export function JournalFormPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const { can } = usePerms();
  const existing = useDoc("/api/v1/accounting/journals", id);
  const [form, setForm] = useState<any>(null);
  const [lines, setLines] = useState<any[]>([]);
  if (id && existing.isLoading) return <Spinner />;
  if (!form) {
    const j = existing.data;
    setForm(j ? { date: j.date, memo: j.memo, notes: j.notes, version: j.version } : { date: new Date().toISOString().slice(0, 10), memo: "", notes: "" });
    setLines(j ? j.lines.map((l: any) => ({ key: l.id, account: l.account, account_label: `${l.account_code} ${l.account_name}`, debit: l.debit, credit: l.credit, description: l.description, partner_type: l.partner_type, partner_id: l.partner_id })) : [{ key: "a", account: null, debit: "", credit: "", description: "" }, { key: "b", account: null, debit: "", credit: "", description: "" }]);
    return <Spinner />;
  }
  const td = lines.reduce((s, l) => s + Number(l.debit || 0), 0);
  const tc = lines.reduce((s, l) => s + Number(l.credit || 0), 0);
  const upd = (k: string, patch: any) => setLines(lines.map((l) => (l.key === k ? { ...l, ...patch } : l)));
  const submit = async (post: boolean) => {
    const valid = lines.filter((l) => l.account && (Number(l.debit) || Number(l.credit)));
    if (valid.length < 2) return toast.error("At least two lines with amounts are required");
    if (Math.abs(td - tc) > 0.005) return toast.error(`Journal does not balance (debit ${money(td)} ≠ credit ${money(tc)})`);
    try {
      const body = { ...form, post, lines: valid.map((l, i) => ({ account: l.account, debit: l.debit || 0, credit: l.credit || 0, description: l.description || "", partner_type: l.partner_type || "", partner_id: l.partner_id || "", sequence: i })) };
      const r = id ? await api.patch(`/api/v1/accounting/journals/${id}`, body) : await api.post("/api/v1/accounting/journals", body, true);
      toast.success("Saved");
      nav(`/app/accounting/journals/${r.id}`);
    } catch (e: any) { toast.error(e.message); }
  };
  return (
    <>
      <PageHeader title={id ? `Edit ${existing.data?.number}` : "Manual journal"} crumbs={[{ label: "Journals", to: "/app/accounting/journals" }, { label: id ? "Edit" : "New" }]} />
      <div className="card mb-3"><div className="card-body">
        <div className="row"><Field label="Date" col="col-md-3"><DateInput className="form-control" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} /></Field><Field label="Memo *" col="col-md-9"><input className="form-control" value={form.memo} onChange={(e) => setForm({ ...form, memo: e.target.value })} /></Field></div>
        <table className="table table-sm align-middle"><thead className="table-light"><tr><th style={{ minWidth: 280 }}>Account</th><th>Description</th><th style={{ width: 140 }} className="num">Debit</th><th style={{ width: 140 }} className="num">Credit</th><th style={{ width: 40 }} /></tr></thead>
          <tbody>{lines.map((l) => <tr key={l.key}><td><EntitySelect endpoint="/api/v1/accounting/accounts" query={{ is_group: false, is_active: true }} value={l.account} onChange={(v) => upd(l.key, { account: v })} mapOption={acctOption} size="sm" allowClear={false} /></td><td><input className="form-control form-control-sm" value={l.description} onChange={(e) => upd(l.key, { description: e.target.value })} /></td><td><input type="number" step="0.01" className="form-control form-control-sm text-end" value={l.debit} onChange={(e) => upd(l.key, { debit: e.target.value, credit: e.target.value ? "" : l.credit })} /></td><td><input type="number" step="0.01" className="form-control form-control-sm text-end" value={l.credit} onChange={(e) => upd(l.key, { credit: e.target.value, debit: e.target.value ? "" : l.debit })} /></td><td><button className="btn btn-sm btn-outline-danger" onClick={() => setLines(lines.filter((x) => x.key !== l.key))}><i className="ti ti-x" /></button></td></tr>)}</tbody>
          <tfoot><tr><td colSpan={2} className="text-end fw-semibold">Totals</td><td className="num fw-bold">{money(td)}</td><td className="num fw-bold">{money(tc)}</td><td /></tr>{Math.abs(td - tc) > 0.005 && <tr><td colSpan={5} className="text-danger fs-12">Difference: {money(td - tc)}</td></tr>}</tfoot></table>
        <button className="btn btn-outline-primary btn-sm" onClick={() => setLines([...lines, { key: crypto.randomUUID(), account: null, debit: "", credit: "", description: "" }])}><i className="ti ti-plus me-1" />Add line</button>
      </div></div>
      <div className="d-flex gap-2 justify-content-end"><button className="btn btn-light" onClick={() => nav(-1)}>Cancel</button><button className="btn btn-outline-primary" onClick={() => submit(false)}>Save draft</button>{pageRights("journals").extras.post && <button className="btn btn-primary" onClick={() => submit(true)}>Save & post</button>}</div>
    </>
  );
}

export function PaymentsPage() {
  const { can } = usePerms();
  const cols: Column<any>[] = [{ key: "number", header: "#", sortKey: "number", render: (p) => <span className="fw-semibold text-dark">{p.number}</span> }, { key: "date", header: "Date", sortKey: "date", render: (p) => date(p.date) }, { key: "direction", header: "Kind", render: (p) => <span className={`badge ${p.direction === "in" ? "bg-success-light text-success" : "bg-danger-light text-danger"}`}>{p.direction === "in" ? "Receipt" : "Payment"}</span> }, { key: "partner_name", header: "Partner", render: (p) => <>{p.partner_name}<div className="text-muted fs-11">{p.partner_type}</div></> }, { key: "method", header: "Method", render: (p) => <>{titleCase(p.method)}{p.cheque_no && <div className="text-muted fs-11">{p.cheque_no} · {p.cheque_status}</div>}</> }, { key: "allocations", header: "Allocated to", render: (p) => p.allocations.map((a: any) => a.document_number).join(", ") || (Number(p.unallocated) > 0 ? <span className="text-warning">advance</span> : "—") }, { key: "collected_by_name", header: "By" }, { key: "amount", header: "Amount", num: true, sortKey: "amount", render: (p) => <span className="fw-semibold">{money(p.amount)}</span> }, { key: "status", header: "Status", render: (p) => <StatusBadge status={p.status} /> }];
  return <ListPage title="Transactions" crumbs={[{ label: "Accounting", to: "/app/accounting" }, { label: "Transactions" }]} endpoint="/api/v1/accounting/payments" columns={cols} rowLink={(p) => `/app/accounting/payments/${p.id}`} defaultOrdering="-date" searchPlaceholder="Search number, partner, reference, cheque…"
    actions={<>{pageRights("payments").extras.receive && <Link to="/app/accounting/payments/new?direction=in" className="btn btn-success"><i className="ti ti-cash me-1" />Receive</Link>}{pageRights("payments").extras.pay && <Link to="/app/accounting/payments/new?direction=out" className="btn btn-primary"><i className="ti ti-cash-off me-1" />Pay</Link>}</>}
    filters={[{ name: "direction", label: "Kind", type: "select", options: [{ value: "in", label: "Receipts" }, { value: "out", label: "Payments" }] }, { name: "method", label: "Method", type: "select", options: ["cash", "bank_transfer", "cheque", "online", "adjustment"].map((m) => ({ value: m, label: titleCase(m) })) }, { name: "partner_type", label: "Partner type", type: "select", options: ["customer", "supplier", "rep", "other"].map((m) => ({ value: m, label: titleCase(m) })) }, { name: "date_from", label: "From", type: "date" }, { name: "date_to", label: "To", type: "date" }]} />;
}

/**
 * Which cheque this payment is written on.
 *
 * Money going **out** leaves on one of our own cheques, so the field offers the book: the next unused number is
 * already filled in and the rest of the book is one click away — a cheque gets written out of order often
 * enough that forcing the next one would be wrong. Money coming **in** arrives on the customer's cheque, which
 * is their paper and their numbering, so it stays a plain box to copy the number into.
 */
function ChequeNumberField({ form, setForm }: { form: any; setForm: (f: any) => void }) {
  const ours = form.direction === "out" && Boolean(form.bank_account);
  const free = useQuery({
    queryKey: ["cheque-leaves", form.bank_account],
    enabled: ours,
    queryFn: () => api.get("/api/v1/accounting/cheques-register", { available: 1, bank_account: form.bank_account }),
  });
  const leaves: any[] = free.data || [];
  // the first unused one, the moment the book is known — nobody should have to go and look it up
  useEffect(() => {
    if (ours && leaves.length && !leaves.some((l) => l.number === form.cheque_no)) {
      setForm((f: any) => ({ ...f, cheque_no: leaves[0].number }));
    }
  }, [ours, leaves.length]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!ours) {
    return (
      <Field label="Cheque no" col="col-md-2" hint={form.direction === "in" ? "Their cheque number" : undefined}>
        <input className="form-control" value={form.cheque_no} onChange={(e) => setForm({ ...form, cheque_no: e.target.value })} />
      </Field>
    );
  }
  if (free.isLoading) return <Field label="Cheque no" col="col-md-3"><input className="form-control" value="" readOnly placeholder="reading the book…" /></Field>;
  if (!leaves.length) {
    return (
      <Field label="Cheque no" col="col-md-3" hint={<>No cheque book with leaves left on this account — <Link to="/app/accounting/cheque-books">add one</Link></>}>
        <input className="form-control" value={form.cheque_no} onChange={(e) => setForm({ ...form, cheque_no: e.target.value })} placeholder="type the number" />
      </Field>
    );
  }
  return (
    <Field label="Cheque no" col="col-md-3" hint={`${leaves.length} unused in the book`}>
      <select className="form-select" value={form.cheque_no} onChange={(e) => setForm({ ...form, cheque_no: e.target.value })}>
        {leaves.map((l: any, i: number) => <option key={l.id} value={l.number}>{l.number}{i === 0 ? " — next in the book" : ""}</option>)}
      </select>
    </Field>
  );
}

export function PaymentFormPage() {
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const banks = useOptions("/api/v1/accounting/bank-accounts");
  const cashes = useOptions("/api/v1/accounting/cash-accounts");
  const [form, setForm] = useState<any>({ direction: sp.get("direction") || "in", partner_type: sp.get("partner_type") || "customer", partner_id: sp.get("partner_id") || null, amount: sp.get("amount") ? String(Math.max(Number(sp.get("amount")), 0)) : "", method: "cash", date: new Date().toISOString().slice(0, 10), bank_account: "", cash_account: "", cheque_no: "", cheque_date: "", cheque_bank: "", reference: "", notes: "", withholding_amount: "" });
  const [alloc, setAlloc] = useState<Record<string, string>>({});
  const [deduct, setDeduct] = useState<Record<string, string>>({});
  const openDocs = useQuery({ queryKey: ["open-docs", form.partner_type, form.partner_id], queryFn: () => api.get("/api/v1/accounting/payments/open-documents", { partner_type: form.partner_type, partner_id: form.partner_id }), enabled: Boolean(form.partner_id) && ["customer", "supplier"].includes(form.partner_type) });
  // paying a manufacturer can deduct their open discount-share claims (not on cheques — the bounce path can't restore them)
  const claimsQ = useQuery({ queryKey: ["open-claims", form.partner_id], queryFn: () => api.get("/api/v1/company-claims", { supplier: form.partner_id, page_size: 200 }), enabled: Boolean(form.partner_id) && form.direction === "out" && form.partner_type === "supplier" });
  const openClaims = ((claimsQ.data?.results || claimsQ.data || []) as any[]).filter((c: any) => ["posted", "partially_paid"].includes(c.status));
  const deductTotal = form.method === "cheque" ? 0 : Object.values(deduct).reduce((s, v) => s + Number(v || 0), 0);
  const preselect = sp.get("invoice") || sp.get("bill");
  const allocated = Object.values(alloc).reduce((s, v) => s + Number(v || 0), 0);
  const budget = Number(form.amount || 0) + deductTotal;
  const endpoint = form.partner_type === "customer" ? "/api/v1/customers" : form.partner_type === "supplier" ? "/api/v1/suppliers" : "/api/v1/supplier-reps";
  return (
    <>
      <PageHeader title={form.direction === "in" ? "Receive payment" : "Make payment"} crumbs={[{ label: "Payments", to: "/app/accounting/payments" }, { label: "New" }]} />
      <div className="card mb-3"><div className="card-body"><div className="row">
        <Field label="Kind" col="col-md-2"><select className="form-select" value={form.direction} onChange={(e) => setForm({ ...form, direction: e.target.value, partner_type: e.target.value === "in" ? "customer" : "supplier", partner_id: null })}><option value="in">Receipt (money in)</option><option value="out">Payment (money out)</option></select></Field>
        <Field label="Partner type" col="col-md-2"><select className="form-select" value={form.partner_type} onChange={(e) => { setForm({ ...form, partner_type: e.target.value, partner_id: null }); setAlloc({}); setDeduct({}); }}><option value="customer">Customer</option><option value="supplier">Supplier</option><option value="other">Other</option></select></Field>
        {form.partner_type !== "other" && <Field label="Partner *" col="col-md-4"><EntitySelect endpoint={endpoint} value={form.partner_id} onChange={(v) => { setForm({ ...form, partner_id: v }); setAlloc({}); setDeduct({}); }} mapOption={(p) => ({ id: p.id, label: p.name, sub: `${p.code || ""} outstanding ${money(p.outstanding)}` })} /></Field>}
        <Field label="Amount *" col="col-md-2"><input type="number" step="0.01" className="form-control" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} /></Field><Field label="Date" col="col-md-2"><DateInput className="form-control" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} /></Field>
        <Field label="Method" col="col-md-2"><select className="form-select" value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value })}><option value="cash">Cash</option><option value="bank_transfer">Bank transfer</option><option value="cheque">Cheque</option><option value="online">Online (JazzCash/Easypaisa/card)</option></select></Field>
        {form.method === "cash" ? <Field label="Cash account" col="col-md-3"><select className="form-select" value={form.cash_account} onChange={(e) => setForm({ ...form, cash_account: e.target.value })}><option value="">Cash in hand</option>{cashes.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field> : form.method !== "cheque" && <Field label="Bank account *" col="col-md-3"><select className="form-select" value={form.bank_account} onChange={(e) => setForm({ ...form, bank_account: e.target.value })}><option value="">Select…</option>{banks.data?.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select></Field>}
        {form.method === "cheque" && <><ChequeNumberField form={form} setForm={setForm} /><Field label="Cheque date" col="col-md-2"><DateInput className="form-control" value={form.cheque_date} onChange={(e) => setForm({ ...form, cheque_date: e.target.value })} /></Field><Field label="Bank" col="col-md-2"><input className="form-control" value={form.cheque_bank} onChange={(e) => setForm({ ...form, cheque_bank: e.target.value })} /></Field></>}
        <Field label="Reference" col="col-md-3"><input className="form-control" value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} /></Field><Field label="Withholding tax amount" col="col-md-2"><input type="number" step="0.01" className="form-control" value={form.withholding_amount} onChange={(e) => setForm({ ...form, withholding_amount: e.target.value })} /></Field><Field label="Notes" col="col-md-7"><input className="form-control" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field></div>
        {form.direction === "out" && form.partner_type === "supplier" && form.method !== "cheque" && openClaims.length > 0 && <>
          <h6 className="fs-13 d-flex justify-content-between mt-2">Deduct company discount claims <span className="fw-normal text-muted">deducting {money(deductTotal)} — bills below can be settled up to cash + deductions · <a className="cursor-pointer" onClick={() => { const a: Record<string, string> = {}; for (const c of openClaims) a[c.id] = Number(c.balance).toFixed(2); setDeduct(a); }}>deduct all</a></span></h6>
          <table className="table table-sm fs-12"><thead className="table-light"><tr><th>Claim</th><th>Invoice</th><th>Date</th><th className="num">Outstanding</th><th style={{ width: 150 }}>Deduct</th></tr></thead>
            <tbody>{openClaims.map((c: any) => <tr key={c.id}><td>{c.number}</td><td>{c.invoice_number}<div className="text-muted fs-11">{c.customer_name}</div></td><td>{date(c.date)}</td><td className="num">{money(c.balance)}</td><td><input type="number" step="0.01" min="0" className="form-control form-control-sm text-end" value={deduct[c.id] ?? ""} onChange={(e) => setDeduct({ ...deduct, [c.id]: e.target.value })} /></td></tr>)}</tbody></table>
        </>}
        {openDocs.data && <><h6 className="fs-13 d-flex justify-content-between">Allocate to open {form.partner_type === "customer" ? "invoices" : "bills"} <span className="fw-normal text-muted">allocated {money(allocated)} of {money(budget)}{budget - allocated > 0.005 && <> · <span className="text-warning">unallocated {money(budget - allocated)} will be an advance</span></>} · <a className="cursor-pointer" onClick={() => { let rem = budget; const a: Record<string, string> = {}; for (const d of openDocs.data) { const take = Math.min(Number(d.balance), rem); if (take > 0) a[d.document_id] = take.toFixed(2); rem -= take; if (rem <= 0) break; } setAlloc(a); }}>auto (FIFO)</a></span></h6>
          <table className="table table-sm fs-12"><thead className="table-light"><tr><th>Document</th><th>Date</th><th>Due</th><th className="num">Total</th><th className="num">Balance</th><th style={{ width: 150 }}>Allocate</th></tr></thead><tbody>{openDocs.data.map((d: any) => <tr key={d.document_id} className={preselect === d.document_id && !alloc[d.document_id] && Object.keys(alloc).length === 0 ? "table-info" : ""}><td>{d.number}</td><td>{date(d.date)}</td><td>{date(d.due_date)}</td><td className="num">{money(d.total)}</td><td className="num">{money(d.balance)}</td><td><input type="number" step="0.01" className="form-control form-control-sm text-end" value={alloc[d.document_id] ?? (preselect === d.document_id && Object.keys(alloc).length === 0 ? Math.min(Number(d.balance), Number(form.amount || d.balance)).toFixed(2) : "")} onChange={(e) => setAlloc({ ...alloc, [d.document_id]: e.target.value })} /></td></tr>)}{!openDocs.data.length && <tr><td colSpan={6} className="text-muted text-center py-2">No open documents — the amount will be recorded as an advance.</td></tr>}</tbody></table></>}
      </div></div>
      <div className="d-flex gap-2 justify-content-end"><button className="btn btn-light" onClick={() => nav(-1)}>Cancel</button><button className="btn btn-primary" onClick={async () => { if (!form.amount || (form.partner_type !== "other" && !form.partner_id)) return toast.error("Partner and amount are required"); const allocations = Object.entries(alloc).filter(([, v]) => Number(v) > 0).map(([document_id, amount]) => ({ document_type: form.partner_type === "customer" ? "sales_invoice" : "purchase_bill", document_id, amount })); if (!allocations.length && preselect && openDocs.data?.some((d: any) => d.document_id === preselect)) { const d = openDocs.data.find((x: any) => x.document_id === preselect); allocations.push({ document_type: form.partner_type === "customer" ? "sales_invoice" : "purchase_bill", document_id: preselect, amount: Math.min(Number(d.balance), Number(form.amount)).toFixed(2) }); } const claim_deductions = form.method === "cheque" ? [] : Object.entries(deduct).filter(([, v]) => Number(v) > 0).map(([claim_id, amount]) => ({ claim_id, amount })); try { const r = await api.post("/api/v1/accounting/payments", { ...form, bank_account: form.bank_account || null, cash_account: form.cash_account || null, cheque_date: form.cheque_date || null, withholding_amount: form.withholding_amount || 0, allocations, claim_deductions }, true); toast.success(`${r.number} recorded`); nav(`/app/accounting/payments/${r.id}`); } catch (e: any) { toast.error(e.message); } }}>Record {form.direction === "in" ? "receipt" : "payment"}</button></div>
    </>
  );
}

export function PaymentDetailPage() {
  const { id } = useParams();
  const { can } = usePerms();
  const q = useDoc("/api/v1/accounting/payments", id);
  const { run, busy } = useAction("/api/v1/accounting/payments", id, [["list", "/api/v1/accounting/payments"]]);
  const banks = useOptions("/api/v1/accounting/bank-accounts");
  const [cancel, setCancel] = useState(false);
  const [bank, setBank] = useState("");
  if (q.isLoading) return <Spinner />;
  const p = q.data;
  if (!p) return null;
  const docLink = (a: any) => (a.document_type === "sales_invoice" ? `/app/sales/invoices/${a.document_id}` : `/app/purchasing/bills/${a.document_id}`);
  return (
    <>
      <PageHeader title={p.number} badge={<StatusBadge status={p.status} />} crumbs={[{ label: "Payments", to: "/app/accounting/payments" }, { label: p.number }]}
        actions={<>{p.method === "cheque" && p.status === "posted" && pageRights("payments").edit && <>{p.cheque_status === "pending" && p.direction === "in" && <div className="input-group" style={{ width: 320 }}><select className="form-select" value={bank} onChange={(e) => setBank(e.target.value)}><option value="">Deposit to bank…</option>{banks.data?.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select><button className="btn btn-outline-primary" disabled={!bank || !!busy} onClick={() => run("cheque/deposited", { bank_account: bank })}>Deposited</button></div>}{["pending", "deposited"].includes(p.cheque_status) && <button className="btn btn-success" disabled={!!busy} onClick={() => run("cheque/cleared", { bank_account: bank || p.bank_account })}>Cleared</button>}{["pending", "deposited"].includes(p.cheque_status) && <button className="btn btn-outline-danger" disabled={!!busy} onClick={() => run("cheque/bounced", {})}>Bounced</button>}</>}{p.status === "posted" && pageRights("payments").edit && <button className="btn btn-outline-danger" onClick={() => setCancel(true)}>Cancel</button>}<button className="btn btn-outline-secondary" onClick={() => api.download(`/api/v1/accounting/payments/${p.id}/print`)}><i className="ti ti-printer me-1" />Voucher</button></>} />
      <div className="row g-3 mb-3"><div className="col-6 col-md-3"><StatCard label={p.direction === "in" ? "Received" : "Paid"} value={money(p.amount)} icon="ti-cash" color={p.direction === "in" ? "success" : "danger"} sub={`${titleCase(p.method)} · ${date(p.date)}`} /></div><div className="col-6 col-md-3"><StatCard label="Partner" value={p.partner_name} icon="ti-user" color="primary" sub={p.partner_type} /></div><div className="col-6 col-md-3"><StatCard label="Unallocated" value={money(p.unallocated)} icon="ti-arrows-split" color={Number(p.unallocated) > 0 ? "warning" : "secondary"} /></div><div className="col-6 col-md-3"><StatCard label="Account" value={p.bank_account_name || p.cash_account_name || "—"} icon="ti-building-bank" color="info" sub={p.cheque_no ? `Cheque ${p.cheque_no} · ${p.cheque_status}` : p.reference} /></div></div>
      <div className="card mb-3"><div className="card-body"><div className="row g-3 mb-3"><InfoItem label="Collected / paid by">{p.collected_by_name}</InfoItem><InfoItem label="Reference">{p.reference || "—"}</InfoItem><InfoItem label="Withholding">{Number(p.withholding_amount) ? money(p.withholding_amount) : "—"}</InfoItem><InfoItem label="Journal">{p.journal_number ? <Link to={`/app/accounting/journals/${p.journal}`}>{p.journal_number}</Link> : "—"}</InfoItem></div>
        <h6 className="fs-13">Allocations</h6>{p.allocations.length ? <table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>Document</th><th className="num">Amount</th></tr></thead><tbody>{p.allocations.map((a: any) => <tr key={a.id}><td><Link to={docLink(a)}>{a.document_number}</Link></td><td className="num">{money(a.amount)}</td></tr>)}</tbody></table> : <p className="text-muted fs-13">Unallocated (advance).</p>}{p.notes && <p className="fs-13 text-muted mt-2 mb-0">{p.notes}</p>}</div></div>
      <DocTabs model="Payment" id={p.id} />
      <ConfirmDialog open={cancel} title="Cancel payment" message="Allocations are undone and the journal reversed." withReason danger onClose={() => setCancel(false)} onConfirm={async (r) => { await run("cancel", { reason: r }); setCancel(false); }} />
    </>
  );
}

export function ChequesPage() {
  const [status, setStatus] = useState("");
  const q = useQuery({ queryKey: ["cheques", status], queryFn: () => api.get("/api/v1/accounting/payments/cheques", { cheque_status: status || undefined }) });
  const cols: Column<any>[] = [{ key: "number", header: "#", render: (p) => <Link to={`/app/accounting/payments/${p.id}`}>{p.number}</Link> }, { key: "direction", header: "Kind", render: (p) => (p.direction === "in" ? "Received" : "Issued") }, { key: "partner_name", header: "Partner" }, { key: "cheque_no", header: "Cheque" }, { key: "cheque_bank", header: "Bank" }, { key: "cheque_date", header: "Cheque date", render: (p) => date(p.cheque_date) }, { key: "amount", header: "Amount", num: true, render: (p) => money(p.amount) }, { key: "cheque_status", header: "Status", render: (p) => <StatusBadge status={p.cheque_status} /> }];
  return (
    <>
      <PageHeader title="Cheque register" crumbs={[{ label: "Accounting", to: "/app/accounting" }, { label: "Cheques" }]} actions={<select className="form-select w-auto" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All</option>{["pending", "deposited", "cleared", "bounced"].map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}</select>} />
      <div className="card"><div className="card-body p-0"><DataTable columns={cols} rows={q.data} loading={q.isLoading} rowLink={(p) => `/app/accounting/payments/${p.id}`} /></div></div>
    </>
  );
}

export function ExpensesPage() {
  const { can } = usePerms();
  const cols: Column<any>[] = [{ key: "number", header: "#", render: (e) => <span className="fw-semibold text-dark">{e.number}</span> }, { key: "date", header: "Date", render: (e) => date(e.date) }, { key: "category_name", header: "Category", render: (e) => <>{e.category_name}<div className="text-muted fs-11">{e.category_code}</div></> }, { key: "vendor", header: "Vendor" }, { key: "cost_centre_name", header: "Cost centre" }, { key: "created_by_name", header: "By" }, { key: "total", header: "Total", num: true, render: (e) => money(e.total) }, { key: "status", header: "Status", render: (e) => <StatusBadge status={e.status} /> }];
  return <ListPage page="expenses" title="Expenses" crumbs={[{ label: "Accounting", to: "/app/accounting" }, { label: "Expenses" }]} endpoint="/api/v1/accounting/expenses" columns={cols} rowLink={(e) => `/app/accounting/expenses/${e.id}`} createTo="/app/accounting/expenses/new" createLabel="New expense" defaultOrdering="-date" filters={[{ name: "status", label: "Status", type: "select", options: ["draft", "submitted", "approved", "paid", "rejected"].map((s) => ({ value: s, label: s })) }, { name: "category", label: "Category", type: "entity", endpoint: "/api/v1/accounting/accounts", query: { type: "expense", is_group: false }, mapOption: acctOption }]} />;
}

const expenseFields: FieldDef[] = [
  { name: "date", label: "Date", type: "date", required: true, col: "col-md-3" }, { name: "category", label: "Expense account *", type: "entity", endpoint: "/api/v1/accounting/accounts", query: { type: "expense", is_group: false }, mapOption: acctOption, required: true, col: "col-md-5" }, { name: "amount", label: "Amount *", type: "money", required: true, col: "col-md-2" }, { name: "tax_amount", label: "Input tax", type: "money", col: "col-md-2", default: 0 },
  { name: "vendor", label: "Vendor / paid to", col: "col-md-4" }, { name: "cost_centre", label: "Cost centre", type: "entity", endpoint: "/api/v1/accounting/cost-centres", mapOption: (c) => ({ id: c.id, label: c.name }), col: "col-md-4" }, { name: "tax_type", label: "Tax type", type: "entity", endpoint: "/api/v1/settings/tax-types", mapOption: (t) => ({ id: t.id, label: t.name }), col: "col-md-4" },
  { name: "paid_from_cash", label: "Paid from cash account", type: "entity", endpoint: "/api/v1/accounting/cash-accounts", mapOption: (c) => ({ id: c.id, label: c.name }), col: "col-md-4", hint: "leave both blank to accrue (pay later)" }, { name: "paid_from_bank", label: "Paid from bank account", type: "entity", endpoint: "/api/v1/accounting/bank-accounts", mapOption: (b) => ({ id: b.id, label: b.name }), col: "col-md-4" }, { name: "is_recurring", label: "Recurring (e.g. rent)", type: "checkbox", col: "col-md-4" }, { name: "notes", label: "Notes", type: "textarea", col: "col-md-12" },
];

export function ExpenseFormPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const q = useDoc("/api/v1/accounting/expenses", id);
  if (id && q.isLoading) return <Spinner />;
  return (
    <>
      <PageHeader title={id ? `Edit ${q.data?.number}` : "New expense"} crumbs={[{ label: "Expenses", to: "/app/accounting/expenses" }, { label: id ? "Edit" : "New" }]} />
      <div className="card"><div className="card-body"><ResourceForm fields={expenseFields} initial={q.data || { date: new Date().toISOString().slice(0, 10) }} onCancel={() => nav(-1)} submitLabel="Save & submit" onSubmit={async (v: any) => { const r = id ? await api.patch(`/api/v1/accounting/expenses/${id}`, v) : await api.post("/api/v1/accounting/expenses", { ...v, submit: true }, true); toast.success("Saved"); nav(`/app/accounting/expenses/${r.id}`); }} /></div></div>
    </>
  );
}

export function ExpenseDetailPage() {
  const { id } = useParams();
  const { can } = usePerms();
  const q = useDoc("/api/v1/accounting/expenses", id);
  const { run, busy } = useAction("/api/v1/accounting/expenses", id, [["acc-dash"]]);
  const banks = useOptions("/api/v1/accounting/bank-accounts");
  const cashes = useOptions("/api/v1/accounting/cash-accounts");
  const [pay, setPay] = useState<{ open: boolean; bank: string; cash: string }>({ open: false, bank: "", cash: "" });
  const [reject, setReject] = useState(false);
  if (q.isLoading) return <Spinner />;
  const e = q.data;
  if (!e) return null;
  return (
    <>
      <PageHeader title={e.number} badge={<StatusBadge status={e.status} />} crumbs={[{ label: "Expenses", to: "/app/accounting/expenses" }, { label: e.number }]}
        actions={<>{["draft", "rejected"].includes(e.status) && <Link to={`/app/accounting/expenses/${e.id}/edit`} className="btn btn-outline-primary">Edit</Link>}{e.status === "draft" && <button className="btn btn-primary" disabled={!!busy} onClick={() => run("submit")}>Submit</button>}{e.status === "submitted" && pageRights("expenses").extras.approve && <><button className="btn btn-success" disabled={!!busy} onClick={() => run("approve")}>Approve</button><button className="btn btn-outline-danger" onClick={() => setReject(true)}>Reject</button></>}{e.status === "approved" && pageRights("payments").extras.pay && <button className="btn btn-primary" onClick={() => setPay({ ...pay, open: true })}>Pay now</button>}</>} />
      <div className="card mb-3"><div className="card-body"><div className="row g-3"><InfoItem label="Category">{e.category_code} {e.category_name}</InfoItem><InfoItem label="Amount">{money(e.amount)}{Number(e.tax_amount) > 0 && <div className="fs-12 text-muted">+ tax {money(e.tax_amount)} = {money(e.total)}</div>}</InfoItem><InfoItem label="Vendor / date">{e.vendor || "—"} · {date(e.date)}</InfoItem><InfoItem label="Paid from">{e.paid_from_bank || e.paid_from_cash ? (banks.data?.find((b) => b.id === e.paid_from_bank)?.name || cashes.data?.find((c) => c.id === e.paid_from_cash)?.name) : "accrued"}</InfoItem><InfoItem label="Cost centre">{e.cost_centre_name || "—"}</InfoItem><InfoItem label="By / approved">{e.created_by_name} · {e.approved_by_name || "—"}</InfoItem><InfoItem label="Journal">{e.journal_number ? <Link to={`/app/accounting/journals/${e.journal}`}>{e.journal_number}</Link> : "—"}</InfoItem><InfoItem label="Notes">{e.notes || "—"}</InfoItem></div></div></div>
      <DocTabs model="Expense" id={e.id} />
      <Modal open={pay.open} title="Pay expense" onClose={() => setPay({ ...pay, open: false })} size="sm" footer={<><button className="btn btn-light" onClick={() => setPay({ ...pay, open: false })}>Cancel</button><button className="btn btn-primary" disabled={!!busy} onClick={async () => { await run("pay", { bank_account: pay.bank || null, cash_account: pay.cash || null }); setPay({ ...pay, open: false }); }}>Pay</button></>}><Field label="Bank account"><select className="form-select" value={pay.bank} onChange={(e2) => setPay({ ...pay, bank: e2.target.value, cash: "" })}><option value="">—</option>{banks.data?.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select></Field><Field label="or cash account"><select className="form-select" value={pay.cash} onChange={(e2) => setPay({ ...pay, cash: e2.target.value, bank: "" })}><option value="">—</option>{cashes.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field></Modal>
      <ConfirmDialog open={reject} title="Reject expense" withReason danger onClose={() => setReject(false)} onConfirm={async (r) => { await run("reject", { reason: r }); setReject(false); }} />
    </>
  );
}

export function BankCashPage() {
  const [tab, setTab] = useState("bank");
  return (
    <>
      <PageHeader title="Cash & bank accounts" crumbs={[{ label: "Accounting", to: "/app/accounting" }, { label: "Cash & bank" }]} />
      <Tabs tabs={[{ key: "bank", label: "Bank accounts" }, { key: "cash", label: "Cash accounts" }, { key: "cc", label: "Cost centres" }]} active={tab} onChange={setTab} />
      {tab === "bank" && <CrudPage page="bank_cash" title="Bank accounts" crumbs={[]} endpoint="/api/v1/accounting/bank-accounts" columns={[{ key: "name", header: "Account", render: (b: any) => <Link to={`/app/accounting/accounts/${b.account}`}>{b.name}</Link> }, { key: "bank", header: "Bank / branch", render: (b: any) => `${b.bank} ${b.branch}` }, { key: "account_no", header: "Account no" }, { key: "account_code", header: "GL" }, { key: "balance", header: "Balance", num: true, render: (b: any) => money(b.balance) }, { key: "is_active", header: "Active", render: (b: any) => (b.is_active ? "Yes" : "No") }]} fields={[{ name: "name", label: "Name", required: true, col: "col-md-6" }, { name: "bank", label: "Bank", col: "col-md-3" }, { name: "branch", label: "Branch", col: "col-md-3" }, { name: "account_no", label: "Account no", col: "col-md-4" }, { name: "iban", label: "IBAN", col: "col-md-4" }, { name: "opening_balance", label: "Opening balance", type: "money", col: "col-md-4", default: 0 }, { name: "is_active", label: "Active", type: "checkbox", default: true, col: "col-md-3" }]} canDelete={false} />}
      {tab === "cash" && <CrudPage page="bank_cash" title="Cash accounts" crumbs={[]} endpoint="/api/v1/accounting/cash-accounts" columns={[{ key: "name", header: "Account", render: (c: any) => <Link to={`/app/accounting/accounts/${c.account}`}>{c.name}</Link> }, { key: "account_code", header: "GL" }, { key: "custodian_name", header: "Custodian" }, { key: "is_petty", header: "Petty cash", render: (c: any) => (c.is_petty ? "Yes" : "No") }, { key: "balance", header: "Balance", num: true, render: (c: any) => money(c.balance) }]} fields={[{ name: "name", label: "Name", required: true, col: "col-md-6" }, { name: "custodian", label: "Custodian", type: "entity", endpoint: "/api/v1/users", mapOption: (u) => ({ id: u.user_id, label: u.full_name }), col: "col-md-6" }, { name: "is_petty", label: "Petty cash", type: "checkbox", col: "col-md-3" }, { name: "is_active", label: "Active", type: "checkbox", default: true, col: "col-md-3" }]} canDelete={false} />}
      {tab === "cc" && <CrudPage page="accounts" title="Cost centres" crumbs={[]} endpoint="/api/v1/accounting/cost-centres" columns={[{ key: "code", header: "Code" }, { key: "name", header: "Name" }, { key: "kind", header: "Kind" }, { key: "is_active", header: "Active", render: (c: any) => (c.is_active ? "Yes" : "No") }]} fields={[{ name: "code", label: "Code", required: true, col: "col-md-3" }, { name: "name", label: "Name", required: true, col: "col-md-5" }, { name: "kind", label: "Kind", type: "select", options: ["warehouse", "route", "vehicle", "department"].map((k) => ({ value: k, label: titleCase(k) })), col: "col-md-4" }, { name: "is_active", label: "Active", type: "checkbox", default: true }]} />}
    </>
  );
}

export function ReconciliationPage() {
  const qc = useQueryClient();
  const banks = useOptions("/api/v1/accounting/bank-accounts");
  const [bank, setBank] = useState("");
  const [asOf, setAsOf] = useState(new Date().toISOString().slice(0, 10));
  const b = bank || banks.data?.[0]?.id;
  const rep = useQuery({ queryKey: ["recon", b, asOf], queryFn: () => api.get(`/api/v1/accounting/bank-accounts/${b}/reconciliation`, { as_of: asOf }), enabled: Boolean(b) });
  const lines = useQuery({ queryKey: ["stmt", b], queryFn: () => api.get(`/api/v1/accounting/bank-accounts/${b}/statement`, { unreconciled: 1 }), enabled: Boolean(b) });
  const [match, setMatch] = useState<Record<string, string>>({});
  return (
    <>
      <PageHeader title="Bank reconciliation" crumbs={[{ label: "Accounting", to: "/app/accounting" }, { label: "Bank reconciliation" }]} actions={<div className="d-flex gap-2"><select className="form-select" value={b || ""} onChange={(e) => setBank(e.target.value)}>{banks.data?.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select><DateInput className="form-control" value={asOf} onChange={(e) => setAsOf(e.target.value)} /><label className="btn btn-outline-primary mb-0"><i className="ti ti-upload me-1" />Import CSV<input type="file" hidden accept=".csv" onChange={async (e) => { const f = e.target.files?.[0]; if (!f || !b) return; const fd = new FormData(); fd.append("file", f); try { const r = await api.upload(`/api/v1/accounting/bank-accounts/${b}/statement/import`, fd); toast.success(`Imported ${r.imported}, auto-matched ${r.auto_matched}`); qc.invalidateQueries({ queryKey: ["stmt", b] }); qc.invalidateQueries({ queryKey: ["recon", b] }); } catch (err: any) { toast.error(err.message); } }} /></label></div>} />
      <Alert kind="info">CSV columns: <code>date, description, reference, amount</code> (positive = credit to bank) or <code>debit</code>/<code>credit</code>. Lines are auto-matched by amount and date (±3 days) and reference.</Alert>
      {rep.data && <div className="row g-3 mb-3"><div className="col-6 col-md-3"><StatCard label="GL balance" value={money(rep.data.gl_balance, 0)} icon="ti-book" color="primary" /></div><div className="col-6 col-md-3"><StatCard label="Unreconciled statement" value={money(rep.data.unreconciled_statement_total, 0)} icon="ti-file-unknown" color="warning" sub={`${rep.data.unreconciled_count} lines`} /></div><div className="col-6 col-md-3"><StatCard label="Statement balance (est.)" value={money(rep.data.statement_balance_estimate, 0)} icon="ti-building-bank" color="info" /></div><div className="col-6 col-md-3"><StatCard label="Unmatched GL lines" value={rep.data.unmatched_gl_lines?.length ?? 0} icon="ti-list" color="secondary" /></div></div>}
      <div className="row g-3">
        <div className="col-md-6"><div className="card h-100"><div className="card-header"><strong>Unreconciled statement lines</strong></div><div className="card-body p-0"><table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>Date</th><th>Description</th><th className="num">Amount</th><th style={{ width: 220 }}>Match to GL line</th></tr></thead><tbody>{lines.data?.map((l: any) => <tr key={l.id}><td>{date(l.date)}</td><td>{l.description}<div className="text-muted fs-11">{l.reference}</div></td><td className={`num ${Number(l.amount) < 0 ? "text-danger" : "text-success"}`}>{money(l.amount)}</td><td><div className="input-group input-group-sm"><select className="form-select" value={match[l.id] || ""} onChange={(e) => setMatch({ ...match, [l.id]: e.target.value })}><option value="">—</option>{rep.data?.unmatched_gl_lines?.filter((g: any) => Math.abs(Number(g.debit) - Number(g.credit) - Number(l.amount)) < 0.01 || true).map((g: any) => <option key={g.id} value={g.id}>{g.journal} {money(Number(g.debit) - Number(g.credit))}</option>)}</select><button className="btn btn-outline-primary" disabled={!match[l.id]} onClick={async () => { await api.post(`/api/v1/accounting/bank-accounts/${b}/statement/${l.id}/match`, { journal_line: match[l.id] }); qc.invalidateQueries({ queryKey: ["stmt", b] }); qc.invalidateQueries({ queryKey: ["recon", b] }); }}>OK</button></div></td></tr>)}{lines.data && !lines.data.length && <tr><td colSpan={4} className="text-muted text-center py-3">All statement lines reconciled</td></tr>}</tbody></table></div></div></div>
        <div className="col-md-6"><div className="card h-100"><div className="card-header"><strong>GL lines not on the statement</strong></div><div className="card-body p-0"><table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>Date</th><th>Journal</th><th>Description</th><th className="num">Amount</th></tr></thead><tbody>{rep.data?.unmatched_gl_lines?.map((g: any) => <tr key={g.id}><td>{date(g.date)}</td><td><Link to={`/app/accounting/journals/${g.id}`}>{g.journal}</Link></td><td>{g.description}</td><td className="num">{money(Number(g.debit) - Number(g.credit))}</td></tr>)}</tbody></table></div></div></div>
      </div>
    </>
  );
}

export function PeriodsPage() {
  const qc = useQueryClient();
  const { can } = usePerms();
  const years = useQuery({ queryKey: ["fiscal-years"], queryFn: () => api.get("/api/v1/accounting/fiscal-years") });
  const periods = useQuery({ queryKey: ["periods"], queryFn: () => api.get("/api/v1/accounting/periods") });
  const [checks, setChecks] = useState<{ period: any; issues: any[] } | null>(null);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["periods"] }); qc.invalidateQueries({ queryKey: ["fiscal-years"] }); };
  return (
    <>
      <PageHeader title="Accounting periods & year end" crumbs={[{ label: "Accounting", to: "/app/accounting" }, { label: "Periods" }]} />
      {years.data?.map((fy: any) => (
        <div className="card mb-3" key={fy.id}><div className="card-header d-flex justify-content-between align-items-center"><strong>{fy.name}</strong> <span className="text-muted fs-12">{date(fy.start)} → {date(fy.end)}</span><div><StatusBadge status={fy.status} />{fy.status === "open" && pageRights("periods").edit && <button className="btn btn-sm btn-outline-danger ms-2" onClick={async () => { if (!confirm("Close the fiscal year? Income and expenses will be transferred to Retained Earnings.")) return; try { const r = await api.post(`/api/v1/accounting/fiscal-years/${fy.id}/close`); toast.success(`Year closed (${r.closing_entry})`); refresh(); } catch (e: any) { toast.error(e.message); } }}>Close year</button>}</div></div>
          <div className="card-body p-0"><table className="table table-sm fs-13 mb-0"><thead className="table-light"><tr><th>Period</th><th>From</th><th>To</th><th>Status</th><th>Closed by</th><th /></tr></thead><tbody>{periods.data?.filter((p: any) => p.fiscal_year === fy.id).map((p: any) => <tr key={p.id}><td>{p.label}</td><td>{date(p.start)}</td><td>{date(p.end)}</td><td><StatusBadge status={p.status} /></td><td>{p.closed_at ? datetime(p.closed_at) : ""}</td><td className="text-end">{pageRights("periods").edit && (p.status === "open" ? <><button className="btn btn-sm btn-outline-secondary me-1" onClick={async () => setChecks({ period: p, issues: await api.get(`/api/v1/accounting/periods/${p.id}/checks`) })}>Pre-close checks</button><button className="btn btn-sm btn-outline-danger" onClick={async () => { try { await api.post(`/api/v1/accounting/periods/${p.id}/close`); toast.success("Period closed"); refresh(); } catch (e: any) { toast.error(e.message); if (e.problem?.errors?.issues) setChecks({ period: p, issues: e.problem.errors.issues }); } }}>Close</button></> : fy.status === "open" && <button className="btn btn-sm btn-outline-secondary" onClick={async () => { const reason = prompt("Reason for reopening?") || ""; await api.post(`/api/v1/accounting/periods/${p.id}/reopen`, { reason }); refresh(); }}>Reopen</button>)}</td></tr>)}</tbody></table></div></div>
      ))}
      <Modal open={Boolean(checks)} title={`Pre-close checks — ${checks?.period.label}`} onClose={() => setChecks(null)} size="md" footer={<><button className="btn btn-light" onClick={() => setChecks(null)}>Close</button>{checks && <button className="btn btn-danger" onClick={async () => { try { await api.post(`/api/v1/accounting/periods/${checks.period.id}/close`, { force: true }); toast.success("Period closed"); refresh(); setChecks(null); } catch (e: any) { toast.error(e.message); } }}>Close anyway</button>}</>}>{checks?.issues.length ? <ul className="list-group">{checks.issues.map((i, idx) => <li key={idx} className={`list-group-item ${i.level === "error" ? "text-danger" : "text-warning"}`}><i className={`ti ${i.level === "error" ? "ti-alert-circle" : "ti-alert-triangle"} me-1`} />{i.message}</li>)}</ul> : <Alert kind="success">All checks passed.</Alert>}</Modal>
    </>
  );
}

export function OpeningBalancesPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["ob"], queryFn: () => api.list("/api/v1/accounting/opening-balances", { page_size: 50 }) });
  const [lines, setLines] = useState<any[]>([{ key: "a", account: null, debit: "", credit: "" }]);
  const [dateV, setDateV] = useState(new Date().toISOString().slice(0, 10));
  const td = lines.reduce((s, l) => s + Number(l.debit || 0), 0), tc = lines.reduce((s, l) => s + Number(l.credit || 0), 0);
  return (
    <>
      <PageHeader title="Opening balances" crumbs={[{ label: "Accounting", to: "/app/accounting" }, { label: "Opening balances" }]} />
      <Alert kind="info">Enter one line per posting account. Any difference between debits and credits is balanced against <strong>Opening Balance Equity</strong>. Opening stock is entered under Stock → Adjustments (opening stock import) and partner balances via opening invoices/bills or the opening balance CSV import.</Alert>
      <div className="card mb-3"><div className="card-body">
        <Field label="Date" col="col-md-3"><DateInput className="form-control" value={dateV} onChange={(e) => setDateV(e.target.value)} /></Field>
        <table className="table table-sm"><thead className="table-light"><tr><th style={{ minWidth: 300 }}>Account</th><th className="num" style={{ width: 160 }}>Debit</th><th className="num" style={{ width: 160 }}>Credit</th><th style={{ width: 40 }} /></tr></thead><tbody>{lines.map((l) => <tr key={l.key}><td><EntitySelect endpoint="/api/v1/accounting/accounts" query={{ is_group: false }} value={l.account} onChange={(v) => setLines(lines.map((x) => (x.key === l.key ? { ...x, account: v } : x)))} mapOption={acctOption} size="sm" allowClear={false} /></td><td><input type="number" step="0.01" className="form-control form-control-sm text-end" value={l.debit} onChange={(e) => setLines(lines.map((x) => (x.key === l.key ? { ...x, debit: e.target.value } : x)))} /></td><td><input type="number" step="0.01" className="form-control form-control-sm text-end" value={l.credit} onChange={(e) => setLines(lines.map((x) => (x.key === l.key ? { ...x, credit: e.target.value } : x)))} /></td><td><button className="btn btn-sm btn-outline-danger" onClick={() => setLines(lines.filter((x) => x.key !== l.key))}><i className="ti ti-x" /></button></td></tr>)}</tbody><tfoot><tr><td className="text-end fw-semibold">Totals</td><td className="num fw-bold">{money(td)}</td><td className="num fw-bold">{money(tc)}</td><td /></tr></tfoot></table>
        <div className="d-flex justify-content-between"><button className="btn btn-outline-primary btn-sm" onClick={() => setLines([...lines, { key: crypto.randomUUID(), account: null, debit: "", credit: "" }])}><i className="ti ti-plus me-1" />Add line</button><button className="btn btn-primary" onClick={async () => { const valid = lines.filter((l) => l.account && (Number(l.debit) || Number(l.credit))); if (!valid.length) return toast.error("Add lines"); try { const b = await api.post("/api/v1/accounting/opening-balances", { date: dateV, lines: valid.map((l) => ({ account: l.account, debit: l.debit || "0", credit: l.credit || "0" })) }); await api.post(`/api/v1/accounting/opening-balances/${b.id}/post`); toast.success("Opening balances posted"); setLines([{ key: "a", account: null, debit: "", credit: "" }]); qc.invalidateQueries({ queryKey: ["ob"] }); } catch (e: any) { toast.error(e.message); } }}>Post opening balances</button></div>
      </div></div>
      <div className="card"><div className="card-header"><strong>Previous batches</strong></div><div className="card-body p-0"><table className="table table-sm fs-13 mb-0"><thead className="table-light"><tr><th>#</th><th>Date</th><th>Lines</th><th>Posted</th><th>Journal</th></tr></thead><tbody>{q.data?.results.map((b: any) => <tr key={b.id}><td>{b.number}</td><td>{date(b.date)}</td><td>{b.lines.length}</td><td>{b.posted ? "yes" : "no"}</td><td>{b.journal_number}</td></tr>)}</tbody></table></div></div>
    </>
  );
}

/**
 * The cheque books the business writes from.
 *
 * A book is shown as what it physically is — a run of numbered leaves — with the next one to write called out,
 * because that is the only thing anybody comes to this page to find out. Opening a book shows every leaf and
 * what became of it: written (against which payment, to whom, for how much) or spoiled.
 */
export function ChequeBooksPage() {
  const page = usePage("cheque_books");
  const qc = useQueryClient();
  const banks = useOptions("/api/v1/accounting/bank-accounts");
  const books = useQuery({ queryKey: ["cheque-books"], queryFn: () => api.get("/api/v1/accounting/cheque-books") });
  const [add, setAdd] = useState<any>(null);
  const [open, setOpen] = useState<any>(null);
  const [saving, setSaving] = useState(false);

  const leaves = useQuery({
    queryKey: ["cheque-book-leaves", open?.id],
    enabled: Boolean(open),
    queryFn: () => api.get(`/api/v1/accounting/cheque-books/${open.id}/leaves`),
  });

  const save = async () => {
    setSaving(true);
    try {
      await api.post("/api/v1/accounting/cheque-books", {
        bank_account: add.bank_account, prefix: add.prefix || "", first_no: Number(add.first_no), last_no: Number(add.last_no),
        issued_on: add.issued_on || null, notes: add.notes || "",
      });
      toast.success("Cheque book added");
      qc.invalidateQueries({ queryKey: ["cheque-books"] });
      qc.invalidateQueries({ queryKey: ["cheque-leaves"] });
      setAdd(null);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setSaving(false);
    }
  };

  const voidLeaf = async (leaf: any) => {
    const reason = window.prompt(`Mark cheque ${leaf.number} spoiled? Say why (torn, misprinted, cancelled):`, "");
    if (reason === null) return;
    try {
      await api.post(`/api/v1/accounting/cheque-books/${open.id}/leaves/${leaf.id}/void`, { reason });
      toast.success(`${leaf.number} marked spoiled`);
      qc.invalidateQueries({ queryKey: ["cheque-book-leaves", open.id] });
      qc.invalidateQueries({ queryKey: ["cheque-books"] });
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const rows: any[] = books.data || [];
  const byBank: Record<string, any[]> = {};
  for (const b of rows) (byBank[b.bank_label] ||= []).push(b);

  return (
    <>
      <PageHeader title="Cheque books" crumbs={[{ label: "Accounting", to: "/app/accounting" }, { label: "Cheque books" }]}
        actions={<>
          <button className="btn btn-outline-secondary" onClick={() => api.download("/api/v1/accounting/cheques-register/export", "cheque-register.xlsx", { format: "xlsx" })}><i className="ti ti-file-spreadsheet me-1" />Export</button>
          {page.add && <button className="btn btn-primary" onClick={() => setAdd({ bank_account: banks.data?.[0]?.id || "", prefix: "", first_no: "", last_no: "", issued_on: today(), notes: "" })}><i className="ti ti-plus me-1" />Add cheque book</button>}
        </>} />
      <Alert kind="info">These are the blank cheques <strong>you</strong> write. A cheque a customer gives you is their paper — record it on the payment, where its number and their bank are asked for.</Alert>
      {books.isLoading ? <Spinner /> : !rows.length ? (
        <div className="card"><div className="card-body text-center py-5">
          <i className="ti ti-checkbook fs-18 text-muted" />
          <h5 className="mt-2">No cheque books yet</h5>
          <p className="text-muted fs-13 mb-3">Add the book your bank gave you — its first and last number — and the payment screen will fill in the next cheque for you.</p>
          {page.add && <button className="btn btn-primary" onClick={() => setAdd({ bank_account: banks.data?.[0]?.id || "", prefix: "", first_no: "", last_no: "", issued_on: today(), notes: "" })}>Add the first one</button>}
        </div></div>
      ) : Object.entries(byBank).map(([bankLabel, list]) => (
        <div key={bankLabel} className="card mb-3">
          <div className="card-header py-2"><strong className="fs-13"><i className="ti ti-building-bank me-1 text-primary" />{bankLabel}</strong></div>
          <div className="card-body p-0"><div className="table-responsive"><table className="table table-sm align-middle mb-0">
            <thead className="table-light"><tr><th>Book</th><th>Received</th><th className="num">Leaves</th><th>Used</th><th>Next cheque</th><th>Status</th><th /></tr></thead>
            <tbody>{list.map((b: any) => {
              const used = b.leaves_issued + b.leaves_void;
              const pct = b.leaves_total ? Math.round((used / b.leaves_total) * 100) : 0;
              return (
                <tr key={b.id}>
                  <td className="fw-medium">{b.label}{b.notes && <div className="fs-11 text-muted">{b.notes}</div>}</td>
                  <td>{date(b.issued_on)}</td>
                  <td className="num">{b.leaves_total}</td>
                  <td style={{ minWidth: 160 }}>
                    <div className="progress" style={{ height: 6 }}><div className="progress-bar" style={{ width: `${pct}%`, background: pct > 85 ? "#E70D0D" : "#F26522" }} /></div>
                    <div className="fs-11 text-muted mt-1">{b.leaves_issued} written{b.leaves_void ? ` · ${b.leaves_void} spoiled` : ""} · {b.leaves_available} left</div>
                  </td>
                  <td>{b.next_number ? <span className="badge bg-primary-transparent fs-12">{b.next_number}</span> : <span className="text-muted fs-12">—</span>}</td>
                  <td><StatusBadge status={b.status} /></td>
                  <td className="text-end"><button className="btn btn-sm btn-outline-primary" onClick={() => setOpen(b)}>Open book</button></td>
                </tr>
              );
            })}</tbody>
          </table></div></div>
        </div>
      ))}

      <Modal open={Boolean(add)} title="Add a cheque book" onClose={() => setAdd(null)} size="md"
        footer={<><button className="btn btn-light" onClick={() => setAdd(null)}>Cancel</button><button className="btn btn-primary" disabled={saving || !add?.bank_account || !add?.first_no || !add?.last_no} onClick={save}>Add book</button></>}>
        {add && <div className="row">
          <Field label="Bank account *" col="col-12"><select className="form-select" value={add.bank_account} onChange={(e) => setAdd({ ...add, bank_account: e.target.value })}>{(banks.data || []).map((b: any) => <option key={b.id} value={b.id}>{b.name} · {b.bank}{b.account_no ? ` · ${b.account_no}` : ""}</option>)}</select></Field>
          <Field label="Prefix" col="col-md-4" hint="Letters printed before the number, if any"><input className="form-control" value={add.prefix} onChange={(e) => setAdd({ ...add, prefix: e.target.value })} placeholder="A" /></Field>
          <Field label="First number *" col="col-md-4"><input type="number" className="form-control" value={add.first_no} onChange={(e) => setAdd({ ...add, first_no: e.target.value })} placeholder="101" /></Field>
          <Field label="Last number *" col="col-md-4"><input type="number" className="form-control" value={add.last_no} onChange={(e) => setAdd({ ...add, last_no: e.target.value })} placeholder="125" /></Field>
          <Field label="Received on" col="col-md-6"><DateInput className="form-control" value={add.issued_on} onChange={(e) => setAdd({ ...add, issued_on: e.target.value })} /></Field>
          <Field label="Note" col="col-md-6"><input className="form-control" value={add.notes} onChange={(e) => setAdd({ ...add, notes: e.target.value })} placeholder="Collected from the branch" /></Field>
          {add.first_no && add.last_no && Number(add.last_no) >= Number(add.first_no) &&
            <div className="col-12"><div className="form-text">{Number(add.last_no) - Number(add.first_no) + 1} cheques, {add.prefix}{add.first_no} to {add.prefix}{add.last_no}.</div></div>}
        </div>}
      </Modal>

      <Modal open={Boolean(open)} title={open ? `${open.label} · ${open.bank_name}` : ""} onClose={() => setOpen(null)} size="lg"
        footer={<button className="btn btn-light" onClick={() => setOpen(null)}>Close</button>}>
        {leaves.isLoading ? <Spinner /> : (
          <div className="table-responsive" style={{ maxHeight: "60vh" }}>
            <table className="table table-sm align-middle mb-0">
              <thead className="table-light position-sticky top-0"><tr><th>Cheque</th><th>Status</th><th>Payment</th><th>Paid to</th><th className="num">Amount</th><th /></tr></thead>
              <tbody>{(leaves.data || []).map((l: any) => (
                <tr key={l.id} className={l.status === "void" ? "text-muted" : ""}>
                  <td className="fw-medium">{l.number}</td>
                  <td><StatusBadge status={l.status} />{l.void_reason && <div className="fs-11 text-muted">{l.void_reason}</div>}</td>
                  <td>{l.payment ? <Link to={`/app/accounting/payments/${l.payment}`}>{l.payment_number}</Link> : <span className="text-muted">—</span>}{l.payment_date && <div className="fs-11 text-muted">{date(l.payment_date)}</div>}</td>
                  <td>{l.payee || "—"}</td>
                  <td className="num">{l.amount ? money(l.amount) : "—"}</td>
                  <td className="text-end">{l.status === "available" && page.edit && <button className="btn btn-sm btn-outline-danger" onClick={() => voidLeaf(l)} title="Mark spoiled">Spoiled</button>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Modal>
    </>
  );
}
