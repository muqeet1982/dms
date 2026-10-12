import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import Chart from "react-apexcharts";
import { api } from "@/api/client";
import { useAuth } from "@/lib/auth";
import { date, money, titleCase } from "@/lib/format";
import { PageHeader, Spinner, StatCard, StatusBadge } from "@/components/ui/Bits";

export function DashboardPage() {
  const me = useAuth((s) => s.me);
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api.get("/api/v1/dashboard") });
  const alerts = useQuery({ queryKey: ["inventory-alerts-top"], queryFn: () => api.list("/api/v1/inventory/alerts", { page_size: 6 }), enabled: Boolean(q.data?.inventory) });
  const inbox = useQuery({ queryKey: ["dash-inbox"], queryFn: () => api.list("/api/v1/sales-orders/inbox", { page_size: 6 }), enabled: Boolean(q.data?.orders) });
  const trips = useQuery({ queryKey: ["dash-trips"], queryFn: () => api.list("/api/v1/trips", { active: 1, page_size: 6 }), enabled: Boolean(q.data?.trips) });
  if (q.isLoading) return <Spinner />;
  const d = q.data || {};
  const cur = me?.tenant?.base_currency || "PKR";
  const trend = d.sales_trend || [];
  const statuses = d.orders?.by_status || {};
  return (
    <>
      <PageHeader title={`Welcome, ${me?.user.full_name?.split(" ")[0] || ""}`} crumbs={[]} actions={<span className="text-muted fs-13"><i className="ti ti-calendar me-1" />{date(d.date)}</span>} />
      <div className="row g-3 mb-3">
        {d.orders && <><div className="col-6 col-xl-3"><StatCard label="Orders inbox" value={d.orders.inbox} icon="ti-inbox" color="warning" sub="awaiting review / on hold" to="/app/sales/inbox" /></div>
          <div className="col-6 col-xl-3"><StatCard label="Orders today" value={d.orders.today} icon="ti-shopping-cart" color="primary" sub={`${d.orders.confirmed} confirmed · ${d.orders.dispatched} on the road`} to="/app/sales/orders" /></div></>}
        {d.sales && <div className="col-6 col-xl-3"><StatCard label={`Sales this month (${cur})`} value={money(d.sales.month, 0)} icon="ti-cash" color="success" sub={<span className={Number(d.sales.change_pct) >= 0 ? "text-success" : "text-danger"}>{Number(d.sales.change_pct) >= 0 ? "▲" : "▼"} {Math.abs(Number(d.sales.change_pct))}% vs last month ({money(d.sales.last_month, 0)})</span>} to="/app/sales/invoices" /></div>}
        {d.cash && <div className="col-6 col-xl-3"><StatCard label="Cash in hand" value={money(d.cash.in_hand, 0)} icon="ti-wallet" color="info" sub={`with reps: ${money(d.cash.with_reps, 0)} · collected today ${money(d.cash.collected_today, 0)}`} to="/app/accounting" /></div>}
        {d.cash && <><div className="col-6 col-xl-3"><StatCard label="Receivables" value={money(d.cash.receivables, 0)} icon="ti-receipt" color="purple" sub={<span className="text-danger">overdue {money(d.cash.receivables_overdue, 0)}</span>} to="/app/sales/invoices?open=1" /></div>
          <div className="col-6 col-xl-3"><StatCard label="Payables" value={money(d.cash.payables, 0)} icon="ti-building-factory-2" color="secondary" to="/app/purchasing/bills" /></div></>}
        {d.trips && <div className="col-6 col-xl-3"><StatCard label="Trips out for delivery" value={d.trips.out} icon="ti-truck-delivery" color="primary" sub={`${d.trips.planned} planned · ${d.trips.to_close} to close`} to="/app/delivery/trips?active=1" /></div>}
        {d.inventory && <div className="col-6 col-xl-3"><StatCard label="Stock alerts" value={d.inventory.low_stock + d.inventory.expiring + d.inventory.expired + d.inventory.negative} icon="ti-alert-triangle" color="danger" sub={`${d.inventory.low_stock} low · ${d.inventory.expiring} expiring · ${d.inventory.expired} expired`} to="/app/inventory/alerts" /></div>}
      </div>
      <div className="row g-3 mb-3">
        {d.sales && (
          <div className="col-xl-8">
            <div className="card h-100"><div className="card-header d-flex align-items-center justify-content-between"><h5 className="card-title mb-0">Sales — last 30 days</h5><span className="text-muted fs-12">invoiced totals</span></div>
              <div className="card-body">
                <Chart type="area" height={280} series={[{ name: "Sales", data: trend.map((t: any) => Number(t.total)) }]} options={{ chart: { toolbar: { show: false }, fontFamily: "Roboto" }, colors: ["#F26522"], stroke: { curve: "smooth", width: 2 }, fill: { type: "gradient", gradient: { opacityFrom: 0.35, opacityTo: 0.05 } }, dataLabels: { enabled: false }, xaxis: { categories: trend.map((t: any) => t.date.slice(5)), labels: { style: { fontSize: "11px" } } }, yaxis: { labels: { formatter: (v: number) => money(v, 0) } }, grid: { borderColor: "#f1f1f1" }, tooltip: { y: { formatter: (v: number) => `${cur} ${money(v)}` } } }} />
              </div></div>
          </div>
        )}
        {d.orders && (
          <div className="col-xl-4">
            <div className="card h-100"><div className="card-header"><h5 className="card-title mb-0">Orders this month by status</h5></div>
              <div className="card-body">
                {Object.keys(statuses).length ? <Chart type="donut" height={220} series={Object.values(statuses) as number[]} options={{ labels: Object.keys(statuses).map(titleCase), legend: { show: false }, colors: ["#F26522", "#1B84FF", "#03C95A", "#FFC107", "#AB47BC", "#E70D0D", "#3B7080", "#212529"], dataLabels: { enabled: false } }} /> : <div className="text-muted text-center py-4">No orders yet</div>}
                <div className="mt-2">{Object.entries(statuses).map(([k, v]) => <div key={k} className="d-flex justify-content-between mb-1"><StatusBadge status={k} /><strong>{v as number}</strong></div>)}</div>
              </div></div>
          </div>
        )}
      </div>
      <div className="row g-3">
        {d.orders && (
          <div className="col-xl-4">
            <div className="card h-100"><div className="card-header d-flex justify-content-between align-items-center"><h5 className="card-title mb-0">Orders inbox</h5><Link to="/app/sales/inbox" className="fs-12">View all</Link></div>
              <div className="card-body p-0"><table className="table table-sm mb-0 fs-13"><tbody>{inbox.data?.results.map((o: any) => <tr key={o.id}><td><Link to={`/app/sales/orders/${o.id}`} className="fw-medium">{o.number}</Link><div className="text-muted fs-12">{o.customer_name}{o.rep_name ? ` · ${o.rep_name}` : ""}</div></td><td className="num">{money(o.total, 0)}</td><td className="text-end"><StatusBadge status={o.status} /></td></tr>)}{inbox.data && !inbox.data.results.length && <tr><td className="text-muted text-center py-3">Inbox is empty</td></tr>}</tbody></table></div></div>
          </div>
        )}
        {d.trips && (
          <div className="col-xl-4">
            <div className="card h-100"><div className="card-header d-flex justify-content-between align-items-center"><h5 className="card-title mb-0">Active trips</h5><Link to="/app/delivery/trips" className="fs-12">View all</Link></div>
              <div className="card-body p-0"><table className="table table-sm mb-0 fs-13"><tbody>{trips.data?.results.map((t: any) => <tr key={t.id}><td><Link to={`/app/delivery/trips/${t.id}`} className="fw-medium">{t.number}</Link><div className="text-muted fs-12">{t.driver_name} · {t.vehicle_reg}</div></td><td>{t.delivered_count}/{t.stops_count} stops</td><td className="text-end"><StatusBadge status={t.status} /></td></tr>)}{trips.data && !trips.data.results.length && <tr><td className="text-muted text-center py-3">No active trips</td></tr>}</tbody></table></div></div>
          </div>
        )}
        {d.inventory && (
          <div className="col-xl-4">
            <div className="card h-100"><div className="card-header d-flex justify-content-between align-items-center"><h5 className="card-title mb-0">Stock alerts</h5><Link to="/app/inventory/alerts" className="fs-12">View all</Link></div>
              <div className="card-body p-0"><table className="table table-sm mb-0 fs-13"><tbody>{alerts.data?.results.map((a: any) => <tr key={a.id}><td><StatusBadge status={a.type} /></td><td className="fs-12">{a.message}</td></tr>)}{alerts.data && !alerts.data.results.length && <tr><td className="text-muted text-center py-3">All stock levels OK</td></tr>}</tbody></table></div></div>
          </div>
        )}
        {d.top_customers?.length > 0 && (
          <div className="col-xl-4">
            <div className="card h-100"><div className="card-header"><h5 className="card-title mb-0">Top customers this month</h5></div>
              <div className="card-body p-0"><table className="table table-sm mb-0 fs-13"><tbody>{d.top_customers.map((c: any, i: number) => <tr key={i}><td>{c.name}</td><td className="num fw-medium">{money(c.total, 0)}</td></tr>)}</tbody></table></div></div>
          </div>
        )}
      </div>
    </>
  );
}
