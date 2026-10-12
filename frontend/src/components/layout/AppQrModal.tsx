import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useAuth } from "@/lib/auth";
import { date } from "@/lib/format";
import { toast } from "@/lib/toast";
import { Modal } from "../ui/Modal";

export function useAppInfo(platform = "android") {
  return useQuery({ queryKey: ["app-info", platform], queryFn: () => api.get(`/api/v1/app/${platform}`), staleTime: 300_000 });
}

export async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success("Link copied");
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); toast.success("Link copied"); } catch { toast.error("Copy failed — long-press the link to copy"); }
    ta.remove();
  }
}

/** Top-bar QR icon: opens a modal with the QR code, copy-link and download buttons for the Android app. */
export function AppQrButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="d-flex align-items-center justify-content-center rounded-circle bg-transparent border-0" style={{ width: 34, height: 34 }} title="Get the mobile app" onClick={() => setOpen(true)}>
        <i className="ti ti-qrcode" style={{ fontSize: 19, color: "#5c5c5c" }} />
      </button>
      <AppQrModal open={open} onClose={() => setOpen(false)} />
    </>
  );
}

export function AppQrModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const info = useAppInfo();
  const user = useAuth((s) => s.user);
  const d = info.data;
  return (
    <Modal open={open} onClose={onClose} title={<><i className="ti ti-device-mobile me-2 text-primary" />Get the DMS mobile app</>} size="sm"
      footer={d?.available ? <><button className="btn btn-outline-secondary" onClick={() => copyText(d.page_url)}><i className="ti ti-copy me-1" />Copy link</button><a className="btn btn-primary" href={d.download_url}><i className="ti ti-download me-1" />Download APK</a></> : <button className="btn btn-light" onClick={onClose}>Close</button>}>
      {info.isLoading && <div className="text-center text-muted py-4">Loading…</div>}
      {d && !d.available && (
        <div className="text-center py-3">
          <i className="ti ti-device-mobile-off fs-1 text-muted" />
          <p className="mt-2 mb-1">No Android build has been uploaded yet.</p>
          {user?.is_platform_admin ? <Link to="/admin/mobile-app" onClick={onClose}>Upload an APK in Platform → Mobile app</Link> : <span className="text-muted fs-13">Ask your platform administrator.</span>}
        </div>
      )}
      {d?.available && (
        <div className="text-center">
          <img src={`${d.qr_url}?scale=7`} alt="QR code to download the app" style={{ width: 220, height: 220, imageRendering: "pixelated" }} className="border rounded p-1 bg-white" />
          <p className="fs-13 mt-2 mb-1">Scan with your phone camera to open the download page.</p>
          <div className="fs-12 text-muted">Android · v{d.version}{d.build_number ? ` (${d.build_number})` : ""} · {d.size_mb} MB · {date(d.released_at)}</div>
          <div className="input-group input-group-sm mt-3"><input className="form-control" readOnly value={d.page_url} onFocus={(e) => e.target.select()} /><button className="btn btn-outline-secondary" onClick={() => copyText(d.page_url)} title="Copy"><i className="ti ti-copy" /></button></div>
          <div className="fs-11 text-muted mt-2">On the phone: open the link → Download → allow "install from this source" → Install.</div>
        </div>
      )}
    </Modal>
  );
}

/** Compact "get the app" strip for public pages (login): direct APK download, QR code and copy-link. Hidden until a build is uploaded. */
export function GetAppStrip() {
  const info = useAppInfo();
  const [open, setOpen] = useState(false);
  const d = info.data;
  if (!d?.available) return null;
  return (
    <div className="text-center mt-4 pt-3 border-top">
      <div className="fs-13 text-muted mb-2"><i className="ti ti-device-mobile me-1" />Using a phone? Get the DMS Android app</div>
      <div className="d-flex flex-wrap justify-content-center gap-2">
        <a className="btn btn-sm btn-outline-primary" href={d.download_url}><i className="ti ti-download me-1" />Download app <span className="fw-normal">(v{d.version} · {d.size_mb} MB)</span></a>
        <button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => setOpen(true)}><i className="ti ti-qrcode me-1" />QR code</button>
        <button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => copyText(d.page_url)}><i className="ti ti-copy me-1" />Copy link</button>
      </div>
      <AppQrModal open={open} onClose={() => setOpen(false)} />
    </div>
  );
}
