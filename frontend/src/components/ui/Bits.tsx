import { labelCase } from "@/lib/format";
import { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { money, titleCase } from "@/lib/format";

/** From this many lines on, a document's line table scrolls in a fixed-height box instead of growing the page. */
export const LINES_SCROLL_FROM = 10;

const STATUS_COLORS: Record<string, string> = {
  draft: "secondary", submitted: "info", under_review: "info", confirmed: "primary", on_hold_credit: "warning", on_hold_stock: "warning", dispatched: "purple", partially_delivered: "purple",
  delivered: "success", invoiced: "success", cancelled: "danger", rejected: "danger", posted: "success", partially_paid: "warning", paid: "success", overdue: "danger", pending: "secondary", loaded: "info",
  failed: "danger", returned: "warning", planned: "secondary", loading: "info", out_for_delivery: "purple", completed: "success", closed: "dark", sent: "info", partially_received: "warning", received: "success",
  requested: "info", approved: "success", in_transit: "purple", pending_approval: "warning", open: "info", counting: "info", review: "warning", active: "success", inactive: "secondary", blocked: "danger",
  trial: "info", past_due: "warning", suspended: "danger", archived: "dark", held_by_rep: "warning", settled: "success", cleared: "success", deposited: "info", bounced: "danger", issued: "primary", partially_issued: "warning", picking: "info", partial: "warning", void: "dark",
  reversed: "dark", running: "info", done: "success", expired: "danger", expiring: "warning", low_stock: "warning", negative: "danger", dead_stock: "secondary", drift: "danger", reorder: "info",
};

export function StatusBadge({ status, label }: { status?: string | null; label?: string }) {
  if (!status) return <span className="text-muted">—</span>;
  const c = STATUS_COLORS[status] || "secondary";
  return <span className={`badge bg-${c}-light text-${c} border border-${c} fs-11`}>{label || titleCase(status)}</span>;
}

export function Money({ value, className = "", bold }: { value: any; className?: string; bold?: boolean }) {
  return <span className={`${bold ? "fw-semibold " : ""}${className}`}>{money(value)}</span>;
}

export function StatCard({ label, value, icon, color = "primary", sub, to }: { label: string; value: ReactNode; icon: string; color?: string; sub?: ReactNode; to?: string }) {
  const body = (
    <div className="card bg-white stat-card border shadow-none h-100 mb-0">
      <div className="card-body">
        <div className="d-flex align-items-center justify-content-between gap-3 flex-wrap">
          <div>
            <p className="fs-13 fw-medium mb-1 text-muted">{label}</p>
            <h3 className="fs-20 mb-0">{value}</h3>
            {sub && <div className="fs-12 text-muted mt-1">{sub}</div>}
          </div>
          <div className={`avatar avatar-lg bg-${color} d-flex align-items-center justify-content-center`}>
            <i className={`ti ${icon} text-white`} />
          </div>
        </div>
      </div>
    </div>
  );
  return to ? <Link to={to} className="text-reset text-decoration-none d-block h-100">{body}</Link> : body;
}

export function EmptyState({ icon = "ti-inbox", text = "Nothing here yet.", action }: { icon?: string; text?: ReactNode; action?: ReactNode }) {
  return (
    <div className="text-center text-muted py-5">
      <i className={`ti ${icon} fs-1 d-block mb-2 opacity-25`} />
      <div>{text}</div>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function Spinner({ text = "Loading…" }: { text?: string }) {
  return (
    <div className="d-flex align-items-center gap-2 text-muted py-4 justify-content-center">
      <div className="spinner-border spinner-border-sm text-primary" role="status" />
      <span className="fs-13">{text}</span>
    </div>
  );
}

export function Alert({ kind = "info", children, icon }: { kind?: "info" | "warning" | "danger" | "success" | "secondary"; children: ReactNode; icon?: string }) {
  return (
    <div className={`alert alert-${kind} d-flex align-items-center gap-2 py-2`}>
      <i className={`ti ${icon || { info: "ti-info-circle", warning: "ti-alert-triangle", danger: "ti-alert-circle", success: "ti-circle-check", secondary: "ti-info-circle" }[kind]} flex-shrink-0`} />
      <div className="flex-grow-1">{children}</div>
    </div>
  );
}

export function Field({ label, children, hint, col = "" }: { label?: ReactNode; children: ReactNode; hint?: ReactNode; col?: string }) {
  return (
    <div className={`${col ? col + " " : ""}mb-3`}>
      {label && <label className="form-label">{typeof label === "string" ? labelCase(label) : label}</label>}
      {children}
      {hint && <div className="form-text">{hint}</div>}
    </div>
  );
}

export function InfoItem({ label, children, col = "col-md-3" }: { label: string; children: ReactNode; col?: string }) {
  return (
    <div className={col}>
      <p className="text-muted fs-11 mb-1">{labelCase(label)}</p>
      <div className="mb-0 fw-medium">{children ?? "—"}</div>
    </div>
  );
}

export function Tabs({ tabs, active, onChange }: { tabs: { key: string; label: ReactNode; count?: number }[]; active: string; onChange: (k: string) => void }) {
  return (
    <ul className="nav nav-tabs nav-tabs-bottom mb-3">
      {tabs.map((t) => (
        <li className="nav-item" key={t.key}>
          <a className={`nav-link ${active === t.key ? "active" : ""} cursor-pointer`} onClick={() => onChange(t.key)}>
            {t.label}
            {t.count !== undefined && <span className="badge bg-light text-dark ms-1">{t.count}</span>}
          </a>
        </li>
      ))}
    </ul>
  );
}

export function Dropdown({ label, items, className = "btn btn-outline-secondary" }: { label: ReactNode; items: { label: ReactNode; onClick?: () => void; to?: string; danger?: boolean; hidden?: boolean }[]; className?: string }) {
  return (
    <div className="dropdown d-inline-block">
      <button className={`${className} dropdown-toggle`} type="button" onClick={(e) => e.currentTarget.nextElementSibling?.classList.toggle("show")} onBlur={(e) => setTimeout(() => e.target.nextElementSibling?.classList.remove("show"), 150)}>
        {label}
      </button>
      <div className="dropdown-menu dropdown-menu-end">
        {items.filter((i) => !i.hidden).map((i, idx) =>
          i.to ? (
            <Link key={idx} className={`dropdown-item ${i.danger ? "text-danger" : ""}`} to={i.to}>{i.label}</Link>
          ) : (
            <button key={idx} className={`dropdown-item ${i.danger ? "text-danger" : ""}`} onMouseDown={(e) => e.preventDefault()} onClick={i.onClick}>{i.label}</button>
          )
        )}
      </div>
    </div>
  );
}

export function Breadcrumb({ items }: { items: { label: string; to?: string }[] }) {
  const { pathname } = useLocation();
  const root = pathname.startsWith("/rep") ? "/rep" : pathname.startsWith("/admin") ? "/admin" : "/app";
  return (
    <nav>
      <ol className="breadcrumb mb-0">
        <li className="breadcrumb-item"><Link to={root}>{root === "/rep" ? "Home" : "Dashboard"}</Link></li>
        {items.map((it, i) => (
          <li key={i} className={`breadcrumb-item ${i === items.length - 1 ? "active" : ""}`}>{it.to && i < items.length - 1 ? <Link to={it.to}>{labelCase(it.label)}</Link> : labelCase(it.label)}</li>
        ))}
      </ol>
    </nav>
  );
}

export function PageHeader({ title, badge, crumbs, actions }: { title: ReactNode; badge?: ReactNode; crumbs?: { label: string; to?: string }[]; actions?: ReactNode }) {
  return (
    <div className="d-md-flex d-block align-items-center justify-content-between mb-3">
      <div className="my-auto mb-2">
        <h3 className="page-title mb-1">{typeof title === "string" ? labelCase(title) : title}{badge && <span className="ms-2 align-middle">{badge}</span>}</h3>
        {crumbs && crumbs.length > 0 && <Breadcrumb items={crumbs} />}
      </div>
      {actions && <div className="d-flex gap-2 flex-wrap">{actions}</div>}
    </div>
  );
}


/** Red box above a hand-rolled form: the exact reason the server (or validation) refused the save. */
export function SaveError({ error, what }: { error: string | null; what: string }) {
  if (!error) return null;
  return (
    <div className="alert alert-danger d-flex gap-2 py-2">
      <i className="ti ti-alert-circle mt-1 flex-shrink-0" />
      <div><strong>Could not save this {what}.</strong><div className="fs-13">{error}</div></div>
    </div>
  );
}
