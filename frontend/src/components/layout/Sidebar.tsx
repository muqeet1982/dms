import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { sideOfPath } from "@/app/guards";
import { useAuth } from "@/lib/auth";
import { useBranding } from "@/lib/branding";
import { usePerms } from "@/lib/perms";
import type { MenuItem, MenuSection } from "./menu";

export function Sidebar({ sections, badges, open, onClose }: { sections: MenuSection[]; badges?: Record<string, number>; open: boolean; onClose: () => void }) {
  const { can, feature } = usePerms();
  const me = useAuth((s) => s.me);
  const user = useAuth((s) => s.user);
  const loc = useLocation();
  // accordion: one group open at a time, so the menu stays short enough not to scroll
  const [openKey, setOpenKey] = useState<string | null>(null);
  const allowed = (it: MenuItem) =>
    (!it.page || Boolean(me?.pages?.[it.page]?.view)) && (!it.perm || can(it.perm)) && (!it.feature || feature(it.feature));
  // a group is worth showing only if something inside it is
  const visible = (it: MenuItem): boolean => (it.children?.length ? allowed(it) && it.children.some(visible) : allowed(it));

  useEffect(() => {
    // auto-open the submenu that contains the current route; on navigation and on reload this is the one left open
    for (const s of sections) for (const it of s.items) if (it.children?.some((c) => c.to && loc.pathname.startsWith(c.to))) setOpenKey(it.label);
  }, [loc.pathname, sections]);

  // white-label (a platform-granted feature) lets the tenant replace the product name/logo; otherwise the platform's
  const b = useBranding();
  const wl = me?.tenant?.white_label ? me?.tenant?.branding || {} : null;
  const brand = wl?.app_name || (wl ? me?.tenant?.display_name : "") || b?.site_name || "DMS";
  const logo = wl?.logo || (wl ? me?.tenant?.logo : "") || b?.logo_url;
  return (
    <>
      <div className={`sidebar ${open ? "opened" : ""}`} id="sidebar">
        <div className="sidebar-logo">
          <NavLink to="/" className="logo logo-normal">{logo ? <img src={logo} alt={brand} style={{ maxHeight: 48 }} /> : <span className="fw-bold text-primary fs-20">{brand}</span>}</NavLink>
          <NavLink to="/" className="logo-small"><span className="fw-bold text-primary fs-18">{brand.slice(0, 1)}</span></NavLink>
        </div>
        <div className="modern-profile p-3 pb-0">
          <div className="text-center rounded bg-light p-3 mb-3 user-profile">
            <div className="avatar avatar-lg online mb-2"><span className="d-flex align-items-center justify-content-center rounded-circle bg-primary text-white" style={{ width: 48, height: 48, fontSize: 20, fontWeight: 700 }}>{(user?.full_name || "U").slice(0, 1).toUpperCase()}</span></div>
            <h6 className="fs-12 fw-normal mb-1">{user?.full_name}</h6>
            <p className="fs-10 mb-0 text-muted">{me?.is_owner ? "Owner" : sideOfPath(loc.pathname) === "rep" && me?.supplier_rep ? me.supplier_rep.supplier_name : user?.is_platform_admin ? "Platform admin" : me?.memberships?.find((m) => m.tenant_id === me?.tenant?.id)?.roles.join(", ")}</p>
          </div>
        </div>
        <div className="sidebar-inner">
          <div id="sidebar-menu" className="sidebar-menu">
            <ul>
              {sections.map((s) => {
                const items = s.items.filter(visible);
                if (!items.length) return null;
                return (
                  <li key={s.title} className="mb-1">
                    <li className="menu-title"><span>{s.title}</span></li>
                    <ul>
                      {items.map((it) => {
                        if (it.children) {
                          const kids = it.children.filter(visible);
                          if (!kids.length) return null;
                          const isOpen = openKey === it.label;
                          const active = kids.some((c) => c.to && loc.pathname.startsWith(c.to));
                          return (
                            <li key={it.label} className={`submenu ${isOpen ? "open" : ""}`}>
                              <a className={`${active || isOpen ? "subdrop" : ""} cursor-pointer`} onClick={() => setOpenKey((k) => (k === it.label ? null : it.label))}>
                                <i className={`ti ${it.icon}`} /><span>{it.label}</span><span className="menu-arrow" />
                              </a>
                              <ul>
                                {kids.map((c) => (
                                  <li key={c.to}>
                                    <NavLink to={c.to!} end={c.end} className={({ isActive }) => (isActive ? "active" : "")} onClick={onClose}>
                                      {c.label}
                                      {c.badgeKey && badges?.[c.badgeKey] ? <span className="badge bg-primary rounded-pill ms-2 fs-10">{badges[c.badgeKey]}</span> : null}
                                    </NavLink>
                                  </li>
                                ))}
                              </ul>
                            </li>
                          );
                        }
                        return (
                          <li key={it.to}>
                            <NavLink to={it.to!} end={it.end} className={({ isActive }) => (isActive ? "active" : "")} onClick={onClose}>
                              <i className={`ti ${it.icon}`} /><span>{it.label}</span>
                              {it.badgeKey && badges?.[it.badgeKey] ? <span className="badge bg-danger rounded-pill ms-auto fs-10">{badges[it.badgeKey]}</span> : null}
                            </NavLink>
                          </li>
                        );
                      })}
                    </ul>
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      </div>
      <div className={`sidebar-overlay ${open ? "opened" : ""}`} onClick={onClose} />
    </>
  );
}
