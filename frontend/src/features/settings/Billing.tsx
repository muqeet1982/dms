import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useAuth } from "@/lib/auth";
import { date, money } from "@/lib/format";
import { toast } from "@/lib/toast";
import { Field, StatusBadge } from "@/components/ui/Bits";
import { Modal } from "@/components/ui/Modal";

/**
 * Paying the platform (owner, 1 Nov 2026: "give option to make payment by showing bank information and all payment
 * methods and option to upload and submit screenshots, and superadmin approves it to reactivate the subscription").
 * The same panel sits on Settings → Subscription and on the screen a suspended company sees.
 */

const ICON: Record<string, string> = { bank: "ti-building-bank", wallet: "ti-device-mobile", note: "ti-info-circle", other: "ti-cash" };

/** One way to pay, with each detail copyable. */
function MethodCard({ m }: { m: any }) {
  const row = (label: string, v?: string) => v ? (
    <div className="d-flex justify-content-between align-items-center fs-13 py-1 border-bottom">
      <span className="text-muted">{label}</span>
      <span className="fw-medium text-end">{v} <button type="button" className="btn btn-sm btn-link p-0 ms-1" title="Copy" onClick={() => { navigator.clipboard?.writeText(v); toast.success(`${label} copied`); }}><i className="ti ti-copy" /></button></span>
    </div>
  ) : null;
  return (
    <div className="card h-100 mb-0"><div className="card-body py-3">
      <div className="fw-semibold mb-2"><i className={`ti ${ICON[m.type] || ICON.other} me-1 text-primary`} />{m.name}</div>
      {row("Account title", m.account_title)}{row("Account number", m.account_number)}{row("IBAN", m.iban)}{row("Branch", m.branch)}
      {m.instructions && <div className="fs-12 text-muted mt-2" style={{ whiteSpace: "pre-line" }}>{m.instructions}</div>}
    </div></div>
  );
}

export function PayPanel({ data, onDone }: { data: any; onDone?: () => void }) {
  const notice = data?.notice;
  const owed = Number(data?.balance || 0) > 0 ? Number(data.balance) : Number(notice?.amount_due || 0);
  const [f, setF] = useState({ amount: owed ? String(Math.round(owed * 100) / 100) : "", method: "bank_transfer", reference: "", paid_at: new Date().toISOString().slice(0, 10), note: "" });
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement | null>(null);
  const methods: any[] = data?.payment_methods || [];
  const pending = (data?.payments || []).filter((p: any) => p.status === "pending");
  const submit = async () => {
    if (!file) return toast.error("Attach the payment screenshot");
    setBusy(true);
    try {
      const fd = new FormData();
      Object.entries(f).forEach(([k, v]) => fd.append(k, v));
      fd.append("receipt", file);
      await api.upload("/api/v1/billing/payments", fd);
      toast.success("Payment sent — it is checked and your subscription renewed as soon as it is approved");
      setFile(null); setF({ ...f, reference: "", note: "" });
      if (input.current) input.current.value = "";
      onDone?.();
    } catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
  };
  return (
    <>
      <h6 className="fs-13 mb-2">How to pay</h6>
      {methods.length ? <div className="row g-3 mb-3">{methods.map((m, i) => <div key={i} className="col-md-6 col-xl-4"><MethodCard m={m} /></div>)}</div>
        : <p className="text-muted fs-13">Payment details have not been set up yet — contact support.</p>}
      {pending.length > 0 && <div className="alert alert-info py-2 fs-13">
        <i className="ti ti-hourglass me-1" />{pending.map((p: any) => `${p.currency} ${money(p.amount, 0)} by ${p.method_label}${p.reference ? ` (${p.reference})` : ""}`).join(", ")} — awaiting approval.
      </div>}
      {data?.can_pay ? (
        <div className="card mb-0 border"><div className="card-body">
          <h6 className="fs-13 mb-3">I have paid — send the screenshot</h6>
          <div className="row">
            <Field label="Amount paid *" col="col-md-3"><input type="number" min="0" step="0.01" className="form-control" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
            <Field label="Paid by *" col="col-md-3"><select className="form-select" value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })}>{(data?.method_choices || []).map((m: any) => <option key={m.value} value={m.value}>{m.label}</option>)}</select></Field>
            <Field label="Transaction / reference no." col="col-md-3"><input className="form-control" value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} /></Field>
            <Field label="Date paid" col="col-md-3"><input type="date" className="form-control" value={f.paid_at} onChange={(e) => setF({ ...f, paid_at: e.target.value })} /></Field>
            <Field label="Screenshot or receipt *" col="col-md-6"><input ref={input} type="file" accept="image/*,.pdf" className="form-control" onChange={(e) => setFile(e.target.files?.[0] || null)} /></Field>
            <Field label="Note" col="col-md-6"><input className="form-control" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
          </div>
          <button className="btn btn-primary" disabled={busy || !Number(f.amount) || !file} onClick={submit}><i className="ti ti-send me-1" />{busy ? "Sending…" : "Submit payment"}</button>
        </div></div>
      ) : <p className="text-muted fs-13 mb-0">Your company's owner submits the payment.</p>}
    </>
  );
}

/** Payments the company sent, with where each one stands. */
export function PaymentsTable({ payments }: { payments: any[] }) {
  return (
    <table className="table table-sm fs-13 mb-0"><thead className="table-light"><tr><th>Date</th><th>Invoice</th><th>Method</th><th>Reference</th><th className="num">Amount</th><th>Status</th><th /></tr></thead>
      <tbody>{payments.map((p) => <tr key={p.id}>
        <td>{date(p.paid_at)}</td><td>{p.invoice_number || "—"}</td><td>{p.method_label}</td><td>{p.reference || "—"}</td><td className="num">{money(p.amount)}</td>
        <td><StatusBadge status={p.status === "pending" ? "pending_approval" : p.status} label={p.status_label} />{p.status === "rejected" && p.review_note && <div className="fs-11 text-danger">{p.review_note}</div>}</td>
        <td>{p.receipt_file && <a href={p.receipt_file} target="_blank" rel="noreferrer" className="fs-12"><i className="ti ti-photo me-1" />Screenshot</a>}</td>
      </tr>)}{!payments.length && <tr><td colSpan={7} className="text-muted text-center py-3">No payments yet</td></tr>}</tbody>
    </table>
  );
}

const LEVEL_CLASS: Record<string, string> = { due: "warning", overdue: "danger", suspended: "danger" };

/**
 * The payment message (owner, 1 Nov 2026: "message on login for payment 3 days before payment due and until grace
 * period"): a strip across the app for everyone, and for the owner a window once per sign-in with the way to pay.
 */
export function BillingNotice() {
  const me = useAuth((s) => s.me) as any;
  const b = me?.billing;
  const owner = Boolean(me?.is_owner);
  const key = `dms.billing-seen.${me?.tenant?.id}.${b?.level}.${b?.end_date}`;
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!b || !owner || me?.impersonating) return;
    try { if (sessionStorage.getItem(key)) return; sessionStorage.setItem(key, "1"); } catch { /* private mode: show it anyway */ }
    setOpen(true);
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!b || b.level === "suspended") return null;
  const cls = LEVEL_CLASS[b.level] || "info";
  return (
    <>
      <div className={`alert alert-${cls} border-0 rounded-0 mb-0 d-flex align-items-center justify-content-between py-2`} style={{ marginLeft: "var(--sidebar-width, 0)" }}>
        <div className="fs-13"><i className="ti ti-credit-card me-1" />{owner ? b.message : b.level === "overdue" ? `${me?.tenant?.display_name}'s subscription is overdue — access stops after ${date(b.grace_until)} unless it is paid.` : `${me?.tenant?.display_name}'s subscription is due on ${date(b.end_date)}.`}</div>
        {owner && <Link to="/app/settings/subscription" className={`btn btn-sm btn-${cls === "warning" ? "dark" : "light"} py-0 fs-12 ms-2`}><i className="ti ti-cash me-1" />Pay now</Link>}
      </div>
      <Modal open={open} onClose={() => setOpen(false)} title={<><i className={`ti ti-credit-card me-2 text-${cls}`} />{b.level === "overdue" ? "Payment overdue" : "Payment due"}</>} size="sm"
        footer={<><button className="btn btn-light" onClick={() => setOpen(false)}>Later</button><Link to="/app/settings/subscription" className="btn btn-primary" onClick={() => setOpen(false)}><i className="ti ti-cash me-1" />Pay now</Link></>}>
        <p className="fs-13 mb-2">{b.message}</p>
        <div className="d-flex justify-content-between fs-13 border-top pt-2"><span className="text-muted">Amount due</span><strong>{b.currency} {money(b.amount_due, 0)}</strong></div>
        <div className="d-flex justify-content-between fs-13"><span className="text-muted">{b.level === "overdue" ? "Access stops after" : "Due on"}</span><strong>{date(b.level === "overdue" ? b.grace_until : b.end_date)}</strong></div>
      </Modal>
    </>
  );
}

/** What a suspended company sees instead of the app: the owner can pay, everyone else is told who can. */
export function SuspendedScreen({ me }: { me: any }) {
  const qc = useQueryClient();
  const owner = Boolean(me?.is_owner);
  const q = useQuery({ queryKey: ["subscription"], queryFn: () => api.get("/api/v1/subscription"), enabled: owner });
  const signOut = () => { useAuth.getState().logout(); window.location.assign("/login"); };
  return (
    <div className="min-vh-100 bg-light py-5 px-3">
      <div className="mx-auto" style={{ maxWidth: 980 }}>
        <div className="card"><div className="card-body p-4">
          <div className="d-flex align-items-start gap-3 mb-3">
            <i className="ti ti-lock-pause text-danger" style={{ fontSize: 36 }} />
            <div className="flex-grow-1">
              <h4 className="mb-1">{me?.tenant?.display_name} is suspended</h4>
              <p className="text-muted mb-0">{owner ? (me?.billing?.message || "The subscription has not been paid.") : "The company's subscription has not been paid, so nobody can use the app until it is. Ask your company's owner to renew it."}</p>
            </div>
            <button className="btn btn-outline-secondary" onClick={signOut}><i className="ti ti-logout me-1" />Sign out</button>
          </div>
          {owner && (q.data ? <>
            <div className="row g-3 mb-3">
              <div className="col-md-4"><div className="border rounded p-3 h-100"><div className="text-muted fs-12">Amount due</div><div className="fs-18 fw-bold">{me?.billing?.currency} {money(Number(q.data.balance) > 0 ? q.data.balance : me?.billing?.amount_due, 0)}</div></div></div>
              <div className="col-md-4"><div className="border rounded p-3 h-100"><div className="text-muted fs-12">Plan</div><div className="fs-15 fw-semibold">{q.data.subscription?.plan_name}</div><div className="fs-12 text-muted">expired {date(q.data.subscription?.end_date)}</div></div></div>
              <div className="col-md-4"><div className="border rounded p-3 h-100"><div className="text-muted fs-12">After payment</div><div className="fs-13">A platform admin checks it and the company reopens for everyone.</div></div></div>
            </div>
            <PayPanel data={q.data} onDone={() => { qc.invalidateQueries({ queryKey: ["subscription"] }); qc.invalidateQueries({ queryKey: ["me"] }); }} />
            {(q.data.payments || []).length > 0 && <><h6 className="fs-13 mt-4">Your payments</h6><PaymentsTable payments={q.data.payments} /></>}
          </> : <div className="text-muted fs-13">Loading…</div>)}
        </div></div>
      </div>
    </div>
  );
}
