import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "@/api/client";
import { date, money } from "@/lib/format";
import { useAction, useDoc, useOptions } from "@/lib/hooks";
import { pageRights } from "@/lib/perms";
import { Alert, Field, InfoItem, PageHeader, Spinner, StatCard, StatusBadge } from "@/components/ui/Bits";
import { Column } from "@/components/ui/DataTable";
import { ListPage } from "@/components/ui/ListPage";
import { Modal } from "@/components/ui/Modal";

export function CompanyClaimsPage() {
  const cols: Column<any>[] = [
    { key: "number", header: "Claim #", render: (c) => <span className="fw-semibold text-dark">{c.number}</span> },
    { key: "date", header: "Date", render: (c) => date(c.date) },
    { key: "supplier_name", header: "Company (manufacturer)" },
    { key: "invoice_number", header: "Invoice", render: (c) => <>{c.invoice_number}<div className="text-muted fs-11">{c.customer_name}</div></> },
    { key: "discount_pct", header: "Discount", render: (c) => `${Number(c.discount_pct)}% · co ${Number(c.company_pct)}%` },
    { key: "total", header: "Claim", num: true, render: (c) => money(c.total) },
    { key: "paid_amount", header: "Recovered", num: true, render: (c) => money(c.paid_amount) },
    { key: "balance", header: "Outstanding", num: true, render: (c) => <span className={Number(c.balance) > 0 ? "fw-semibold" : "text-muted"}>{money(c.balance)}</span> },
    { key: "status", header: "Status", render: (c) => <StatusBadge status={c.status} /> },
  ];
  return (
    <ListPage
      title="Company discount claims"
      crumbs={[{ label: "Sales" }, { label: "Company claims" }]}
      endpoint="/api/v1/company-claims"
      columns={cols}
      rowLink={(c) => `/app/sales/company-claims/${c.id}`}
      page="company_claims"
      defaultOrdering="-date"
      searchPlaceholder="Search claim, company, invoice…"
      filters={[
        { name: "status", label: "Status", type: "select", options: [{ value: "posted", label: "Open" }, { value: "partially_paid", label: "Partially recovered" }, { value: "paid", label: "Recovered" }] },
        { name: "supplier", label: "Company", type: "entity", endpoint: "/api/v1/suppliers", mapOption: (s: any) => ({ id: s.id, label: s.name }) },
      ]}
    />
  );
}

export function CompanyClaimDetailPage() {
  const { id } = useParams();
  const q = useDoc("/api/v1/company-claims", id);
  const { run, busy } = useAction("/api/v1/company-claims", id, [["doc", "/api/v1/company-claims", id], ["list", "/api/v1/company-claims"]]);
  const [dlg, setDlg] = useState<null | "settle" | "netoff" | "cancel">(null);
  const [netAmt, setNetAmt] = useState("");
  if (q.isLoading || !q.data) return <Spinner />;
  const c = q.data;
  const rights = pageRights("company_claims");
  const open = ["posted", "partially_paid"].includes(c.status);
  return (
    <>
      <PageHeader title={c.number} badge={<StatusBadge status={c.status} />} crumbs={[{ label: "Company claims", to: "/app/sales/company-claims" }, { label: c.number }]}
        actions={open && rights.extras?.settle && <>
          <button className="btn btn-primary" disabled={!!busy} onClick={() => setDlg("settle")}><i className="ti ti-cash me-1" />Company paid</button>
          <button className="btn btn-outline-primary" disabled={!!busy} onClick={() => setDlg("netoff")}><i className="ti ti-arrows-exchange me-1" />Net off purchases</button>
          {Number(c.paid_amount) === 0 && rights.delete && <button className="btn btn-outline-danger" disabled={!!busy} onClick={() => setDlg("cancel")}>Cancel</button>}
        </>} />
      <Alert kind="info">This is <strong>{c.supplier_name}</strong>'s {Number(c.company_pct)}% share of the discount on invoice <Link to={`/app/sales/invoices/${c.invoice}`}>{c.invoice_number}</Link>. Recover it either by recording a payment from the company, or by netting it against what you owe them on purchases.</Alert>
      <div className="row g-3 mb-3">
        <div className="col-6 col-md-3"><StatCard label="Claim amount" value={money(c.total)} icon="ti-receipt" color="primary" /></div>
        <div className="col-6 col-md-3"><StatCard label="Recovered" value={money(c.paid_amount)} icon="ti-check" color="success" /></div>
        <div className="col-6 col-md-3"><StatCard label="Outstanding" value={money(c.balance)} icon="ti-clock" color="warning" /></div>
        <div className="col-6 col-md-3"><StatCard label="Total discount" value={`${Number(c.discount_pct)}%`} icon="ti-discount" color="secondary" /></div>
      </div>
      <div className="card mb-3"><div className="card-body"><div className="row">
        <InfoItem label="Company" col="col-md-3">{c.supplier_name}</InfoItem>
        <InfoItem label="Invoice" col="col-md-3"><Link to={`/app/sales/invoices/${c.invoice}`}>{c.invoice_number}</Link></InfoItem>
        <InfoItem label="Customer" col="col-md-3">{c.customer_name}</InfoItem>
        <InfoItem label="Date" col="col-md-3">{date(c.date)}</InfoItem>
        {c.journal_number && <InfoItem label="Journal" col="col-md-3">{c.journal_number}</InfoItem>}
      </div></div></div>
      <div className="card"><div className="card-header"><strong>Lines</strong></div><div className="card-body p-0">
        <table className="table table-sm mb-0 fs-13"><thead className="table-light"><tr><th>Item</th><th className="num">Qty</th><th className="num">Sale base</th><th className="num">Company share</th></tr></thead>
          <tbody>{c.lines.map((l: any) => <tr key={l.id}><td>{l.variant_name}<div className="text-muted fs-11">{l.variant_sku}</div></td><td className="num">{Number(l.qty)}</td><td className="num">{money(l.base_amount)}</td><td className="num fw-medium">{money(l.company_amount)}</td></tr>)}</tbody>
        </table>
      </div></div>
      {dlg === "settle" && <SettleModal claim={c} onClose={() => setDlg(null)} onDone={() => { setDlg(null); q.refetch(); }} />}
      <Modal open={dlg === "netoff"} title="Net off against purchases" onClose={() => setDlg(null)} footer={<><button className="btn btn-light" onClick={() => setDlg(null)}>Cancel</button><button className="btn btn-primary" disabled={!!busy} onClick={async () => { await run("net-off", { amount: netAmt || undefined }, { success: "Netted against the company's payable" }); setDlg(null); }}>Net off</button></>}>
        <p className="fs-13 text-muted">Reduce what you owe <strong>{c.supplier_name}</strong> on their bills by this claim. Leave blank to net off the full outstanding {money(c.balance)}.</p>
        <Field label="Amount"><input type="number" step="0.01" className="form-control" value={netAmt} onChange={(e) => setNetAmt(e.target.value)} placeholder={String(c.balance)} /></Field>
      </Modal>
      <Modal open={dlg === "cancel"} title="Cancel claim" onClose={() => setDlg(null)} footer={<><button className="btn btn-light" onClick={() => setDlg(null)}>Keep</button><button className="btn btn-danger" disabled={!!busy} onClick={async () => { await run("cancel", {}, { success: "Claim cancelled" }); setDlg(null); }}>Cancel claim</button></>}>
        <p className="fs-13">Reverse this claim's journal and mark it cancelled. Only possible while nothing has been recovered.</p>
      </Modal>
    </>
  );
}

function SettleModal({ claim, onClose, onDone }: { claim: any; onClose: () => void; onDone: () => void }) {
  const [form, setForm] = useState({ amount: String(claim.balance), method: "bank_transfer", bank_account: "", cash_account: "", date: new Date().toISOString().slice(0, 10), reference: "" });
  const [busy, setBusy] = useState(false);
  const banks = useOptions("/api/v1/accounting/bank-accounts");
  const cashes = useOptions("/api/v1/accounting/cash-accounts");
  const submit = async () => {
    setBusy(true);
    try {
      await api.post(`/api/v1/company-claims/${claim.id}/settle`, {
        amount: form.amount || undefined, method: form.method,
        bank_account: form.method === "cash" ? "" : form.bank_account || "",
        cash_account: form.method === "cash" ? form.cash_account || "" : "",
        date: form.date, reference: form.reference,
      });
      onDone();
    } catch (e: any) { (await import("@/lib/toast")).toast.error(e.message); } finally { setBusy(false); }
  };
  return (
    <Modal open title={`Company payment — ${claim.supplier_name}`} onClose={onClose} footer={<><button className="btn btn-light" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={busy} onClick={submit}>Record payment</button></>}>
      <p className="fs-13 text-muted">Record money received from <strong>{claim.supplier_name}</strong> for this claim. Outstanding {money(claim.balance)}.</p>
      <div className="row">
        <Field label="Amount" col="col-md-6"><input type="number" step="0.01" className="form-control" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} /></Field>
        <Field label="Method" col="col-md-6"><select className="form-select" value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value })}><option value="bank_transfer">Bank transfer</option><option value="online">Online</option><option value="cheque">Cheque</option><option value="cash">Cash</option></select></Field>
        {form.method === "cash"
          ? <Field label="Cash account" col="col-md-6"><select className="form-select" value={form.cash_account} onChange={(e) => setForm({ ...form, cash_account: e.target.value })}><option value="">default</option>{cashes.data?.map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
          : <Field label="Bank account" col="col-md-6"><select className="form-select" value={form.bank_account} onChange={(e) => setForm({ ...form, bank_account: e.target.value })}><option value="">select…</option>{banks.data?.map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>}
        <Field label="Date" col="col-md-6"><input type="date" className="form-control" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} /></Field>
        <Field label="Reference" col="col-md-12"><input className="form-control" value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} placeholder="cheque no, transfer ref…" /></Field>
      </div>
    </Modal>
  );
}
