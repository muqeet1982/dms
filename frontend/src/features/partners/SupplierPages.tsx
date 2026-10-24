import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { date, money } from "@/lib/format";
import { useDoc } from "@/lib/hooks";
import { pageRights, usePage, usePerms } from "@/lib/perms";
import { toast } from "@/lib/toast";
import { InfoItem, PageHeader, Spinner, StatCard, StatusBadge, Tabs } from "@/components/ui/Bits";
import { CrudPage } from "@/components/ui/CrudPage";
import { Column } from "@/components/ui/DataTable";
import { ListPage } from "@/components/ui/ListPage";
import { CitySelect } from "@/components/ui/CitySelect";
import { FieldDef, ResourceForm, ResourceModal } from "@/components/ui/ResourceForm";
import { AttachmentsTab, HistoryTab } from "@/components/documents/DocTabs";
import { RepAreasTab } from "./GeofencePages";
import { LedgerTable } from "./CustomerPages";

const sec = (section: string, sectionIcon: string, sectionHint?: string, sectionCollapsed = false) => ({ section, sectionIcon, sectionHint, sectionCollapsed });

const supplierFields: FieldDef[] = [
  { ...sec("Who they are", "ti-building-factory-2"), name: "name", label: "Trading name", required: true, col: "col-md-5", placeholder: "Keune Pakistan", icon: "ti-building-factory-2" },
  { ...sec("Who they are", "ti-building-factory-2"), name: "code", label: "Supplier code", col: "col-md-2", hint: "auto if left blank" },
  { ...sec("Who they are", "ti-building-factory-2"), name: "payment_terms_days", label: "Payment terms", type: "number", default: 30, col: "col-md-3", suffix: "days", hint: "net days from the bill date" },
  { ...sec("Who they are", "ti-building-factory-2"), name: "is_active", label: "Active supplier", type: "checkbox", col: "col-md-2", default: true },

  { ...sec("How to reach them", "ti-phone"), name: "contact_person", label: "Contact person", col: "col-md-4", icon: "ti-user" },
  { ...sec("How to reach them", "ti-phone"), name: "phone", label: "Mobile", col: "col-md-4", mask: "phone", prefix: "🇵🇰", placeholder: "+92 300 1234567" },
  { ...sec("How to reach them", "ti-phone"), name: "alt_mobile", label: "Alt mobile", col: "col-md-4", mask: "phone", prefix: "🇵🇰" },
  { ...sec("How to reach them", "ti-phone"), name: "landline", label: "Landline", col: "col-md-4", mask: "phone", prefix: "🇵🇰", placeholder: "+92 21 35881234" },
  { ...sec("How to reach them", "ti-phone"), name: "email", label: "Email", type: "email", col: "col-md-4", icon: "ti-mail" },

  { ...sec("Where they are", "ti-map-pin"), name: "address", label: "Address", type: "textarea", col: "col-md-6" },
  { ...sec("Where they are", "ti-map-pin"), name: "city", label: "City", type: "custom", col: "col-md-3", render: (v, set) => <CitySelect value={v || ""} onChange={set} /> },

  { ...sec("Tax & legal", "ti-file-certificate", "Used on purchase orders, bills and withholding statements."), name: "business_name", label: "Registered business name", col: "col-md-5", placeholder: "same as trading name if left blank" },
  { ...sec("Tax & legal", "ti-file-certificate"), name: "filer_status", label: "FBR status", type: "select", options: [{ value: "unknown", label: "Not known" }, { value: "filer", label: "Filer" }, { value: "non_filer", label: "Non-filer (higher withholding)" }], default: "unknown", col: "col-md-3" },
  { ...sec("Tax & legal", "ti-file-certificate"), name: "withholding_tax", label: "Withholding tax on payments", type: "entity", endpoint: "/api/v1/settings/tax-types", mapOption: (t) => ({ id: t.id, label: t.name }), col: "col-md-4" },
  { ...sec("Tax & legal", "ti-file-certificate"), name: "ntn", label: "NTN", col: "col-md-3", mask: "ntn", placeholder: "1234567-8", maxLength: 9 },
  { ...sec("Tax & legal", "ti-file-certificate"), name: "strn", label: "STRN (sales tax)", col: "col-md-3", mask: "strn", placeholder: "03-01-2345-678-90", maxLength: 17 },
  { ...sec("Tax & legal", "ti-file-certificate"), name: "cnic", label: "CNIC", col: "col-md-3", mask: "cnic", placeholder: "35202-1234567-1", maxLength: 15 },
  { ...sec("Tax & legal", "ti-file-certificate"), name: "tax_class_override", label: "Tax class", type: "entity", endpoint: "/api/v1/settings/tax-classes", mapOption: (t) => ({ id: t.id, label: t.name }), col: "col-md-3" },

  { ...sec("Opening balance", "ti-scale", "What you already owed them when you started using DMS. Saving posts it to the ledger (Dr opening equity / Cr payables), so it shows in their ledger and in payables."), name: "opening_balance", label: "Amount we owe", type: "money", col: "col-md-3", default: 0, prefix: "PKR" },
  { ...sec("Opening balance", "ti-scale"), name: "opening_balance_date", label: "As at", type: "date", col: "col-md-3" },

  { ...sec("Notes", "ti-notes", undefined, true), name: "notes", label: "Internal notes", type: "textarea", col: "col-md-12" },
];

const repFields = (supplierId?: string): FieldDef[] => [
  { name: "name", label: "Name", required: true, col: "col-md-6" }, { name: "code", label: "Rep code", col: "col-md-3", hint: "auto if blank" }, { name: "territory", label: "Territory (label)", col: "col-md-3" },
  { name: "phone", label: "Phone", col: "col-md-4" }, { name: "email", label: "Email", type: "email", col: "col-md-4" }, { name: "monthly_target", label: "Monthly target", type: "money", col: "col-md-4", default: 0 },
  { name: "role", label: "External role (what the rep can see)", type: "entity", endpoint: "/api/v1/roles", query: { role_type: "external" }, mapOption: (r) => ({ id: r.id, label: r.name, sub: r.description }), col: "col-md-6", required: true },
  { name: "geofence_policy", label: "Can take orders", type: "select", col: "col-md-4", options: [{ value: "", label: "Company rule (default)" }, { value: "block", label: "At the shop only" }, { value: "warn", label: "Anywhere — flag when away from the shop" }, { value: "off", label: "Anywhere" }], hint: "overrides the company's shop-proximity rule for this rep" },
  { name: "is_active", label: "Active", type: "checkbox", default: true, col: "col-md-2" },
  { name: "create_login", label: "Create mobile/portal login", type: "checkbox", default: true, col: "col-md-4" },
  { name: "password", label: "Password", type: "password", col: "col-md-6", hint: "leave blank to send an invitation code by email/SMS", showIf: (v) => Boolean(v.create_login) },
];

export function SuppliersPage() {
  const { can } = usePerms();
  const cols: Column<any>[] = [
    { key: "name", header: "Supplier", sortKey: "name", render: (s) => (
      <><span className="fw-semibold text-dark">{s.name}</span><div className="text-muted fs-11 font-monospace">{s.code}</div>{s.business_name && s.business_name !== s.name && <div className="text-muted fs-11">{s.business_name}</div>}</>
    ) },
    { key: "ntn", header: "Tax", render: (s) => (s.ntn || s.strn || s.cnic ? <span className="fs-11">{s.ntn ? <>NTN {s.ntn}<br /></> : null}{s.strn ? <>STRN {s.strn}<br /></> : null}{!s.ntn && !s.strn && s.cnic ? `CNIC ${s.cnic}` : null}</span> : <span className="text-muted">—</span>) },
    { key: "phone", header: "Contact", render: (s) => (
      <>{s.phone ? <a href={`tel:${s.phone}`} onClick={(e) => e.stopPropagation()}>{s.phone}</a> : <span className="text-muted">—</span>}
        {(s.alt_mobile || s.landline) && <div className="text-muted fs-11">{[s.alt_mobile, s.landline].filter(Boolean).join(" · ")}</div>}
        {s.contact_person && <div className="text-muted fs-11">{s.contact_person}</div>}</>
    ) },
    { key: "city", header: "City" },
    { key: "payment_terms_days", header: "Terms", render: (s) => `Net ${s.payment_terms_days}` },
    { key: "outstanding", header: "Payable", num: true, render: (s) => <span className={Number(s.outstanding) > 0 ? "fw-semibold" : "text-muted"}>{money(s.outstanding, 0)}</span> },
    { key: "reps_count", header: "Reps", num: true }, { key: "products_count", header: "Products", num: true },
    { key: "is_active", header: "Status", render: (s) => <StatusBadge status={s.is_active ? "active" : "inactive"} /> },
  ];
  return <ListPage title="Suppliers (manufacturers)" endpoint="/api/v1/suppliers" columns={cols} rowLink={(s) => `/app/suppliers/${s.id}`} page="suppliers" createTo="/app/suppliers/new" createLabel="Add supplier" defaultOrdering="name" actions={<button className="btn btn-outline-success" onClick={() => api.download("/api/v1/exports/suppliers", "suppliers.csv")}><i className="ti ti-file-type-csv me-1" />Export</button>} />;
}

export function SupplierFormPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const q = useDoc("/api/v1/suppliers", id);
  if (id && q.isLoading) return <Spinner />;
  return (
    <>
      <PageHeader title={id ? `Edit ${q.data?.name}` : "New supplier"} crumbs={[{ label: "Suppliers", to: "/app/suppliers" }, { label: id ? "Edit" : "New" }]} />
      <div className="card"><div className="card-body"><ResourceForm fields={supplierFields} initial={q.data} onCancel={() => nav(-1)} onSubmit={async (v) => { const r = id ? await api.patch(`/api/v1/suppliers/${id}`, v) : await api.post("/api/v1/suppliers", v); toast.success("Saved"); nav(`/app/suppliers/${r.id}`); }} /></div></div>
    </>
  );
}

export function SupplierDetailPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const { can } = usePerms();
  const page = usePage("suppliers");
  const repsPage = usePage("supplier_reps");
  const q = useDoc("/api/v1/suppliers", id);
  const [tab, setTab] = useState(repsPage.view ? "reps" : page.can("ledger") ? "ledger" : "products");
  const [repModal, setRepModal] = useState<{ open: boolean; row?: any }>({ open: false });
  const reps = useQuery({ queryKey: ["supplier-reps", id], queryFn: () => api.get(`/api/v1/suppliers/${id}/reps`), enabled: Boolean(id) && repsPage.view });
  const ledger = useQuery({ queryKey: ["supplier-ledger", id], queryFn: () => api.get(`/api/v1/suppliers/${id}/ledger`), enabled: tab === "ledger" });
  const bills = useQuery({ queryKey: ["supplier-bills", id], queryFn: () => api.get(`/api/v1/suppliers/${id}/bills`), enabled: tab === "bills" });
  const products = useQuery({ queryKey: ["supplier-products", id], queryFn: () => api.list("/api/v1/products", { supplier: id, page_size: 200 }), enabled: tab === "products" });
  if (q.isLoading) return <Spinner />;
  const s = q.data;
  if (!s) return null;
  return (
    <>
      <PageHeader title={s.name} badge={<StatusBadge status={s.is_active ? "active" : "inactive"} />} crumbs={[{ label: "Suppliers", to: "/app/suppliers" }, { label: s.name }]}
        actions={<>{pageRights("purchase_orders").add && <Link to={`/app/purchasing/orders/new?supplier=${s.id}`} className="btn btn-primary"><i className="ti ti-shopping-bag me-1" />New PO</Link>}{pageRights("payments").extras.pay && <Link to={`/app/accounting/payments/new?direction=out&partner_type=supplier&partner_id=${s.id}`} className="btn btn-outline-success"><i className="ti ti-cash me-1" />Pay supplier</Link>}{can("suppliers.change_supplier") && <Link to={`/app/suppliers/${s.id}/edit`} className="btn btn-outline-primary"><i className="ti ti-edit me-1" />Edit</Link>}</>} />
      <div className="row g-3 mb-3"><div className="col-6 col-md-3"><StatCard label="Outstanding payable" value={money(s.outstanding, 0)} icon="ti-receipt" color="warning" /></div><div className="col-6 col-md-3"><StatCard label="Payment terms" value={`Net ${s.payment_terms_days}`} icon="ti-calendar" color="info" /></div><div className="col-6 col-md-3"><StatCard label="Reps" value={s.reps_count} icon="ti-users" color="primary" /></div><div className="col-6 col-md-3"><StatCard label="Products" value={s.products_count} icon="ti-package" color="success" /></div></div>
      <div className="card mb-3"><div className="card-body"><div className="row g-3"><InfoItem label="Code">{s.code}</InfoItem><InfoItem label="Contact">{s.contact_person}<div className="fs-12 text-muted">{s.phone} {s.email}</div></InfoItem><InfoItem label="Address">{s.address}<div className="fs-12 text-muted">{s.city}</div></InfoItem><InfoItem label="Tax">{s.tax_no || "—"}{s.tax_class_override_name && <div className="fs-12 text-muted">{s.tax_class_override_name}</div>}</InfoItem></div></div></div>
      <div className="card"><div className="card-body">
        <Tabs tabs={[...(repsPage.view ? [{ key: "reps", label: "Sales reps", count: reps.data?.length }] : []), ...(page.can("ledger") ? [{ key: "ledger", label: "Ledger" }, { key: "bills", label: "Bills" }] : []), { key: "products", label: "Products" }, { key: "attachments", label: "Documents" }, { key: "history", label: "History" }]} active={tab} onChange={setTab} />
        {tab === "reps" && <>
          {repsPage.add && <div className="text-end mb-2"><button className="btn btn-sm btn-primary" onClick={() => setRepModal({ open: true })}><i className="ti ti-plus me-1" />Add rep</button></div>}
          <table className="table table-sm fs-12"><thead className="table-light"><tr><th>Rep</th><th>Code</th><th>Contact</th><th>Territory</th><th>Role</th><th>Login</th><th className="num">Target</th><th>Status</th><th /></tr></thead>
            <tbody>{reps.data?.map((r: any) => <tr key={r.id}><td className="fw-medium"><Link to={`/app/suppliers/reps/${r.id}`}>{r.name}</Link></td><td>{r.code}</td><td>{r.phone}<div className="text-muted fs-11">{r.email}</div></td><td>{r.territory}</td><td>{r.role_name || <span className="text-warning">no role</span>}</td><td>{r.has_login ? <span className="text-success"><i className="ti ti-device-mobile-check" /> {r.user_email}</span> : <span className="text-muted">none</span>}</td><td className="num">{money(r.monthly_target, 0)}</td><td><StatusBadge status={r.is_active ? "active" : "inactive"} /></td><td className="text-end">{can("suppliers.manage_supplier_reps") && <button className="btn btn-sm btn-outline-secondary" onClick={() => setRepModal({ open: true, row: r })}><i className="ti ti-edit" /></button>}</td></tr>)}</tbody></table>
        </>}
        {tab === "ledger" && (ledger.isLoading ? <Spinner /> : ledger.data && <LedgerTable data={ledger.data} />)}
        {tab === "bills" && (bills.isLoading ? <Spinner /> : <table className="table table-sm fs-12"><thead className="table-light"><tr><th>Bill</th><th>Date</th><th>Due</th><th>Supplier inv</th><th className="num">Total</th><th className="num">Balance</th><th>Status</th></tr></thead><tbody>{bills.data?.map((b: any) => <tr key={b.id}><td><Link to={`/app/purchasing/bills/${b.id}`}>{b.number}</Link></td><td>{date(b.bill_date)}</td><td>{date(b.due_date)}</td><td>{b.supplier_invoice_no}</td><td className="num">{money(b.total)}</td><td className="num">{money(b.balance)}</td><td><StatusBadge status={b.status} /></td></tr>)}</tbody></table>)}
        {tab === "products" && (products.isLoading ? <Spinner /> : <table className="table table-sm fs-12"><thead className="table-light"><tr><th>SKU</th><th>Product</th><th>Brand</th><th className="num">Cost</th><th className="num">Sale</th><th>Status</th></tr></thead><tbody>{products.data?.results.map((p: any) => <tr key={p.id}><td>{p.sku}</td><td><Link to={`/app/catalog/products/${p.id}`}>{p.name}</Link></td><td>{p.brand_name}</td><td className="num">{money(p.cost_price)}</td><td className="num">{money(p.sale_price)}</td><td><StatusBadge status={p.status} /></td></tr>)}</tbody></table>)}
        {tab === "attachments" && <AttachmentsTab model="Supplier" id={s.id} />}
        {tab === "history" && <HistoryTab model="Supplier" id={s.id} />}
      </div></div>
      <ResourceModal open={repModal.open} onClose={() => setRepModal({ open: false })} title={repModal.row ? "Edit rep" : "New sales rep"} endpoint={repModal.row ? "/api/v1/supplier-reps" : `/api/v1/suppliers/${id}/reps`} id={repModal.row?.id} initial={repModal.row ? { ...repModal.row, create_login: false } : undefined} fields={repFields(id)} onSaved={() => { qc.invalidateQueries({ queryKey: ["supplier-reps", id] }); qc.invalidateQueries({ queryKey: ["doc", "/api/v1/suppliers", id] }); }} />
    </>
  );
}

export function SupplierRepsPage() {
  const cols: Column<any>[] = [
    { key: "name", header: "Rep", render: (r) => <><span className="fw-semibold text-dark">{r.name}</span><div className="text-muted fs-11">{r.code}</div></> }, { key: "supplier_name", header: "Supplier" }, { key: "territory", header: "Territory" }, { key: "phone", header: "Contact", render: (r) => <>{r.phone}<div className="text-muted fs-11">{r.email}</div></> },
    { key: "role_name", header: "External role" }, { key: "has_login", header: "Login", render: (r) => (r.has_login ? <span className="text-success">yes</span> : <span className="text-muted">no</span>) }, { key: "monthly_target", header: "Target", num: true, render: (r) => money(r.monthly_target, 0) }, { key: "is_active", header: "Status", render: (r) => <StatusBadge status={r.is_active ? "active" : "inactive"} /> },
  ];
  return <ListPage title="Supplier reps" page="supplier_reps" crumbs={[{ label: "Suppliers", to: "/app/suppliers" }, { label: "Reps" }]} endpoint="/api/v1/supplier-reps" columns={cols} rowLink={(r) => `/app/suppliers/reps/${r.id}`} filters={[{ name: "supplier", label: "Supplier", type: "entity", endpoint: "/api/v1/suppliers", mapOption: (s) => ({ id: s.id, label: s.name }) }, { name: "is_active", label: "Active", type: "select", options: [{ value: "true", label: "Active" }, { value: "false", label: "Inactive" }] }]} />;
}

export function SupplierRepDetailPage() {
  const { id } = useParams();
  const q = useDoc("/api/v1/supplier-reps", id);
  const perf = useQuery({ queryKey: ["rep-perf", id], queryFn: () => api.get(`/api/v1/supplier-reps/${id}/performance`), enabled: Boolean(id) });
  const ledger = useQuery({ queryKey: ["rep-ledger", id], queryFn: () => api.get(`/api/v1/supplier-reps/${id}/ledger`), enabled: Boolean(id) });
  const orders = useQuery({ queryKey: ["rep-orders", id], queryFn: () => api.list("/api/v1/sales-orders", { rep: id, page_size: 50 }), enabled: Boolean(id) });
  const customers = useQuery({ queryKey: ["rep-customers", id], queryFn: () => api.list("/api/v1/customers", { assigned_reps: id, page_size: 200 }), enabled: Boolean(id) });
  const [tab, setTab] = useState("orders");
  const [editing, setEditing] = useState(false);
  const qc = useQueryClient();
  if (q.isLoading) return <Spinner />;
  const r = q.data;
  if (!r) return null;
  const p = perf.data;
  return (
    <>
      <PageHeader title={r.name} badge={<StatusBadge status={r.is_active ? "active" : "inactive"} />} crumbs={[{ label: "Suppliers", to: "/app/suppliers" }, { label: r.supplier_name, to: `/app/suppliers/${r.supplier}` }, { label: r.name }]}
        actions={pageRights("supplier_reps").edit && <button className="btn btn-outline-primary" onClick={() => setEditing(true)}><i className="ti ti-edit me-1" />Edit</button>} />
      <ResourceModal open={editing} onClose={() => setEditing(false)} title="Edit rep" endpoint="/api/v1/supplier-reps" id={r.id} initial={{ ...r, create_login: false }} fields={repFields(r.supplier)} onSaved={() => qc.invalidateQueries({ queryKey: ["doc", "/api/v1/supplier-reps", id] })} />
      {p && <div className="row g-3 mb-3"><div className="col-6 col-md-3"><StatCard label="Orders" value={p.orders} icon="ti-clipboard-list" color="primary" sub={`${p.rejected} rejected`} /></div><div className="col-6 col-md-3"><StatCard label="Order value" value={money(p.order_value, 0)} icon="ti-cash" color="success" sub={`delivered ${money(p.delivered_value, 0)}`} /></div><div className="col-6 col-md-3"><StatCard label="Cash held" value={money(p.cash_held, 0)} icon="ti-wallet" color={Number(p.cash_held) > 0 ? "warning" : "secondary"} /></div><div className="col-6 col-md-3"><StatCard label="Monthly target" value={money(p.target, 0)} icon="ti-target" color="info" /></div></div>}
      <div className="card mb-3"><div className="card-body"><div className="row g-3"><InfoItem label="Code">{r.code}</InfoItem><InfoItem label="Contact">{r.phone}<div className="fs-12 text-muted">{r.email}</div></InfoItem><InfoItem label="Territory">{r.territory || "—"}</InfoItem><InfoItem label="External role">{r.role_name || "—"}<div className="fs-12 text-muted">{r.has_login ? `login: ${r.user_email}` : "no login"}</div></InfoItem><InfoItem label="Can take orders">{({ off: "Anywhere", warn: "Anywhere — flag when away from the shop", block: "At the shop only" } as any)[r.geofence_policy] || "Company rule"}</InfoItem></div></div></div>
      <div className="card"><div className="card-body">
        <Tabs tabs={[{ key: "orders", label: "Orders" }, { key: "customers", label: "Customers", count: customers.data?.count }, { key: "areas", label: "Working areas" }, { key: "ledger", label: "Cash ledger" }]} active={tab} onChange={setTab} />
        {tab === "orders" && <table className="table table-sm fs-12"><thead className="table-light"><tr><th>Order</th><th>Date</th><th>Customer</th><th className="num">Total</th><th>Status</th></tr></thead><tbody>{orders.data?.results.map((o: any) => <tr key={o.id}><td><Link to={`/app/sales/orders/${o.id}`}>{o.number}</Link></td><td>{date(o.order_date)}</td><td>{o.customer_name}</td><td className="num">{money(o.total)}</td><td><StatusBadge status={o.status} /></td></tr>)}</tbody></table>}
        {tab === "customers" && <table className="table table-sm fs-12"><thead className="table-light"><tr><th>Customer</th><th>Code</th><th>Route</th><th className="num">Outstanding</th></tr></thead><tbody>{customers.data?.results.map((c: any) => <tr key={c.id}><td><Link to={`/app/customers/${c.id}`}>{c.name}</Link></td><td>{c.code}</td><td>{c.route_name}</td><td className="num">{money(c.outstanding, 0)}</td></tr>)}</tbody></table>}
        {tab === "areas" && <RepAreasTab repId={r.id} />}
        {tab === "ledger" && (ledger.data ? <LedgerTable data={ledger.data} /> : <Spinner />)}
      </div></div>
    </>
  );
}
