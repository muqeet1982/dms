import { DateInput } from "@/components/ui/DateInput";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { boxes, date, datetime, money, qty, titleCase } from "@/lib/format";
import { useAction, useDoc, useOptions, useWarehouses } from "@/lib/hooks";
import { pageRights, usePage, usePerms } from "@/lib/perms";
import { useScanListener } from "@/lib/scan";
import { toast } from "@/lib/toast";
import { Alert, Field, InfoItem, PageHeader, Spinner, StatCard, StatusBadge, Tabs, SaveError } from "@/components/ui/Bits";
import { Column, DataTable } from "@/components/ui/DataTable";
import { EntitySelect } from "@/components/ui/EntitySelect";
import { ListPage, useListQuery } from "@/components/ui/ListPage";
import { ResourceModal } from "@/components/ui/ResourceForm";
import { ConfirmDialog, Modal } from "@/components/ui/Modal";
import { DocTabs } from "@/components/documents/DocTabs";
import { EditLine, LinesEditor } from "@/components/documents/LinesEditor";

const whFilter = { name: "warehouse", label: "Warehouse", type: "entity" as const, endpoint: "/api/v1/settings/warehouses", mapOption: (w: any) => ({ id: w.id, label: w.name }) };

export function StockPage() {
  const [view, setView] = useState<"levels" | "summary">("levels");
  const summary = useQuery({ queryKey: ["stock-summary"], queryFn: () => api.get("/api/v1/inventory/stock/summary"), enabled: view === "summary" });
  const cols: Column<any>[] = [
    { key: "variant_name", header: "Item", sortKey: "variant__name", render: (s) => <><span className="fw-semibold text-dark">{s.variant_name}</span><div className="text-muted fs-11 font-monospace">{s.variant_sku}</div></> }, { key: "warehouse_name", header: "Warehouse", render: (s) => <>{s.warehouse_name}{s.bin_code && <div className="text-muted fs-11">bin {s.bin_code}</div>}</> },
    { key: "batch_no", header: "Batch / expiry", render: (s) => (s.batch_no ? <>{s.batch_no}<div className={`fs-11 ${s.expiry_date && new Date(s.expiry_date) < new Date() ? "text-danger" : "text-muted"}`}>{date(s.expiry_date)}</div></> : "—") },
    { key: "qty_on_hand", header: "On hand", num: true, sortKey: "qty_on_hand", render: (s) => <span className={Number(s.qty_on_hand) < 0 ? "text-danger fw-bold" : Number(s.qty_on_hand) < Number(s.min_stock) ? "text-warning fw-semibold" : "fw-medium"}>{qty(s.qty_on_hand)} <span className="text-muted fs-11">{s.uom}</span></span> },
    // a storeman thinks in boxes: 1 box = `packing` units, the remainder is loose
    { key: "boxes", header: "Boxes + loose", num: true, render: (s) => (Number(s.packing) > 1 ? <>{boxes(s.qty_on_hand, s.packing)}<div className="text-muted fs-11">{s.packing} per box</div></> : <span className="text-muted">loose</span>) },
    { key: "qty_reserved", header: "Reserved", num: true, render: (s) => qty(s.qty_reserved) }, { key: "qty_available", header: "Available", num: true, render: (s) => qty(s.qty_available) }, { key: "avg_cost", header: "Avg cost", num: true, render: (s) => money(s.avg_cost) }, { key: "value", header: "Value", num: true, render: (s) => money(s.value, 0) },
  ];
  return (
    <>
      {view === "levels" ? (
        <ListPage title="Stock levels" endpoint="/api/v1/inventory/stock" columns={cols} defaultOrdering="variant__name" searchPlaceholder="Search item, SKU, batch, barcode…" filters={[whFilter, { name: "variant__product__brand", label: "Brand", type: "entity", endpoint: "/api/v1/brands", mapOption: (b) => ({ id: b.id, label: b.name }) }, { name: "variant__product__category", label: "Category", type: "entity", endpoint: "/api/v1/categories", mapOption: (c) => ({ id: c.id, label: c.name }) }, { name: "low_stock", label: "Low stock", type: "toggle" }]}
          actions={<><button className="btn btn-outline-secondary" onClick={() => setView("summary")}><i className="ti ti-layout-list me-1" />In stock / dispatched / delivered</button><Link to="/app/inventory/lookup" className="btn btn-outline-primary"><i className="ti ti-scan me-1" />Scan lookup</Link><button className="btn btn-outline-success" onClick={() => api.download("/api/v1/exports/stock", "stock.csv")}><i className="ti ti-file-type-csv me-1" />Export</button></>} rowLink={(s) => `/app/catalog/products/${s.product_id}`} />
      ) : (
        <>
          <PageHeader title="Stock summary" crumbs={[{ label: "Stock", to: "/app/inventory/stock" }, { label: "Summary" }]} actions={<button className="btn btn-outline-secondary" onClick={() => setView("levels")}>Back to levels</button>} />
          <Alert kind="info">Three buckets per item: <strong>In stock</strong> (warehouse ledger), <strong>Dispatched</strong> (on a trip — sold but not yet delivered) and <strong>Delivered</strong>. The last two come from order state, not the warehouse ledger.</Alert>
          <div className="card"><div className="card-body p-0"><DataTable columns={[{ key: "name", header: "Item", render: (r: any) => <>{r.name}<div className="text-muted fs-11">{r.sku}</div></> }, { key: "in_stock", header: "In stock", num: true, render: (r: any) => qty(r.in_stock) }, { key: "reserved", header: "Reserved", num: true, render: (r: any) => qty(r.reserved) }, { key: "dispatched", header: "Dispatched (sold)", num: true, render: (r: any) => qty(r.dispatched) }, { key: "delivered", header: "Delivered", num: true, render: (r: any) => qty(r.delivered) }]} rows={summary.data} loading={summary.isLoading} /></div></div>
        </>
      )}
    </>
  );
}

export function StockLookupPage() {
  const [code, setCode] = useState("");
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const lookup = async (c: string) => { if (!c.trim()) return; try { setResult(await api.get("/api/v1/inventory/stock/lookup", { code: c.trim() })); setError(null); } catch (e: any) { setError(e.message); setResult(null); } setCode(""); };
  useScanListener(lookup, { captureInInputs: true });
  return (
    <>
      <PageHeader title="Scan lookup" crumbs={[{ label: "Stock", to: "/app/inventory/stock" }, { label: "Scan lookup" }]} />
      <div className="card scanner-mode mb-3"><div className="card-body"><label className="form-label">Scan a barcode or type a SKU and press Enter</label><input className="form-control" autoFocus value={code} onChange={(e) => setCode(e.target.value)} onKeyDown={(e) => e.key === "Enter" && lookup(code)} placeholder="6291041500001" /></div></div>
      {error && <Alert kind="danger">{error}</Alert>}
      {result && (
        <div className="card"><div className="card-body">
          <div className="d-flex justify-content-between align-items-start"><div><h4 className="mb-1">{result.variant.name}</h4><div className="text-muted">{result.variant.sku}{result.packaging ? ` · scanned packaging: ${result.packaging}` : ""}{result.batch ? ` · batch ${result.batch}` : ""} · sale {money(result.variant.sale_price)}</div></div><div className="text-end"><div className="kpi-value">{qty(result.total_on_hand)}</div><div className="fs-12 text-muted">total on hand ({result.variant.base_uom})</div></div></div>
          <table className="table table-sm fs-13 mt-3 mb-0"><thead className="table-light"><tr><th>Warehouse</th><th>Bin</th><th>Batch</th><th>Expiry</th><th className="num">On hand</th><th className="num">Reserved</th><th className="num">Available</th></tr></thead><tbody>{result.levels.map((l: any) => <tr key={l.id}><td>{l.warehouse_name}</td><td>{l.bin_code}</td><td>{l.batch_no}</td><td>{date(l.expiry_date)}</td><td className="num fw-semibold">{qty(l.qty_on_hand)}</td><td className="num">{qty(l.qty_reserved)}</td><td className="num">{qty(l.qty_available)}</td></tr>)}</tbody></table>
          <div className="mt-3"><Link to={`/app/catalog/products/${result.variant.product}`} className="btn btn-outline-primary btn-sm">Open product</Link></div>
        </div></div>
      )}
    </>
  );
}

export function MovementsPage() {
  const [sp, setSp] = useState<Record<string, any>>({});
  const q = useQuery({ queryKey: ["movements", sp], queryFn: () => api.get("/api/v1/inventory/movements", { page_size: 100, ...sp }) });
  const cols: Column<any>[] = [
    { key: "occurred_at", header: "Date", render: (m) => datetime(m.occurred_at) }, { key: "warehouse_name", header: "Warehouse" }, { key: "variant_name", header: "Item", render: (m) => <>{m.variant_name}<div className="text-muted fs-11">{m.variant_sku}</div></> }, { key: "batch_no", header: "Batch" },
    { key: "movement_type", header: "Type", render: (m) => <StatusBadge status={m.movement_type} label={titleCase(m.movement_type)} /> }, { key: "reference_number", header: "Reference" }, { key: "qty", header: "Qty", num: true, render: (m) => <span className={`fw-semibold ${Number(m.qty) < 0 ? "text-danger" : "text-success"}`}>{Number(m.qty) > 0 ? "+" : ""}{qty(m.qty)}</span> }, { key: "unit_cost", header: "Unit cost", num: true, render: (m) => money(m.unit_cost) }, { key: "value", header: "Value", num: true, render: (m) => money(m.value) }, { key: "performed_by_name", header: "By" },
  ];
  const types = ["opening", "grn", "purchase_return", "sale_dispatch", "sales_return", "transfer_out", "transfer_in", "adjustment", "wastage", "count_variance", "delivery_return"];
  return (
    <>
      <PageHeader title="Stock ledger" crumbs={[{ label: "Stock", to: "/app/inventory/stock" }, { label: "Ledger" }]} />
      <div className="card mb-3"><div className="card-body py-2"><div className="row g-2">
        <div className="col-md-3"><EntitySelect endpoint="/api/v1/variants" value={sp.variant} onChange={(v) => setSp({ ...sp, variant: v })} mapOption={(v) => ({ id: v.id, label: v.name, sub: v.sku })} placeholder="Item" /></div>
        <div className="col-md-2"><EntitySelect endpoint="/api/v1/settings/warehouses" value={sp.warehouse} onChange={(v) => setSp({ ...sp, warehouse: v })} mapOption={(w) => ({ id: w.id, label: w.name })} placeholder="Warehouse" /></div>
        <div className="col-md-2"><select className="form-select" value={sp.movement_type || ""} onChange={(e) => setSp({ ...sp, movement_type: e.target.value })}><option value="">All types</option>{types.map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}</select></div>
        <div className="col-md-2"><DateInput className="form-control" value={sp.date_from || ""} onChange={(e) => setSp({ ...sp, date_from: e.target.value })} /></div><div className="col-md-2"><DateInput className="form-control" value={sp.date_to || ""} onChange={(e) => setSp({ ...sp, date_to: e.target.value })} /></div>
        <div className="col-md-1"><button className="btn btn-outline-secondary w-100" onClick={() => setSp({})}>Reset</button></div>
      </div></div></div>
      <div className="card"><div className="card-body p-0"><DataTable columns={cols} rows={q.data?.results} loading={q.isLoading} /></div>{q.data?.next && <div className="card-footer text-center"><button className="btn btn-sm btn-outline-secondary" onClick={() => setSp({ ...sp, cursor: new URL(q.data.next).searchParams.get("cursor") })}>Next page</button></div>}</div>
    </>
  );
}

export function BatchesPage() {
  const page = usePage("batches");
  const qc = useQueryClient();
  const [edit, setEdit] = useState<any>(null);
  const cols: Column<any>[] = [{ key: "batch_no", header: "Batch", render: (b) => <span className="fw-semibold text-dark">{b.batch_no}</span> }, { key: "variant_name", header: "Item", render: (b) => <>{b.variant_name}<div className="text-muted fs-11">{b.variant_sku}</div></> }, { key: "mfg_date", header: "Mfg", render: (b) => date(b.mfg_date) }, { key: "expiry_date", header: "Expiry", sortKey: "expiry_date", render: (b) => <span className={b.is_expired ? "text-danger fw-semibold" : ""}>{date(b.expiry_date)}</span> }, { key: "qty_on_hand", header: "On hand", num: true, render: (b) => qty(b.qty_on_hand || 0) }, { key: "is_expired", header: "Status", render: (b) => <StatusBadge status={b.is_expired ? "expired" : Number(b.qty_on_hand) > 0 ? "active" : "inactive"} label={b.is_expired ? "Expired" : Number(b.qty_on_hand) > 0 ? "In stock" : "Empty"} /> }];
  // A posted receipt cannot be edited, so this is the only way to correct a date mistyped on the dock — and the
  // date decides what is picked first (first-expiry-first-out) and what the near-expiry report shows.
  if (page.edit) cols.push({ key: "fix", header: "", className: "text-end", render: (b: any) => <button className="btn btn-sm btn-outline-secondary" title="Correct the batch number or its dates" onClick={() => setEdit(b)}><i className="ti ti-pencil" /></button> });
  return (
    <>
        <ListPage title="Batches & expiry" crumbs={[{ label: "Stock", to: "/app/inventory/stock" }, { label: "Batches" }]} endpoint="/api/v1/inventory/batches" columns={cols} defaultOrdering="expiry_date" filters={[{ name: "expiring_days", label: "Expiring within", type: "select", options: [{ value: "30", label: "30 days" }, { value: "60", label: "60 days" }, { value: "90", label: "90 days" }] }, { name: "expired", label: "Expired only", type: "toggle" }, { name: "nonzero", label: "In stock only", type: "toggle" }]} />
      <ResourceModal open={Boolean(edit)} onClose={() => setEdit(null)} id={edit?.id} endpoint="/api/v1/inventory/batches"
        title={`Correct batch ${edit?.batch_no || ""}`}
        initial={edit ? { batch_no: edit.batch_no, mfg_date: edit.mfg_date, expiry_date: edit.expiry_date } : {}}
        fields={[
          { name: "batch_no", label: "Batch (lot) number", required: true, col: "col-md-4", hint: "as printed on the carton" },
          { name: "mfg_date", label: "Manufactured", type: "date", col: "col-md-4" },
          // Required where it means something: an expiry-tracked product must carry one, and a batch already
          // holding one must not be able to lose it. A brush or an apron has no expiry on the carton to type.
          { name: "expiry_date", label: "Expiry", type: "date", col: "col-md-4", required: Boolean(edit?.is_expiry_tracked || edit?.expiry_date),
            hint: edit?.is_expiry_tracked ? "required — this product is expiry-tracked" : edit?.expiry_date ? "required — picking and the alerts read this date" : "leave empty if the carton shows none" },
        ]}
        onSaved={() => { setEdit(null); qc.invalidateQueries({ queryKey: ["list", "/api/v1/inventory/batches"] }); qc.invalidateQueries({ queryKey: ["alert-counts"] }); }} />
    </>
  );
}

export function AlertsPage() {
  const qc = useQueryClient();
  const counts = useQuery({ queryKey: ["alert-counts"], queryFn: () => api.get("/api/v1/inventory/alerts/counts") });
  const reorder = useQuery({ queryKey: ["reorder"], queryFn: () => api.get("/api/v1/inventory/alerts/reorder-suggestions") });
  const { can } = usePerms();
  const [tab, setTab] = useState("alerts");
  const [sel, setSel] = useState<string[]>([]);
  const cols: Column<any>[] = [{ key: "type", header: "Type", render: (a) => <StatusBadge status={a.type} /> }, { key: "message", header: "Alert" }, { key: "warehouse_name", header: "Warehouse" }, { key: "created_at", header: "Since", render: (a) => datetime(a.created_at) }];
  const bySupplier: Record<string, any[]> = {};
  for (const r of reorder.data || []) (bySupplier[r.supplier || "(no supplier)"] ||= []).push(r);
  return (
    <>
      <PageHeader title="Inventory alerts & reorder" crumbs={[{ label: "Stock", to: "/app/inventory/stock" }, { label: "Alerts" }]} actions={<button className="btn btn-outline-primary" onClick={async () => { await api.post("/api/v1/inventory/alerts/refresh"); qc.invalidateQueries({ queryKey: ["list", "/api/v1/inventory/alerts"] }); qc.invalidateQueries({ queryKey: ["alert-counts"] }); toast.success("Alerts recomputed"); }}><i className="ti ti-refresh me-1" />Recompute</button>} />
      {counts.data && <div className="row g-3 mb-3">{Object.entries(counts.data).filter(([, v]) => v).map(([k, v]) => <div className="col-6 col-md-2" key={k}><StatCard label={titleCase(k)} value={v as number} icon="ti-alert-triangle" color={k === "expired" || k === "negative" ? "danger" : k === "low_stock" || k === "expiring" ? "warning" : "secondary"} /></div>)}</div>}
      <Tabs tabs={[{ key: "alerts", label: "Alerts" }, { key: "reorder", label: "Reorder suggestions", count: reorder.data?.length }]} active={tab} onChange={setTab} />
      {tab === "alerts" && <ListPage title="" crumbs={[]} endpoint="/api/v1/inventory/alerts" columns={cols} filters={[{ name: "type", label: "Type", type: "select", options: ["low_stock", "reorder", "expiring", "expired", "dead_stock", "negative", "drift"].map((t) => ({ value: t, label: titleCase(t) })) }]} />}
      {tab === "reorder" && Object.entries(bySupplier).map(([sup, rows]) => (
        <div className="card mb-3" key={sup}><div className="card-header d-flex justify-content-between align-items-center"><strong>{sup}</strong>{pageRights("purchase_orders").add && rows[0].supplier_id && <button className="btn btn-sm btn-primary" onClick={async () => { try { const po = await api.post("/api/v1/purchase-orders/from-reorder", { supplier: rows[0].supplier_id, rows }); toast.success(`Draft PO ${po.number} created`); } catch (e: any) { toast.error(e.message); } }}><i className="ti ti-shopping-bag me-1" />Create draft PO</button>}</div>
          <div className="card-body p-0"><table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>SKU</th><th>Item</th><th className="num">On hand</th><th className="num">Min</th><th className="num">Max</th><th className="num">Suggested qty</th><th className="num">Est. cost</th></tr></thead><tbody>{rows.map((r) => <tr key={r.variant_id}><td className="font-monospace">{r.sku}</td><td>{r.name}</td><td className="num text-danger">{qty(r.on_hand)}</td><td className="num">{qty(r.min_stock)}</td><td className="num">{qty(r.max_stock)}</td><td className="num fw-semibold">{qty(r.suggested_qty)}</td><td className="num">{money(Number(r.suggested_qty) * Number(r.cost), 0)}</td></tr>)}</tbody></table></div></div>
      ))}
      {tab === "reorder" && reorder.data && !reorder.data.length && <Alert kind="success">Nothing below minimum stock.</Alert>}
    </>
  );
}

export function ValuationPage() {
  const [wh, setWh] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["valuation", wh], queryFn: () => api.get("/api/v1/inventory/valuation", { warehouse: wh || undefined }) });
  const recon = useQuery({ queryKey: ["inv-recon"], queryFn: () => api.get("/api/v1/reports/inventory_reconciliation") });
  return (
    <>
      <PageHeader title="Inventory valuation" crumbs={[{ label: "Stock", to: "/app/inventory/stock" }, { label: "Valuation" }]} actions={<div style={{ minWidth: 240 }}><EntitySelect endpoint="/api/v1/settings/warehouses" value={wh} onChange={setWh} mapOption={(w) => ({ id: w.id, label: w.name })} placeholder="All warehouses" /></div>} />
      <div className="row g-3 mb-3"><div className="col-md-4"><StatCard label="Stock value (qty × avg cost)" value={money(q.data?.total, 0)} icon="ti-coins" color="primary" /></div>{recon.data && recon.data.rows.map((r: any) => <div className="col-md-4" key={r.item}><StatCard label={r.item} value={money(r.amount, 0)} icon="ti-book" color={r.item === "Difference" ? (Math.abs(Number(r.amount)) < 1 ? "success" : "danger") : "secondary"} /></div>)}</div>
      <div className="card"><div className="card-body p-0"><DataTable columns={[{ key: "warehouse", header: "Warehouse" }, { key: "sku", header: "SKU" }, { key: "name", header: "Item" }, { key: "batch", header: "Batch" }, { key: "qty", header: "Qty", num: true, render: (r: any) => qty(r.qty) }, { key: "avg_cost", header: "Avg cost", num: true, render: (r: any) => money(r.avg_cost) }, { key: "value", header: "Value", num: true, render: (r: any) => money(r.value) }]} rows={q.data?.rows} loading={q.isLoading} footer={<tr className="fw-bold"><td colSpan={6} className="text-end">Total</td><td className="num">{money(q.data?.total)}</td></tr>} /></div></div>
    </>
  );
}

// ------------------------------------------------------------------ transfers
export function TransfersPage() {
  const { can } = usePerms();
  const cols: Column<any>[] = [{ key: "number", header: "Transfer #", render: (t) => <span className="fw-semibold text-dark">{t.number}</span> }, { key: "date", header: "Date", render: (t) => date(t.date) }, { key: "from_warehouse_name", header: "From" }, { key: "to_warehouse_name", header: "To" }, { key: "lines", header: "Lines", render: (t) => t.lines.length }, { key: "requested_by_name", header: "Requested by" }, { key: "status", header: "Status", render: (t) => <StatusBadge status={t.status} /> }];
  return <ListPage title="Stock transfers" crumbs={[{ label: "Stock", to: "/app/inventory/stock" }, { label: "Transfers" }]} endpoint="/api/v1/inventory/transfers" columns={cols} rowLink={(t) => `/app/inventory/transfers/${t.id}`} page="transfers" createTo="/app/inventory/transfers/new" createLabel="New transfer" defaultOrdering="-date" filters={[{ name: "status", label: "Status", type: "select", options: ["requested", "approved", "in_transit", "received", "cancelled"].map((s) => ({ value: s, label: s.replace(/_/g, " ") })) }]} />;
}

export function TransferFormPage() {
  const [err, setErr] = useState<string | null>(null);
  const nav = useNavigate();
  const warehouses = useWarehouses();
  const [form, setForm] = useState<any>({ from_warehouse: "", to_warehouse: "", date: new Date().toISOString().slice(0, 10), notes: "" });
  const [lines, setLines] = useState<EditLine[]>([]);
  return (
    <>
      <PageHeader title="New stock transfer" crumbs={[{ label: "Transfers", to: "/app/inventory/transfers" }, { label: "New" }]} />
      <SaveError error={err} what="document" />
      <div className="card mb-3"><div className="card-body"><div className="row">
        <Field label="From warehouse *" col="col-md-3"><select className="form-select" value={form.from_warehouse} onChange={(e) => setForm({ ...form, from_warehouse: e.target.value })}><option value="">Select…</option>{warehouses.rows.map((w: any) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
        <Field label="To warehouse *" col="col-md-3"><select className="form-select" value={form.to_warehouse} onChange={(e) => setForm({ ...form, to_warehouse: e.target.value })}><option value="">Select…</option>{warehouses.rows.map((w: any) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
        <Field label="Date" col="col-md-2"><DateInput className="form-control" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} /></Field><Field label="Notes" col="col-md-4"><input className="form-control" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field></div>
        <LinesEditor lines={lines} onChange={setLines} showPrice={false} showDiscount={false} variantQuery={{ with_stock: 1 }} />
      </div></div>
      <div className="d-flex gap-2 justify-content-end"><button className="btn btn-light" onClick={() => nav(-1)}>Cancel</button><button className="btn btn-primary" onClick={async () => { const valid = lines.filter((l) => l.variant && Number(l.qty) > 0); if (!form.from_warehouse || !form.to_warehouse || !valid.length) return toast.error("Fill warehouses and lines"); try { const r = await api.post("/api/v1/inventory/transfers", { ...form, lines: valid.map((l) => ({ variant: l.variant, batch: l.batch || null, qty_requested: Number(l.qty) * (l.packagings?.find((p) => p.id === l.packaging)?.qty_in_base ? Number(l.packagings.find((p) => p.id === l.packaging)!.qty_in_base) : 1) })) }, true); toast.success("Transfer created"); nav(`/app/inventory/transfers/${r.id}`); } catch (e: any) { setErr(e.message); toast.error(e.message); } }}>Create transfer</button></div>
    </>
  );
}

export function TransferDetailPage() {
  const transfers = usePage("transfers");
  const { id } = useParams();
  const { can } = usePerms();
  const q = useDoc("/api/v1/inventory/transfers", id);
  const { run, busy } = useAction("/api/v1/inventory/transfers", id);
  const [recv, setRecv] = useState<Record<string, string>>({});
  const [dlg, setDlg] = useState<null | "receive" | "cancel">(null);
  if (q.isLoading) return <Spinner />;
  const t = q.data;
  if (!t) return null;
  return (
    <>
      <PageHeader title={t.number} badge={<StatusBadge status={t.status} />} crumbs={[{ label: "Transfers", to: "/app/inventory/transfers" }, { label: t.number }]}
        actions={<>{t.status === "requested" && transfers.can("approve") && <button className="btn btn-success" disabled={!!busy} onClick={() => run("approve")}>Approve</button>}{["approved", "requested"].includes(t.status) && transfers.can("dispatch") && <button className="btn btn-primary" disabled={!!busy} onClick={() => run("dispatch", {}, { success: "Dispatched — stock left the source warehouse" })}><i className="ti ti-truck me-1" />Dispatch</button>}{t.status === "in_transit" && transfers.can("dispatch") && <button className="btn btn-primary" onClick={() => { setRecv(Object.fromEntries(t.lines.map((l: any) => [l.id, l.qty_sent]))); setDlg("receive"); }}><i className="ti ti-package-import me-1" />Receive</button>}{["requested", "approved"].includes(t.status) && <button className="btn btn-outline-danger" onClick={() => setDlg("cancel")}>Cancel</button>}</>} />
      <div className="card mb-3"><div className="card-body"><div className="row g-3 mb-3"><InfoItem label="From">{t.from_warehouse_name}</InfoItem><InfoItem label="To">{t.to_warehouse_name}</InfoItem><InfoItem label="Requested">{date(t.date)} · {t.requested_by_name}</InfoItem><InfoItem label="Approved / dispatched / received">{t.approved_by_name || "—"} · {datetime(t.dispatched_at)} · {datetime(t.received_at)}</InfoItem></div>
        <table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>Item</th><th>Batch</th><th className="num">Requested</th><th className="num">Sent</th><th className="num">Received</th><th className="num">Lost</th><th className="num">Unit cost</th></tr></thead><tbody>{t.lines.map((l: any) => <tr key={l.id}><td>{l.variant_name}<div className="text-muted fs-11">{l.variant_sku}</div></td><td>{l.batch_no}</td><td className="num">{qty(l.qty_requested)}</td><td className="num">{qty(l.qty_sent)}</td><td className="num">{qty(l.qty_received)}</td><td className={`num ${Number(l.qty_lost) > 0 ? "text-danger fw-semibold" : ""}`}>{qty(l.qty_lost)}</td><td className="num">{money(l.unit_cost)}</td></tr>)}</tbody></table>{t.notes && <p className="fs-13 text-muted mt-2 mb-0">{t.notes}</p>}</div></div>
      <DocTabs model="StockTransfer" id={t.id} showMovements />
      <Modal open={dlg === "receive"} title="Receive transfer" onClose={() => setDlg(null)} size="md" footer={<><button className="btn btn-light" onClick={() => setDlg(null)}>Cancel</button><button className="btn btn-primary" disabled={!!busy} onClick={async () => { await run("receive", { received_qtys: recv }, { success: "Received" }); setDlg(null); }}>Confirm receipt</button></>}><p className="fs-13 text-muted">Enter the quantities actually received. Shortages are booked as transit loss.</p><table className="table table-sm fs-12"><tbody>{t.lines.map((l: any) => <tr key={l.id}><td>{l.variant_name}</td><td className="num">{qty(l.qty_sent)} sent</td><td style={{ width: 120 }}><input type="number" step="any" className="form-control form-control-sm" value={recv[l.id] ?? ""} onChange={(e) => setRecv({ ...recv, [l.id]: e.target.value })} /></td></tr>)}</tbody></table></Modal>
      <ConfirmDialog open={dlg === "cancel"} title="Cancel transfer" withReason danger onClose={() => setDlg(null)} onConfirm={async (r) => { await run("cancel", { reason: r }); setDlg(null); }} />
    </>
  );
}

// ---------------------------------------------------------------- adjustments
export function AdjustmentsPage() {
  const stats = useQuery({ queryKey: ["adj-stats"], queryFn: () => api.get("/api/v1/inventory/adjustments/stats") });
  const { can } = usePerms();
  const cols: Column<any>[] = [{ key: "number", header: "Adjustment #", render: (a) => <span className="fw-semibold text-dark">{a.number}</span> }, { key: "date", header: "Date", render: (a) => date(a.date) }, { key: "warehouse_name", header: "Warehouse" }, { key: "type", header: "Type", render: (a) => <span className={a.type === "decrease" ? "text-danger" : "text-success"}>{a.type}</span> }, { key: "reason_name", header: "Reason", render: (a) => <>{a.reason_name}<div className="text-muted fs-11">{a.reason_category}</div></> }, { key: "lines", header: "Lines", render: (a) => a.lines.length }, { key: "total_value", header: "Value", num: true, render: (a) => money(a.total_value) }, { key: "created_by_name", header: "By" }, { key: "status", header: "Status", render: (a) => <StatusBadge status={a.status} /> }];
  return <ListPage page="adjustments" title="Stock adjustments & wastage" crumbs={[{ label: "Stock", to: "/app/inventory/stock" }, { label: "Adjustments" }]} endpoint="/api/v1/inventory/adjustments" columns={cols} rowLink={(a) => `/app/inventory/adjustments/${a.id}`} createTo="/app/inventory/adjustments/new" createLabel="New adjustment" canCreate={pageRights("adjustments").add} defaultOrdering="-date"
    filters={[{ name: "status", label: "Status", type: "select", options: ["draft", "pending_approval", "approved", "rejected"].map((s) => ({ value: s, label: s.replace(/_/g, " ") })) }, { name: "type", label: "Type", type: "select", options: [{ value: "decrease", label: "Decrease" }, { value: "increase", label: "Increase" }] }, whFilter, { name: "reason__category", label: "Category", type: "select", options: ["damage", "expiry", "theft", "lost", "sample", "promotion", "count", "correction", "transit_damage", "other"].map((c) => ({ value: c, label: titleCase(c) })) }]}
    stats={stats.data && <div className="row g-3 mb-3"><div className="col-6 col-md-4"><StatCard label="Pending approval" value={stats.data.pending} icon="ti-clock" color="warning" /></div><div className="col-6 col-md-4"><StatCard label="Approved this month" value={stats.data.approved_month} icon="ti-circle-check" color="success" /></div><div className="col-6 col-md-4"><StatCard label="Value this month" value={money(stats.data.value_month, 0)} icon="ti-coins" color="danger" /></div></div>} />;
}

export function AdjustmentFormPage() {
  const [err, setErr] = useState<string | null>(null);
  const nav = useNavigate();
  const warehouses = useWarehouses();
  const reasons = useOptions("/api/v1/settings/reason-codes", { is_active: true });
  const [form, setForm] = useState<any>({ warehouse: "", type: "decrease", reason: "", date: new Date().toISOString().slice(0, 10), notes: "", photo_confirmed: false });
  const [lines, setLines] = useState<EditLine[]>([]);
  const reason = reasons.data?.find((r) => r.id === form.reason);
  return (
    <>
      <PageHeader title="New stock adjustment" crumbs={[{ label: "Adjustments", to: "/app/inventory/adjustments" }, { label: "New" }]} />
      <SaveError error={err} what="document" />
      <div className="card mb-3"><div className="card-body"><div className="row">
        <Field label="Warehouse *" col="col-md-3"><select className="form-select" value={form.warehouse} onChange={(e) => setForm({ ...form, warehouse: e.target.value })}><option value="">Select…</option>{warehouses.rows.map((w: any) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
        <Field label="Type *" col="col-md-2"><select className="form-select" value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}><option value="decrease">Decrease (write-off)</option><option value="increase">Increase</option></select></Field>
        <Field label="Reason *" col="col-md-3"><select className="form-select" value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })}><option value="">Select…</option>{reasons.data?.map((r) => <option key={r.id} value={r.id}>{r.name} ({r.category})</option>)}</select></Field>
        <Field label="Date" col="col-md-2"><DateInput className="form-control" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} /></Field>
        <Field label="Notes" col="col-md-12"><input className="form-control" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} placeholder="What happened?" /></Field></div>
        {reason && <Alert kind={reason.requires_approval ? "warning" : "info"}>{reason.requires_approval ? "This reason requires approval by a user with the approve-adjustment permission (within their approval limit)." : "This reason posts immediately."}{reason.requires_photo && " A photo attachment is required — upload it on the adjustment page after saving."}</Alert>}
        <LinesEditor lines={lines} onChange={setLines} showPrice={false} showDiscount={false} showBatch variantQuery={{ with_stock: 1 }} />
      </div></div>
      <div className="d-flex gap-2 justify-content-end"><button className="btn btn-light" onClick={() => nav(-1)}>Cancel</button><button className="btn btn-primary" onClick={async () => { const valid = lines.filter((l) => l.variant && Number(l.qty) > 0); if (!form.warehouse || !form.reason || !valid.length) return toast.error("Fill warehouse, reason and lines"); try { const r = await api.post("/api/v1/inventory/adjustments", { ...form, photo_confirmed: true, submit: !reason?.requires_photo, lines: valid.map((l) => ({ variant: l.variant, batch: l.batch || null, qty: Number(l.qty) * (l.packagings?.find((p) => p.id === l.packaging) ? Number(l.packagings.find((p) => p.id === l.packaging)!.qty_in_base) : 1), note: l.batch_no ? `batch ${l.batch_no}` : "" })) }, true); toast.success("Adjustment saved"); nav(`/app/inventory/adjustments/${r.id}`); } catch (e: any) { setErr(e.message); toast.error(e.message); } }}>Save{reason?.requires_photo ? " as draft" : " & submit"}</button></div>
    </>
  );
}

export function AdjustmentDetailPage() {
  const adjustments = usePage("adjustments");
  const { id } = useParams();
  const { can } = usePerms();
  const q = useDoc("/api/v1/inventory/adjustments", id);
  const { run, busy } = useAction("/api/v1/inventory/adjustments", id, [["adj-stats"]]);
  const [reject, setReject] = useState(false);
  if (q.isLoading) return <Spinner />;
  const a = q.data;
  if (!a) return null;
  return (
    <>
      <PageHeader title={a.number} badge={<StatusBadge status={a.status} />} crumbs={[{ label: "Adjustments", to: "/app/inventory/adjustments" }, { label: a.number }]}
        actions={<>{a.status === "draft" && adjustments.add && <button className="btn btn-primary" disabled={!!busy} onClick={() => run("submit")}>Submit</button>}{a.status === "pending_approval" && adjustments.can("approve") && <><button className="btn btn-success" disabled={!!busy} onClick={() => run("approve", {}, { success: "Approved — stock and journal posted" })}><i className="ti ti-check me-1" />Approve</button><button className="btn btn-outline-danger" onClick={() => setReject(true)}>Reject</button></>}</>} />
      {a.rejection_reason && <Alert kind="danger">Rejected: {a.rejection_reason}</Alert>}
      <div className="card mb-3"><div className="card-body"><div className="row g-3 mb-3"><InfoItem label="Warehouse">{a.warehouse_name}</InfoItem><InfoItem label="Type / reason"><span className={a.type === "decrease" ? "text-danger" : "text-success"}>{a.type}</span> · {a.reason_name}</InfoItem><InfoItem label="Date / by">{date(a.date)} · {a.created_by_name}</InfoItem><InfoItem label="Approved">{a.approved_by_name || "—"} {a.approved_at ? datetime(a.approved_at) : ""}{a.journal_number && <div className="fs-12"><Link to={`/app/accounting/journals/${a.journal}`}>{a.journal_number}</Link></div>}</InfoItem></div>
        <table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>Item</th><th>Batch</th><th className="num">Qty</th><th className="num">Unit cost</th><th className="num">Value</th><th>Note</th></tr></thead><tbody>{a.lines.map((l: any) => <tr key={l.id}><td>{l.variant_name}<div className="text-muted fs-11">{l.variant_sku}</div></td><td>{l.batch_no}</td><td className="num">{qty(l.qty)}</td><td className="num">{money(l.unit_cost)}</td><td className="num">{money(l.value)}</td><td>{l.note}</td></tr>)}</tbody><tfoot><tr className="fw-bold"><td colSpan={4} className="text-end">Total value</td><td className="num">{money(a.total_value)}</td><td /></tr></tfoot></table>{a.notes && <p className="fs-13 text-muted mt-2 mb-0">{a.notes}</p>}</div></div>
      <DocTabs model="StockAdjustment" id={a.id} showMovements />
      <ConfirmDialog open={reject} title="Reject adjustment" withReason danger onClose={() => setReject(false)} onConfirm={async (r) => { await run("reject", { reason: r }); setReject(false); }} />
    </>
  );
}

// --------------------------------------------------------------------- counts
export function CountsPage() {
  const { can } = usePerms();
  const cols: Column<any>[] = [{ key: "number", header: "Count #", render: (c) => <span className="fw-semibold text-dark">{c.number}</span> }, { key: "warehouse_name", header: "Warehouse" }, { key: "type", header: "Type" }, { key: "progress", header: "Progress", render: (c) => `${c.progress.counted}/${c.progress.total}` }, { key: "started_by_name", header: "Started by" }, { key: "created_at", header: "Opened", render: (c) => datetime(c.created_at) }, { key: "status", header: "Status", render: (c) => <StatusBadge status={c.status} /> }];
  return <ListPage title="Stock counts" crumbs={[{ label: "Stock", to: "/app/inventory/stock" }, { label: "Counts" }]} endpoint="/api/v1/inventory/counts" columns={cols} rowLink={(c) => `/app/inventory/counts/${c.id}`} page="counts" createTo="/app/inventory/counts/new" createLabel="Open a count"  defaultOrdering="-created_at" />;
}

export function CountFormPage() {
  const [err, setErr] = useState<string | null>(null);
  const nav = useNavigate();
  const warehouses = useWarehouses();
  const cats = useOptions("/api/v1/categories");
  const [form, setForm] = useState<any>({ warehouse: "", type: "full", categories: [] as string[], tolerance_pct: "2", notes: "" });
  return (
    <>
      <PageHeader title="Open a stock count" crumbs={[{ label: "Counts", to: "/app/inventory/counts" }, { label: "New" }]} />
      <SaveError error={err} what="document" />
      <div className="card"><div className="card-body"><div className="row">
        <Field label="Warehouse *" col="col-md-4"><select className="form-select" value={form.warehouse} onChange={(e) => setForm({ ...form, warehouse: e.target.value })}><option value="">Select…</option>{warehouses.rows.map((w: any) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
        <Field label="Type" col="col-md-2"><select className="form-select" value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}><option value="full">Full</option><option value="cycle">Cycle (by category)</option></select></Field>
        <Field label="Tolerance %" col="col-md-2"><input type="number" step="0.1" className="form-control" value={form.tolerance_pct} onChange={(e) => setForm({ ...form, tolerance_pct: e.target.value })} /></Field>
        {form.type === "cycle" && <Field label="Categories" col="col-md-12"><div className="d-flex flex-wrap gap-3">{cats.data?.map((c) => <div className="form-check" key={c.id}><input className="form-check-input" type="checkbox" id={`c-${c.id}`} checked={form.categories.includes(c.id)} onChange={(e) => setForm({ ...form, categories: e.target.checked ? [...form.categories, c.id] : form.categories.filter((x: string) => x !== c.id) })} /><label htmlFor={`c-${c.id}`} className="form-check-label">{c.path || c.name}</label></div>)}</div></Field>}
        <Field label="Notes" col="col-md-12"><input className="form-control" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field></div>
        <Alert kind="info">Opening a count snapshots the system quantity for every item in scope. Counters then enter or scan counted quantities; variances become adjustments when the count is posted.</Alert>
        <div className="text-end"><button className="btn btn-primary" disabled={!form.warehouse} onClick={async () => { try { const r = await api.post("/api/v1/inventory/counts", { warehouse: form.warehouse, type: form.type, tolerance_pct: form.tolerance_pct, notes: form.notes, scope: form.type === "cycle" ? { categories: form.categories } : {} }, true); nav(`/app/inventory/counts/${r.id}`); } catch (e: any) { setErr(e.message); toast.error(e.message); } }}>Open count</button></div>
      </div></div>
    </>
  );
}

export function CountDetailPage() {
  const counts = usePage("counts");
  const { id } = useParams();
  const { can } = usePerms();
  const q = useDoc("/api/v1/inventory/counts", id);
  const { run, busy } = useAction("/api/v1/inventory/counts", id);
  const variances = useQuery({ queryKey: ["count-variances", id], queryFn: () => api.get(`/api/v1/inventory/counts/${id}/variances`), enabled: Boolean(q.data && ["review", "counting", "posted"].includes(q.data.status)) });
  const [entries, setEntries] = useState<Record<string, string>>({});
  const [scan, setScan] = useState("");
  const [tab, setTab] = useState("entry");
  if (q.isLoading) return <Spinner />;
  const c = q.data;
  if (!c) return null;
  const open = ["counting", "review"].includes(c.status);
  const save = async () => { const list = Object.entries(entries).map(([line_id, counted_qty]) => ({ line_id, counted_qty })); if (!list.length) return; await run("record", { entries: list }, { success: "Counts saved" }); setEntries({}); };
  const doScan = async () => { if (!scan.trim()) return; try { const r = await api.get("/api/v1/barcodes/resolve", { code: scan.trim() }); const line = c.lines.find((l: any) => l.variant === r.variant.id && (!r.batch || l.batch === r.batch.id)); if (!line) throw new Error("Item not in this count"); const mult = r.packaging ? Number(r.packaging.qty_in_base) : 1; setEntries({ ...entries, [line.id]: String(Number(entries[line.id] ?? line.counted_qty ?? 0) + mult) }); } catch (e: any) { toast.error(e.message); } setScan(""); };
  return (
    <>
      <PageHeader title={c.number} badge={<StatusBadge status={c.status} />} crumbs={[{ label: "Counts", to: "/app/inventory/counts" }, { label: c.number }]}
        actions={<>{open && counts.edit && <button className="btn btn-outline-primary" disabled={!!busy} onClick={save}>Save entries</button>}{c.status === "counting" && <button className="btn btn-outline-secondary" onClick={() => run("review")}>Move to review</button>}{open && counts.can("post") && <button className="btn btn-success" disabled={!!busy} onClick={() => run("post", {}, { success: "Count posted — variances adjusted" })}><i className="ti ti-check me-1" />Post count</button>}{open && counts.delete && <button className="btn btn-outline-danger" onClick={() => run("cancel")}>Cancel</button>}</>} />
      <div className="row g-3 mb-3"><div className="col-6 col-md-3"><StatCard label="Warehouse" value={c.warehouse_name} icon="ti-building-warehouse" color="primary" sub={`${c.type} count · tolerance ${c.tolerance_pct}%`} /></div><div className="col-6 col-md-3"><StatCard label="Progress" value={`${c.progress.counted}/${c.progress.total}`} icon="ti-progress" color="info" /></div><div className="col-6 col-md-3"><StatCard label="Variances" value={variances.data?.filter((v: any) => Number(v.variance) !== 0).length ?? "—"} icon="ti-arrows-diff" color="warning" /></div><div className="col-6 col-md-3"><StatCard label="Adjustments" value={c.adjustment_number || c.adjustment_increase_number || "—"} icon="ti-adjustments" color="secondary" sub={c.adjustment_increase_number && c.adjustment_number ? c.adjustment_increase_number : ""} /></div></div>
      <Tabs tabs={[{ key: "entry", label: "Count entry" }, { key: "variances", label: "Variances" }]} active={tab} onChange={setTab} />
      {tab === "entry" && <>
        {open && <div className="card scanner-mode mb-3"><div className="card-body"><input className="form-control" placeholder="Scan item to add 1 (or one packaging)…" value={scan} onChange={(e) => setScan(e.target.value)} onKeyDown={(e) => e.key === "Enter" && doScan()} /></div></div>}
        <div className="card"><div className="card-body p-0"><table className="table table-sm align-middle mb-0 fs-13"><thead className="table-light"><tr><th>Item</th><th>Batch</th><th>Bin</th><th className="num">System</th><th style={{ width: 140 }}>Counted</th><th>By</th></tr></thead><tbody>{c.lines.map((l: any) => <tr key={l.id} className={l.counted_qty !== null && Number(l.counted_qty) !== Number(l.system_qty) ? "table-warning" : ""}><td>{l.variant_name}<div className="text-muted fs-11">{l.variant_sku}</div></td><td>{l.batch_no}</td><td>{l.bin_code}</td><td className="num">{qty(l.system_qty)}</td><td>{open ? <input type="number" step="any" className="form-control form-control-sm" value={entries[l.id] ?? l.counted_qty ?? ""} onChange={(e) => setEntries({ ...entries, [l.id]: e.target.value })} /> : qty(l.counted_qty)}</td><td className="fs-12 text-muted">{l.counted_by_name}{l.scanned && <i className="ti ti-scan ms-1" />}</td></tr>)}</tbody></table></div></div>
      </>}
      {tab === "variances" && <div className="card"><div className="card-body p-0"><table className="table table-sm mb-0 fs-13"><thead className="table-light"><tr><th>Item</th><th>Batch</th><th className="num">System</th><th className="num">Counted</th><th className="num">Variance</th><th className="num">%</th><th className="num">Value</th></tr></thead><tbody>{variances.data?.map((v: any) => <tr key={v.line_id} className={v.over_tolerance ? "table-danger" : Number(v.variance) !== 0 ? "table-warning" : ""}><td>{v.name}<div className="text-muted fs-11">{v.sku}</div></td><td>{v.batch}</td><td className="num">{qty(v.system_qty)}</td><td className="num">{qty(v.counted_qty)}</td><td className="num fw-semibold">{qty(v.variance)}</td><td className="num">{v.variance_pct}%</td><td className="num">{money(v.value)}</td></tr>)}{variances.data && !variances.data.length && <tr><td colSpan={7} className="text-muted text-center py-3">Nothing counted yet</td></tr>}</tbody></table></div></div>}
    </>
  );
}
