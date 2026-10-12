import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getFreshAccess } from "@/api/client";
import { datetime } from "@/lib/format";
import { toast } from "@/lib/toast";
import { Alert, Field, PageHeader, Spinner, StatCard } from "@/components/ui/Bits";
import { ConfirmDialog } from "@/components/ui/Modal";
import { copyText, useAppInfo } from "@/components/layout/AppQrModal";

const P = "/api/v1/platform/app-releases";

export function MobileAppPage() {
  const qc = useQueryClient();
  const releases = useQuery({ queryKey: ["app-releases"], queryFn: () => api.list(P, { page_size: 100 }) });
  const info = useAppInfo();
  const fileRef = useRef<HTMLInputElement>(null);
  const [form, setForm] = useState({ version: "", build_number: "", notes: "", min_supported_version: "", is_current: true });
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [del, setDel] = useState<any>(null);
  const [purge, setPurge] = useState(false);
  const storage = useQuery({ queryKey: ["app-storage"], queryFn: () => api.get(`${P}/storage`) });
  const config = useQuery({ queryKey: ["app-config"], queryFn: () => api.get(`${P}/config`) });
  const [cfg, setCfg] = useState<any>(null);
  const identity = cfg ?? (config.data ? { android_package: config.data.android_package, scheme: config.data.scheme, fingerprints: (config.data.sha256_fingerprints || []).join("\n") } : null);
  const oldCount = releases.data?.results.filter((r: any) => !r.is_current).length ?? 0;
  const refresh = () => { qc.invalidateQueries({ queryKey: ["app-releases"] }); qc.invalidateQueries({ queryKey: ["app-info"] }); qc.invalidateQueries({ queryKey: ["app-storage"] }); };

  const upload = () => {
    if (!file || !form.version) return toast.error("Choose the .apk file and enter a version");
    const fd = new FormData();
    fd.append("platform", "android"); fd.append("version", form.version); fd.append("notes", form.notes); fd.append("min_supported_version", form.min_supported_version); fd.append("is_current", String(form.is_current));
    if (form.build_number) fd.append("build_number", form.build_number);
    fd.append("file", file);
    // XMLHttpRequest for upload progress (APKs are large). Tokens live 15 min, so refresh first and retry once on 401.
    const send = async (retry: boolean) => {
      const access = await getFreshAccess(!retry); // second attempt (after a 401): force a token refresh
      if (!access) { setProgress(null); toast.error("Your session has expired — please sign in again"); return; }
      const xhr = new XMLHttpRequest();
      xhr.open("POST", P);
      xhr.setRequestHeader("Authorization", `Bearer ${access}`);
      xhr.upload.onprogress = (e) => e.lengthComputable && setProgress(Math.round((e.loaded / e.total) * 100));
      xhr.onload = async () => {
        if (xhr.status >= 200 && xhr.status < 300) { setProgress(null); toast.success(`Version ${form.version} uploaded and set as current`); setFile(null); if (fileRef.current) fileRef.current.value = ""; setForm({ ...form, version: "", build_number: "", notes: "" }); refresh(); return; }
        if (xhr.status === 401 && retry) { setProgress(0); await send(false); return; }
        setProgress(null);
        if (xhr.status === 413) return toast.error("File too large for the server upload limit (320 MB)");
        try { toast.error(JSON.parse(xhr.responseText).detail || `Upload failed (${xhr.status})`); } catch { toast.error(`Upload failed (${xhr.status})`); }
      };
      xhr.onerror = () => { setProgress(null); toast.error("Upload failed (network)"); };
      xhr.send(fd);
    };
    setProgress(0);
    void send(true);
  };
  const cur = info.data;
  return (
    <>
      <PageHeader title="Mobile app (Android APK)" crumbs={[]} />
      <Alert kind="info">Upload each new Android build here. The <strong>current</strong> build is what the QR code (top-bar icon) and the public link <code>{cur?.page_url}</code> serve to phones. Users install it directly (sideload) — no Play Store needed.</Alert>
      <div className="row g-3 mb-3">
        <div className="col-md-7">
          <div className="card h-100"><div className="card-header"><strong>Upload a new build</strong></div><div className="card-body">
            <div className="row">
              <Field label="APK file *" col="col-md-12"><input ref={fileRef} type="file" accept=".apk,.aab,application/vnd.android.package-archive" className="form-control" onChange={(e) => setFile(e.target.files?.[0] || null)} />{file && <div className="form-text">{file.name} · {(file.size / 1024 / 1024).toFixed(1)} MB</div>}</Field>
              <Field label="Version *" col="col-md-4"><input className="form-control" placeholder="1.0.0" value={form.version} onChange={(e) => setForm({ ...form, version: e.target.value })} /></Field>
              <Field label="Build number" col="col-md-4"><input type="number" className="form-control" value={form.build_number} onChange={(e) => setForm({ ...form, build_number: e.target.value })} /></Field>
              <Field label="Min supported version" col="col-md-4"><input className="form-control" placeholder="optional" value={form.min_supported_version} onChange={(e) => setForm({ ...form, min_supported_version: e.target.value })} /></Field>
              <Field label="Release notes" col="col-md-12"><textarea className="form-control" rows={3} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} placeholder="What changed in this build" /></Field>
              <div className="col-md-12 mb-3"><div className="form-check"><input className="form-check-input" type="checkbox" id="cur" checked={form.is_current} onChange={(e) => setForm({ ...form, is_current: e.target.checked })} /><label htmlFor="cur" className="form-check-label">Make this the current build (served by the QR / download link)</label></div></div>
            </div>
            {progress !== null && <div className="progress mb-3" style={{ height: 10 }}><div className="progress-bar bg-primary" style={{ width: `${progress}%` }} /></div>}
            <button className="btn btn-primary" disabled={progress !== null || !file || !form.version} onClick={upload}><i className="ti ti-upload me-1" />{progress !== null ? `Uploading… ${progress}%` : "Upload APK"}</button>
          </div></div>
        </div>
        <div className="col-md-5">
          <div className="card h-100"><div className="card-header"><strong>Current build & QR code</strong></div><div className="card-body text-center">
            {info.isLoading && <Spinner />}
            {cur && !cur.available && <div className="text-muted py-4"><i className="ti ti-device-mobile-off fs-1 d-block mb-2 opacity-25" />No build uploaded yet</div>}
            {cur?.available && <>
              <img src={`${cur.qr_url}?scale=7`} alt="QR" style={{ width: 200, height: 200, imageRendering: "pixelated" }} className="border rounded p-1 bg-white" />
              <div className="fw-semibold mt-2">v{cur.version}{cur.build_number ? ` (${cur.build_number})` : ""}</div>
              <div className="fs-12 text-muted">{cur.size_mb} MB · {cur.file_name} · uploaded {datetime(cur.released_at)}</div>
              <div className="input-group input-group-sm mt-3"><input className="form-control" readOnly value={cur.page_url} onFocus={(e) => e.target.select()} /><button className="btn btn-outline-secondary" onClick={() => copyText(cur.page_url)}><i className="ti ti-copy me-1" />Copy</button></div>
              <div className="d-flex gap-2 justify-content-center mt-3"><a className="btn btn-primary" href={cur.download_url}><i className="ti ti-download me-1" />Download APK</a><a className="btn btn-outline-secondary" href={`${cur.qr_url}?scale=12`} download="dms-app-qr.png"><i className="ti ti-qrcode me-1" />Save QR</a></div>
              <div className="fs-11 text-muted mt-2 font-monospace text-break">sha256 {cur.sha256}</div>
            </>}
          </div></div>
        </div>
      </div>
      <div className="row g-3 mb-3"><div className="col-6 col-md-3"><StatCard label="Builds uploaded" value={releases.data?.count ?? "—"} icon="ti-stack-2" color="primary" /></div><div className="col-6 col-md-3"><StatCard label="Downloads (current)" value={releases.data?.results.find((r: any) => r.is_current)?.downloads ?? 0} icon="ti-download" color="success" /></div><div className="col-6 col-md-3"><StatCard label="APK files on server" value={storage.data ? `${storage.data.count} · ${storage.data.total_mb} MB` : "—"} icon="ti-database" color={storage.data?.orphans ? "warning" : "info"} sub={storage.data?.orphans ? `${storage.data.orphans} orphaned file(s) — use "Delete old builds"` : "deleting a build removes its file"} /></div></div>
      <div className="card mb-3"><div className="card-header"><strong>App identity — deep links &amp; "Log in on phone"</strong></div><div className="card-body">
        <div className="fs-12 text-muted mb-3">The web app's <em>Log in on phone (QR code)</em> encodes <code>{config.data?.app_login_url || "…/app-login"}?t=…</code>. With the package name and signing fingerprint below, Android opens that link straight in the app (App Links via <a href={config.data?.assetlinks_url} target="_blank" rel="noreferrer">/.well-known/assetlinks.json</a>) and the fallback page can use an intent link. The mobile developer provides these values (see <code>mobile-app.md</code>).</div>
        {identity && <div className="row">
          <Field label="Android package name" col="col-md-4"><input className="form-control" value={identity.android_package} placeholder="pk.invoiceflow.dms" onChange={(e) => setCfg({ ...identity, android_package: e.target.value })} /></Field>
          <Field label="URL scheme" col="col-md-2"><input className="form-control" value={identity.scheme} placeholder="dms" onChange={(e) => setCfg({ ...identity, scheme: e.target.value })} /></Field>
          <Field label="Signing certificate SHA-256 fingerprint(s), one per line" col="col-md-6"><textarea className="form-control font-monospace fs-12" rows={2} value={identity.fingerprints} placeholder="AA:BB:CC:…" onChange={(e) => setCfg({ ...identity, fingerprints: e.target.value })} /></Field>
        </div>}
        <div className="d-flex align-items-center gap-3"><button className="btn btn-primary" disabled={!cfg} onClick={async () => { try { await api.patch(`${P}/config`, { android_package: identity.android_package, scheme: identity.scheme, sha256_fingerprints: identity.fingerprints }); toast.success("App identity saved"); setCfg(null); qc.invalidateQueries({ queryKey: ["app-config"] }); qc.invalidateQueries({ queryKey: ["app-info"] }); } catch (e: any) { toast.error(e.message); } }}><i className="ti ti-device-floppy me-1" />Save</button><span className={`badge ${config.data?.assetlinks_ready ? "bg-success-light text-success" : "bg-warning-light text-warning"}`}>{config.data?.assetlinks_ready ? "App Links ready" : "App Links not configured — camera scans fall back to the download page"}</span></div>
      </div></div>
      <div className="card"><div className="card-header d-flex justify-content-between align-items-center"><strong>All builds</strong>{(oldCount > 0 || Boolean(storage.data?.orphans)) && <button className="btn btn-sm btn-outline-danger" onClick={() => setPurge(true)}><i className="ti ti-trash me-1" />Delete old builds (keep current)</button>}</div><div className="card-body p-0">
        <table className="table align-middle mb-0 fs-13"><thead className="table-light"><tr><th>Version</th><th>File</th><th className="num">Size</th><th>Uploaded</th><th>By</th><th className="num">Downloads</th><th>Notes</th><th>Status</th><th /></tr></thead>
          <tbody>{releases.data?.results.map((r: any) => <tr key={r.id}><td className="fw-semibold">{r.version}{r.build_number ? <span className="text-muted fw-normal"> ({r.build_number})</span> : ""}</td><td className="text-muted">{r.file_name}</td><td className="num">{r.size_mb} MB</td><td>{datetime(r.created_at)}</td><td>{r.uploaded_by_name}</td><td className="num">{r.downloads}</td><td className="text-muted" style={{ maxWidth: 260 }}>{r.notes}</td><td>{r.is_current ? <span className="badge bg-success-light text-success border border-success">current</span> : <span className="badge bg-secondary-light text-secondary">archived</span>}</td>
            <td className="text-end text-nowrap">{!r.is_current && <button className="btn btn-sm btn-outline-primary me-1" onClick={async () => { await api.post(`${P}/${r.id}/set-current`); toast.success(`v${r.version} is now current`); refresh(); }}>Make current</button>}<button className="btn btn-sm btn-outline-danger" onClick={() => setDel(r)}><i className="ti ti-trash" /></button></td></tr>)}
          {releases.data && !releases.data.results.length && <tr><td colSpan={9} className="text-muted text-center py-4">No builds yet — upload the first APK above.</td></tr>}</tbody></table>
      </div></div>
      <ConfirmDialog open={purge} title="Delete old builds" confirmText="Delete" message={<>Delete <strong>{oldCount}</strong> build(s) that are not current and remove their APK files{storage.data?.orphans ? <> plus {storage.data.orphans} orphaned file(s)</> : null} from the server? The current build stays.</>} danger onClose={() => setPurge(false)} onConfirm={async () => { try { const r = await api.post(`${P}/purge-old`, { platform: "android" }); toast.success(`${r.deleted} build(s) deleted, ${r.freed_mb} MB freed${r.orphans_removed ? `, ${r.orphans_removed} orphan file(s) removed` : ""}`); setPurge(false); refresh(); } catch (e: any) { toast.error(e.message); } }} />
      <ConfirmDialog open={Boolean(del)} title="Delete build" confirmText="Delete" message={<>Delete <strong>v{del?.version}</strong> ({del?.file_name}, {del?.size_mb} MB)? The APK file is removed from the server too.</>} danger onClose={() => setDel(null)} onConfirm={async () => { try { await api.delete(`${P}/${del.id}`); toast.success("Deleted"); refresh(); } catch (e: any) { toast.error(e.message); } setDel(null); }} />
    </>
  );
}
