import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useAuth } from "@/lib/auth";
import { datetime } from "@/lib/format";
import { useDoc, useOptions } from "@/lib/hooks";
import { pageRights, usePage, usePerms } from "@/lib/perms";
import { toast } from "@/lib/toast";
import { Alert, Field, PageHeader, Spinner, StatusBadge, Tabs } from "@/components/ui/Bits";
import { Column } from "@/components/ui/DataTable";
import { EntitySelect } from "@/components/ui/EntitySelect";
import { ListPage } from "@/components/ui/ListPage";
import { ConfirmDialog, Modal } from "@/components/ui/Modal";
import { PasswordFields, passwordOk, passwordProblems } from "@/components/ui/PasswordField";
import { ResourceForm } from "@/components/ui/ResourceForm";
import { PermissionMatrix } from "./PermissionMatrix";

export function UsersPage() {
  const page = usePage("users");
  const cols: Column<any>[] = [{ key: "full_name", header: "User", render: (u) => <><span className="fw-semibold text-dark">{u.full_name}</span>{u.is_owner && <span className="badge bg-primary ms-2">Owner</span>}<div className="text-muted fs-11">{u.email} {u.phone}</div></> }, { key: "role_names", header: "Roles", render: (u) => u.role_names.join(", ") || "—" }, { key: "user_type", header: "Access", render: (u) => <>{u.supplier_rep ? <span className="badge bg-warning-light text-warning border border-warning fs-11"><i className="ti ti-device-mobile me-1" />Rep app</span> : <span className="badge bg-secondary-light text-secondary border border-secondary fs-11">Office only</span>}{u.rep_name && <div className="text-muted fs-11">{u.rep_name}</div>}</> }, { key: "warehouses", header: "Warehouse scope", render: (u) => (u.warehouses.length ? `${u.warehouses.length} warehouse(s)` : "all") }, { key: "last_login", header: "Last login", render: (u) => datetime(u.last_login) }, { key: "status", header: "Status", render: (u) => <StatusBadge status={u.status} /> }];
  return <ListPage title="Users" crumbs={[{ label: "Users Management" }, { label: "Users" }]} endpoint="/api/v1/users" columns={cols} rowLink={(u) => `/app/users/${u.id}`} page="users" createTo="/app/users/new" createLabel="Add user" filters={[{ name: "status", label: "Status", type: "select", options: ["active", "suspended", "invited"].map((s) => ({ value: s, label: s })) }, { name: "user__user_type", label: "Type", type: "select", options: [{ value: "TENANT", label: "Staff" }, { value: "EXTERNAL", label: "Rep" }] }]} />;
}

export function UserFormPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { isOwner } = usePerms();
  const page = usePage("users");
  const activityPage = usePage("activity");
  const me = useAuth((s) => s.me);
  const q = useDoc("/api/v1/users", id);
  // Both kinds of role now: the API stopped refusing external ones, and a rep's roles are edited on this screen.
  const roles = useOptions("/api/v1/roles");
  const warehouses = useOptions("/api/v1/settings/warehouses");
  const [form, setForm] = useState<any>(null);
  const [dlg, setDlg] = useState<null | "password" | "deactivate" | "transfer">(null);
  const [pwd, setPwd] = useState("");
  const [pwd2, setPwd2] = useState("");
  const [repError, setRepError] = useState<string | null>(null);
  const [roleError, setRoleError] = useState<string | null>(null);
  const access = useQuery<any>({ queryKey: ["user-access", id], queryFn: () => api.get(`/api/v1/users/${id}/access`), enabled: Boolean(id) && page.view });
  const preview = usePreviewAccess(form?.roles || [], Boolean(form));
  if (id && q.isLoading) return <Spinner />;
  // Role ids arrive as strings on the membership but as numbers in the role list, so every comparison is on String().
  if (!form) { setForm(q.data ? { full_name: q.data.full_name, email: q.data.email || "", phone: q.data.phone || "", roles: (q.data.roles || []).map(String), warehouses: q.data.warehouses, status: q.data.status, login_surface: q.data.login_surface || "both", rep_mode: q.data.supplier_rep ? "keep" : "existing", rep_id: null, rep_supplier: null } : { full_name: "", email: "", phone: "", roles: [], warehouses: [], password: "", password_confirm: "", send_invite: false, login_surface: "both", rep_mode: "existing", rep_id: null, rep_supplier: null }); return <Spinner />; }
  const u = q.data;
  const roleRows: any[] = roles.data || [];
  // `needs_rep` is the server's own reading of the selection, so the rep control appears on exactly the
  // condition the server will enforce. Until the answer is in, nothing is claimed about the selection —
  // claiming it anyway warned a rep user that their rep was about to be unlinked on every page load.
  const hasField = Boolean(preview.data?.needs_rep);
  const previewReady = Boolean(preview.data);
  const fieldRoleNames: string[] = (preview.data?.roles || []).filter((r: any) => r.type === "external").map((r: any) => r.name);
  const keepingRep = form.rep_mode === "keep" && Boolean(u?.supplier_rep);
  const repChosen = keepingRep || (form.rep_mode === "existing" && Boolean(form.rep_id)) || (form.rep_mode === "new" && Boolean(form.rep_supplier));
  const willUnlinkRep = previewReady && Boolean(u?.supplier_rep) && !hasField;
  const save = async () => {
    setRepError(null);
    if (!form.full_name?.trim()) return toast.error("Please fill in: Full name.");
    if (!id) {
      if (!form.email && !form.phone) return toast.error("Give an email or a mobile number — the user signs in with one of them.");
      if (!form.password) return toast.error("Set a password for the new user.");
      const problems = passwordProblems(form.password);
      if (problems.length) return toast.error(`The password needs ${problems.join(", ")}.`);
      if (form.password !== form.password_confirm) return toast.error("The two passwords do not match.");
    }
    // A login with no role can sign in and open nothing, which reads as a broken account rather than as a
    // missing right — so the role is part of creating the user, not a later step.
    if (page.can("manage_access") && !(form.roles || []).length) {
      setRoleError(id ? "Pick at least one role before saving — a login with no role opens nothing." : "Pick at least one role — a login with no role can sign in but open nothing.");
      return toast.error("Give the user at least one role.");
    }
    if (page.can("manage_access") && hasField && !repChosen) { setRepError("Pick the rep this login belongs to, or the supplier they sell for, before saving."); return toast.error("A field role needs a rep record."); }
    try {
      const body: any = { full_name: form.full_name, email: form.email || null, phone: form.phone || null };
      if (page.can("manage_access")) {
        body.roles = form.roles;
        body.warehouses = form.warehouses;
        body.login_surface = form.login_surface || "both";
        // Only while the server says a rep is needed. `rep_for_supplier` is harmlessly ignored without a field
        // role, but `supplier_rep` is unique on the membership and its uniqueness is checked during field
        // validation — before the view can ignore it — so a pick left behind by an unticked field role would
        // fail an unrelated office-only save with "tenant membership with this supplier rep already exists".
        if (hasField && form.rep_mode === "existing" && form.rep_id) body.supplier_rep = form.rep_id;
        if (hasField && form.rep_mode === "new" && form.rep_supplier) body.rep_for_supplier = form.rep_supplier;
      }
      if (!id) { body.password = form.password || undefined; body.send_invite = form.send_invite; }
      const r = id ? await api.patch(`/api/v1/users/${id}`, body) : await api.post("/api/v1/users", body);
      toast.success(id ? "User updated" : "User created");
      qc.invalidateQueries({ queryKey: ["list", "/api/v1/users"] });
      qc.invalidateQueries({ queryKey: ["doc", "/api/v1/users", r.id] });
      // the saved access answer is now stale; re-reading it is what turns the preview back into a fact
      qc.invalidateQueries({ queryKey: ["user-access", r.id] });
      qc.invalidateQueries({ queryKey: ["options", "/api/v1/supplier-reps"] });
      if (id) { setForm({ ...form, roles: (r.roles || []).map(String), rep_mode: r.supplier_rep ? "keep" : "existing", rep_id: null, rep_supplier: null }); return; }
      nav(`/app/users/${r.id}`);
    } catch (e: any) {
      // the server writes a full sentence for the first one, and it says more than "could not save" ever would;
      // the second arrives as a model-level uniqueness message ("tenant membership with this supplier rep
      // already exists") because the rep picker can offer a rep that already backs somebody else's login
      if (e?.problem?.type === "rep_required") setRepError(e.problem.detail || e.message);
      else if (e?.problem?.errors?.supplier_rep) setRepError("That rep record already backs another login. Pick a rep with no login yet, or create a new one under the supplier.");
      toast.error(e.message);
    }
  };
  const act = async (a: string, body: any = {}) => { try { await api.post(`/api/v1/users/${id}/${a}`, body); toast.success("Done"); qc.invalidateQueries({ queryKey: ["doc", "/api/v1/users", id] }); } catch (e: any) { toast.error(e.message); } setDlg(null); };
  return (
    <>
      <PageHeader title={id ? u.full_name : "Add user"} badge={u && <StatusBadge status={u.status} />} crumbs={[{ label: "Users", to: "/app/users" }, { label: id ? "Edit user" : "Add user" }]}
        actions={id && <>{me?.is_owner && !u.is_owner && u.status === "active" && <button className="btn btn-outline-primary" onClick={async () => {
          try { await api.post(`/api/v1/users/${id}/login-as`, {}); toast.success(`Now viewing as ${u.full_name}`); window.location.assign("/app"); }
          catch (e: any) { toast.error(e.message); }
        }}><i className="ti ti-login-2 me-1" />Log in as this user</button>}{page.can("reset_password") && <button className="btn btn-outline-secondary" onClick={() => setDlg("password")}>Reset password</button>}{u.status === "invited" && <button className="btn btn-outline-secondary" onClick={() => act("resend-invite")}>Resend invite</button>}{page.edit && <button className="btn btn-outline-secondary" onClick={() => act("revoke-sessions")}>Sign out everywhere</button>}{!u.is_owner && (u.status === "active" ? page.delete && <button className="btn btn-outline-danger" onClick={() => setDlg("deactivate")}>Deactivate</button> : page.edit && <button className="btn btn-outline-success" onClick={() => act("activate")}>Activate</button>)}{isOwner && !u.is_owner && u.user_type === "TENANT" && <button className="btn btn-outline-warning" onClick={() => setDlg("transfer")}>Transfer ownership</button>}</>} />
      {u?.rep_name && <Alert kind="info">Signs in to the rep app as <strong>{u.rep_name}</strong>. Roles are set here; the rep record's own details — territory, working area, monthly target — live under <Link to={`/app/suppliers/reps/${u.supplier_rep}`}>Suppliers → Reps</Link>.</Alert>}
      <div className="card"><div className="card-body"><div className="row">
        <Field label="Full name *" col="col-md-4"><input className="form-control" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></Field>
        <Field label="Email" col="col-md-4"><input type="email" className="form-control" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
        <Field label="Mobile" col="col-md-4"><input className="form-control" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} placeholder="+92 300 1234567" /></Field>
        {!id && <PasswordFields value={form.password} onChange={(v) => setForm({ ...form, password: v })} confirm={form.password_confirm} onConfirmChange={(v) => setForm({ ...form, password_confirm: v })} label="Password" confirmLabel="Confirm password" col="col-md-4" hint="the user signs in with this password" required />}
        {!id && <Field label=" " col="col-md-4"><div className="form-check form-switch mt-2"><input className="form-check-input" type="checkbox" id="inv" checked={form.send_invite} onChange={(e) => setForm({ ...form, send_invite: e.target.checked })} /><label className="form-check-label fs-13" htmlFor="inv">Email / SMS the login details to them</label></div></Field>}
        {page.can("manage_access") && <Field label="Roles *" col="col-12">{roles.data ? <RolePicker roles={roleRows} value={form.roles} onChange={(ids) => { setRoleError(null); setForm({ ...form, roles: ids }); }} /> : <Spinner />}{roleError && <div className="text-danger fs-12 mt-1"><i className="ti ti-alert-circle me-1" />{roleError}</div>}</Field>}
        {page.can("manage_access") && hasField && <Field label="Rep record *" col="col-12"><RepLink form={form} setForm={setForm} currentRep={u?.rep_name} error={repError} fieldRoles={fieldRoleNames} /></Field>}
        {page.can("manage_access") && willUnlinkRep && <div className="col-12"><Alert kind="warning">Saving with no field role unlinks <strong>{u.rep_name}</strong> from this login. The rep record and its history stay; this person simply stops being a rep and loses the mobile app.</Alert></div>}
        {id && u?.supplier_rep && pageRights("supplier_reps").edit && <Field label="Can take orders" col="col-md-6" hint="this rep's own override of the shop-proximity rule — saves immediately">
          <RepOrderFreedom repId={u.supplier_rep} />
        </Field>}
        {page.can("manage_access") && <Field label="Can sign in on" col="col-md-6" hint="where this login works — the mobile app, the web, or both">
          <select className="form-select" value={form.login_surface || "both"} onChange={(e) => setForm({ ...form, login_surface: e.target.value })}>
            <option value="both">App and web (default)</option>
            <option value="app">Mobile app only</option>
            <option value="web">Web only</option>
          </select>
        </Field>}
        {page.can("manage_access") && <Field label="Warehouse scope" col="col-md-6" hint="Leave all unchecked for access to every warehouse"><div className="d-flex flex-wrap gap-3">{warehouses.data?.map((w) => <div className="form-check" key={w.id}><input className="form-check-input" type="checkbox" id={`w-${w.id}`} checked={form.warehouses.includes(w.id)} onChange={(e) => setForm({ ...form, warehouses: e.target.checked ? [...form.warehouses, w.id] : form.warehouses.filter((x: string) => x !== w.id) })} /><label htmlFor={`w-${w.id}`} className="form-check-label">{w.name}</label></div>)}</div></Field>}
      </div><div className="d-flex gap-2 justify-content-end"><button className="btn btn-light" onClick={() => nav(-1)}>Cancel</button>{(id ? page.edit : page.add) && <button className="btn btn-primary" onClick={save}>Save user</button>}</div></div></div>
      {(id || page.can("manage_access")) && <AccessPanel roleIds={form.roles} savedRoleIds={u?.roles || []} access={access.data} preview={preview.data} hasField={hasField} repLabel={keepingRep ? u.rep_name : form.rep_mode === "new" && form.rep_supplier ? "a new rep record on save" : form.rep_mode === "existing" && form.rep_id ? "the rep picked above" : ""} repChosen={repChosen} />}
      {id && activityPage.view && <ActivityCard id={id} />}
      <Modal open={dlg === "password"} title="Reset password" onClose={() => { setDlg(null); setPwd(""); setPwd2(""); }} size="md"
        footer={<><button className="btn btn-light" onClick={() => { setDlg(null); setPwd(""); setPwd2(""); }}>Cancel</button><button className="btn btn-primary" disabled={!passwordOk(pwd) || pwd !== pwd2} onClick={() => { act("reset-password", { password: pwd }); setPwd(""); setPwd2(""); }}>Reset password</button></>}>
        <div className="row"><PasswordFields value={pwd} onChange={setPwd} confirm={pwd2} onConfirmChange={setPwd2} label="New password" confirmLabel="Confirm new password" col="col-md-6" hint="the user must change it at their next login" required /></div>
      </Modal>
      <ConfirmDialog open={dlg === "deactivate"} title="Deactivate user" message="They will be signed out and unable to log in." danger confirmText="Deactivate" onClose={() => setDlg(null)} onConfirm={async () => { try { await api.delete(`/api/v1/users/${id}`); toast.success("Deactivated"); qc.invalidateQueries({ queryKey: ["doc", "/api/v1/users", id] }); } catch (e: any) { toast.error(e.message); } setDlg(null); }} />
      <ConfirmDialog open={dlg === "transfer"} title="Transfer ownership" message={<>Make <strong>{u?.full_name}</strong> the company owner? You will keep your roles but lose owner rights.</>} danger confirmText="Transfer" onClose={() => setDlg(null)} onConfirm={async () => { try { await api.post("/api/v1/users/transfer-ownership", { membership_id: id }); toast.success("Ownership transferred"); nav("/app/users"); } catch (e: any) { toast.error(e.message); } setDlg(null); }} />
    </>
  );
}

/** The two kinds of role, and the difference the person choosing has to understand before they tick anything. */
const ROLE_KINDS = [
  { type: "internal", title: "Office roles", caption: "Pages and buttons in this web admin.", icon: "ti-building", tone: "primary" },
  { type: "external", title: "Field roles (rep app)", caption: "The rep mobile app. Needs a rep record.", icon: "ti-device-mobile", tone: "warning" },
] as const;

/**
 * Both kinds in one picker, in two labelled groups.
 *
 * They are not interchangeable and the consequence of confusing them is large, so the grouping is the point:
 * an office role only widens what opens in this admin, while a field role moves the login into the rep app
 * with a rep record, its own customers and its own working area behind it. A flat alphabetical list of
 * checkboxes hid that distinction exactly where it mattered — at the moment of ticking one.
 */
function RolePicker({ roles, value, onChange }: { roles: any[]; value: string[]; onChange: (ids: string[]) => void }) {
  const toggle = (rid: string, on: boolean) => onChange(on ? [...value, rid] : value.filter((x) => x !== rid));
  return (
    <>
      <div className="fs-12 text-muted mb-2">Office roles decide which pages of this admin site open. A field role is a bigger step: it signs the user into the rep mobile app against a rep record of their own, with their own customers and their own working area.</div>
      <div className="row g-2">
        {ROLE_KINDS.map((k) => {
          const rows = roles.filter((r) => r.role_type === k.type && !r.is_system);
          return (
            <div className="col-md-6" key={k.type}>
              <div className={`border border-${k.tone} rounded h-100`}>
                <div className={`d-flex flex-wrap align-items-center gap-2 px-2 py-1 border-bottom bg-${k.tone}-light`}>
                  <i className={`ti ${k.icon} text-${k.tone}`} />
                  <strong className="fs-13">{k.title}</strong>
                  <span className="text-muted fs-11">{k.caption}</span>
                </div>
                <div className="p-2">
                  {!rows.length && <div className="text-muted fs-12">None defined yet. <Link to={`/app/users/roles/new?type=${k.type}`}>Add one</Link>.</div>}
                  {rows.map((r) => (
                    <div className="form-check mb-1" key={r.id}>
                      <input className="form-check-input" type="checkbox" id={`r-${r.id}`} checked={value.some((x) => String(x) === String(r.id))} onChange={(e) => toggle(String(r.id), e.target.checked)} />
                      <label htmlFor={`r-${r.id}`} className="form-check-label">
                        <span className="fw-medium fs-13">{r.name}</span>
                        {r.description && <span className="text-muted fs-11 d-block">{r.description}</span>}
                      </label>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          );
        })}
      </div>
      <div className="form-text">Changing roles signs the user out of every device, the mobile app included — they must sign in again before the change applies.</div>
    </>
  );
}

/**
 * Shown only while a field role is selected, because that is the only time it means anything.
 *
 * Either end of the requirement is offered: an existing rep record, or a supplier the server creates one
 * under. Dropping the last field role needs nothing here — the server unlinks the rep by itself — which is
 * why this control disappears instead of growing an "unlink" option nobody would find.
 */
function RepLink({ form, setForm, currentRep, error, fieldRoles }: { form: any; setForm: (f: any) => void; currentRep?: string; error: string | null; fieldRoles: string[] }) {
  const set = (patch: any) => setForm({ ...form, ...patch });
  const modes = [
    { key: "keep", label: `Keep ${currentRep}`, hidden: !currentRep },
    { key: "existing", label: "Link an existing rep" },
    { key: "new", label: "Create a rep under a supplier" },
  ].filter((m) => !m.hidden);
  return (
    <div className="border border-warning rounded p-2 bg-warning-light">
      <div className="fs-12 mb-2"><strong>{fieldRoles.join(" + ")}</strong> {fieldRoles.length > 1 ? "are field roles" : "is a field role"}, so this login needs a rep record: the rep record is what gives it its own customers and its own working area, and without one the field permissions apply to nobody.</div>
      <div className="d-flex flex-wrap gap-3 mb-2">
        {modes.map((m) => (
          <div className="form-check" key={m.key}>
            <input className="form-check-input" type="radio" name="rep-mode" id={`rm-${m.key}`} checked={form.rep_mode === m.key} onChange={() => set({ rep_mode: m.key, rep_id: null, rep_supplier: null })} />
            <label className="form-check-label fs-13" htmlFor={`rm-${m.key}`}>{m.label}</label>
          </div>
        ))}
      </div>
      {form.rep_mode === "existing" && <div style={{ maxWidth: 440 }}><EntitySelect endpoint="/api/v1/supplier-reps" query={{ is_active: true, has_login: false }} value={form.rep_id} onChange={(v) => set({ rep_id: v })} mapOption={(r: any) => ({ id: r.id, label: `${r.code} · ${r.name}`, sub: r.supplier_name })} placeholder="Search reps with no login yet…" /></div>}
      {form.rep_mode === "new" && <div style={{ maxWidth: 440 }}><EntitySelect endpoint="/api/v1/suppliers" query={{ is_active: true }} value={form.rep_supplier} onChange={(v) => set({ rep_supplier: v })} mapOption={(s: any) => ({ id: s.id, label: s.name, sub: s.code })} placeholder="Search suppliers…" /><div className="form-text">A rep record is created for this person under that supplier, numbered automatically, and linked to this login.</div></div>}
      {error && <div className="text-danger fs-12 mt-2 d-flex gap-2"><i className="ti ti-alert-circle mt-1 flex-shrink-0" /><span>{error}</span></div>}
    </div>
  );
}

type AccessPage = { key: string; label: string; group: string; path: string; actions: Record<string, boolean>; extras: Record<string, boolean> };
const ACTION_CHIPS: [string, string, string][] = [["view", "V", "View"], ["add", "A", "Add"], ["edit", "E", "Edit"], ["delete", "D", "Delete"]];
const roleKey = (ids: any[]) => ids.map(String).sort().join(",");

/**
 * What a role selection would grant, answered by the server before anything is saved.
 *
 * `/users/{id}/access` can only speak for roles already saved, and the question being asked here is "what does
 * this tick give them?". `preview-access` runs the same `resolve_pages` the user's own session runs, so there
 * is one copy of the permission rules and this screen cannot drift from the thing it is reporting on. Keyed on
 * the sorted ids, so flicking a checkbox back serves the cached answer.
 */
function usePreviewAccess(roleIds: string[], enabled = true) {
  const key = roleKey(roleIds);
  // The previous answer stays on screen while the next one loads, so ticking a second role updates the panel
  // in place instead of blanking it — and the rep control does not flicker out between two selections.
  return useQuery<any>({ queryKey: ["preview-access", key], queryFn: () => api.post("/api/v1/users/preview-access", { roles: key ? key.split(",") : [] }), staleTime: 300_000, enabled, placeholderData: (prev: any) => prev });
}

/** What the user can reach: the headline figure, the roles behind it, and every page they open with the actions they get. */
function AccessPanel({ roleIds, savedRoleIds, access, preview, hasField, repLabel, repChosen }: { roleIds: string[]; savedRoleIds: string[]; access: any; preview: any; hasField: boolean; repLabel: string; repChosen: boolean }) {
  const [showAll, setShowAll] = useState(false);
  // Labels and denominators only — "7 of 8 in Inventory", the name behind an extra's key, the groups that got
  // nothing. Not one grant is decided here; every true and false below comes from the server's own answer.
  const meta = useQuery<any[]>({ queryKey: ["permission-pages"], queryFn: () => api.get("/api/v1/permission-pages"), staleTime: 600_000 });
  const extCatalog = useQuery<any[]>({ queryKey: ["perm-catalog", "external"], queryFn: () => api.get("/api/v1/permissions-catalog", { role_type: "external" }), staleTime: 600_000, enabled: hasField });
  const isOwner = Boolean(access?.is_owner);
  // An owner's reach comes from the membership flag, not from roles: the Owner role itself carries no
  // permissions, so previewing it would answer "no pages" for the one person who has them all.
  const data = isOwner ? access : preview;
  const pages: AccessPage[] = data?.pages || [];
  const total: number = data?.pages_total || 0;
  const groups = useMemo(() => {
    // Insertion order, so the groups follow the order the server sent them rather than a second opinion here.
    const out = new Map<string, AccessPage[]>();
    for (const p of pages) out.set(p.group, [...(out.get(p.group) || []), p]);
    return [...out.entries()].map(([group, rows]) => ({ group, pages: rows }));
  }, [pages]);
  const groupTotals = useMemo(() => new Map<string, number>((meta.data || []).map((g: any) => [g.group, g.pages.length])), [meta.data]);
  const extraLabels = useMemo(() => {
    const m = new Map<string, string>();
    (meta.data || []).forEach((g: any) => g.pages.forEach((p: any) => p.extras.forEach((e: any) => m.set(`${p.key}.${e.key}`, e.label))));
    return m;
  }, [meta.data]);
  const fieldPerms = useMemo(() => {
    const labels = new Map<string, string>();
    (extCatalog.data || []).forEach((m: any) => m.permissions.forEach((p: any) => labels.set(p.codename, p.label)));
    return ((data?.field_permissions || []) as string[]).map((c) => labels.get(c) || c).sort();
  }, [extCatalog.data, data]);
  const picked: any[] = data?.roles || [];
  const dirty = roleKey(roleIds) !== roleKey(savedRoleIds);
  const shut = (meta.data || []).map((g: any) => g.group).filter((g: string) => !groups.some((x) => x.group === g));
  const unknown: string[] = preview?.unknown_roles || [];
  return (
    <div className="card mt-3">
      <div className="card-header d-flex flex-wrap align-items-center justify-content-between gap-2">
        <div><strong>What this user can see</strong><div className="fs-11 text-muted">Resolved by the server the same way their own session resolves it — web admin and rep app alike.</div></div>
        {access && !dirty && <span className="badge bg-success-light text-success border border-success fs-11"><i className="ti ti-circle-check me-1" />Matches what is saved</span>}
        {dirty && <span className="badge bg-warning-light text-warning border border-warning fs-11"><i className="ti ti-pencil me-1" />Not saved yet — this is what Save would grant{access ? ` (saved now: ${access.pages.length} of ${access.pages_total} pages)` : ""}</span>}
      </div>
      <div className="card-body">
        {!data ? <Spinner /> : (
          <>
            <div className="row g-3 align-items-center mb-3">
              <div className="col-md-5">
                <div className="fs-20 fw-semibold">{pages.length === 0 ? "Sees no pages at all" : `Sees ${pages.length} of ${total} pages`}</div>
                <div className="progress mt-1" style={{ height: 6, maxWidth: 320 }}><div className={`progress-bar bg-${pages.length ? "primary" : "danger"}`} style={{ width: `${total ? Math.round((pages.length / total) * 100) : 0}%` }} /></div>
              </div>
              <div className="col-md-7 d-flex flex-wrap align-items-center gap-1">
                {!picked.length && <span className="text-muted fs-12">No role selected — this login could sign in but every screen would be closed to it.</span>}
                {picked.map((r: any) => <span key={r.id} className={`badge fs-11 bg-${r.type === "external" ? "warning" : "primary"}-light text-${r.type === "external" ? "warning" : "primary"} border border-${r.type === "external" ? "warning" : "primary"}`}>{r.name}</span>)}
                <span className={`badge fs-11 ms-1 bg-${hasField ? "success" : "secondary"}-light text-${hasField ? "success" : "secondary"} border border-${hasField ? "success" : "secondary"}`}><i className={`ti ${hasField ? "ti-device-mobile-check" : "ti-device-desktop"} me-1`} />{hasField ? `Rep mobile app${repLabel ? ` · ${repLabel}` : ""}` : "Web admin only — no mobile app"}</span>
                {isOwner && <span className="badge bg-primary fs-11 ms-1">Company owner</span>}
              </div>
            </div>
            {Boolean(unknown.length) && <Alert kind="warning">{unknown.length === 1 ? "One selected role no longer exists" : `${unknown.length} selected roles no longer exist`} and {unknown.length === 1 ? "was" : "were"} left out of this answer — it was probably deleted in another tab. Reload the page before saving.</Alert>}
            {access?.field_without_rep && <Alert kind="danger">This user holds rep permissions they cannot use: a field role is saved against them but no rep record is linked, so the rep app has no customers, no working area and no targets to show them. Link a rep above and save.</Alert>}
            {hasField && !repChosen && <Alert kind="warning">A field role is selected but no rep record is linked yet. Pick one above — until then these rep permissions would apply to nobody.</Alert>}
            {hasField && Boolean(fieldPerms.length) && <div className="mb-3"><div className="fs-12 text-muted mb-1">In the rep mobile app they may:</div><div className="d-flex flex-wrap gap-1">{fieldPerms.map((p) => <span key={p} className="badge bg-secondary-light text-secondary fs-11">{p}</span>)}</div></div>}
            {isOwner && !showAll ? (
              <div className="fs-13">Company owner — every page and every action, always. <button className="btn btn-link btn-sm p-0 align-baseline" onClick={() => setShowAll(true)}>List them anyway</button></div>
            ) : (
              <>
                <div className="row g-2">
                  {groups.map((g) => (
                    <div className="col-md-6 col-xl-4" key={g.group}>
                      <div className="border rounded h-100">
                        <div className="d-flex align-items-center justify-content-between px-2 py-1 bg-light border-bottom"><strong className="fs-12">{g.group}</strong><span className="text-muted fs-11">{g.pages.length}{groupTotals.has(g.group) ? ` of ${groupTotals.get(g.group)}` : ""}</span></div>
                        <div className="px-2 py-1">
                          {g.pages.map((p) => {
                            const extras = Object.entries(p.extras || {}).filter(([, on]) => on).map(([k]) => extraLabels.get(`${p.key}.${k}`) || k);
                            return (
                              <div className="d-flex align-items-start justify-content-between gap-2 py-1" key={p.key}>
                                <div className="flex-grow-1">
                                  <div className="fs-12">{p.label}</div>
                                  {Boolean(extras.length) && <div className="fs-11 text-muted">+ {extras.join(", ")}</div>}
                                </div>
                                <div className="d-flex gap-1 flex-shrink-0 pt-1">
                                  {ACTION_CHIPS.map(([a, letter, label]) => (
                                    <span key={a} title={`${label}: ${p.actions[a] === undefined ? "not applicable to this page" : p.actions[a] ? "yes" : "no"}`}
                                      className={`badge ${p.actions[a] === undefined ? "bg-white text-muted border" : p.actions[a] ? "bg-primary" : "bg-light text-muted border"}`}
                                      style={{ fontSize: 10, width: 17, padding: 0, lineHeight: "15px" }}>{p.actions[a] === undefined ? "–" : letter}</span>
                                  ))}
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
                <div className="fs-11 text-muted mt-2"><strong>V</strong> view · <strong>A</strong> add · <strong>E</strong> edit · <strong>D</strong> delete — filled means allowed. {Boolean(shut.length) && <>No access at all to: {shut.join(", ")}.</>}</div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function ActivityCard({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["user-activity", id], queryFn: () => api.get(`/api/v1/users/${id}/activity`) });
  return <div className="card mt-3"><div className="card-header"><strong>Recent activity</strong></div><div className="card-body p-0"><table className="table table-sm fs-12 mb-0"><tbody>{q.data?.slice(0, 30).map((a: any) => <tr key={a.id}><td>{datetime(a.at)}</td><td>{a.action}</td><td>{a.model} {a.object_repr}</td><td className="text-muted">{a.ip}</td></tr>)}{q.data && !q.data.length && <tr><td className="text-muted text-center py-3">No activity yet</td></tr>}</tbody></table></div></div>;
}

export function RolesPage() {
  const qc = useQueryClient();
  const page = usePage("roles");
  const q = useQuery({ queryKey: ["roles-all"], queryFn: () => api.get("/api/v1/roles") });
  const [del, setDel] = useState<any>(null);
  const groups = { internal: (q.data || []).filter((r: any) => r.role_type === "internal"), external: (q.data || []).filter((r: any) => r.role_type === "external") };
  return (
    <>
      <PageHeader title="Roles" crumbs={[{ label: "Users Management" }, { label: "Roles" }]} actions={page.add && <><Link to="/app/users/roles/new?type=internal" className="btn btn-primary"><i className="ti ti-plus me-1" />Add internal role</Link><Link to="/app/users/roles/new?type=external" className="btn btn-outline-primary"><i className="ti ti-plus me-1" />Add external role</Link></>} />
      {(["internal", "external"] as const).map((t) => (
        <div className="card mb-3" key={t}><div className="card-header"><strong>{t === "internal" ? "Internal roles (staff)" : "External roles (manufacturer reps)"}</strong><span className="text-muted fs-12 ms-2">{t === "internal" ? "Clone a default template or build your own from the permission catalogue." : "Controls exactly what each rep sees: stock (exact / bands / none), prices, customer outstanding, collections."}</span></div>
          <div className="card-body p-0"><table className="table align-middle mb-0 fs-13"><thead className="table-light"><tr><th>Role</th><th>Description</th><th className="num">Permissions</th><th className="num">Users</th><th /></tr></thead><tbody>{groups[t].map((r: any) => <tr key={r.id}><td className="fw-medium"><Link to={`/app/users/roles/${r.id}`}>{r.name}</Link>{r.is_system && <span className="badge bg-secondary-light text-secondary ms-2">system</span>}{r.full_access && <span className="badge bg-success-light text-success ms-2">full access</span>}</td><td className="text-muted">{r.description}</td><td className="num">{r.full_access ? "all" : r.permissions.length}</td><td className="num">{r.users_count}</td><td className="text-end">{page.add && <button className="btn btn-sm btn-outline-secondary me-1" onClick={async () => { const name = prompt("Name for the copy", `${r.name} (copy)`); if (!name) return; await api.post(`/api/v1/roles/${r.id}/clone`, { name }); qc.invalidateQueries({ queryKey: ["roles-all"] }); }}>Clone</button>}{page.delete && !r.is_system && <button className="btn btn-sm btn-outline-danger" onClick={() => setDel(r)}><i className="ti ti-trash" /></button>}</td></tr>)}</tbody></table></div></div>
      ))}
      <ConfirmDialog open={Boolean(del)} title="Delete role" message={<>Delete <strong>{del?.name}</strong>? Users must be unassigned first.</>} danger onClose={() => setDel(null)} onConfirm={async () => { try { await api.delete(`/api/v1/roles/${del.id}`); toast.success("Deleted"); qc.invalidateQueries({ queryKey: ["roles-all"] }); } catch (e: any) { toast.error(e.message); } setDel(null); }} />
    </>
  );
}

export function RoleFormPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const sp = new URLSearchParams(window.location.search);
  const q = useDoc("/api/v1/roles", id);
  const roleType = q.data?.role_type || sp.get("type") || "internal";
  const catalog = useQuery({ queryKey: ["perm-catalog", roleType], queryFn: () => api.get("/api/v1/permissions-catalog", { role_type: roleType }), enabled: roleType === "external" });
  const page = usePage("roles");
  const [form, setForm] = useState<any>(null);
  const [saved, setSaved] = useState<null | { name: string; users: number; revoked: boolean }>(null);
  const [busy, setBusy] = useState(false);
  // The first seed must come from data fresh off the network, not the cached copy react-query hands back
  // instantly while it refetches — seeding from that stale copy showed pre-save switches until a hard refresh.
  // Once the form is seeded, background refetches are ignored so they can never wipe the user's editing.
  if ((id && (q.isLoading || (q.isFetching && !form))) || (roleType === "external" && catalog.isLoading)) return <Spinner />;
  // `permissions_effective` expands any coarse codenames an older role still holds, so the switches show reality
  if (!form) { setForm(q.data ? { name: q.data.name, description: q.data.description, permissions: q.data.permissions_effective || q.data.permissions, approval_limits: q.data.approval_limits || {}, full_access: Boolean(q.data.full_access) } : { name: "", description: "", permissions: [], approval_limits: {} }); return <Spinner />; }
  const readOnly = Boolean(q.data?.is_system) || (id ? !page.edit : !page.add);
  const toggle = (c: string) => setForm({ ...form, permissions: form.permissions.includes(c) ? form.permissions.filter((x: string) => x !== c) : [...form.permissions, c] });
  const toggleModule = (mod: any, on: boolean) => { const codes = mod.permissions.map((p: any) => p.codename); setForm({ ...form, permissions: on ? Array.from(new Set([...form.permissions, ...codes])) : form.permissions.filter((x: string) => !codes.includes(x)) }); };
  // Every key that carries role data. The global staleTime is 15s, so without this the list and the next visit
  // to this role re-serve the pre-save copy — the change looks like it was never written. `me` is in the list
  // because the signed-in user's own page rights come from the roles they hold.
  const invalidateRoles = (roleId: string) => [["roles-all"], ["doc", "/api/v1/roles", roleId], ["list", "/api/v1/roles"], ["options", "/api/v1/roles"], ["entity-select", "/api/v1/roles"], ["entity-select-one", "/api/v1/roles"], ["me"]].forEach((queryKey) => qc.invalidateQueries({ queryKey }));
  const save = async () => {
    // The server revokes every holder's sessions only when the stored codenames really differ
    // (`RoleService.update_role`), so test the same thing and never warn about a sign-out that did not happen.
    const held = new Set<string>(q.data?.permissions || []);
    const wanted = new Set<string>(form.permissions);
    const permsChanged = held.size !== wanted.size || [...wanted].some((c) => !held.has(c));
    setSaved(null);
    setBusy(true);
    try {
      const body = { ...form, role_type: roleType };
      const r = id ? await api.patch(`/api/v1/roles/${id}`, body) : await api.post("/api/v1/roles", body);
      invalidateRoles(r.id);
      // Editing stays put so the confirmation is read and a second edit needs no reload; a brand-new role still
      // has to land on its own URL, or pressing Save again would create a second role.
      if (!id) { toast.success(`Role "${r.name}" created`); nav(`/app/users/roles/${r.id}`, { replace: true }); return; }
      setSaved({ name: r.name, users: r.users_count || 0, revoked: permsChanged });
      toast.success(`Role "${r.name}" saved`);
    } catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
  };
  return (
    <>
      <PageHeader title={id ? `Role: ${q.data.name}` : "Add New Role"} badge={!id && <span className="badge bg-secondary-light text-secondary">{roleType === "external" ? "external (rep) role" : "internal (staff) role"}</span>} crumbs={[{ label: "Users Management" }, { label: "Roles", to: "/app/users/roles" }, { label: id ? "Edit role" : "Add New Role" }]} />
      {saved && <Alert kind={saved.revoked && saved.users > 0 ? "warning" : "success"}>{saved.revoked && saved.users > 0
        ? <><strong>{saved.name}</strong> saved. Its permissions changed, so {saved.users === 1 ? "the 1 user who holds it has" : `all ${saved.users} users who hold it have`} been signed out of every device, the mobile app included — they must sign in again before the new permissions apply.</>
        : saved.revoked
          ? <><strong>{saved.name}</strong> saved. Its permissions changed, but nobody holds this role yet, so no one was signed out.</>
          : <><strong>{saved.name}</strong> saved. Its permissions are unchanged, so nobody was signed out of their devices.</>}</Alert>}
      <div className="card mb-3"><div className="card-body"><div className="row"><Field label="Role Name *" col="col-md-4"><input className="form-control" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} disabled={q.data?.is_system} /></Field><Field label="Description" col="col-md-8"><input className="form-control" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
        {roleType === "internal" && <><Field label="Stock adjustment approval limit" col="col-md-3" hint="blank = unlimited"><input type="number" className="form-control" value={form.approval_limits.stock_adjustment || ""} onChange={(e) => setForm({ ...form, approval_limits: { ...form.approval_limits, stock_adjustment: e.target.value } })} /></Field><Field label="Expense approval limit" col="col-md-3"><input type="number" className="form-control" value={form.approval_limits.expense || ""} onChange={(e) => setForm({ ...form, approval_limits: { ...form.approval_limits, expense: e.target.value } })} /></Field></>}</div></div></div>
      {q.data?.is_system ? <Alert kind="info">The Owner role always has every internal permission and cannot be edited.</Alert> : roleType === "external" ? (
        <>
          <div className="d-flex align-items-center justify-content-between mb-2">
            <div>
              <h6 className="mb-0">Permissions</h6>
              <div className="fs-12 text-muted">What a manufacturer rep may see and do in the rep portal and the mobile app.</div>
            </div>
            <span className="badge bg-primary-light text-primary">{form.permissions.length} selected</span>
          </div>
          <div className="row g-3">{catalog.data?.map((mod: any) => { const all = mod.permissions.every((p: any) => form.permissions.includes(p.codename)); return (
            <div className="col-md-6 col-xl-4" key={mod.module}><div className="card h-100"><div className="card-header d-flex justify-content-between align-items-center"><strong>{mod.label}</strong><div className="form-check form-switch mb-0"><input className="form-check-input" type="checkbox" checked={all} onChange={(e) => toggleModule(mod, e.target.checked)} /></div></div><div className="card-body perm-grid">{mod.permissions.map((p: any) => <div className="form-check" key={p.codename}><input className="form-check-input" type="checkbox" id={p.codename} checked={form.permissions.includes(p.codename)} onChange={() => toggle(p.codename)} /><label className="form-check-label" htmlFor={p.codename}>{p.label}</label></div>)}</div></div></div>); })}</div>
        </>
      ) : (
        <div className={readOnly ? "pe-none opacity-75" : ""}>
          <div className="card mb-3"><div className="card-body py-2">
            <div className="form-check form-switch mb-0">
              <input className="form-check-input" type="checkbox" id="full-access" checked={Boolean(form.full_access)} onChange={(e) => setForm({ ...form, full_access: e.target.checked })} />
              <label className="form-check-label fw-semibold" htmlFor="full-access">Full access — same as the Owner, every page and button, including new ones</label>
            </div>
          </div></div>
          {!form.full_access && <PermissionMatrix value={form.permissions} onChange={(permissions) => setForm({ ...form, permissions })} />}
        </div>
      )}
      <div className="d-flex gap-2 justify-content-end mt-3"><button className="btn btn-light" onClick={() => nav(-1)}>Cancel</button>{!readOnly && <button className="btn btn-primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save role"}</button>}</div>
    </>
  );
}

export function ActivityLogPage() {
  const cols: Column<any>[] = [{ key: "at", header: "When", render: (a) => datetime(a.at) }, { key: "actor_name", header: "User" }, { key: "action", header: "Action" }, { key: "model", header: "Object", render: (a) => <>{a.model}<div className="text-muted fs-11">{a.object_repr}</div></> }, { key: "ip", header: "IP" }, { key: "diff", header: "Change", render: (a) => (a.before || a.after) && <details className="fs-11"><summary className="cursor-pointer text-muted">view</summary><pre className="mb-0 bg-light p-1 rounded" style={{ maxWidth: 400, whiteSpace: "pre-wrap" }}>{JSON.stringify({ before: a.before, after: a.after }, null, 1)}</pre></details> }];
  return <ListPage title="Activity log" crumbs={[{ label: "Users & roles" }, { label: "Activity log" }]} endpoint="/api/v1/audit-log" columns={cols} defaultOrdering="-at" searchPlaceholder="Search action, model…" filters={[{ name: "action", label: "Action", type: "select", options: ["auth.login", "created", "updated", "deleted", "status.confirmed", "status.dispatched", "status.posted", "journal.posted", "payment.recorded", "role.updated", "user.created"].map((a) => ({ value: a, label: a })) }]} />;
}

export function ProfilePage() {
  const qc = useQueryClient();
  const { user, refresh } = useAuth();
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => api.get("/api/v1/me/sessions") });
  const prefs = useQuery({ queryKey: ["notif-prefs"], queryFn: () => api.get("/api/v1/me/notification-preferences") });
  const [tab, setTab] = useState("profile");
  const [pwd, setPwd] = useState({ current_password: "", new_password: "" });
  return (
    <>
      <PageHeader title="My profile" crumbs={[{ label: "Profile" }]} />
      <Tabs tabs={[{ key: "profile", label: "Profile" }, { key: "password", label: "Password" }, { key: "sessions", label: "Devices & sessions" }, { key: "notifications", label: "Notification preferences" }]} active={tab} onChange={setTab} />
      {tab === "profile" && <div className="card"><div className="card-body"><ResourceForm fields={[{ name: "full_name", label: "Full name", required: true }, { name: "locale", label: "Language", type: "select", options: [{ value: "en", label: "English" }, { value: "ur", label: "اردو (coming soon)" }] }, { name: "timezone", label: "Timezone" }]} initial={user || {}} onSubmit={async (v) => { await api.patch("/api/v1/me", v); toast.success("Profile updated"); qc.invalidateQueries({ queryKey: ["me"] }); }} /></div></div>}
      {tab === "password" && <div className="card"><div className="card-body" style={{ maxWidth: 480 }}><Field label="Current password"><input type="password" className="form-control" value={pwd.current_password} onChange={(e) => setPwd({ ...pwd, current_password: e.target.value })} /></Field><Field label="New password"><input type="password" className="form-control" value={pwd.new_password} onChange={(e) => setPwd({ ...pwd, new_password: e.target.value })} /></Field><button className="btn btn-primary" onClick={async () => { try { await api.post("/api/v1/auth/password/change", pwd); toast.success("Password changed — please sign in again"); useAuth.getState().logout(); window.location.assign("/login"); } catch (e: any) { toast.error(e.message); } }}>Change password</button></div></div>}
      {tab === "sessions" && <div className="card"><div className="card-header d-flex justify-content-between"><strong>Active sessions</strong><button className="btn btn-sm btn-outline-danger" onClick={async () => { await api.post("/api/v1/me/sessions/revoke-all"); useAuth.getState().logout(); window.location.assign("/login"); }}>Sign out everywhere</button></div><div className="card-body p-0"><table className="table table-sm fs-13 mb-0"><thead className="table-light"><tr><th>Device</th><th>Platform</th><th>IP</th><th>Signed in</th><th>Last used</th><th /></tr></thead><tbody>{sessions.data?.map((s: any) => <tr key={s.id}><td className="text-truncate" style={{ maxWidth: 320 }}>{s.device}</td><td>{s.platform}</td><td>{s.ip}</td><td>{datetime(s.created_at)}</td><td>{datetime(s.last_used_at)}</td><td className="text-end"><button className="btn btn-sm btn-outline-danger" onClick={async () => { await api.post(`/api/v1/me/sessions/${s.id}/revoke`); sessions.refetch(); }}>Revoke</button></td></tr>)}</tbody></table></div></div>}
      {tab === "notifications" && prefs.data && <div className="card"><div className="card-body p-0"><table className="table table-sm fs-13 mb-0"><thead className="table-light"><tr><th>Notification</th><th>Push</th><th>Email</th><th>SMS</th></tr></thead><tbody>{prefs.data.map((p: any, i: number) => <tr key={p.type}><td>{p.label}<div className="text-muted fs-11">{p.type}</div></td>{(["push", "email", "sms"] as const).map((ch) => <td key={ch}><input type="checkbox" className="form-check-input" checked={p[ch]} onChange={async (e) => { const next = prefs.data.map((x: any, j: number) => (j === i ? { ...x, [ch]: e.target.checked } : x)); await api.put("/api/v1/me/notification-preferences", next.map((x: any) => ({ type: x.type, push: x.push, email: x.email, sms: x.sms }))); prefs.refetch(); }} /></td>)}</tr>)}</tbody></table></div></div>}
    </>
  );
}

/** The rep's own "order from anywhere" switch, editable from the user page the owner actually uses —
 *  it writes straight onto the rep record, same as Suppliers → Reps → Edit. */
function RepOrderFreedom({ repId }: { repId: string }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["doc", "/api/v1/supplier-reps", repId], queryFn: () => api.get(`/api/v1/supplier-reps/${repId}`) });
  if (!q.data) return <Spinner />;
  return (
    <select className="form-select" value={q.data.geofence_policy || ""} onChange={async (e) => {
      try {
        await api.patch(`/api/v1/supplier-reps/${repId}`, { geofence_policy: e.target.value });
        toast.success("Saved — applies to the rep's next order");
        qc.invalidateQueries({ queryKey: ["doc", "/api/v1/supplier-reps", repId] });
      } catch (err: any) { toast.error(err.message); }
    }}>
      <option value="">Company rule (default)</option>
      <option value="block">At the shop only</option>
      <option value="warn">Anywhere — flag when away from the shop</option>
      <option value="off">Anywhere</option>
    </select>
  );
}
