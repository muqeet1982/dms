import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "@/api/client";

/**
 * The print view every Print button opens: a normal page of the site (no blob:… address) showing the
 * document, with Print / Download PDF / Close on top. The toolbar is never part of the output — printing
 * prints the embedded document itself, and the download is the PDF the server rendered.
 */
export function PrintViewPage() {
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const src = sp.get("u") || "";
  const [url, setUrl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const frame = useRef<HTMLIFrameElement>(null);
  useEffect(() => {
    let alive = true;
    let obj = "";
    if (!src.startsWith("/api/")) { setErr("Nothing to print."); return; }
    api.blobOf(src).then((b: Blob) => { if (!alive) return; obj = URL.createObjectURL(b); setUrl(obj); })
      .catch((e: any) => alive && setErr(e.message || "Could not load the document."));
    return () => { alive = false; if (obj) URL.revokeObjectURL(obj); };
  }, [src]);
  const doPrint = () => {
    try { frame.current?.contentWindow?.focus(); frame.current?.contentWindow?.print(); }
    catch { window.print(); }
  };
  const doDownload = () => {
    if (!url) return;
    const a = document.createElement("a");
    const last = src.split("?")[0].split("/").filter(Boolean).pop() || "document";
    a.href = url;
    a.download = /\.[a-z0-9]{2,5}$/i.test(last) ? last : `${last}.pdf`;
    a.click();
  };
  const doClose = () => { window.close(); setTimeout(() => nav(-1), 150); };
  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: "#525659" }}>
      <style>{`@media print { .print-toolbar { display: none !important; } .print-frame { border: 0 !important; } }`}</style>
      <div className="print-toolbar d-flex align-items-center gap-2 px-3 py-2 bg-white border-bottom shadow-sm">
        <span className="fw-semibold me-auto"><i className="ti ti-file-text me-2" />Print View</span>
        <button className="btn btn-primary btn-sm" disabled={!url} onClick={doPrint}><i className="ti ti-printer me-1" />Print</button>
        <button className="btn btn-outline-primary btn-sm" disabled={!url} onClick={doDownload}><i className="ti ti-download me-1" />Download PDF</button>
        <button className="btn btn-outline-secondary btn-sm" onClick={doClose}><i className="ti ti-x me-1" />Close</button>
      </div>
      {err ? <div className="m-auto text-white fs-14">{err}</div>
        : !url ? <div className="m-auto text-white fs-14">Preparing the document…</div>
        : <iframe ref={frame} className="print-frame" title="document" src={url} style={{ flex: 1, border: 0, width: "100%" }} />}
    </div>
  );
}
