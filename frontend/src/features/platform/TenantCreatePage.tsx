import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { money } from "@/lib/format";
import { toast } from "@/lib/toast";
import { Field, PageHeader, SaveError, Spinner } from "@/components/ui/Bits";

const P = "/api/v1/platform";

const STEPS = [
  { key: "company", title: "Company", hint: "Who is the business", icon: "ti-building-store" },
  { key: "plan", title: "Plan", hint: "What they can use", icon: "ti-stack-2" },
  { key: "owner", title: "Owner & access", hint: "Who signs in first", icon: "ti-user-shield" },
];

const CURRENCIES = ["PKR", "USD", "AED", "SAR", "GBP", "EUR"];
const TIMEZONES = ["Asia/Karachi", "Asia/Dubai", "Asia/Riyadh", "Asia/Kolkata", "Europe/London", "UTC"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const LIMIT_LABELS: Record<string, string> = { max_users: "users", max_reps: "reps", max_warehouses: "warehouses", max_products: "products", max_orders_month: "orders/month", max_storage_mb: "storage" };
const FEATURE_LABELS: Record<string, string> = { barcode: "Barcodes", rep_portal: "Rep portal", external_api: "API & webhooks", advanced_accounting: "Full accounting", report_scheduling: "Scheduled reports", excel_export: "Excel export", multi_supplier: "Multi-supplier", branding: "White-label" };

const slugify = (v: string) => v.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40);
const randomPassword = () => {
  const words = ["Falcon", "Harbor", "Lumen", "Cobalt", "Zephyr", "Quartz", "Nimbus", "Orchid"];
  return `${words[Math.floor(Math.random() * words.length)]}-${Math.floor(1000 + Math.random() * 8999)}-${words[Math.floor(Math.random() * words.length)].toLowerCase()}`;
};
const limitText = (v: any) => (v === -1 || v === undefined ? "unlimited" : typeof v === "number" && v >= 1024 && v % 1024 === 0 ? `${v / 1024} GB` : String(v));

/**
 * Creating a company is rare and has three distinct decisions — who they are, what they may use, who gets the
 * keys — so the form is a three-step wizard with a running summary instead of one long column of inputs.
 */
export function TenantCreatePage() {
  const nav = useNavigate();
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [touchedSlug, setTouchedSlug] = useState(false);
  const plans = useQuery({ queryKey: ["plans-current"], queryFn: () => api.get(`${P}/plans`, { page_size: 50 }) });
  const settings = useQuery({ queryKey: ["platform-settings"], queryFn: () => api.get(`${P}/settings`) });
  const [f, setF] = useState<any>({
    display_name: "", legal_name: "", slug: "", email: "", phone: "", city: "", address: "",
    ntn: "", strn: "", base_currency: "PKR", fiscal_year_start_month: 7, timezone: "Asia/Karachi",
    plan: "", owner_name: "", owner_email: "", owner_phone: "", owner_password: "", send_invite: true,
    white_label: false, impersonation_allowed: true,
  });
  const set = (patch: any) => setF((prev: any) => ({ ...prev, ...patch }));
  const planRows = useMemo(() => {
    const rows = (plans.data?.results || plans.data || []) as any[];
    return rows.filter((p) => p.is_current);
  }, [plans.data]);
  const defaultCode = settings.data?.default_plan_code;
  const chosenPlan = planRows.find((p) => p.id === f.plan);

  const problems = (which: number): string[] => {
    const out: string[] = [];
    if (which === 0) {
      if (!f.display_name.trim()) out.push("Company name");
      if (!f.legal_name.trim()) out.push("Legal name");
      if (f.email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(f.email)) out.push("a valid company email");
    }
    if (which === 2) {
      if (!f.owner_name.trim()) out.push("Owner name");
      if (!f.owner_email.trim() && !f.owner_phone.trim()) out.push("owner email or phone");
      if (f.owner_email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(f.owner_email)) out.push("a valid owner email");
    }
    return out;
  };
  const next = () => {
    const missing = problems(step);
    if (missing.length) { const m = `Please fill in: ${missing.join(", ")}.`; setErr(m); return toast.error(m); }
    setErr(null); setStep(step + 1);
  };
  const create = async () => {
    const missing = [...problems(0), ...problems(2)];
    if (missing.length) { const m = `Please fill in: ${missing.join(", ")}.`; setErr(m); return toast.error(m); }
    setBusy(true); setErr(null);
    try {
      const body: any = {
        display_name: f.display_name.trim(), legal_name: f.legal_name.trim(), slug: f.slug || slugify(f.display_name),
        address: f.address, city: f.city, phone: f.phone, email: f.email,
        tax_numbers: Object.fromEntries([["NTN", f.ntn], ["STRN", f.strn]].filter(([, v]) => v)),
        base_currency: f.base_currency, fiscal_year_start_month: Number(f.fiscal_year_start_month), timezone: f.timezone,
        plan: f.plan || undefined, owner_name: f.owner_name.trim(), owner_email: f.owner_email.trim() || undefined,
        owner_phone: f.owner_phone.trim() || undefined, owner_password: f.owner_password || "", send_invite: f.send_invite,
      };
      const t = await api.post(`${P}/tenants`, body, true);
      if (f.white_label) await api.post(`${P}/tenants/${t.id}/set-feature`, { key: "branding", value: true }).catch(() => toast.warning("Company created, but white-label could not be enabled — set it on the Features tab."));
      if (!f.impersonation_allowed) await api.patch(`${P}/tenants/${t.id}`, { impersonation_allowed: false }).catch(() => {});
      toast.success(`${t.display_name} created${f.owner_password ? "" : " — invitation sent to the owner"}`);
      nav(`/admin/tenants/${t.id}`);
    } catch (e: any) { setErr(e.message); toast.error(e.message); } finally { setBusy(false); }
  };

  if (plans.isLoading) return <Spinner />;
  return (
    <>
      <PageHeader title="New company" crumbs={[{ label: "Tenants", to: "/admin/tenants" }, { label: "New" }]} />

      <div className="card mb-3"><div className="card-body py-3">
        <div className="d-flex align-items-center justify-content-between flex-wrap gap-2">
          {STEPS.map((s, i) => (
            <div key={s.key} className="d-flex align-items-center gap-2 flex-grow-1" style={{ cursor: i < step ? "pointer" : "default" }} onClick={() => i < step && setStep(i)}>
              <span className={`d-inline-flex align-items-center justify-content-center rounded-circle flex-shrink-0 ${i < step ? "bg-success text-white" : i === step ? "bg-primary text-white" : "bg-light text-muted"}`} style={{ width: 36, height: 36 }}>
                {i < step ? <i className="ti ti-check" /> : <i className={`ti ${s.icon}`} />}
              </span>
              <div className="lh-sm">
                <div className={`fs-13 fw-semibold ${i === step ? "text-dark" : "text-muted"}`}>{i + 1}. {s.title}</div>
                <div className="fs-11 text-muted">{s.hint}</div>
              </div>
              {i < STEPS.length - 1 && <div className="flex-grow-1 border-top mx-2 d-none d-md-block" style={{ borderColor: i < step ? "#03C95A" : undefined }} />}
            </div>
          ))}
        </div>
      </div></div>

      <SaveError error={err} what="company" />

      <div className="row g-3">
        <div className="col-lg-8">
          {step === 0 && (
            <div className="card"><div className="card-header"><strong>Company details</strong></div><div className="card-body">
              <div className="row">
                <Field label="Company name *" col="col-md-6" hint="shown in the app and on documents">
                  <input className="form-control form-control-lg" autoFocus value={f.display_name} placeholder="Trafco Distributors"
                    onChange={(e) => set({ display_name: e.target.value, ...(touchedSlug ? {} : { slug: slugify(e.target.value) }), ...(f.legal_name || touchedSlug ? {} : {}) })} />
                </Field>
                <Field label="Registered / legal name *" col="col-md-6" hint="as written on the NTN certificate">
                  <input className="form-control" value={f.legal_name} placeholder="Trafco Distributors (Pvt) Ltd" onChange={(e) => set({ legal_name: e.target.value })} />
                  {!f.legal_name && f.display_name && <button type="button" className="btn btn-link p-0 fs-11" onClick={() => set({ legal_name: f.display_name })}>same as company name</button>}
                </Field>
                <Field label="Identifier (slug)" col="col-md-4" hint="used in URLs, exports and the API">
                  <input className="form-control font-monospace" value={f.slug} placeholder={slugify(f.display_name) || "trafco"} onChange={(e) => { setTouchedSlug(true); set({ slug: slugify(e.target.value) }); }} />
                </Field>
                <Field label="Company email" col="col-md-4"><input type="email" className="form-control" value={f.email} placeholder="info@trafco.pk" onChange={(e) => set({ email: e.target.value })} /></Field>
                <Field label="Phone" col="col-md-4"><input className="form-control" value={f.phone} placeholder="+92 42 1234567" onChange={(e) => set({ phone: e.target.value })} /></Field>
                <Field label="City" col="col-md-4"><input className="form-control" value={f.city} placeholder="Lahore" onChange={(e) => set({ city: e.target.value })} /></Field>
                <Field label="Address" col="col-md-8"><input className="form-control" value={f.address} placeholder="Plot 12, Industrial Area" onChange={(e) => set({ address: e.target.value })} /></Field>
                <Field label="NTN" col="col-md-3"><input className="form-control" value={f.ntn} placeholder="1234567-8" onChange={(e) => set({ ntn: e.target.value })} /></Field>
                <Field label="Sales tax no (STRN)" col="col-md-3"><input className="form-control" value={f.strn} placeholder="03-01-2345-678-90" onChange={(e) => set({ strn: e.target.value })} /></Field>
              </div>
              <div className="border-top pt-3 mt-1">
                <div className="fs-13 fw-semibold mb-2">Regional settings</div>
                <div className="row">
                  <Field label="Currency" col="col-md-3"><select className="form-select" value={f.base_currency} onChange={(e) => set({ base_currency: e.target.value })}>{(settings.data?.currencies || CURRENCIES).map((c: string) => <option key={c} value={c}>{c}</option>)}</select></Field>
                  <Field label="Financial year starts" col="col-md-4"><select className="form-select" value={f.fiscal_year_start_month} onChange={(e) => set({ fiscal_year_start_month: Number(e.target.value) })}>{MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}</select></Field>
                  <Field label="Timezone" col="col-md-5"><select className="form-select" value={f.timezone} onChange={(e) => set({ timezone: e.target.value })}>{TIMEZONES.map((t) => <option key={t} value={t}>{t}</option>)}</select></Field>
                </div>
              </div>
            </div></div>
          )}

          {step === 1 && (
            <div className="card"><div className="card-header d-flex justify-content-between align-items-center"><strong>Choose a plan</strong><span className="fs-12 text-muted">the company starts on trial — you can change this any time</span></div><div className="card-body">
              <div className="row g-3">
                {planRows.map((p) => {
                  const on = f.plan === p.id;
                  const feats = Object.entries(p.features || {}).filter(([, v]) => v === true).map(([k]) => FEATURE_LABELS[k] || k);
                  return (
                    <div className="col-md-6" key={p.id}>
                      <div role="button" onClick={() => set({ plan: p.id })} className={`border rounded p-3 h-100 position-relative ${on ? "border-primary shadow-sm" : ""}`} style={{ borderWidth: on ? 2 : 1, background: on ? "rgba(242,101,34,.04)" : undefined }}>
                        {p.code === defaultCode && <span className="badge bg-primary-light text-primary position-absolute" style={{ top: 10, right: 10 }}>Recommended</span>}
                        <div className="d-flex align-items-center gap-2 mb-1">
                          <input type="radio" className="form-check-input mt-0" checked={on} onChange={() => set({ plan: p.id })} />
                          <span className="fw-semibold">{p.name}</span>
                        </div>
                        <div className="fs-20 fw-bold">{Number(p.price) ? money(p.price, 0) : "Custom"}<span className="fs-12 text-muted fw-normal">{Number(p.price) ? ` / ${p.billing_cycle}` : ""}</span></div>
                        <div className="fs-12 text-muted mb-2">{p.trial_days} day trial{p.description ? ` · ${p.description}` : ""}</div>
                        <div className="fs-12">{Object.entries(LIMIT_LABELS).filter(([k]) => p.limits?.[k] !== undefined).slice(0, 4).map(([k, label]) => <div key={k}><i className="ti ti-check text-success me-1" />{limitText(p.limits[k])} {label}</div>)}</div>
                        <div className="d-flex flex-wrap gap-1 mt-2">{feats.map((x) => <span key={x} className="badge bg-light text-dark fs-11">{x}</span>)}</div>
                      </div>
                    </div>
                  );
                })}
                <div className="col-md-6">
                  <div role="button" onClick={() => set({ plan: "" })} className={`border rounded p-3 h-100 ${!f.plan ? "border-primary shadow-sm" : ""}`} style={{ borderWidth: !f.plan ? 2 : 1, borderStyle: "dashed" }}>
                    <div className="d-flex align-items-center gap-2 mb-1"><input type="radio" className="form-check-input mt-0" checked={!f.plan} onChange={() => set({ plan: "" })} /><span className="fw-semibold">No plan yet</span></div>
                    <div className="fs-12 text-muted">Create the company now and assign a subscription later from its Subscription tab.</div>
                  </div>
                </div>
              </div>
            </div></div>
          )}

          {step === 2 && (
            <div className="card"><div className="card-header"><strong>Owner account</strong></div><div className="card-body">
              <div className="row">
                <Field label="Owner name *" col="col-md-4"><input className="form-control" autoFocus value={f.owner_name} placeholder="Kashif Malik" onChange={(e) => set({ owner_name: e.target.value })} /></Field>
                <Field label="Owner email" col="col-md-4" hint="email or phone is required"><input type="email" className="form-control" value={f.owner_email} placeholder="owner@trafco.pk" onChange={(e) => set({ owner_email: e.target.value })} /></Field>
                <Field label="Owner phone" col="col-md-4"><input className="form-control" value={f.owner_phone} placeholder="+92 300 1234567" onChange={(e) => set({ owner_phone: e.target.value })} /></Field>
                <Field label="Owner password" col="col-md-6" hint="leave blank to send an invitation instead">
                  <div className="input-group">
                    <input className="form-control font-monospace" value={f.owner_password} placeholder="(invitation)" onChange={(e) => set({ owner_password: e.target.value })} />
                    <button type="button" className="btn btn-outline-secondary" title="Generate" onClick={() => set({ owner_password: randomPassword() })}><i className="ti ti-refresh" /></button>
                    {f.owner_password && <button type="button" className="btn btn-outline-secondary" title="Copy" onClick={() => { navigator.clipboard?.writeText(f.owner_password); toast.success("Password copied"); }}><i className="ti ti-copy" /></button>}
                  </div>
                </Field>
                <div className="col-md-6 mb-3 d-flex align-items-end"><div className="form-check form-switch"><input className="form-check-input" type="checkbox" id="inv" checked={f.send_invite} onChange={(e) => set({ send_invite: e.target.checked })} /><label htmlFor="inv" className="form-check-label fs-13">Send the welcome email / SMS now</label></div></div>
              </div>
              <div className="border-top pt-3">
                <div className="fs-13 fw-semibold mb-2">Access options</div>
                <div className="form-check form-switch mb-2"><input className="form-check-input" type="checkbox" id="wl" checked={f.white_label} onChange={(e) => set({ white_label: e.target.checked })} /><label htmlFor="wl" className="form-check-label fs-13">White-label branding <span className="text-muted fs-11">— the company can use its own name, logo and colours inside the app</span></label></div>
                <div className="form-check form-switch"><input className="form-check-input" type="checkbox" id="imp" checked={f.impersonation_allowed} onChange={(e) => set({ impersonation_allowed: e.target.checked })} /><label htmlFor="imp" className="form-check-label fs-13">Allow “Log in as owner” for support <span className="text-muted fs-11">— every session is logged</span></label></div>
              </div>
            </div></div>
          )}
        </div>

        <div className="col-lg-4">
          <div className="card position-sticky" style={{ top: 80 }}>
            <div className="card-header"><strong>Summary</strong></div>
            <div className="card-body fs-13">
              <div className="d-flex align-items-center gap-2 mb-3">
                <span className="d-inline-flex align-items-center justify-content-center rounded bg-primary text-white fw-bold" style={{ width: 44, height: 44 }}>{(f.display_name || "?").slice(0, 1).toUpperCase()}</span>
                <div><div className="fw-semibold">{f.display_name || "New company"}</div><div className="text-muted fs-11 font-monospace">{f.slug || slugify(f.display_name) || "identifier"}</div></div>
              </div>
              <dl className="row mb-0 fs-12">
                <dt className="col-5 text-muted fw-normal">Legal name</dt><dd className="col-7">{f.legal_name || "—"}</dd>
                <dt className="col-5 text-muted fw-normal">Contact</dt><dd className="col-7">{f.email || f.phone || "—"}{f.city ? ` · ${f.city}` : ""}</dd>
                <dt className="col-5 text-muted fw-normal">Currency / FY</dt><dd className="col-7">{f.base_currency} · {MONTHS[(f.fiscal_year_start_month || 1) - 1]}</dd>
                <dt className="col-5 text-muted fw-normal">Plan</dt><dd className="col-7">{chosenPlan ? `${chosenPlan.name} · ${Number(chosenPlan.price) ? money(chosenPlan.price, 0) + "/" + chosenPlan.billing_cycle : "custom"}` : "assign later"}</dd>
                <dt className="col-5 text-muted fw-normal">Trial</dt><dd className="col-7">{chosenPlan ? `${chosenPlan.trial_days} days` : "—"}</dd>
                <dt className="col-5 text-muted fw-normal">Owner</dt><dd className="col-7">{f.owner_name || "—"}<div className="text-muted">{f.owner_email || f.owner_phone}</div></dd>
                <dt className="col-5 text-muted fw-normal">First login</dt><dd className="col-7">{f.owner_password ? "temporary password" : "invitation link"}</dd>
                <dt className="col-5 text-muted fw-normal">White-label</dt><dd className="col-7">{f.white_label ? "enabled" : "off (platform branding)"}</dd>
              </dl>
            </div>
            <div className="card-footer d-flex gap-2">
              <button className="btn btn-light" onClick={() => (step === 0 ? nav(-1) : setStep(step - 1))}>{step === 0 ? "Cancel" : "Back"}</button>
              {step < STEPS.length - 1
                ? <button className="btn btn-primary ms-auto" onClick={next}>Continue<i className="ti ti-arrow-right ms-1" /></button>
                : <button className="btn btn-primary ms-auto" disabled={busy} onClick={create}><i className="ti ti-building-plus me-1" />{busy ? "Creating…" : "Create company"}</button>}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
