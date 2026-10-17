import { DateInput } from "@/components/ui/DateInput";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { date, datetime, money, qty } from "@/lib/format";
import { useAction, useDoc, useOptions, useWarehouses } from "@/lib/hooks";
import { usePage, usePerms } from "@/lib/perms";
import { toast } from "@/lib/toast";
import { Alert, Field, InfoItem, PageHeader, Spinner, StatCard, StatusBadge, SaveError } from "@/components/ui/Bits";
import { Column } from "@/components/ui/DataTable";
import { EntitySelect } from "@/components/ui/EntitySelect";
import { ListPage } from "@/components/ui/ListPage";
import { ConfirmDialog, Modal } from "@/components/ui/Modal";
import { DocTabs } from "@/components/documents/DocTabs";
import { EditLine, LinesEditor, newLine } from "@/components/documents/LinesEditor";
import { WorkflowStepper } from "@/components/documents/WorkflowStepper";

const STATUS_OPTIONS = ["draft", "submitted", "under_review", "confirmed", "on_hold_credit", "on_hold_stock", "dispatched", "partially_delivered", "delivered", "invoiced", "cancelled", "rejected"].map((s) => ({ value: s, label: s.replace(/_/g, " ") }));

const orderColumns: Column<any>[] = [
  { key: "number", header: "Order #", sortKey: "number", render: (o) => <><span className="fw-semibold text-dark">{o.number}</span>{o.external_ref && <div className="text-muted fs-11">{o.external_ref}</div>}</> },
  { key: "order_date", header: "Date", sortKey: "order_date", render: (o) => date(o.order_date) },
  { key: "customer_name", header: "Customer", render: (o) => <><div>{o.customer_name}</div><div className="text-muted fs-11">{o.customer_code}</div></> },
  { key: "sales_person", header: "Sales person", render: (o) => o.sales_person || o.rep_name || <span className="text-muted">—</span> },
  { key: "warehouse_name", header: "Warehouse" },
  { key: "trip_number", header: "Trip", render: (o) => o.trip_number || "—" },
  { key: "total", header: "Total", num: true, sortKey: "total", render: (o) => <span className="fw-semibold">{money(o.total)}</span> },
  { key: "status", header: "Status", sortKey: "status", render: (o) => <><StatusBadge status={o.status} />{o.delivery_status && <div className="fs-11 text-muted mt-1"><i className="ti ti-truck me-1" />{o.delivery_status}</div>}{o.hold_reason && <div className="text-warning fs-11 mt-1" style={{ maxWidth: 220 }}>{o.hold_reason}</div>}</> },
];

export function OrdersPage() {
  const stats = useQuery({ queryKey: ["orders-stats"], queryFn: () => api.get("/api/v1/sales-orders/stats") });
  return (
    <ListPage title="Sales orders" endpoint="/api/v1/sales-orders" columns={orderColumns} rowLink={(o) => `/app/sales/orders/${o.id}`} page="sales_orders" createTo="/app/sales/orders/new" createLabel="New order" defaultOrdering="-order_date" searchPlaceholder="Search number, customer, external ref…"
      filters={[{ name: "status", label: "Status", type: "select", options: STATUS_OPTIONS }, { name: "customer", label: "Customer", type: "entity", endpoint: "/api/v1/customers", mapOption: (c) => ({ id: c.id, label: c.name, sub: c.code }) }, { name: "rep", label: "Rep", type: "entity", endpoint: "/api/v1/supplier-reps", mapOption: (r) => ({ id: r.id, label: r.name, sub: r.supplier_name }) }, { name: "date_from", label: "From", type: "date" }, { name: "date_to", label: "To", type: "date" }]}
      stats={stats.data && <div className="row g-3 mb-3"><div className="col-6 col-md-3"><StatCard label="Inbox" value={stats.data.inbox} icon="ti-inbox" color="warning" to="/app/sales/inbox" /></div><div className="col-6 col-md-3"><StatCard label="Confirmed (to dispatch)" value={stats.data.by_status.confirmed || 0} icon="ti-circle-check" color="primary" /></div><div className="col-6 col-md-3"><StatCard label="Dispatched" value={stats.data.dispatched} icon="ti-truck" color="purple" /></div><div className="col-6 col-md-3"><StatCard label="Today" value={stats.data.today} icon="ti-calendar" color="success" sub={money(stats.data.today_value)} /></div></div>} />
  );
}

export function OrdersInboxPage() {
  return (
    <ListPage title="Orders inbox" crumbs={[{ label: "Sales orders", to: "/app/sales/orders" }, { label: "Inbox" }]} endpoint="/api/v1/sales-orders/inbox" columns={orderColumns} rowLink={(o) => `/app/sales/orders/${o.id}`} defaultOrdering="created_at" searchPlaceholder="Search…"
      filters={[{ name: "status", label: "Status", type: "select", options: ["submitted", "under_review", "on_hold_credit", "on_hold_stock"].map((s) => ({ value: s, label: s.replace(/_/g, " ") })) }, { name: "rep", label: "Rep", type: "entity", endpoint: "/api/v1/supplier-reps", mapOption: (r) => ({ id: r.id, label: r.name }) }]}>
      <Alert kind="info">Orders submitted by manufacturer reps, the external API or staff land here. Review, adjust prices/quantities if allowed, then confirm, hold or reject. The rep is notified on every change.</Alert>
    </ListPage>
  );
}

export function OrderDetailPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { can, isOwner } = usePerms();
  const page = usePage("sales_orders");
  const notesPage = usePage("delivery_notes");
  const tripsPage = usePage("trips");
  const q = useDoc("/api/v1/sales-orders", id);
  const { run, busy } = useAction("/api/v1/sales-orders", id, [["orders-stats"], ["orders-stats-badge"], ["dashboard"]]);
  const [dlg, setDlg] = useState<null | "reject" | "cancel" | "hold" | "deliver" | "confirm" | "delete">(null);
  const [deliver, setDeliver] = useState({ receiver_name: "", cash_collected: "" });
  const avail = useQuery({ queryKey: ["availability", id], queryFn: () => api.get(`/api/v1/sales-orders/${id}/availability`), enabled: Boolean(q.data && ["submitted", "under_review", "on_hold_stock", "on_hold_credit", "confirmed"].includes(q.data.status)) });
  if (q.isLoading) return <Spinner />;
  const o = q.data;
  if (!o) return null;
  const editable = ["draft", "submitted", "under_review", "on_hold_credit", "on_hold_stock"].includes(o.status);
  const confirmable = ["submitted", "under_review", "on_hold_credit", "on_hold_stock", "draft"].includes(o.status);
  return (
    <>
      <PageHeader title={o.number} badge={<><StatusBadge status={o.status} />{o.delivery_status && <span className="badge bg-purple-light text-purple ms-1"><i className="ti ti-truck me-1" />{o.delivery_status}</span>}</>} crumbs={[{ label: "Sales orders", to: "/app/sales/orders" }, { label: o.number }]}
        actions={<>
          {editable && page.edit && <Link to={`/app/sales/orders/${o.id}/edit`} className="btn btn-outline-primary"><i className="ti ti-edit me-1" />Edit</Link>}
          {o.status === "draft" && page.add && <button className="btn btn-primary" disabled={!!busy} onClick={() => run("submit")}>Submit</button>}
          {o.status === "submitted" && page.can("confirm") && <button className="btn btn-outline-info" disabled={!!busy} onClick={() => run("review")}>Mark under review</button>}
          {confirmable && page.can("confirm") && <button className="btn btn-success" disabled={!!busy} onClick={() => (o.status === "on_hold_credit" ? setDlg("confirm") : run("confirm", {}, { success: "Order confirmed" }))}><i className="ti ti-circle-check me-1" />Confirm</button>}
          {confirmable && page.can("confirm") && <button className="btn btn-outline-warning" disabled={!!busy} onClick={() => setDlg("hold")}>Hold</button>}
          {confirmable && page.can("hold") && <button className="btn btn-outline-danger" disabled={!!busy} onClick={() => setDlg("reject")}>Reject</button>}
          {["confirmed", "dispatched", "partially_delivered"].includes(o.status) && notesPage.can("complete") && <button className="btn btn-primary" onClick={() => setDlg("deliver")}><i className="ti ti-package-export me-1" />Mark delivered</button>}
          {o.status === "confirmed" && !o.trip && tripsPage.add && <Link to={`/app/delivery/trips/new?order=${o.id}&warehouse=${o.warehouse}`} className="btn btn-outline-primary"><i className="ti ti-truck me-1" />Add to trip</Link>}
          {o.trip && <Link to={`/app/delivery/trips/${o.trip}`} className="btn btn-outline-secondary"><i className="ti ti-truck me-1" />View trip {o.trip_number}</Link>}
          {["draft", "submitted", "under_review", "confirmed", "on_hold_credit", "on_hold_stock"].includes(o.status) && page.delete && <button className="btn btn-outline-danger" disabled={!!busy} onClick={() => setDlg("cancel")}>Cancel</button>}{["draft", "submitted", "under_review", "rejected"].includes(o.status) && page.delete && <button className="btn btn-outline-danger" disabled={!!busy} onClick={() => setDlg("delete")}><i className="ti ti-trash me-1" />Delete</button>}
          <button className="btn btn-outline-secondary" onClick={() => api.printDoc(`/api/v1/sales-orders/${o.id}/pdf`)}><i className="ti ti-printer me-1" />Print</button>
          <button className="btn btn-outline-secondary" onClick={() => api.download(`/api/v1/sales-orders/${o.id}/pdf`)}><i className="ti ti-file-type-pdf me-1" />PDF</button>
        </>} />
      {o.hold_reason && <Alert kind="warning">{o.hold_reason}</Alert>}
      {o.rejection_reason && <Alert kind="danger">{o.rejection_reason}</Alert>}
      {!["draft", "cancelled", "rejected"].includes(o.status) && <WorkflowStepper orderId={o.id} />}
      {o.price_flags?.length > 0 && <Alert kind="warning">Price differences flagged: {o.price_flags.map((f: any) => `${f.variant} requested ${money(f.requested)} vs list ${money(f.list)}`).join("; ")}</Alert>}
      {avail.data && !avail.data.ok && <Alert kind="warning">Stock short: {avail.data.rows.filter((r: any) => Number(r.short) > 0).map((r: any) => `${r.sku} short ${qty(r.short)}`).join(", ")}</Alert>}
      <div className="row g-3">
        <div className="col-xl-9">
          <div className="card mb-3"><div className="card-header"><strong>Order details</strong></div>
            <div className="card-body">
              <div className="row g-3 mb-3">
                <InfoItem label="Customer"><Link to={`/app/customers/${o.customer}`}>{o.customer_name}</Link><div className="text-muted fs-12">{o.customer_phone} · {o.customer_address}</div></InfoItem>
                <InfoItem label="Sales person / supplier" col="col-md-2">{o.sales_person || o.rep_name || "—"}<div className="text-muted fs-12">{o.supplier_name}</div></InfoItem>
                <InfoItem label="Dates" col="col-md-2">{date(o.order_date)}<div className="text-muted fs-12">deliver by {date(o.requested_delivery_date)}</div></InfoItem>
                <InfoItem label="Warehouse" col="col-md-2">{o.warehouse_name}</InfoItem><InfoItem label="Payment term" col="col-md-2">{o.payment_terms || "—"}</InfoItem><InfoItem label="Delivery person" col="col-md-2">{o.delivery_person || "—"}</InfoItem>
                <InfoItem label="Trip" col="col-md-3">{o.trip_number ? <Link to={`/app/delivery/trips/${o.trip}`}>{o.trip_number}</Link> : "—"}{o.driver_name && <div className="text-muted fs-12">{o.driver_name} · {o.vehicle_reg}</div>}</InfoItem>
              </div>
              <div className="table-responsive">
                <table className="table table-sm align-middle mb-0 fs-12" style={{ minWidth: 760 }}>
                  <thead className="table-light"><tr><th>#</th><th>Item</th><th className="num">Qty</th><th className="num">Disp.</th><th className="num">Deliv.</th><th className="num">Price</th><th className="num">Disc</th><th className="num">Total</th></tr></thead>
                  <tbody>{o.lines.map((l: any, i: number) => <tr key={l.id}><td>{i + 1}</td><td>{l.variant_name}<div className="text-muted fs-11">{l.variant_sku}{l.packaging_name ? ` · ${l.packaging_name}` : ""}{l.scheme_names?.length ? ` · ${l.scheme_names.join(", ")}` : ""}{Number(l.qty_allocated) > 0 ? ` · reserved ${qty(l.qty_allocated)}` : ""}</div></td><td className="num">{qty(l.qty_ordered)}</td><td className="num">{qty(l.qty_dispatched)}</td><td className="num">{qty(l.qty_delivered)}</td><td className="num">{money(l.unit_price)}{l.requested_price && Number(l.requested_price) !== Number(l.unit_price) && <div className="text-warning fs-11">req {money(l.requested_price)}</div>}</td><td className="num">{money(Number(l.discount_amount) + Number(l.scheme_discount))}</td><td className="num fw-medium">{money(l.line_total)}</td></tr>)}</tbody>
                  <tfoot>{Number(o.discount) > 0 && <tr><td colSpan={7} className="text-end">Discount</td><td className="num">-{money(o.discount)}</td></tr>}{Number(o.scheme_discount) > 0 && <tr><td colSpan={7} className="text-end">Scheme discount</td><td className="num">-{money(o.scheme_discount)}</td></tr>}<tr className="fw-bold"><td colSpan={7} className="text-end">Total ({o.currency})</td><td className="num">{money(o.total)}</td></tr></tfoot>
                </table>
              </div>
              {o.notes && <p className="text-muted fs-13 mt-2 mb-0"><i className="ti ti-note me-1" />{o.notes}</p>}
            </div></div>
          <DocTabs model="SalesOrder" id={o.id} showMovements extra={[{ key: "deliveries", label: "Deliveries & invoices", render: () => (
            <div className="row g-3"><div className="col-md-6"><h6 className="fs-13">Delivery notes</h6>{!o.delivery_notes.length && <p className="text-muted fs-13">None yet</p>}<ul className="list-group">{o.delivery_notes.map((d: any) => <li key={d.id} className="list-group-item d-flex justify-content-between"><Link to={`/app/delivery/notes/${d.id}`}>{d.number}</Link><span className="fs-12 text-muted">{d.trip || "no trip"} · {datetime(d.delivered_at)}</span><StatusBadge status={d.status} /></li>)}</ul></div>
              <div className="col-md-6"><h6 className="fs-13">Invoices</h6>{!o.invoices.length && <p className="text-muted fs-13">None yet</p>}<ul className="list-group">{o.invoices.map((i: any) => <li key={i.id} className="list-group-item d-flex justify-content-between"><Link to={`/app/sales/invoices/${i.id}`}>{i.number}</Link><span className="fs-12">{money(i.total)} · paid {money(i.paid_amount)}</span><StatusBadge status={i.status} /></li>)}</ul></div></div>) }]} />
        </div>
        <div className="col-xl-3">
          <div className="card mb-3"><div className="card-header"><strong>Timeline</strong></div><div className="card-body">
            <ul className="status-timeline mb-0">
              <li><div className="fs-13 fw-medium">Created</div><div className="fs-12 text-muted">{datetime(o.created_at)} · {o.created_by_name || o.source}</div></li>
              {o.confirmed_at && <li><div className="fs-13 fw-medium">Confirmed</div><div className="fs-12 text-muted">{datetime(o.confirmed_at)} · {o.confirmed_by_name}</div></li>}
              {o.dispatched_at && <li><div className="fs-13 fw-medium">Dispatched (stock out)</div><div className="fs-12 text-muted">{datetime(o.dispatched_at)}</div></li>}
              {o.delivered_at && <li><div className="fs-13 fw-medium">Delivered</div><div className="fs-12 text-muted">{datetime(o.delivered_at)}</div></li>}
            </ul>
          </div></div>
          {avail.data && <div className="card"><div className="card-header"><strong>Availability</strong> <span className={`badge ms-2 ${avail.data.ok ? "bg-success" : "bg-warning"}`}>{avail.data.ok ? "in stock" : "short"}</span></div><div className="card-body p-0"><table className="table table-sm fs-12 mb-0"><tbody>{avail.data.rows.map((r: any) => <tr key={r.line_id}><td>{r.sku}</td><td className="num">{qty(r.ordered)}</td><td className={`num ${Number(r.short) > 0 ? "text-danger" : "text-success"}`}>{qty(r.available)}</td></tr>)}</tbody></table></div></div>}
        </div>
      </div>
      <ConfirmDialog open={dlg === "reject"} title="Reject order" withReason danger confirmText="Reject" onClose={() => setDlg(null)} onConfirm={async (r) => { await run("reject", { reason: r }); setDlg(null); }} />
      <ConfirmDialog open={dlg === "delete"} title={`Delete ${o.number}?`} danger confirmText="Delete order" message="This removes the order completely. Only an order that has not been confirmed, delivered or invoiced can be deleted — otherwise cancel it instead." onClose={() => setDlg(null)} onConfirm={async () => {
        try { await api.delete(`/api/v1/sales-orders/${o.id}`); toast.success(`${o.number} deleted`); qc.invalidateQueries({ queryKey: ["list", "/api/v1/sales-orders"] }); nav("/app/sales/orders"); }
        catch (e: any) { toast.error(e.message); }
      }} />
      <ConfirmDialog open={dlg === "cancel"} title="Cancel order" withReason danger confirmText="Cancel order" onClose={() => setDlg(null)} onConfirm={async (r) => { await run("cancel", { reason: r }); setDlg(null); }} />
      <ConfirmDialog open={dlg === "hold"} title="Put on hold" withReason confirmText="Hold" onClose={() => setDlg(null)} onConfirm={async (r) => { await run("hold", { reason: r }); setDlg(null); }} />
      <ConfirmDialog open={dlg === "confirm"} title="Override credit hold?" message={<>{o.hold_reason}<br />Confirming will override the credit check.</>} confirmText="Override & confirm" onClose={() => setDlg(null)} onConfirm={async () => { await run("confirm", { override_credit: true }); setDlg(null); }} />
      <Modal open={dlg === "deliver"} title={`Mark ${o.number} delivered`} onClose={() => setDlg(null)} size="sm" footer={<><button className="btn btn-light" onClick={() => setDlg(null)}>Cancel</button><button className="btn btn-primary" disabled={!!busy} onClick={async () => { await run("mark-delivered", { receiver_name: deliver.receiver_name, cash_collected: deliver.cash_collected || 0 }, { success: "Delivered and invoiced" }); setDlg(null); }}>Mark delivered</button></>}>
        <p className="fs-13 text-muted">All remaining quantities will be delivered now without a trip: stock leaves the warehouse (if not already dispatched), a delivery note and an invoice are created.</p>
        <Field label="Receiver name"><input className="form-control" value={deliver.receiver_name} onChange={(e) => setDeliver({ ...deliver, receiver_name: e.target.value })} /></Field>
        <Field label="Cash collected now" hint="Creates a receipt allocated to the new invoice"><input type="number" step="0.01" className="form-control" value={deliver.cash_collected} onChange={(e) => setDeliver({ ...deliver, cash_collected: e.target.value })} /></Field>
      </Modal>
    </>
  );
}


/** The order's sale person comes from the customer's linked rep; it is changed on the customer, not per order. */
function SalePersonAuto({ customer, editingRep }: { customer: string | null; editingRep?: string | null }) {
  const c = useDoc("/api/v1/customers", customer || undefined);
  const name = c.data?.rep_names?.[0];
  return (
    <div className="form-control-plaintext fs-13">
      {name ? <span className="fw-medium">{name}</span> : customer ? <span className="text-muted">none linked{" "}{c.data && <Link to={`/app/customers/${customer}/edit`} className="fs-12">set on customer</Link>}</span> : <span className="text-muted">pick a customer</span>}
    </div>
  );
}

export function OrderFormPage() {
  const [err, setErr] = useState<string | null>(null);
  const { id } = useParams();
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const { can } = usePerms();
  const page = usePage("sales_orders");
  const existing = useDoc("/api/v1/sales-orders", id);
  const warehouses = useWarehouses();
  const [form, setForm] = useState<any>(null);
  const [lines, setLines] = useState<EditLine[]>([]);
  // Most orders have no discount on the line, so the column is out of the way until somebody asks for it —
  // and it shows itself when an order being edited already carries one.
  const [showDiscount, setShowDiscount] = useState(false);
  const [saving, setSaving] = useState(false);
  if (id && existing.isLoading) return <Spinner />;
  if (!form) {
    const o = existing.data;
    setForm(o ? { customer: o.customer, warehouse: o.warehouse, rep: o.rep, order_date: o.order_date, requested_delivery_date: o.requested_delivery_date || "", notes: o.notes || "", version: o.version, apply_customer_discount: o.apply_customer_discount } : { customer: sp.get("customer") || null, warehouse: "", rep: null, order_date: new Date().toISOString().slice(0, 10), requested_delivery_date: "", notes: "", apply_customer_discount: false });
    if (o) setLines(o.lines.map((l: any) => ({ key: l.id, variant: l.variant, variant_name: l.variant_name, sku: l.variant_sku, packaging: l.packaging, packagings: [], qty: String(l.packaging ? Number(l.qty_ordered) / 1 : Number(l.qty_ordered)), unit_price: l.unit_price, discount_pct: l.discount_pct })));
    return <Spinner />;
  }
  const submit = async (confirm: boolean) => {
    if (!form.customer) return toast.error("Select a customer");
    const valid = lines.filter((l) => l.variant && Number(l.qty) > 0);
    if (!valid.length) return toast.error("Add at least one line");
    setSaving(true);
    try {
      const body = { ...form, warehouse: form.warehouse || null, confirm, lines: valid.map((l) => ({ variant: l.variant, packaging: l.packaging || null, qty: l.qty, unit_price: l.unit_price || null, discount_pct: l.discount_pct || 0, product_discount_pct: l.product_discount_pct || 0, price_per_packaging: Boolean(l.packaging) })) };
      const r = id ? await api.patch(`/api/v1/sales-orders/${id}`, body) : await api.post("/api/v1/sales-orders", body, true);
      toast.success(id ? "Order updated" : "Order created");
      nav(`/app/sales/orders/${r.id}`);
    } catch (e: any) { setErr(e.message); toast.error(e.message); } finally { setSaving(false); }
  };
  return (
    <>
      <PageHeader title={id ? `Edit ${existing.data?.number}` : "New sales order"} crumbs={[{ label: "Sales orders", to: "/app/sales/orders" }, { label: id ? "Edit" : "New" }]} />
      <SaveError error={err} what="order" />
      <div className="card mb-3"><div className="card-body">
        <div className="row">
          <Field label="Customer *" col="col-md-4"><EntitySelect endpoint="/api/v1/customers" query={{ status: "active" }} value={form.customer} onChange={(v) => setForm({ ...form, customer: v })} mapOption={(c) => ({ id: c.id, label: c.name, sub: `${c.code} · ${c.city || ""} · outstanding ${money(c.outstanding)}`, raw: c })} /></Field>
          <Field label="Warehouse" col="col-md-2"><select className="form-select" value={form.warehouse || warehouses.defaultId || ""} onChange={(e) => setForm({ ...form, warehouse: e.target.value })}>{!warehouses.rows.length && <option value="">—</option>}{warehouses.rows.map((w: any) => <option key={w.id} value={w.id}>{w.name}{w.is_default ? " (default)" : ""}</option>)}</select></Field>
          <Field label="Sale person" col="col-md-2" hint="linked on the customer — change it there"><SalePersonAuto customer={form.customer} editingRep={form.rep} /></Field>
          <Field label="Order date" col="col-md-2"><DateInput className="form-control" value={form.order_date} onChange={(e) => setForm({ ...form, order_date: e.target.value })} /></Field>
          <Field label="Requested delivery" col="col-md-2"><DateInput className="form-control" value={form.requested_delivery_date} onChange={(e) => setForm({ ...form, requested_delivery_date: e.target.value })} /></Field>
        </div>
        <LinesEditor lines={lines} onChange={setLines} customerId={form.customer} showDiscount={page.can("override_discount") && (showDiscount || lines.some((l) => Number(l.discount_pct || 0) || Number((l as any).discount_amount || 0)))} onToggleDiscount={page.can("override_discount") ? () => setShowDiscount((v) => !v) : undefined} discountShown={showDiscount} variantQuery={{ with_stock: 1 }} />
        <Field label="Customer discount" col="col-md-4" hint="the customer's standing discount is applied to lines with no discount of their own">
          <div className="form-check form-switch mt-2"><input className="form-check-input" type="checkbox" id="ocd" checked={Boolean(form.apply_customer_discount)} onChange={(e) => setForm({ ...form, apply_customer_discount: e.target.checked })} /><label className="form-check-label fs-13" htmlFor="ocd">{form.apply_customer_discount === false ? "Not applied on this order" : "Apply on this order"}</label></div>
        </Field>
        <Field label="Notes"><textarea className="form-control" rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
        {!page.can("override_price") && <div className="form-text">Prices you enter are advisory — the price list applies unless you have the price-override permission.</div>}
      </div></div>
      <div className="d-flex gap-2 justify-content-end">
        <button className="btn btn-light" onClick={() => nav(-1)}>Cancel</button>
        <button className="btn btn-outline-primary" disabled={saving} onClick={() => submit(false)}>{id ? "Save changes" : "Save & submit"}</button>
        {!id && page.can("confirm") && <button className="btn btn-primary" disabled={saving} onClick={() => submit(true)}>Save & confirm</button>}
      </div>
    </>
  );
}
