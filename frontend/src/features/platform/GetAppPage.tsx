import { useAppInfo, copyText } from "@/components/layout/AppQrModal";
import { date } from "@/lib/format";

/** Public page the QR code points to: big download button + install steps. No login needed. */
export function GetAppPage() {
  const info = useAppInfo();
  const d = info.data;
  return (
    <div className="min-vh-100 d-flex align-items-center justify-content-center" style={{ background: "#F8F9FA", padding: 16 }}>
      <div className="card shadow-sm border-0" style={{ width: "100%", maxWidth: 440 }}>
        <div className="card-body p-4 text-center">
          <div className="avatar avatar-lg bg-primary d-inline-flex align-items-center justify-content-center rounded mb-3" style={{ width: 56, height: 56 }}><i className="ti ti-device-mobile text-white fs-3" /></div>
          <h4 className="mb-1">DMS Mobile App</h4>
          <p className="text-muted fs-13">Orders, deliveries and collections on your phone</p>
          {info.isLoading && <div className="text-muted py-3">Loading…</div>}
          {d && !d.available && <div className="alert alert-warning">No Android build is available yet. Please check back later.</div>}
          {d?.available && (
            <>
              <div className="fs-13 mb-3">Android · version <strong>{d.version}</strong>{d.build_number ? ` (${d.build_number})` : ""} · {d.size_mb} MB · {date(d.released_at)}</div>
              <a className="btn btn-primary btn-lg w-100 mb-2" href={d.download_url}><i className="ti ti-download me-2" />Download APK</a>
              <button className="btn btn-outline-secondary w-100 mb-3" onClick={() => copyText(d.page_url)}><i className="ti ti-copy me-1" />Copy this link</button>
              <div className="text-start fs-13 border rounded p-3 bg-light">
                <div className="fw-semibold mb-1">How to install</div>
                <ol className="ps-3 mb-0" style={{ listStyle: "decimal" }}>
                  <li>Tap <strong>Download APK</strong> and open the downloaded file.</li>
                  <li>If asked, allow installs from this source (browser / Files).</li>
                  <li>Tap <strong>Install</strong>, then open the app and sign in with your DMS login.</li>
                </ol>
              </div>
              {d.notes && <div className="text-start fs-12 text-muted mt-3"><strong>What's new:</strong> {d.notes}</div>}
              <div className="d-none d-md-block mt-3"><img src={`${d.qr_url}?scale=5`} alt="QR" style={{ width: 140, height: 140, imageRendering: "pixelated" }} /><div className="fs-11 text-muted">Scan on your phone</div></div>
              <div className="fs-11 text-muted mt-2 font-monospace text-break">sha256 {d.sha256}</div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
