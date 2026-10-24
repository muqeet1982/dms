import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { date, datetime, money, qty, titleCase } from "@/lib/format";
import { useDoc, useOptions } from "@/lib/hooks";
import { pageRights, usePage } from "@/lib/perms";
import { toast } from "@/lib/toast";
import { Alert, Field, InfoItem, PageHeader, Spinner, StatCard, StatusBadge, Tabs } from "@/components/ui/Bits";
import { CrudPage } from "@/components/ui/CrudPage";
import { Column } from "@/components/ui/DataTable";
import { EntitySelect } from "@/components/ui/EntitySelect";
import { ListPage } from "@/components/ui/ListPage";
import { Modal } from "@/components/ui/Modal";
import { FieldDef, ResourceForm, ResourceModal } from "@/components/ui/ResourceForm";
import { AttachmentsTab, HistoryTab } from "@/components/documents/DocTabs";
import { ImportButton } from "@/components/documents/ImportPanel";

/** Location picker backed by the warehouse locations (bins) list, with inline "add location". */
function LocationField({ value, onChange }: { value: string | null; onChange: (id: string | null) => void }) {
  const [add, setAdd] = useState(false);
  const qc = useQueryClient();
  const warehouses = useOptions("/api/v1/settings/warehouses", { is_active: true });
  return (
    <>
      <div className="d-flex gap-2">
        <div className="flex-grow-1"><EntitySelect endpoint="/api/v1/settings/bins" value={value} onChange={onChange} mapOption={(b) => ({ id: b.id, label: b.label || b.code, sub: b.warehouse_name })} placeholder="Rack / shelf / bin…" /></div>
        <button type="button" className="btn btn-outline-secondary" title="Add a location" onClick={() => setAdd(true)}><i className="ti ti-plus" /></button>
      </div>
      <div className="form-text fs-11">Manage the full list under <Link to="/app/settings/locations">Settings → Locations</Link>.</div>
      {add && warehouses.data && <ResourceModal open onClose={() => setAdd(false)} title="New location" endpoint="/api/v1/settings/bins"
        fields={[{ name: "warehouse", label: "Warehouse", type: "select", required: true, col: "col-md-6", options: warehouses.data.map((w: any) => ({ value: w.id, label: w.name })), default: (warehouses.data.find((w: any) => w.type === "main") || warehouses.data[0])?.id },
          { name: "code", label: "Location code", required: true, col: "col-md-3", placeholder: "A-01" }, { name: "name", label: "Description", col: "col-md-3", placeholder: "Rack A, shelf 1" }]}
        onSaved={(row) => { qc.invalidateQueries({ queryKey: ["entity-select", "/api/v1/settings/bins"] }); qc.invalidateQueries({ queryKey: ["entity-select-one", "/api/v1/settings/bins"] }); qc.invalidateQueries({ queryKey: ["options", "/api/v1/settings/bins"] }); qc.invalidateQueries({ queryKey: ["crud", "/api/v1/settings/bins"] }); onChange(row.id); }} />}
    </>
  );
}

const productFields = (isNew: boolean): FieldDef[] => [
  { name: "name", label: "Product name", required: true, col: "col-md-6" },
  { name: "upc", label: "UPC (barcode on the unit)", required: true, col: "col-md-3", hint: "One scan = one unit", inputMode: "numeric", autoComplete: "off" },
  { name: "status", label: "Status", type: "select", options: ["active", "inactive", "archived"].map((s) => ({ value: s, label: s })), default: "active", col: "col-md-3" },
  { name: "item_code", label: "Item code", col: "col-md-3", hint: "The supplier's own code, e.g. 62001" },
  { name: "brand", label: "Brand", type: "entity", endpoint: "/api/v1/brands", mapOption: (b) => ({ id: b.id, label: b.name }), col: "col-md-3" }, { name: "category", label: "Category", type: "entity", endpoint: "/api/v1/categories", mapOption: (c) => ({ id: c.id, label: c.path || c.name }), col: "col-md-3" },
  { name: "supplier", label: "Supplier (manufacturer)", type: "entity", endpoint: "/api/v1/suppliers", mapOption: (s) => ({ id: s.id, label: s.name }), col: "col-md-3" }, { name: "base_uom", label: "Base unit", type: "entity", endpoint: "/api/v1/uoms", mapOption: (u) => ({ id: u.id, label: `${u.code} — ${u.name}` }), required: true, col: "col-md-3" },
  { name: "unit_size", label: "Unit / size", col: "col-md-3", placeholder: "60ml, 1L, 500g, pcs" }, { name: "packing", label: "Units per box", type: "number", col: "col-md-3", default: 1, hint: "How many units in one box / carton" }, { name: "box_upc", label: "Box UPC (barcode on the box)", col: "col-md-3", hint: "One scan = one box", inputMode: "numeric", autoComplete: "off", showIf: (v) => Number(v.packing) > 1 }, { name: "location", label: "Location (rack / shelf / bin)", type: "custom", col: "col-md-3", render: (v, set) => <LocationField value={v || null} onChange={set} /> }, { name: "tax_pct", label: "Tax %", type: "number", col: "col-md-3", suffix: "%", hint: "shows on each invoice line for this product when the invoice charges tax" },
  { name: "cost_price", label: "D.P — distributor price", type: "money", col: "col-md-3", default: 0 }, { name: "sale_price", label: "T.P — trade price", type: "money", col: "col-md-3", default: 0 }, { name: "retail_price", label: "R.P — retail price", type: "money", col: "col-md-3", default: 0 }, { name: "price_effective_date", label: "Price list date", type: "date", col: "col-md-3", hint: "The date of the supplier price list these prices came from. A record only — it does not schedule a price change." },
  { name: "discount_pct", label: "Product discount %", type: "number", col: "col-md-3", default: 0, suffix: "%", hint: "applies on this product's sales lines once the minimum quantity is reached; separate from the customer's co-funded discount", validate: (v: any) => (Number(v) > 100 ? "Cannot be more than 100%" : Number(v) < 0 ? "Cannot be negative" : null) },
  { name: "discount_min_qty", label: "Discount from (qty)", type: "number", col: "col-md-2", default: 0, hint: "0 = always" },
  { name: "offers_write", label: "Dated offers (override the standard discount while they match)", type: "custom", col: "col-md-12", default: [],
    render: (v: any, set: any) => <OffersEditor value={Array.isArray(v) ? v : []} onChange={set} /> },
  ...(isNew
    ? ([{ name: "opening_qty", label: "Opening quantity", type: "number", col: "col-md-3", hint: "Stock already on hand — posted as an opening movement" },
        { name: "opening_warehouse", label: "Opening stock warehouse", type: "entity", endpoint: "/api/v1/settings/warehouses", mapOption: (w: any) => ({ id: w.id, label: w.name, sub: w.code }), col: "col-md-3", hint: "blank = the location's warehouse / main" },
        { name: "opening_unit_cost", label: "Opening unit cost", type: "money", col: "col-md-3", hint: "blank = D.P" },
        { name: "opening_batch_no", label: "Opening batch no", col: "col-md-3", showIf: (v) => Boolean(v.is_batch_tracked) },
        { name: "opening_expiry_date", label: "Opening batch expiry", type: "date", col: "col-md-3", showIf: (v) => Boolean(v.is_batch_tracked) }] as FieldDef[])
    : []),
  { name: "min_stock", label: "Min stock", type: "number", col: "col-md-3", default: 0 }, { name: "max_stock", label: "Max stock", type: "number", col: "col-md-3", default: 0 }, { name: "reorder_qty", label: "Reorder qty", type: "number", col: "col-md-3", default: 0 }, { name: "shelf_life_days", label: "Shelf life (days)", type: "number", col: "col-md-3" },
  { name: "is_batch_tracked", label: "Batch (lot) tracked", type: "checkbox", col: "col-md-3", hint: "Stock is held per batch (lot) number, and movements have to say which batch. On a goods receipt a blank batch falls back to the supplier challan number." }, { name: "is_expiry_tracked", label: "Expiry tracked", type: "checkbox", col: "col-md-3", hint: "Receiving this product will insist on the expiry date from the carton, and the near-expiry alerts, the expiry report and first-expiry-first-out picking all read it." }, { name: "description", label: "Description", type: "textarea", col: "col-md-9" },
];

function OffersEditor({ value, onChange }: { value: any[]; onChange: (v: any[]) => void }) {
  const row = (r: any, i: number, patch: any) => { const next = [...value]; next[i] = { ...r, ...patch }; onChange(next); };
  return (
    <div>
      {value.map((r, i) => (
        <div className="d-flex gap-2 align-items-center mb-1" key={i}>
          <input type="number" step="0.01" className="form-control form-control-sm" style={{ width: 90 }} placeholder="%" value={r.discount_pct ?? ""} onChange={(e) => row(r, i, { discount_pct: e.target.value })} />
          <span className="fs-12 text-muted">% from</span>
          <input type="number" className="form-control form-control-sm" style={{ width: 90 }} placeholder="qty" value={r.min_qty ?? ""} onChange={(e) => row(r, i, { min_qty: e.target.value })} />
          <span className="fs-12 text-muted">qty ·</span>
          <input type="date" className="form-control form-control-sm" style={{ width: 150 }} value={r.start_date ?? ""} onChange={(e) => row(r, i, { start_date: e.target.value })} />
          <span className="fs-12 text-muted">to</span>
          <input type="date" className="form-control form-control-sm" style={{ width: 150 }} value={r.end_date ?? ""} onChange={(e) => row(r, i, { end_date: e.target.value })} />
          <button type="button" className="btn btn-sm btn-outline-danger" onClick={() => onChange(value.filter((_, j) => j !== i))}><i className="ti ti-trash" /></button>
        </div>
      ))}
      <button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => onChange([...value, { discount_pct: "", min_qty: "0", start_date: "", end_date: "" }])}><i className="ti ti-plus me-1" />Add Offer</button>
    </div>
  );
}

export function ProductsPage() {
  const productsPage = usePage("products");
  const stats = useQuery({ queryKey: ["product-stats"], queryFn: () => api.get("/api/v1/products/stats") });
  const cols: Column<any>[] = [
    { key: "name", header: "Product", sortKey: "name", render: (p) => <><span className="fw-semibold text-dark">{p.name}</span><div className="text-muted fs-11 font-monospace">{[p.upc, p.item_code && p.item_code !== p.upc ? p.item_code : null].filter(Boolean).join(" · ") || p.sku}{Number(p.packing) > 1 ? ` · ${p.packing}/box` : ""}{p.variants_count > 1 ? ` · ${p.variants_count} variants` : ""}</div></> },
    { key: "brand_name", header: "Brand", sortKey: "brand__name", render: (p) => (p.brand_name ? <span className="badge bg-info-light text-info">{p.brand_name}</span> : "—") }, { key: "category_name", header: "Category", sortKey: "category__name" },
    { key: "unit_size", header: "Unit", sortKey: "unit_size", render: (p) => p.unit_size || <span className="text-muted">{p.base_uom_code}</span> }, { key: "packing", header: "Packing", num: true, sortKey: "packing", render: (p) => (p.packing > 1 ? p.packing : <span className="text-muted">1</span>) },
    { key: "sale_price", header: "T.P", num: true, sortKey: "sale_price", render: (p) => <span className="fw-semibold">{money(p.sale_price)}</span> },
    { key: "location_name", header: "Location", sortKey: "location__name", render: (p) => (p.location_name ? <span className="badge bg-secondary-light text-secondary">{p.location_name}</span> : <span className="text-muted">—</span>) },
    { key: "stock_on_hand", header: "Stock", num: true, sortKey: "stock_on_hand", render: (p) => <span className={Number(p.stock_on_hand || 0) < Number(p.min_stock) ? "text-danger fw-semibold" : ""}>{qty(p.stock_on_hand || 0)} <span className="text-muted fs-11">{p.base_uom_code}</span></span> },
    { key: "status", header: "Status", sortKey: "status", render: (p) => <><StatusBadge status={p.status} />{p.is_batch_tracked && <span className="badge bg-secondary-light text-secondary ms-1">batch</span>}</> },
  ];
  return (
    <ListPage title="Products" endpoint="/api/v1/products" exports={false} columns={cols} rowLink={(p) => `/app/catalog/products/${p.id}`} page="products" createTo="/app/catalog/products/new" createLabel="Add product" defaultOrdering="name" extraQuery={{ with_stock: 1 }} searchPlaceholder="Search name, SKU, barcode…"
      filters={[{ name: "brand", label: "Brand", type: "entity", endpoint: "/api/v1/brands", mapOption: (b) => ({ id: b.id, label: b.name }) }, { name: "category", label: "Category", type: "entity", endpoint: "/api/v1/categories", mapOption: (c) => ({ id: c.id, label: c.name }) }, { name: "supplier", label: "Supplier", type: "entity", endpoint: "/api/v1/suppliers", mapOption: (s) => ({ id: s.id, label: s.name }) }, { name: "location", label: "Location", type: "entity", endpoint: "/api/v1/settings/bins", mapOption: (b) => ({ id: b.id, label: b.label || b.code, sub: b.warehouse_name }) }, { name: "status", label: "Status", type: "select", options: ["active", "inactive", "archived"].map((s) => ({ value: s, label: s })) }, { name: "low_stock", label: "Low stock", type: "toggle" }]}
      actions={<>{productsPage.add && <ImportButton type="products" label="Import price list (Excel)" />}</>}
      stats={stats.data && <div className="row g-3 mb-3"><div className="col-6 col-md-3"><StatCard label="Products" value={stats.data.total} icon="ti-box" color="primary" sub={`${stats.data.variants} variants`} /></div><div className="col-6 col-md-3"><StatCard label="Active" value={stats.data.active} icon="ti-circle-check" color="success" /></div><div className="col-6 col-md-3"><StatCard label="Inactive / archived" value={stats.data.inactive} icon="ti-ban" color="secondary" /></div><div className="col-6 col-md-3"><StatCard label="Low stock" value={stats.data.low_stock} icon="ti-alert-triangle" color="warning" to="/app/inventory/alerts" /></div></div>} />
  );
}

export function ProductFormPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const q = useDoc("/api/v1/products", id);
  if (id && q.isLoading) return <Spinner />;
  return (
    <>
      <PageHeader title={id ? `Edit ${q.data?.name}` : "New product"} crumbs={[{ label: "Products", to: "/app/catalog/products" }, { label: id ? "Edit" : "New" }]} />
      <div className="card"><div className="card-body">
        <ResourceForm fields={productFields(!id)} initial={q.data ? { ...q.data, offers_write: (q.data.offers || []).map((o: any) => ({ discount_pct: o.discount_pct, min_qty: o.min_qty, start_date: o.start_date, end_date: o.end_date })) } : q.data} onCancel={() => nav(-1)} onSubmit={async (v) => { const r = id ? await api.patch(`/api/v1/products/${id}`, v) : await api.post("/api/v1/products", v); toast.success("Saved"); nav(`/app/catalog/products/${r.id}`); }} />
        {!id && <div className="form-text mt-2">A default variant, base packaging and barcodes can be added on the product page after saving. For shade/size variants add them there too.</div>}
      </div></div>
    </>
  );
}

export function ProductDetailPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const page = usePage("products");
  const q = useDoc("/api/v1/products", id);
  const [tab, setTab] = useState("variants");
  const [variantModal, setVariantModal] = useState<{ open: boolean; row?: any }>({ open: false });
  const [packModal, setPackModal] = useState<{ open: boolean; variant?: any; row?: any }>({ open: false });
  const [bcModal, setBcModal] = useState<{ open: boolean; variant?: any; row?: any }>({ open: false });
  const stock = useQuery({ queryKey: ["product-stock", id], queryFn: () => api.list("/api/v1/inventory/stock", { variant__product: id, page_size: 200 }), enabled: tab === "stock" });
  const movements = useQuery({ queryKey: ["product-movements", id], queryFn: () => api.get("/api/v1/inventory/movements", { variant__product: id, page_size: 100 }), enabled: tab === "movements" });
  const prices = useQuery({ queryKey: ["product-prices", id], queryFn: async () => { const pls = await api.get("/api/v1/price-lists"); const out: any[] = []; for (const pl of pls) { const items = await api.get(`/api/v1/price-lists/${pl.id}/items`); out.push(...items.filter((i: any) => q.data?.variants.some((v: any) => v.id === i.variant)).map((i: any) => ({ ...i, price_list_name: pl.name }))); } return out; }, enabled: tab === "prices" && Boolean(q.data) });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["doc", "/api/v1/products", id] });
  if (q.isLoading) return <Spinner />;
  const p = q.data;
  if (!p) return null;
  const totalStock = p.variants.reduce((s: number, v: any) => s + Number(v.stock_on_hand || 0), 0);
  return (
    <>
      <PageHeader title={p.name} badge={<StatusBadge status={p.status} />} crumbs={[{ label: "Products", to: "/app/catalog/products" }, { label: p.name }]}
        actions={<>{page.edit && p.status !== "archived" && <button className={`btn ${p.status === "active" ? "btn-outline-warning" : "btn-outline-success"}`} onClick={async () => {
          const next = p.status === "active" ? "inactive" : "active";
          try { await api.patch(`/api/v1/products/${p.id}`, { status: next }); toast.success(next === "active" ? "Product activated" : "Product set inactive"); qc.invalidateQueries({ queryKey: ["doc", "/api/v1/products", id] }); qc.invalidateQueries({ queryKey: ["list", "/api/v1/products"] }); }
          catch (e: any) { toast.error(e.message); }
        }}><i className={`ti ${p.status === "active" ? "ti-toggle-right" : "ti-toggle-left"} me-1`} />{p.status === "active" ? "Set inactive" : "Activate"}</button>}{page.can("print_barcode") && <Link to={`/app/catalog/labels?product=${p.id}`} className="btn btn-outline-secondary"><i className="ti ti-barcode me-1" />Labels</Link>}{pageRights("products").edit && <Link to={`/app/catalog/products/${p.id}/edit`} className="btn btn-outline-primary"><i className="ti ti-edit me-1" />Edit</Link>}{pageRights("products").edit && <button className="btn btn-primary" onClick={() => setVariantModal({ open: true })}><i className="ti ti-plus me-1" />Add variant</button>}</>} />
      <div className="row g-3 mb-3"><div className="col-6 col-md-3"><StatCard label="T.P — trade price" value={money(p.sale_price)} icon="ti-tag" color="primary" sub={p.price_effective_date ? `price list of ${date(p.price_effective_date)}` : undefined} /></div><div className="col-6 col-md-3"><StatCard label="Stock on hand" value={qty(totalStock)} icon="ti-box" color={totalStock < Number(p.min_stock) ? "danger" : "success"} sub={`min ${qty(p.min_stock)} · max ${qty(p.max_stock)} ${p.base_uom_code}`} /></div><div className="col-6 col-md-3"><StatCard label="Variants" value={p.variants.length} icon="ti-color-swatch" color="info" /></div><div className="col-6 col-md-3"><StatCard label="Tracking" value={p.is_batch_tracked ? "Batch" + (p.is_expiry_tracked ? " + expiry" : "") : "None"} icon="ti-calendar-event" color="secondary" sub={p.shelf_life_days ? `${p.shelf_life_days} days shelf life` : ""} /></div></div>
      <div className="card mb-3"><div className="card-body"><div className="row g-3"><InfoItem label="UPC (unit)">{p.upc || p.sku}</InfoItem><InfoItem label="Item code">{p.item_code || "—"}</InfoItem><InfoItem label="Box">{Number(p.packing) > 1 ? <>{p.packing} per box{p.box_upc ? <div className="fs-11 text-muted font-monospace">{p.box_upc}</div> : null}</> : "loose units"}</InfoItem><InfoItem label="Brand / category">{p.brand_name || "—"}<div className="fs-12 text-muted">{p.category_name}</div></InfoItem><InfoItem label="Unit · packing">{p.unit_size || p.base_uom_code}<div className="fs-12 text-muted">{p.packing > 1 ? `${p.packing} per carton` : "sold per unit"}</div></InfoItem><InfoItem label="Location">{p.location_name || "—"}</InfoItem><InfoItem label="Supplier">{p.supplier_name ? <Link to={`/app/suppliers/${p.supplier}`}>{p.supplier_name}</Link> : "—"}</InfoItem><InfoItem label="Tax class">{p.tax_class_name || "default"}</InfoItem></div>{p.description && <p className="fs-13 text-muted mt-3 mb-0">{p.description}</p>}</div></div>
      <div className="card"><div className="card-body">
        <Tabs tabs={[{ key: "variants", label: "Variants, packaging & barcodes", count: p.variants.length }, { key: "stock", label: "Stock by warehouse" }, { key: "movements", label: "Movements" }, { key: "prices", label: "Price lists" }, { key: "attachments", label: "Images & files" }, { key: "history", label: "History" }]} active={tab} onChange={setTab} />
        {tab === "variants" && p.variants.map((v: any) => (
          <div key={v.id} className="border rounded p-3 mb-3">
            <div className="d-flex justify-content-between align-items-start flex-wrap gap-2">
              <div><div className="fw-semibold">{v.name} {v.is_default && <span className="badge bg-secondary-light text-secondary ms-1">default</span>} {!v.is_active && <StatusBadge status="inactive" />}</div><div className="fs-12 text-muted font-monospace">{v.sku} · {Object.entries(v.attributes || {}).map(([k, val]) => `${k}: ${val}`).join(", ")}</div><div className="fs-12 mt-1">Sale {money(v.sale_price)} · stock {qty(v.stock_on_hand || 0)}</div></div>
              {page.edit && <div className="d-flex gap-1"><button className="btn btn-sm btn-outline-secondary" onClick={() => setVariantModal({ open: true, row: v })}><i className="ti ti-edit" /></button><button className="btn btn-sm btn-outline-primary" onClick={() => setPackModal({ open: true, variant: v })}><i className="ti ti-package me-1" />Packaging</button><button className="btn btn-sm btn-outline-primary" onClick={() => setBcModal({ open: true, variant: v })}><i className="ti ti-barcode me-1" />Barcode</button><button className="btn btn-sm btn-outline-secondary" onClick={async () => { await api.post(`/api/v1/variants/${v.id}/generate-barcode`); toast.success("Internal barcode generated"); invalidate(); }}>Generate internal</button></div>}
            </div>
            <div className="row mt-2"><div className="col-md-6"><div className="fs-11 text-muted mb-1">Packaging levels{page.edit && " — click one to edit"}</div><table className="table table-sm fs-12 mb-0"><tbody>{v.packagings.map((pk: any) => <tr key={pk.id} role={page.edit ? "button" : undefined} className={page.edit ? "cursor-pointer" : undefined} onClick={page.edit ? () => setPackModal({ open: true, variant: v, row: pk }) : undefined}><td>{pk.name}</td><td className="num">× {qty(pk.qty_in_base)} {v.base_uom}</td><td className="font-monospace">{pk.barcode}</td><td>{pk.is_default_sales && <span className="badge bg-info-light text-info">default sales</span>}</td></tr>)}</tbody></table></div>
              <div className="col-md-6"><div className="fs-11 text-muted mb-1">Barcodes{page.edit && " — click one to correct it"}</div><table className="table table-sm fs-12 mb-0"><tbody>{v.barcodes.map((b: any) => <tr key={b.id} role={page.edit ? "button" : undefined} className={page.edit ? "cursor-pointer" : undefined} onClick={page.edit ? () => setBcModal({ open: true, variant: v, row: b }) : undefined}><td className="font-monospace">{b.code}</td><td>{b.type}</td><td>{b.packaging_name || "base"}</td><td>{b.is_primary && <span className="badge bg-success-light text-success">primary</span>}</td></tr>)}{!v.barcodes.length && <tr><td className="text-muted">No barcodes — scanning will use the SKU</td></tr>}</tbody></table></div></div>
          </div>
        ))}
        {tab === "stock" && (stock.isLoading ? <Spinner /> : <table className="table table-sm fs-12"><thead className="table-light"><tr><th>Warehouse</th><th>Variant</th><th>Batch</th><th>Expiry</th><th className="num">On hand</th><th className="num">Reserved</th><th className="num">Available</th><th className="num">Value</th></tr></thead><tbody>{stock.data?.results.map((s: any) => <tr key={s.id}><td>{s.warehouse_name}{s.bin_code ? ` / ${s.bin_code}` : ""}</td><td>{s.variant_name}</td><td>{s.batch_no}</td><td>{s.expiry_date || ""}</td><td className="num">{qty(s.qty_on_hand)}</td><td className="num">{qty(s.qty_reserved)}</td><td className="num fw-medium">{qty(s.qty_available)}</td><td className="num">{money(s.value)}</td></tr>)}</tbody></table>)}
        {tab === "movements" && (movements.isLoading ? <Spinner /> : <table className="table table-sm fs-12"><thead className="table-light"><tr><th>Date</th><th>Warehouse</th><th>Type</th><th>Reference</th><th>Batch</th><th className="num">Qty</th><th className="num">Unit cost</th><th>By</th></tr></thead><tbody>{movements.data?.results.map((m: any) => <tr key={m.id}><td>{datetime(m.occurred_at)}</td><td>{m.warehouse_name}</td><td>{m.movement_type}</td><td>{m.reference_number}</td><td>{m.batch_no}</td><td className={`num ${Number(m.qty) < 0 ? "text-danger" : "text-success"}`}>{qty(m.qty)}</td><td className="num">{money(m.unit_cost)}</td><td>{m.performed_by_name}</td></tr>)}</tbody></table>)}
        {tab === "prices" && (prices.isLoading ? <Spinner /> : <table className="table table-sm fs-12"><thead className="table-light"><tr><th>Price list</th><th>Variant</th><th>Packaging</th><th className="num">Min qty</th><th className="num">Price</th></tr></thead><tbody>{prices.data?.map((i: any) => <tr key={i.id}><td>{i.price_list_name}</td><td>{i.variant_name}</td><td>{i.packaging_name || "base"}</td><td className="num">{qty(i.min_qty)}</td><td className="num">{money(i.price)}</td></tr>)}{prices.data && !prices.data.length && <tr><td className="text-muted">No price list entries — the variant sale price applies. <Link to="/app/catalog/price-lists">Manage price lists</Link></td></tr>}</tbody></table>)}
        {tab === "attachments" && <AttachmentsTab model="Product" id={p.id} />}
        {tab === "history" && <HistoryTab model="Product" id={p.id} />}
      </div></div>
      <ResourceModal open={variantModal.open} onClose={() => setVariantModal({ open: false })} title={variantModal.row ? "Edit variant" : "New variant"} endpoint={variantModal.row ? "/api/v1/variants" : `/api/v1/products/${p.id}/variants`} id={variantModal.row?.id} initial={variantModal.row} onSaved={invalidate}
        fields={[{ name: "name", label: "Variant name", required: true, col: "col-md-8", placeholder: "e.g. ColorMax 7.1 Ash Blonde 60ml" }, { name: "sku", label: "Variant SKU", required: true, col: "col-md-4" }, { name: "attributes", label: "Attributes (JSON: shade_code, size_ml…)", type: "json", col: "col-md-6", default: { shade_code: "", size_ml: "" } }, { name: "cost_price", label: "Cost price", type: "money", col: "col-md-3", default: p.cost_price }, { name: "sale_price", label: "Sale price", type: "money", col: "col-md-3", default: p.sale_price }, { name: "is_default", label: "Default variant", type: "checkbox", col: "col-md-3" }, { name: "is_active", label: "Active", type: "checkbox", default: true, col: "col-md-3" }]} />
      <ResourceModal open={packModal.open} onClose={() => setPackModal({ open: false })} title={`${packModal.row ? "Edit" : "Add"} packaging — ${packModal.variant?.name}`} endpoint={packModal.row ? "/api/v1/packagings" : `/api/v1/variants/${packModal.variant?.id}/packagings`} id={packModal.row?.id} initial={packModal.row} onSaved={invalidate}
        fields={[{ name: "name", label: "Name", required: true, col: "col-md-4", placeholder: "box / carton" }, { name: "uom", label: "Unit", type: "entity", endpoint: "/api/v1/uoms", mapOption: (u) => ({ id: u.id, label: u.code }), required: true, col: "col-md-4" }, { name: "qty_in_base", label: `Qty in base (${p.base_uom_code})`, type: "number", required: true, col: "col-md-4" }, { name: "barcode", label: "Packaging barcode", col: "col-md-8" }, { name: "is_default_sales", label: "Default for sales", type: "checkbox", col: "col-md-4" }]} />
      {/* Editing goes to the flat route, which — unlike the nested create — does not demote the variant's other primary barcode. */}
      <ResourceModal open={bcModal.open} onClose={() => setBcModal({ open: false })} title={`${bcModal.row ? "Edit" : "Add"} barcode — ${bcModal.variant?.name}`} endpoint={bcModal.row ? "/api/v1/barcodes" : `/api/v1/variants/${bcModal.variant?.id}/barcodes`} id={bcModal.row?.id} initial={bcModal.row} onSaved={invalidate}
        fields={[{ name: "code", label: "Barcode", required: true, col: "col-md-6" }, { name: "type", label: "Type", type: "select", options: ["EAN13", "UPC", "CODE128", "QR", "internal"].map((t) => ({ value: t, label: t })), default: "EAN13", col: "col-md-3" }, { name: "packaging", label: "Packaging level", type: "select", options: (bcModal.variant?.packagings || []).map((pk: any) => ({ value: pk.id, label: pk.name })), col: "col-md-3" }, { name: "is_primary", label: "Primary", type: "checkbox", col: "col-md-3" }]} />
    </>
  );
}

export function CategoriesPage() {
  return <CrudPage page="categories" title="Categories" crumbs={[{ label: "Products", to: "/app/catalog/products" }, { label: "Categories" }]} endpoint="/api/v1/categories" columns={[{ key: "path", header: "Category" }, { key: "products_count", header: "Products", num: true }, { key: "is_active", header: "Active", render: (c: any) => (c.is_active ? "Yes" : "No") }]} fields={[{ name: "name", label: "Name", required: true }, { name: "parent", label: "Parent", type: "entity", endpoint: "/api/v1/categories", mapOption: (c) => ({ id: c.id, label: c.path || c.name }) }, { name: "is_active", label: "Active", type: "checkbox", default: true }]} />;
}
export function BrandsPage() {
  return <CrudPage page="brands" title="Brands" crumbs={[{ label: "Products", to: "/app/catalog/products" }, { label: "Brands" }]} endpoint="/api/v1/brands" columns={[{ key: "name", header: "Brand" }, { key: "supplier_name", header: "Supplier" }, { key: "products_count", header: "Products", num: true }, { key: "is_active", header: "Active", render: (b: any) => (b.is_active ? "Yes" : "No") }]} fields={[{ name: "name", label: "Name", required: true }, { name: "supplier", label: "Supplier", type: "entity", endpoint: "/api/v1/suppliers", mapOption: (s) => ({ id: s.id, label: s.name }) }, { name: "is_active", label: "Active", type: "checkbox", default: true }]} />;
}
export function UomsPage() {
  return <CrudPage page="uoms" title="Units of measure" crumbs={[{ label: "Products", to: "/app/catalog/products" }, { label: "Units" }]} endpoint="/api/v1/uoms" columns={[{ key: "code", header: "Code" }, { key: "name", header: "Name" }]} fields={[{ name: "code", label: "Code", required: true }, { name: "name", label: "Name", required: true }]} />;
}

export function PriceListsPage() {
  const qc = useQueryClient();
  const lists = useOptions("/api/v1/price-lists");
  const [active, setActive] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [listModal, setListModal] = useState<{ open: boolean; row?: any }>({ open: false });
  const [item, setItem] = useState<any>({ variant: null, packaging: null, min_qty: "0", price: "" });
  const current = active || lists.data?.[0]?.id;
  const items = useQuery({ queryKey: ["pl-items", current, search], queryFn: () => api.get(`/api/v1/price-lists/${current}/items`, { search: search || undefined }), enabled: Boolean(current) });
  const priceLists = usePage("price_lists");
  const canEdit = priceLists.edit;
  return (
    <>
      <PageHeader title="Price lists" crumbs={[{ label: "Products", to: "/app/catalog/products" }, { label: "Price lists" }]} actions={<><Link to="/app/settings/imports?type=price_list" className="btn btn-outline-primary"><i className="ti ti-upload me-1" />Import CSV</Link>{canEdit && <button className="btn btn-primary" onClick={() => setListModal({ open: true })}><i className="ti ti-plus me-1" />New price list</button>}</>} />
      <div className="row g-3">
        <div className="col-md-3"><div className="card"><div className="list-group list-group-flush">{lists.data?.map((pl) => <a key={pl.id} className={`list-group-item list-group-item-action cursor-pointer d-flex justify-content-between ${pl.id === current ? "active" : ""}`} onClick={() => setActive(pl.id)}><span>{pl.name}{pl.is_default && <span className="fs-11 ms-1">(default)</span>}</span><span className="badge bg-light text-dark">{pl.items_count}</span></a>)}</div>{canEdit && current && <div className="card-footer"><button className="btn btn-sm btn-outline-secondary w-100" onClick={() => setListModal({ open: true, row: lists.data?.find((l) => l.id === current) })}>Edit price list</button></div>}</div></div>
        <div className="col-md-9"><div className="card"><div className="card-body">
          {canEdit && <div className="row g-2 align-items-end mb-3"><div className="col-md-5"><label className="form-label fs-12">Product</label><EntitySelect endpoint="/api/v1/variants" query={{ is_active: true }} value={item.variant} onChange={(v, raw) => setItem({ ...item, variant: v, packagings: raw?.packagings || [] })} mapOption={(v) => ({ id: v.id, label: v.name, sub: v.sku, raw: v })} /></div><div className="col-md-2"><label className="form-label fs-12">Packaging</label><select className="form-select" value={item.packaging || ""} onChange={(e) => setItem({ ...item, packaging: e.target.value || null })}><option value="">base</option>{(item.packagings || []).map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div><div className="col-md-2"><label className="form-label fs-12">Min qty</label><input type="number" className="form-control" value={item.min_qty} onChange={(e) => setItem({ ...item, min_qty: e.target.value })} /></div><div className="col-md-2"><label className="form-label fs-12">Price</label><input type="number" step="0.01" className="form-control" value={item.price} onChange={(e) => setItem({ ...item, price: e.target.value })} /></div><div className="col-md-1"><button className="btn btn-primary w-100" disabled={!item.variant || !item.price} onClick={async () => { await api.post(`/api/v1/price-lists/${current}/items`, { variant: item.variant, packaging: item.packaging, min_qty: item.min_qty, price: item.price }); toast.success("Price saved"); setItem({ ...item, price: "" }); qc.invalidateQueries({ queryKey: ["pl-items", current] }); }}><i className="ti ti-plus" /></button></div></div>}
          <input className="form-control mb-2" placeholder="Search item…" value={search} onChange={(e) => setSearch(e.target.value)} />
          <table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>SKU</th><th>Item</th><th>Packaging</th><th className="num">Min qty</th><th className="num">Price</th><th /></tr></thead><tbody>{items.data?.map((i: any) => <tr key={i.id}><td className="font-monospace">{i.variant_sku}</td><td>{i.variant_name}</td><td>{i.packaging_name || "base"}</td><td className="num">{qty(i.min_qty)}</td><td className="num fw-medium">{money(i.price)}</td><td className="text-end">{canEdit && <button className="btn btn-sm btn-link text-danger p-0" onClick={async () => { await api.delete(`/api/v1/price-lists/${current}/items/${i.id}`); qc.invalidateQueries({ queryKey: ["pl-items", current] }); }}><i className="ti ti-trash" /></button>}</td></tr>)}{items.data && !items.data.length && <tr><td colSpan={6} className="text-muted text-center py-3">No prices in this list yet</td></tr>}</tbody></table>
        </div></div></div>
      </div>
      <ResourceModal open={listModal.open} onClose={() => setListModal({ open: false })} title={listModal.row ? "Edit price list" : "New price list"} endpoint="/api/v1/price-lists" id={listModal.row?.id} initial={listModal.row} onSaved={() => qc.invalidateQueries({ queryKey: ["options", "/api/v1/price-lists"] })} fields={[{ name: "name", label: "Name", required: true, col: "col-md-6" }, { name: "currency", label: "Currency", default: "PKR", col: "col-md-3" }, { name: "is_default", label: "Default", type: "checkbox", col: "col-md-3" }, { name: "valid_from", label: "Valid from", type: "date", col: "col-md-4" }, { name: "valid_to", label: "Valid to", type: "date", col: "col-md-4" }, { name: "is_active", label: "Active", type: "checkbox", default: true, col: "col-md-4" }]} />
    </>
  );
}

export function SchemesPage() {
  const rulesRender = (value: any, set: (v: any) => void, values: Record<string, any>) => {
    const r = typeof value === "string" ? (() => { try { return JSON.parse(value); } catch { return {}; } })() : value || {};
    const upd = (k: string, v: any) => set({ ...r, [k]: v });
    return <div className="row g-2">{values.type === "free_qty" ? <><div className="col-4"><input type="number" className="form-control" placeholder="Buy qty" value={r.buy_qty || ""} onChange={(e) => upd("buy_qty", e.target.value)} /></div><div className="col-4"><input type="number" className="form-control" placeholder="Free qty" value={r.free_qty || ""} onChange={(e) => upd("free_qty", e.target.value)} /></div></> : <div className="col-4"><input type="number" step="0.01" className="form-control" placeholder={values.type === "percent" ? "% off" : "amount off per unit"} value={r.value || ""} onChange={(e) => upd("value", e.target.value)} /></div>}<div className="col-4"><input type="number" className="form-control" placeholder="Min qty" value={r.min_qty || ""} onChange={(e) => upd("min_qty", e.target.value)} /></div></div>;
  };
  return <CrudPage page="schemes" title="Schemes & promotions" crumbs={[{ label: "Products", to: "/app/catalog/products" }, { label: "Schemes" }]} endpoint="/api/v1/schemes" columns={[{ key: "name", header: "Scheme" }, { key: "type", header: "Type", render: (s: any) => titleCase(s.type) }, { key: "rules", header: "Rule", render: (s: any) => (s.type === "free_qty" ? `Buy ${s.rules.buy_qty} get ${s.rules.free_qty} free` : `${s.rules.value}${s.type === "percent" ? "%" : ""} off${s.rules.min_qty ? ` from ${s.rules.min_qty} units` : ""}`) }, { key: "valid_from", header: "Valid", render: (s: any) => `${s.valid_from || "…"} → ${s.valid_to || "…"}` }, { key: "customer_types", header: "Customer types", render: (s: any) => s.customer_types?.join(", ") || "all" }, { key: "is_active", header: "Active", render: (s: any) => <StatusBadge status={s.is_active ? "active" : "inactive"} /> }]}
    fields={[{ name: "name", label: "Name", required: true, col: "col-md-8" }, { name: "type", label: "Type", type: "select", options: [{ value: "percent", label: "Percent off" }, { value: "amount", label: "Amount off per unit" }, { value: "free_qty", label: "Buy X get Y free" }], required: true, col: "col-md-4", default: "percent" }, { name: "rules", label: "Rule", type: "custom", render: rulesRender, col: "col-md-12", default: {} }, { name: "valid_from", label: "Valid from", type: "date", col: "col-md-3" }, { name: "valid_to", label: "Valid to", type: "date", col: "col-md-3" }, { name: "is_active", label: "Active", type: "checkbox", default: true, col: "col-md-3" }, { name: "customer_types", label: "Customer types (JSON list, empty = all)", type: "json", default: [], col: "col-md-6" }, { name: "brands", label: "Brands (JSON list of ids, empty = all)", type: "json", default: [], col: "col-md-6" }]}
    intro={<Alert kind="info">Schemes apply automatically when orders are priced. Restrict by brand or customer type, or leave both empty for all products.</Alert>} />;
}

export function LabelsPage() {
  const [items, setItems] = useState<any[]>([]);
  const [size, setSize] = useState("50x30");
  const [pick, setPick] = useState<any>({ variant: null, packaging: null, qty: 1, show_price: false, packagings: [] });
  return (
    <>
      <PageHeader title="Barcode labels" crumbs={[{ label: "Products", to: "/app/catalog/products" }, { label: "Labels" }]} />
      <div className="card mb-3"><div className="card-body"><div className="row g-2 align-items-end">
        <div className="col-md-5"><label className="form-label fs-12">Product</label><EntitySelect endpoint="/api/v1/variants" value={pick.variant} onChange={(v, raw) => setPick({ ...pick, variant: v, name: raw?.name, packagings: raw?.packagings || [] })} mapOption={(v) => ({ id: v.id, label: v.name, sub: v.sku, raw: v })} /></div>
        <div className="col-md-2"><label className="form-label fs-12">Packaging</label><select className="form-select" value={pick.packaging || ""} onChange={(e) => setPick({ ...pick, packaging: e.target.value || null })}><option value="">base unit</option>{pick.packagings.map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>
        <div className="col-md-1"><label className="form-label fs-12">Qty</label><input type="number" className="form-control" value={pick.qty} onChange={(e) => setPick({ ...pick, qty: Number(e.target.value) })} /></div>
        <div className="col-md-2"><div className="form-check mt-4"><input className="form-check-input" type="checkbox" id="sp" checked={pick.show_price} onChange={(e) => setPick({ ...pick, show_price: e.target.checked })} /><label htmlFor="sp" className="form-check-label">Show price</label></div></div>
        <div className="col-md-2"><button className="btn btn-outline-primary w-100" disabled={!pick.variant} onClick={() => setItems([...items, { ...pick }])}>Add to sheet</button></div>
      </div></div></div>
      <div className="card"><div className="card-body">
        <div className="d-flex justify-content-between align-items-center mb-2"><div className="d-flex gap-2 align-items-center"><span className="fs-13">Label size</span><select className="form-select form-select-sm w-auto" value={size} onChange={(e) => setSize(e.target.value)}><option value="38x25">38 × 25 mm</option><option value="50x30">50 × 30 mm</option><option value="100x50">100 × 50 mm</option></select></div><span className="fs-12 text-muted">{items.reduce((s, i) => s + i.qty, 0)} labels</span></div>
        <table className="table table-sm fs-13"><thead className="table-light"><tr><th>Product</th><th>Packaging</th><th className="num">Labels</th><th /></tr></thead><tbody>{items.map((it, i) => <tr key={i}><td>{it.name}</td><td>{it.packagings.find((p: any) => p.id === it.packaging)?.name || "base"}</td><td className="num">{it.qty}</td><td className="text-end"><button className="btn btn-sm btn-link text-danger" onClick={() => setItems(items.filter((_, j) => j !== i))}><i className="ti ti-trash" /></button></td></tr>)}{!items.length && <tr><td colSpan={4} className="text-muted text-center py-3">Add products to the label sheet</td></tr>}</tbody></table>
        <PrintButton items={items} size={size} />
      </div></div>
    </>
  );
}

function PrintButton({ items, size }: { items: any[]; size: string }) {
  const print = async () => {
    try { await api.postBlob("/api/v1/labels/print", { size, items: items.map((i) => ({ variant: i.variant, packaging: i.packaging, qty: i.qty, show_price: i.show_price })) }); } catch (e: any) { toast.error(e.message); }
  };
  return <div className="text-end"><button className="btn btn-primary" disabled={!items.length} onClick={print}><i className="ti ti-printer me-1" />Generate label PDF</button></div>;
}


/** Warehouse storage locations (racks / shelves / bins) — products point at one as their default location. */
export function LocationsPage() {
  const warehouses = useOptions("/api/v1/settings/warehouses", { is_active: true });
  return (
    <CrudPage page="settings_locations" title="Locations" crumbs={[{ label: "Settings" }, { label: "Locations" }]} endpoint="/api/v1/settings/bins"
      intro={<div className="alert alert-light border fs-13"><i className="ti ti-info-circle me-1 text-primary" />A location is a rack, shelf or bin inside a warehouse. Products carry a default location, stock movements can record one, and pick lists are sorted by it. A location that still holds stock cannot be deleted.</div>}
      columns={[{ key: "code", header: "Location", render: (b: any) => <span className="fw-semibold">{b.code}</span> }, { key: "name", header: "Description", render: (b: any) => b.name || "—" }, { key: "warehouse_name", header: "Warehouse" }, { key: "products_count", header: "Products", num: true }]}
      fields={[{ name: "warehouse", label: "Warehouse", type: "select", required: true, col: "col-md-6", options: (warehouses.data || []).map((w: any) => ({ value: w.id, label: w.name })) },
        { name: "code", label: "Location code", required: true, col: "col-md-3", placeholder: "A-01" }, { name: "name", label: "Description", col: "col-md-3", placeholder: "Rack A, shelf 1" }]} />
  );
}
