import { useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useAuth } from "@/lib/auth";
import { useBranding } from "@/lib/branding";
import { rememberSide, sideOfPath, useSides } from "@/app/guards";
import type { Side } from "@/app/guards";
import { Header } from "./Header";
import { Sidebar } from "./Sidebar";
import { SIDE_SWITCH } from "./menu";
import type { MenuSection } from "./menu";

export function AppShell({ sections, notificationsTo }: { sections: MenuSection[]; notificationsTo?: string }) {
  const brand = useBranding();
  const wl = (useAuth.getState().me?.tenant as any)?.white_label ? ((useAuth.getState().me?.tenant as any)?.branding || {}) : null;
  const [mini, setMini] = useState(() => localStorage.getItem("dms.mini") === "1");
  const [mobile, setMobile] = useState(false);
  const me = useAuth((s) => s.me);
  // /admin uses this component too, and there the question of sides does not arise (side is null).
  const side: Side | null = sideOfPath(useLocation().pathname);
  const dual = useSides().dual && side !== null;
  // Remember the side they are working on for the next sign-in, and drop a preference that no longer applies —
  // a login that has lost one of its two roles must not keep being sent to a shell it cannot open.
  useEffect(() => { if (side) rememberSide(dual ? side : null); }, [side, dual]);
  useEffect(() => {
    document.body.classList.toggle("mini-sidebar", mini);
    localStorage.setItem("dms.mini", mini ? "1" : "0");
  }, [mini]);
  useEffect(() => {
    document.querySelector(".main-wrapper")?.classList.toggle("slide-nav", mobile);
    document.documentElement.classList.toggle("menu-opened", mobile);
  }, [mobile]);
  const unread = useQuery({ queryKey: ["notif-unread"], queryFn: () => api.get("/api/v1/me/notifications/unread-count") });
  const inbox = useQuery({ queryKey: ["orders-stats-badge"], queryFn: () => api.get("/api/v1/sales-orders/stats"), enabled: Boolean(me?.tenant) && side !== "rep" && Boolean(me?.pages?.sales_orders?.view || me?.pages?.sales_inbox?.view) });
  const badges = { unread: unread.data?.unread || 0, inbox: inbox.data?.inbox || 0 };
  const announcements = useQuery({ queryKey: ["announcements"], queryFn: () => api.get("/api/v1/announcements"), staleTime: 300_000 });
  const [dismissed, setDismissed] = useState<string[]>(() => JSON.parse(localStorage.getItem("dms.dismissed") || "[]"));
  return (
    <div className="main-wrapper">
      <Header onToggleMini={() => setMini((m) => !m)} onToggleMobile={() => setMobile((m) => !m)} notificationsTo={notificationsTo} />
      <Sidebar sections={dual && side ? [...sections, SIDE_SWITCH[side === "rep" ? "office" : "rep"]] : sections} badges={badges} open={mobile} onClose={() => setMobile(false)} />
      {dual && (
        <div className="d-flex align-items-center justify-content-between border-bottom bg-light px-3 py-1" style={{ marginLeft: "var(--sidebar-width, 0)" }}>
          <span className="fs-12 text-muted">
            <i className={`ti ${side === "rep" ? "ti-device-mobile" : "ti-building"} me-1`} />You are in the <strong className="text-dark">{side === "rep" ? "rep view" : "office app"}</strong>
          </span>
          <Link to={side === "rep" ? "/app" : "/rep"} className="btn btn-sm btn-outline-primary py-0 fs-12">
            <i className={`ti ${side === "rep" ? "ti-building" : "ti-device-mobile"} me-1`} />Switch to {side === "rep" ? "the office app" : "the rep view"}
          </Link>
        </div>
      )}
      {(me as any)?.impersonating_as && (
        <div className="d-flex align-items-center justify-content-between bg-warning-subtle border-bottom px-3 py-1" style={{ marginLeft: "var(--sidebar-width, 0)" }}>
          <span className="fs-12"><i className="ti ti-eye me-1 text-warning" />You are viewing the app as <strong>{(me as any).impersonating_as}</strong> — actions you take are recorded as the owner.</span>
          <button className="btn btn-sm btn-dark py-0 fs-12" onClick={async () => {
            try { await api.post("/api/v1/users/exit-impersonation", {}); } catch { /* ignore */ }
            window.location.assign("/app");
          }}><i className="ti ti-arrow-back-up me-1" />Back to owner</button>
        </div>
      )}
      {announcements.data?.filter((a: any) => !dismissed.includes(a.id)).map((a: any) => (
        <div key={a.id} className={`alert alert-${a.level === "danger" ? "danger" : a.level === "warning" ? "warning" : "info"} border-0 rounded-0 mb-0 d-flex align-items-center justify-content-between py-2`} style={{ marginLeft: "var(--sidebar-width, 0)" }}>
          <div className="fs-13"><strong>{a.title}</strong> — {a.body}</div>
          {a.dismissible && <button className="btn-close" onClick={() => { const d = [...dismissed, a.id]; setDismissed(d); localStorage.setItem("dms.dismissed", JSON.stringify(d)); }} />}
        </div>
      ))}
      <div className="page-wrapper">
        <div className="content">
          <Outlet />
        </div>
        <div className="footer d-sm-flex align-items-center justify-content-between border-top bg-white p-3 mt-4">
          <p className="mb-0 fs-12">{wl?.footer_text || `© ${new Date().getFullYear()} ${me?.tenant?.display_name || brand?.site_name || "DMS"}`}</p>
          <p className="mb-0 fs-12 text-muted">{!me?.tenant || wl?.hide_powered_by ? brand?.platform?.site_tagline || brand?.site_tagline || "" : `powered by ${brand?.platform?.site_name || "DMS"}`}</p>
        </div>
      </div>
    </div>
  );
}
