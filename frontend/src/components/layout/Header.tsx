import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useAuth } from "@/lib/auth";
import { sideOfPath } from "@/app/guards";
import { ago } from "@/lib/format";
import { toast } from "@/lib/toast";
import { AppQrButton } from "./AppQrModal";
import { QrLoginModal } from "./QrLoginModal";

export function Header({ onToggleMini, onToggleMobile, notificationsTo = "/app/notifications" }: { onToggleMini: () => void; onToggleMobile: () => void; notificationsTo?: string }) {
  const { user, me, memberships, tenantId, setTenant, logout, refresh } = useAuth();
  const nav = useNavigate();
  const qc = useQueryClient();
  // Where to send someone depends on the shell they are in, not on whether they happen to have a rep record:
  // a user who holds both jobs is working on one side at a time, and the URL is the one thing that knows which.
  const side = sideOfPath(useLocation().pathname);
  const [notifOpen, setNotifOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [qrLogin, setQrLogin] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const unread = useQuery({ queryKey: ["notif-unread"], queryFn: () => api.get("/api/v1/me/notifications/unread-count"), enabled: Boolean(user) });
  const recent = useQuery({ queryKey: ["notif-recent"], queryFn: () => api.list("/api/v1/me/notifications", { page_size: 8 }), enabled: notifOpen });
  useEffect(() => {
    const h = (e: MouseEvent) => { if (wrap.current && !wrap.current.contains(e.target as Node)) { setNotifOpen(false); setProfileOpen(false); } };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);
  const doLogout = async () => {
    try { await api.post("/api/v1/auth/logout", { refresh }); } catch { /* ignore */ }
    logout();
    qc.clear();
    nav("/login");
  };
  const brand = me?.tenant?.branding?.app_name || me?.tenant?.display_name || (user?.is_platform_admin ? "DMS Platform" : "DMS");
  return (
    <div className="header">
      <div className="main-header" ref={wrap}>
        <div className="header-left">
          <Link to="/" className="logo"><span className="fw-bold text-primary fs-18">{brand}</span></Link>
        </div>
        <a className="mobile_btn" onClick={onToggleMobile}><span className="bar-icon"><span /><span /><span /></span></a>
        <div className="header-user">
          <div className="nav user-menu nav-list">
            <div className="me-auto d-flex align-items-center">
              <a className="btn btn-menubar me-1 cursor-pointer" onClick={onToggleMini}><i className="ti ti-arrow-bar-to-left" /></a>
              {me?.impersonating && <span className="d-inline-flex align-items-center gap-2 ms-2"><span className="badge bg-warning text-dark" title={me.impersonation_expires_at ? `This session stays open while you work (until ${new Date(me.impersonation_expires_at).toLocaleTimeString()} if you stop)` : undefined}><i className="ti ti-user-screen me-1" />Logged in as owner of {me.tenant?.display_name}</span><button className="btn btn-sm btn-outline-warning" onClick={async () => { try { await api.post("/api/v1/platform/tenants/end-impersonation"); } catch { /* ignore */ } setTenant(null); qc.clear(); nav("/admin"); }}><i className="ti ti-arrow-back-up me-1" />Return to platform admin</button></span>}
              {me?.tenant?.status === "trial" && me.tenant.plan && <span className="badge bg-info-light text-info ms-2 d-none d-md-inline">Trial · {me.tenant.plan.days_left} days left</span>}
              {me?.tenant?.status === "past_due" && <span className="badge bg-danger-light text-danger ms-2">Subscription past due</span>}
            </div>
            <div className="d-flex align-items-center gap-2">
              {/* Mobile app QR */}
              <AppQrButton /><QrLoginModal open={qrLogin} onClose={() => setQrLogin(false)} />
              {/* Notifications */}
              <div className="position-relative">
                <button type="button" className="position-relative d-flex align-items-center justify-content-center rounded-circle bg-transparent border-0" style={{ width: 34, height: 34 }} onClick={() => { setNotifOpen((o) => !o); setProfileOpen(false); }} title="Notifications">
                  <i className="ti ti-bell" style={{ fontSize: 18, color: "#5c5c5c" }} />
                  {unread.data?.unread ? <span className="position-absolute rounded-pill bg-danger text-white" style={{ top: 1, right: 1, minWidth: 16, height: 16, fontSize: 9, fontWeight: 700, lineHeight: "16px", textAlign: "center", padding: "0 3px" }}>{unread.data.unread}</span> : null}
                </button>
                {notifOpen && (
                  <div style={{ position: "absolute", right: 0, top: "calc(100% + 8px)", width: 340, zIndex: 9999, borderRadius: 10, overflow: "hidden", boxShadow: "0 8px 24px rgba(0,0,0,.12)", background: "#fff", border: "1px solid #e9ecef" }}>
                    <div className="d-flex align-items-center justify-content-between px-3 py-2 border-bottom">
                      <span className="fw-semibold fs-13"><i className="ti ti-bell text-primary me-1" />Notifications</span>
                      <a className="text-primary fs-12 cursor-pointer" onClick={async () => { await api.post("/api/v1/me/notifications/read-all"); qc.invalidateQueries({ queryKey: ["notif-unread"] }); qc.invalidateQueries({ queryKey: ["notif-recent"] }); }}>Mark all read</a>
                    </div>
                    <div style={{ maxHeight: 380, overflowY: "auto" }}>
                      {!recent.data?.results?.length && <div className="text-center text-muted py-5 fs-13"><i className="ti ti-bell-off d-block mb-2" style={{ fontSize: 28, opacity: 0.4 }} />You're all caught up</div>}
                      {recent.data?.results?.map((n: any) => (
                        <div key={n.id} className={`px-3 py-2 border-bottom cursor-pointer ${!n.read_at ? "bg-light" : ""}`} onClick={async () => { if (!n.read_at) { await api.post(`/api/v1/me/notifications/${n.id}/read`); qc.invalidateQueries({ queryKey: ["notif-unread"] }); } setNotifOpen(false); if (user?.is_platform_admin && !me?.impersonating) nav(notificationsTo); else if (n.data?.order_id) nav(`${side === "rep" ? "/rep" : "/app/sales"}/orders/${n.data.order_id}`); else if (n.data?.trip_id) nav(`/app/delivery/trips/${n.data.trip_id}`); else nav(notificationsTo); }}>
                          <div className="fs-13 fw-medium">{n.title}</div>
                          <div className="fs-12 text-muted">{n.body}</div>
                          <div className="fs-11 text-muted">{ago(n.created_at)}</div>
                        </div>
                      ))}
                    </div>
                    <div className="px-3 py-2 text-center border-top"><Link to={notificationsTo} className="fs-12" onClick={() => setNotifOpen(false)}>View all</Link></div>
                  </div>
                )}
              </div>
              {/* Profile */}
              <div className="dropdown profile-dropdown">
                <a className="dropdown-toggle d-flex align-items-center cursor-pointer" onClick={() => { setProfileOpen((o) => !o); setNotifOpen(false); }}>
                  <span className="avatar avatar-sm online"><span className="d-flex align-items-center justify-content-center rounded-circle bg-primary text-white" style={{ width: 32, height: 32, fontWeight: 700 }}>{(user?.full_name || "U").slice(0, 1).toUpperCase()}</span></span>
                </a>
                <div className={`dropdown-menu shadow-none dropdown-menu-end ${profileOpen ? "show" : ""}`} style={{ minWidth: 260 }}>
                  <div className="card mb-0">
                    <div className="card-header">
                      <div className="d-flex align-items-center">
                        <span className="avatar avatar-lg me-2 avatar-rounded"><span className="d-flex align-items-center justify-content-center rounded-circle bg-light text-dark" style={{ width: 44, height: 44 }}><i className="ti ti-user" /></span></span>
                        <div><h5 className="mb-0 fs-14">{user?.full_name}</h5><p className="fs-12 fw-medium mb-0 text-muted">{user?.email || user?.phone}</p></div>
                      </div>
                    </div>
                    <div className="card-body py-2">
                      {!user?.is_platform_admin && memberships.length > 1 && (
                        <div className="mb-2">
                          <div className="fs-11 text-muted mb-1">Company</div>
                          <select className="form-select form-select-sm" value={tenantId || ""} onChange={(e) => { setTenant(e.target.value); qc.clear(); nav(side === "rep" ? "/rep" : "/app"); toast.info("Switched company"); }}>
                            {memberships.map((m) => <option key={m.tenant_id} value={m.tenant_id}>{m.tenant_name}</option>)}
                          </select>
                        </div>
                      )}
                      {me?.impersonating && <a className="dropdown-item d-inline-flex align-items-center p-0 py-2 text-warning fw-semibold cursor-pointer" onClick={async () => { try { await api.post("/api/v1/platform/tenants/end-impersonation"); } catch { /* ignore */ } setTenant(null); qc.clear(); nav("/admin"); }}><i className="ti ti-arrow-back-up me-2" />Return to platform admin</a>}
                      <Link className="dropdown-item d-inline-flex align-items-center p-0 py-2" to={user?.is_platform_admin && !me?.impersonating ? "/admin/profile" : side === "rep" ? "/rep/profile" : "/app/profile"} onClick={() => setProfileOpen(false)}><i className="ti ti-user-circle me-2" />My profile & sessions</Link>
                      {!user?.is_platform_admin && <a className="dropdown-item d-inline-flex align-items-center p-0 py-2 cursor-pointer" onClick={() => { setProfileOpen(false); setQrLogin(true); }}><i className="ti ti-scan me-2" />Log in on phone (QR code)</a>}
                    </div>
                    <div className="card-footer py-2"><a className="dropdown-item d-inline-flex align-items-center p-0 py-2 cursor-pointer" onClick={doLogout}><i className="ti ti-logout me-2" />Logout</a></div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
