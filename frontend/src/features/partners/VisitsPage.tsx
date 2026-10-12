import { DateInput } from "@/components/ui/DateInput";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { datetime } from "@/lib/format";
import { EmptyState, PageHeader, Spinner, StatCard } from "@/components/ui/Bits";
import { DataTable } from "@/components/ui/DataTable";

/**
 * Check-ins: where the field force went, and what came of it.
 *
 * The column that matters is the distance from the shop's own pin — it is the difference between "was there"
 * and "says they were there".
 */
export function VisitsPage() {
  const today = new Date().toISOString().slice(0, 10);
  const [date, setDate] = useState(today);
  const [outcome, setOutcome] = useState("");
  const [rep, setRep] = useState("");
  const [photo, setPhoto] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["visits", date, outcome, rep], queryFn: () => api.get("/api/v1/visits", { date, outcome, rep }) });
  const rows = q.data?.results || [];
  const summary = q.data?.summary || [];

  const near = (m: number | null) =>
    m === null || m === undefined ? <span className="text-muted">no fix</span>
      : m <= 100 ? <span className="text-success">{Math.round(m)} m</span>
        : m <= 500 ? <span className="text-warning">{Math.round(m)} m</span>
          : <span className="text-danger">{m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`}</span>;

  return (
    <>
      <PageHeader title="Check-ins" crumbs={[]} />
      <div className="row g-2 mb-3">
        <div className="col-6 col-md-3"><StatCard label="Calls" value={String(q.data?.count ?? 0)} icon="ti-map-pin-check" /></div>
        <div className="col-6 col-md-3"><StatCard label="Productive" value={String(summary.reduce((n: number, s: any) => n + s.orders, 0))} icon="ti-shopping-cart" /></div>
        <div className="col-6 col-md-3"><StatCard label="No order" value={String(summary.reduce((n: number, s: any) => n + s.no_orders, 0))} icon="ti-mood-empty" /></div>
      </div>
      <div className="card mb-3"><div className="card-body py-2">
        <div className="row g-2 align-items-end">
          <div className="col-6 col-md-3"><label className="form-label fs-12 mb-1">Day</label><DateInput className="form-control form-control-sm" value={date} onChange={(e) => setDate(e.target.value)} /></div>
          <div className="col-6 col-md-3"><label className="form-label fs-12 mb-1">Outcome</label>
            <select className="form-select form-select-sm" value={outcome} onChange={(e) => setOutcome(e.target.value)}>
              <option value="">All</option><option value="no_order">No order</option><option value="order">Order taken</option>
            </select></div>
          <div className="col-12 col-md-4"><label className="form-label fs-12 mb-1">Rep</label>
            <select className="form-select form-select-sm" value={rep} onChange={(e) => setRep(e.target.value)}>
              <option value="">Everyone</option>{(q.data?.reps || []).map((r: any) => <option key={r.id} value={r.id}>{r.name}</option>)}
            </select></div>
        </div>
      </div></div>
      {summary.length > 0 && (
        <div className="card mb-3"><div className="card-header py-2 fs-13 fw-medium">Calls per person</div><div className="card-body p-0">
          <table className="table table-sm fs-13 mb-0">
            <thead className="table-light"><tr><th>Rep</th><th className="num">Calls</th><th className="num">Orders</th><th className="num">No order</th><th>Why not</th></tr></thead>
            <tbody>{summary.map((s: any) => (
              <tr key={s.user_id || s.user_name}>
                <td>{s.user_name}</td><td className="num">{s.visits}</td>
                <td className="num text-success">{s.orders}</td><td className="num text-muted">{s.no_orders}</td>
                <td className="fs-12 text-muted">{Object.entries(s.by_reason_label || {}).map(([k, n]) => `${k} ${n}`).join(" · ")}</td>
              </tr>))}</tbody>
          </table>
        </div></div>
      )}
      <div className="card"><div className="card-body p-0">
        {q.isLoading ? <Spinner /> : !rows.length ? <EmptyState text="No check-ins on this day." /> : (
          <DataTable
            small
            rows={rows}
            columns={[
              { key: "photo", header: "", render: (v: any) => v.photo_url
                  ? <span className="position-relative d-inline-block">
                      <img src={v.photo_url} alt="" role="button" onClick={() => setPhoto(v.photo_url)} style={{ height: 36, width: 36, objectFit: "cover", borderRadius: 4 }} />
                      {v.photo_verified === false && (
                        <i className="ti ti-alert-triangle-filled text-warning position-absolute"
                           style={{ right: -4, bottom: -4, fontSize: 13 }}
                           title="The phone did not say when this photo was taken, so nothing vouches for it" />
                      )}
                    </span>
                  : <span className="text-muted fs-11">—</span> },
              { key: "at", header: "Time", render: (v: any) => datetime(v.at) },
              { key: "customer", header: "Shop", render: (v: any) => <>{v.customer_name}<div className="fs-11 text-muted">{v.customer_code}</div></> },
              { key: "rep", header: "Rep", render: (v: any) => v.rep_name || v.user_name },
              { key: "outcome", header: "Outcome", render: (v: any) => v.outcome === "order"
                  ? <span className="badge bg-success-light text-success">order {v.order_ref || ""}</span>
                  : <span className="badge bg-secondary-light text-secondary">{v.reason_label || "no order"}</span> },
              { key: "distance", header: "From the shop", num: true, render: (v: any) => near(v.customer_distance_m) },
              { key: "area", header: "In their area", render: (v: any) => v.inside_area === false
                  ? <span className="text-danger">no</span> : v.inside_area ? <span className="text-success">yes</span> : <span className="text-muted">—</span> },
              { key: "note", header: "Note", render: (v: any) => <span className="fs-12 text-muted">{v.note}</span> },
            ]}
          />
        )}
      </div></div>
      {photo && (
        <div className="modal d-block" style={{ background: "rgba(0,0,0,.6)" }} role="button" onClick={() => setPhoto(null)}>
          <div className="modal-dialog modal-dialog-centered modal-lg"><div className="modal-content">
            <img src={photo} alt="shop" className="w-100" style={{ objectFit: "contain", maxHeight: "80vh" }} />
          </div></div>
        </div>
      )}
    </>
  );
}
