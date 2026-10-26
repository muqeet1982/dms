import { DateInput } from "@/components/ui/DateInput";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useAuth } from "@/lib/auth";
import { date, datetime, fbrRateLabel, money, qty } from "@/lib/format";
import { usePerms } from "@/lib/perms";
import { toast } from "@/lib/toast";
import { Alert, EmptyState, Field, PageHeader, Spinner, StatCard, StatusBadge } from "@/components/ui/Bits";
import { EntitySelect } from "@/components/ui/EntitySelect";
import { ConfirmDialog, Modal } from "@/components/ui/Modal";
import { useVisit, VisitCard } from "./Visit";

/** Start / Break / Back / End — the owner's shift model, on the web portal the same as the app.
 * No shift started, no order submitted: the server refuses, so this card is how the web rep gets going. */
export function ShiftCard() {
  const qc = useQueryClient();
  const my = useQuery({ queryKey: ["my-areas"], queryFn: () => api.get("/ext/v1/my-areas") });
  const q = useQuery({ queryKey: ["rep-shift"], queryFn: () => api.get("/ext/v1/shifts/current"), refetchInterval: 60000 });
  const s = q.data?.shift;
  if (!my.data?.shift?.enabled) return null;
  const act = async (path: string, body: any = {}) => {
    try {
      let gps: any;
      try { const pos: any = await new Promise((res, rej) => navigator.geolocation?.getCurrentPosition(res, rej, { timeout: 4000 })); gps = { lat: pos.coords.latitude, lng: pos.coords.longitude, accuracy_m: pos.coords.accuracy }; } catch { /* no fix — the server records that too */ }
      await api.post(path, { ...body, at: new Date().toISOString(), gps });
      qc.invalidateQueries({ queryKey: ["rep-shift"] });
    } catch (e: any) { toast.error(e.message); }
  };
  const open = s && s.status !== "ended";
  return (
    <div className="card mb-3"><div className="card-body d-flex flex-wrap align-items-center justify-content-between gap-2">
      <div>
        <strong>{!open ? "Off shift" : s.status === "break" ? "On a break" : "On shift"}</strong>
        <div className="fs-12 text-muted">
          {!open && s?.status === "ended" && <>Last shift ended {datetime(s.ended_at)}{s.end_reason === "tracking_lost" ? " — your office stopped receiving your location" : s.end_reason === "auto_cutoff" ? " — closed at the daily cutoff" : ""}</>}
          {!open && !s && "Start your shift to send orders."}
          {open && <>Started {datetime(s.started_at)}{s.corrected && " · times corrected by the office"}</>}
        </div>
      </div>
      <div className="d-flex gap-2">
        {!open && <button className="btn btn-success" onClick={() => act("/ext/v1/shifts", { client_id: (crypto as any).randomUUID?.() || String(Date.now()), device: { model: "web portal", os: navigator.userAgent.slice(0, 40), app_version: "web" }, checks: { location_services: Boolean(navigator.geolocation), background_permission: false, notifications: false, battery_unrestricted: false } })}><i className="ti ti-player-play me-1" />Start Shift</button>}
        {open && s.status === "on" && <button className="btn btn-outline-secondary" onClick={() => act(`/ext/v1/shifts/${s.id}/break`)}><i className="ti ti-coffee me-1" />Break</button>}
        {open && s.status === "break" && <button className="btn btn-primary" onClick={() => act(`/ext/v1/shifts/${s.id}/resume`)}><i className="ti ti-player-play me-1" />Back From Break</button>}
        {open && <button className="btn btn-outline-danger" onClick={() => act(`/ext/v1/shifts/${s.id}/end`, { reason: "rep" })}><i className="ti ti-player-stop me-1" />End Shift</button>}
      </div>
    </div></div>
  );
}

export function RepHomePage() {
  const me = useAuth((s) => s.me);
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api.get("/api/v1/dashboard") });
  const orders = useQuery({ queryKey: ["rep-orders-recent"], queryFn: () => api.get("/ext/v1/orders", { limit: 8 }) });
  const r = q.data?.rep;
  const pct = r && Number(r.target) ? Math.min(100, (Number(r.order_value_month) / Number(r.target)) * 100) : 0;
  return (
    <>
      <PageHeader title={`Hi ${me?.user.full_name?.split(" ")[0]}`} crumbs={[]} actions={<Link to="/rep/orders/new" className="btn btn-primary"><i className="ti ti-shopping-cart-plus me-1" />New order</Link>} />
      <ShiftCard />
      {r && <div className="row g-3 mb-3"><div className="col-6 col-md-3"><StatCard label="Orders this month" value={r.orders_month} icon="ti-clipboard-list" color="primary" sub={`${r.pending} pending`} /></div><div className="col-6 col-md-3"><StatCard label="Order value" value={money(r.order_value_month, 0)} icon="ti-cash" color="success" sub={Number(r.target) ? `target ${money(r.target, 0)}` : ""} /></div><div className="col-6 col-md-3"><StatCard label="Cash with me" value={money(r.cash_held, 0)} icon="ti-wallet" color={Number(r.cash_held) > 0 ? "warning" : "secondary"} to="/rep/collections" /></div><div className="col-6 col-md-3"><StatCard label="My customers" value={r.customers} icon="ti-users" color="info" to="/rep/customers" /></div></div>}
      {r && Number(r.target) > 0 && <div className="card mb-3"><div className="card-body"><div className="d-flex justify-content-between fs-13 mb-1"><span>Monthly target</span><span>{pct.toFixed(0)}%</span></div><div className="progress" style={{ height: 10 }}><div className={`progress-bar ${pct >= 100 ? "bg-success" : "bg-primary"}`} style={{ width: `${pct}%` }} /></div></div></div>}
      <div className="card"><div className="card-header d-flex justify-content-between"><strong>Recent orders</strong><Link to="/rep/orders" className="fs-12">All orders</Link></div><div className="list-group list-group-flush">{orders.data?.results.map((o: any) => <Link key={o.id} to={`/rep/orders/${o.id}`} className="list-group-item list-group-item-action"><div className="d-flex justify-content-between"><span className="fw-medium">{o.customer_name}</span><StatusBadge status={o.status} /></div><div className="fs-12 text-muted">{o.number} · {date(o.order_date)} · {money(o.total)}</div></Link>)}{orders.data && !orders.data.results.length && <div className="p-4"><EmptyState text="No orders yet" action={<Link to="/rep/orders/new" className="btn btn-primary btn-sm">Place your first order</Link>} /></div>}</div></div>
    </>
  );
}

export function RepCustomersPage() {
  const { can } = usePerms();
  const [search, setSearch] = useState("");
  const [add, setAdd] = useState(false);
  const [form, setForm] = useState({ name: "", type: "retailer", phone: "", address: "", city: "", map_url: "", ntn: "", cnic: "" });
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["rep-customers", search], queryFn: () => api.get("/ext/v1/customers", { search: search || undefined }) });
  return (
    <>
      <PageHeader title="My customers" crumbs={[]} actions={can("external.add_customer_request") && <button className="btn btn-primary" onClick={() => setAdd(true)}><i className="ti ti-user-plus me-1" />Add customer</button>} />
      <div className="card mb-3"><div className="card-body py-2"><input className="form-control" placeholder="Search customers…" value={search} onChange={(e) => setSearch(e.target.value)} /></div></div>
      {q.isLoading ? <Spinner /> : !q.data?.results?.length
        ? <EmptyState text={search ? "No shop matches that." : "No shops are assigned to you yet — the office assigns them, and they appear here."} />
        : <div className="row g-3">{q.data.results.map((c: any) => (
        <div className="col-md-6 col-xl-4" key={c.id}><div className="card h-100 mb-0"><div className="card-body"><div className="d-flex justify-content-between"><Link to={`/rep/customers/${c.id}`} className="fw-semibold">{c.name}</Link><StatusBadge status={c.is_blocked ? "blocked" : c.status} /></div><div className="fs-12 text-muted">{c.code} · {c.type} · {c.route || c.territory || ""}</div><div className="fs-13 mt-1"><i className="ti ti-phone me-1" /><a href={`tel:${c.phone}`}>{c.phone}</a></div><div className="fs-12 text-muted">{c.address}</div>{c.outstanding !== undefined && c.outstanding !== null && <div className="fs-13 mt-1">Outstanding <strong className={Number(c.outstanding) > 0 ? "text-danger" : ""}>{money(c.outstanding)}</strong>{Number(c.overdue) > 0 && <span className="text-danger fs-11"> · overdue {money(c.overdue)}</span>}</div>}<div className="d-flex gap-2 mt-2"><Link to={`/rep/orders/new?customer=${c.id}`} className="btn btn-sm btn-primary">Order</Link>{can("external.record_collection") && <Link to={`/rep/collections?customer=${c.id}`} className="btn btn-sm btn-outline-success">Collect</Link>}{c.gps && <a className="btn btn-sm btn-outline-secondary" href={`https://maps.google.com/?q=${c.gps.lat},${c.gps.lng}`} target="_blank" rel="noreferrer"><i className="ti ti-map-pin" /></a>}</div></div></div></div>
      ))}</div>}
      <Modal open={add} title="Request a new customer" onClose={() => setAdd(false)} size="sm" footer={<><button className="btn btn-light" onClick={() => setAdd(false)}>Cancel</button><button className="btn btn-primary" onClick={async () => { try { let gps; try { const pos: any = await new Promise((res, rej) => navigator.geolocation?.getCurrentPosition(res, rej, { timeout: 3000 })); gps = { lat: pos.coords.latitude, lng: pos.coords.longitude, accuracy_m: pos.coords.accuracy }; } catch { /* no fix — the map link can still pin it */ } const r = await api.post("/ext/v1/customers", { ...form, map_url: form.map_url || undefined, gps }); toast.success(r.status === "pending" ? "Submitted for approval" : "Customer added"); setAdd(false); qc.invalidateQueries({ queryKey: ["rep-customers"] }); } catch (e: any) { toast.error(e.message); } }}>Submit</button></>}>
        <Field label="Shop / salon name *"><input className="form-control" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field><Field label="Type"><select className="form-select" value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}><option value="retailer">Retailer</option><option value="salon">Salon</option><option value="wholesaler">Wholesaler</option></select></Field><Field label="Phone"><input className="form-control" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></Field><Field label="Address"><input className="form-control" value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} /></Field><Field label="City"><input className="form-control" value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })} /></Field><Field label="NTN" hint="NTN or CNIC — one of the two is required"><input className="form-control" value={form.ntn} onChange={(e) => setForm({ ...form, ntn: e.target.value })} placeholder="1234567-8" /></Field><Field label="CNIC"><input className="form-control" value={form.cnic} onChange={(e) => setForm({ ...form, cnic: e.target.value })} placeholder="35202-1234567-1" /></Field><Field label="Shop location" hint="paste a Google Maps link or lat, lng — otherwise your current position pins it"><input className="form-control" value={form.map_url} onChange={(e) => setForm({ ...form, map_url: e.target.value })} placeholder="https://maps.app.goo.gl/… or 31.52, 74.33" /></Field>
      </Modal>
    </>
  );
}

export function RepCustomerDetailPage() {
  const { id } = useParams();
  const { can } = usePerms();
  const q = useQuery({ queryKey: ["rep-customer", id], queryFn: async () => (await api.get("/ext/v1/customers")).results.find((c: any) => c.id === id) });
  const orders = useQuery({ queryKey: ["rep-customer-orders", id], queryFn: () => api.get("/ext/v1/orders", { customer: id, limit: 30 }) });
  const stmt = useQuery({ queryKey: ["rep-customer-statement", id], queryFn: () => api.get(`/api/v1/customers/${id}/statement`), enabled: can("external.view_customer_outstanding") });
  if (q.isLoading) return <Spinner />;
  const c = q.data;
  if (!c) return <Alert kind="danger">Customer not found or not assigned to you.</Alert>;
  return (
    <>
      <PageHeader title={c.name} badge={<StatusBadge status={c.is_blocked ? "blocked" : c.status} />} crumbs={[]} actions={<><Link to={`/rep/orders/new?customer=${c.id}`} className="btn btn-primary">New order</Link>{can("external.record_collection") && <Link to={`/rep/collections?customer=${c.id}`} className="btn btn-outline-success">Collect cash</Link>}</>} />
      <div className="card mb-3"><div className="card-body fs-13"><div><i className="ti ti-phone me-1" /><a href={`tel:${c.phone}`}>{c.phone}</a></div><div><i className="ti ti-map-pin me-1" />{c.address} {c.city}</div><div className="text-muted">{c.code} · {c.type} · {c.route || ""} · price list {c.price_list || "default"} · credit days {c.credit_days}</div>{c.outstanding !== undefined && c.outstanding !== null && <div className="mt-2">Outstanding <strong className="text-danger">{money(c.outstanding)}</strong> · limit {money(c.credit_limit, 0)}</div>}</div></div>
      {stmt.data && <div className="card mb-3"><div className="card-header"><strong>Open invoices</strong></div><div className="card-body p-0"><table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>Invoice</th><th>Date</th><th>Due</th><th className="num">Balance</th></tr></thead><tbody>{stmt.data.open_invoices.map((i: any) => <tr key={i.id}><td>{i.number}</td><td>{date(i.date)}</td><td>{i.due_date && i.due_date !== i.date ? date(i.due_date) : "—"}</td><td className="num fw-semibold">{money(i.balance)}</td></tr>)}{!stmt.data.open_invoices.length && <tr><td colSpan={4} className="text-muted text-center py-3">Nothing outstanding</td></tr>}</tbody></table></div></div>}
      <div className="card"><div className="card-header"><strong>Orders</strong></div><div className="list-group list-group-flush">{orders.data?.results.map((o: any) => <Link key={o.id} to={`/rep/orders/${o.id}`} className="list-group-item list-group-item-action d-flex justify-content-between"><span>{o.number} · {date(o.order_date)} · {money(o.total)}</span><StatusBadge status={o.status} /></Link>)}</div></div>
    </>
  );
}

export function RepNewOrderPage() {
  const nav = useNavigate();
  const sp = new URLSearchParams(window.location.search);
  const { can } = usePerms();
  // /rep/orders/:id/edit reuses this page: same basket, but Save corrects the existing order (PATCH)
  const { id: editId } = useParams();
  const [customer, setCustomer] = useState<string | null>(sp.get("customer"));
  const [cart, setCart] = useState<any[]>([]);
  const [search, setSearch] = useState("");
  const [notes, setNotes] = useState("");
  const editing = useQuery({ queryKey: ["rep-order", editId], queryFn: () => api.get(`/ext/v1/orders/${editId}`), enabled: Boolean(editId) });
  const [seeded, setSeeded] = useState(false);
  if (editId && editing.data && !seeded) {
    const o = editing.data;
    setCustomer(o.customer);
    setNotes(o.notes || "");
    setCart(o.lines.map((l: any) => ({ key: l.id, variant_id: l.variant, name: l.variant_name, sku: l.variant_sku, packaging: undefined, packaging_id: undefined, mult: 1, price: l.unit_price ? Number(l.unit_price) : null, qty: Number(l.qty_ordered) })));
    setSeeded(true);
  }
  const [deliveryDate, setDeliveryDate] = useState("");
  const [scan, setScan] = useState("");
  const visit = useVisit();   // where the rep is standing, and the photo of the shop
  const products = useQuery({ queryKey: ["rep-products", customer, search], queryFn: () => api.get("/ext/v1/products", { customer: customer || undefined, search: search || undefined }), enabled: Boolean(customer) });
  const add = (p: any, packaging?: any) => { const key = `${p.id}:${packaging?.id || ""}`; const ex = cart.find((c) => c.key === key); if (ex) setCart(cart.map((c) => (c.key === key ? { ...c, qty: c.qty + 1 } : c))); else setCart([...cart, { key, variant_id: p.id, name: p.name, sku: p.sku, packaging: packaging?.name, packaging_id: packaging?.id, mult: packaging ? Number(packaging.qty_in_base) : 1, price: p.price ? Number(p.price) * (packaging ? Number(packaging.qty_in_base) : 1) : null, qty: 1 }]); };
  const doScan = async () => { if (!scan.trim()) return; try { const r = await api.get("/api/v1/barcodes/resolve", { code: scan.trim() }); const p = products.data?.results.find((x: any) => x.id === r.variant.id) || { ...r.variant, price: r.variant.sale_price }; add(p, r.packaging); toast.success(`Added ${r.variant.name}`); } catch (e: any) { toast.error(e.message); } setScan(""); };
  const total = cart.reduce((s, c) => s + (c.price || 0) * c.qty, 0);
  const submit = async () => {
    if (!customer || !cart.length) return toast.error("Choose a customer and add items");
    try {
      if (editId) {
        const r = await api.patch(`/ext/v1/orders/${editId}`, {
          notes, requested_delivery_date: deliveryDate || undefined,
          lines: cart.map((c) => ({ variant_id: c.variant_id, qty: c.qty, packaging: c.packaging || undefined })),
        });
        toast.success(`Order ${r.number} updated`);
        return nav(`/rep/orders/${r.id}`);
      }
      const r = await api.post("/ext/v1/orders", {
        customer: { id: customer }, requested_delivery_date: deliveryDate || undefined, notes,
        gps: visit.gps || undefined,
        attachments: visit.photo ? [visit.photo] : undefined,
        lines: cart.map((c) => ({ variant_id: c.variant_id, qty: c.qty, packaging: c.packaging || undefined })),
      }, true);
      toast.success(`Order ${r.number} submitted`);
      nav(`/rep/orders/${r.id}`);
    } catch (e: any) { toast.error(e.message); }
  };
  return (
    <>
      <PageHeader title={editId ? `Correct ${editing.data?.number || "order"}` : "New order"} crumbs={[]} />
      {editId ? <Alert kind="info">You are correcting this order — the whole basket below replaces its lines when you save. No location check on a correction.</Alert> : <VisitCard visit={visit} />}
      <div className="card mb-3"><div className="card-body"><Field label="Customer *"><EntitySelect endpoint="/ext/v1/customers" value={customer} onChange={setCustomer} mapOption={(c) => ({ id: c.id, label: c.name, sub: `${c.code} · ${c.city || ""}` })} /></Field><div className="row"><Field label="Requested delivery" col="col-6"><DateInput className="form-control" value={deliveryDate} onChange={(e) => setDeliveryDate(e.target.value)} /></Field><Field label="Notes" col="col-6"><input className="form-control" value={notes} onChange={(e) => setNotes(e.target.value)} /></Field></div></div></div>
      <div className="row g-3">
        <div className="col-lg-7">
          <div className="card"><div className="card-header"><div className="input-group"><span className="input-group-text"><i className="ti ti-scan" /></span><input className="form-control" placeholder="Scan barcode…" value={scan} onChange={(e) => setScan(e.target.value)} onKeyDown={(e) => e.key === "Enter" && doScan()} /><input className="form-control" placeholder="Search products…" value={search} onChange={(e) => setSearch(e.target.value)} /></div></div>
            <div className="list-group list-group-flush" style={{ maxHeight: 560, overflowY: "auto" }}>{!customer && <div className="p-4 text-muted text-center">Select a customer to see prices</div>}{products.data?.results.map((p: any) => <div key={p.id} className="list-group-item d-flex justify-content-between align-items-center gap-2"><div><div className="fw-medium fs-13">{p.name}</div><div className="fs-11 text-muted">{p.sku}{p.unit_size ? ` · ${p.unit_size}` : ""}{p.packing > 1 ? ` · ${p.packing}/ctn` : ""} · {p.brand_name}{p.price ? ` · ${money(p.price)} / ${p.base_uom}` : ""}{p.retail_price && Number(p.retail_price) > 0 ? ` · R.P ${money(p.retail_price)}` : ""}</div></div><div className="d-flex gap-1 flex-wrap justify-content-end"><button className="btn btn-sm btn-outline-primary" onClick={() => add(p)}>+1 {p.base_uom}</button>{p.packagings?.map((pk: any) => <button key={pk.id} className="btn btn-sm btn-outline-secondary" onClick={() => add(p, pk)}>+{pk.name} ({Number(pk.qty_in_base)})</button>)}</div></div>)}</div></div>
        </div>
        <div className="col-lg-5">
          <div className="card"><div className="card-header d-flex justify-content-between"><strong>Cart</strong><span>{cart.length} items</span></div><div className="card-body p-0"><table className="table table-sm fs-13 mb-0"><tbody>{cart.map((c) => <tr key={c.key}><td>{c.name}<div className="fs-11 text-muted">{c.packaging ? `${c.packaging} ×${c.mult}` : "unit"}{c.price !== null ? ` · ${money(c.price)}` : ""}</div></td><td style={{ width: 120 }}><div className="input-group input-group-sm"><button className="btn btn-outline-secondary" onClick={() => setCart(cart.map((x) => (x.key === c.key ? { ...x, qty: Math.max(0, x.qty - 1) } : x)).filter((x) => x.qty > 0))}>−</button><input className="form-control text-center" value={c.qty} onChange={(e) => setCart(cart.map((x) => (x.key === c.key ? { ...x, qty: Number(e.target.value) || 0 } : x)))} /><button className="btn btn-outline-secondary" onClick={() => setCart(cart.map((x) => (x.key === c.key ? { ...x, qty: x.qty + 1 } : x)))}>+</button></div></td><td className="num">{c.price !== null ? money(c.price * c.qty) : ""}</td></tr>)}{!cart.length && <tr><td className="text-muted text-center py-4">Cart is empty</td></tr>}</tbody>{cart.length > 0 && can("external.view_prices") && <tfoot><tr className="fw-bold"><td colSpan={2} className="text-end">Approx. total (before tax/schemes)</td><td className="num">{money(total)}</td></tr></tfoot>}</table></div><div className="card-footer"><button className="btn btn-primary w-100" disabled={!cart.length || !customer} onClick={submit}><i className="ti ti-send me-1" />{editId ? "Save correction" : "Submit order"}</button><div className="form-text text-center">Final prices, schemes and taxes are calculated by the distributor.</div></div></div>
        </div>
      </div>
    </>
  );
}

export function RepOrdersPage() {
  const [status, setStatus] = useState("");
  const q = useQuery({ queryKey: ["rep-orders", status], queryFn: () => api.get("/ext/v1/orders", { status: status || undefined, limit: 100 }) });
  return (
    <>
      <PageHeader title="My orders" crumbs={[]} actions={<select className="form-select w-auto" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All</option>{["submitted", "under_review", "confirmed", "on_hold_credit", "on_hold_stock", "dispatched", "delivered", "invoiced", "rejected", "cancelled"].map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}</select>} />
      {q.isLoading ? <Spinner /> : <div className="card"><div className="list-group list-group-flush">{q.data?.results.map((o: any) => <Link key={o.id} to={`/rep/orders/${o.id}`} className="list-group-item list-group-item-action"><div className="d-flex justify-content-between"><span className="fw-medium">{o.customer_name}</span><StatusBadge status={o.status} /></div><div className="fs-12 text-muted">{o.number} · {date(o.order_date)} · {money(o.total)}{o.trip_number ? ` · trip ${o.trip_number}` : ""}</div>{o.hold_reason && <div className="fs-12 text-warning">{o.hold_reason}</div>}</Link>)}{q.data && !q.data.results.length && <div className="p-4"><EmptyState text="No orders" /></div>}</div></div>}
    </>
  );
}

export function RepOrderDetailPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const { can } = usePerms();
  const q = useQuery({ queryKey: ["rep-order", id], queryFn: () => api.get(`/ext/v1/orders/${id}`) });
  const [cancel, setCancel] = useState(false);
  if (q.isLoading) return <Spinner />;
  const o = q.data;
  if (!o) return null;
  return (
    <>
      <PageHeader title={o.number} badge={<StatusBadge status={o.status} />} crumbs={[]} actions={<>
        <button className="btn btn-outline-secondary" onClick={() => api.printDoc(`/ext/v1/orders/${o.id}/pdf`)}><i className="ti ti-printer me-1" />Print</button>
        <button className="btn btn-outline-secondary" onClick={() => api.download(`/ext/v1/orders/${o.id}/pdf`)}><i className="ti ti-file-type-pdf me-1" />PDF</button>
        {o.invoices?.length > 0 && <button className="btn btn-outline-secondary" onClick={() => api.download(`/ext/v1/orders/${o.id}/invoice-pdf`)}><i className="ti ti-file-invoice me-1" />Invoice PDF</button>}
        {o.can_edit && <Link to={`/rep/orders/${o.id}/edit`} className="btn btn-outline-primary"><i className="ti ti-pencil me-1" />Correct order</Link>}
        {o.status === "submitted" && can("external.cancel_own_order") && <button className="btn btn-outline-danger" onClick={() => setCancel(true)}>Cancel order</button>}
      </>} />
      {o.can_edit && o.editable_until && <div className="fs-12 text-muted mb-2"><i className="ti ti-clock me-1" />Corrections allowed until {datetime(o.editable_until)}.</div>}
      {o.rejection_reason && <Alert kind="danger">{o.rejection_reason}</Alert>}{o.hold_reason && <Alert kind="warning">{o.hold_reason}</Alert>}
      <div className="card mb-3"><div className="card-body fs-13"><div><strong>{o.customer_name}</strong> · {date(o.order_date)}{o.requested_delivery_date ? ` · deliver by ${date(o.requested_delivery_date)}` : ""}</div>{o.trip_number && <div className="text-muted">Trip {o.trip_number} · {o.driver_name} · {o.vehicle_reg}</div>}{o.notes && <div className="text-muted">{o.notes}</div>}
        {(() => {
          const prices = can("external.view_prices");
          // a tax customer's order reads like the office's (owner, 26 Oct 2026): Taxable → GST → Line Total
          const taxed = prices && Boolean(o.charge_tax);
          const taxTotal = (Number(o.tax1_total) || 0) + (Number(o.tax2_total) || 0);
          const span = taxed ? 6 : 4;
          const row = (label: string, v: any, bold = false) => <tr className={bold ? "fw-bold" : ""}><td colSpan={span} className="text-end">{label}</td><td className="num">{v}</td><td /></tr>;
          return <table className="table table-sm fs-12 mt-2 mb-0"><thead className="table-light"><tr><th>Item</th><th className="num">Qty</th><th className="num">Free</th>{prices && <th className="num">Price</th>}{taxed && <><th className="num">Taxable</th><th className="num">GST</th></>}{prices && <th className="num">{taxed ? "Line Total" : "Total"}</th>}<th className="num">Delivered</th></tr></thead>
            <tbody>{o.lines.map((l: any) => { const t = (Number(l.tax1_amount) || 0) + (Number(l.tax2_amount) || 0); return <tr key={l.id}><td>{l.variant_name}<div className="fs-11 text-muted">{l.variant_sku}{l.packaging_name ? ` · ${l.packaging_name}` : ""}{taxed && l.hs_code ? ` · HS ${l.hs_code}` : ""}</div></td><td className="num">{qty(l.qty_ordered)}</td><td className="num">{qty(l.free_qty)}</td>{prices && <td className="num">{money(l.unit_price)}</td>}{taxed && <><td className="num">{money(Number(l.line_total) - t)}</td><td className="num">{t ? <>{money(t)}{l.product_fbr_rate && <div className="text-muted fs-11">{fbrRateLabel(l.product_fbr_rate)}</div>}</> : "—"}</td></>}{prices && <td className="num">{money(l.line_total)}</td>}<td className="num">{qty(l.qty_delivered)}</td></tr>; })}</tbody>
            {prices && <tfoot>{taxed ? <>
              {row("Sub Total", money(o.subtotal))}
              {Number(o.discount) > 0 && row("Discount", `−${money(o.discount)}`)}
              {Number(o.scheme_discount) > 0 && row("Scheme", `−${money(o.scheme_discount)}`)}
              {row("Net Total", money(Number(o.total) - taxTotal))}
              {row("GST", money(taxTotal))}
              {row("Total Tax", money(taxTotal))}
              {row("Grand Total", money(o.total))}
              {row("Total Payable", money(o.total), true)}
            </> : row("Total (incl. tax)", money(o.total), true)}</tfoot>}
          </table>;
        })()}</div></div>
      <div className="card"><div className="card-body"><ul className="status-timeline mb-0"><li><div className="fs-13 fw-medium">Submitted</div><div className="fs-12 text-muted">{datetime(o.created_at)}</div></li>{o.confirmed_at && <li><div className="fs-13 fw-medium">Confirmed</div><div className="fs-12 text-muted">{datetime(o.confirmed_at)}</div></li>}{o.dispatched_at && <li><div className="fs-13 fw-medium">Dispatched</div><div className="fs-12 text-muted">{datetime(o.dispatched_at)}</div></li>}{o.delivered_at && <li><div className="fs-13 fw-medium">Delivered</div><div className="fs-12 text-muted">{datetime(o.delivered_at)}</div></li>}{o.invoices?.map((i: any) => <li key={i.id}><div className="fs-13 fw-medium">Invoice {i.number}</div><div className="fs-12 text-muted">{money(i.total)} · {i.status}</div></li>)}</ul></div></div>
      <ConfirmDialog open={cancel} title="Cancel this order?" withReason danger onClose={() => setCancel(false)} onConfirm={async (r) => { try { await api.post(`/ext/v1/orders/${id}/cancel`, { reason: r }); toast.success("Order cancelled"); qc.invalidateQueries({ queryKey: ["rep-order", id] }); } catch (e: any) { toast.error(e.message); } setCancel(false); }} />
    </>
  );
}

export function RepDeliveriesPage() {
  const q = useQuery({ queryKey: ["rep-deliveries"], queryFn: () => api.get("/ext/v1/deliveries") });
  return (
    <>
      <PageHeader title="Deliveries" crumbs={[]} />
      {q.isLoading ? <Spinner /> : <div className="card"><div className="list-group list-group-flush">{q.data?.results.map((d: any) => <div key={d.id} className="list-group-item"><div className="d-flex justify-content-between"><span className="fw-medium">{d.customer}</span><StatusBadge status={d.status} /></div><div className="fs-12 text-muted">{d.order} · {d.number}{d.trip ? ` · trip ${d.trip} · ${d.driver || ""} ${d.vehicle || ""}` : ""}</div>{d.delivered_at && <div className="fs-12">Delivered {datetime(d.delivered_at)} to {d.receiver_name}{Number(d.cash_collected) > 0 ? ` · cash ${money(d.cash_collected)}` : ""}</div>}</div>)}{q.data && !q.data.results.length && <div className="p-4"><EmptyState text="No deliveries yet" /></div>}</div></div>}
    </>
  );
}

export function RepCollectionsPage() {
  const qc = useQueryClient();
  const { can } = usePerms();
  const sp = new URLSearchParams(window.location.search);
  const [open, setOpen] = useState(Boolean(sp.get("customer")));
  const [form, setForm] = useState<any>({ customer: sp.get("customer"), amount: "", method: "cash", reference: "", notes: "" });
  const q = useQuery({ queryKey: ["rep-collections"], queryFn: () => api.get("/ext/v1/collections") });
  const invoices = useQuery({ queryKey: ["rep-open-inv", form.customer], queryFn: () => api.get("/api/v1/invoices", { customer: form.customer, open: 1, page_size: 50 }), enabled: Boolean(form.customer) && can("external.view_customer_outstanding") });
  return (
    <>
      <PageHeader title="Collections" crumbs={[]} actions={can("external.record_collection") && <button className="btn btn-primary" onClick={() => setOpen(true)}><i className="ti ti-cash me-1" />Record collection</button>} />
      <div className="row g-3 mb-3"><div className="col-6"><StatCard label="Cash with me (unsettled)" value={money(q.data?.held_total, 0)} icon="ti-wallet" color="warning" sub="hand over to the distributor's office" /></div></div>
      <div className="card"><div className="list-group list-group-flush">{q.data?.results.map((c: any) => <div key={c.id} className="list-group-item"><div className="d-flex justify-content-between"><span className="fw-medium">{c.customer_name}</span><span className="fw-semibold">{money(c.amount)}</span></div><div className="fs-12 text-muted">{c.number} · {date(c.date)} · {c.method}{c.reference ? ` · ${c.reference}` : ""} · {c.allocations.map((a: any) => a.number).join(", ") || "advance"}</div><StatusBadge status={c.status} />{c.settlement_number && <span className="fs-11 text-muted ms-2">{c.settlement_number}</span>}</div>)}{q.data && !q.data.results.length && <div className="p-4"><EmptyState text="No collections yet" /></div>}</div></div>
      <Modal open={open} title="Record cash collected" onClose={() => setOpen(false)} size="sm" footer={<><button className="btn btn-light" onClick={() => setOpen(false)}>Cancel</button><button className="btn btn-primary" disabled={!form.customer || !form.amount} onClick={async () => { try { let gps; try { const pos: any = await new Promise((res, rej) => navigator.geolocation?.getCurrentPosition(res, rej, { timeout: 3000 })); gps = { lat: pos.coords.latitude, lng: pos.coords.longitude }; } catch { /* no gps */ } const r = await api.post("/ext/v1/collections", { ...form, gps }, true); toast.success(`${r.number} recorded`); setOpen(false); setForm({ customer: null, amount: "", method: "cash", reference: "", notes: "" }); qc.invalidateQueries({ queryKey: ["rep-collections"] }); qc.invalidateQueries({ queryKey: ["dashboard"] }); } catch (e: any) { toast.error(e.message); } }}>Record</button></>}>
        <Field label="Customer *"><EntitySelect endpoint="/ext/v1/customers" value={form.customer} onChange={(v) => setForm({ ...form, customer: v })} mapOption={(c) => ({ id: c.id, label: c.name, sub: c.outstanding !== null && c.outstanding !== undefined ? `outstanding ${money(c.outstanding)}` : c.code })} /></Field>
        {invoices.data && invoices.data.results.length > 0 && <div className="fs-12 mb-2 text-muted">Open: {invoices.data.results.map((i: any) => `${i.number} ${money(i.balance)}`).join(", ")}</div>}
        <Field label="Amount *"><input type="number" step="0.01" className="form-control form-control-lg" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} /></Field><Field label="Method"><select className="form-select" value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value })}><option value="cash">Cash</option><option value="cheque">Cheque</option><option value="online">Online</option></select></Field><Field label="Reference / cheque no"><input className="form-control" value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} /></Field>
        <div className="form-text">Allocated to the oldest open invoices. Your GPS position is attached if available.</div>
      </Modal>
    </>
  );
}

export function RepStockPage() {
  const [search, setSearch] = useState("");
  const q = useQuery({ queryKey: ["rep-stock"], queryFn: () => api.get("/ext/v1/stock") });
  const rows = (q.data?.results || []).filter((r: any) => !search || r.name.toLowerCase().includes(search.toLowerCase()) || r.sku.toLowerCase().includes(search.toLowerCase()));
  const band = (b: string) => ({ out: "danger", low: "warning", medium: "info", high: "success" }[b] || "secondary");
  return (
    <>
      <PageHeader title="Stock availability" crumbs={[]} />
      <div className="card mb-3"><div className="card-body py-2"><input className="form-control" placeholder="Search…" value={search} onChange={(e) => setSearch(e.target.value)} /></div></div>
      <div className="card"><div className="card-body p-0">{q.isLoading ? <Spinner /> : <table className="table table-sm fs-13 mb-0"><tbody>{rows.map((r: any) => <tr key={r.variant_id}><td>{r.name}<div className="fs-11 text-muted">{r.sku}</div></td><td className="text-end">{r.available !== undefined ? <strong>{qty(r.available)}</strong> : <span className={`badge bg-${band(r.band)}-light text-${band(r.band)}`}>{r.band}</span>}</td></tr>)}</tbody></table>}</div></div>
    </>
  );
}

/**
 * Check in at a shop — the record a visit leaves when nothing is sold.
 *
 * The photo must be taken here and now, so the file input is camera-only (`capture="environment"`) and the
 * capture time goes with it; the server refuses a picture that was not taken in the last few minutes.
 */
export function RepCheckInPage() {
  const qc = useQueryClient();
  const visit = useVisit();
  const [params] = useSearchParams();
  const [customer, setCustomer] = useState<string | null>(params.get("customer"));
  const [outcome, setOutcome] = useState<"no_order" | "order">("no_order");
  const [reason, setReason] = useState("closed");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const mine = useQuery({ queryKey: ["rep-visits"], queryFn: () => api.get("/ext/v1/visits") });
  const rules = mine.data?.policy;
  const needPhoto = rules?.photo !== "optional" && rules?.photo !== "off";

  const submit = async () => {
    if (!customer) return toast.error("Which shop are you at?");
    if (needPhoto && !visit.photo) return toast.error("Take a photo of the shop first.");
    setSaving(true);
    try {
      await api.post("/ext/v1/visits", {
        customer, outcome, no_order_reason: outcome === "no_order" ? reason : "", note,
        checked_in_at: new Date().toISOString(),
        gps: visit.gps ? { ...visit.gps, accuracy_m: visit.accuracy } : undefined,
        photo: visit.photo, photo_taken_at: visit.photoAt, client_id: crypto.randomUUID(),
      }, true);
      toast.success("Checked in");
      setNote(""); visit.setPhoto(null);
      qc.invalidateQueries({ queryKey: ["rep-visits"] });
    } catch (e: any) {
      toast.error(e.message);
    }
    setSaving(false);
  };

  return (
    <>
      <PageHeader title="Check in" crumbs={[]} />
      <VisitCard visit={visit} required={needPhoto} />
      <div className="card mb-3"><div className="card-body">
        <Field label="Shop"><EntitySelect endpoint="/ext/v1/customers" value={customer} onChange={setCustomer} mapOption={(c) => ({ id: c.id, label: c.name, sub: `${c.code} · ${c.city || ""}` })} /></Field>
        <Field label="What happened">
          <div className="btn-group w-100" role="group">
            <button type="button" className={`btn btn-sm btn-outline-${outcome === "no_order" ? "danger active" : "secondary"}`} onClick={() => setOutcome("no_order")}>No order</button>
            <button type="button" className={`btn btn-sm btn-outline-${outcome === "order" ? "success active" : "secondary"}`} onClick={() => setOutcome("order")}>Took an order</button>
          </div>
        </Field>
        {outcome === "no_order" && (
          <Field label="Why">
            <select className="form-select" value={reason} onChange={(e) => setReason(e.target.value)}>
              {(rules?.reasons || []).map((r: any) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </select>
          </Field>
        )}
        <Field label="Note (optional)"><input className="form-control" value={note} maxLength={300} placeholder="Will try again Thursday" onChange={(e) => setNote(e.target.value)} /></Field>
        <button className="btn btn-primary w-100" disabled={saving} onClick={submit}>
          <i className="ti ti-map-pin-check me-1" />{saving ? "Saving…" : "Check in"}
        </button>
      </div></div>
      <div className="card"><div className="card-header py-2 fs-13 fw-medium">Today's check-ins</div><div className="card-body p-0">
        {mine.isLoading ? <Spinner /> : !(mine.data?.results || []).length
          ? <EmptyState text="No check-ins yet. Check in at a shop even when there is no order — it is how the office knows you were there." />
          : <table className="table table-sm fs-13 mb-0"><tbody>{mine.data.results.map((v: any) => (
              <tr key={v.id}>
                <td>{v.photo_url && <img src={v.photo_url} alt="" style={{ height: 32, width: 32, objectFit: "cover", borderRadius: 4 }} />}</td>
                <td>{v.customer_name}<div className="fs-11 text-muted">{datetime(v.at)}</div></td>
                <td className="text-end">{v.outcome === "order" ? <span className="badge bg-success-light text-success">order</span> : <span className="badge bg-secondary-light text-secondary">{v.reason_label || "no order"}</span>}</td>
              </tr>))}</tbody></table>}
      </div></div>
    </>
  );
}
