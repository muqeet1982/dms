import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { DateInput } from "@/components/ui/DateInput";
import { Field, PageHeader, Spinner, EmptyState } from "@/components/ui/Bits";
import { Modal } from "@/components/ui/Modal";
import { date, datetime, money, qty } from "@/lib/format";
import { pageRights } from "@/lib/perms";
import { toast } from "@/lib/toast";

/** A rep's day as the office reads it: the shift the rep declared, what happened inside it, and the
 * office's corrections — with the phone's own record kept underneath, because attendance gets disputed. */

const STATE_BADGE: Record<string, string> = { live: "bg-success-light text-success", stale: "bg-warning-light text-warning", lost: "bg-danger-light text-danger", paused: "bg-secondary-subtle text-secondary", off: "bg-light text-muted" };
const END_WORDS: Record<string, string> = { rep: "ended by the rep", tracking_off: "tracking switched off", location_off: "location switched off", permission_revoked: "permission revoked", tracking_lost: "tracking went silent", auto_cutoff: "daily cutoff", office: "ended by the office" };

const toLocalInput = (v?: string | null) => (v ? new Date(v).toISOString().slice(0, 16) : "");
const hm = (mins: number) => `${Math.floor(mins / 60)}h ${String(mins % 60).padStart(2, "0")}m`;

export function TimesheetsPage() {
  const [day, setDay] = useState(new Date().toISOString().slice(0, 10));
  const [edit, setEdit] = useState<any>(null);
  const [reason, setReason] = useState("");
  const qc = useQueryClient();
  const rights = pageRights("timesheets");
  const q = useQuery({ queryKey: ["timesheets", day], queryFn: () => api.get("/api/v1/shifts", { date: day }) });
  const rows = q.data?.results || [];
  const save = async () => {
    try {
      await api.patch(`/api/v1/shifts/${edit.id}`, {
        started_at: edit.started_at || undefined, ended_at: edit.ended_at || undefined,
        breaks: edit.breaks.map((b: any) => ({ id: b.id, started_at: b.started_at || undefined, ended_at: b.ended_at || undefined })),
        reason,
      });
      toast.success("Times corrected — the rep has been told");
      setEdit(null); setReason("");
      qc.invalidateQueries({ queryKey: ["timesheets", day] });
    } catch (e: any) { toast.error(e.message); }
  };
  return (
    <>
      <PageHeader title="Timesheets" crumbs={[{ label: "Customers", to: "/app/customers" }, { label: "Timesheets" }]}
        actions={<DateInput className="form-control" style={{ width: 160 }} value={day} onChange={(e: any) => setDay(e.target.value)} />} />
      {q.isLoading ? <Spinner /> : !rows.length ? <EmptyState icon="ti-clock-off" text={`No shifts on ${date(day)} — a shift appears when a rep starts one on the phone.`} /> : (
        <div className="card"><div className="card-body p-0"><div className="table-responsive">
          <table className="table table-hover fs-13 mb-0">
            <thead className="table-light"><tr><th>Sale Person</th><th>Started</th><th>Breaks</th><th>Ended</th><th className="num">Worked</th><th className="num">Distance</th><th className="num">Visits</th><th className="num">Orders</th><th>Tracking</th><th /></tr></thead>
            <tbody>{rows.map((r: any) => (
              <tr key={r.id}>
                <td><div className="fw-medium">{r.rep_name}</div><div className="fs-11 text-muted">{r.rep_code}{r.mocked_pings > 0 && <span className="text-danger ms-1" title="Positions from a fake-GPS app"><i className="ti ti-alert-triangle" /> {r.mocked_pings} mocked</span>}</div></td>
                <td>{datetime(r.started_at)}{r.corrected && <div className="fs-11 text-warning" title={`${r.correction_reason} — by ${r.corrected_by}`}><i className="ti ti-pencil" /> corrected · phone said {datetime(r.recorded_started_at)}</div>}</td>
                <td className="fs-12">{r.breaks.length ? r.breaks.map((b: any) => <div key={b.id}>{datetime(b.started_at).slice(-5)} – {b.ended_at ? datetime(b.ended_at).slice(-5) : "…"}</div>) : <span className="text-muted">—</span>}</td>
                <td>{r.ended_at ? <>{datetime(r.ended_at)}<div className="fs-11 text-muted">{END_WORDS[r.end_reason] || r.end_reason || ""}</div></> : <span className="badge bg-success-light text-success">on shift</span>}</td>
                <td className="num">{hm(r.working_minutes)}</td>
                <td className="num">{(r.distance_m / 1000).toFixed(1)} km</td>
                <td className="num">{r.visits}</td>
                <td className="num">{r.orders}</td>
                <td><span className={`badge ${STATE_BADGE[r.tracking_state] || "bg-light text-muted"}`}>{r.tracking_state}</span></td>
                <td className="text-end">{rights.edit && <button className="btn btn-sm btn-outline-secondary" onClick={() => { setEdit({ id: r.id, started_at: toLocalInput(r.started_at), ended_at: toLocalInput(r.ended_at), breaks: r.breaks.map((b: any) => ({ id: b.id, started_at: toLocalInput(b.started_at), ended_at: toLocalInput(b.ended_at) })) }); setReason(""); }}><i className="ti ti-pencil" /></button>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div></div></div>
      )}
      <Modal open={Boolean(edit)} title="Correct Times" onClose={() => setEdit(null)} size="md"
        footer={<><button className="btn btn-light" onClick={() => setEdit(null)}>Cancel</button><button className="btn btn-primary" disabled={!reason.trim()} onClick={save}>Save Correction</button></>}>
        {edit && <>
          <div className="row">
            <Field label="Shift started" col="col-6"><input type="datetime-local" className="form-control" value={edit.started_at} onChange={(e) => setEdit({ ...edit, started_at: e.target.value })} /></Field>
            <Field label="Shift ended" col="col-6"><input type="datetime-local" className="form-control" value={edit.ended_at} onChange={(e) => setEdit({ ...edit, ended_at: e.target.value })} /></Field>
          </div>
          {edit.breaks.map((b: any, i: number) => <div className="row" key={b.id}>
            <Field label={`Break ${i + 1} from`} col="col-6"><input type="datetime-local" className="form-control" value={b.started_at} onChange={(e) => { const bs = [...edit.breaks]; bs[i] = { ...b, started_at: e.target.value }; setEdit({ ...edit, breaks: bs }); }} /></Field>
            <Field label="to" col="col-6"><input type="datetime-local" className="form-control" value={b.ended_at} onChange={(e) => { const bs = [...edit.breaks]; bs[i] = { ...b, ended_at: e.target.value }; setEdit({ ...edit, breaks: bs }); }} /></Field>
          </div>)}
          <Field label="Reason *" hint="kept on the record and shown to the rep — corrected attendance gets disputed later"><input className="form-control" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Forgot to end the shift after the last call" /></Field>
          <div className="form-text">The phone's own times are kept alongside the correction, and the rep gets a notification.</div>
        </>}
      </Modal>
    </>
  );
}
