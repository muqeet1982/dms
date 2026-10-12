import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useAppInfo, copyText } from "@/components/layout/AppQrModal";

const isAndroid = () => /android/i.test(navigator.userAgent);
const isPhone = () => /android|iphone|ipad|ipod/i.test(navigator.userAgent);

/**
 * Public landing page encoded in the "log in on phone" QR code (https://…/app-login?t=…).
 * Scanned with the DMS app → the app handles it itself. Scanned with the phone camera → this page opens:
 * it tries to hand the token to the installed app (deep link / Android intent), otherwise offers the APK download.
 */
export function AppLoginPage() {
  const [sp] = useSearchParams();
  const token = sp.get("t") || "";
  const info = useAppInfo();
  const d = info.data;
  const [tried, setTried] = useState(false);
  const status = useQuery({ queryKey: ["qr-login-public-status", token], queryFn: () => api.get("/api/v1/auth/qr-login/status", { t: token }), enabled: Boolean(token), refetchInterval: (q) => (q.state.data?.status === "pending" ? 3000 : false) });
  const st = status.data?.status as string | undefined;
  const scheme = d?.scheme || "dms";
  const deepLink = `${scheme}://login?t=${encodeURIComponent(token)}`;
  // Android Chrome: an intent URL opens the app when installed and falls back to the download page when it is not.
  const intentUrl = useMemo(() => {
    const fallback = encodeURIComponent(`${window.location.origin}/get-app`);
    return d?.package_name ? `intent://login?t=${encodeURIComponent(token)}#Intent;scheme=${scheme};package=${d.package_name};S.browser_fallback_url=${fallback};end` : deepLink;
  }, [d?.package_name, scheme, token, deepLink]);
  const openApp = () => { setTried(true); window.location.href = isAndroid() ? intentUrl : deepLink; };
  useEffect(() => { if (token && isPhone() && d && st === "pending" && !tried) { const t = setTimeout(openApp, 600); return () => clearTimeout(t); } }, [token, d, st]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="min-vh-100 d-flex align-items-center justify-content-center" style={{ background: "#F8F9FA", padding: 16 }}>
      <div className="card shadow-sm border-0" style={{ width: "100%", maxWidth: 440 }}>
        <div className="card-body p-4 text-center">
          <div className="avatar avatar-lg bg-primary d-inline-flex align-items-center justify-content-center rounded mb-3" style={{ width: 56, height: 56 }}><i className="ti ti-scan text-white fs-3" /></div>
          <h4 className="mb-1">Log in to the DMS app</h4>
          {!token && <p className="text-muted fs-13 mb-0">This link is incomplete. Open the DMS web app → your profile → <strong>Log in on phone</strong> and scan the code again.</p>}
          {token && st === "used" && <div className="alert alert-success fs-13"><i className="ti ti-circle-check me-1" />This code was already used to sign in{status.data?.redeemed_device ? ` on ${status.data.redeemed_device}` : ""}. Open the app — you are logged in.</div>}
          {token && (st === "expired" || st === "invalid") && <div className="alert alert-warning fs-13"><i className="ti ti-clock-x me-1" />This code has expired (codes last 5 minutes). On the computer, open your profile → <strong>Log in on phone</strong> to get a new one.</div>}
          {token && st === "pending" && (
            <>
              {!isPhone() && <div className="alert alert-info fs-13">Open this link on your <strong>phone</strong> — or scan the QR code with the DMS app there.</div>}
              <p className="text-muted fs-13">If the DMS app is installed it opens and signs you in automatically. Otherwise install it first:</p>
              <button className="btn btn-primary btn-lg w-100 mb-2" onClick={openApp}><i className="ti ti-device-mobile me-2" />Open in the DMS app</button>
              {d?.available && <a className="btn btn-outline-primary w-100 mb-2" href={d.download_url}><i className="ti ti-download me-1" />Don't have the app? Download APK ({d.size_mb} MB)</a>}
              {d && !d.available && <div className="alert alert-warning fs-13">No Android build is published yet — ask your administrator.</div>}
              <button className="btn btn-outline-secondary w-100 mb-3" onClick={() => copyText(window.location.href)}><i className="ti ti-copy me-1" />Copy this link</button>
              <div className="text-start fs-13 border rounded p-3 bg-light">
                <div className="fw-semibold mb-1">After installing</div>
                <ol className="ps-3 mb-0" style={{ listStyle: "decimal" }}>
                  <li>Open the app and tap <strong>Scan QR to log in</strong>, then scan the code on the computer screen again.</li>
                  <li>Or come back to this page and tap <strong>Open in the DMS app</strong>.</li>
                  <li>Codes last 5 minutes — if it expired, generate a new one from your profile menu on the computer.</li>
                </ol>
              </div>
            </>
          )}
          <div className="fs-12 text-muted mt-3"><Link to="/get-app">About the app</Link> · <Link to="/login">Web login</Link></div>
        </div>
      </div>
    </div>
  );
}
