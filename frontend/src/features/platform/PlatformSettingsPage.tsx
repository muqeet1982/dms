import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { toast } from "@/lib/toast";
import { Alert, Field, PageHeader, Spinner, Tabs } from "@/components/ui/Bits";

const P = "/api/v1/platform";

type Group = { key: string; label: string; hint?: string; fields: F[] };
type Opt = string | { value: string; label: string };
type F = { name: string; label: string; type?: "text" | "number" | "email" | "password" | "checkbox" | "textarea" | "list" | "color" | "select"; col?: string; hint?: string; options?: Opt[]; showIf?: (values: Record<string, any>) => boolean };

/** Project-wide settings (everything here is about the DMS product/website itself, never a single tenant's data). */
const GROUPS: Group[] = [
  {
    key: "branding", label: "Branding", hint: "Shown on the login page, the top bar, the browser tab and public pages.",
    fields: [
      { name: "site_name", label: "Site name", col: "col-md-3" }, { name: "site_tagline", label: "Tagline", col: "col-md-4" }, { name: "company_name", label: "Legal company name", col: "col-md-3" }, { name: "primary_color", label: "Primary colour", type: "color", col: "col-md-2" },
      { name: "login_headline", label: "Login page headline", col: "col-md-6" }, { name: "login_subtext", label: "Login page subtext", col: "col-md-6" },
      { name: "footer_text", label: "Footer text", col: "col-md-6" }, { name: "announcement_banner", label: "Site-wide banner (blank = none)", col: "col-md-6" },
    ],
  },
  {
    key: "contact", label: "Contact & legal", fields: [
      { name: "support_email", label: "Support email", type: "email", col: "col-md-3" }, { name: "support_phone", label: "Support phone", col: "col-md-3" }, { name: "support_whatsapp", label: "WhatsApp", col: "col-md-3" }, { name: "sales_email", label: "Sales email", type: "email", col: "col-md-3" },
      { name: "company_address", label: "Company address", type: "textarea", col: "col-md-6" }, { name: "meta_description", label: "Meta description (SEO)", type: "textarea", col: "col-md-6" },
      { name: "terms_url", label: "Terms URL", col: "col-md-3" }, { name: "privacy_url", label: "Privacy URL", col: "col-md-3" }, { name: "public_base_url", label: "Public base URL", col: "col-md-6", hint: "used in QR codes, emails and app links" },
    ],
  },
  {
    key: "tenants", label: "New tenants", hint: "Defaults applied when a company signs up or is created here.",
    fields: [
      { name: "default_plan_code", label: "Default plan code", col: "col-md-3" }, { name: "trial_days", label: "Trial days", type: "number", col: "col-md-2" }, { name: "default_fiscal_year_start_month", label: "FY start month", type: "number", col: "col-md-2" }, { name: "default_timezone", label: "Default timezone", col: "col-md-3" },
      { name: "currencies", label: "Currencies (one per line)", type: "list", col: "col-md-2" },
      { name: "signup_enabled", label: "Public self sign-up", type: "checkbox", col: "col-md-3" }, { name: "tenant_approval_required", label: "New tenants need approval", type: "checkbox", col: "col-md-3" },
    ],
  },
  {
    key: "maps", label: "Maps",
    hint: "Which tiles the working-area and store screens draw on. Plain OpenStreetMap shows Pakistani names in Urdu; Esri shows the same roads with English labels.",
    fields: [
      { name: "map_tiles", label: "Map style", type: "select", col: "col-md-4",
        options: [
          { value: "osm", label: "OpenStreetMap — local language (no key)" },
          { value: "esri_en", label: "Esri World Street Map — English labels (no key)" },
          { value: "maptiler", label: "MapTiler — your own style (free key)" },
          { value: "custom", label: "Custom tile URL" },
        ] },
      { name: "map_tiles_key", label: "MapTiler key", col: "col-md-4", hint: "only for MapTiler", showIf: (v: any) => v.map_tiles === "maptiler" },
      { name: "map_tiles_style", label: "MapTiler style id", col: "col-md-2", hint: "e.g. streets-v2", showIf: (v: any) => v.map_tiles === "maptiler" },
      { name: "map_tiles_url", label: "Tile URL template", col: "col-md-8", hint: "with {z}/{x}/{y}", showIf: (v: any) => v.map_tiles === "custom" },
      { name: "map_attribution", label: "Attribution", col: "col-md-4", hint: "shown in the map corner — required by most providers", showIf: (v: any) => v.map_tiles === "custom" },
    ],
  },
  {
    key: "providers", label: "Messaging", hint: "Microsoft 365 is configured here; SMTP and SMS credentials still live in the server .env. The From address must be a real mailbox the app registration may send as.",
    fields: [
      { name: "email_provider", label: "Email provider", type: "select", options: [{ value: "smtp", label: "SMTP" }, { value: "graph", label: "Microsoft 365 (Graph)" }, { value: "ses", label: "Amazon SES" }, { value: "console", label: "Console (nothing is sent)" }], col: "col-md-3" }, { name: "email_from_name", label: "From name", col: "col-md-3" }, { name: "email_from_address", label: "From address", type: "email", col: "col-md-3" },
      { name: "graph_tenant_id", label: "Directory (tenant) ID", col: "col-md-4", hint: "Azure → your app registration → Overview", showIf: (v: any) => v.email_provider === "graph" },
      { name: "graph_client_id", label: "Application (client) ID", col: "col-md-4", hint: "the same Overview page", showIf: (v: any) => v.email_provider === "graph" },
      { name: "graph_client_secret", label: "Client secret", type: "password", col: "col-md-4", hint: "the Value, not the Secret ID — it is shown once in Azure and only its last four appear here afterwards", showIf: (v: any) => v.email_provider === "graph" },
      { name: "sms_provider", label: "SMS provider", type: "select", options: ["console", "twilio", "jazz", "telenor"], col: "col-md-2" }, { name: "sms_sender_id", label: "SMS sender ID", col: "col-md-2" }, { name: "push_provider", label: "Push provider", type: "select", options: ["fcm", "none"], col: "col-md-2" },
    ],
  },
  {
    key: "security", label: "Security", fields: [
      { name: "login_lockout_attempts", label: "Failed logins before lockout", type: "number", col: "col-md-3" }, { name: "login_lockout_minutes", label: "Lockout minutes", type: "number", col: "col-md-2" }, { name: "password_min_length", label: "Min password length", type: "number", col: "col-md-2" },
      { name: "otp_ttl_minutes", label: "OTP valid (minutes)", type: "number", col: "col-md-2" }, { name: "qr_login_ttl_seconds", label: "QR login valid (seconds)", type: "number", col: "col-md-3" }, { name: "impersonation_minutes", label: "\"Log in as owner\" minutes", type: "number", col: "col-md-3" },
      { name: "session_idle_days", label: "Session lifetime (days)", type: "number", col: "col-md-2" }, { name: "force_2fa_platform_admins", label: "Require 2FA for platform admins", type: "checkbox", col: "col-md-4" },
      { name: "allowed_admin_ips", label: "Restrict /admin to these IPs (one per line, blank = any)", type: "list", col: "col-md-6" },
    ],
  },
  {
    key: "ops", label: "Operations", fields: [
      { name: "maintenance_mode", label: "Maintenance mode", type: "checkbox", col: "col-md-3" }, { name: "maintenance_message", label: "Maintenance message", col: "col-md-9" },
      { name: "backup_schedule", label: "Backup schedule (cron)", col: "col-md-3" }, { name: "backup_retention_days", label: "Backup retention (days)", type: "number", col: "col-md-2" },
      { name: "max_upload_mb", label: "Max upload (MB)", type: "number", col: "col-md-2" }, { name: "api_rate_limit_per_min", label: "API rate limit / min", type: "number", col: "col-md-2" },
    ],
  },
];

const ASSETS: { kind: string; key: string; label: string; hint: string }[] = [
  { kind: "logo", key: "logo_url", label: "Logo", hint: "Top bar and login page · PNG/SVG, transparent, ~200×48" },
  { kind: "logo_dark", key: "logo_dark_url", label: "Logo (dark background)", hint: "optional" },
  { kind: "favicon", key: "favicon_url", label: "Favicon", hint: "Browser tab · 32×32 or 64×64 PNG/ICO" },
  { kind: "login_image", key: "login_image_url", label: "Login illustration", hint: "optional, shown on the left of the login page" },
];

export function PlatformSettingsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["platform-settings"], queryFn: () => api.get(`${P}/settings`) });
  const [tab, setTab] = useState("branding");
  const [values, setValues] = useState<Record<string, any> | null>(null);
  const [saving, setSaving] = useState(false);
  // Settings can be filled in perfectly and still not deliver — a secret copied from the wrong box, consent
  // never granted, a From address that is not a mailbox. Only an actual send finds those out.
  const [testing, setTesting] = useState(false);
  const [testTo, setTestTo] = useState("");
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);
  const files = useRef<Record<string, HTMLInputElement | null>>({});
  useEffect(() => { if (q.data && !values) setValues(q.data); }, [q.data]); // eslint-disable-line react-hooks/exhaustive-deps
  if (q.isLoading || !values) return <Spinner />;
  const set = (k: string, v: any) => setValues({ ...values, [k]: v });
  const save = async () => {
    setSaving(true);
    try { const r = await api.patch(`${P}/settings`, values); setValues(r); qc.invalidateQueries({ queryKey: ["branding"] }); toast.success("Settings saved"); }
    catch (e: any) { toast.error(e.message); } finally { setSaving(false); }
  };
  const upload = async (kind: string, file: File) => {
    const fd = new FormData(); fd.append("kind", kind); fd.append("file", file);
    try { const r = await api.upload(`${P}/settings/asset`, fd); setValues({ ...values, ...r }); qc.invalidateQueries({ queryKey: ["branding"] }); toast.success(`${kind} updated`); }
    catch (e: any) { toast.error(e.message); }
  };
  const sendTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const r = await api.post(`${P}/settings/email-test`, { to: testTo });
      setTestResult({ ok: true, text: r.detail });
    } catch (e: any) {
      setTestResult({ ok: false, text: e.message });
    } finally {
      setTesting(false);
    }
  };
  const group = GROUPS.find((g) => g.key === tab)!;
  return (
    <>
      <PageHeader title="Platform settings" crumbs={[]} actions={<button className="btn btn-primary" disabled={saving} onClick={save}><i className="ti ti-device-floppy me-1" />{saving ? "Saving…" : "Save changes"}</button>} />
      <Tabs tabs={GROUPS.map((g) => ({ key: g.key, label: g.label }))} active={tab} onChange={setTab} />
      <div className="card mt-3"><div className="card-body">
        {group.hint && <div className="fs-13 text-muted mb-3">{group.hint}</div>}
        <div className="row">
          {group.fields.filter((f) => !f.showIf || f.showIf(values)).map((f) => (
            <Field key={f.name} label={f.label} col={f.col || "col-md-4"} hint={f.hint}>
              {f.type === "checkbox" ? (
                <div className="form-check form-switch mt-1"><input className="form-check-input" type="checkbox" checked={Boolean(values[f.name])} onChange={(e) => set(f.name, e.target.checked)} /></div>
              ) : f.type === "textarea" ? (
                <textarea className="form-control" rows={2} value={values[f.name] ?? ""} onChange={(e) => set(f.name, e.target.value)} />
              ) : f.type === "list" ? (
                <textarea className="form-control font-monospace fs-12" rows={3} value={Array.isArray(values[f.name]) ? values[f.name].join("\n") : values[f.name] ?? ""} onChange={(e) => set(f.name, e.target.value.split("\n").map((x) => x.trim()).filter(Boolean))} />
              ) : f.type === "select" ? (
                <select className="form-select" value={values[f.name] ?? ""} onChange={(e) => set(f.name, e.target.value)}>{(f.options || []).map((o) => { const v = typeof o === "string" ? o : o.value; const l = typeof o === "string" ? o : o.label; return <option key={v} value={v}>{l}</option>; })}</select>
              ) : f.type === "color" ? (
                <div className="input-group"><input type="color" className="form-control form-control-color" value={values[f.name] || "#F26522"} onChange={(e) => set(f.name, e.target.value)} /><input className="form-control" value={values[f.name] ?? ""} onChange={(e) => set(f.name, e.target.value)} /></div>
              ) : (
                <input type={f.type === "number" ? "number" : f.type === "email" ? "email" : f.type === "password" ? "password" : "text"} className="form-control" value={values[f.name] ?? ""} onChange={(e) => set(f.name, f.type === "number" ? Number(e.target.value) : e.target.value)} />
              )}
            </Field>
          ))}
        {group.key === "providers" && (
          <div className="border-top mt-3 pt-3">
            <h6 className="fs-13 mb-1">Send a test message</h6>
            <p className="fs-12 text-muted">Save first, then send one real email with the settings as they stand. Anything wrong — a secret from the wrong box, consent never granted, a From address that is not a mailbox — only shows up when something is actually sent.</p>
            <div className="d-flex gap-2 align-items-start flex-wrap">
              <input className="form-control" style={{ maxWidth: 320 }} placeholder="send it to…" value={testTo} onChange={(e) => setTestTo(e.target.value)} />
              <button className="btn btn-outline-primary" disabled={testing || !testTo.trim()} onClick={sendTest}>
                <i className="ti ti-send me-1" />{testing ? "Sending…" : "Send test"}
              </button>
            </div>
            {testResult && <div className={`alert ${testResult.ok ? "alert-success" : "alert-danger"} mt-2 mb-0 fs-13`}>{testResult.text}</div>}
          </div>
        )}
        </div>

        {tab === "branding" && (
          <div className="row g-3 mt-1">
            {ASSETS.map((a) => (
              <div className="col-md-3" key={a.kind}>
                <div className="border rounded p-3 h-100 text-center">
                  <div className="fw-semibold fs-13 mb-2">{a.label}</div>
                  <div className="d-flex align-items-center justify-content-center bg-light rounded mb-2" style={{ height: 64 }}>
                    {values[a.key] ? <img src={values[a.key]} alt={a.label} style={{ maxHeight: 56, maxWidth: "100%" }} /> : <span className="text-muted fs-12">none</span>}
                  </div>
                  <input ref={(el) => (files.current[a.kind] = el)} type="file" accept="image/png,image/jpeg,image/svg+xml,image/webp,image/x-icon" className="d-none" onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(a.kind, f); }} />
                  <div className="d-flex gap-1 justify-content-center">
                    <button className="btn btn-sm btn-outline-primary" onClick={() => files.current[a.kind]?.click()}><i className="ti ti-upload me-1" />Upload</button>
                    {values[a.key] && <button className="btn btn-sm btn-outline-secondary" onClick={() => set(a.key, "")} title="Clear (then Save changes)"><i className="ti ti-x" /></button>}
                  </div>
                  <div className="fs-11 text-muted mt-2">{a.hint}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div></div>

      <div className="d-flex justify-content-end mt-3"><button className="btn btn-primary" disabled={saving} onClick={save}><i className="ti ti-device-floppy me-1" />{saving ? "Saving…" : "Save changes"}</button></div>
      <Alert kind="info">These settings apply to the whole product (login page, emails, QR codes, security limits). Company-specific settings — logo on invoices, taxes, warehouses — live inside each tenant under Settings.</Alert>
    </>
  );
}
