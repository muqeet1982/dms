import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { toast } from "@/lib/toast";
import { Modal } from "../ui/Modal";
import { useAppInfo } from "./AppQrModal";

type Code = { id: string; url: string; deep_link: string; qr_png: string; expires_at: string; expires_in: number };

/**
 * "Log in on phone": shows a one-time QR code; scanning it in the DMS app signs that phone in without a password.
 * Polls the code's status so the desktop sees "Signed in on <device>" the moment the phone redeems it.
 */
export function QrLoginModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [code, setCode] = useState<Code | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [left, setLeft] = useState(0);
  const info = useAppInfo();
  const create = async () => {
    setError(null); setCode(null);
    try { const c = await api.post("/api/v1/auth/qr-login/create", {}); setCode(c); setLeft(c.expires_in); } catch (e: any) { setError(e.message); }
  };
  useEffect(() => { if (open) void create(); else { setCode(null); setError(null); } }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!code) return;
    const t = setInterval(() => setLeft((s) => Math.max(0, Math.round((new Date(code.expires_at).getTime() - Date.now()) / 1000))), 1000);
    return () => clearInterval(t);
  }, [code]);
  const status = useQuery({ queryKey: ["qr-login-status", code?.id], queryFn: () => api.get("/api/v1/auth/qr-login/status", { id: code!.id }), enabled: Boolean(code) && open, refetchInterval: (q) => (q.state.data?.status === "pending" ? 2000 : false) });
  const st = status.data?.status as string | undefined;
  useEffect(() => { if (st === "used") { toast.success(`Phone signed in${status.data?.redeemed_device ? ` (${status.data.redeemed_device})` : ""}`); const t = setTimeout(onClose, 2500); return () => clearTimeout(t); } }, [st]); // eslint-disable-line react-hooks/exhaustive-deps
  const expired = left <= 0 || st === "expired";
  const mm = String(Math.floor(left / 60)), ss = String(left % 60).padStart(2, "0");
  return (
    <Modal open={open} onClose={onClose} title={<><i className="ti ti-scan me-2 text-primary" />Log in on your phone</>} size="sm"
      footer={<><button className="btn btn-outline-secondary" onClick={create}><i className="ti ti-refresh me-1" />New code</button><button className="btn btn-light" onClick={onClose}>Close</button></>}>
      {error && <div className="alert alert-danger py-2 fs-13">{error}</div>}
      {!code && !error && <div className="text-center text-muted py-4">Generating code…</div>}
      {code && (
        <div className="text-center">
          {st === "used" ? (
            <div className="py-3"><i className="ti ti-circle-check fs-1 text-success" /><h5 className="mt-2 mb-1">Signed in on your phone</h5><div className="fs-13 text-muted">{status.data?.redeemed_device || "Mobile app"} · this code can't be used again</div></div>
          ) : (
            <>
              <div className="position-relative d-inline-block">
                <img src={code.qr_png} alt="Scan to log in" style={{ width: 232, height: 232, imageRendering: "pixelated", opacity: expired ? 0.15 : 1 }} className="border rounded p-1 bg-white" />
                {expired && <div className="position-absolute top-50 start-50 translate-middle"><div className="fw-semibold">Code expired</div><button className="btn btn-sm btn-primary mt-2" onClick={create}>Get a new code</button></div>}
              </div>
              <div className="fs-13 mt-3 mb-1">Open the <strong>DMS app</strong> on your phone → <strong>Scan QR to log in</strong>. No username or password needed.</div>
              <div className="fs-12 text-muted">{expired ? "Codes last 5 minutes." : <>Valid for <span className="font-monospace fw-semibold">{mm}:{ss}</span> · one-time use · anyone who scans it signs in as you</>}</div>
              <div className="fs-12 mt-3 border-top pt-2">
                No app on the phone yet? Scan with the phone <em>camera</em> instead — it opens a page with the download link — or get it at{" "}
                <Link to="/get-app" target="_blank">{info.data?.page_url?.replace(/^https?:\/\//, "") || "/get-app"}</Link>.
              </div>
            </>
          )}
        </div>
      )}
    </Modal>
  );
}
