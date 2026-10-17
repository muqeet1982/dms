import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useOptions } from "@/lib/hooks";
import { pageRights } from "@/lib/perms";
import { toast } from "@/lib/toast";
import { Alert, Field, PageHeader, Spinner, StatCard } from "@/components/ui/Bits";
import { Modal } from "@/components/ui/Modal";
import { FenceMap, MapFence, MapPin, Shape } from "@/components/map/FenceMap";

type Store = { id: string; name: string; code: string; phone: string; address: string; city: string; status: string; route: string; reps: { id: string; name: string }[]; lat?: number; lng?: number; fences?: string[] };

const shapeOf = (f: any): Shape =>
  f.type === "circle"
    ? { type: "circle", lat: Number(f.centre_lat), lng: Number(f.centre_lng), radius_m: Number(f.radius_m || 0) }
    : { type: "polygon", points: (f.points || []).map((p: any) => ({ lat: Number(p.lat), lng: Number(p.lng) })) };

/**
 * Store locations: every shop on one map, with the working areas drawn over them.
 *
 * Three jobs live here — putting a new shop on the map, fixing one that sits in the wrong place (or has no pin at
 * all), and handing the shops inside an area to the rep who covers it.
 */
export function StoresPage() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const rights = pageRights("store_locations");
  const customerRights = pageRights("customers");
  const q = useQuery({ queryKey: ["stores-map"], queryFn: () => api.get("/api/v1/customers/map") });
  const reps = useOptions("/api/v1/supplier-reps", { is_active: true });
  const [selected, setSelected] = useState<string | null>(null);
  const [area, setArea] = useState("");
  const [rep, setRep] = useState("");
  const [search, setSearch] = useState("");
  const [placing, setPlacing] = useState<Store | null>(null);   // an existing store waiting for its pin
  const [adding, setAdding] = useState<{ lat: number; lng: number } | null>(null);
  const [addMode, setAddMode] = useState(false);
  const [assign, setAssign] = useState(false);

  if (q.isLoading) return <Spinner />;
  const stores: Store[] = q.data?.stores || [];
  const noPin: Store[] = q.data?.without_pin || [];
  const fences: any[] = q.data?.fences || [];

  const shown = stores.filter((s) =>
    (!area || (s.fences || []).includes(area)) &&
    (!rep || s.reps.some((r) => r.id === rep)) &&
    (!search || `${s.name} ${s.code} ${s.phone} ${s.address}`.toLowerCase().includes(search.toLowerCase())));

  const pins: MapPin[] = shown.map((s) => ({ id: s.id, name: s.name, lat: s.lat!, lng: s.lng!, covered: (s.fences || []).length > 0, sub: s.reps.map((r) => r.name).join(", ") || "no rep", selected: s.id === selected }));
  const mapFences: MapFence[] = fences.filter((f) => !area || f.id === area).map((f) => ({ id: f.id, name: f.name, colour: f.colour, shape: shapeOf(f), faded: true }));
  const chosen = stores.find((s) => s.id === selected) || null;
  const areaFence = fences.find((f) => f.id === area);

  const movePin = async (id: string, lat: number, lng: number) => {
    await api.post(`/api/v1/customers/${id}/pin`, { lat, lng });
    qc.invalidateQueries({ queryKey: ["stores-map"] });
    toast.success("Pin moved");
  };

  return (
    <>
      <PageHeader title="Store locations" crumbs={[{ label: "Customers", to: "/app/customers" }, { label: "Store locations" }]}
        actions={<>
          {area && customerRights.edit && <button className="btn btn-outline-primary me-2" onClick={() => setAssign(true)}><i className="ti ti-user-plus me-1" />Assign stores in this area</button>}
          {customerRights.add && <button className={`btn ${addMode ? "btn-warning" : "btn-primary"}`} onClick={() => { setAddMode(!addMode); setPlacing(null); }}>
            <i className="ti ti-map-pin-plus me-1" />{addMode ? "Click the map… (cancel)" : "Add a store"}</button>}
        </>} />

      <div className="row g-3 mb-3">
        <div className="col-6 col-md-3"><StatCard label="Stores on the map" value={stores.length} icon="ti-building-store" color="primary" /></div>
        <div className="col-6 col-md-3"><StatCard label="Shown" value={shown.length} icon="ti-filter" color="info" sub={area ? areaFence?.name : rep ? "by rep" : "all"} /></div>
        <div className="col-6 col-md-3"><StatCard label="In no area" value={stores.filter((s) => !(s.fences || []).length).length} icon="ti-map-off" color="warning" /></div>
        <div className="col-6 col-md-3"><StatCard label="No pin yet" value={noPin.length} icon="ti-map-pin-off" color={noPin.length ? "danger" : "secondary"} sub="place them below" /></div>
      </div>

      <div className="row g-3">
        <div className="col-lg-8">
          <div className="card"><div className="card-body p-2">
            {(addMode || placing) && <Alert kind="info">{placing ? <>Click the map where <strong>{placing.name}</strong> is.</> : "Click the map where the shop is — you can fill in its details next."}</Alert>}
            <FenceMap
              fences={mapFences}
              pins={pins}
              point={chosen && chosen.lat != null ? { lat: chosen.lat, lng: chosen.lng! } : null}
              onPointChange={(lat, lng) => chosen && movePin(chosen.id, lat, lng)}
              onPinClick={(id) => { setSelected(id); setAddMode(false); }}
              onPinMove={() => {}}
              cursor={addMode || placing ? "crosshair" : undefined}
              onMapClick={async (lat, lng) => {
                if (placing) { await movePin(placing.id, lat, lng); setPlacing(null); return; }
                if (addMode) { setAdding({ lat: +lat.toFixed(7), lng: +lng.toFixed(7) }); setAddMode(false); }
              }}
              height={560}
            />
            <div className="fs-12 text-muted px-2 pt-2">
              <span className="me-3"><i className="ti ti-circle-filled text-success me-1" />inside a working area</span>
              <span className="me-3"><i className="ti ti-circle text-danger me-1" />in no area</span>
              <span>click a store to select it, then drag the orange handle to correct where it stands</span>
            </div>
          </div></div>
        </div>

        <div className="col-lg-4">
          <div className="card mb-3"><div className="card-body">
            <Field label="Working area" hint="Pick one to see only the stores inside it">
              <select className="form-select" value={area} onChange={(e) => { setArea(e.target.value); setSelected(null); }}>
                <option value="">All areas</option>
                {fences.map((f) => <option key={f.id} value={f.id}>{f.name}{f.reps?.length ? ` — ${f.reps.map((r: any) => r.name).join(", ")}` : " — not assigned"}</option>)}
              </select>
            </Field>
            <Field label="Rep">
              <select className="form-select" value={rep} onChange={(e) => setRep(e.target.value)}>
                <option value="">Any rep</option>
                {(reps.data || []).map((r: any) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
            </Field>
            <Field label="Search"><input className="form-control" placeholder="name, code, phone or address" value={search} onChange={(e) => setSearch(e.target.value)} /></Field>
          </div></div>

          {chosen && (
            <div className="card mb-3 border-primary"><div className="card-body">
              <div className="d-flex justify-content-between">
                <div>
                  <div className="fw-semibold">{chosen.name}</div>
                  <div className="text-muted fs-12">{chosen.code}{chosen.phone ? ` · ${chosen.phone}` : ""}</div>
                  <div className="fs-12">{chosen.address || <span className="text-muted">no address</span>}</div>
                  <div className="fs-12 mt-1">Reps: {chosen.reps.length ? chosen.reps.map((r) => r.name).join(", ") : <span className="text-warning">none yet</span>}</div>
                </div>
                <button className="btn-close" onClick={() => setSelected(null)} />
              </div>
              <div className="d-flex gap-2 mt-2">
                <Link className="btn btn-sm btn-outline-primary" to={`/app/customers/${chosen.id}`}>Open store</Link>
                {customerRights.edit && <Link className="btn btn-sm btn-outline-secondary" to={`/app/customers/${chosen.id}/edit`}>Edit</Link>}
              </div>
            </div></div>
          )}

          <div className="card"><div className="card-header py-2 d-flex justify-content-between align-items-center">
            <strong className="fs-13">Stores {area ? "in this area" : "shown"}</strong><span className="text-muted fs-12">{shown.length}</span>
          </div>
            <div className="card-body p-0 table-responsive" style={{ maxHeight: 260, overflowY: "auto" }}>
              <table className="table table-sm fs-12 mb-0"><tbody>
                {shown.map((s) => (
                  <tr key={s.id} className={s.id === selected ? "table-active" : ""} role="button" onClick={() => setSelected(s.id)}>
                    <td>{s.name}<div className="text-muted fs-11">{s.code}{s.route ? ` · ${s.route}` : ""}</div></td>
                    <td className="text-end text-muted fs-11">{s.reps.map((r) => r.name).join(", ") || "—"}</td>
                  </tr>
                ))}
                {!shown.length && <tr><td className="text-muted p-3">No stores match.</td></tr>}
              </tbody></table>
            </div>
          </div>

          {noPin.length > 0 && (
            <div className="card mt-3 border-warning"><div className="card-header py-2"><strong className="fs-13">No pin yet</strong><span className="text-muted fs-12 ms-2">they cannot fall inside any area</span></div>
              <div className="card-body p-0 table-responsive" style={{ maxHeight: 200, overflowY: "auto" }}>
                <table className="table table-sm fs-12 mb-0"><tbody>
                  {noPin.map((s) => (
                    <tr key={s.id}>
                      <td>{s.name}<div className="text-muted fs-11">{s.code}</div></td>
                      <td className="text-end">{rights.edit && <button className="btn btn-sm btn-outline-primary" onClick={() => { setPlacing(s); setAddMode(false); }}>Place on map</button>}</td>
                    </tr>
                  ))}
                </tbody></table>
              </div>
            </div>
          )}
        </div>
      </div>

      <AddStoreModal at={adding} onClose={() => setAdding(null)} onSaved={(id) => { setAdding(null); qc.invalidateQueries({ queryKey: ["stores-map"] }); setSelected(id); }} />
      <AssignStoresModal open={assign} fence={areaFence} onClose={() => setAssign(false)} onDone={() => { setAssign(false); qc.invalidateQueries({ queryKey: ["stores-map"] }); }} />
    </>
  );
}

/** A new shop, started from a point on the map: the least a store needs, the rest on its own page later. */
function AddStoreModal({ at, onClose, onSaved }: { at: { lat: number; lng: number } | null; onClose: () => void; onSaved: (id: string) => void }) {
  const [form, setForm] = useState<any>({ name: "", phone: "", type: "retailer", address: "", city: "" });
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (!form.name.trim()) return toast.error("The shop needs a name.");
    setBusy(true);
    try {
      const c = await api.post("/api/v1/customers", { ...form, gps_lat: at!.lat, gps_lng: at!.lng }, true);
      toast.success(`${c.name} added`);
      setForm({ name: "", phone: "", type: "retailer", address: "", city: "" });
      onSaved(c.id);
    } catch (e: any) { toast.error(e.message); }
    finally { setBusy(false); }
  };
  return (
    <Modal open={Boolean(at)} onClose={onClose} title="Add a store here" size="md"
      footer={<><button className="btn btn-light" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Add store"}</button></>}>
      <div className="fs-12 text-muted mb-2"><i className="ti ti-map-pin me-1" />{at?.lat.toFixed(5)}, {at?.lng.toFixed(5)}</div>
      <Field label="Shop name *"><input className="form-control" autoFocus value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Al-Noor Cosmetics" /></Field>
      <div className="row g-2">
        <div className="col-6"><Field label="Mobile"><input className="form-control" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></Field></div>
        <div className="col-6"><Field label="Type"><select className="form-select" value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>{["retailer", "salon", "wholesaler", "other"].map((t) => <option key={t} value={t}>{t}</option>)}</select></Field></div>
      </div>
      <Field label="Address"><input className="form-control" value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} /></Field>
    </Modal>
  );
}

/** Hand the stores inside one area to a rep — one, some, or all of them. */
export function AssignStoresModal({ open, fence, rep: fixedRep, onClose, onDone }: { open: boolean; fence: any; rep?: string; onClose: () => void; onDone: () => void }) {
  const inside = useQuery({ queryKey: ["fence-stores", fence?.id], queryFn: () => api.get(`/api/v1/geofences/${fence.id}/customers`), enabled: Boolean(open && fence?.id) });
  const reps = useOptions("/api/v1/supplier-reps", { is_active: true }, Boolean(open));
  const [rep, setRep] = useState(fixedRep || "");
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const rows = inside.data?.results || [];
  const suggested = useMemo(() => fixedRep || fence?.reps?.[0]?.id || "", [fixedRep, fence]);
  const current = rep || suggested;

  const run = async (all: boolean, remove = false) => {
    if (!current) return toast.error("Pick the rep these stores are for.");
    setBusy(true);
    try {
      const r = await api.post(`/api/v1/geofences/${fence.id}/assign-customers`, all ? { rep: current, all: true, remove } : { rep: current, customers: picked, remove });
      toast.success(`${r.stores} store(s) ${remove ? "taken off" : "assigned to"} ${r.rep}`);
      setPicked([]);
      onDone();
    } catch (e: any) { toast.error(e.message); }
    finally { setBusy(false); }
  };

  return (
    <Modal open={open} onClose={onClose} title={`Stores in ${fence?.name || "this area"}`} size="lg"
      footer={<>
        <button className="btn btn-light" onClick={onClose}>Close</button>
        <button className="btn btn-outline-danger" disabled={busy || !picked.length} onClick={() => run(false, true)}>Take selected off the rep</button>
        <button className="btn btn-outline-primary" disabled={busy || !picked.length} onClick={() => run(false)}>Assign selected ({picked.length})</button>
        <button className="btn btn-primary" disabled={busy || !rows.length} onClick={() => run(true)}>Assign all {rows.length}</button>
      </>}>
      <Field label="Rep" hint="Their area, their stores — the rep only sees the stores assigned to them.">
        <select className="form-select" value={current} onChange={(e) => setRep(e.target.value)} disabled={Boolean(fixedRep)}>
          <option value="">— pick a rep —</option>
          {(reps.data || []).map((r: any) => <option key={r.id} value={r.id}>{r.name} — {r.supplier_name}</option>)}
        </select>
      </Field>
      {inside.isLoading ? <Spinner /> : (
        <div className="table-responsive" style={{ maxHeight: 360, overflowY: "auto" }}>
          <table className="table table-sm fs-12 mb-0">
            <thead className="table-light"><tr>
              <th style={{ width: 34 }}><input type="checkbox" className="form-check-input" checked={picked.length === rows.length && rows.length > 0}
                onChange={(e) => setPicked(e.target.checked ? rows.map((r: any) => r.id) : [])} /></th>
              <th>Store</th><th>Address</th><th>Route</th><th>Reps today</th>
            </tr></thead>
            <tbody>
              {rows.map((c: any) => {
                const has = (c.reps || []).some((r: any) => r.id === current);
                return (
                  <tr key={c.id} className={has ? "table-success" : ""}>
                    <td><input type="checkbox" className="form-check-input" checked={picked.includes(c.id)}
                      onChange={(e) => setPicked(e.target.checked ? [...picked, c.id] : picked.filter((x) => x !== c.id))} /></td>
                    <td>{c.name}<div className="text-muted fs-11">{c.code}{c.phone ? ` · ${c.phone}` : ""}</div></td>
                    <td className="text-muted">{c.address || "—"}</td>
                    <td>{c.route || "—"}</td>
                    <td>{(c.reps || []).map((r: any) => r.name).join(", ") || <span className="text-muted">none</span>}{has && <i className="ti ti-check text-success ms-1" />}</td>
                  </tr>
                );
              })}
              {!rows.length && <tr><td colSpan={5} className="text-muted p-3">No stores fall inside this area yet. Place their pins on the map first.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </Modal>
  );
}
