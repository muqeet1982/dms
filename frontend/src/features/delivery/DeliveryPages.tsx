import { DateInput, DateTimeInput } from "@/components/ui/DateInput";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { date, datetime, labelCase, money, qty } from "@/lib/format";
import { useAction, useDoc, useOptions, useWarehouses } from "@/lib/hooks";
import { pageRights, usePerms } from "@/lib/perms";
import { toast } from "@/lib/toast";
import { useAuth } from "@/lib/auth";
import { Alert, Field, InfoItem, PageHeader, Spinner, StatCard, StatusBadge } from "@/components/ui/Bits";
import { CrudPage } from "@/components/ui/CrudPage";
import { Column } from "@/components/ui/DataTable";
import { EntitySelect } from "@/components/ui/EntitySelect";
import { ListPage } from "@/components/ui/ListPage";
import { ConfirmDialog, Modal } from "@/components/ui/Modal";
import { useOverlapGuard } from "@/components/documents/OverlapGuard";
import { DocTabs } from "@/components/documents/DocTabs";
import { WorkflowStepper } from "@/components/documents/WorkflowStepper";

export function VehiclesPage() {
  return <CrudPage page="vehicles" title="Vehicles" crumbs={[{ label: "Dispatch" }, { label: "Vehicles" }]} endpoint="/api/v1/vehicles" columns={[{ key: "reg_no", header: "Reg. no", render: (v: any) => <span className="fw-semibold text-dark">{v.reg_no}</span> }, { key: "make_model", header: "Make / model" }, { key: "type", header: "Type" }, { key: "capacity_cartons", header: "Capacity (ctn)", num: true }, { key: "owner_type", header: "Owner" }, { key: "insurance_expiry", header: "Insurance", render: (v: any) => date(v.insurance_expiry) }, { key: "fitness_expiry", header: "Fitness", render: (v: any) => date(v.fitness_expiry) }, { key: "trips_count", header: "Trips", num: true }, { key: "is_active", header: "Status", render: (v: any) => <StatusBadge status={v.is_active ? "active" : "inactive"} /> }]}
    fields={[{ name: "reg_no", label: "Registration no", required: true, col: "col-md-4" }, { name: "make_model", label: "Make / model", col: "col-md-4" }, { name: "type", label: "Type", type: "select", options: ["van", "pickup", "bike", "truck", "car"].map((t) => ({ value: t, label: t })), col: "col-md-4" }, { name: "capacity_cartons", label: "Capacity (cartons)", type: "number", col: "col-md-3" }, { name: "owner_type", label: "Ownership", type: "select", options: [{ value: "company", label: "Company" }, { value: "rented", label: "Rented" }], default: "company", col: "col-md-3" }, { name: "insurance_expiry", label: "Insurance expiry", type: "date", col: "col-md-3" }, { name: "fitness_expiry", label: "Fitness expiry", type: "date", col: "col-md-3" }, { name: "tracker_id", label: "Tracker ID", col: "col-md-4" }, { name: "is_active", label: "Active", type: "checkbox", default: true, col: "col-md-2" }, { name: "notes", label: "Notes", type: "textarea", col: "col-md-6" }]} />;
}

export function DriversPage() {
  return <CrudPage page="drivers" title="Drivers" crumbs={[{ label: "Dispatch" }, { label: "Drivers" }]} endpoint="/api/v1/drivers" columns={[{ key: "name", header: "Driver", render: (d: any) => <span className="fw-semibold text-dark">{d.name}</span> }, { key: "phone", header: "Phone" }, { key: "cnic", header: "CNIC" }, { key: "license_no", header: "License", render: (d: any) => <>{d.license_no}<div className="text-muted fs-11">exp {date(d.license_expiry)}</div></> }, { key: "default_vehicle_reg", header: "Default vehicle" }, { key: "user_email", header: "App login", render: (d: any) => d.user_email || <span className="text-muted">none</span> }, { key: "trips_count", header: "Trips", num: true }, { key: "is_active", header: "Status", render: (d: any) => <StatusBadge status={d.is_active ? "active" : "inactive"} /> }]}
    fields={[{ name: "name", label: "Name", required: true, col: "col-md-4" }, { name: "phone", label: "Phone", col: "col-md-4" }, { name: "cnic", label: "CNIC", col: "col-md-4" }, { name: "license_no", label: "License no", col: "col-md-4" }, { name: "license_expiry", label: "License expiry", type: "date", col: "col-md-4" }, { name: "default_vehicle", label: "Default vehicle", type: "entity", endpoint: "/api/v1/vehicles", mapOption: (v) => ({ id: v.id, label: v.reg_no, sub: v.make_model }), col: "col-md-4" }, { name: "user", label: "Linked app user (driver login)", type: "entity", endpoint: "/api/v1/users", mapOption: (u) => ({ id: u.user_id, label: u.full_name, sub: u.email || u.phone }), col: "col-md-6", hint: "Create the user under Users & roles with the Driver role first" }, { name: "is_active", label: "Active", type: "checkbox", default: true, col: "col-md-2" }, { name: "notes", label: "Notes", type: "textarea", col: "col-md-12" }]}
    intro={<Alert kind="info">Drivers without a login are fine — staff mark deliveries from the trip screen. Give a driver a user with the <strong>Driver</strong> role to let them use the mobile app for proof of delivery.</Alert>} />;
}

export function TripsPage() {
  const stats = useQuery({ queryKey: ["trip-stats"], queryFn: () => api.get("/api/v1/trips/stats") });
  const { can } = usePerms();
  const cols: Column<any>[] = [
    { key: "number", header: "Trip #", sortKey: "number", render: (t) => <span className="fw-semibold text-dark">{t.number}</span> }, { key: "date", header: "Date", sortKey: "date", render: (t) => date(t.date) }, { key: "driver_name", header: "Driver / vehicle", render: (t) => <>{t.driver_name || "—"}<div className="text-muted fs-11">{t.vehicle_reg}</div></> }, { key: "rep_name", header: "Rep" }, { key: "customer_names", header: "Customer", render: (t) => (t.customer_names || []).join(", ") || "—" },
    { key: "stops", header: "Stops", render: (t) => `${t.delivered_count}/${t.stops_count}` }, { key: "loaded_value", header: "Loaded value", num: true, render: (t) => money(t.loaded_value, 0) }, { key: "cash_collected", header: "Cash", num: true, render: (t) => money(t.cash_collected, 0) }, { key: "status", header: "Status", render: (t) => <StatusBadge status={t.status} /> },
  ];
  return <ListPage page="trips" title="Delivery trips" crumbs={[{ label: "Dispatch" }, { label: "Trips" }]} endpoint="/api/v1/trips" columns={cols} rowLink={(t) => `/app/delivery/trips/${t.id}`} createTo="/app/delivery/trips/new" createLabel="New trip" defaultOrdering="-date"
    filters={[{ name: "status", label: "Status", type: "select", options: ["planned", "loading", "out_for_delivery", "completed", "closed", "cancelled"].map((s) => ({ value: s, label: s.replace(/_/g, " ") })) }, { name: "driver", label: "Driver", type: "entity", endpoint: "/api/v1/drivers", mapOption: (d) => ({ id: d.id, label: d.name }) }, { name: "active", label: "Active only", type: "toggle" }, { name: "date_from", label: "From", type: "date" }, { name: "date_to", label: "To", type: "date" }]}
    stats={stats.data && <div className="row g-3 mb-3"><div className="col-6 col-md-3"><StatCard label="Out for delivery" value={stats.data.out_for_delivery} icon="ti-truck-delivery" color="purple" /></div><div className="col-6 col-md-3"><StatCard label="Planned / loading" value={stats.data.planned} icon="ti-clipboard-list" color="info" /></div><div className="col-6 col-md-3"><StatCard label="To close" value={stats.data.to_close} icon="ti-lock" color="warning" /></div><div className="col-6 col-md-3"><StatCard label="Cash on trips" value={money(stats.data.cash_on_trips, 0)} icon="ti-cash" color="success" /></div></div>} />;
}

export function TripFormPage() {
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const warehouses = useWarehouses();
  const [form, setForm] = useState<any>({ warehouse: sp.get("warehouse") || "", date: new Date().toISOString().slice(0, 10), driver: null, vehicle: null, rep: null, delivery_rep: null, loader: null, helper: null, route: null, notes: "" });
  const [selected, setSelected] = useState<string[]>(sp.get("order") ? [sp.get("order")!] : []);
  const orders = useQuery({ queryKey: ["dispatchable-orders", form.warehouse], queryFn: () => api.list("/api/v1/sales-orders", { ready_for_trip: 1, warehouse: form.warehouse || undefined, page_size: 200, ordering: "requested_delivery_date" }) });
  const rows = (orders.data?.results || []).filter((o: any) => !o.trip);
  const value = rows.filter((o: any) => selected.includes(o.id)).reduce((s: number, o: any) => s + Number(o.total), 0);
  return (
    <>
      <PageHeader title="New delivery trip" crumbs={[{ label: "Trips", to: "/app/delivery/trips" }, { label: "New" }]} />
      <div className="card mb-3"><div className="card-body"><div className="row">
        <Field label="Warehouse *" col="col-md-3"><select className="form-select" value={form.warehouse} onChange={(e) => { setForm({ ...form, warehouse: e.target.value }); setSelected([]); }}><option value="">Select…</option>{warehouses.rows.filter((w: any) => w.type !== "van").map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
        <Field label="Date" col="col-md-2"><DateInput className="form-control" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} /></Field>
        <Field label="Driver *" col="col-md-3"><EntitySelect endpoint="/api/v1/drivers" query={{ is_active: true }} value={form.driver} onChange={(v, raw) => setForm({ ...form, driver: v, vehicle: raw?.default_vehicle || form.vehicle })} mapOption={(d) => ({ id: d.id, label: d.name, sub: d.default_vehicle_reg, raw: d })} /></Field>
        <Field label="Vehicle" col="col-md-2"><EntitySelect endpoint="/api/v1/vehicles" query={{ is_active: true }} value={form.vehicle} onChange={(v) => setForm({ ...form, vehicle: v })} mapOption={(v) => ({ id: v.id, label: v.reg_no, sub: v.make_model })} /></Field>
        <Field label="Sales rep" col="col-md-2"><EntitySelect endpoint="/api/v1/supplier-reps" query={{ is_active: true }} value={form.rep} onChange={(v) => setForm({ ...form, rep: v })} mapOption={(r) => ({ id: r.id, label: r.name })} placeholder="optional" /></Field>
        <Field label="Delivery person *" col="col-md-3"><EntitySelect endpoint="/api/v1/people" value={form.delivery_rep} onChange={(v) => setForm({ ...form, delivery_rep: v })} mapOption={(u) => ({ id: u.user_id || u.id, label: u.full_name || u.email, sub: (u.role_names || []).join(", ") || u.email })} placeholder="optional" /></Field>
        <Field label="Helper / delivery person" col="col-md-3"><EntitySelect endpoint="/api/v1/people" value={form.helper} onChange={(v) => setForm({ ...form, helper: v })} mapOption={(u) => ({ id: u.user_id || u.id, label: u.full_name || u.email, sub: (u.role_names || []).join(", ") || u.email })} placeholder="rides with the driver" /></Field>
        <Field label="Route" col="col-md-3"><EntitySelect endpoint="/api/v1/routes" value={form.route} onChange={(v) => setForm({ ...form, route: v })} mapOption={(r) => ({ id: r.id, label: r.name })} placeholder="optional" /></Field>
        <Field label="Notes" col="col-md-6"><input className="form-control" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
      </div></div></div>
      <div className="card mb-3"><div className="card-header d-flex justify-content-between"><strong>Orders ready to dispatch {form.warehouse ? "in this warehouse" : "(select a warehouse)"}</strong><span className="fs-13">{selected.length} selected · {money(value)}</span></div>
        <div className="card-body p-0"><table className="table table-sm mb-0 fs-12"><thead className="table-light"><tr><th><input type="checkbox" className="form-check-input" onChange={(e) => setSelected(e.target.checked ? rows.map((o: any) => o.id) : [])} /></th><th>Order</th><th>Customer</th><th>Status</th><th>Deliver by</th><th>Rep</th><th className="num">Total</th></tr></thead>
          <tbody>{rows.map((o: any) => <tr key={o.id}><td><input type="checkbox" className="form-check-input" checked={selected.includes(o.id)} onChange={(e) => setSelected(e.target.checked ? [...selected, o.id] : selected.filter((x) => x !== o.id))} /></td><td>{o.number}</td><td>{o.customer_name}<div className="text-muted fs-11">{o.customer_code}</div></td><td><StatusBadge status={o.status} /></td><td>{date(o.requested_delivery_date)}</td><td>{o.rep_name}</td><td className="num">{money(o.total)}</td></tr>)}{!rows.length && <tr><td colSpan={7} className="text-muted text-center py-3">No orders waiting for dispatch</td></tr>}</tbody></table></div></div>
      <div className="d-flex gap-2 justify-content-end"><button className="btn btn-light" onClick={() => nav(-1)}>Cancel</button><button className="btn btn-primary" disabled={!form.warehouse || !selected.length} onClick={async () => { if (!form.delivery_rep) { toast.error("Please choose the delivery person for this trip."); return; } try { const r = await api.post("/api/v1/trips", { ...form, order_ids: selected }, true); toast.success("Trip created"); nav(`/app/delivery/trips/${r.id}`); } catch (e: any) { toast.error(e.message); } }}>Create trip with {selected.length} order(s)</button></div>
    </>
  );
}

/**
 * Print or save one of the delivery documents.
 *
 * Both report failures. A bare `onClick={() => api.download(...)}` drops the rejection on the floor, so a
 * document the server cannot render looks exactly like a button that does nothing — which is how a broken
 * PDF endpoint stays unnoticed.
 */
async function printPdf(path: string): Promise<void> {
  try {
    await api.printDoc(path);
  } catch (e: any) {
    toast.error(e.message);
  }
}

async function savePdf(path: string, filename: string): Promise<void> {
  try {
    await api.download(path, filename);
  } catch (e: any) {
    toast.error(e.message);
  }
}

export function TripDetailPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const { can } = usePerms();
  const q = useDoc("/api/v1/trips", id);
  const { run, busy } = useAction("/api/v1/trips", id, [["trip-stats"], ["list", "/api/v1/delivery-notes"], ["list", "/api/v1/sales-orders"]]);
  const [dlg, setDlg] = useState<null | "start" | "close" | "cancel" | "add">(null);
  const [closeForm, setCloseForm] = useState({ odometer_end: "", fuel_amount: "", expense_amount: "", override: false });
  const [odo, setOdo] = useState("");
  const [addSel, setAddSel] = useState<string[]>([]);
  const [deliverNote, setDeliverNote] = useState<any>(null);
  const [removing, setRemoving] = useState<any>(null);   // the stop the × takes off the trip, asked first
  const rec = useQuery({ queryKey: ["trip-rec", id], queryFn: () => api.get(`/api/v1/trips/${id}/reconciliation`), enabled: Boolean(q.data && ["out_for_delivery", "completed", "closed"].includes(q.data.status)) });
  const candidates = useQuery({ queryKey: ["trip-add-orders", q.data?.warehouse], queryFn: () => api.list("/api/v1/sales-orders", { ready_for_trip: 1, warehouse: q.data?.warehouse, page_size: 200 }), enabled: dlg === "add" });
  if (q.isLoading) return <Spinner />;
  const t = q.data;
  if (!t) return null;
  const notes = [...(t.delivery_notes || [])].sort((a: any, b: any) => a.sequence - b.sequence);
  const planned = ["planned", "loading"].includes(t.status);
  return (
    <>
      <PageHeader title={t.number} badge={<StatusBadge status={t.status} />} crumbs={[{ label: "Trips", to: "/app/delivery/trips" }, { label: t.number }]}
        actions={<>
          {planned && pageRights("trips").edit && <button className="btn btn-outline-primary" onClick={() => setDlg("add")}><i className="ti ti-plus me-1" />Add orders</button>}
          {planned && pageRights("trips").extras.start && <button className="btn btn-success" disabled={!!busy} onClick={() => setDlg("start")}><i className="ti ti-player-play me-1" />Start trip (dispatch)</button>}
          {["out_for_delivery", "completed"].includes(t.status) && pageRights("trips").extras.start && <button className="btn btn-primary" disabled={!!busy} onClick={() => setDlg("close")}><i className="ti ti-lock me-1" />Close trip</button>}
          {planned && pageRights("trips").edit && <button className="btn btn-outline-danger" onClick={() => setDlg("cancel")}>Cancel</button>}
          <button className="btn btn-outline-secondary" onClick={() => printPdf(`/api/v1/trips/${t.id}/dispatch-sheet`)}><i className="ti ti-printer me-1" />Print sheet</button>
          <button className="btn btn-outline-secondary" onClick={() => savePdf(`/api/v1/trips/${t.id}/dispatch-sheet`, `${t.number}-dispatch-sheet.pdf`)}><i className="ti ti-file-type-pdf me-1" />PDF</button>
        </>} />
      <div className="row g-3 mb-3">
        <div className="col-6 col-md-3"><StatCard label="Driver" value={t.driver_name || "—"} icon="ti-steering-wheel" color="primary" sub={t.driver_phone} /></div>
        <div className="col-6 col-md-3"><StatCard label="Vehicle" value={t.vehicle_reg || "—"} icon="ti-truck" color="info" sub={[t.rep_name ? `sales: ${t.rep_name}` : "", t.delivery_rep_name ? `delivery: ${t.delivery_rep_name}` : ""].filter(Boolean).join(" · ")} /></div>
        <div className="col-6 col-md-3"><StatCard label="Stops" value={`${notes.filter((n: any) => ["delivered", "partially_delivered", "failed", "returned"].includes(n.status)).length} / ${t.stops_count}`} icon="ti-map-pin" color="purple" sub={`orders value ${money(t.orders_value, 0)}`} /></div>
        <div className="col-6 col-md-3"><StatCard label="Cash collected" value={money(t.cash_collected, 0)} icon="ti-cash" color="success" sub={t.loaded_value ? `loaded value ${money(t.loaded_value, 0)}` : ""} /></div>
      </div>
      {t.status === "out_for_delivery" && <Alert kind="info">Stock has left the warehouse (orders are <strong>dispatched / sold</strong>). Mark each stop delivered, partially delivered or failed, then close the trip to book returns and reconcile cash.</Alert>}
      {rec.data && <div className="card mb-3"><div className="card-header d-flex justify-content-between"><strong>Reconciliation</strong>{rec.data.ok ? <span className="badge bg-success">balanced</span> : <span className="badge bg-warning text-dark">mismatch</span>}</div><div className="card-body"><div className="row g-2 fs-13">{[["Loaded", rec.data.loaded], ["Delivered", rec.data.delivered], ["Returned (good)", rec.data.returned_good], ["Returned (damaged)", rec.data.returned_damaged], ["Unaccounted", rec.data.unaccounted], ["Cash collected", money(rec.data.cash_collected)], ["Receipts booked", money(rec.data.receipts)], ["Cash mismatch", money(rec.data.cash_mismatch)]].map(([k, v]) => <div className="col-6 col-md-3" key={k as string}><div className="border rounded p-2"><div className="text-muted fs-11">{k}</div><div className="fw-semibold">{v as string}</div></div></div>)}</div>{rec.data.damage_adjustment && <div className="mt-2 fs-13">Damage adjustment: {rec.data.damage_adjustment}</div>}</div></div>}
      <div className="card mb-3"><div className="card-header"><strong>Stops</strong></div><div className="card-body p-0">
        <table className="table align-middle mb-0 fs-13"><thead className="table-light"><tr><th>#</th><th>Delivery note</th><th>Customer</th><th>Order</th><th className="num">Loaded</th><th className="num">Delivered</th><th className="num">Returned</th><th className="num">Cash</th><th>Status</th><th /></tr></thead>
          <tbody>{notes.map((n: any) => { const loaded = n.lines.reduce((s: number, l: any) => s + Number(l.qty_loaded), 0); const del = n.lines.reduce((s: number, l: any) => s + Number(l.qty_delivered), 0); const ret = n.lines.reduce((s: number, l: any) => s + Number(l.qty_returned), 0); return (
            <tr key={n.id}><td>{n.sequence}</td><td><Link to={`/app/delivery/notes/${n.id}`}>{n.number}</Link></td><td>{n.customer_name}<div className="text-muted fs-11">{n.customer_address}</div></td><td><Link to={`/app/sales/orders/${n.order}`}>{n.order_number}</Link><div className="text-muted fs-11">{money(n.order_total)}</div></td><td className="num">{qty(loaded)}</td><td className="num">{qty(del)}</td><td className="num">{qty(ret)}</td><td className="num">{money(n.cash_collected)}</td><td><StatusBadge status={n.status} />{n.invoice_number && <div className="fs-11"><Link to={`/app/sales/invoices/${n.invoice_id}`}>{n.invoice_number}</Link></div>}</td>
              <td className="text-end no-row-click">{["loaded", "pending", "dispatched"].includes(n.status) && (t.status === "out_for_delivery" || planned) && pageRights("delivery_notes").extras.complete && <><button className="btn btn-sm btn-primary" onClick={() => setDeliverNote(n)}>Deliver</button><button className="btn btn-sm btn-outline-danger ms-1" title="Store refused the whole delivery" onClick={() => setDeliverNote({ ...n, _startFailed: true })}>Refused</button></>}{planned && pageRights("trips").edit && <button className="btn btn-sm btn-outline-danger ms-1" title={`Take ${n.order_number} off this trip`} onClick={() => setRemoving(n)}><i className="ti ti-x me-1" />Remove</button>}</td></tr>); })}</tbody></table>
      </div></div>
      <DocTabs model="DeliveryTrip" id={t.id} showMovements />
      <Modal open={dlg === "start"} title="Start trip" onClose={() => setDlg(null)} size="sm" footer={<><button className="btn btn-light" onClick={() => setDlg(null)}>Cancel</button><button className="btn btn-success" disabled={!!busy} onClick={async () => { await run("start", { odometer_start: odo || null }, { success: "Trip started — stock dispatched" }); setDlg(null); }}>Start & dispatch</button></>}><p className="fs-13">Starting the trip removes the loaded goods from the warehouse ledger and marks every order <strong>dispatched</strong>. The driver is notified.</p><Field label="Odometer start"><input type="number" className="form-control" value={odo} onChange={(e) => setOdo(e.target.value)} /></Field></Modal>
      <Modal open={dlg === "close"} title="Close trip" onClose={() => setDlg(null)} size="sm" footer={<><button className="btn btn-light" onClick={() => setDlg(null)}>Cancel</button><button className="btn btn-primary" disabled={!!busy} onClick={async () => { try { await run("close", { odometer_end: closeForm.odometer_end || null, fuel_amount: closeForm.fuel_amount || 0, expense_amount: closeForm.expense_amount || 0, override: closeForm.override }, { success: "Trip closed" }); setDlg(null); } catch { /* shown */ } }}>Close trip</button></>}>
        <p className="fs-13">Returned goods go back into the warehouse, damaged items become an adjustment, and cash collected is checked against receipts.</p>
        <div className="row"><Field label="Odometer end" col="col-6"><input type="number" className="form-control" value={closeForm.odometer_end} onChange={(e) => setCloseForm({ ...closeForm, odometer_end: e.target.value })} /></Field><Field label="Fuel amount" col="col-6"><input type="number" step="0.01" className="form-control" value={closeForm.fuel_amount} onChange={(e) => setCloseForm({ ...closeForm, fuel_amount: e.target.value })} /></Field><Field label="Other trip expense" col="col-6"><input type="number" step="0.01" className="form-control" value={closeForm.expense_amount} onChange={(e) => setCloseForm({ ...closeForm, expense_amount: e.target.value })} /></Field></div>
        {pageRights("adjustments").extras.approve && <div className="form-check"><input className="form-check-input" type="checkbox" id="ovr" checked={closeForm.override} onChange={(e) => setCloseForm({ ...closeForm, override: e.target.checked })} /><label className="form-check-label" htmlFor="ovr">Override mismatch / mark pending stops as failed</label></div>}
      </Modal>
      <ConfirmDialog open={!!removing} title={`Take ${removing?.order_number} off this trip?`} danger confirmText="Remove from trip" loading={!!busy}
        message={`${removing?.customer_name || ""} · ${removing?.number || ""} goes back to waiting for a trip. Its stock stays issued and its invoice stays as it is.`}
        onClose={() => setRemoving(null)} onConfirm={async () => { await run("remove-order", { order_id: removing.order }, { success: `${removing.order_number} taken off the trip` }); setRemoving(null); }} />
      <ConfirmDialog open={dlg === "cancel"} title="Cancel trip" withReason danger confirmText="Cancel trip" onClose={() => setDlg(null)} onConfirm={async (r) => { await run("cancel", { reason: r }); setDlg(null); }} />
      <Modal open={dlg === "add"} title="Add issued orders" onClose={() => setDlg(null)} footer={<><button className="btn btn-light" onClick={() => setDlg(null)}>Cancel</button><button className="btn btn-primary" disabled={!addSel.length} onClick={async () => { await run("assign-orders", { order_ids: addSel }, { success: "Orders added" }); setAddSel([]); setDlg(null); }}>Add {addSel.length}</button></>}>
        <table className="table table-sm fs-12"><thead className="table-light"><tr><th /><th>Order</th><th>Customer</th><th>Deliver by</th><th className="num">Total</th></tr></thead><tbody>{(candidates.data?.results || []).filter((o: any) => !o.trip).map((o: any) => <tr key={o.id}><td><input type="checkbox" className="form-check-input" checked={addSel.includes(o.id)} onChange={(e) => setAddSel(e.target.checked ? [...addSel, o.id] : addSel.filter((x) => x !== o.id))} /></td><td>{o.number}</td><td>{o.customer_name}</td><td>{date(o.requested_delivery_date)}</td><td className="num">{money(o.total)}</td></tr>)}</tbody></table>
      </Modal>
      {deliverNote && <DeliverModal note={deliverNote} onClose={() => setDeliverNote(null)} onDone={() => { setDeliverNote(null); qc.invalidateQueries({ queryKey: ["doc", "/api/v1/trips", id] }); qc.invalidateQueries({ queryKey: ["trip-rec", id] }); }} />}
    </>
  );
}

/** A delivery is recorded on a trip (owner, 11 Nov 2026): one on no trip names its driver and delivery person, and the
 *  trip is made with the delivery. Off only when the company turns "Deliveries need a trip" off in Settings. */
export const deliveryNeedsTrip = () => (useAuth.getState().me as any)?.tenant?.settings?.delivery_needs_trip !== false;
export type Crew = { driver: string | null; vehicle: string | null; delivery_rep: string | null };
export const NO_CREW: Crew = { driver: null, vehicle: null, delivery_rep: null };
export const crewMissing = (c: Crew) => (!c.driver ? "Please choose the driver." : !c.delivery_rep ? "Please choose the delivery person." : null);

export function TripCrewFields({ crew, setCrew, col = "col-md-4" }: { crew: Crew; setCrew: (c: Crew) => void; col?: string }) {
  return (
    <div className="row">
      <Field label="Driver *" col={col}><EntitySelect endpoint="/api/v1/drivers" query={{ is_active: true }} value={crew.driver} onChange={(v, raw) => setCrew({ ...crew, driver: v, vehicle: raw?.default_vehicle || crew.vehicle })} mapOption={(d) => ({ id: d.id, label: d.name, sub: d.default_vehicle_reg, raw: d })} /></Field>
      <Field label="Vehicle" col={col}><EntitySelect endpoint="/api/v1/vehicles" query={{ is_active: true }} value={crew.vehicle} onChange={(v) => setCrew({ ...crew, vehicle: v })} mapOption={(v) => ({ id: v.id, label: v.reg_no, sub: v.make_model })} /></Field>
      <Field label="Delivery person *" col={col}><EntitySelect endpoint="/api/v1/people" value={crew.delivery_rep} onChange={(v) => setCrew({ ...crew, delivery_rep: v })} mapOption={(u) => ({ id: u.user_id || u.id, label: u.full_name || u.email, sub: (u.role_names || []).join(", ") || u.email })} /></Field>
    </div>
  );
}

const RETURN_REASONS = [["rejected", "Rejected By Customer"], ["damaged", "Damaged"], ["shortage", "Shortage"], ["not_ordered", "Not Ordered"], ["other", "Other"]];
const LOST_REASONS = ["damaged", "shortage"];   // a broken or missing unit cannot go back on the shelf

export function DeliverModal({ note, onClose, onDone }: { note: any; onClose: () => void; onDone: () => void }) {
  const [lines, setLines] = useState<any[]>(note.lines.map((l: any) => ({ line_id: l.id, name: l.variant_name, loaded: l.qty_loaded, qty_delivered: l.qty_loaded, qty_returned: "0", return_reason: "", return_condition: "good" })));
  const [form, setForm] = useState({ receiver_name: "", cash_collected: "", failed: Boolean(note._startFailed), failure_reason: note._startFailed ? "Customer refused the delivery" : "", remarks: "", delivered_at: "" });
  const [sig, setSig] = useState<File | null>(null);
  const [photo, setPhoto] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const needsCrew = !note.trip && deliveryNeedsTrip();
  const [crew, setCrew] = useState<Crew>(NO_CREW);
  // what saving this will credit the customer, worked out by the server the way the credit note will be
  const [preview, setPreview] = useState<any>(null);
  const backKey = lines.map((l) => l.qty_returned).join("|");
  useEffect(() => {
    if (form.failed || !lines.some((l) => Number(l.qty_returned) > 0)) { setPreview(null); return; }
    let stale = false;
    const t = setTimeout(async () => {
      try { const p = await api.post(`/api/v1/delivery-notes/${note.id}/return-preview`, { delivered: lines.map((l) => ({ line_id: l.line_id, qty_returned: l.qty_returned })) }); if (!stale) setPreview(p); }
      catch { if (!stale) setPreview(null); }
    }, 300);
    return () => { stale = true; clearTimeout(t); };
  }, [backKey, form.failed]);
  const setLine = (i: number, patch: any) => setLines(lines.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  // every unit that went out is either delivered or returned, so each figure follows the other
  const setReturned = (i: number, v: number) => {
    const l = lines[i], loaded = Number(l.loaded), back = Math.min(Math.max(v, 0), loaded);
    setLine(i, { qty_returned: String(back), qty_delivered: String(Number((loaded - back).toFixed(3))), return_reason: back > 0 ? l.return_reason || "rejected" : "", return_condition: back > 0 ? l.return_condition : "good" });
  };
  const submit = async () => {
    const missing = needsCrew ? crewMissing(crew) : null;
    if (missing) return toast.error(missing);
    setBusy(true);
    try {
      const fd = new FormData();
      if (needsCrew) { fd.append("driver", crew.driver || ""); fd.append("vehicle", crew.vehicle || ""); fd.append("delivery_rep", crew.delivery_rep || ""); }
      fd.append("delivered", JSON.stringify(lines.map((l) => ({ line_id: l.line_id, qty_delivered: l.qty_delivered, qty_returned: l.qty_returned, return_reason: l.return_reason, return_condition: l.return_condition }))));
      fd.append("receiver_name", form.receiver_name); fd.append("cash_collected", form.cash_collected || "0"); fd.append("failed", String(form.failed)); fd.append("failure_reason", form.failure_reason);
      fd.append("remarks", form.remarks); if (form.delivered_at) fd.append("delivered_at", new Date(form.delivered_at).toISOString());
      if (sig) fd.append("signature", sig); if (photo) fd.append("photo", photo);
      const saved = await api.upload(`/api/v1/delivery-notes/${note.id}/deliver`, fd);
      toast.success(form.failed ? "Stop marked failed" : saved?.credit_note ? `Delivered — credit note ${saved.credit_note.number} posted` : "Delivered — an invoice not raised yet is saved as a draft to review and post");
      onDone();
    } catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
  };
  return (
    <Modal open title={`Deliver ${note.number} — ${note.customer_name}`} onClose={onClose} footer={<><button className="btn btn-light" onClick={onClose}>Cancel</button><button className={`btn ${form.failed ? "btn-danger" : "btn-primary"}`} disabled={busy} onClick={submit}>{form.failed ? "Mark failed" : "Confirm delivery"}</button></>}>
      {needsCrew && <TripCrewFields crew={crew} setCrew={setCrew} />}
      <div className="form-check form-switch mb-2"><input className="form-check-input" type="checkbox" id="failed" checked={form.failed} onChange={(e) => setForm({ ...form, failed: e.target.checked })} /><label className="form-check-label" htmlFor="failed">Delivery failed (shop closed, refused…)</label></div>
      {form.failed ? <><Field label="Failure reason"><input className="form-control" value={form.failure_reason} onChange={(e) => setForm({ ...form, failure_reason: e.target.value })} /></Field><Field label="Remarks"><input className="form-control" value={form.remarks} onChange={(e) => setForm({ ...form, remarks: e.target.value })} placeholder="what the driver reported" /></Field></> : <>
        <table className="table table-sm fs-12"><thead className="table-light"><tr><th>Item</th><th className="num">Issued</th><th style={{ width: 90 }}>Delivered</th><th style={{ width: 90 }}>Returned</th><th style={{ minWidth: 215 }}>Reason</th><th style={{ minWidth: 150 }}>Back To Stock / Wastage</th></tr></thead>
          <tbody>{lines.map((l, i) => { const back = Number(l.qty_returned) > 0; return <tr key={l.line_id}><td>{l.name}</td><td className="num">{qty(l.loaded)}</td><td><input type="number" step="any" min="0" max={l.loaded} className="form-control form-control-sm" value={l.qty_delivered} onChange={(e) => setReturned(i, Number(l.loaded) - Number(e.target.value || 0))} /></td><td><input type="number" step="any" min="0" max={l.loaded} className="form-control form-control-sm" value={l.qty_returned} onChange={(e) => setReturned(i, Number(e.target.value || 0))} /></td><td><select className="form-select form-select-sm" disabled={!back} value={l.return_reason} onChange={(e) => setLine(i, { return_reason: e.target.value, return_condition: LOST_REASONS.includes(e.target.value) ? "damaged" : l.return_condition })}>{!back && <option value="" />}{RETURN_REASONS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></td><td><select className="form-select form-select-sm" disabled={!back || LOST_REASONS.includes(l.return_reason)} value={l.return_condition} onChange={(e) => setLine(i, { return_condition: e.target.value })}><option value="good">Back To Stock</option><option value="damaged">Wastage</option></select></td></tr>; })}</tbody></table>
        {preview && Number(preview.credit) > 0 && <div className="row g-3 mb-3"><InfoItem label="Credit Note" col="col-md-6"><span className="fw-semibold">{money(preview.credit)}</span></InfoItem>{preview.balance_after != null && <InfoItem label="Balance After Return" col="col-md-6"><span className="fw-semibold">{money(preview.balance_after)}</span></InfoItem>}</div>}
        <div className="row"><Field label="Receiver name" col="col-md-6"><input className="form-control" value={form.receiver_name} onChange={(e) => setForm({ ...form, receiver_name: e.target.value })} /></Field><Field label="Cash collected" col="col-md-6" hint="Creates a receipt allocated to this delivery's invoice"><input type="number" step="0.01" className="form-control" value={form.cash_collected} onChange={(e) => setForm({ ...form, cash_collected: e.target.value })} /></Field>
          <Field label="Delivered on" col="col-md-6" hint="blank = now"><DateTimeInput className="form-control" value={form.delivered_at} onChange={(e) => setForm({ ...form, delivered_at: e.target.value })} /></Field>
          <Field label="Delivery remarks" col="col-md-6"><input className="form-control" value={form.remarks} onChange={(e) => setForm({ ...form, remarks: e.target.value })} placeholder="e.g. one carton refused — damaged corner" /></Field>
          <Field label="Signature image" col="col-md-6"><input type="file" accept="image/*" className="form-control" onChange={(e) => setSig(e.target.files?.[0] || null)} /></Field><Field label="Photo" col="col-md-6"><input type="file" accept="image/*" className="form-control" onChange={(e) => setPhoto(e.target.files?.[0] || null)} /></Field></div>
      </>}
    </Modal>
  );
}

export function DeliveryNotesPage() {
  const cols: Column<any>[] = [
    { key: "number", header: "DN #", render: (n) => <span className="fw-semibold text-dark">{n.number}</span> }, { key: "trip_number", header: "Trip", render: (n) => n.trip_number || <span className="text-muted">direct</span> }, { key: "order_number", header: "Order" }, { key: "customer_name", header: "Customer" },
    { key: "delivered_at", header: "Delivered", render: (n) => <>{datetime(n.delivered_at)}<div className="text-muted fs-11">{n.receiver_name}</div></> }, { key: "cash_collected", header: "Cash", num: true, render: (n) => money(n.cash_collected) }, { key: "invoice_number", header: "Invoice" }, { key: "status", header: "Status", render: (n) => <StatusBadge status={n.status} /> },
  ];
  return <ListPage title="Delivery notes" crumbs={[{ label: "Dispatch" }, { label: "Delivery notes" }]} endpoint="/api/v1/delivery-notes" columns={cols} rowLink={(n) => `/app/delivery/notes/${n.id}`} defaultOrdering="-created_at" filters={[{ name: "status", label: "Status", type: "select", options: ["pending", "loaded", "delivered", "partially_delivered", "failed", "returned"].map((s) => ({ value: s, label: s.replace(/_/g, " ") })) }, { name: "customer", label: "Customer", type: "entity", endpoint: "/api/v1/customers", mapOption: (c) => ({ id: c.id, label: c.name }) }]} />;
}

export function DeliveryNoteDetailPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const { can } = usePerms();
  const q = useDoc("/api/v1/delivery-notes", id);
  const [open, setOpen] = useState(false);
  const [again, setAgain] = useState(false);
  const [undo, setUndo] = useState(false);
  if (q.isLoading) return <Spinner />;
  const n = q.data;
  if (!n) return null;
  return (
    <>
      <PageHeader title={n.number} badge={<StatusBadge status={n.status} label={n.status_label} />} crumbs={[{ label: "Delivery notes", to: "/app/delivery/notes" }, { label: n.number }]} actions={<>{["pending", "loaded", "dispatched"].includes(n.status) && n.stock_out_done && pageRights("delivery_notes").extras.complete && <button className="btn btn-primary" onClick={() => setOpen(true)}>Deliver</button>}{["failed", "returned"].includes(n.status) && pageRights("delivery_notes").extras.complete && <button className="btn btn-primary" onClick={() => setAgain(true)}><i className="ti ti-truck-return me-1" />Deliver again</button>}{n.can_undo_return && pageRights("delivery_notes").extras.complete && <button className="btn btn-outline-danger" onClick={() => setUndo(true)}><i className="ti ti-arrow-back-up me-1" />Undo return</button>}<button className="btn btn-outline-secondary" onClick={() => printPdf(`/api/v1/delivery-notes/${n.id}/pdf`)}><i className="ti ti-printer me-1" />Print challan</button><button className="btn btn-outline-secondary" onClick={() => savePdf(`/api/v1/delivery-notes/${n.id}/pdf`, `${n.number}.pdf`)}><i className="ti ti-file-type-pdf me-1" />PDF</button></>} />
      <div className="card mb-3"><div className="card-body">
        <div className="row g-3 mb-3"><InfoItem label="Customer"><Link to={`/app/customers/${n.customer}`}>{n.customer_name}</Link><div className="fs-12 text-muted">{n.customer_phone} · {n.customer_address}</div></InfoItem><InfoItem label="Order"><Link to={`/app/sales/orders/${n.order}`}>{n.order_number}</Link><div className="fs-12 text-muted">{money(n.order_total)}</div></InfoItem><InfoItem label="Load form">{n.picklist_number ? <Link to={`/app/delivery/pick-lists/${n.picklist}`}>{n.picklist_number}</Link> : "—"}</InfoItem><InfoItem label="Dispatch">{n.trip_number ? <Link to={`/app/delivery/trips/${n.trip}`}>{n.trip_number}</Link> : "direct delivery"}</InfoItem><InfoItem label="Delivered">{datetime(n.delivered_at)}<div className="fs-12 text-muted">{n.receiver_name} · by {n.delivered_by_name}</div></InfoItem>
          <InfoItem label="Invoice">{n.invoice_number ? <Link to={`/app/sales/invoices/${n.invoice_id}`}>{n.invoice_number}</Link> : "—"}</InfoItem>{n.credit_note && <InfoItem label="Credit note"><Link to={`/app/sales/credit-notes/${n.credit_note.id}`}>{n.credit_note.number}</Link><div className="fs-12 text-muted">{money(n.credit_note.total)}</div></InfoItem>}<InfoItem label="Cash collected">{money(n.cash_collected)}</InfoItem><InfoItem label="GPS">{n.gps_lat ? <a href={`https://maps.google.com/?q=${n.gps_lat},${n.gps_lng}`} target="_blank" rel="noreferrer">{n.gps_lat}, {n.gps_lng}</a> : "—"}</InfoItem><InfoItem label="Failure reason">{n.failure_reason || "—"}</InfoItem><InfoItem label="Remarks" col="col-md-6">{n.remarks || "—"}</InfoItem></div>
        <table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>Item</th><th>Batch</th><th className="num">Loaded</th><th className="num">Delivered</th><th className="num">Returned</th><th>Reason</th><th>Back To Stock / Wastage</th></tr></thead><tbody>{n.lines.map((l: any) => <tr key={l.id}><td>{l.variant_name}<div className="text-muted fs-11">{l.variant_sku}</div></td><td>{l.batch_no}</td><td className="num">{qty(l.qty_loaded)}</td><td className="num">{qty(l.qty_delivered)}</td><td className="num">{qty(l.qty_returned)}</td><td>{l.return_reason_label}</td><td>{l.return_result}</td></tr>)}</tbody></table>
        {(n.signature_url || n.photo_url) && <div className="d-flex gap-3 mt-3">{n.signature_url && <div><div className="fs-11 text-muted">Signature</div><img src={n.signature_url} alt="signature" style={{ maxHeight: 120, border: "1px solid #eee" }} /></div>}{n.photo_url && <div><div className="fs-11 text-muted">Photo</div><a href={n.photo_url} target="_blank" rel="noreferrer"><img src={n.photo_url} alt="proof" style={{ maxHeight: 120, border: "1px solid #eee" }} /></a></div>}</div>}
      </div></div>
      {n.order && <WorkflowStepper orderId={n.order} compact />}
      <DocTabs model="DeliveryNote" id={n.id} showMovements />
      {open && <DeliverModal note={n} onClose={() => setOpen(false)} onDone={() => { setOpen(false); qc.invalidateQueries({ queryKey: ["doc", "/api/v1/delivery-notes", id] }); }} />}
      <ConfirmDialog open={undo} title={`Undo the return on ${n.number}`} withReason confirmText="Undo return"
        message={`The returned units count as delivered${n.credit_note ? ` and ${n.credit_note.number} is cancelled` : ""}.`}
        onClose={() => setUndo(false)} onConfirm={async (reason) => {
          try { await api.post(`/api/v1/delivery-notes/${n.id}/undo-return`, { reason: reason || "" }, true); toast.success("Return undone"); setUndo(false); qc.invalidateQueries({ queryKey: ["doc", "/api/v1/delivery-notes", id] }); }
          catch (e: any) { toast.error(e.message); }
        }} />
      <ConfirmDialog open={again} title={`Deliver ${n.number} again`} withReason confirmText="Deliver again"
        message={`The same goods and the same invoice${n.invoice_number ? ` (${n.invoice_number})` : ""} go out again — on the next trip, or deliver it from here.`}
        onClose={() => setAgain(false)} onConfirm={async (reason) => {
          try { await api.post(`/api/v1/delivery-notes/${n.id}/deliver-again`, { reason: reason || "" }, true); toast.success(`${n.number} goes out again`); setAgain(false); qc.invalidateQueries({ queryKey: ["doc", "/api/v1/delivery-notes", id] }); }
          catch (e: any) { toast.error(e.message); }
        }} />
    </>
  );
}

export function PickListsPage() {
  const { can } = usePerms();
  const cols: Column<any>[] = [{ key: "number", header: "Load form #", render: (p) => <span className="fw-semibold text-dark">{p.number}</span> }, { key: "customer_names", header: "Customer", render: (p) => (p.customer_names || []).join(", ") || "—" }, { key: "order_numbers", header: "Orders", render: (p) => p.order_numbers?.join(", ") }, { key: "sent_at", header: "Sent to warehouse", render: (p) => datetime(p.sent_at) }, { key: "issued_at", header: "Issued", render: (p) => <>{datetime(p.issued_at)}{p.issued_by_name ? <div className="text-muted fs-11">{p.issued_by_name}</div> : null}</> }, { key: "status", header: "Status", render: (p) => <StatusBadge status={p.status} label={p.status_label} /> }];
  return <ListPage page="load_forms" title="Load forms" crumbs={[{ label: "Dispatch" }, { label: "Load forms" }]} endpoint="/api/v1/pick-lists" columns={cols} rowLink={(p) => `/app/delivery/pick-lists/${p.id}`} createTo="/app/delivery/pick-lists/new" createLabel="New load form" defaultOrdering="-created_at" filters={[{ name: "status", label: "Status", type: "select", options: [["open", "Pending"], ["sent", "Sent to warehouse"], ["picking", "Picking"], ["issued", "Issued"], ["partially_issued", "Partially issued"]].map(([value, label]) => ({ value, label })) }]} />;
}

export function PickListFormPage() {
  const nav = useNavigate();
  const warehouses = useWarehouses();
  const [warehouse, setWarehouse] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  // nothing is made until Save or Send (owner, 12 Nov 2026): Create load form only shows what the form will carry
  const [preview, setPreview] = useState<any>(null);
  const [qtys, setQtys] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState<"" | "preview" | "save" | "send">("");
  const qc = useQueryClient();
  const overlap = useOverlapGuard();
  const canSend = pageRights("load_forms").edit;
  const orders = useQuery({ queryKey: ["confirmed-orders", warehouse], queryFn: () => api.list("/api/v1/sales-orders", { status__in: "confirmed,partially_delivered", warehouse: warehouse || undefined, no_load_form: 1, page_size: 200 }), enabled: Boolean(warehouse) });
  // an order that has since got its load form leaves the list, and must leave the selection with it
  useEffect(() => { if (orders.data) setSelected((s) => (orders.data.results.some((o: any) => o.id === s) ? s : null)); }, [orders.data]);
  useEffect(() => { if (preview && preview.order !== selected) setPreview(null); }, [selected]);
  const showPreview = async () => {
    setBusy("preview");
    try {
      const p = await api.get("/api/v1/pick-lists/preview", { warehouse, order: selected });
      setPreview(p);
      setQtys(Object.fromEntries(p.lines.map((l: any) => [l.order_line, String(Number(l.qty))])));
      setNotes("");
    } catch (e: any) { toast.error(e.message); } finally { setBusy(""); }
  };
  const make = async (send: boolean) => {
    setBusy(send ? "send" : "save");
    try {
      const lines = preview.lines.map((l: any) => ({ order_line: l.order_line, qty: qtys[l.order_line] === "" ? "0" : qtys[l.order_line] }));
      const r = await api.post("/api/v1/pick-lists", { warehouse, orders: [preview.order], notes, send, lines }, true);
      toast.success(send ? "Sent to the warehouse" : "Load form saved");
      qc.invalidateQueries({ queryKey: ["confirmed-orders"] });
      nav(`/app/delivery/pick-lists/${r.id}`);
    } catch (e: any) { toast.error(e.message); qc.invalidateQueries({ queryKey: ["confirmed-orders"] }); }
    finally { setBusy(""); }
  };
  const balance = orders.data?.results.find((o: any) => o.id === preview?.order)?.status === "partially_delivered";
  const go = (send: boolean) => (balance ? overlap.guard({ order: preview.order }, () => make(send)) : make(send));
  const nothing = preview && preview.lines.every((l: any) => !Number(qtys[l.order_line] || 0));
  return (
    <>
      <PageHeader title="New load form" crumbs={[{ label: "Load forms", to: "/app/delivery/pick-lists" }, { label: "New" }]} />
      <div className="card mb-3"><div className="card-body"><Field label="Warehouse" col="col-md-4"><select className="form-select" value={warehouse} onChange={(e) => { setWarehouse(e.target.value); setSelected(null); setPreview(null); }}><option value="">Select…</option>{warehouses.rows.map((w: any) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
        <table className="table table-sm fs-12"><thead className="table-light"><tr><th /><th>Order</th><th>Customer</th><th>Deliver by</th><th className="num">Total</th></tr></thead><tbody>{orders.data?.results.map((o: any) => <tr key={o.id}><td><input type="radio" name="load-form-order" className="form-check-input" checked={selected === o.id} onChange={() => setSelected(o.id)} /></td><td>{o.number}</td><td>{o.customer_name}</td><td>{date(o.requested_delivery_date)}</td><td className="num">{money(o.total)}</td></tr>)}</tbody></table>
        {!preview && <div className="text-end"><button className="btn btn-primary" disabled={!selected || !!busy} onClick={showPreview}>{busy === "preview" ? "Loading…" : "Create load form"}</button></div>}
      </div></div>
      {preview && <div className="card mb-3">
        <div className="card-header"><strong>{preview.order_number}</strong> <span className="text-muted">· {preview.customer_name}</span></div>
        <div className="card-body">
          <table className="table table-sm fs-12">
            <thead className="table-light"><tr><th>{labelCase("item")}</th><th>{labelCase("batch")}</th><th className="num">{labelCase("pending")}</th><th className="num">{labelCase("in stock")}</th><th className="num" style={{ width: 120 }}>{labelCase("to pick")}</th></tr></thead>
            <tbody>{preview.lines.map((l: any) => <tr key={l.order_line}>
              <td>{l.variant_name}<div className="text-muted fs-11">{l.sku}</div></td>
              <td className="fs-11">{l.batches.map((b: any, i: number) => <div key={i}>{b.batch_no || "—"}{b.expiry ? ` · ${date(b.expiry)}` : ""} × {qty(b.qty)}</div>)}</td>
              <td className="num">{qty(l.pending)}</td>
              <td className={`num ${Number(l.available) < Number(qtys[l.order_line] || 0) ? "text-danger" : ""}`}>{qty(l.available)}</td>
              <td className="num"><input type="number" min={0} max={Number(l.pending)} step="any" className="form-control form-control-sm text-end" value={qtys[l.order_line] ?? ""} onChange={(e) => setQtys({ ...qtys, [l.order_line]: e.target.value })} /></td>
            </tr>)}</tbody>
          </table>
          <Field label="Notes" col="col-12"><textarea className="form-control" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
          <div className="d-flex justify-content-end gap-2 mt-2">
            <button className="btn btn-light" disabled={!!busy} onClick={() => setPreview(null)}>Cancel</button>
            <button className="btn btn-outline-primary" disabled={!!busy || nothing} onClick={() => go(false)}><i className="ti ti-device-floppy me-1" />{busy === "save" ? "Saving…" : "Save"}</button>
            {canSend && <button className="btn btn-primary" disabled={!!busy || nothing} onClick={() => go(true)}><i className="ti ti-send me-1" />{busy === "send" ? "Sending…" : "Send to warehouse"}</button>}
          </div>
        </div>
      </div>}
      {overlap.dialog}
    </>
  );
}

export function PickListDetailPage() {
  const { id } = useParams();
  const q = useDoc("/api/v1/pick-lists", id);
  const { run, busy } = useAction("/api/v1/pick-lists", id);
  const [scan, setScan] = useState("");
  const [issuing, setIssuing] = useState(false);
  // a saved, unsent load form is the office's to correct (owner, 27 Oct 2026): quantities and notes are edited
  // here and saved; only Send hands it to the warehouse
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  if (q.isLoading) return <Spinner />;
  const p = q.data;
  if (!p) return null;
  const issued = ["issued", "partially_issued"].includes(p.status);
  const closed = issued || p.status === "cancelled";
  const draft = p.status === "open";
  const dirty = Object.keys(edits).length > 0 || (notes !== null && notes !== (p.notes || ""));
  const save = async () => {
    setSaving(true);
    try {
      await api.post(`/api/v1/pick-lists/${p.id}/lines`, { lines: Object.entries(edits).map(([line_id, v]) => ({ line_id, qty_to_pick: v === "" ? "0" : v })), ...(notes !== null ? { notes } : {}) }, true);
      toast.success("Load form saved");
      setEdits({}); setNotes(null);
      q.refetch();
    } catch (e: any) { toast.error(e.message); } finally { setSaving(false); }
  };
  const doScan = async () => { if (!scan.trim()) return; try { await run("scans", { scans: [{ code: scan.trim(), qty: 1 }] }, { silent: true }); } catch { /* shown */ } setScan(""); };
  return (
    <>
      <IssueModal open={issuing} onClose={() => setIssuing(false)} picklist={p} onIssued={() => { setIssuing(false); q.refetch(); }} />
      <PageHeader title={p.number} badge={<StatusBadge status={p.status} label={p.status_label} />} crumbs={[{ label: "Load forms", to: "/app/delivery/pick-lists" }, { label: p.number }]}
        actions={<>
          {draft && <button className="btn btn-outline-primary" disabled={saving || !dirty} onClick={save}><i className="ti ti-device-floppy me-1" />{saving ? "Saving…" : "Save"}</button>}
          {draft && <button className="btn btn-primary" disabled={!!busy || saving || dirty} title={dirty ? "Save your changes first" : ""} onClick={() => run("send", {}, { success: "Sent to the warehouse" })}><i className="ti ti-send me-1" />Send to warehouse</button>}
          {!draft && !issued && p.status !== "cancelled" && <button className="btn btn-success" disabled={!!busy} onClick={() => setIssuing(true)}><i className="ti ti-package-export me-1" />Issue stock</button>}
          {/* no Cancel: a load form goes with its order's Cancel (owner, 12 Nov 2026, Scenario 1); no Create dispatch: trips start from the Dispatch page */}
          <button className="btn btn-outline-secondary" onClick={() => printPdf(`/api/v1/pick-lists/${p.id}/pdf`)}><i className="ti ti-printer me-1" />Print</button><button className="btn btn-outline-secondary" onClick={() => savePdf(`/api/v1/pick-lists/${p.id}/pdf`, `${p.number}.pdf`)}><i className="ti ti-file-type-pdf me-1" />PDF</button>
        </>} />
      <div className="card mb-3"><div className="card-body py-2">
        <div className="row g-3 fs-12">
          <div className="col-md-3"><div className="text-muted">Warehouse</div><div className="fw-medium">{p.warehouse_name}</div></div>
          <div className="col-md-3"><div className="text-muted">{p.orders?.length > 1 ? "Orders" : "Order"}</div><div className="fw-medium">{p.order_numbers?.join(", ") || "—"}</div></div>
          <div className="col-md-3"><div className="text-muted">Sent to warehouse</div><div className="fw-medium">{p.sent_at ? `${datetime(p.sent_at)}${p.sent_by_name ? ` · ${p.sent_by_name}` : ""}` : "not sent yet"}</div></div>
          <div className="col-md-3"><div className="text-muted">Issued</div><div className="fw-medium">{p.issued_at ? `${datetime(p.issued_at)}${p.issued_by_name ? ` · ${p.issued_by_name}` : ""}` : "—"}</div></div>
        </div>
        {p.delivery_note_numbers?.length > 0 && <div className="fs-12 mt-2">Delivery notes: {p.delivery_note_numbers.map((d: any) => <Link key={d.id} to={`/app/delivery/notes/${d.id}`} className="me-2">{d.number}</Link>)}</div>}
      </div></div>
      {draft && <Alert kind="info"><strong>Saved — not sent to the warehouse yet.</strong> Check the quantities below and change any that are wrong (0 takes a line off), press <strong>Save</strong>, then <strong>Send to warehouse</strong>.</Alert>}
      {draft && <div className="card mb-3"><div className="card-body py-2"><label className="form-label fs-12 mb-1">Notes for the warehouse</label><textarea className="form-control form-control-sm" rows={2} value={notes ?? (p.notes || "")} onChange={(e) => setNotes(e.target.value)} placeholder="optional" /></div></div>}
      {!closed && !draft && <div className="card mb-3 scanner-mode"><div className="card-body"><label className="form-label">Scan items (each scan adds 1 base unit or one packaging)</label><input className="form-control" autoFocus placeholder="Scan barcode…" value={scan} onChange={(e) => setScan(e.target.value)} onKeyDown={(e) => e.key === "Enter" && doScan()} /></div></div>}
      {p.orders?.length === 1 && <WorkflowStepper orderId={p.orders[0]} compact />}
      <div className="card"><div className="card-body p-0"><table className="table align-middle mb-0 fs-13"><thead className="table-light"><tr><th>Item</th><th>Batch</th><th>Order</th><th className="num">Requested</th><th className="num">Issued</th><th /></tr></thead>
        <tbody>{p.lines.map((l: any) => {
          const v = edits[l.id];
          const removed = draft && v !== undefined && Number(v || 0) === 0;
          return <tr key={l.id} className={removed ? "table-danger" : !draft && Number(l.qty_picked) >= Number(l.qty_to_pick) ? "table-success" : ""}>
            <td>{l.variant_name}<div className="text-muted fs-11">{l.variant_sku}{removed ? " · will be taken off" : ""}</div></td><td>{l.batch_no}</td><td>{l.order_number}</td>
            <td className="num">{draft
              ? <input type="number" step="any" min="0" className="form-control form-control-sm text-end ms-auto" style={{ width: 110 }} value={v ?? String(Number(l.qty_to_pick))} onChange={(e) => setEdits({ ...edits, [l.id]: e.target.value })} />
              : qty(l.qty_to_pick)}</td>
            <td className="num">{qty(l.qty_picked)}</td>
            <td className="text-end">{draft
              ? <button className="btn btn-sm btn-outline-danger" title="Take this line off" onClick={() => setEdits({ ...edits, [l.id]: "0" })}><i className="ti ti-x" /></button>
              : !closed && <button className="btn btn-sm btn-outline-primary" onClick={() => run("picks", { picks: [{ line_id: l.id, qty_picked: l.qty_to_pick }] }, { silent: true })}>Pick all</button>}</td>
          </tr>;
        })}</tbody></table></div></div>
    </>
  );
}

/** Warehouse issue: what actually leaves the shelf. Confirming reduces stock and opens the delivery note. */
function IssueModal({ open, onClose, picklist, onIssued }: { open: boolean; onClose: () => void; picklist: any; onIssued: () => void }) {
  const [rows, setRows] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // what each item can take out of the warehouse now (TDN-PL-00214, 11 Nov 2026: the window offered 2 of an item with 0
  // left, and every Confirm Issue was refused): shown beside the request, and Issued starts at no more than that
  const stock = useQuery<{ lines: Record<string, string> | null }>({ queryKey: ["load-form-in-stock", picklist?.id], queryFn: () => api.get(`/api/v1/pick-lists/${picklist.id}/in-stock`), enabled: open && !!picklist?.id, staleTime: 0 });
  const have = stock.data?.lines;
  useEffect(() => {
    if (!open || stock.isFetching) return;
    setRows((picklist?.lines || []).map((l: any) => {
      const want = Number(l.qty_picked) > 0 ? l.qty_picked : l.qty_to_pick;
      const there = have ? have[l.id] : undefined;
      return { line_id: l.id, name: l.variant_name, sku: l.variant_sku, order: l.order_number, requested: l.qty_to_pick, in_stock: there,
               qty_issued: String(Number(there !== undefined && Number(there) < Number(want) ? there : want)) };
    }));
  }, [open, picklist, have, stock.isFetching]);
  const short = rows.filter((r) => Number(r.qty_issued || 0) < Number(r.requested));
  const nothing = rows.every((r) => Number(r.qty_issued || 0) <= 0);
  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      await api.post(`/api/v1/pick-lists/${picklist.id}/issue`, { issues: rows.map((r) => ({ line_id: r.line_id, qty_issued: String(r.qty_issued || 0) })) }, true);
      toast.success(short.length ? "Issued (short on some items) — stock updated" : "Issued — stock updated");
      onIssued();
    } catch (e: any) { setErr(e.message); toast.error(e.message); stock.refetch(); } finally { setBusy(false); }
  };
  return (
    <Modal open={open} onClose={onClose} size="lg" title={<><i className="ti ti-package-export me-2 text-primary" />Warehouse issue — {picklist?.number}</>}
      footer={<><button className="btn btn-light" onClick={onClose}>Cancel</button><button className="btn btn-success" disabled={busy || nothing || stock.isFetching} onClick={submit}>{busy ? "Issuing…" : "Confirm issue"}</button></>}>
      {err && <div className="alert alert-danger py-2 fs-13">{err}</div>}
      <div className="fs-13 text-muted mb-2">Enter what the warehouse is handing over. On confirm the stock leaves {picklist?.warehouse_name}, a delivery note is created per order and the order moves to <strong>Issued</strong> — ready to invoice and dispatch.</div>
      <table className="table table-sm fs-12 align-middle">
        <thead className="table-light"><tr><th>Item</th><th>Order</th><th className="num">Requested</th>{have && <th className="num">{labelCase("in stock")}</th>}<th style={{ width: 130 }}>Issued</th><th style={{ width: 90 }} /></tr></thead>
        <tbody>{rows.map((r, i) => (
          <tr key={r.line_id} className={Number(r.qty_issued || 0) < Number(r.requested) ? "table-warning" : ""}>
            <td>{r.name}<div className="text-muted fs-11">{r.sku}</div></td>
            <td>{r.order}</td>
            <td className="num">{qty(r.requested)}</td>
            {have && <td className={`num ${r.in_stock !== undefined && Number(r.in_stock) < Number(r.requested) ? "text-danger fw-semibold" : "text-muted"}`}>{r.in_stock !== undefined ? qty(r.in_stock) : ""}</td>}
            <td><input type="number" step="any" min="0" className="form-control form-control-sm" value={r.qty_issued} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, qty_issued: e.target.value } : x)))} /></td>
            <td className="text-end"><button className="btn btn-sm btn-outline-secondary" onClick={() => setRows(rows.map((x, j) => (j === i ? { ...x, qty_issued: x.requested } : x)))}>All</button></td>
          </tr>
        ))}</tbody>
      </table>
      {short.length > 0 && <div className="alert alert-warning py-2 fs-12 mb-0"><i className="ti ti-alert-triangle me-1" />{short.length} item(s) short of the request — the load form will be marked <strong>partially issued</strong> and the rest stays on the order.</div>}
    </Modal>
  );
}


