import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { date, money, titleCase } from "@/lib/format";
import { useDoc, useOptions } from "@/lib/hooks";
import { pageRights, usePage, usePerms } from "@/lib/perms";
import { toast } from "@/lib/toast";
import { Alert, InfoItem, PageHeader, Spinner, StatCard, StatusBadge, Tabs } from "@/components/ui/Bits";
import { CrudPage } from "@/components/ui/CrudPage";
import { Column } from "@/components/ui/DataTable";
import { ListPage } from "@/components/ui/ListPage";
import { ConfirmDialog } from "@/components/ui/Modal";
import { CitySelect } from "@/components/ui/CitySelect";
import { FieldDef, ResourceForm } from "@/components/ui/ResourceForm";
import { AttachmentsTab, HistoryTab } from "@/components/documents/DocTabs";
import { StorePinPicker } from "./StorePinPicker";

const TYPES = [{ value: "retailer", label: "Retailer" }, { value: "salon", label: "Salon" }, { value: "wholesaler", label: "Wholesaler" }, { value: "lmt", label: "LMT" }, { value: "other", label: "Other" }];

/** How this shop's pin was set, in words the office can judge before approving — "Set at the shop ±8 m"
 *  beats "Placed on the map — rep was 3.1 km away" every time an order later measures against the pin. */
function pinStory(c: any): string {
  const dist = c.pin_rep_distance_m != null ? Number(c.pin_rep_distance_m) : null;
  const far = dist == null ? "" : dist < 100 ? ` ±${Math.round(dist)} m` : ` — rep was ${(dist / 1000).toFixed(1)} km away`;
  switch (c.pin_source) {
    case "gps": return `Set standing at the shop${far}`;
    case "search": case "map": return `Placed on the map${far}`;
    case "coordinates": return `Typed as coordinates${far}`;
    case "link": return `From a shared maps link${far}`;
    default: return c.requested_by_rep ? `Set by the rep${far}` : "Set by the office";
  }
}

function LocationsEditor({ value, onChange }: { value: any[]; onChange: (v: any[]) => void }) {
  const row = (r: any, i: number, patch: any) => { const next = [...value]; next[i] = { ...r, ...patch }; onChange(next); };
  return (
    <div>
      {value.map((r, i) => (
        <div className="d-flex flex-wrap gap-2 align-items-center mb-1" key={i}>
          <input className="form-control form-control-sm" style={{ width: 180 }} placeholder="Shop name *" value={r.name ?? ""} onChange={(e) => row(r, i, { name: e.target.value })} />
          <input className="form-control form-control-sm" style={{ width: 220 }} placeholder="Address" value={r.address ?? ""} onChange={(e) => row(r, i, { address: e.target.value })} />
          <input className="form-control form-control-sm" style={{ width: 260 }} placeholder="Maps link or lat, lng" value={r.map_url ?? (r.gps_lat ? `${r.gps_lat}, ${r.gps_lng}` : "")} onChange={(e) => row(r, i, { map_url: e.target.value, gps_lat: null, gps_lng: null })} />
          <input type="number" className="form-control form-control-sm" style={{ width: 90 }} placeholder="radius m" value={r.order_radius_m ?? ""} onChange={(e) => row(r, i, { order_radius_m: e.target.value })} />
          <button type="button" className="btn btn-sm btn-outline-danger" onClick={() => onChange(value.filter((_, j) => j !== i))}><i className="ti ti-trash" /></button>
        </div>
      ))}
      <button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => onChange([...value, { name: "", address: "", map_url: "", order_radius_m: "" }])}><i className="ti ti-plus me-1" />Add Shop</button>
    </div>
  );
}

export function CustomersPage() {
  const stats = useQuery({ queryKey: ["customer-stats"], queryFn: () => api.get("/api/v1/customers/stats") });
  const { can } = usePerms();
  const cols: Column<any>[] = [
    { key: "name", header: "Customer", sortKey: "name", render: (c) => (
      <><span className="fw-semibold text-dark">{c.name}</span>{Number(c.default_discount_pct) > 0 && <span className="badge bg-success-light text-success ms-2">{Number(c.default_discount_pct)}% off</span>}
        <div className="text-muted fs-11 font-monospace">{c.code}</div>
        {c.business_name && c.business_name !== c.name && <div className="text-muted fs-11">{c.business_name}</div>}</>
    ) },
    { key: "type", header: "Type", render: (c) => <>{titleCase(c.type)}{c.ntn || c.strn ? <div className="text-muted fs-11">{c.ntn ? `NTN ${c.ntn}` : ""}{c.ntn && c.strn ? " · " : ""}{c.strn ? `STRN ${c.strn}` : ""}</div> : c.cnic ? <div className="text-muted fs-11">CNIC {c.cnic}</div> : null}</> },
    { key: "phone", header: "Contact", render: (c) => (
      <>{c.phone ? <a href={`tel:${c.phone}`} onClick={(e) => e.stopPropagation()}>{c.phone}</a> : <span className="text-muted">—</span>}
        {(c.alt_mobile || c.landline) && <div className="text-muted fs-11">{[c.alt_mobile, c.landline].filter(Boolean).join(" · ")}</div>}
        {c.contact_person && <div className="text-muted fs-11">{c.contact_person}</div>}</>
    ) },
    { key: "city", header: "City", render: (c) => c.city || "—" },
    { key: "rep_names", header: "Sale person", render: (c) => c.rep_names?.join(", ") || <span className="text-muted">—</span> },
    { key: "outstanding", header: "Outstanding", num: true, render: (c) => (
      <><span className={Number(c.outstanding) > Number(c.credit_limit) && Number(c.credit_limit) > 0 ? "text-danger fw-semibold" : Number(c.outstanding) > 0 ? "fw-semibold" : "text-muted"}>{money(c.outstanding, 0)}</span>
        {Number(c.credit_limit) > 0 && <div className="text-muted fs-11">limit {money(c.credit_limit, 0)}</div>}</>
    ) },
    { key: "status", header: "Status", render: (c) => <StatusBadge status={c.is_blocked ? "blocked" : c.status} /> },
  ];
  return (
    <ListPage title="Customers" endpoint="/api/v1/customers" columns={cols} rowLink={(c) => `/app/customers/${c.id}`} page="customers" createTo="/app/customers/new" createLabel="Add customer" defaultOrdering="name" searchPlaceholder="Search name, code, phone, address…"
      filters={[{ name: "assigned_reps", label: "Sale person", type: "entity", endpoint: "/api/v1/supplier-reps", mapOption: (r: any) => ({ id: r.id, label: r.name, sub: r.supplier_name }) }, { name: "type", label: "Type", type: "select", options: TYPES }, { name: "status", label: "Status", type: "select", options: ["active", "pending", "blocked", "inactive"].map((s) => ({ value: s, label: s })) }]}
      actions={<button className="btn btn-outline-success" onClick={() => api.download("/api/v1/exports/customers", "customers.csv")}><i className="ti ti-file-type-csv me-1" />Export</button>}
      stats={stats.data && <div className="row g-3 mb-3"><div className="col-6 col-md-3"><StatCard label="Total" value={stats.data.total} icon="ti-users" color="primary" /></div><div className="col-6 col-md-3"><StatCard label="Active" value={stats.data.active} icon="ti-user-check" color="success" /></div><div className="col-6 col-md-3"><StatCard label="Pending approval" value={stats.data.pending} icon="ti-user-question" color="warning" /></div><div className="col-6 col-md-3"><StatCard label="Blocked" value={stats.data.blocked} icon="ti-user-off" color="danger" /></div></div>} />
  );
}

/** Checkbox list of the manufacturer/sales reps that may visit this customer (one or many). */
function RepPicker({ value, onChange }: { value: string[]; onChange: (ids: string[]) => void }) {
  const reps = useOptions("/api/v1/supplier-reps", { is_active: true });
  const [q, setQ] = useState("");
  const rows = (reps.data || []).filter((r: any) => !q || `${r.name} ${r.code} ${r.supplier_name || ""} ${r.territory || ""}`.toLowerCase().includes(q.toLowerCase()));
  const toggle = (id: string, on: boolean) => onChange(on ? [...value, id] : value.filter((x) => x !== id));
  return (
    <div className="border rounded p-2">
      {(reps.data || []).length > 6 && <input className="form-control form-control-sm mb-2" placeholder="Search reps…" value={q} onChange={(e) => setQ(e.target.value)} />}
      <div className="d-flex flex-wrap gap-3" style={{ maxHeight: 160, overflowY: "auto" }}>
        {rows.map((r: any) => (
          <div className="form-check" key={r.id}>
            <input className="form-check-input" type="checkbox" id={`rep-${r.id}`} checked={value.includes(r.id)} onChange={(e) => toggle(r.id, e.target.checked)} />
            <label className="form-check-label fs-13" htmlFor={`rep-${r.id}`}>{r.name} <span className="text-muted fs-11">{r.code}{r.supplier_name ? ` · ${r.supplier_name}` : ""}{r.territory ? ` · ${r.territory}` : ""}</span></label>
          </div>
        ))}
        {!rows.length && <span className="text-muted fs-13">{reps.isLoading ? "Loading reps…" : "No reps yet — add them under Suppliers → reps."}</span>}
      </div>
      {value.length > 0 && <div className="fs-11 text-muted mt-1">{value.length} rep(s) attached — they see this customer in the rep app and can order for it.</div>}
    </div>
  );
}

export function customerFields(can: (p: string) => boolean): FieldDef[] {
  const sec = (section: string, sectionIcon: string, sectionHint?: string, sectionCollapsed = false) => ({ section, sectionIcon, sectionHint, sectionCollapsed });
  return [
    // ── the owner's field order (24 Oct 2026): identity, contacts, place, notes, status — all on top ──
    { ...sec("The shop", "ti-building-store"), name: "name", label: "Trading name", required: true, col: "col-md-4", placeholder: "Al-Madina Store", autoComplete: "off", icon: "ti-building-store" },
    { ...sec("The shop", "ti-building-store"), name: "business_name", label: "Registered business name", col: "col-md-4", placeholder: "same as trading name if left blank" },
    { ...sec("The shop", "ti-building-store"), name: "code", label: "Customer code", col: "col-md-2", hint: "auto if left blank", maxLength: 30 },
    { ...sec("The shop", "ti-building-store"), name: "type", label: "Type", type: "select", options: TYPES, default: "retailer", col: "col-md-2", required: true },
    { ...sec("The shop", "ti-building-store"), name: "contact_person", label: "Contact person", col: "col-md-3", icon: "ti-user", autoComplete: "name" },
    { ...sec("The shop", "ti-building-store"), name: "phone", label: "Mobile", col: "col-md-3", mask: "phone", prefix: "🇵🇰", placeholder: "+92 300 1234567", autoComplete: "tel" },
    { ...sec("The shop", "ti-building-store"), name: "alt_mobile", label: "Alt mobile", col: "col-md-2", mask: "phone", prefix: "🇵🇰" },
    { ...sec("The shop", "ti-building-store"), name: "landline", label: "Landline", col: "col-md-2", mask: "phone", prefix: "🇵🇰" },
    { ...sec("The shop", "ti-building-store"), name: "email", label: "Email", type: "email", col: "col-md-2", autoComplete: "email" },

    { ...sec("The shop", "ti-building-store"), name: "address", label: "Shop address", col: "col-md-6" },
    { ...sec("The shop", "ti-building-store"), name: "city", label: "City", type: "custom", col: "col-md-3", render: (v, set) => <CitySelect value={v || ""} onChange={set} /> },
    { ...sec("The shop", "ti-building-store"), name: "province", label: "Province", type: "select", col: "col-md-3", default: "Punjab", options: ["Punjab", "Sindh", "Khyber Pakhtunkhwa", "Balochistan", "Gilgit-Baltistan", "Azad Jammu & Kashmir", "Islamabad Capital Territory"].map((p) => ({ value: p, label: p })) },
    { ...sec("The shop", "ti-building-store"), name: "min_shelf_life_days", label: "Min shelf life", type: "number", col: "col-md-2", suffix: "days", hint: "refuse stock expiring sooner" },
    { ...sec("The shop", "ti-building-store"), name: "notes", label: "Notes", type: "textarea", col: "col-md-8" },
    { ...sec("The shop", "ti-building-store"), name: "status", label: "Status", type: "select", options: [{ value: "active", label: "Active" }, { value: "pending", label: "Pending approval" }, { value: "inactive", label: "Inactive" }], default: "active", col: "col-md-2" },
    { ...sec("The shop", "ti-building-store"), name: "order_radius_m", label: "", type: "custom", col: "d-none", default: 50, render: () => null },
    { ...sec("The shop", "ti-building-store"), name: "assigned_reps", label: "Sale person(s) *", type: "custom", col: "col-md-12", default: [],
      validate: (v) => (Array.isArray(v) && v.length ? null : "Link at least one sale person — a shop without one reaches no rep."),
      render: (v, set) => <RepPicker value={Array.isArray(v) ? v : []} onChange={set} /> },
    { ...sec("The shop", "ti-building-store"), name: "gps_lat", label: "Shop pin", type: "custom", col: "col-md-12",
      render: (v, _set, values, setField) => <StorePinPicker compact lat={v} lng={values.gps_lng} name={values.name}
        onChange={(lat, lng) => { setField("gps_lat", lat); setField("gps_lng", lng); }}
        radius={values.order_radius_m} onRadiusChange={(m) => setField("order_radius_m", m)} /> },
    { ...sec("The shop", "ti-building-store"), name: "gps_lng", label: "", type: "custom", col: "d-none", render: () => null },

    { ...sec("Tax & FBR", "ti-receipt", "NTN or CNIC — one of the two is required. The FBR row matters only for customers who get tax invoices."), name: "ntn", label: "NTN", col: "col-md-3", mask: "ntn", placeholder: "1234567-8", maxLength: 9, alwaysValidate: true,
      validate: (_v, vals) => (String(vals.ntn || "").trim() || String(vals.cnic || "").trim() ? null : "Give NTN or CNIC — one of the two") },
    { ...sec("Tax & FBR", "ti-receipt"), name: "cnic", label: "CNIC", col: "col-md-3", mask: "cnic", placeholder: "35202-1234567-1", maxLength: 15, alwaysValidate: true,
      validate: (_v, vals) => (String(vals.ntn || "").trim() || String(vals.cnic || "").trim() ? null : "Give NTN or CNIC — one of the two") },
    { ...sec("Tax & FBR", "ti-receipt"), name: "strn", label: "STRN (sales tax)", col: "col-md-3", mask: "strn", placeholder: "03-01-2345-678-90", maxLength: 17, hint: "your records only — not sent to FBR" },
    { ...sec("Tax & FBR", "ti-receipt"), name: "filer_status", label: "ATL filer status", type: "select", options: [{ value: "unknown", label: "Not known" }, { value: "filer", label: "Filer" }, { value: "non_filer", label: "Non-filer (higher withholding)" }], default: "unknown", col: "col-md-3", hint: "filer and registered are different things" },
    { ...sec("Tax & FBR", "ti-receipt"), name: "fbr_registration_type", label: "FBR registration", type: "custom", col: "col-md-4",
      hint: "drives the FBR tax invoice — check it with FBR rather than guessing",
      render: (v, set, values) => <FBRRegTypeField value={v || ""} onChange={set} ntn={values.ntn} cnic={values.cnic} customerId={values.id} /> },
    { ...sec("Tax & FBR", "ti-receipt"), name: "tax_class_override", label: "Tax class override", type: "entity", endpoint: "/api/v1/settings/tax-classes", mapOption: (t) => ({ id: t.id, label: t.name }), col: "col-md-2", hint: "e.g. unregistered → further tax" },
    { ...sec("Tax & FBR", "ti-receipt"), name: "default_discount_pct", label: "Discount %", type: "number", col: "col-md-2", default: 0, suffix: "%", validate: (v) => (Number(v) > 100 ? "A discount cannot be more than 100%" : Number(v) < 0 ? "Cannot be negative" : null) },
    { ...sec("Tax & FBR", "ti-receipt"), name: "credit_limit", label: "Credit limit", type: "money", col: "col-md-2", default: 0, prefix: "PKR", readOnly: !can("customers.set_credit_limit") },
    { ...sec("Tax & FBR", "ti-receipt"), name: "credit_days", label: "Credit days", type: "number", col: "col-md-2", default: 0, suffix: "days", readOnly: !can("customers.set_credit_limit"), hint: "0 days = a cash customer" },

    // ── everything occasional folds away until asked for ─────────────────────────────────────────────
    { ...sec("More shops (multi-shop account)", "ti-building-community", "Orders can be taken at any of these; each delivery goes to the shop the order was taken at.", true), name: "locations_write", label: "", type: "custom", col: "col-md-12", default: [],
      render: (v: any, set: any) => <LocationsEditor value={Array.isArray(v) ? v : []} onChange={set} /> },


    { ...sec("Opening balance", "ti-scale", "What they already owed when you started using DMS. Saving posts it to the ledger, so it shows in their statement and ageing.", true), name: "opening_balance", label: "Amount they owe", type: "money", col: "col-md-3", default: 0, prefix: "PKR" },
    { ...sec("Opening balance", "ti-scale", undefined, true), name: "opening_balance_date", label: "As at", type: "date", col: "col-md-3", hint: "the date this balance is from" },

  ];
}

export function CustomerFormPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const { can } = usePerms();
  const q = useDoc("/api/v1/customers", id);
  if (id && q.isLoading) return <Spinner />;
  return (
    <>
      <PageHeader title={id ? `Edit ${q.data?.name}` : "New customer"} crumbs={[{ label: "Customers", to: "/app/customers" }, { label: id ? "Edit" : "New" }]} />
      <div className="card"><div className="card-body">
        <ResourceForm dense fields={customerFields(can)} initial={q.data ? { ...q.data, locations_write: (q.data.locations || []).map((l: any) => ({ name: l.name, address: l.address, gps_lat: l.gps_lat, gps_lng: l.gps_lng, order_radius_m: l.order_radius_m ?? "" })) } : q.data} onCancel={() => nav(-1)}
          onSubmit={async (v) => { const r = id ? await api.patch(`/api/v1/customers/${id}`, v) : await api.post("/api/v1/customers", v); toast.success("Saved"); nav(`/app/customers/${r.id}`); }} />
      </div></div>
    </>
  );
}

export function CustomerDetailPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { can } = usePerms();
  const page = usePage("customers");
  const q = useDoc("/api/v1/customers", id);
  const [tab, setTab] = useState(page.can("ledger") ? "ledger" : "orders");
  const [dlg, setDlg] = useState<null | "block" | "unblock" | "delete">(null);
  const ledger = useQuery({ queryKey: ["customer-ledger", id], queryFn: () => api.get(`/api/v1/customers/${id}/ledger`), enabled: tab === "ledger" && page.can("ledger") });
  const statement = useQuery({ queryKey: ["customer-statement", id], queryFn: () => api.get(`/api/v1/customers/${id}/statement`), enabled: tab === "statement" });
  const orders = useQuery({ queryKey: ["customer-orders", id], queryFn: () => api.get(`/api/v1/customers/${id}/orders`), enabled: tab === "orders" });
  const invoices = useQuery({ queryKey: ["customer-invoices", id], queryFn: () => api.list("/api/v1/invoices", { customer: id, page_size: 100 }), enabled: tab === "invoices" });
  if (q.isLoading) return <Spinner />;
  const c = q.data;
  if (!c) return null;
  const act = async (a: string, body: any = {}) => { try { await api.post(`/api/v1/customers/${id}/${a}`, body); toast.success("Done"); qc.invalidateQueries({ queryKey: ["doc", "/api/v1/customers", id] }); } catch (e: any) { toast.error(e.message); } setDlg(null); };
  return (
    <>
      <PageHeader title={c.name} badge={<StatusBadge status={c.is_blocked ? "blocked" : c.status} />} crumbs={[{ label: "Customers", to: "/app/customers" }, { label: c.name }]}
        actions={<>
          {pageRights("sales_orders").add && <Link to={`/app/sales/orders/new?customer=${c.id}`} className="btn btn-primary"><i className="ti ti-shopping-cart-plus me-1" />New order</Link>}
          {pageRights("payments").extras.receive && Number(c.outstanding) > 0 && <Link to={`/app/accounting/payments/new?direction=in&partner_type=customer&partner_id=${c.id}`} className="btn btn-success"><i className="ti ti-cash me-1" />Receive payment</Link>}
          {c.status === "pending" && page.edit && <button className="btn btn-outline-success" onClick={() => act("approve")}>Approve</button>}
          {page.edit && <Link to={`/app/customers/${c.id}/edit`} className="btn btn-outline-primary"><i className="ti ti-edit me-1" />Edit</Link>}
          {page.can("block") && (c.is_blocked ? <button className="btn btn-outline-secondary" onClick={() => setDlg("unblock")}>Unblock</button> : <button className="btn btn-outline-danger" onClick={() => setDlg("block")}>Block</button>)}
          {page.delete && c.status !== "inactive" && <button className="btn btn-outline-danger" onClick={() => setDlg("delete")}><i className="ti ti-trash me-1" />Delete</button>}
          {page.can("ledger") && <button className="btn btn-outline-secondary" onClick={() => api.download(`/api/v1/customers/${c.id}/statement?format=pdf`)}><i className="ti ti-printer me-1" />Statement PDF</button>}
        </>} />
      {c.is_blocked && <Alert kind="danger">Blocked: {c.blocked_reason || "no reason given"}</Alert>}
      <div className="row g-3 mb-3">
        <div className="col-6 col-md-4"><StatCard label="Outstanding" value={money(c.outstanding, 0)} icon="ti-receipt" color={Number(c.outstanding) > 0 ? "warning" : "success"} /></div>
        <div className="col-6 col-md-4"><StatCard label="Credit limit" value={money(c.credit_limit, 0)} icon="ti-credit-card" color="primary" sub={`${c.credit_days} credit days`} /></div>
        <div className="col-6 col-md-4"><StatCard label="Sale Person" value={(c.rep_names || []).join(", ") || "—"} icon="ti-user-star" color="secondary" /></div>
      </div>
      <div className="card mb-3"><div className="card-body"><div className="row g-3">
        <InfoItem label="Code">{c.code}</InfoItem><InfoItem label="Type">{titleCase(c.type)}</InfoItem><InfoItem label="Contact">{c.contact_person || "—"}<div className="fs-12 text-muted">{c.phone} {c.phone2} {c.email}</div></InfoItem><InfoItem label="Address">{c.address || "—"}<div className="fs-12 text-muted">{c.city}</div></InfoItem>
        <InfoItem label="Reps">{c.rep_names?.join(", ") || "—"}</InfoItem><InfoItem label="GPS">{c.gps_lat ? <><a href={`https://maps.google.com/?q=${c.gps_lat},${c.gps_lng}`} target="_blank" rel="noreferrer">{c.gps_lat}, {c.gps_lng}</a><div className="fs-12 text-muted">{pinStory(c)}</div></> : <span className="text-warning">No location yet — the rep can set it at the first visit</span>}</InfoItem><InfoItem label="Tax numbers">{Object.entries(c.tax_numbers || {}).map(([k, v]) => `${k}: ${v}`).join(" · ") || "—"}{c.tax_class_override_name && <div className="fs-12 text-muted">tax class {c.tax_class_override_name}</div>}</InfoItem><InfoItem label="FBR">{(c.fbr_problems || []).length === 0 ? <span className="text-success"><i className="ti ti-check me-1" />Ready for tax invoices{c.fbr_registration_type ? ` · ${c.fbr_registration_type}` : ""}{c.fbr_reg_checked_at ? <div className="fs-12 text-muted">checked {date(c.fbr_reg_checked_at)}</div> : null}</span> : <span className="text-warning" title={(c.fbr_problems || []).join(" ")}>{c.fbr_problems.length} issue(s) before a tax invoice can file<div className="fs-12 text-muted">{c.fbr_problems[0]}</div></span>}</InfoItem><InfoItem label="Notes">{c.notes || "—"}</InfoItem>
      </div></div></div>
      <div className="card"><div className="card-body">
        <Tabs tabs={[...(page.can("ledger") ? [{ key: "ledger", label: "Ledger" }, { key: "statement", label: "Statement & ageing" }] : []), { key: "orders", label: "Orders" }, { key: "invoices", label: "Invoices" }, { key: "attachments", label: "Documents" }, { key: "history", label: "History" }]} active={tab} onChange={setTab} />
        {tab === "ledger" && (ledger.isLoading ? <Spinner /> : ledger.data ? <LedgerTable data={ledger.data} /> : <p className="text-muted">No access to the ledger.</p>)}
        {tab === "statement" && (statement.isLoading ? <Spinner /> : statement.data && <>
          <div className="row g-2 mb-3">{Object.entries(statement.data.ageing).map(([k, v]) => <div className="col" key={k}><div className="border rounded p-2 text-center"><div className="fs-11 text-muted">{k}</div><div className="fw-semibold">{money(v as any, 0)}</div></div></div>)}</div>
          <h6 className="fs-13">Open invoices</h6><table className="table table-sm fs-12"><thead className="table-light"><tr><th>Invoice</th><th>Date</th><th>Due</th><th className="num">Total</th><th className="num">Paid</th><th className="num">Balance</th></tr></thead><tbody>{statement.data.open_invoices.map((i: any) => <tr key={i.id}><td><Link to={`/app/sales/invoices/${i.id}`}>{i.number}</Link></td><td>{date(i.date)}</td><td>{date(i.due_date)}</td><td className="num">{money(i.total)}</td><td className="num">{money(i.paid_amount)}</td><td className="num fw-semibold">{money(i.balance)}</td></tr>)}</tbody></table>
        </>)}
        {tab === "orders" && (orders.isLoading ? <Spinner /> : <table className="table table-sm fs-12"><thead className="table-light"><tr><th>Order</th><th>Date</th><th>Rep</th><th className="num">Total</th><th>Status</th></tr></thead><tbody>{orders.data?.map((o: any) => <tr key={o.id}><td><Link to={`/app/sales/orders/${o.id}`}>{o.number}</Link></td><td>{date(o.order_date)}</td><td>{o.rep_name}</td><td className="num">{money(o.total)}</td><td><StatusBadge status={o.status} /></td></tr>)}</tbody></table>)}
        {tab === "invoices" && (invoices.isLoading ? <Spinner /> : <table className="table table-sm fs-12"><thead className="table-light"><tr><th>Invoice</th><th>Date</th><th>Due</th><th className="num">Total</th><th className="num">Balance</th><th>Status</th></tr></thead><tbody>{invoices.data?.results.map((i: any) => <tr key={i.id}><td><Link to={`/app/sales/invoices/${i.id}`}>{i.number}</Link></td><td>{date(i.date)}</td><td>{date(i.due_date)}</td><td className="num">{money(i.total)}</td><td className="num">{money(i.balance)}</td><td><StatusBadge status={i.status} /></td></tr>)}</tbody></table>)}
        {tab === "attachments" && <AttachmentsTab model="Customer" id={c.id} />}
        {tab === "history" && <HistoryTab model="Customer" id={c.id} />}
      </div></div>
      <ConfirmDialog open={dlg === "block"} title="Block customer" withReason danger confirmText="Block" onClose={() => setDlg(null)} onConfirm={(r) => act("block", { reason: r })} />
      <ConfirmDialog open={dlg === "unblock"} title="Unblock customer" confirmText="Unblock" onClose={() => setDlg(null)} onConfirm={() => act("unblock")} />
      <ConfirmDialog open={dlg === "delete"} title="Delete customer" message={<>Make <strong>{c.name}</strong> inactive? Their orders, invoices and ledger stay exactly as they are.</>} danger confirmText="Delete" onClose={() => setDlg(null)} onConfirm={async () => { try { await api.delete(`/api/v1/customers/${c.id}`); toast.success("Customer deleted"); qc.invalidateQueries({ queryKey: ["list", "/api/v1/customers"] }); nav("/app/customers"); } catch (e: any) { toast.error(e.message); } setDlg(null); }} />
    </>
  );
}

export function LedgerTable({ data }: { data: any }) {
  return (
    <div className="table-responsive"><table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>Date</th><th>Journal</th><th>Description</th><th>Account</th><th className="num">Debit</th><th className="num">Credit</th><th className="num">Balance</th></tr></thead>
      <tbody><tr className="table-light"><td colSpan={6}>Opening balance</td><td className="num">{money(data.opening_balance)}</td></tr>{data.rows.map((r: any, i: number) => <tr key={i}><td>{date(r.date)}</td><td><Link to={`/app/accounting/journals/${r.journal_id}`}>{r.journal}</Link></td><td>{r.description}</td><td className="text-muted">{r.account}</td><td className="num">{Number(r.debit) ? money(r.debit) : ""}</td><td className="num">{Number(r.credit) ? money(r.credit) : ""}</td><td className="num fw-medium">{money(r.balance)}</td></tr>)}<tr className="fw-bold"><td colSpan={6}>Closing balance</td><td className="num">{money(data.closing_balance)}</td></tr></tbody></table></div>
  );
}

export function TerritoriesPage() {
  return <CrudPage page="territories" title="Territories" crumbs={[{ label: "Customers", to: "/app/customers" }, { label: "Territories" }]} endpoint="/api/v1/territories" columns={[{ key: "name", header: "Name" }, { key: "parent_name", header: "Parent" }, { key: "customers_count", header: "Customers", num: true }]} fields={[{ name: "name", label: "Name", required: true }, { name: "parent", label: "Parent territory", type: "entity", endpoint: "/api/v1/territories", mapOption: (t) => ({ id: t.id, label: t.name }) }]} />;
}

export function RoutesPage() {
  const days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  return <CrudPage page="territories" title="Routes" crumbs={[{ label: "Customers", to: "/app/customers" }, { label: "Routes" }]} endpoint="/api/v1/routes" columns={[{ key: "name", header: "Route" }, { key: "territory_name", header: "Territory" }, { key: "day_of_week", header: "Day", render: (r: any) => (r.day_of_week === null ? "—" : days[r.day_of_week]) }, { key: "customers_count", header: "Customers", num: true }, { key: "is_active", header: "Active", render: (r: any) => (r.is_active ? "Yes" : "No") }]} fields={[{ name: "name", label: "Name", required: true }, { name: "territory", label: "Territory", type: "entity", endpoint: "/api/v1/territories", mapOption: (t) => ({ id: t.id, label: t.name }) }, { name: "day_of_week", label: "Day of week", type: "select", options: days.map((d, i) => ({ value: String(i), label: d })) }, { name: "is_active", label: "Active", type: "checkbox", default: true }]} />;
}


/** "Registered" / "Unregistered" for FBR tax invoices — the Check button asks FBR's Get_Reg_Type for
 *  the customer's NTN/CNIC, because guessing it wrong is a filing rejection. */
function FBRRegTypeField({ value, onChange, ntn, cnic, customerId }: { value: string; onChange: (v: string) => void; ntn?: string; cnic?: string; customerId?: string }) {
  const [busy, setBusy] = useState(false);
  return (
    <div className="d-flex gap-2">
      <select className="form-select" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Not set</option>
        <option value="Registered">Registered</option>
        <option value="Unregistered">Unregistered</option>
      </select>
      <button type="button" className="btn btn-outline-secondary text-nowrap" disabled={busy || !(ntn || cnic)}
        title={ntn || cnic ? "Ask FBR whether this NTN/CNIC is registered" : "Enter the NTN or CNIC first"}
        onClick={async () => {
          setBusy(true);
          try {
            const r = await api.post("/api/v1/fbr/check-registration", { registration_no: ntn || cnic, ...(customerId ? { customer_id: customerId } : {}) });
            if (r.registration_type) { onChange(r.registration_type); toast.success(`FBR says: ${r.registration_type}`); }
            else toast.error("FBR gave no answer for that number.");
          } catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
        }}><i className="ti ti-building-bank me-1" />Check with FBR</button>
    </div>
  );
}
