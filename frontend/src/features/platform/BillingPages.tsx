import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { date, money, titleCase } from "@/lib/format";
import { toast } from "@/lib/toast";
import { DateInput } from "@/components/ui/DateInput";
import { Field, PageHeader, Spinner, StatCard, StatusBadge, Tabs } from "@/components/ui/Bits";
import { Column, DataTable } from "@/components/ui/DataTable";
import { ListPage } from "@/components/ui/ListPage";
import { Modal } from "@/components/ui/Modal";

/**
 * The platform's billing desk (owner, 1 Nov 2026): payments companies sent with their screenshots wait here for a
 * platform admin — approving one renews the subscription and reopens a suspended company; turning one down tells
 * the owner why. Everything received is on record, and the report adds it up.
 */

const P = "/api/v1/platform";
const METHODS = ["bank_transfer", "cash", "cheque", "jazzcash", "easypaisa", "card", "other"];
export const methodOptions = METHODS.map((m) => ({ value: m, label: m === "jazzcash" ? "JazzCash" : m === "easypaisa" ? "Easypaisa" : titleCase(m) }));

function Screenshot({ url }: { url?: string | null }) {
  if (!url) return <span className="text-muted">—</span>;
  const pdf = /\.pdf($|\?)/i.test(url);
  return <a href={url} target="_blank" rel="noreferrer" title="Open the screenshot">{pdf ? <><i className="ti ti-file-type-pdf me-1" />PDF</> : <img src={url} alt="payment screenshot" style={{ height: 44, maxWidth: 80, objectFit: "cover", borderRadius: 4, border: "1px solid #e5e7eb" }} />}</a>;
}

/** Approve (renews and reactivates) or turn down one submitted payment. */
function ReviewDialog({ pay, mode, onClose, onDone }: { pay: any; mode: "approve" | "reject"; onClose: () => void; onDone: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      await api.post(`${P}/payments/${pay.id}/${mode}`, mode === "approve" ? { note: text } : { reason: text });
      toast.success(mode === "approve" ? `Approved — ${pay.tenant_name} is renewed` : "Turned down — the owner is told why");
      onDone(); onClose();
    } catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
  };
  return (
    <Modal open onClose={onClose} size="md" title={mode === "approve" ? <><i className="ti ti-circle-check me-2 text-success" />Approve payment</> : <><i className="ti ti-circle-x me-2 text-danger" />Turn down payment</>}
      footer={<><button className="btn btn-light" onClick={onClose}>Cancel</button><button className={`btn ${mode === "approve" ? "btn-success" : "btn-danger"}`} disabled={busy || (mode === "reject" && !text.trim())} onClick={run}>{busy ? "Saving…" : mode === "approve" ? "Approve" : "Turn down"}</button></>}>
      <div className="row g-2 mb-3 fs-13">
        <div className="col-6"><span className="text-muted">Company</span><div className="fw-semibold">{pay.tenant_name}</div></div>
        <div className="col-6"><span className="text-muted">Amount</span><div className="fw-semibold">{pay.currency} {money(pay.amount)}</div></div>
        <div className="col-6"><span className="text-muted">Paid by</span><div>{pay.method_label}{pay.reference ? ` · ${pay.reference}` : ""}</div></div>
        <div className="col-6"><span className="text-muted">Date · invoice</span><div>{date(pay.paid_at)} · {pay.invoice_number || "—"}</div></div>
        {pay.note && <div className="col-12"><span className="text-muted">Their note</span><div>{pay.note}</div></div>}
      </div>
      {pay.receipt_file && !/\.pdf($|\?)/i.test(pay.receipt_file) && <a href={pay.receipt_file} target="_blank" rel="noreferrer"><img src={pay.receipt_file} alt="payment screenshot" className="img-fluid border rounded mb-3" style={{ maxHeight: 320 }} /></a>}
      {pay.receipt_file && /\.pdf($|\?)/i.test(pay.receipt_file) && <p><a href={pay.receipt_file} target="_blank" rel="noreferrer"><i className="ti ti-file-type-pdf me-1" />Open the receipt</a></p>}
      <Field label={mode === "approve" ? "Note (optional)" : "Why it is turned down *"}><textarea className="form-control" rows={2} value={text} onChange={(e) => setText(e.target.value)} /></Field>
    </Modal>
  );
}

export function PendingPayments({ compact = false }: { compact?: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["platform-pending-payments"], queryFn: () => api.list(`${P}/payments`, { status: "pending", page_size: 100, ordering: "paid_at" }) });
  const [dlg, setDlg] = useState<{ pay: any; mode: "approve" | "reject" } | null>(null);
  const done = () => { ["platform-pending-payments", "platform-dash", "list"].forEach((k) => qc.invalidateQueries({ queryKey: [k] })); };
  const rows: any[] = q.data?.results || [];
  if (compact && !rows.length) return null;
  const cols: Column<any>[] = [
    { key: "paid_at", header: "Paid on", render: (p) => date(p.paid_at) },
    { key: "tenant_name", header: "Company", render: (p) => <Link to={`/admin/tenants/${p.tenant}`}>{p.tenant_name}</Link> },
    { key: "amount", header: "Amount", num: true, render: (p) => `${p.currency} ${money(p.amount)}` },
    { key: "method_label", header: "Method", render: (p) => <>{p.method_label}{p.reference && <div className="fs-11 text-muted">{p.reference}</div>}</> },
    { key: "invoice_number", header: "Invoice", render: (p) => p.invoice_number || "—" },
    { key: "receipt_file", header: "Screenshot", render: (p) => <Screenshot url={p.receipt_file} /> },
    { key: "submitted_by_name", header: "Sent by", render: (p) => <>{p.submitted_by_name || "—"}<div className="fs-11 text-muted">{date(p.created_at)}</div></> },
    { key: "id", header: "", render: (p) => <div className="d-flex gap-1 justify-content-end no-row-click"><button className="btn btn-sm btn-success" onClick={() => setDlg({ pay: p, mode: "approve" })}><i className="ti ti-check me-1" />Approve</button><button className="btn btn-sm btn-outline-danger" onClick={() => setDlg({ pay: p, mode: "reject" })}><i className="ti ti-x" /></button></div> },
  ];
  return (
    <div className={`card ${compact ? "border-warning" : ""} mb-3`}>
      <div className="card-header d-flex justify-content-between align-items-center"><strong><i className="ti ti-hourglass me-1 text-warning" />Payments awaiting approval ({rows.length})</strong>{compact && <Link to="/admin/payments" className="fs-12">All payments</Link>}</div>
      <div className="card-body p-0">{q.isLoading ? <Spinner /> : <DataTable columns={cols} rows={rows} emptyText="Nothing waiting — every payment sent has been checked." />}</div>
      {dlg && <ReviewDialog pay={dlg.pay} mode={dlg.mode} onClose={() => setDlg(null)} onDone={done} />}
    </div>
  );
}

export function PlatformPaymentsPage() {
  const [tab, setTab] = useState("pending");
  const cols: Column<any>[] = [
    { key: "paid_at", header: "Date", sortKey: "paid_at", render: (p) => date(p.paid_at) },
    { key: "tenant_name", header: "Company", render: (p) => <Link to={`/admin/tenants/${p.tenant}`}>{p.tenant_name}</Link> },
    { key: "invoice_number", header: "Invoice" },
    { key: "method", header: "Method", render: (p) => p.method_label || titleCase(p.method) },
    { key: "reference", header: "Reference" },
    { key: "amount", header: "Amount", num: true, sortKey: "amount", render: (p) => money(p.amount) },
    { key: "status", header: "Status", render: (p) => <><StatusBadge status={p.status === "pending" ? "pending_approval" : p.status} label={p.status_label} />{p.review_note && <div className="fs-11 text-muted">{p.review_note}</div>}</> },
    { key: "receipt_file", header: "Screenshot", render: (p) => <Screenshot url={p.receipt_file} /> },
    { key: "recorded_by_name", header: "By", render: (p) => p.reviewed_by_name || p.recorded_by_name || p.submitted_by_name || "—" },
  ];
  return (
    <>
      <Tabs tabs={[{ key: "pending", label: "Awaiting approval" }, { key: "all", label: "All payments" }]} active={tab} onChange={setTab} />
      {tab === "pending" ? <><PageHeader title="Payments awaiting approval" crumbs={[]} /><PendingPayments /></>
        : <ListPage title="Platform payments" endpoint={`${P}/payments`} columns={cols} defaultOrdering="-paid_at"
          actions={<button className="btn btn-outline-success" onClick={() => api.download(`${P}/payments/export`, "platform-payments.csv")}><i className="ti ti-file-type-csv me-1" />Export</button>}
          filters={[{ name: "method", label: "Method", type: "select", options: methodOptions }, { name: "status", label: "Status", type: "select", options: [["paid", "Paid"], ["partial", "Partial"], ["pending", "Awaiting approval"], ["rejected", "Rejected"], ["refunded", "Refunded"], ["failed", "Failed"]].map(([value, label]) => ({ value, label })) }, { name: "tenant", label: "Company", type: "entity", endpoint: `${P}/tenants`, mapOption: (t) => ({ id: t.id, label: t.display_name }) }, { name: "date_from", label: "From", type: "date" }, { name: "date_to", label: "To", type: "date" }]} />}
    </>
  );
}

const firstOfMonth = () => { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10); };

/** "keep record of payments and report" (owner, 1 Nov 2026). */
export function BillingReportPage() {
  const [range, setRange] = useState({ date_from: firstOfMonth(), date_to: new Date().toISOString().slice(0, 10) });
  const q = useQuery({ queryKey: ["platform-billing-report", range], queryFn: () => api.get(`${P}/billing-report`, range) });
  const d = q.data;
  const kv = (obj: Record<string, string> = {}) => Object.entries(obj).map(([k, v]) => <div key={k} className="d-flex justify-content-between fs-13 border-bottom py-1"><span>{k}</span><strong>{money(v, 0)}</strong></div>);
  return (
    <>
      <PageHeader title="Billing report" crumbs={[]} actions={<>
        <DateInput className="form-control" style={{ width: 150 }} value={range.date_from} onChange={(e: any) => setRange({ ...range, date_from: e.target.value })} />
        <DateInput className="form-control" style={{ width: 150 }} value={range.date_to} onChange={(e: any) => setRange({ ...range, date_to: e.target.value })} />
        <button className="btn btn-outline-success" onClick={() => api.download(`${P}/payments/export?date_from=${range.date_from}&date_to=${range.date_to}`, "platform-payments.csv")}><i className="ti ti-file-type-csv me-1" />Payments CSV</button>
      </>} />
      {q.isLoading || !d ? <Spinner /> : <>
        <div className="row g-3 mb-3">
          <div className="col-6 col-xl-3"><StatCard label="Received" value={money(d.summary.received, 0)} icon="ti-cash" color="success" sub={`${d.summary.payments} payment(s) · refunded ${money(d.summary.refunded, 0)}`} /></div>
          <div className="col-6 col-xl-3"><StatCard label="Invoiced" value={money(d.summary.invoiced, 0)} icon="ti-file-invoice" color="primary" sub="in the period" /></div>
          <div className="col-6 col-xl-3"><StatCard label="Awaiting approval" value={money(d.summary.pending_amount, 0)} icon="ti-hourglass" color="warning" sub={`${d.summary.pending_count} payment(s) · ${d.summary.rejected} turned down`} to="/admin/payments" /></div>
          <div className="col-6 col-xl-3"><StatCard label="Outstanding" value={money(d.summary.outstanding, 0)} icon="ti-alert-circle" color="danger" sub="all open invoices" to="/admin/outstanding" /></div>
        </div>
        <div className="row g-3 mb-3">
          <div className="col-md-4"><div className="card h-100"><div className="card-header"><strong>By method</strong></div><div className="card-body">{kv(d.by_method)}{!Object.keys(d.by_method).length && <span className="text-muted fs-13">Nothing received</span>}</div></div></div>
          <div className="col-md-4"><div className="card h-100"><div className="card-header"><strong>By month</strong></div><div className="card-body">{kv(d.by_month)}</div></div></div>
          <div className="col-md-4"><div className="card h-100"><div className="card-header"><strong>By company</strong></div><div className="card-body">{kv(d.by_tenant)}</div></div></div>
        </div>
        <div className="card mb-3"><div className="card-header"><strong>Payments in the period</strong></div><div className="card-body p-0">
          <DataTable rows={d.payments} emptyText="No payments in the period" columns={[
            { key: "date", header: "Date", render: (r: any) => date(r.date) }, { key: "tenant", header: "Company", render: (r: any) => <Link to={`/admin/tenants/${r.tenant_id}`}>{r.tenant}</Link> }, { key: "invoice", header: "Invoice" },
            { key: "method", header: "Method" }, { key: "reference", header: "Reference" }, { key: "amount", header: "Amount", num: true, render: (r: any) => money(r.amount) },
            { key: "status", header: "Status", render: (r: any) => <StatusBadge status={r.status === "pending" ? "pending_approval" : r.status} label={r.status_label} /> }, { key: "by", header: "Approved / recorded by" }, { key: "note", header: "Note" },
          ]} />
        </div></div>
        <div className="row g-3">
          <div className="col-xl-6"><div className="card h-100"><div className="card-header"><strong>Renewals due in 30 days</strong></div><div className="card-body p-0">
            <DataTable rows={d.renewals_due} emptyText="None" columns={[{ key: "tenant", header: "Company", render: (r: any) => <Link to={`/admin/tenants/${r.tenant_id}`}>{r.tenant}</Link> }, { key: "plan", header: "Plan" }, { key: "end_date", header: "Expires", render: (r: any) => <>{date(r.end_date)} <span className="text-muted fs-11">({r.days_left} d)</span></> }, { key: "price", header: "Price", num: true, render: (r: any) => money(r.price, 0) }]} />
          </div></div></div>
          <div className="col-xl-6"><div className="card h-100"><div className="card-header"><strong>Lapsed — past due or suspended</strong></div><div className="card-body p-0">
            <DataTable rows={d.lapsed} emptyText="None" columns={[{ key: "tenant", header: "Company", render: (r: any) => <Link to={`/admin/tenants/${r.tenant_id}`}>{r.tenant}</Link> }, { key: "status", header: "Status", render: (r: any) => <StatusBadge status={r.status} /> }, { key: "end_date", header: "Expired", render: (r: any) => date(r.end_date) }, { key: "grace_until", header: "Grace until", render: (r: any) => date(r.grace_until) }, { key: "price", header: "Price", num: true, render: (r: any) => money(r.price, 0) }]} />
          </div></div></div>
        </div>
      </>}
    </>
  );
}
