import { DateInput } from "@/components/ui/DateInput";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Chart from "react-apexcharts";
import { api } from "@/api/client";
import { date, datetime, money, titleCase } from "@/lib/format";
import { useAuth } from "@/lib/auth";
import { useDoc, useOptions } from "@/lib/hooks";
import { toast } from "@/lib/toast";
import { Alert, Field, InfoItem, PageHeader, Spinner, StatCard, StatusBadge, Tabs } from "@/components/ui/Bits";
import { CrudPage } from "@/components/ui/CrudPage";
import { DiscountDialog, TenantFlags, TenantRowActions } from "./TenantActions";
import { Column, DataTable } from "@/components/ui/DataTable";
import { ListPage } from "@/components/ui/ListPage";
import { ConfirmDialog, Modal } from "@/components/ui/Modal";
import { FieldDef, ResourceForm, ResourceModal } from "@/components/ui/ResourceForm";

const P = "/api/v1/platform";

export function PlatformDashboardPage() {
  const q = useQuery({ queryKey: ["platform-dash"], queryFn: () => api.get(`${P}/dashboard`) });
  if (q.isLoading) return <Spinner />;
  const d = q.data;
  return (
    <>
      <PageHeader title="Platform dashboard" crumbs={[]} />
      <div className="row g-3 mb-3">
        <div className="col-6 col-xl-3"><StatCard label="MRR" value={money(d.mrr, 0)} icon="ti-chart-line" color="primary" sub={`ARR ${money(d.arr, 0)}`} /></div>
        <div className="col-6 col-xl-3"><StatCard label="Active tenants" value={d.active_tenants} icon="ti-building" color="success" sub={`${d.trial_tenants} on trial`} to="/admin/tenants" /></div>
        <div className="col-6 col-xl-3"><StatCard label="Past due / suspended" value={`${d.past_due_tenants} / ${d.suspended_tenants}`} icon="ti-alert-triangle" color="warning" sub={`past-due amount ${money(d.past_due_amount, 0)}`} to="/admin/outstanding" /></div>
        <div className="col-6 col-xl-3"><StatCard label="Payments this month" value={money(d.payments_month, 0)} icon="ti-cash" color="info" sub={`churn ${d.churn_month} · API calls ${d.api_calls_month}`} to="/admin/payments" /></div>
      </div>
      <div className="row g-3">
        <div className="col-xl-6"><div className="card h-100"><div className="card-header"><h5 className="card-title mb-0">Revenue by plan (monthly)</h5></div><div className="card-body">{Object.keys(d.revenue_by_plan).length ? <Chart type="bar" height={260} series={[{ name: "MRR", data: Object.values(d.revenue_by_plan).map(Number) }]} options={{ chart: { toolbar: { show: false } }, colors: ["#F26522"], xaxis: { categories: Object.keys(d.revenue_by_plan) }, dataLabels: { enabled: false }, plotOptions: { bar: { borderRadius: 4, columnWidth: "40%" } } }} /> : <div className="text-muted text-center py-4">No paid subscriptions yet</div>}</div></div></div>
        <div className="col-xl-6"><div className="card h-100"><div className="card-header"><h5 className="card-title mb-0">Tenants by status · top tenants by orders</h5></div><div className="card-body"><div className="d-flex flex-wrap gap-2 mb-3">{Object.entries(d.tenants_by_status).map(([k, v]) => <span key={k} className="badge bg-light text-dark fs-12"><StatusBadge status={k} /> {v as number}</span>)}</div><table className="table table-sm fs-13 mb-0"><tbody>{d.top_tenants_by_orders.map((t: any, i: number) => <tr key={i}><td>{t.tenant__display_name}</td><td className="num">{t.c} orders</td></tr>)}</tbody></table></div></div></div>
      </div>
    </>
  );
}


export function PlansPage() {
  const [tab, setTab] = useState("plans");
  const planFields: FieldDef[] = [
    { name: "name", label: "Plan name", required: true, col: "col-md-4" }, { name: "code", label: "Code", required: true, col: "col-md-2", hint: "lowercase, used in the API" },
    { name: "tagline", label: "Tagline", col: "col-md-6", placeholder: "For a single warehouse and a small team" },
    { name: "price", label: "Price per month", type: "money", col: "col-md-3", default: 0 }, { name: "price_annual", label: "Price per year", type: "money", col: "col-md-3", default: 0, hint: "0 = yearly billing not offered" },
    { name: "setup_fee", label: "One-off setup fee", type: "money", col: "col-md-3", default: 0 }, { name: "currency", label: "Currency", col: "col-md-3", default: "PKR" },
    { name: "billing_cycle", label: "Default cycle", type: "select", options: [{ value: "monthly", label: "Monthly" }, { value: "yearly", label: "Yearly" }], default: "monthly", col: "col-md-3" },
    { name: "trial_days", label: "Trial days", type: "number", col: "col-md-3", default: 14 }, { name: "grace_days", label: "Grace days after expiry", type: "number", col: "col-md-3", default: 7 }, { name: "sort_order", label: "Sort order", type: "number", col: "col-md-3", default: 0 },
    { name: "limits", label: "Limits (max_users, max_products, max_warehouses, max_reps, max_orders_month, max_storage_mb — use -1 for unlimited)", type: "json", col: "col-md-12", default: {} },
    { name: "features", label: "Features (barcode, rep_portal, external_api, advanced_accounting, report_scheduling, excel_export, multi_supplier, branding)", type: "json", col: "col-md-12", default: {} },
    { name: "description", label: "Description", type: "textarea", col: "col-md-8" },
    { name: "is_featured", label: "Most popular (highlighted)", type: "checkbox", col: "col-md-2" }, { name: "is_public", label: "Public (self-signup)", type: "checkbox", col: "col-md-2", default: true },
  ];
  const addonFields: FieldDef[] = [
    { name: "name", label: "Add-on", required: true, col: "col-md-4" }, { name: "code", label: "Code", required: true, col: "col-md-2" },
    { name: "kind", label: "Charged", type: "select", options: [{ value: "recurring", label: "Every cycle (recurring)" }, { value: "one_time", label: "Once (setup, training…)" }], default: "recurring", col: "col-md-3" },
    { name: "unit_price", label: "Price per unit", type: "money", col: "col-md-3", default: 0 },
    { name: "unit_label", label: "Unit name", col: "col-md-2", default: "unit", placeholder: "user, warehouse, hour" },
    { name: "limit_key", label: "Raises which limit", col: "col-md-3", placeholder: "max_users" }, { name: "units_per_qty", label: "Units added per quantity", type: "number", col: "col-md-3", default: 1 },
    { name: "max_quantity", label: "Max quantity (blank = no cap)", type: "number", col: "col-md-2" },
    { name: "description", label: "Description", col: "col-md-8" }, { name: "sort_order", label: "Sort order", type: "number", col: "col-md-2", default: 0 },
    { name: "is_active", label: "Active", type: "checkbox", col: "col-md-2", default: true },
  ];
  return (
    <>
      <PageHeader title="Plans, add-ons & discounts" crumbs={[]} />
      <Tabs tabs={[{ key: "plans", label: "Plans" }, { key: "addons", label: "Add-ons" }, { key: "coupons", label: "Coupons" }]} active={tab} onChange={setTab} />
      {tab === "plans" && <CrudPage title="Plans" crumbs={[]} endpoint={`${P}/plans`} fields={planFields}
        intro={<div className="alert alert-light border fs-13"><i className="ti ti-info-circle me-1 text-primary" />A plan carries its price (monthly and/or yearly), what the company may use (limits) and which modules are switched on (features). Changing a price creates a new version — companies already subscribed keep the price they signed up on until you change their plan. Per-company discounts are set on the company's Subscription tab.</div>}
        columns={[
          { key: "name", header: "Plan", render: (p: any) => <><span className="fw-semibold text-dark">{p.name}</span>{p.is_featured && <span className="badge bg-primary-light text-primary ms-2">popular</span>}<div className="text-muted fs-11">{p.code} · v{p.version}{p.tagline ? ` · ${p.tagline}` : ""}</div></> },
          { key: "price", header: "Monthly", num: true, render: (p: any) => `${p.currency} ${money(p.price, 0)}` },
          { key: "price_annual", header: "Yearly", num: true, render: (p: any) => (Number(p.price_annual) ? `${p.currency} ${money(p.price_annual, 0)}` : <span className="text-muted">—</span>) },
          { key: "setup_fee", header: "Setup", num: true, render: (p: any) => (Number(p.setup_fee) ? money(p.setup_fee, 0) : <span className="text-muted">—</span>) },
          { key: "trial_days", header: "Trial", num: true, render: (p: any) => `${p.trial_days} d` },
          { key: "limits", header: "Limits", render: (p: any) => <span className="fs-11">{["max_users", "max_warehouses", "max_products"].map((k) => `${p.limits?.[k] === -1 ? "∞" : p.limits?.[k] ?? "—"}`).join(" / ")}<div className="text-muted">users / warehouses / products</div></span> },
          { key: "is_public", header: "Visibility", render: (p: any) => (p.is_public ? <span className="badge bg-success-light text-success">public</span> : <span className="badge bg-secondary-light text-secondary">private</span>) },
        ]} />}
      {tab === "addons" && <CrudPage title="Add-ons" crumbs={[]} endpoint={`${P}/addons`} fields={addonFields}
        intro={<div className="alert alert-light border fs-13"><i className="ti ti-info-circle me-1 text-primary" />Add-ons sit on top of a plan: recurring ones (extra users, extra warehouses) are charged on every invoice, one-off ones (setup, training, data migration) only once. Linking an add-on to a limit key raises that limit automatically when a company buys it.</div>}
        columns={[
          { key: "name", header: "Add-on", render: (a: any) => <><span className="fw-semibold text-dark">{a.name}</span><div className="text-muted fs-11">{a.code}{a.description ? ` · ${a.description}` : ""}</div></> },
          { key: "kind", header: "Charged", render: (a: any) => (a.kind === "one_time" ? <span className="badge bg-info-light text-info">one-off</span> : <span className="badge bg-success-light text-success">every cycle</span>) },
          { key: "unit_price", header: "Price", num: true, render: (a: any) => <>{money(a.unit_price, 0)}<div className="text-muted fs-11">per {a.unit_label}</div></> },
          { key: "limit_key", header: "Raises", render: (a: any) => (a.limit_key ? <>{a.limit_key}<div className="text-muted fs-11">+{a.units_per_qty} per unit</div></> : <span className="text-muted">—</span>) },
          { key: "max_quantity", header: "Max qty", num: true, render: (a: any) => a.max_quantity || <span className="text-muted">∞</span> },
          { key: "is_active", header: "Active", render: (a: any) => (a.is_active ? <span className="badge bg-success-light text-success">yes</span> : <span className="badge bg-secondary-light text-secondary">no</span>) },
        ]} />}
      {tab === "coupons" && <CrudPage title="Coupons" crumbs={[]} endpoint={`${P}/coupons`}
        intro={<div className="alert alert-light border fs-13"><i className="ti ti-info-circle me-1 text-primary" />Coupons are shared codes anyone may use at renewal. For a price negotiated with one company, use <strong>Give a discount</strong> on that company instead.</div>}
        fields={[{ name: "code", label: "Code", required: true, col: "col-md-3" }, { name: "type", label: "Type", type: "select", options: [{ value: "percent", label: "Percent %" }, { value: "fixed", label: "Fixed amount" }], col: "col-md-3" }, { name: "value", label: "Value", type: "money", col: "col-md-2" }, { name: "max_uses", label: "Max uses", type: "number", col: "col-md-2" }, { name: "valid_from", label: "Valid from", type: "date", col: "col-md-2" }, { name: "valid_to", label: "Valid to", type: "date", col: "col-md-2" }, { name: "is_active", label: "Active", type: "checkbox", col: "col-md-2", default: true }]}
        columns={[{ key: "code", header: "Code", render: (c: any) => <span className="fw-semibold font-monospace">{c.code}</span> }, { key: "value", header: "Discount", render: (c: any) => (c.type === "percent" ? `${Number(c.value)}%` : money(c.value, 0)) }, { key: "valid_from", header: "Valid", render: (c: any) => `${c.valid_from || "…"} → ${c.valid_to || "…"}` }, { key: "used_count", header: "Used", render: (c: any) => `${c.used_count}${c.max_uses ? ` / ${c.max_uses}` : ""}` }, { key: "is_active", header: "Active", render: (c: any) => (c.is_active ? "yes" : "no") }]} />}
    </>
  );
}

export function TenantsPage() {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["list"] });
  const usageBar = (used: number, limit: number) => {
    if (!limit || limit === -1) return null;
    const pct = Math.min(100, Math.round((used / limit) * 100));
    return <div className="progress mt-1" style={{ height: 4 }}><div className={`progress-bar ${pct > 90 ? "bg-danger" : pct > 70 ? "bg-warning" : "bg-success"}`} style={{ width: `${pct}%` }} /></div>;
  };
  const cols: Column<any>[] = [
    { key: "display_name", header: "Company", sortKey: "display_name", render: (t) => (
      <><span className="fw-semibold text-dark">{t.display_name}</span><div className="text-muted fs-11">{t.slug}{t.city ? ` · ${t.city}` : ""}</div><div className="mt-1"><TenantFlags tenant={t} /></div></>
    ) },
    { key: "owner_name", header: "Owner", render: (t) => <>{t.owner_name}<div className="text-muted fs-11">{t.owner_email || t.owner_phone || "—"}</div></> },
    { key: "plan", header: "Plan", render: (t) => t.subscription
      ? <>{t.subscription.plan_name}<div className="text-muted fs-11">{money(t.subscription.price_breakdown?.total ?? t.subscription.plan_snapshot?.price, 0)} / {t.subscription.billing_cycle || "monthly"}</div></>
      : <span className="text-muted">no plan</span> },
    { key: "renewal", header: "Renews", render: (t) => t.subscription
      ? <><span className={t.subscription.days_left < 0 ? "text-danger fw-semibold" : t.subscription.days_left <= 7 ? "text-warning fw-semibold" : ""}>{date(t.subscription.end_date)}</span><div className="text-muted fs-11">{t.subscription.days_left < 0 ? `${-t.subscription.days_left} days overdue` : `in ${t.subscription.days_left} days`}</div></>
      : "—" },
    { key: "usage", header: "Usage", render: (t) => {
      const u = t.usage || {}; const l = t.subscription?.plan_snapshot?.limits || {};
      return <div className="fs-11" style={{ minWidth: 110 }}>
        <div>{u.users ?? "—"}{l.max_users > 0 ? ` / ${l.max_users}` : ""} users</div>{usageBar(u.users, l.max_users)}
        <div className="mt-1">{u.products ?? "—"}{l.max_products > 0 ? ` / ${l.max_products}` : ""} products</div>{usageBar(u.products, l.max_products)}
      </div>;
    } },
    { key: "status", header: "Status", sortKey: "status", render: (t) => <StatusBadge status={t.status} /> },
    { key: "_actions", header: "", className: "text-end", render: (t) => <TenantRowActions tenant={t} onDone={refresh} /> },
  ];
  return <ListPage title="Companies (tenants)" endpoint={`${P}/tenants`} columns={cols} rowLink={(t) => `/admin/tenants/${t.id}`} createTo="/admin/tenants/new" createLabel="New company" defaultOrdering="display_name" searchPlaceholder="Search company, slug, owner, email…"
    filters={[{ name: "status", label: "Status", type: "select", options: ["trial", "active", "past_due", "suspended", "cancelled", "archived"].map((s) => ({ value: s, label: s.replace(/_/g, " ") })) },
      { name: "plan", label: "Plan", type: "entity", endpoint: `${P}/plans`, mapOption: (p: any) => ({ id: p.id, label: p.name }) },
      { name: "expiring", label: "Renewing in 7 days", type: "toggle" }, { name: "white_label", label: "White-label", type: "toggle" }]} />;
}

export function TenantDetailPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { setTenant } = useAuth();
  const q = useDoc(`${P}/tenants`, id);
  const usage = useQuery({ queryKey: ["tenant-usage", id], queryFn: () => api.get(`${P}/tenants/${id}/usage`), enabled: Boolean(id) });
  const subs = useQuery({ queryKey: ["tenant-subs", id], queryFn: () => api.get(`${P}/tenants/${id}/subscriptions`), enabled: Boolean(id) });
  const timeline = useQuery({ queryKey: ["tenant-timeline", id], queryFn: () => api.get(`${P}/tenants/${id}/timeline`), enabled: Boolean(id) });
  const invoices = useQuery({ queryKey: ["tenant-invoices", id], queryFn: () => api.list(`${P}/invoices`, { tenant: id, page_size: 50 }), enabled: Boolean(id) });
  const payments = useQuery({ queryKey: ["tenant-payments", id], queryFn: () => api.list(`${P}/payments`, { tenant: id, page_size: 50 }), enabled: Boolean(id) });
  const plans = useOptions(`${P}/plans`);
  const [tab, setTab] = useState("subscription");
  const [dlg, setDlg] = useState<null | "suspend" | "reactivate" | "cancel" | "renew" | "change" | "trial" | "payment" | "overrides" | "password" | "discount">(null);
  const [renew, setRenew] = useState({ periods: "1", from_today: false, pay: false, amount: "", method: "bank_transfer", reference: "" });
  const [change, setChange] = useState<{ plan: string; when: string; preview?: any }>({ plan: "", when: "immediate" });
  const [pay, setPay] = useState({ invoice: "", amount: "", method: "bank_transfer", reference: "", paid_at: new Date().toISOString().slice(0, 10), note: "" });
  const [trialDays, setTrialDays] = useState("7");
  const [pwd, setPwd] = useState("");
  const refresh = () => { qc.invalidateQueries({ queryKey: ["doc", `${P}/tenants`, id] }); qc.invalidateQueries({ queryKey: ["tenant-subs", id] }); qc.invalidateQueries({ queryKey: ["tenant-timeline", id] }); qc.invalidateQueries({ queryKey: ["tenant-invoices", id] }); qc.invalidateQueries({ queryKey: ["tenant-payments", id] }); };
  if (q.isLoading) return <Spinner />;
  const t = q.data;
  if (!t) return null;
  const sub = t.subscription;
  const act = async (a: string, body: any = {}) => { try { await api.post(`${P}/tenants/${id}/${a}`, body); toast.success("Done"); refresh(); } catch (e: any) { toast.error(e.message); } setDlg(null); };
  const subAct = async (a: string, body: any = {}) => { try { const r = await api.post(`${P}/subscriptions/${sub.id}/${a}`, body); toast.success("Done"); refresh(); return r; } catch (e: any) { toast.error(e.message); throw e; } };
  return (
    <>
      <PageHeader title={t.display_name} badge={<StatusBadge status={t.status} />} crumbs={[{ label: "Tenants", to: "/admin/tenants" }, { label: t.display_name }]}
        actions={<>{t.impersonation_allowed && !["cancelled", "archived"].includes(t.status) && <button className="btn btn-outline-primary" onClick={async () => { try { const r = await api.post(`${P}/tenants/${id}/impersonate`, { reason: "support" }); setTenant(r.tenant_id); qc.clear(); nav("/app"); toast.warning(`Logged in as ${t.owner_name || "owner"} — use "Return to platform admin" in the top bar to come back`); } catch (e: any) { toast.error(e.message); } }}><i className="ti ti-user-screen me-1" />Log in as owner</button>}{["trial", "active", "past_due"].includes(t.status) && <button className="btn btn-outline-warning" onClick={() => setDlg("suspend")}>Suspend</button>}{t.status === "suspended" && <button className="btn btn-outline-success" onClick={() => setDlg("reactivate")}>Reactivate</button>}{!["cancelled", "archived"].includes(t.status) && <button className="btn btn-outline-danger" onClick={() => setDlg("cancel")}>Cancel</button>}<button className="btn btn-outline-secondary" onClick={() => api.download(`${P}/tenants/${id}/export`, `${t.slug}-export.zip`)}><i className="ti ti-download me-1" />Export data</button></>} />
      {t.suspended_reason && <Alert kind="danger">Suspended: {t.suspended_reason}</Alert>}
      <div className="row g-3 mb-3"><div className="col-6 col-md-3"><StatCard label="Plan" value={sub?.plan_name || "—"} icon="ti-stack-2" color="primary" sub={sub && <><StatusBadge status={sub.status} /> ends {date(sub.end_date)}</>} /></div><div className="col-6 col-md-3"><StatCard label="Owner" value={t.owner_name || "—"} icon="ti-user" color="info" sub={t.owner_email || t.owner_phone} /></div><div className="col-6 col-md-3"><StatCard label="Users / reps" value={`${usage.data?.users ?? "—"} / ${usage.data?.reps ?? "—"}`} icon="ti-users" color="success" sub={`${usage.data?.warehouses ?? "—"} warehouses · ${usage.data?.products ?? "—"} products`} /></div><div className="col-6 col-md-3"><StatCard label="Orders this month" value={usage.data?.orders_month ?? "—"} icon="ti-shopping-cart" color="warning" sub={`${usage.data?.storage_mb ?? 0} MB · ${usage.data?.api_calls_month ?? 0} API calls · last activity ${usage.data?.last_activity ? datetime(usage.data.last_activity) : "—"}`} /></div></div>
      <div className="card"><div className="card-body">
        <Tabs tabs={[{ key: "subscription", label: "Subscription" }, { key: "billing", label: "Invoices & payments" }, { key: "features", label: "Features & white-label" }, { key: "profile", label: "Profile & overrides" }, { key: "timeline", label: "Timeline" }, { key: "security", label: "Owner & security" }]} active={tab} onChange={setTab} />
        {tab === "subscription" && sub && <>
          <div className="d-flex flex-wrap gap-2 mb-3"><button className="btn btn-primary" onClick={() => setDlg("renew")}><i className="ti ti-refresh me-1" />Renew</button><button className="btn btn-outline-success" onClick={() => setDlg("discount")}><i className="ti ti-discount-2 me-1" />{sub.discount_active ? "Edit discount" : "Give a discount"}</button><button className="btn btn-outline-primary" onClick={() => setDlg("change")}>Upgrade / downgrade</button>{sub.status === "trial" && <><button className="btn btn-outline-secondary" onClick={() => setDlg("trial")}>Extend trial</button><button className="btn btn-outline-success" onClick={async () => { await subAct("convert-trial", {}); }}>Convert trial</button></>}</div>
          <div className="card bg-light border mb-3"><div className="card-body py-3">
            <div className="row">
              <div className="col-md-7">
                <div className="fs-13 fw-semibold mb-2">What they pay each {sub.billing_cycle || "month"}</div>
                {(sub.price_breakdown?.lines || []).map((l: any, i: number) => <div key={i} className="d-flex justify-content-between fs-13"><span>{l.label}</span><span>{money(l.amount, 0)}</span></div>)}
                {Number(sub.price_breakdown?.discount) > 0 && <div className="d-flex justify-content-between fs-13 text-success"><span>Discount — {sub.discount_label}{sub.discount_reason ? ` (${sub.discount_reason})` : ""}</span><span>− {money(sub.price_breakdown.discount, 0)}</span></div>}
                <div className="d-flex justify-content-between fw-bold border-top mt-2 pt-2"><span>Total</span><span>{sub.price_breakdown?.currency} {money(sub.price_breakdown?.total, 0)}</span></div>
              </div>
              <div className="col-md-5 border-start ps-md-4">
                {sub.discount_active
                  ? <><span className="badge bg-success-light text-success mb-2">discount active</span><div className="fs-13">{sub.discount_label}</div>{sub.discount_cycles > 0 && <div className="fs-12 text-muted">{sub.discount_used} of {sub.discount_cycles} invoices used</div>}{sub.discount_until && <div className="fs-12 text-muted">ends {date(sub.discount_until)}</div>}{sub.discount_set_by_name && <div className="fs-11 text-muted mt-1">set by {sub.discount_set_by_name}</div>}<button className="btn btn-sm btn-outline-danger mt-2" onClick={async () => { try { await api.delete(`${P}/subscriptions/${sub.id}/discount`); toast.success("Discount removed"); refresh(); } catch (e: any) { toast.error(e.message); } }}>Remove discount</button></>
                  : <div className="fs-13 text-muted">No discount. Use <strong>Give a discount</strong> to agree a percentage or a fixed amount off this company's invoices.</div>}
              </div>
            </div>
          </div></div>
          <div className="row g-3"><InfoItem label="Plan / version">{sub.plan_name} v{sub.plan_version}</InfoItem><InfoItem label="Period">{date(sub.start_date)} → {date(sub.end_date)} ({sub.days_left} days)</InfoItem><InfoItem label="Grace days">{sub.grace_days}</InfoItem><InfoItem label="Add-ons">{sub.addons?.map((a: any) => `${a.addon_name} × ${a.quantity}`).join(", ") || "none"}</InfoItem></div>
          <h6 className="fs-13 mt-3">Snapshot limits</h6><div className="d-flex flex-wrap gap-2">{Object.entries(sub.plan_snapshot.limits || {}).map(([k, v]) => <span key={k} className="badge bg-light text-dark">{k}: {v === -1 ? "∞" : String(v)}</span>)}</div>
          <h6 className="fs-13 mt-3">All subscriptions</h6><table className="table table-sm fs-12"><thead className="table-light"><tr><th>Plan</th><th>Status</th><th>Start</th><th>End</th><th>Notes</th></tr></thead><tbody>{subs.data?.map((s: any) => <tr key={s.id}><td>{s.plan_name} v{s.plan_version}</td><td><StatusBadge status={s.status} /></td><td>{date(s.start_date)}</td><td>{date(s.end_date)}</td><td className="text-muted">{s.notes}</td></tr>)}</tbody></table>
        </>}
        {tab === "billing" && <><div className="text-end mb-2"><button className="btn btn-sm btn-primary" onClick={() => setDlg("payment")}><i className="ti ti-cash me-1" />Record payment</button></div>
          <h6 className="fs-13">Invoices</h6><table className="table table-sm fs-12"><thead className="table-light"><tr><th>Invoice</th><th>Period</th><th className="num">Total</th><th className="num">Paid</th><th>Status</th><th /></tr></thead><tbody>{invoices.data?.results.map((i: any) => <tr key={i.id}><td>{i.number}{i.is_credit_note && <span className="badge bg-warning-light text-warning ms-1">credit note</span>}</td><td>{date(i.period_start)} → {date(i.period_end)}</td><td className="num">{money(i.total)}</td><td className="num">{money(i.paid_amount)}</td><td><StatusBadge status={i.status} /></td><td className="text-end"><button className="btn btn-sm btn-link p-0 me-2" onClick={() => api.download(`${P}/invoices/${i.id}/pdf`)}>PDF</button>{i.status !== "paid" && i.status !== "void" && <button className="btn btn-sm btn-link p-0" onClick={() => { setPay({ ...pay, invoice: i.id, amount: String(Number(i.balance)) }); setDlg("payment"); }}>Pay</button>}</td></tr>)}</tbody></table>
          <h6 className="fs-13 mt-3">Payments</h6><table className="table table-sm fs-12"><thead className="table-light"><tr><th>Date</th><th>Invoice</th><th>Method</th><th>Reference</th><th className="num">Amount</th><th>Status</th><th>By</th></tr></thead><tbody>{payments.data?.results.map((p: any) => <tr key={p.id}><td>{date(p.paid_at)}</td><td>{p.invoice_number}</td><td>{titleCase(p.method)}</td><td>{p.reference}</td><td className="num">{money(p.amount)}</td><td><StatusBadge status={p.status} /></td><td>{p.recorded_by_name}</td></tr>)}</tbody></table></>}
        {tab === "features" && <FeaturesTab tenant={t} onChanged={refresh} />}
        {tab === "profile" && <ResourceForm fields={[{ name: "display_name", label: "Display name", col: "col-md-4" }, { name: "legal_name", label: "Legal name", col: "col-md-4" }, { name: "email", label: "Email", type: "email", col: "col-md-4" }, { name: "phone", label: "Phone", col: "col-md-4" }, { name: "city", label: "City", col: "col-md-4" }, { name: "impersonation_allowed", label: "Allow \"Log in as owner\"", type: "checkbox", col: "col-md-4" }, { name: "limit_overrides", label: "Limit overrides (JSON) — e.g. {\"max_users\": 20}", type: "json", col: "col-md-6" }, { name: "feature_overrides", label: "Feature overrides (JSON) — e.g. {\"branding\": true}", type: "json", col: "col-md-6" }]} initial={t} onSubmit={async (v) => { await api.patch(`${P}/tenants/${id}`, v); toast.success("Saved"); refresh(); }} />}
        {tab === "timeline" && <ul className="status-timeline">{timeline.data?.map((e: any) => <li key={e.id}><div className="fs-13 fw-medium">{titleCase(e.event_type)} <span className="fw-normal text-muted">by {e.actor_name || "system"}</span></div><div className="fs-12 text-muted">{datetime(e.at)} {e.reason && `· ${e.reason}`}</div>{e.to_data && <details className="fs-11"><summary className="text-muted cursor-pointer">details</summary><pre className="bg-light p-2 rounded mb-0">{JSON.stringify({ from: e.from_data, to: e.to_data }, null, 1)}</pre></details>}</li>)}</ul>}
        {tab === "security" && <div className="d-flex flex-wrap gap-2"><button className="btn btn-outline-secondary" onClick={() => setDlg("password")}>Reset owner password</button><button className="btn btn-outline-secondary" onClick={() => act("unlock-owner")}>Unlock owner</button><button className="btn btn-outline-danger" onClick={() => act("revoke-sessions")}>Revoke all sessions</button><Link to={`/admin/audit?tenant=${id}`} className="btn btn-outline-secondary">Audit log</Link></div>}
      </div></div>
      {dlg === "discount" && sub && <DiscountDialog sub={{ ...sub, tenant_name: t.display_name }} onClose={() => setDlg(null)} onDone={refresh} />}
      <ConfirmDialog open={dlg === "suspend"} title="Suspend tenant" withReason danger confirmText="Suspend" onClose={() => setDlg(null)} onConfirm={(r) => act("suspend", { reason: r })} />
      <ConfirmDialog open={dlg === "reactivate"} title="Reactivate tenant" withReason confirmText="Reactivate" onClose={() => setDlg(null)} onConfirm={(r) => act("reactivate", { reason: r })} />
      <ConfirmDialog open={dlg === "cancel"} title="Cancel tenant" message="All subscriptions are cancelled. Data stays until archived." withReason danger confirmText="Cancel tenant" onClose={() => setDlg(null)} onConfirm={(r) => act("cancel", { reason: r })} />
      <Modal open={dlg === "renew"} title="Renew subscription" onClose={() => setDlg(null)} size="sm" footer={<><button className="btn btn-light" onClick={() => setDlg(null)}>Cancel</button><button className="btn btn-primary" onClick={async () => { const r = await subAct("renew", { periods: Number(renew.periods), from_today: renew.from_today, payment: renew.pay ? { amount: renew.amount || undefined, method: renew.method, reference: renew.reference } : undefined }); toast.success(`Renewed — invoice ${r.invoice.number}`); setDlg(null); }}>Renew</button></>}><Field label="Periods"><input type="number" className="form-control" value={renew.periods} onChange={(e) => setRenew({ ...renew, periods: e.target.value })} /></Field><div className="form-check mb-2"><input className="form-check-input" type="checkbox" id="ft" checked={renew.from_today} onChange={(e) => setRenew({ ...renew, from_today: e.target.checked })} /><label htmlFor="ft" className="form-check-label">Extend from today (instead of current end date)</label></div><div className="form-check mb-2"><input className="form-check-input" type="checkbox" id="rp" checked={renew.pay} onChange={(e) => setRenew({ ...renew, pay: e.target.checked })} /><label htmlFor="rp" className="form-check-label">Record payment now</label></div>{renew.pay && <><Field label="Amount (blank = invoice total)"><input type="number" className="form-control" value={renew.amount} onChange={(e) => setRenew({ ...renew, amount: e.target.value })} /></Field><Field label="Method"><select className="form-select" value={renew.method} onChange={(e) => setRenew({ ...renew, method: e.target.value })}>{["bank_transfer", "cash", "cheque", "jazzcash", "easypaisa", "card", "other"].map((m) => <option key={m} value={m}>{titleCase(m)}</option>)}</select></Field><Field label="Reference"><input className="form-control" value={renew.reference} onChange={(e) => setRenew({ ...renew, reference: e.target.value })} /></Field></>}</Modal>
      <Modal open={dlg === "change"} title="Change plan" onClose={() => setDlg(null)} size="md" footer={<><button className="btn btn-light" onClick={() => setDlg(null)}>Cancel</button><button className="btn btn-primary" disabled={!change.plan} onClick={async () => { await subAct("change-plan", { plan: change.plan, when: change.when }); setDlg(null); }}>Apply</button></>}><Field label="New plan"><select className="form-select" value={change.plan} onChange={async (e) => { const plan = e.target.value; let preview; if (plan) preview = await api.post(`${P}/subscriptions/${sub.id}/change-plan/preview`, { plan }); setChange({ ...change, plan, preview }); }}><option value="">Select…</option>{plans.data?.filter((p) => p.id !== sub?.plan).map((p) => <option key={p.id} value={p.id}>{p.name} — {money(p.price, 0)}/{p.billing_cycle}</option>)}</select></Field><Field label="When"><select className="form-select" value={change.when} onChange={(e) => setChange({ ...change, when: e.target.value })}><option value="immediate">Immediately (pro-rata)</option><option value="period_end">At period end</option></select></Field>{change.preview && <div className="fs-13"><div>Days left: {change.preview.prorata.days_left} · credit {money(change.preview.prorata.credit)} · charge {money(change.preview.prorata.charge)} · <strong>net {money(change.preview.prorata.net)}</strong></div>{change.preview.over_limits.length > 0 && <Alert kind="danger">Over the new plan's limits: {change.preview.over_limits.map((o: any) => `${o.limit} ${o.current}/${o.allowed}`).join(", ")} — downgrade blocked until usage is reduced.</Alert>}</div>}</Modal>
      <Modal open={dlg === "trial"} title="Extend trial" onClose={() => setDlg(null)} size="sm" footer={<><button className="btn btn-light" onClick={() => setDlg(null)}>Cancel</button><button className="btn btn-primary" onClick={async () => { await subAct("extend-trial", { days: Number(trialDays) }); setDlg(null); }}>Extend</button></>}><Field label="Days"><input type="number" className="form-control" value={trialDays} onChange={(e) => setTrialDays(e.target.value)} /></Field></Modal>
      <Modal open={dlg === "payment"} title="Record payment" onClose={() => setDlg(null)} size="sm" footer={<><button className="btn btn-light" onClick={() => setDlg(null)}>Cancel</button><button className="btn btn-primary" onClick={async () => { try { await api.post(`${P}/payments`, { tenant: id, invoice: pay.invoice || null, amount: pay.amount, method: pay.method, reference: pay.reference, paid_at: pay.paid_at, note: pay.note }); toast.success("Payment recorded"); refresh(); setDlg(null); } catch (e: any) { toast.error(e.message); } }}>Record</button></>}><Field label="Invoice"><select className="form-select" value={pay.invoice} onChange={(e) => setPay({ ...pay, invoice: e.target.value })}><option value="">— unapplied —</option>{invoices.data?.results.filter((i: any) => !["paid", "void"].includes(i.status)).map((i: any) => <option key={i.id} value={i.id}>{i.number} · balance {money(i.balance)}</option>)}</select></Field><Field label="Amount"><input type="number" step="0.01" className="form-control" value={pay.amount} onChange={(e) => setPay({ ...pay, amount: e.target.value })} /></Field><Field label="Method"><select className="form-select" value={pay.method} onChange={(e) => setPay({ ...pay, method: e.target.value })}>{["bank_transfer", "cash", "cheque", "jazzcash", "easypaisa", "card", "other"].map((m) => <option key={m} value={m}>{titleCase(m)}</option>)}</select></Field><Field label="Reference"><input className="form-control" value={pay.reference} onChange={(e) => setPay({ ...pay, reference: e.target.value })} /></Field><Field label="Date"><DateInput className="form-control" value={pay.paid_at} onChange={(e) => setPay({ ...pay, paid_at: e.target.value })} /></Field></Modal>
      <Modal open={dlg === "password"} title="Reset owner password" onClose={() => setDlg(null)} size="sm" footer={<><button className="btn btn-light" onClick={() => setDlg(null)}>Cancel</button><button className="btn btn-primary" disabled={pwd.length < 8} onClick={() => act("reset-owner-password", { password: pwd })}>Reset</button></>}><Field label="Temporary password"><input type="password" className="form-control" value={pwd} onChange={(e) => setPwd(e.target.value)} /></Field></Modal>
    </>
  );
}

export function SubscriptionsPage() {
  const cols: Column<any>[] = [{ key: "tenant_name", header: "Company", render: (s) => <Link to={`/admin/tenants/${s.tenant}`}>{s.tenant_name}</Link> }, { key: "plan_name", header: "Plan", render: (s) => `${s.plan_name} v${s.plan_version}` }, { key: "start_date", header: "Start", sortKey: "start_date", render: (s) => date(s.start_date) }, { key: "end_date", header: "End", sortKey: "end_date", render: (s) => <>{date(s.end_date)}<div className={`fs-11 ${s.days_left < 0 ? "text-danger" : s.days_left < 7 ? "text-warning" : "text-muted"}`}>{s.days_left} days</div></> }, { key: "status", header: "Status", render: (s) => <StatusBadge status={s.status} /> }];
  return <ListPage title="Subscriptions" endpoint={`${P}/subscriptions`} columns={cols} rowLink={(s) => `/admin/tenants/${s.tenant}`} defaultOrdering="end_date" filters={[{ name: "status", label: "Status", type: "select", options: ["trial", "active", "past_due", "suspended", "cancelled", "expired"].map((s) => ({ value: s, label: s.replace(/_/g, " ") })) }]} />;
}

export function PlatformInvoicesPage() {
  const cols: Column<any>[] = [{ key: "number", header: "Invoice", render: (i) => <>{i.number}{i.is_credit_note && <span className="badge bg-warning-light text-warning ms-1">CN</span>}</> }, { key: "tenant_name", header: "Company", render: (i) => <Link to={`/admin/tenants/${i.tenant}`}>{i.tenant_name}</Link> }, { key: "period_start", header: "Period", render: (i) => `${date(i.period_start)} → ${date(i.period_end)}` }, { key: "due_date", header: "Due", render: (i) => date(i.due_date) }, { key: "total", header: "Total", num: true, sortKey: "total", render: (i) => money(i.total) }, { key: "balance", header: "Balance", num: true, render: (i) => money(i.balance) }, { key: "status", header: "Status", render: (i) => <StatusBadge status={i.status} /> }, { key: "pdf", header: "", render: (i) => <button className="btn btn-sm btn-link no-row-click" onClick={() => api.download(`${P}/invoices/${i.id}/pdf`)}>PDF</button> }];
  return <ListPage title="Platform invoices" endpoint={`${P}/invoices`} columns={cols} rowLink={(i) => `/admin/tenants/${i.tenant}`} defaultOrdering="-created_at" filters={[{ name: "status", label: "Status", type: "select", options: ["issued", "partial", "paid", "overdue", "void"].map((s) => ({ value: s, label: s })) }, { name: "tenant", label: "Company", type: "entity", endpoint: `${P}/tenants`, mapOption: (t) => ({ id: t.id, label: t.display_name }) }]} />;
}

export function PlatformPaymentsPage() {
  const cols: Column<any>[] = [{ key: "paid_at", header: "Date", sortKey: "paid_at", render: (p) => date(p.paid_at) }, { key: "tenant_name", header: "Company", render: (p) => <Link to={`/admin/tenants/${p.tenant}`}>{p.tenant_name}</Link> }, { key: "invoice_number", header: "Invoice" }, { key: "method", header: "Method", render: (p) => titleCase(p.method) }, { key: "reference", header: "Reference" }, { key: "amount", header: "Amount", num: true, sortKey: "amount", render: (p) => money(p.amount) }, { key: "status", header: "Status", render: (p) => <StatusBadge status={p.status} /> }, { key: "recorded_by_name", header: "By" }];
  return <ListPage title="Platform payments" endpoint={`${P}/payments`} columns={cols} defaultOrdering="-paid_at" actions={<button className="btn btn-outline-success" onClick={() => api.download(`${P}/payments/export`, "platform-payments.csv")}><i className="ti ti-file-type-csv me-1" />Export</button>} filters={[{ name: "method", label: "Method", type: "select", options: ["bank_transfer", "cash", "cheque", "jazzcash", "easypaisa", "card", "other"].map((m) => ({ value: m, label: titleCase(m) })) }, { name: "status", label: "Status", type: "select", options: ["paid", "partial", "pending", "refunded", "failed"].map((s) => ({ value: s, label: s })) }, { name: "tenant", label: "Company", type: "entity", endpoint: `${P}/tenants`, mapOption: (t) => ({ id: t.id, label: t.display_name }) }, { name: "date_from", label: "From", type: "date" }, { name: "date_to", label: "To", type: "date" }]} />;
}

export function OutstandingPage() {
  const q = useQuery({ queryKey: ["platform-outstanding"], queryFn: () => api.get(`${P}/invoices/outstanding`) });
  const buckets: Record<string, number> = {};
  for (const r of q.data || []) buckets[r.bucket] = (buckets[r.bucket] || 0) + Number(r.balance);
  return (
    <>
      <PageHeader title="Outstanding balances" crumbs={[]} />
      <div className="row g-3 mb-3">{["current", "1-30", "31-60", "61-90", "90+"].map((b) => <div className="col" key={b}><StatCard label={b} value={money(buckets[b] || 0, 0)} icon="ti-hourglass" color={b === "current" ? "info" : b === "90+" ? "danger" : "warning"} /></div>)}</div>
      <div className="card"><div className="card-body p-0"><DataTable columns={[{ key: "tenant", header: "Company", render: (r: any) => <Link to={`/admin/tenants/${r.tenant_id}`}>{r.tenant}</Link> }, { key: "number", header: "Invoice" }, { key: "due_date", header: "Due", render: (r: any) => date(r.due_date) }, { key: "days_overdue", header: "Days overdue", num: true }, { key: "bucket", header: "Bucket" }, { key: "balance", header: "Balance", num: true, render: (r: any) => money(r.balance) }]} rows={q.data} loading={q.isLoading} /></div></div>
    </>
  );
}

export function AnnouncementsPage() {
  return <CrudPage title="Announcements" crumbs={[]} endpoint={`${P}/announcements`} columns={[{ key: "title", header: "Title" }, { key: "level", header: "Level", render: (a: any) => <StatusBadge status={a.level === "danger" ? "cancelled" : a.level === "warning" ? "on_hold_credit" : "submitted"} label={a.level} /> }, { key: "audience", header: "Audience", render: (a: any) => (a.audience === "plan" ? `plan ${a.plan_code}` : a.audience) }, { key: "starts_at", header: "Window", render: (a: any) => `${datetime(a.starts_at)} → ${a.ends_at ? datetime(a.ends_at) : "…"}` }, { key: "is_active", header: "Active", render: (a: any) => (a.is_active ? "yes" : "no") }]} fields={[{ name: "title", label: "Title", required: true, col: "col-md-8" }, { name: "level", label: "Level", type: "select", options: ["info", "warning", "danger"].map((l) => ({ value: l, label: l })), default: "info", col: "col-md-4" }, { name: "body", label: "Message", type: "textarea", required: true, col: "col-md-12" }, { name: "audience", label: "Audience", type: "select", options: [{ value: "all", label: "All tenants" }, { value: "plan", label: "Tenants on a plan" }, { value: "selected", label: "Selected tenants" }], default: "all", col: "col-md-4" }, { name: "plan_code", label: "Plan code", col: "col-md-4", showIf: (v) => v.audience === "plan" }, { name: "tenants", label: "Tenant ids (JSON list)", type: "json", default: [], col: "col-md-4", showIf: (v) => v.audience === "selected" }, { name: "starts_at", label: "Starts", col: "col-md-4", hint: "ISO datetime" }, { name: "ends_at", label: "Ends", col: "col-md-4" }, { name: "dismissible", label: "Dismissible", type: "checkbox", default: true, col: "col-md-2" }, { name: "is_active", label: "Active", type: "checkbox", default: true, col: "col-md-2" }]} />;
}

export function TemplatesPage() {
  return <CrudPage title="Message templates" crumbs={[]} endpoint={`${P}/templates`} columns={[{ key: "key", header: "Key" }, { key: "channel", header: "Channel" }, { key: "subject", header: "Subject" }, { key: "body", header: "Body", render: (t: any) => <span className="text-muted fs-12">{t.body.slice(0, 80)}…</span> }, { key: "variables", header: "Variables", render: (t: any) => t.variables.map((v: string) => `{{${v}}}`).join(" ") }, { key: "is_active", header: "Active", render: (t: any) => (t.is_active ? "yes" : "no") }]} fields={[{ name: "key", label: "Key", required: true, col: "col-md-6" }, { name: "channel", label: "Channel", type: "select", options: ["email", "sms", "push", "whatsapp"].map((c) => ({ value: c, label: c })), required: true, col: "col-md-6" }, { name: "subject", label: "Subject", col: "col-md-12" }, { name: "body", label: "Body (use {{variable}})", type: "textarea", required: true, col: "col-md-12" }, { name: "variables", label: "Variables (JSON list)", type: "json", default: [], col: "col-md-8" }, { name: "is_active", label: "Active", type: "checkbox", default: true, col: "col-md-4" }]} />;
}

export function FeatureFlagsPage() {
  return <CrudPage title="Feature flags" crumbs={[]} endpoint={`${P}/feature-flags`} columns={[{ key: "key", header: "Flag" }, { key: "description", header: "Description" }, { key: "enabled", header: "Enabled", render: (f: any) => (f.enabled ? "yes" : "no") }, { key: "all_tenants", header: "Scope", render: (f: any) => (f.all_tenants ? "all tenants" : `${f.tenants.length} tenant(s)`) }]} fields={[{ name: "key", label: "Key", required: true, col: "col-md-4", placeholder: "branding" }, { name: "description", label: "Description", col: "col-md-8" }, { name: "enabled", label: "Enabled", type: "checkbox", col: "col-md-3" }, { name: "all_tenants", label: "All tenants", type: "checkbox", col: "col-md-3" }, { name: "tenants", label: "Tenant ids (JSON list)", type: "json", default: [], col: "col-md-6" }]} intro={<Alert kind="info">Flags override plan features for gradual rollout. The <code>branding</code> flag makes Settings → Branding visible for the selected tenants.</Alert>} />;
}

export function LegalPage() {
  return <CrudPage title="Legal documents" crumbs={[]} endpoint={`${P}/legal`} columns={[{ key: "kind", header: "Kind" }, { key: "version", header: "Version" }, { key: "published_at", header: "Published", render: (l: any) => datetime(l.published_at) }, { key: "is_current", header: "Current", render: (l: any) => (l.is_current ? "yes" : "") }]} fields={[{ name: "kind", label: "Kind", type: "select", options: [{ value: "terms", label: "Terms of service" }, { value: "privacy", label: "Privacy policy" }], required: true, col: "col-md-6" }, { name: "version", label: "Version", required: true, col: "col-md-6" }, { name: "body", label: "Body", type: "textarea", required: true, col: "col-md-12" }]} />;
}

export function PlatformAuditPage() {
  const cols: Column<any>[] = [{ key: "at", header: "When", render: (a) => datetime(a.at) }, { key: "actor_name", header: "Actor" }, { key: "action", header: "Action" }, { key: "tenant_name", header: "Tenant" }, { key: "model", header: "Object", render: (a) => `${a.model} ${a.object_repr}` }, { key: "ip", header: "IP" }, { key: "diff", header: "Change", render: (a) => (a.before || a.after) && <details className="fs-11"><summary className="cursor-pointer text-muted">view</summary><pre className="mb-0 bg-light p-1 rounded" style={{ maxWidth: 420, whiteSpace: "pre-wrap" }}>{JSON.stringify({ before: a.before, after: a.after }, null, 1)}</pre></details> }];
  return <ListPage title="Platform audit log" endpoint={`${P}/audit`} columns={cols} defaultOrdering="-at" searchPlaceholder="Search action…" filters={[{ name: "tenant", label: "Tenant", type: "entity", endpoint: `${P}/tenants`, mapOption: (t) => ({ id: t.id, label: t.display_name }) }]} />;
}

export function PlatformUsersPage() {
  const roles = useQuery({ queryKey: ["platform-roles"], queryFn: () => api.get(`${P}/users/roles`) });
  const rolesRender = (value: any, set: (v: any) => void) => <div className="d-flex flex-wrap gap-2">{roles.data?.map((r: any) => <div className="form-check" key={r.id}><input className="form-check-input" type="checkbox" id={`pr-${r.id}`} checked={(value || []).includes(r.id)} onChange={(e) => set(e.target.checked ? [...(value || []), r.id] : (value || []).filter((x: string) => x !== r.id))} /><label className="form-check-label" htmlFor={`pr-${r.id}`}>{r.name}</label></div>)}</div>;
  return <CrudPage title="Platform users" crumbs={[]} endpoint={`${P}/users`} columns={[{ key: "full_name", header: "User" }, { key: "email", header: "Email" }, { key: "roles", header: "Roles", render: (u: any) => u.roles.map((r: any) => r.name).join(", ") || (u.is_superuser ? "Superuser" : "—") }, { key: "mfa_enabled", header: "MFA", render: (u: any) => (u.mfa_enabled ? "on" : "off") }, { key: "last_login", header: "Last login", render: (u: any) => datetime(u.last_login) }, { key: "is_active", header: "Status", render: (u: any) => <StatusBadge status={u.is_active ? "active" : "inactive"} /> }]} fields={[{ name: "full_name", label: "Full name", required: true, col: "col-md-6" }, { name: "email", label: "Email", type: "email", required: true, col: "col-md-6" }, { name: "phone", label: "Phone", col: "col-md-4" }, { name: "password", label: "Password", type: "password", col: "col-md-4", hint: "blank = keep / random" }, { name: "is_active", label: "Active", type: "checkbox", default: true, col: "col-md-4" }, { name: "role_ids", label: "Platform roles", type: "custom", render: rolesRender, col: "col-md-12", default: [] }]} initialFor={(u: any) => ({ ...u, role_ids: u.roles.map((r: any) => r.id) })} canDelete={false} />;
}

export function HealthPage() {
  const q = useQuery({ queryKey: ["platform-health"], queryFn: () => api.get(`${P}/health`) });
  if (q.isLoading) return <Spinner />;
  const h = q.data;
  return (
    <>
      <PageHeader title="System health" crumbs={[]} />
      <div className="row g-3 mb-3"><div className="col-6 col-md-3"><StatCard label="Database size" value={h.db_size} icon="ti-database" color="primary" /></div><div className="col-6 col-md-3"><StatCard label="Queue length" value={h.queue_length ?? "n/a"} icon="ti-stack" color="info" /></div><div className="col-6 col-md-3"><StatCard label="Failed webhooks" value={h.failed_webhooks} icon="ti-webhook" color={h.failed_webhooks ? "danger" : "success"} /></div><div className="col-6 col-md-3"><StatCard label="Server time" value={datetime(h.time)} icon="ti-clock" color="secondary" /></div></div>
      <div className="card"><div className="card-header"><strong>Scheduled jobs</strong></div><div className="card-body p-0"><table className="table table-sm fs-13 mb-0"><thead className="table-light"><tr><th>Job</th><th>Enabled</th><th>Last run</th></tr></thead><tbody>{h.scheduled_jobs.map((j: any) => <tr key={j.name}><td>{j.name}</td><td>{j.enabled ? "yes" : "no"}</td><td>{datetime(j.last_run_at)}</td></tr>)}{!h.scheduled_jobs.length && <tr><td colSpan={3} className="text-muted text-center py-3">Celery beat schedule is code-defined (see settings.CELERY_BEAT_SCHEDULE)</td></tr>}</tbody></table></div></div>
    </>
  );
}

export function TicketsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["platform-tickets"], queryFn: () => api.list(`${P}/tickets`, { page_size: 100 }) });
  const [active, setActive] = useState<any>(null);
  const [reply, setReply] = useState("");
  const [status, setStatus] = useState("pending");
  return (
    <>
      <PageHeader title="Support tickets" crumbs={[]} />
      <div className="row g-3"><div className="col-md-5"><div className="card"><div className="list-group list-group-flush">{q.data?.results.map((t: any) => <a key={t.id} className={`list-group-item list-group-item-action cursor-pointer ${active?.id === t.id ? "active" : ""}`} onClick={() => setActive(t)}><div className="d-flex justify-content-between"><span className="fw-medium">{t.subject}</span><StatusBadge status={t.status} /></div><div className="fs-11 text-muted">{t.tenant_name} · {t.priority} · {datetime(t.updated_at)}</div></a>)}</div></div></div>
        <div className="col-md-7">{active && <div className="card"><div className="card-header d-flex justify-content-between"><strong>{active.subject}</strong><span className="fs-12 text-muted">{active.tenant_name} · {active.opened_by_name}</span></div><div className="card-body">{active.messages.map((m: any) => <div key={m.id} className={`mb-3 p-2 rounded ${m.is_staff_reply ? "bg-primary-light" : "bg-light"}`}><div className="fs-11 text-muted">{m.author_name} · {datetime(m.created_at)}</div><div className="fs-13">{m.body}</div></div>)}<div className="input-group"><input className="form-control" placeholder="Reply…" value={reply} onChange={(e) => setReply(e.target.value)} /><select className="form-select" style={{ maxWidth: 140 }} value={status} onChange={(e) => setStatus(e.target.value)}>{["open", "pending", "resolved", "closed"].map((s) => <option key={s} value={s}>{s}</option>)}</select><button className="btn btn-primary" disabled={!reply} onClick={async () => { await api.post(`${P}/tickets/${active.id}/reply`, { body: reply, status }); setReply(""); const fresh = await api.get(`${P}/tickets/${active.id}`); setActive(fresh); qc.invalidateQueries({ queryKey: ["platform-tickets"] }); }}>Send</button></div></div></div>}</div></div>
    </>
  );
}

export function NotificationLogPage() {
  const cols: Column<any>[] = [{ key: "sent_at", header: "Sent", render: (n) => datetime(n.sent_at) }, { key: "channel", header: "Channel" }, { key: "template_key", header: "Template" }, { key: "to", header: "To" }, { key: "status", header: "Status", render: (n) => <StatusBadge status={n.status === "sent" ? "posted" : "failed"} label={n.status} /> }, { key: "provider_response", header: "Provider response", render: (n) => <span className="text-muted fs-11">{n.provider_response?.slice(0, 80)}</span> }];
  return <ListPage title="Notification log" endpoint={`${P}/notification-log`} columns={cols} defaultOrdering="-sent_at" filters={[{ name: "channel", label: "Channel", type: "select", options: ["email", "sms", "push"].map((c) => ({ value: c, label: c })) }]} />;
}


/** Per-tenant feature switches — the plan sets the default, the platform admin can override any of them. */
function FeaturesTab({ tenant, onChanged }: { tenant: any; onChanged: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const feats = tenant.effective_features || {};
  const LABELS: Record<string, { label: string; hint: string }> = {
    branding: { label: "White-label branding", hint: "The company can replace the product name, logo, favicon and colours with their own (Settings → Branding). Their invoices always carry their own company logo regardless." },
    barcode: { label: "Barcodes & labels", hint: "Barcode scanning and label printing." },
    external_api: { label: "External API & webhooks", hint: "API keys for reps and integrations." },
    advanced_accounting: { label: "Advanced accounting", hint: "Cost centres, manual journals, full financial statements." },
    report_scheduling: { label: "Scheduled reports", hint: "Email reports on a schedule." },
    excel_export: { label: "Excel export", hint: "XLSX exports in addition to CSV." },
    rep_portal: { label: "Rep portal", hint: "Manufacturer reps can log in and place orders." },
    multi_supplier: { label: "Multiple suppliers", hint: "More than one supplier/principal." },
  };
  const set = async (key: string, value: boolean | null) => {
    setBusy(key);
    try { await api.post(`${P}/tenants/${tenant.id}/set-feature`, { key, value }); toast.success(value === null ? `${key} follows the plan again` : `${key} ${value ? "enabled" : "disabled"}`); onChanged(); }
    catch (e: any) { toast.error(e.message); } finally { setBusy(null); }
  };
  const rows = Object.entries(feats).filter(([k, v]: any) => typeof v.value === "boolean");
  return (
    <>
      <Alert kind={tenant.white_label ? "success" : "info"}>
        {tenant.white_label
          ? <>White-label is <strong>on</strong>: {tenant.display_name} can show their own name and logo inside the app.</>
          : <>White-label is <strong>off</strong>: {tenant.display_name} sees the platform branding. Switch “White-label branding” on below to let them use their own name and logo.</>}
      </Alert>
      <table className="table table-sm align-middle fs-13">
        <thead className="table-light"><tr><th>Feature</th><th style={{ width: 120 }}>Plan default</th><th style={{ width: 140 }}>This tenant</th><th style={{ width: 150 }} /></tr></thead>
        <tbody>
          {rows.map(([key, v]: any) => (
            <tr key={key} className={key === "branding" ? "table-warning" : ""}>
              <td><span className="fw-semibold">{LABELS[key]?.label || titleCase(key)}</span><div className="text-muted fs-11">{LABELS[key]?.hint || key}</div></td>
              <td>{v.from_plan === undefined ? "—" : v.from_plan ? "on" : "off"}</td>
              <td>
                <div className="form-check form-switch mb-0">
                  <input className="form-check-input" type="checkbox" checked={Boolean(v.value)} disabled={busy === key} onChange={(e) => set(key, e.target.checked)} />
                  <span className="fs-11 text-muted ms-1">{v.overridden ? "overridden" : "from plan"}</span>
                </div>
              </td>
              <td className="text-end">{v.overridden && <button className="btn btn-sm btn-outline-secondary" disabled={busy === key} onClick={() => set(key, null)}>Follow the plan</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="fs-12 text-muted">Switches here override the plan for this tenant only. Changing the plan does not clear an override — use “Follow the plan” for that.</div>
    </>
  );
}
