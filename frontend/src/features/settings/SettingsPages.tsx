import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useAuth } from "@/lib/auth";
import { date, datetime, money, titleCase } from "@/lib/format";
import { useOptions } from "@/lib/hooks";
import { usePerms } from "@/lib/perms";
import { toast } from "@/lib/toast";
import { Alert, Field, InfoItem, PageHeader, Spinner, StatCard, StatusBadge, Tabs } from "@/components/ui/Bits";
import { CrudPage } from "@/components/ui/CrudPage";
import { Column, DataTable } from "@/components/ui/DataTable";
import { EntitySelect } from "@/components/ui/EntitySelect";
import { Modal } from "@/components/ui/Modal";
import { ImportPanel, importLabel } from "@/components/documents/ImportPanel";
import { FieldDef, ResourceForm, ResourceModal } from "@/components/ui/ResourceForm";

const acctOption = (a: any) => ({ id: a.id, label: `${a.code} ${a.name}` });

export function CompanySettingsPage() {
  const qc = useQueryClient();
  const me = useAuth((s) => s.me);
  const [tab, setTab] = useState("profile");
  const t = me?.tenant;
  if (!t) return <Spinner />;
  const company = useQuery({ queryKey: ["company"], queryFn: () => api.get("/api/v1/settings/company") });
  const logoRef = useRef<HTMLInputElement | null>(null);
  const save = async (patch: any) => { await api.patch("/api/v1/settings/company", patch); toast.success("Saved"); qc.invalidateQueries({ queryKey: ["me"] }); qc.invalidateQueries({ queryKey: ["company"] }); };
  const saveSettings = async (patch: any) => save({ settings: patch });
  const uploadLogo = async (file: File) => {
    const fd = new FormData(); fd.append("file", file);
    try { await api.upload("/api/v1/settings/company/logo", fd); toast.success("Logo updated"); qc.invalidateQueries({ queryKey: ["me"] }); qc.invalidateQueries({ queryKey: ["company"] }); }
    catch (e: any) { toast.error(e.message); }
  };
  const settingFields: FieldDef[] = [
    { name: "credit_policy", label: "Credit policy when a customer exceeds the limit", type: "select", options: [{ value: "warn", label: "Warn — put order on credit hold for approval" }, { value: "hard", label: "Hard block — refuse confirmation" }], col: "col-md-6" },
    { name: "reserve_stock_on_confirm", label: "Reserve stock when an order is confirmed", type: "checkbox", col: "col-md-6" },
    { name: "two_step_dispatch", label: "Two-step dispatch (move stock to the van's warehouse at trip start, write off at close)", type: "checkbox", col: "col-md-12" },
    { name: "accept_external_prices", label: "Prices sent by reps / API", type: "select", options: [{ value: "flag", label: "Use price list, flag differences for review" }, { value: "price_list", label: "Always use the price list silently" }, { value: "accept", label: "Accept the caller's price" }], col: "col-md-6" },
    { name: "customer_requests_need_approval", label: "Customers added by reps need approval", type: "checkbox", col: "col-md-6" },
    { name: "allow_negative_stock", label: "Allow negative stock (all warehouses)", type: "checkbox", col: "col-md-4" }, { name: "transfer_requires_approval", label: "Stock transfers need approval", type: "checkbox", col: "col-md-4" }, { name: "block_stock_moves_during_count", label: "Block stock moves during a count", type: "checkbox", col: "col-md-4" },
    { name: "backorder_auto_cancel_days", label: "Auto-cancel unfulfilled orders after (days)", type: "number", col: "col-md-3" }, { name: "min_shelf_life_days", label: "Minimum remaining shelf life to deliver (days)", type: "number", col: "col-md-3" }, { name: "count_tolerance_pct", label: "Count variance tolerance %", type: "number", col: "col-md-3" }, { name: "rep_unsettled_alert_days", label: "Alert when rep cash unsettled for (days)", type: "number", col: "col-md-3" },
    { name: "geofence_policy", label: "When a rep works outside their working area", type: "select", col: "col-md-6", options: [
      { value: "block", label: "Refuse it — orders, cash and new shops only inside the area" },
      { value: "warn", label: "Allow it, but record it on the out-of-area report" },
      { value: "off", label: "Do not check at all" }] },
    { name: "geofence_require_location", label: "Refuse when the app sends no location at all", type: "checkbox", col: "col-md-6", hint: "turn on once every rep's app sends GPS, otherwise an old app is locked out" },
    { name: "rep_order_photo", label: "Photo of the shop with a rep's order", type: "select", col: "col-md-6", options: [
      { value: "optional", label: "Optional — taken when the rep has one" },
      { value: "required", label: "Required — no photo, no order" },
      { value: "off", label: "Do not ask for one" }] },
    { name: "tracking_enabled", label: "Record where reps and drivers are during the day", type: "checkbox", col: "col-md-12", hint: "Off by default. This is employee location data — tell your staff before you turn it on; the app shows them the notice below and lets them see their own history." },
    { name: "tracking_mode", label: "When to record", type: "select", col: "col-md-3", options: [{ value: "work_hours", label: "Working hours only" }, { value: "always", label: "All day" }, { value: "off", label: "Not at all" }] },
    { name: "tracking_from", label: "From", col: "col-md-2", placeholder: "09:00" },
    { name: "tracking_to", label: "To", col: "col-md-2", placeholder: "19:00" },
    { name: "tracking_interval_s", label: "A position every (seconds)", type: "number", col: "col-md-2", hint: "lower drains the battery" },
    { name: "tracking_min_distance_m", label: "…and only after moving (m)", type: "number", col: "col-md-3" },
    { name: "tracking_retention_days", label: "Keep location history for (days)", type: "number", col: "col-md-3", hint: "older points are deleted every night" },
    { name: "tracking_notice", label: "What staff are told before tracking starts", type: "textarea", col: "col-md-9" },
    { name: "invoice_footer", label: "Invoice / document footer text", type: "textarea", col: "col-md-12" },
  ];
  return (
    <>
      <PageHeader title="Company settings" crumbs={[{ label: "Settings" }, { label: "Company" }]} />
      <Tabs tabs={[{ key: "profile", label: "Profile" }, { key: "contact", label: "Contact & documents" }, { key: "rules", label: "Business rules" }]} active={tab} onChange={setTab} />
      {tab === "profile" && (
        <div className="card mb-3"><div className="card-body d-flex align-items-center gap-3">
          <div className="border rounded d-flex align-items-center justify-content-center bg-light" style={{ width: 120, height: 72 }}>
            {company.data?.logo ? <img src={company.data.logo} alt="Company logo" style={{ maxHeight: 64, maxWidth: 112 }} /> : <span className="text-muted fs-12">no logo</span>}
          </div>
          <div>
            <div className="fw-semibold">Company logo</div>
            <div className="fs-12 text-muted mb-2">Printed on invoices, delivery notes and purchase orders. PNG or SVG, max 5 MB.</div>
            <input ref={logoRef} type="file" accept="image/png,image/jpeg,image/svg+xml,image/webp" className="d-none" onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadLogo(f); }} />
            <button className="btn btn-sm btn-outline-primary" onClick={() => logoRef.current?.click()}><i className="ti ti-upload me-1" />Upload logo</button>
          </div>
        </div></div>
      )}
      {tab === "contact" && company.data && (
        <div className="card"><div className="card-body"><ResourceForm
          fields={[{ name: "contact_person", label: "Contact person", col: "col-md-4" }, { name: "support_phone", label: "Support / helpline", col: "col-md-4" }, { name: "whatsapp", label: "WhatsApp", col: "col-md-4" },
            { name: "registration_no", label: "Registration no", col: "col-md-3" }, { name: "license_no", label: "Licence no", col: "col-md-3" }, { name: "facebook", label: "Facebook", col: "col-md-3" }, { name: "instagram", label: "Instagram", col: "col-md-3" },
            { name: "bank_details", label: "Bank details (shown on invoices)", type: "textarea", col: "col-md-6" }, { name: "invoice_footer", label: "Invoice footer", type: "textarea", col: "col-md-6" },
            { name: "po_footer", label: "Purchase order footer", type: "textarea", col: "col-md-6" }, { name: "quote_footer", label: "Quotation footer", type: "textarea", col: "col-md-6" }]}
          initial={company.data.settings} onSubmit={saveSettings} /></div></div>
      )}
      {tab === "profile" && <div className="card"><div className="card-body"><ResourceForm fields={[{ name: "legal_name", label: "Legal name", required: true, col: "col-md-6" }, { name: "display_name", label: "Display name", required: true, col: "col-md-6" }, { name: "address", label: "Address", type: "textarea", col: "col-md-6" }, { name: "city", label: "City", col: "col-md-3" }, { name: "phone", label: "Phone", col: "col-md-3" }, { name: "email", label: "Email", type: "email", col: "col-md-4" }, { name: "website", label: "Website", col: "col-md-4" }, { name: "base_currency", label: "Currency", col: "col-md-2", readOnly: true }, { name: "fiscal_year_start_month", label: "Fiscal year starts (month 1–12)", type: "number", col: "col-md-2" }, { name: "timezone", label: "Timezone", col: "col-md-4" }, { name: "ntn", label: "NTN", col: "col-md-2", placeholder: "1234567-8" }, { name: "strn", label: "STRN (sales tax)", col: "col-md-3", placeholder: "03-01-2345-678-90" }]} initial={{ ...t, ntn: t.tax_numbers?.NTN || "", strn: t.tax_numbers?.STRN || "" }} onSubmit={(v: any) => { const { ntn, strn, ...rest } = v; return save({ ...rest, tax_numbers: { ...(t.tax_numbers || {}), ...(ntn ? { NTN: ntn } : {}), ...(strn ? { STRN: strn } : {}) } }); }} /></div></div>}
      {tab === "rules" && <div className="card"><div className="card-body"><ResourceForm fields={settingFields} initial={{ ...{ credit_policy: "warn", reserve_stock_on_confirm: true, two_step_dispatch: false, accept_external_prices: "flag", customer_requests_need_approval: true, allow_negative_stock: false, transfer_requires_approval: true, block_stock_moves_during_count: true, backorder_auto_cancel_days: 30, min_shelf_life_days: 30, count_tolerance_pct: 2, rep_unsettled_alert_days: 3, geofence_policy: "block", geofence_require_location: false, rep_order_photo: "optional", tracking_enabled: false, tracking_mode: "work_hours", tracking_from: "09:00", tracking_to: "19:00", tracking_interval_s: 120, tracking_min_distance_m: 50, tracking_retention_days: 90, tracking_notice: "", invoice_footer: "" }, ...t.settings }} onSubmit={(v) => save({ settings: { ...t.settings, ...v } })} /></div></div>}
    </>
  );
}

export function BrandingPage() {
  const qc = useQueryClient();
  const me = useAuth((s) => s.me);
  const [draft, setDraft] = useState<any>(null);
  const [requesting, setRequesting] = useState(false);
  const files = useRef<Record<string, HTMLInputElement | null>>({});
  const q = useQuery({ queryKey: ["tenant-branding"], queryFn: () => api.get("/api/v1/settings/branding") });
  if (q.isLoading || !me?.tenant) return <Spinner />;
  const d = q.data;
  const b = draft ?? d.branding;
  const enabled = d.enabled;
  const set = (k: string, v: any) => setDraft({ ...b, [k]: v });
  const save = async () => {
    try { const r = await api.patch("/api/v1/settings/branding", b); setDraft(null); q.refetch(); qc.invalidateQueries({ queryKey: ["me"] }); qc.invalidateQueries({ queryKey: ["branding"] }); toast.success("Branding saved"); return r; }
    catch (e: any) { toast.error(e.message); }
  };
  const upload = async (kind: string, file: File) => {
    const fd = new FormData(); fd.append("kind", kind); fd.append("file", file);
    try { await api.upload("/api/v1/settings/branding/asset", fd); setDraft(null); q.refetch(); qc.invalidateQueries({ queryKey: ["me"] }); toast.success(`${kind} updated`); }
    catch (e: any) { toast.error(e.message); }
  };
  const uploadCompanyLogo = async (file: File) => {
    const fd = new FormData(); fd.append("file", file);
    try { await api.upload("/api/v1/settings/company/logo", fd); q.refetch(); qc.invalidateQueries({ queryKey: ["company"] }); qc.invalidateQueries({ queryKey: ["me"] }); toast.success("Company logo updated"); }
    catch (e: any) { toast.error(e.message); }
  };
  const clearCompanyLogo = async () => {
    const fd = new FormData(); fd.append("clear", "1");
    try { await api.upload("/api/v1/settings/company/logo", fd); q.refetch(); qc.invalidateQueries({ queryKey: ["company"] }); qc.invalidateQueries({ queryKey: ["me"] }); toast.success("Company logo removed"); }
    catch (e: any) { toast.error(e.message); }
  };
  const requestWhiteLabel = async () => {
    setRequesting(true);
    try {
      await api.post("/api/v1/support-tickets", { subject: "Request: white-label branding for our company", priority: "normal", body: `${me?.tenant?.display_name} would like to use their own name, logo and colours inside the app.` });
      toast.success("Request sent to the platform team — you will be notified when it is switched on.");
    } catch (e: any) { toast.error(e.message); } finally { setRequesting(false); }
  };
  const assets = [
    { kind: "logo", label: "Logo", hint: "top bar · ~200×48 PNG or SVG" },
    { kind: "logo_dark", label: "Logo (dark background)", hint: "optional" },
    { kind: "favicon", label: "Favicon", hint: "browser tab · 32×32" },
  ];
  return (
    <>
      <PageHeader title="Branding (white-label)" crumbs={[{ label: "Settings" }, { label: "Branding" }]}
        actions={enabled && <button className="btn btn-primary" disabled={!draft} onClick={save}><i className="ti ti-device-floppy me-1" />Save changes</button>} />
      {!enabled && (
        <div className="alert alert-warning d-flex flex-wrap align-items-center justify-content-between gap-2">
          <div className="fs-13">{d.upsell} Until then the app shows <strong>{d.platform?.site_name}</strong>{d.platform?.logo_url ? " and its logo" : ""} — your own logo below still appears on invoices and delivery notes.</div>
          <button className="btn btn-sm btn-warning" disabled={requesting} onClick={requestWhiteLabel}><i className="ti ti-send me-1" />{requesting ? "Sending…" : "Ask for our own branding"}</button>
        </div>
      )}
      {enabled && <Alert kind="success">White-label is active: your staff and reps see your own name, logo and colours instead of {d.platform?.site_name}.</Alert>}

      <div className="card mb-3"><div className="card-header"><strong>Your company logo</strong><span className="badge bg-success-light text-success ms-2">every plan</span></div><div className="card-body">
        <div className="d-flex align-items-center gap-3 flex-wrap">
          <div className="d-flex align-items-center justify-content-center bg-light rounded border" style={{ width: 120, height: 64 }}>
            {d.company_logo ? <img src={d.company_logo} alt="Company logo" style={{ maxHeight: 56, maxWidth: 112 }} /> : <span className="text-muted fs-12">none</span>}
          </div>
          <div className="flex-grow-1">
            <div className="fs-13">Printed on invoices, delivery notes, purchase orders and emails. This is your own business identity, so it is yours to set whatever the plan says.</div>
            <div className="fs-12 text-muted">PNG, JPG or SVG · up to {d.max_image_mb || 5} MB · a wide logo around 400×120 looks best.</div>
          </div>
          <input ref={(el) => (files.current.company = el)} type="file" accept="image/png,image/jpeg,image/svg+xml,image/webp" className="d-none" onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadCompanyLogo(f); }} />
          <div className="d-flex gap-2">
            <button className="btn btn-outline-primary" onClick={() => files.current.company?.click()}><i className="ti ti-upload me-1" />{d.company_logo ? "Replace" : "Upload logo"}</button>
            {d.company_logo && <button className="btn btn-outline-danger" onClick={clearCompanyLogo}><i className="ti ti-trash" /></button>}
          </div>
        </div>
      </div></div>

      <div className="card mb-3"><div className="card-header"><strong>Name &amp; colours</strong></div><div className="card-body">
        <div className="row">
          <Field label="App name (replaces the product name)" col="col-md-4"><input className="form-control" value={b.app_name || ""} disabled={!enabled} onChange={(e) => set("app_name", e.target.value)} placeholder={me.tenant.display_name} /></Field>
          <Field label="Primary colour" col="col-md-2"><div className="input-group"><input type="color" className="form-control form-control-color" value={b.primary_color || d.platform?.primary_color || "#F26522"} disabled={!enabled} onChange={(e) => set("primary_color", e.target.value)} /><input className="form-control" value={b.primary_color || ""} disabled={!enabled} onChange={(e) => set("primary_color", e.target.value)} placeholder="#F26522" /></div></Field>
          <Field label="Accent colour" col="col-md-2"><div className="input-group"><input type="color" className="form-control form-control-color" value={b.accent_color || "#202C4B"} disabled={!enabled} onChange={(e) => set("accent_color", e.target.value)} /><input className="form-control" value={b.accent_color || ""} disabled={!enabled} onChange={(e) => set("accent_color", e.target.value)} placeholder="#202C4B" /></div></Field>
          <Field label="Email sender name" col="col-md-4"><input className="form-control" value={b.email_from_name || ""} disabled={!enabled} onChange={(e) => set("email_from_name", e.target.value)} placeholder={me.tenant.display_name} /></Field>
          <Field label="Footer text (app and documents)" col="col-md-8"><input className="form-control" value={b.footer_text || ""} disabled={!enabled} onChange={(e) => set("footer_text", e.target.value)} placeholder={`© ${new Date().getFullYear()} ${me.tenant.display_name}`} /></Field>
          <div className="col-md-4 mb-3 d-flex align-items-end"><div className="form-check"><input className="form-check-input" type="checkbox" id="hidepb" checked={Boolean(b.hide_powered_by)} disabled={!enabled} onChange={(e) => set("hide_powered_by", e.target.checked)} /><label htmlFor="hidepb" className="form-check-label fs-13">Hide "powered by {d.platform?.site_name}"</label></div></div>
        </div>
      </div></div>

      <div className="card mb-3"><div className="card-header"><strong>Images</strong></div><div className="card-body"><div className="row g-3">
        {assets.map((a) => (
          <div className="col-md-4" key={a.kind}>
            <div className="border rounded p-3 text-center h-100">
              <div className="fw-semibold fs-13 mb-2">{a.label}</div>
              <div className="d-flex align-items-center justify-content-center bg-light rounded mb-2" style={{ height: 64 }}>{b[a.kind] ? <img src={b[a.kind]} alt={a.label} style={{ maxHeight: 56, maxWidth: "100%" }} /> : <span className="text-muted fs-12">none</span>}</div>
              <input ref={(el) => (files.current[a.kind] = el)} type="file" accept="image/png,image/jpeg,image/svg+xml,image/webp,image/x-icon" className="d-none" onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(a.kind, f); }} />
              <button className="btn btn-sm btn-outline-primary" disabled={!enabled} onClick={() => files.current[a.kind]?.click()}><i className="ti ti-upload me-1" />Upload</button>
              <div className="fs-11 text-muted mt-2">{a.hint}</div>
            </div>
          </div>
        ))}
      </div></div></div>

      <div className="card"><div className="card-header"><strong>Preview</strong></div><div className="card-body">
        <div className="d-flex align-items-center gap-3 border rounded p-3" style={{ background: "#F8F9FA" }}>
          {(enabled && b.logo) ? <img src={b.logo} alt="" style={{ maxHeight: 40 }} /> : d.platform?.logo_url ? <img src={d.platform.logo_url} alt="" style={{ maxHeight: 40 }} /> : <span className="fw-bold fs-20" style={{ color: (enabled && b.primary_color) || d.platform?.primary_color }}>{(enabled && b.app_name) || d.platform?.site_name}</span>}
          <div><div className="fw-semibold">{(enabled && b.app_name) || me.tenant.display_name}</div><div className="fs-12 text-muted">{(enabled && b.footer_text) || d.platform?.footer_text || `© ${new Date().getFullYear()} ${me.tenant.display_name}`}{enabled && b.hide_powered_by ? "" : ` · powered by ${d.platform?.site_name}`}</div></div>
        </div>
        <div className="fs-12 text-muted mt-2">Your company logo on invoices and delivery notes is set under <Link to="/app/settings/company">Settings → Company</Link> and works on every plan.</div>
      </div></div>
    </>
  );
}

export function WarehousesPage() {
  const qc = useQueryClient();
  const [binModal, setBinModal] = useState<{ open: boolean; warehouse?: any }>({ open: false });
  return (
    <>
      <CrudPage page="settings_warehouses" title="Warehouses" crumbs={[{ label: "Settings" }, { label: "Warehouses" }]} endpoint="/api/v1/settings/warehouses"
        extraActions={(w: any) => (w.is_default ? <span className="badge bg-primary-light text-primary align-self-center me-1">default</span> : <button className="btn btn-sm btn-outline-primary" title="Make this the default warehouse" onClick={async () => { try { await api.post(`/api/v1/settings/warehouses/${w.id}/set-default`); toast.success(`${w.name} is now the default warehouse`); qc.invalidateQueries({ queryKey: ["crud", "/api/v1/settings/warehouses"] }); qc.invalidateQueries({ queryKey: ["options", "/api/v1/settings/warehouses"] }); } catch (e: any) { toast.error(e.message); } }}>Make default</button>)}
        columns={[{ key: "name", header: "Warehouse", render: (w: any) => <><span className="fw-semibold text-dark">{w.name}</span>{w.is_default && <span className="badge bg-primary-light text-primary ms-2">default</span>}<div className="text-muted fs-11">{w.code}</div></> }, { key: "type", header: "Type", render: (w: any) => titleCase(w.type) }, { key: "address", header: "Address" }, { key: "manager_name", header: "Manager" }, { key: "bins", header: "Bins", render: (w: any) => <span className="no-row-click">{w.bins.map((b: any) => b.code).join(", ") || "—"} <button className="btn btn-sm btn-link p-0 ms-1" onClick={() => setBinModal({ open: true, warehouse: w })}><i className="ti ti-plus" /></button></span> }, { key: "allow_negative_stock", header: "Negative stock", render: (w: any) => (w.allow_negative_stock ? "allowed" : "no") }, { key: "is_active", header: "Status", render: (w: any) => <StatusBadge status={w.is_active ? "active" : "inactive"} /> }]}
        fields={[{ name: "name", label: "Name", required: true, col: "col-md-5" }, { name: "code", label: "Code", required: true, col: "col-md-3" }, { name: "type", label: "Type", type: "select", options: [{ value: "main", label: "Main" }, { value: "sub", label: "Sub" }, { value: "van", label: "Van / in-transit" }, { value: "quarantine", label: "Quarantine" }], default: "main", col: "col-md-4", required: true }, { name: "address", label: "Address", type: "textarea", col: "col-md-6" }, { name: "manager", label: "Manager", type: "entity", endpoint: "/api/v1/users", mapOption: (u) => ({ id: u.user_id, label: u.full_name }), col: "col-md-6" }, { name: "vehicle", label: "Vehicle (van warehouses)", type: "entity", endpoint: "/api/v1/vehicles", mapOption: (v) => ({ id: v.id, label: v.reg_no }), col: "col-md-4" }, { name: "allow_negative_stock", label: "Allow negative stock", type: "checkbox", col: "col-md-4" }, { name: "is_active", label: "Active", type: "checkbox", default: true, col: "col-md-4" }]}
        intro={<Alert kind="info">Main = central store · Sub = second store · Van = virtual per-vehicle warehouse (used by two-step dispatch) · Quarantine = damaged/returned stock awaiting decision. Create a warehouse with code <code>TRANSIT</code> and type Van (no vehicle) to route inter-warehouse transfers through an in-transit account. The <strong>default</strong> warehouse is pre-selected on new sales orders, purchase orders, receipts and imports — use “Make default” on the one you work from.</Alert>} />
      <ResourceModal open={binModal.open} onClose={() => setBinModal({ open: false })} title={`Add bin to ${binModal.warehouse?.name}`} endpoint={`/api/v1/settings/warehouses/${binModal.warehouse?.id}/bins`} fields={[{ name: "code", label: "Bin code", required: true, col: "col-md-6" }, { name: "name", label: "Name", col: "col-md-6" }]} onSaved={() => qc.invalidateQueries({ queryKey: ["crud", "/api/v1/settings/warehouses"] })} />
    </>
  );
}

export function TaxesPage() {
  const qc = useQueryClient();
  const [tab, setTab] = useState("types");
  const [rateModal, setRateModal] = useState<{ open: boolean; tax?: any }>({ open: false });
  return (
    <>
      <PageHeader title="Taxes" crumbs={[{ label: "Settings" }, { label: "Taxes" }]} />
      <Alert kind="info">Define tax <strong>types</strong> (sales tax, further tax, withholding…) with rates by date, then combine up to two per <strong>tax class</strong>. Every product carries a tax class; a customer/supplier can override it (e.g. unregistered → further tax). Each type posts to its own GL account.</Alert>
      <Tabs tabs={[{ key: "types", label: "Tax types & rates" }, { key: "classes", label: "Tax classes" }]} active={tab} onChange={setTab} />
      {tab === "types" && <><CrudPage page="settings_taxes" title="Tax types" crumbs={[]} endpoint="/api/v1/settings/tax-types" columns={[{ key: "code", header: "Code" }, { key: "name", header: "Name" }, { key: "kind", header: "Kind", render: (t: any) => titleCase(t.kind) }, { key: "applies_to", header: "Applies to" }, { key: "current_rate", header: "Current rate", render: (t: any) => `${t.current_rate}%` }, { key: "rates", header: "Rates", render: (t: any) => <span className="no-row-click">{t.rates.map((r: any) => <span key={r.id} className="badge bg-light text-dark me-1">{r.rate_pct}% from {r.valid_from}{r.valid_to ? ` to ${r.valid_to}` : ""} <a className="text-danger cursor-pointer" onClick={async () => { await api.delete(`/api/v1/settings/tax-types/${t.id}/rates/${r.id}`); qc.invalidateQueries({ queryKey: ["crud", "/api/v1/settings/tax-types"] }); }}>×</a></span>)}<button className="btn btn-sm btn-link p-0" onClick={() => setRateModal({ open: true, tax: t })}><i className="ti ti-plus" /> rate</button></span> }, { key: "output_account_name", header: "Output / input GL", render: (t: any) => <>{t.output_account_name}<div className="text-muted fs-11">{t.input_account_name}</div></> }]}
        fields={[{ name: "code", label: "Code", required: true, col: "col-md-3" }, { name: "name", label: "Name", required: true, col: "col-md-5" }, { name: "kind", label: "Kind", type: "select", options: ["sales_tax", "further_tax", "withholding", "other"].map((k) => ({ value: k, label: titleCase(k) })), default: "sales_tax", col: "col-md-4" }, { name: "applies_to", label: "Applies to", type: "select", options: ["sales", "purchases", "both"].map((k) => ({ value: k, label: titleCase(k) })), default: "both", col: "col-md-4" }, { name: "output_account", label: "Output (payable) account", type: "entity", endpoint: "/api/v1/accounting/accounts", query: { is_group: false, type: "liability" }, mapOption: acctOption, col: "col-md-4" }, { name: "input_account", label: "Input (receivable) account", type: "entity", endpoint: "/api/v1/accounting/accounts", query: { is_group: false, type: "asset" }, mapOption: acctOption, col: "col-md-4" }, { name: "is_active", label: "Active", type: "checkbox", default: true }]} />
        <ResourceModal open={rateModal.open} onClose={() => setRateModal({ open: false })} title={`Add rate — ${rateModal.tax?.name}`} endpoint={`/api/v1/settings/tax-types/${rateModal.tax?.id}/rates`} fields={[{ name: "name", label: "Label", col: "col-md-4" }, { name: "rate_pct", label: "Rate %", type: "number", required: true, col: "col-md-4" }, { name: "valid_from", label: "Valid from", type: "date", required: true, col: "col-md-4" }, { name: "valid_to", label: "Valid to", type: "date", col: "col-md-4" }]} onSaved={() => qc.invalidateQueries({ queryKey: ["crud", "/api/v1/settings/tax-types"] })} /></>}
      {tab === "classes" && <CrudPage page="settings_taxes" title="Tax classes" crumbs={[]} endpoint="/api/v1/settings/tax-classes" columns={[{ key: "name", header: "Class" }, { key: "tax1_name", header: "Tax 1" }, { key: "tax2_name", header: "Tax 2", render: (c: any) => <>{c.tax2_name || "—"}{c.tax2_compounds_on_tax1 && <span className="badge bg-light text-dark ms-1">compounds</span>}</> }, { key: "is_default", header: "Default", render: (c: any) => (c.is_default ? "yes" : "") }]} fields={[{ name: "name", label: "Name", required: true, col: "col-md-6" }, { name: "tax1", label: "Tax 1", type: "entity", endpoint: "/api/v1/settings/tax-types", mapOption: (t) => ({ id: t.id, label: t.name }), col: "col-md-6" }, { name: "tax2", label: "Tax 2", type: "entity", endpoint: "/api/v1/settings/tax-types", mapOption: (t) => ({ id: t.id, label: t.name }), col: "col-md-6" }, { name: "tax2_compounds_on_tax1", label: "Tax 2 compounds on tax 1", type: "checkbox", col: "col-md-3" }, { name: "is_default", label: "Default class", type: "checkbox", col: "col-md-3" }]} />}
    </>
  );
}

export function NumberingPage() {
  return <CrudPage page="settings_numbering" title="Document numbering" crumbs={[{ label: "Settings" }, { label: "Numbering" }]} endpoint="/api/v1/settings/numbering" columns={[{ key: "document_type", header: "Document", render: (n: any) => titleCase(n.document_type) }, { key: "prefix", header: "Prefix" }, { key: "next_number", header: "Next number", num: true }, { key: "padding", header: "Padding", num: true }, { key: "reset", header: "Reset" }, { key: "preview", header: "Preview", render: (n: any) => <code>{n.prefix}{n.reset === "yearly" ? `${new Date().getFullYear()}-` : ""}{String(n.next_number).padStart(n.padding, "0")}</code> }]} fields={[{ name: "prefix", label: "Prefix", col: "col-md-4" }, { name: "next_number", label: "Next number", type: "number", col: "col-md-4" }, { name: "padding", label: "Padding (digits)", type: "number", col: "col-md-4" }, { name: "reset", label: "Reset", type: "select", options: [{ value: "never", label: "Never" }, { value: "yearly", label: "Yearly" }], col: "col-md-4" }]} canDelete={false} />;
}

export function PostingRulesPage() {
  return <CrudPage page="settings_posting_rules" title="Posting rules (account mapping)" crumbs={[{ label: "Settings" }, { label: "Posting rules" }]} endpoint="/api/v1/settings/posting-rules" columns={[{ key: "role_label", header: "Role" }, { key: "document_type", header: "Document type" }, { key: "account_code", header: "Account", render: (r: any) => `${r.account_code} ${r.account_name}` }]} fields={[{ name: "role", label: "Role", col: "col-md-6", readOnly: true }, { name: "document_type", label: "Document type", col: "col-md-6", hint: "* = all documents" }, { name: "account", label: "Account", type: "entity", endpoint: "/api/v1/accounting/accounts", query: { is_group: false }, mapOption: acctOption, required: true, col: "col-md-12" }]} canDelete={false} intro={<Alert kind="info">Automatic journals look up the account for each role here, so you can remap accounts without code changes. Add a document-type specific rule to override the default (<code>*</code>).</Alert>} />;
}

export function ReasonCodesPage() {
  return <CrudPage page="settings_reason_codes" title="Adjustment reason codes" crumbs={[{ label: "Settings" }, { label: "Reason codes" }]} endpoint="/api/v1/settings/reason-codes" columns={[{ key: "code", header: "Code" }, { key: "name", header: "Reason" }, { key: "category", header: "Category", render: (r: any) => titleCase(r.category) }, { key: "expense_account_name", header: "Expense account" }, { key: "requires_approval", header: "Approval", render: (r: any) => (r.requires_approval ? "required" : "no") }, { key: "requires_photo", header: "Photo", render: (r: any) => (r.requires_photo ? "required" : "no") }, { key: "is_active", header: "Active", render: (r: any) => (r.is_active ? "Yes" : "No") }]} fields={[{ name: "code", label: "Code", required: true, col: "col-md-3" }, { name: "name", label: "Name", required: true, col: "col-md-5" }, { name: "category", label: "Category", type: "select", options: ["damage", "expiry", "theft", "lost", "sample", "promotion", "count", "correction", "transit_damage", "other"].map((c) => ({ value: c, label: titleCase(c) })), required: true, col: "col-md-4" }, { name: "expense_account", label: "Expense account", type: "entity", endpoint: "/api/v1/accounting/accounts", query: { is_group: false, type: "expense" }, mapOption: acctOption, col: "col-md-6" }, { name: "requires_approval", label: "Requires approval", type: "checkbox", col: "col-md-2" }, { name: "requires_photo", label: "Requires photo", type: "checkbox", col: "col-md-2" }, { name: "is_active", label: "Active", type: "checkbox", default: true, col: "col-md-2" }]} />;
}

export { ProfilePage as NotificationSettingsPage } from "../users/UsersPages";

export function ApiKeysPage() {
  const qc = useQueryClient();
  const { feature } = usePerms();
  const [created, setCreated] = useState<any>(null);
  const scopes = useOptions("/api/v1/api-keys/scopes");
  const scopesRender = (value: any, set: (v: any) => void) => <div className="d-flex flex-wrap gap-2">{scopes.data?.map((s: any) => <div className="form-check" key={s.scope}><input className="form-check-input" type="checkbox" id={`s-${s.scope}`} checked={(value || []).includes(s.scope)} onChange={(e) => set(e.target.checked ? [...(value || []), s.scope] : (value || []).filter((x: string) => x !== s.scope))} /><label className="form-check-label" htmlFor={`s-${s.scope}`}>{s.label} <span className="text-muted fs-11">{s.scope}</span></label></div>)}</div>;
  return (
    <>
      {!feature("external_api") && <Alert kind="warning">The external API is not included in your plan.</Alert>}
      <CrudPage page="settings_api_keys" title="API keys" crumbs={[{ label: "Settings" }, { label: "API keys" }]} endpoint="/api/v1/api-keys" columns={[{ key: "name", header: "Name" }, { key: "key_prefix", header: "Key", render: (k: any) => <code>{k.key_prefix}…</code> }, { key: "supplier_name", header: "Supplier" }, { key: "scopes", header: "Scopes", render: (k: any) => k.scopes.join(", ") }, { key: "rate_limit", header: "Rate/min", num: true }, { key: "usage_month", header: "Calls this month", num: true }, { key: "last_used_at", header: "Last used", render: (k: any) => datetime(k.last_used_at) }, { key: "is_active", header: "Status", render: (k: any) => <StatusBadge status={k.is_active ? "active" : "inactive"} /> }]}
        fields={[{ name: "name", label: "Name", required: true, col: "col-md-6" }, { name: "supplier", label: "Bound to supplier", type: "entity", endpoint: "/api/v1/suppliers", mapOption: (s) => ({ id: s.id, label: s.name }), col: "col-md-6", hint: "The key only sees this supplier's customers/orders" }, { name: "scopes", label: "Scopes", type: "custom", render: scopesRender, col: "col-md-12", default: ["orders:write", "orders:read", "customers:read", "catalog:read"] }, { name: "rate_limit", label: "Rate limit / min", type: "number", col: "col-md-3", default: 60 }, { name: "expires_at", label: "Expires", type: "date", col: "col-md-3" }, { name: "ip_allowlist", label: "IP allow-list (JSON list)", type: "json", col: "col-md-6", default: [] }, { name: "is_active", label: "Active", type: "checkbox", default: true }]}
        extraActions={(k: any) => <button className="btn btn-sm btn-outline-secondary" title="Rotate" onClick={async () => { const r = await api.post(`/api/v1/api-keys/${k.id}/rotate`); setCreated(r); qc.invalidateQueries({ queryKey: ["crud", "/api/v1/api-keys"] }); }}><i className="ti ti-refresh" /></button>}
        intro={<Alert kind="info">Keys are shown <strong>once</strong> when created. Machine clients send <code>X-API-Key</code> to <code>/ext/v1/…</code> (see the API docs).</Alert>} onSaved={(row) => row?.key && setCreated(row)} />
      <Modal open={Boolean(created)} title="API key created" onClose={() => setCreated(null)} size="md"><Alert kind="warning">Copy this key now — it will not be shown again.</Alert><pre className="bg-light p-3 rounded user-select-all">{created?.key}</pre><div className="fs-13">Base URL: <code>{window.location.origin}/ext/v1</code> · header <code>X-API-Key</code></div></Modal>
    </>
  );
}

export function WebhooksPage() {
  const qc = useQueryClient();
  const events = useOptions("/api/v1/webhooks/events");
  const [deliveries, setDeliveries] = useState<any>(null);
  const evRender = (value: any, set: (v: any) => void) => <div className="d-flex flex-wrap gap-2">{events.data?.map((e: string) => <div className="form-check" key={e}><input className="form-check-input" type="checkbox" id={`e-${e}`} checked={(value || []).includes(e)} onChange={(ev) => set(ev.target.checked ? [...(value || []), e] : (value || []).filter((x: string) => x !== e))} /><label className="form-check-label" htmlFor={`e-${e}`}>{e}</label></div>)}</div>;
  return (
    <>
      <CrudPage page="settings_webhooks" title="Webhooks" crumbs={[{ label: "Settings" }, { label: "Webhooks" }]} endpoint="/api/v1/webhooks" columns={[{ key: "url", header: "URL" }, { key: "events", header: "Events", render: (w: any) => w.events.join(", ") }, { key: "supplier_name", header: "Supplier filter" }, { key: "secret", header: "Secret", render: (w: any) => <code className="fs-11">{w.secret}</code> }, { key: "recent_failures", header: "Failed", num: true }, { key: "is_active", header: "Status", render: (w: any) => <StatusBadge status={w.is_active ? "active" : "inactive"} /> }]}
        fields={[{ name: "url", label: "Endpoint URL", required: true, col: "col-md-8" }, { name: "supplier", label: "Only this supplier's events", type: "entity", endpoint: "/api/v1/suppliers", mapOption: (s) => ({ id: s.id, label: s.name }), col: "col-md-4" }, { name: "events", label: "Events", type: "custom", render: evRender, col: "col-md-12", default: [] }, { name: "description", label: "Description", col: "col-md-9" }, { name: "is_active", label: "Active", type: "checkbox", default: true, col: "col-md-3" }]}
        extraActions={(w: any) => <><button className="btn btn-sm btn-outline-secondary" onClick={async () => { const d = await api.post(`/api/v1/webhooks/${w.id}/test`); toast[d.status === "delivered" ? "success" : "error"](`Test ${d.status} (${d.last_status_code || d.last_error})`); qc.invalidateQueries({ queryKey: ["crud", "/api/v1/webhooks"] }); }}>Test</button><button className="btn btn-sm btn-outline-secondary" onClick={async () => setDeliveries({ hook: w, rows: await api.get(`/api/v1/webhooks/${w.id}/deliveries`) })}>Log</button></>}
        intro={<Alert kind="info">Payloads are signed with <code>X-DMS-Signature: sha256=HMAC(secret, body)</code> and retried with exponential backoff up to 6 times.</Alert>} />
      <Modal open={Boolean(deliveries)} title={`Deliveries — ${deliveries?.hook.url}`} onClose={() => setDeliveries(null)} size="xl"><table className="table table-sm fs-12"><thead className="table-light"><tr><th>When</th><th>Event</th><th>Status</th><th>Attempts</th><th>HTTP</th><th>Error</th><th /></tr></thead><tbody>{deliveries?.rows.map((d: any) => <tr key={d.id}><td>{datetime(d.created_at)}</td><td>{d.event}</td><td><StatusBadge status={d.status === "delivered" ? "posted" : d.status} label={d.status} /></td><td>{d.attempts}</td><td>{d.last_status_code}</td><td className="text-truncate" style={{ maxWidth: 260 }}>{d.last_error}</td><td>{d.status !== "delivered" && <button className="btn btn-sm btn-link p-0" onClick={async () => { await api.post(`/api/v1/webhooks/${deliveries.hook.id}/deliveries/${d.id}/retry`); setDeliveries({ hook: deliveries.hook, rows: await api.get(`/api/v1/webhooks/${deliveries.hook.id}/deliveries`) }); }}>retry</button>}</td></tr>)}</tbody></table></Modal>
    </>
  );
}

export function ImportsPage() {
  const [sp] = useSearchParams();
  const jobs = useQuery({ queryKey: ["imports"], queryFn: () => api.list("/api/v1/imports", { page_size: 30 }) });
  return (
    <>
      <PageHeader title="Imports & exports" crumbs={[{ label: "Settings" }, { label: "Imports" }]} />
      <div className="row g-3 mb-3">
        <div className="col-md-7"><div className="card h-100"><div className="card-header"><strong>Import from Excel / CSV</strong></div><div className="card-body"><ImportPanel type={sp.get("type") || "products"} /></div></div></div>
        <div className="col-md-5"><div className="card h-100"><div className="card-header"><strong>Export CSV</strong></div><div className="card-body"><div className="d-flex flex-wrap gap-2">{["products", "customers", "suppliers", "stock"].map((k) => <button key={k} className="btn btn-outline-success" onClick={() => api.download(`/api/v1/exports/${k}`, `${k}.csv`)}><i className="ti ti-file-type-csv me-1" />{titleCase(k)}</button>)}</div><div className="fs-12 text-muted mt-3">The products export uses the same columns as the import (Code, Product Name, Category, Brand, Supplier, UNIT, Packing, D.P, T.P, R.P…), so you can edit it in Excel and import it back.</div></div></div></div>
      </div>
      <div className="card"><div className="card-header"><strong>Import history</strong></div><div className="card-body p-0"><table className="table table-sm fs-13 mb-0"><thead className="table-light"><tr><th>When</th><th>Type</th><th>By</th><th className="num">Rows</th><th className="num">OK</th><th className="num">Failed</th><th>Result</th><th>Status</th><th>Errors</th></tr></thead><tbody>{jobs.data?.results.map((j: any) => <tr key={j.id}><td>{datetime(j.created_at)}</td><td>{importLabel(j.type)}</td><td>{j.started_by_name}</td><td className="num">{j.rows_total}</td><td className="num text-success">{j.rows_ok}</td><td className="num text-danger">{j.rows_failed}</td><td className="fs-12">
        {j.summary?.po_id && <><Link to={`/app/purchasing/orders/${j.summary.po_id}`} className="fw-semibold">{j.summary.po_number}</Link> · {j.summary.lines} lines · {money(j.summary.total)}<div className="text-muted fs-11">{j.summary.supplier} · {j.summary.reference}{j.summary.supplier_ref ? ` · doc ${j.summary.supplier_ref}` : ""}{j.summary.skipped_no_qty ? ` · ${j.summary.skipped_no_qty} rows without qty skipped` : ""}</div></>}
        {j.summary?.created !== undefined && <>{j.summary.created} new · {j.summary.updated} updated{j.summary.categories_created ? ` · ${j.summary.categories_created} categories` : ""}</>}
        {j.summary?.price_list_date && <div className="text-muted fs-11">price list {date(j.summary.price_list_date)}{j.summary.title ? ` · ${j.summary.title}` : ""}</div>}
      </td><td><StatusBadge status={j.status} /></td><td>{j.errors.length > 0 && <details className="fs-11"><summary className="cursor-pointer">{j.errors.length} error(s)</summary><ul className="mb-0">{j.errors.slice(0, 20).map((e: any, i: number) => <li key={i}>{e.row ? `row ${e.row}: ` : ""}{e.error}</li>)}</ul></details>}</td></tr>)}</tbody></table></div></div>
    </>
  );
}

export function SubscriptionPage() {
  const q = useQuery({ queryKey: ["subscription"], queryFn: () => api.get("/api/v1/subscription") });
  if (q.isLoading) return <Spinner />;
  const d = q.data;
  if (!d) return null;
  const s = d.subscription;
  const usageRows = [["users", "max_users"], ["reps", "max_reps"], ["warehouses", "max_warehouses"], ["suppliers", "max_suppliers"], ["products", "max_products"], ["orders_month", "max_orders_month"], ["storage_mb", "max_storage_mb"]];
  return (
    <>
      <PageHeader title="Subscription" crumbs={[{ label: "Settings" }, { label: "Subscription" }]} />
      {s && <div className="row g-3 mb-3"><div className="col-6 col-md-3"><StatCard label="Plan" value={s.plan_name} icon="ti-stack-2" color="primary" sub={<StatusBadge status={s.status} />} /></div><div className="col-6 col-md-3"><StatCard label="Period" value={`${date(s.start_date)} → ${date(s.end_date)}`} icon="ti-calendar" color="info" sub={`${s.days_left} days left`} /></div><div className="col-6 col-md-3"><StatCard label="Price" value={`${s.plan_snapshot.currency} ${money(s.plan_snapshot.price, 0)}`} icon="ti-cash" color="success" sub={`per ${s.plan_snapshot.billing_cycle}`} /></div><div className="col-6 col-md-3"><StatCard label="Support" value={titleCase(String(d.features.support || "email"))} icon="ti-lifebuoy" color="secondary" /></div></div>}
      <div className="row g-3 mb-3"><div className="col-md-6"><div className="card h-100"><div className="card-header"><strong>Usage vs limits</strong></div><div className="card-body">{usageRows.map(([u, l]) => { const cur = Number(d.usage[u] || 0); const lim = d.limits[l]; const pct = lim && lim !== -1 ? Math.min(100, (cur / lim) * 100) : 0; return <div key={u} className="mb-2"><div className="d-flex justify-content-between fs-13"><span>{titleCase(u)}</span><span>{cur} / {lim === -1 || lim === undefined ? "∞" : lim}</span></div><div className="progress" style={{ height: 6 }}><div className={`progress-bar ${pct > 90 ? "bg-danger" : pct > 70 ? "bg-warning" : "bg-success"}`} style={{ width: `${pct}%` }} /></div></div>; })}</div></div></div>
        <div className="col-md-6"><div className="card h-100"><div className="card-header"><strong>Features</strong></div><div className="card-body d-flex flex-wrap gap-2">{Object.entries(d.features).map(([k, v]) => <span key={k} className={`badge ${v ? "bg-success-light text-success" : "bg-secondary-light text-secondary"}`}>{titleCase(k)}{typeof v === "string" ? `: ${v}` : ""}</span>)}</div></div></div></div>
      <div className="card mb-3"><div className="card-header"><strong>Invoices from the platform</strong></div><div className="card-body p-0"><table className="table table-sm fs-13 mb-0"><thead className="table-light"><tr><th>Invoice</th><th>Period</th><th className="num">Total</th><th className="num">Paid</th><th>Status</th></tr></thead><tbody>{d.invoices.map((i: any) => <tr key={i.id}><td>{i.number}</td><td>{date(i.period_start)} → {date(i.period_end)}</td><td className="num">{money(i.total)}</td><td className="num">{money(i.paid_amount)}</td><td><StatusBadge status={i.status} /></td></tr>)}{!d.invoices.length && <tr><td colSpan={5} className="text-muted text-center py-3">No invoices yet</td></tr>}</tbody></table></div></div>
      <div className="card"><div className="card-header"><strong>Payments</strong></div><div className="card-body p-0"><table className="table table-sm fs-13 mb-0"><thead className="table-light"><tr><th>Date</th><th>Invoice</th><th>Method</th><th>Reference</th><th className="num">Amount</th></tr></thead><tbody>{d.payments.map((p: any) => <tr key={p.id}><td>{date(p.paid_at)}</td><td>{p.invoice_number}</td><td>{titleCase(p.method)}</td><td>{p.reference}</td><td className="num">{money(p.amount)}</td></tr>)}</tbody></table></div></div>
    </>
  );
}

export function SupportPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["tickets"], queryFn: () => api.list("/api/v1/support-tickets", { page_size: 50 }) });
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState<any>(null);
  const [reply, setReply] = useState("");
  return (
    <>
      <PageHeader title="Support" crumbs={[{ label: "Settings" }, { label: "Support" }]} actions={<button className="btn btn-primary" onClick={() => setOpen(true)}><i className="ti ti-plus me-1" />New ticket</button>} />
      <div className="row g-3"><div className="col-md-5"><div className="card"><div className="list-group list-group-flush">{q.data?.results.map((t: any) => <a key={t.id} className={`list-group-item list-group-item-action cursor-pointer ${active?.id === t.id ? "active" : ""}`} onClick={() => setActive(t)}><div className="d-flex justify-content-between"><span className="fw-medium">{t.subject}</span><StatusBadge status={t.status} /></div><div className="fs-11 text-muted">{t.priority} · {datetime(t.updated_at)}</div></a>)}{q.data && !q.data.results.length && <div className="p-4 text-muted text-center">No tickets</div>}</div></div></div>
        <div className="col-md-7">{active && <div className="card"><div className="card-header"><strong>{active.subject}</strong></div><div className="card-body">{active.messages.map((m: any) => <div key={m.id} className={`mb-3 p-2 rounded ${m.is_staff_reply ? "bg-light" : "bg-primary-light"}`}><div className="fs-11 text-muted">{m.author_name || (m.is_staff_reply ? "Support" : "You")} · {datetime(m.created_at)}</div><div className="fs-13">{m.body}</div></div>)}<div className="input-group"><input className="form-control" placeholder="Reply…" value={reply} onChange={(e) => setReply(e.target.value)} /><button className="btn btn-primary" disabled={!reply} onClick={async () => { await api.post(`/api/v1/support-tickets/${active.id}/reply`, { body: reply }); setReply(""); const fresh = await api.get(`/api/v1/support-tickets/${active.id}`); setActive(fresh); qc.invalidateQueries({ queryKey: ["tickets"] }); }}>Send</button></div></div></div>}</div></div>
      <ResourceModal open={open} onClose={() => setOpen(false)} title="New support ticket" endpoint="/api/v1/support-tickets" fields={[{ name: "subject", label: "Subject", required: true, col: "col-md-8" }, { name: "priority", label: "Priority", type: "select", options: ["low", "normal", "high", "urgent"].map((p) => ({ value: p, label: titleCase(p) })), default: "normal", col: "col-md-4" }, { name: "body", label: "Describe the issue", type: "textarea", col: "col-md-12", required: true }]} onSaved={() => qc.invalidateQueries({ queryKey: ["tickets"] })} />
    </>
  );
}
