import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { sideOfPath } from "@/app/guards";
import { useAuth } from "@/lib/auth";
import { ago } from "@/lib/format";
import { EmptyState, PageHeader, Spinner } from "@/components/ui/Bits";

export function NotificationsPage() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const me = useAuth((s) => s.me);
  // this page serves both shells, so an order opens on the side the reader is already on
  const side = sideOfPath(useLocation().pathname);
  const q = useQuery({ queryKey: ["notifications-all"], queryFn: () => api.list("/api/v1/me/notifications", { page_size: 100 }) });
  const open = async (n: any) => {
    if (!n.read_at) { await api.post(`/api/v1/me/notifications/${n.id}/read`); qc.invalidateQueries({ queryKey: ["notif-unread"] }); qc.invalidateQueries({ queryKey: ["notifications-all"] }); }
    if (me?.user.is_platform_admin && !me.impersonating) return;
    if (n.data?.order_id) nav(`${side === "rep" ? "/rep" : "/app/sales"}/orders/${n.data.order_id}`);
    else if (n.data?.trip_id) nav(`/app/delivery/trips/${n.data.trip_id}`);
    else if (n.data?.adjustment_id) nav(`/app/inventory/adjustments/${n.data.adjustment_id}`);
    else if (n.data?.expense_id) nav(`/app/accounting/expenses/${n.data.expense_id}`);
  };
  return (
    <>
      <PageHeader title="Notifications" crumbs={[{ label: "Notifications" }]} actions={<button className="btn btn-outline-secondary" onClick={async () => { await api.post("/api/v1/me/notifications/read-all"); qc.invalidateQueries({ queryKey: ["notif-unread"] }); qc.invalidateQueries({ queryKey: ["notifications-all"] }); }}>Mark all read</button>} />
      <div className="card"><div className="card-body p-0">{q.isLoading ? <Spinner /> : !q.data?.results.length ? <EmptyState icon="ti-bell-off" text="No notifications" /> : <div className="list-group list-group-flush">{q.data.results.map((n: any) => <a key={n.id} className={`list-group-item list-group-item-action cursor-pointer ${!n.read_at ? "bg-light" : ""}`} onClick={() => open(n)}><div className="d-flex justify-content-between"><div><div className={`fs-13 ${!n.read_at ? "fw-semibold" : ""}`}>{n.title}</div><div className="fs-12 text-muted">{n.body}</div></div><div className="text-end fs-11 text-muted text-nowrap">{ago(n.created_at)}<div><span className="badge bg-secondary-light text-secondary">{n.type}</span></div></div></div></a>)}</div>}</div></div>
    </>
  );
}
