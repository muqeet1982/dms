import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { datetime } from "@/lib/format";
import { toast } from "@/lib/toast";
import { Alert, Field, PageHeader, Spinner, StatCard, StatusBadge } from "@/components/ui/Bits";
import { ConfirmDialog, Modal } from "@/components/ui/Modal";

const P = "/api/v1/platform/security";

function NotInstalled({ hint }: { hint: string }) {
  return (
    <div className="card"><div className="card-body text-center py-5">
      <i className="ti ti-shield-off fs-1 text-muted" />
      <h5 className="mt-2">Security helper not installed</h5>
      <p className="text-muted fs-13 mb-3" style={{ maxWidth: 620, margin: "0 auto" }}>{hint}</p>
      <pre className="bg-light border rounded p-3 text-start d-inline-block fs-12 mb-0">sudo bash /var/www/dms/deploy/security/install-security.sh</pre>
      <div className="fs-12 text-muted mt-3">It installs the hardened fail2ban jails (SSH, nginx, API logins, scanners, repeat offenders — all permanent bans),<br />sets the firewall policy (22, 80, 443, 8081 open · everything else closed) and the one root command this page uses.</div>
    </div></div>
  );
}

/** Admin → Security → Bans: everything fail2ban is currently blocking, with unban / trust / manual block. */
export function SecurityBansPage() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [jail, setJail] = useState("");
  const [page, setPage] = useState(1);
  const [blockIp, setBlockIp] = useState("");
  const [trustRow, setTrustRow] = useState<any>(null);
  const [untrust, setUntrust] = useState<string | null>(null);
  const bans = useQuery({ queryKey: ["sec-bans", q, jail, page], queryFn: () => api.get(`${P}/bans`, { q: q || undefined, jail: jail || undefined, page, page_size: 25 }) });
  const act = async (action: string, ip: string, note?: string) => {
    try { await api.post(`${P}/bans`, { action, ip, note }); toast.success(`${ip} ${action === "unban" ? "unbanned" : action === "trust" ? "trusted — it will not be banned again" : action === "block" ? "blocked for good" : "removed from the trusted list"}`); qc.invalidateQueries({ queryKey: ["sec-bans"] }); qc.invalidateQueries({ queryKey: ["sec-status"] }); }
    catch (e: any) { toast.error(e.message); }
  };
  const d = bans.data;
  if (bans.isLoading) return <Spinner />;
  if (d && !d.installed) return (<><PageHeader title="Banned IPs" crumbs={[{ label: "Security" }, { label: "Bans" }]} /><NotInstalled hint={d.install_hint} /></>);
  const pages = d?.pages || 1;
  return (
    <>
      <PageHeader title="Banned IPs" crumbs={[{ label: "Security" }, { label: "Bans" }]}
        actions={<><button className="btn btn-outline-danger" onClick={() => setBlockIp(" ")}><i className="ti ti-ban me-1" />Block an IP</button><button className="btn btn-outline-success" onClick={() => setTrustRow({ ip: d?.your_ip || "" })}><i className="ti ti-shield-check me-1" />Trust an IP</button></>} />
      <div className="row g-3 mb-3">
        <div className="col-6 col-md-3"><StatCard label="Banned right now" value={d?.count ?? 0} icon="ti-ban" color="danger" /></div>
        <div className="col-6 col-md-3"><StatCard label="Blocked all-time" value={(d?.jails || []).reduce((s: number, j: any) => s + j.total_banned, 0)} icon="ti-shield-lock" color="primary" /></div>
        <div className="col-6 col-md-3"><StatCard label="Trusted IPs" value={(d?.trusted || []).length} icon="ti-shield-check" color="success" sub="never banned again" /></div>
        <div className="col-6 col-md-3"><StatCard label="Your IP" value={d?.your_ip || "—"} icon="ti-current-location" color="info" sub={<button className="btn btn-sm btn-link p-0 fs-11" onClick={() => setTrustRow({ ip: d?.your_ip })}>trust it</button>} /></div>
      </div>

      <div className="card mb-3"><div className="card-body py-2"><div className="row g-2 align-items-center">
        <div className="col-md-5"><input className="form-control" placeholder="Search IP…" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} /></div>
        <div className="col-md-4"><select className="form-select" value={jail} onChange={(e) => { setJail(e.target.value); setPage(1); }}><option value="">All jails</option>{(d?.jails || []).map((j: any) => <option key={j.jail} value={j.jail}>{j.jail} ({j.currently_banned})</option>)}</select></div>
        <div className="col-md-3 text-end fs-13 text-muted">{d?.count ?? 0} banned · page {d?.page} / {pages}</div>
      </div></div></div>

      <div className="card mb-3"><div className="card-body p-0"><table className="table table-sm align-middle mb-0 fs-13">
        <thead className="table-light"><tr><th>IP address</th><th>Jail (what it tripped)</th><th>Banned</th><th>Expires</th><th /></tr></thead>
        <tbody>
          {(d?.results || []).map((r: any) => (
            <tr key={`${r.jail}-${r.ip}`}>
              <td className="font-monospace fw-semibold">{r.ip}{r.trusted && <span className="badge bg-success-light text-success ms-2">trusted</span>}</td>
              <td><StatusBadge status={r.jail} /></td>
              <td>{r.banned_at || "—"}</td>
              <td>{r.permanent ? <span className="badge bg-danger-light text-danger">permanent</span> : r.expires_at}</td>
              <td className="text-end text-nowrap">
                <button className="btn btn-sm btn-outline-secondary me-1" onClick={() => act("unban", r.ip)}><i className="ti ti-lock-open me-1" />Unban</button>
                <button className="btn btn-sm btn-outline-success" onClick={() => setTrustRow(r)}><i className="ti ti-shield-check me-1" />Trust</button>
              </td>
            </tr>
          ))}
          {!(d?.results || []).length && <tr><td colSpan={5} className="text-center text-muted py-4">Nothing is banned right now.</td></tr>}
        </tbody>
      </table></div>
        {pages > 1 && <div className="card-footer d-flex justify-content-between align-items-center py-2">
          <button className="btn btn-sm btn-outline-secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
          <span className="fs-13">Page {page} of {pages}</span>
          <button className="btn btn-sm btn-outline-secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
        </div>}
      </div>

      <div className="card"><div className="card-header d-flex justify-content-between align-items-center"><strong>Trusted IPs</strong><span className="fs-12 text-muted">never banned, always allowed through the firewall</span></div>
        <div className="card-body p-0"><table className="table table-sm mb-0 fs-13"><thead className="table-light"><tr><th>IP</th><th>Note</th><th>Added</th><th>By</th><th /></tr></thead>
          <tbody>{(d?.trusted || []).map((t: any) => <tr key={t.ip}><td className="font-monospace">{t.ip}</td><td>{t.note || "—"}</td><td>{t.added_at ? datetime(t.added_at) : "—"}</td><td>{t.added_by || "—"}</td><td className="text-end"><button className="btn btn-sm btn-outline-danger" onClick={() => setUntrust(t.ip)}><i className="ti ti-trash" /></button></td></tr>)}
            {!(d?.trusted || []).length && <tr><td colSpan={5} className="text-center text-muted py-3">No trusted IPs yet — trust your office IP so an accidental lockout cannot happen.</td></tr>}</tbody></table></div></div>

      <Modal open={Boolean(trustRow)} onClose={() => setTrustRow(null)} title={<><i className="ti ti-shield-check me-2 text-success" />Trust an IP address</>} size="sm"
        footer={<><button className="btn btn-light" onClick={() => setTrustRow(null)}>Cancel</button><button className="btn btn-success" onClick={async () => { await act("trust", trustRow.ip, trustRow.note); setTrustRow(null); }}>Trust this IP</button></>}>
        <div className="row">
          <Field label="IP address" col="col-md-12"><input className="form-control font-monospace" value={trustRow?.ip || ""} onChange={(e) => setTrustRow({ ...trustRow, ip: e.target.value })} placeholder="203.0.113.10" /></Field>
          <Field label="Note (who or what this is)" col="col-md-12"><input className="form-control" value={trustRow?.note || ""} onChange={(e) => setTrustRow({ ...trustRow, note: e.target.value })} placeholder="Lahore office, warehouse router…" /></Field>
        </div>
        <div className="fs-12 text-muted">The IP is unbanned everywhere, added to fail2ban's ignore list (so it can never be banned again) and allowed through the firewall.</div>
      </Modal>

      <Modal open={Boolean(blockIp)} onClose={() => setBlockIp("")} title={<><i className="ti ti-ban me-2 text-danger" />Block an IP for good</>} size="sm"
        footer={<><button className="btn btn-light" onClick={() => setBlockIp("")}>Cancel</button><button className="btn btn-danger" onClick={async () => { await act("block", blockIp.trim()); setBlockIp(""); }}>Block permanently</button></>}>
        <Field label="IP address"><input className="form-control font-monospace" autoFocus value={blockIp.trim()} onChange={(e) => setBlockIp(e.target.value)} placeholder="203.0.113.10" /></Field>
        <div className="fs-12 text-muted">The address is dropped on every port until you unban it here.</div>
      </Modal>

      <ConfirmDialog open={Boolean(untrust)} title="Remove from trusted?" danger confirmText="Remove" message={<>The firewall rule for <strong>{untrust}</strong> is removed and fail2ban may ban it again.</>}
        onClose={() => setUntrust(null)} onConfirm={async () => { await act("untrust", untrust!); setUntrust(null); }} />
    </>
  );
}

/** Admin → Security → Firewall: ufw rules, which ports are exposed and whether that is what we want. */
export function FirewallPage() {
  const qc = useQueryClient();
  const [allow, setAllow] = useState<any>(null);
  const [del, setDel] = useState<any>(null);
  const q = useQuery({ queryKey: ["sec-firewall"], queryFn: () => api.get(`${P}/firewall`) });
  const act = async (body: any, ok: string) => {
    try { await api.post(`${P}/firewall`, body); toast.success(ok); qc.invalidateQueries({ queryKey: ["sec-firewall"] }); }
    catch (e: any) { toast.error(e.message); }
  };
  if (q.isLoading) return <Spinner />;
  const d = q.data;
  if (d && !d.installed) return (<><PageHeader title="Firewall" crumbs={[{ label: "Security" }, { label: "Firewall" }]} /><NotInstalled hint={d.install_hint} /></>);
  const danger = (d?.results || []).filter((p: any) => p.verdict === "danger");
  return (
    <>
      <PageHeader title="Firewall" crumbs={[{ label: "Security" }, { label: "Firewall" }]}
        actions={<button className="btn btn-primary" onClick={() => setAllow({ ip: d?.your_ip || "", port: "", proto: "tcp", note: "" })}><i className="ti ti-plus me-1" />Allow an IP</button>} />
      <div className="row g-3 mb-3">
        <div className="col-6 col-md-3"><StatCard label="Firewall" value={d?.status?.active ? "Active" : "Off"} icon="ti-shield" color={d?.status?.active ? "success" : "danger"} sub={d?.status?.defaults?.replace("Default: ", "")} /></div>
        <div className="col-6 col-md-3"><StatCard label="Rules" value={(d?.rules || []).length} icon="ti-list" color="primary" /></div>
        <div className="col-6 col-md-3"><StatCard label="Ports open to the world" value={(d?.results || []).filter((p: any) => p.exposed).length} icon="ti-door" color="info" /></div>
        <div className="col-6 col-md-3"><StatCard label="Risky exposures" value={danger.length} icon="ti-alert-triangle" color={danger.length ? "danger" : "success"} /></div>
      </div>

      {danger.length > 0 && <Alert kind="danger">{danger.map((p: any) => <div key={p.port}>Port <strong>{p.port}</strong> ({p.process}) — {p.note}</div>)}</Alert>}
      {(d?.not_open || []).length > 0 && <Alert kind="warning">{d.not_open.map((p: any) => <div key={p.port}>Port <strong>{p.port}</strong> is closed: {p.note}</div>)}</Alert>}

      <div className="card mb-3"><div className="card-header"><strong>Listening services</strong> <span className="fs-12 text-muted">what the server runs and whether the internet can reach it</span></div>
        <div className="card-body p-0"><table className="table table-sm mb-0 fs-13"><thead className="table-light"><tr><th>Port</th><th>Service</th><th>Reachable from internet</th><th>Assessment</th><th /></tr></thead>
          <tbody>{(d?.results || []).map((p: any) => (
            <tr key={`${p.port}-${p.proto}`} className={p.verdict === "danger" ? "table-danger" : ""}>
              <td className="fw-semibold">{p.port}<span className="text-muted fs-11">/{p.proto.replace("tcp6", "tcp").replace("udp6", "udp")}</span></td>
              <td>{p.process || "—"}</td>
              <td>{p.exposed ? <span className="badge bg-danger-light text-danger">open</span> : <span className="badge bg-success-light text-success">blocked</span>}</td>
              <td className="fs-12">{p.note}</td>
              <td className="text-end text-nowrap">
                {p.exposed ? <button className="btn btn-sm btn-outline-danger" onClick={() => act({ action: "deny_port", port: p.port, proto: p.proto.startsWith("udp") ? "udp" : "tcp" }, `Port ${p.port} closed`)}>Close</button>
                  : <button className="btn btn-sm btn-outline-secondary" onClick={() => act({ action: "allow_port", port: p.port, proto: p.proto.startsWith("udp") ? "udp" : "tcp", note: "opened from admin" }, `Port ${p.port} opened`)}>Open</button>}
              </td>
            </tr>
          ))}</tbody></table></div></div>

      <div className="card"><div className="card-header"><strong>Firewall rules</strong></div>
        <div className="card-body p-0"><table className="table table-sm mb-0 fs-13"><thead className="table-light"><tr><th style={{ width: 60 }}>#</th><th>To</th><th>Action</th><th>From</th><th /></tr></thead>
          <tbody>{(d?.rules || []).map((r: any) => (
            <tr key={r.num}><td>{r.num}</td><td>{r.to}</td><td>{r.action.includes("DENY") ? <span className="badge bg-danger-light text-danger">{r.action}</span> : <span className="badge bg-success-light text-success">{r.action}</span>}</td><td className="font-monospace">{r.from}</td>
              <td className="text-end"><button className="btn btn-sm btn-outline-danger" onClick={() => setDel(r)}><i className="ti ti-trash" /></button></td></tr>
          ))}
          {!(d?.rules || []).length && <tr><td colSpan={5} className="text-center text-muted py-3">No rules.</td></tr>}</tbody></table></div></div>

      <Modal open={Boolean(allow)} onClose={() => setAllow(null)} title={<><i className="ti ti-plus me-2 text-primary" />Allow an IP through the firewall</>} size="sm"
        footer={<><button className="btn btn-light" onClick={() => setAllow(null)}>Cancel</button><button className="btn btn-primary" onClick={async () => { await act({ action: "allow_ip", ...allow }, `${allow.ip} allowed`); setAllow(null); }}>Add rule</button></>}>
        <div className="row">
          <Field label="IP address or network" col="col-md-12"><input className="form-control font-monospace" value={allow?.ip || ""} onChange={(e) => setAllow({ ...allow, ip: e.target.value })} placeholder="203.0.113.10 or 203.0.113.0/24" /></Field>
          <Field label="Port (blank = all ports)" col="col-md-6"><input className="form-control" value={allow?.port || ""} onChange={(e) => setAllow({ ...allow, port: e.target.value })} placeholder="5432" /></Field>
          <Field label="Protocol" col="col-md-6"><select className="form-select" value={allow?.proto || "tcp"} onChange={(e) => setAllow({ ...allow, proto: e.target.value })}><option value="tcp">tcp</option><option value="udp">udp</option></select></Field>
          <Field label="Note" col="col-md-12"><input className="form-control" value={allow?.note || ""} onChange={(e) => setAllow({ ...allow, note: e.target.value })} placeholder="office static IP" /></Field>
        </div>
      </Modal>

      <ConfirmDialog open={Boolean(del)} title="Delete firewall rule?" danger confirmText="Delete" message={<>Rule {del?.num}: <code>{del?.raw}</code></>}
        onClose={() => setDel(null)} onConfirm={async () => { await act({ action: "delete", num: del.num }, "Rule deleted"); setDel(null); }} />
    </>
  );
}
