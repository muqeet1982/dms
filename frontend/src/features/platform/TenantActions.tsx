import { DateInput } from "@/components/ui/DateInput";
import { ReactNode, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useAuth } from "@/lib/auth";
import { money } from "@/lib/format";
import { toast } from "@/lib/toast";
import { Field } from "@/components/ui/Bits";
import { ConfirmDialog, Modal } from "@/components/ui/Modal";

const P = "/api/v1/platform";

type Action = { key: string; label: string; icon: string; danger?: boolean; hidden?: boolean; run: () => void };

/**
 * Everything an admin does to a company, available from the list row as well as the detail page — the actions
 * offered depend on the tenant's state (you cannot renew a cancelled company, only reactivate a suspended one).
 */
export function TenantRowActions({ tenant, onDone, align = "end" }: { tenant: any; onDone?: () => void; align?: "start" | "end" }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const { setTenant } = useAuth();
  const [open, setOpen] = useState(false);
  const [dialog, setDialog] = useState<null | "suspend" | "reactivate" | "cancel" | "renew" | "discount" | "payment" | "message" | "reset">(null);
  const box = useRef<HTMLDivElement | null>(null);
  const btn = useRef<HTMLButtonElement | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    const close = () => setOpen(false);
    document.addEventListener("mousedown", h);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    return () => { document.removeEventListener("mousedown", h); window.removeEventListener("resize", close); window.removeEventListener("scroll", close, true); };
  }, []);
  // the menu is rendered into <body> at the button's position: a table's own scroll container would clip it
  const place = () => {
    const r = btn.current?.getBoundingClientRect();
    if (!r) return;
    const height = 44 * actionsCount + 16;
    const flip = r.bottom + height > window.innerHeight - 8;
    setPos({ top: flip ? Math.max(8, r.top - height) : r.bottom + 4, left: Math.min(r.right - 230, window.innerWidth - 240) });
  };
  const refresh = () => { qc.invalidateQueries({ queryKey: ["list"] }); qc.invalidateQueries({ queryKey: ["doc"] }); onDone?.(); };
  const sub = tenant.subscription;
  const call = async (path: string, body: any = {}, ok = "Done") => {
    try { await api.post(path, body); toast.success(ok); refresh(); } catch (e: any) { toast.error(e.message); }
  };
  const impersonate = async () => {
    try {
      const r = await api.post(`${P}/tenants/${tenant.id}/impersonate`, { reason: "support" });
      setTenant(r.tenant_id); qc.clear(); nav("/app");
      toast.warning(`You are now working inside ${tenant.display_name} — use "Return to platform admin" when done.`);
    } catch (e: any) { toast.error(e.message); }
  };
  const live = !["cancelled", "archived"].includes(tenant.status);
  const actionsCount = 8 + (tenant.impersonation_allowed && live ? 1 : 0) + (sub && live ? 3 : 0) + (tenant.status === "suspended" ? 1 : 0);
  const actions: Action[] = [
    { key: "open", label: "Open company", icon: "ti-external-link", run: () => nav(`/admin/tenants/${tenant.id}`) },
    { key: "impersonate", label: "Log in as owner", icon: "ti-user-screen", hidden: !tenant.impersonation_allowed || !live, run: impersonate },
    { key: "renew", label: "Renew subscription", icon: "ti-refresh", hidden: !sub || !live, run: () => setDialog("renew") },
    { key: "plan", label: "Change plan", icon: "ti-stack-2", hidden: !sub || !live, run: () => nav(`/admin/tenants/${tenant.id}?tab=subscription&action=change`) },
    { key: "discount", label: sub?.discount_active ? "Edit discount" : "Give a discount", icon: "ti-discount-2", hidden: !sub || !live, run: () => setDialog("discount") },
    { key: "payment", label: "Record payment", icon: "ti-cash", hidden: !live, run: () => setDialog("payment") },
    { key: "features", label: "Features & white-label", icon: "ti-toggle-right", run: () => nav(`/admin/tenants/${tenant.id}?tab=features`) },
    { key: "export", label: "Export company data", icon: "ti-download", run: () => api.download(`${P}/tenants/${tenant.id}/export`, `${tenant.slug}-export.json`) },
    { key: "suspend", label: "Suspend", icon: "ti-player-pause", danger: true, hidden: tenant.status === "suspended" || !live, run: () => setDialog("suspend") },
    { key: "reactivate", label: "Reactivate", icon: "ti-player-play", hidden: tenant.status !== "suspended", run: () => setDialog("reactivate") },
    { key: "cancel", label: "Cancel subscription", icon: "ti-circle-x", danger: true, hidden: !live, run: () => setDialog("cancel") },
    { key: "reset", label: "Reset data", icon: "ti-eraser", danger: true, run: () => setDialog("reset") },
  ].filter((a) => !a.hidden);

  return (
    <div className="no-row-click d-inline-block" ref={box}>
      <button ref={btn} className="btn btn-sm btn-outline-secondary" onClick={(e) => { e.stopPropagation(); if (!open) place(); setOpen((o) => !o); }} title="Actions">
        <i className="ti ti-dots-vertical" />
      </button>
      {open && pos && createPortal(
        <div className="card shadow" style={{ position: "fixed", top: pos.top, left: pos.left, zIndex: 1080, minWidth: 230 }} onMouseDown={(e) => e.stopPropagation()}>
          <div className="card-body p-1">
            {actions.map((a) => (
              <button key={a.key} className={`dropdown-item d-flex align-items-center gap-2 px-2 py-2 rounded fs-13 ${a.danger ? "text-danger" : ""}`} onClick={() => { setOpen(false); a.run(); }}>
                <i className={`ti ${a.icon}`} />{a.label}
              </button>
            ))}
          </div>
        </div>,
        document.body,
      )}

      <ConfirmDialog open={dialog === "suspend"} title={`Suspend ${tenant.display_name}`} withReason danger confirmText="Suspend"
        message="Users cannot sign in while the company is suspended. Data is kept."
        onClose={() => setDialog(null)} onConfirm={async (r) => { await call(`${P}/tenants/${tenant.id}/suspend`, { reason: r }, "Suspended"); setDialog(null); }} />
      <ConfirmDialog open={dialog === "reactivate"} title={`Reactivate ${tenant.display_name}`} withReason confirmText="Reactivate"
        onClose={() => setDialog(null)} onConfirm={async (r) => { await call(`${P}/tenants/${tenant.id}/reactivate`, { reason: r }, "Reactivated"); setDialog(null); }} />
      <ConfirmDialog open={dialog === "cancel"} title={`Cancel ${tenant.display_name}`} withReason danger confirmText="Cancel subscription"
        message="The subscription stops at the end of the current period and the company becomes read-only."
        onClose={() => setDialog(null)} onConfirm={async (r) => { await call(`${P}/tenants/${tenant.id}/cancel`, { reason: r }, "Cancelled"); setDialog(null); }} />
      {dialog === "renew" && sub && <RenewDialog sub={sub} onClose={() => setDialog(null)} onDone={refresh} />}
      {dialog === "discount" && sub && <DiscountDialog sub={sub} onClose={() => setDialog(null)} onDone={refresh} />}
      {dialog === "payment" && <PaymentDialog tenant={tenant} onClose={() => setDialog(null)} onDone={refresh} />}
      {dialog === "reset" && <ResetDataDialog tenant={tenant} onClose={() => setDialog(null)} onDone={refresh} />}
    </div>
  );
}

/** Percentage or fixed-amount discount for this company only, with how long it runs. */
export function DiscountDialog({ sub, onClose, onDone }: { sub: any; onClose: () => void; onDone?: () => void }) {
  const [f, setF] = useState<any>({
    type: sub.discount_type || "percent", value: sub.discount_value && Number(sub.discount_value) ? String(Number(sub.discount_value)) : "",
    cycles: sub.discount_cycles || 0, until: sub.discount_until || "", reason: sub.discount_reason || "",
  });
  const [busy, setBusy] = useState(false);
  const price = Number(sub.price_breakdown?.subtotal ?? sub.plan_snapshot?.price ?? 0);
  const cut = f.type === "percent" ? (price * Number(f.value || 0)) / 100 : Number(f.value || 0);
  const after = Math.max(0, price - cut);
  const save = async (remove = false) => {
    setBusy(true);
    try {
      if (remove) await api.delete(`${P}/subscriptions/${sub.id}/discount`);
      else await api.post(`${P}/subscriptions/${sub.id}/discount`, { type: f.type, value: Number(f.value || 0), cycles: Number(f.cycles || 0), until: f.until || null, reason: f.reason });
      toast.success(remove ? "Discount removed" : "Discount applied");
      onDone?.(); onClose();
    } catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
  };
  return (
    <Modal open onClose={onClose} title={<><i className="ti ti-discount-2 me-2 text-primary" />Discount for {sub.tenant_name || "this company"}</>} size="md"
      footer={<>
        {sub.discount_active && <button className="btn btn-outline-danger me-auto" disabled={busy} onClick={() => save(true)}>Remove discount</button>}
        <button className="btn btn-light" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={busy || !Number(f.value)} onClick={() => save()}>{busy ? "Saving…" : "Apply discount"}</button>
      </>}>
      <div className="row">
        <Field label="Type" col="col-md-4">
          <div className="btn-group w-100">
            <button className={`btn ${f.type === "percent" ? "btn-primary" : "btn-outline-secondary"}`} onClick={() => setF({ ...f, type: "percent" })}>Percent %</button>
            <button className={`btn ${f.type === "fixed" ? "btn-primary" : "btn-outline-secondary"}`} onClick={() => setF({ ...f, type: "fixed" })}>Fixed amount</button>
          </div>
        </Field>
        <Field label={f.type === "percent" ? "Percentage off" : `Amount off (${sub.plan_snapshot?.currency || "PKR"})`} col="col-md-3">
          <div className="input-group"><input type="number" className="form-control" autoFocus value={f.value} onChange={(e) => setF({ ...f, value: e.target.value })} />{f.type === "percent" && <span className="input-group-text">%</span>}</div>
        </Field>
        <Field label="Applies to" col="col-md-5" hint="how many invoices this discount covers">
          <select className="form-select" value={f.cycles} onChange={(e) => setF({ ...f, cycles: Number(e.target.value) })}>
            <option value={0}>Every invoice (until removed)</option>
            <option value={1}>The next invoice only</option>
            <option value={3}>The next 3 invoices</option>
            <option value={6}>The next 6 invoices</option>
            <option value={12}>The next 12 invoices</option>
          </select>
        </Field>
        <Field label="Stop on (optional)" col="col-md-4"><DateInput className="form-control" value={f.until || ""} onChange={(e) => setF({ ...f, until: e.target.value })} /></Field>
        <Field label="Reason (shown in the subscription history)" col="col-md-8"><input className="form-control" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} placeholder="Negotiated by sales · launch offer · goodwill" /></Field>
      </div>
      <div className="border rounded p-3 bg-light">
        <div className="d-flex justify-content-between fs-13"><span>Current recurring price</span><span>{money(price, 0)}</span></div>
        <div className="d-flex justify-content-between fs-13 text-danger"><span>Discount</span><span>− {money(cut, 0)}</span></div>
        <div className="d-flex justify-content-between fw-bold border-top pt-2 mt-2"><span>They pay</span><span>{money(after, 0)} / {sub.billing_cycle || "monthly"}</span></div>
      </div>
    </Modal>
  );
}

function RenewDialog({ sub, onClose, onDone }: { sub: any; onClose: () => void; onDone?: () => void }) {
  const [periods, setPeriods] = useState(1);
  const [pay, setPay] = useState(true);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try { const r = await api.post(`${P}/subscriptions/${sub.id}/renew`, { periods, record_payment: pay ? {} : null }); toast.success(`Renewed — invoice ${r.invoice?.number || ""}`); onDone?.(); onClose(); }
    catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
  };
  return (
    <Modal open onClose={onClose} title={<><i className="ti ti-refresh me-2 text-primary" />Renew subscription</>} size="sm"
      footer={<><button className="btn btn-light" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={busy} onClick={run}>{busy ? "Renewing…" : "Renew"}</button></>}>
      <Field label="Periods"><input type="number" min={1} className="form-control" value={periods} onChange={(e) => setPeriods(Number(e.target.value))} /></Field>
      <div className="form-check"><input className="form-check-input" type="checkbox" id="rpay" checked={pay} onChange={(e) => setPay(e.target.checked)} /><label className="form-check-label fs-13" htmlFor="rpay">Mark the invoice as paid</label></div>
      {sub.discount_active && <div className="alert alert-info py-2 fs-12 mt-3 mb-0">The company's discount ({sub.discount_label}) is applied to this invoice.</div>}
    </Modal>
  );
}

function PaymentDialog({ tenant, onClose, onDone }: { tenant: any; onClose: () => void; onDone?: () => void }) {
  const [f, setF] = useState<any>({ amount: "", method: "bank", reference: "", notes: "" });
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try { await api.post(`${P}/payments`, { tenant: tenant.id, amount: Number(f.amount || 0), method: f.method, reference: f.reference, notes: f.notes }); toast.success("Payment recorded"); onDone?.(); onClose(); }
    catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
  };
  return (
    <Modal open onClose={onClose} title={<><i className="ti ti-cash me-2 text-primary" />Record a payment from {tenant.display_name}</>} size="sm"
      footer={<><button className="btn btn-light" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={busy || !Number(f.amount)} onClick={run}>{busy ? "Saving…" : "Record payment"}</button></>}>
      <div className="row">
        <Field label="Amount" col="col-md-6"><input type="number" className="form-control" autoFocus value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
        <Field label="Method" col="col-md-6"><select className="form-select" value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })}>{["bank", "cash", "cheque", "card", "online"].map((m) => <option key={m} value={m}>{m}</option>)}</select></Field>
        <Field label="Reference" col="col-md-12"><input className="form-control" value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} placeholder="transfer id / cheque no" /></Field>
      </div>
    </Modal>
  );
}

/**
 * Clear one company's trading history and leave its setup standing — what we do when a distributor finishes
 * trying the system out, or an import lands thousands of wrong rows and they want to begin again.
 *
 * Nothing here can be undone, so the dialog does the work of talking the admin out of it: it counts the rows
 * first, spells out what goes and what stays, offers the export on the way past, and will not enable the button
 * until the company's slug has been typed by hand. A company we are currently billing needs the extra tick too.
 */
function ResetDataDialog({ tenant, onClose, onDone }: { tenant: any; onClose: () => void; onDone?: () => void }) {
  const [preview, setPreview] = useState<any>(null);
  const [previewFailed, setPreviewFailed] = useState(false);
  const [typed, setTyped] = useState("");
  const [understood, setUnderstood] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.get(`${P}/tenants/${tenant.id}/reset-data-preview`)
      .then(setPreview)
      .catch(() => setPreviewFailed(true));
  }, [tenant.id]);
  // If the count could not be fetched we cannot tell whether this company is being billed, so assume it is and
  // ask for the extra tick. Assuming the other way round left the button enabled and the server refused the
  // submission for a confirmation the dialog had never shown — which read as "reset is broken".
  const paying = preview ? preview.is_paying === true : true;
  const ready = typed === tenant.slug && (!paying || understood) && !busy;
  // A disabled red button with no reason is indistinguishable from a broken one, so say what is missing.
  const blocker = busy ? "" :
    typed !== tenant.slug ? (typed ? `That is not the company's short name — type ${tenant.slug} exactly.` : `Type ${tenant.slug} below to enable this.`)
      : paying && !understood ? "Tick the confirmation above as well — this company is being billed."
        : "";
  const run = async () => {
    if (!ready) return;
    setBusy(true);
    try {
      const r = await api.post(`${P}/tenants/${tenant.id}/reset-data`, { confirm: typed, i_understand: understood });
      toast.success(`${tenant.display_name} reset — ${Number(r.total || 0).toLocaleString()} records deleted`);
      onDone?.(); onClose();
    } catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
  };
  const rows: [string, number][] = Object.entries(preview?.deleted || {}).map(([k, v]) => [k, Number(v)]);
  rows.sort((a, b) => b[1] - a[1]);
  return (
    <Modal open onClose={onClose} title={<><i className="ti ti-eraser me-2 text-danger" />Reset {tenant.display_name}&rsquo;s data</>} size="md"
      footer={<>
        <button className="btn btn-light me-auto" onClick={() => api.download(`${P}/tenants/${tenant.id}/export`, `${tenant.slug}-export.zip`)}>
          <i className="ti ti-download me-1" />Export first
        </button>
        {blocker && <span className="fs-12 text-muted me-2 text-end" style={{ maxWidth: 320 }}>{blocker}</span>}
        <button className="btn btn-light" onClick={onClose}>Cancel</button>
        <button className="btn btn-danger" disabled={!ready} onClick={run} title={blocker}>{busy ? "Deleting…" : "Reset data"}</button>
      </>}>
      <div className="alert alert-danger py-2 fs-13">
        <i className="ti ti-alert-triangle me-1" />
        This permanently deletes everything this company has traded. It cannot be undone, and there is no restore —
        take the export first if there is any doubt.
      </div>
      <div className="row">
        <div className="col-md-6">
          <div className="fw-semibold text-danger mb-1"><i className="ti ti-trash me-1" />Deleted</div>
          <ul className="fs-13 text-muted ps-3 mb-3">
            <li>Sales orders, invoices and credit notes</li>
            <li>Payments, receipts, rep collections and settlements</li>
            <li>Deliveries, trips and pick lists</li>
            <li>Purchase orders, goods receipts, bills and returns</li>
            <li>All stock: movements, levels, batches, adjustments, counts, transfers</li>
            <li>Ledger: journal entries, expenses, opening balances</li>
            <li>Visits, location history and area events</li>
            <li>Attachments and notifications</li>
          </ul>
        </div>
        <div className="col-md-6">
          <div className="fw-semibold text-success mb-1"><i className="ti ti-lock me-1" />Kept</div>
          <ul className="fs-13 text-muted ps-3 mb-3">
            <li>The company, its users, roles and logins</li>
            <li>Warehouses and bins</li>
            <li>Products, brands, categories, units, barcodes</li>
            <li>Price lists, schemes, taxes</li>
            <li>Customers, suppliers and reps</li>
            <li>Territories, routes and working areas</li>
            <li>Chart of accounts and every setting</li>
            <li>The audit log &mdash; including this reset</li>
          </ul>
        </div>
      </div>
      <div className="border rounded p-3 bg-light mb-3">
        {previewFailed ? <div className="text-warning fs-13"><i className="ti ti-alert-triangle me-1" />The count could not be fetched, so what follows is unknown — the reset itself still works, and still deletes only trading data.</div>
          : preview === null ? <div className="text-muted fs-13">Counting what would be deleted…</div> : (
          <>
            <div className="d-flex justify-content-between fw-bold">
              <span>Records to delete</span>
              <span className="text-danger">{Number(preview.total || 0).toLocaleString()}</span>
            </div>
            <div className="d-flex justify-content-between fs-13 text-muted">
              <span>Document numbering restarts at 1</span><span>{preview.numbering_reset} sequence(s)</span>
            </div>
            {rows.length > 0 && (
              <details className="mt-2">
                <summary className="fs-13">Show the breakdown</summary>
                <div className="mt-2" style={{ maxHeight: 180, overflowY: "auto" }}>
                  {rows.map(([label, n]) => (
                    <div key={label} className="d-flex justify-content-between fs-12 text-muted">
                      <span>{label.split(".")[1].replace(/([a-z])([A-Z])/g, "$1 $2")}</span><span>{n.toLocaleString()}</span>
                    </div>
                  ))}
                </div>
              </details>
            )}
            {preview.total === 0 && <div className="fs-13 text-muted mt-1">This company has no trading data — there is nothing to delete.</div>}
          </>
        )}
      </div>
      {paying && (
        <div className="alert alert-warning py-2 fs-13">
          <div className="fw-semibold mb-1"><i className="ti ti-cash me-1" />This is a paying customer on an active subscription.</div>
          <div className="form-check">
            <input className="form-check-input" type="checkbox" id="reset-understood" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} />
            <label className="form-check-label" htmlFor="reset-understood">I mean to delete {tenant.display_name}&rsquo;s live business data.</label>
          </div>
        </div>
      )}
      <Field label={<>Type <code className="text-danger">{tenant.slug}</code> to confirm</>}>
        <input className="form-control" autoComplete="off" autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={tenant.slug} />
      </Field>
    </Modal>
  );
}

/** Small health flags for the list: what needs attention on this company right now. */
export function TenantFlags({ tenant }: { tenant: any }): ReactNode {
  const sub = tenant.subscription;
  const flags: { label: string; cls: string; title: string }[] = [];
  if (tenant.status === "suspended") flags.push({ label: "suspended", cls: "bg-danger-light text-danger", title: tenant.suspended_reason || "Suspended" });
  if (sub?.status === "trial") flags.push({ label: `trial ${sub.days_left}d`, cls: sub.days_left <= 3 ? "bg-warning-light text-warning" : "bg-info-light text-info", title: "On trial" });
  if (sub && sub.status !== "trial" && sub.days_left <= 7 && sub.days_left >= 0) flags.push({ label: `renews ${sub.days_left}d`, cls: "bg-warning-light text-warning", title: "Renewal due" });
  if (sub && sub.days_left < 0) flags.push({ label: "expired", cls: "bg-danger-light text-danger", title: "Subscription expired" });
  if (tenant.outstanding > 0) flags.push({ label: `due ${money(tenant.outstanding, 0)}`, cls: "bg-danger-light text-danger", title: "Unpaid platform invoices" });
  if (sub?.discount_active) flags.push({ label: sub.discount_label, cls: "bg-success-light text-success", title: "Discount applied" });
  if (tenant.white_label) flags.push({ label: "white-label", cls: "bg-secondary-light text-secondary", title: "Uses its own branding" });
  return <>{flags.map((f) => <span key={f.label} className={`badge ${f.cls} me-1`} title={f.title}>{f.label}</span>)}</>;
}
