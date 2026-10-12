import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useDoc, useOptions } from "@/lib/hooks";
import { pageRights, usePage } from "@/lib/perms";
import { toast } from "@/lib/toast";
import { Alert, Field, PageHeader, SaveError, Spinner, StatCard, StatusBadge } from "@/components/ui/Bits";
import { Column } from "@/components/ui/DataTable";
import { ListPage } from "@/components/ui/ListPage";
import { ConfirmDialog } from "@/components/ui/Modal";
import { asBox, boxPoints, DEFAULT_CENTRE, FenceMap, MapFence, MapPin, Shape } from "@/components/map/FenceMap";
import { AssignStoresModal } from "./StorePages";

/** The stored fence turned into something the map can draw. */
function toShape(f: any): Shape {
  return f.type === "circle"
    ? { type: "circle", lat: Number(f.centre_lat), lng: Number(f.centre_lng), radius_m: Number(f.radius_m || 0) }
    : { type: "polygon", points: (f.points || []).map((p: any) => ({ lat: Number(p.lat), lng: Number(p.lng) })) };
}

const asFence = (f: any): MapFence => ({ id: f.id, name: f.name, colour: f.colour, shape: toShape(f) });

export function GeofencesPage() {
  const cols: Column<any>[] = [
    { key: "name", header: "Area", render: (f) => <><span className="fw-semibold text-dark">{f.name}</span>{f.place_name && <div className="text-muted fs-11">{f.place_name}</div>}</> },
    { key: "type", header: "Shape", render: (f) => (f.type === "circle" ? `${(f.radius_m / 1000).toFixed(1)} km radius` : `drawn · ${(f.points || []).length} corners`) },
    { key: "area_km2", header: "Size", num: true, render: (f) => `${f.area_km2} km²` },
    { key: "customers_count", header: "Customers", num: true, render: (f) => (f.customers_count ?? "—") },
    { key: "reps", header: "Covered by", render: (f) => (f.assignments?.length ? f.assignments.map((a: any) => a.rep_name).join(", ") : <span className="text-muted">not assigned</span>) },
    { key: "is_active", header: "Status", render: (f) => <StatusBadge status={f.is_active ? "active" : "inactive"} /> },
  ];
  return (
    <ListPage
      page="geofences"
      title="Working areas"
      endpoint="/api/v1/geofences"
      extraQuery={{ with_counts: "1" }}
      columns={cols}
      rowLink={(f) => `/app/customers/areas/${f.id}`}
      createTo="/app/customers/areas/new"
      createLabel="New area"
      defaultOrdering="name"
      actions={<Link to="/app/customers/areas/map" className="btn btn-outline-secondary me-2"><i className="ti ti-map-2 me-1" />Coverage map</Link>}
      filters={[{ name: "type", label: "Shape", type: "select", options: [{ value: "circle", label: "Radius" }, { value: "polygon", label: "Drawn" }] },
                { name: "is_active", label: "Status", type: "select", options: [{ value: "true", label: "Active" }, { value: "false", label: "Off" }] }]}
      emptyText={<>No working areas yet. <Link to="/app/customers/areas/new">Draw one</Link> and hand it to a rep — their orders, collections and new shops are then checked against it.</>}
    />
  );
}

/** Radius · Area name · Draw it — three ways into the same shape. */
const MODES = [
  { key: "circle", label: "Radius", icon: "ti-circle", hint: "A pin and a distance — the simplest area." },
  { key: "place", label: "Area name", icon: "ti-search", hint: "Type a town or neighbourhood and we fetch its shape." },
  { key: "draw", label: "Draw it", icon: "ti-polygon", hint: "Click the map to drop corners, drag them to adjust." },
];

export function GeofenceFormPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const existing = useDoc("/api/v1/geofences", id);
  const reps = useOptions("/api/v1/supplier-reps", { is_active: true });
  const rights = pageRights("geofences");
  const [err, setErr] = useState<string | null>(null);
  const [mode, setMode] = useState("circle");
  const [form, setForm] = useState<any>({ name: "", colour: "#F26522", is_active: true, notes: "", reps: [], place_name: "", source: "manual", osm_ref: "" });
  const [shape, setShape] = useState<Shape>({ type: "circle", lat: DEFAULT_CENTRE.lat, lng: DEFAULT_CENTRE.lng, radius_m: 5000 });
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [del, setDel] = useState(false);
  // re-frame the map when the shape changes wholesale (opening, switching mode, picking a place) — never mid-drag
  const [fitNonce, setFitNonce] = useState(0);
  const reframe = () => setFitNonce((n) => n + 1);

  if (existing.data && !loaded) {
    setLoaded(true);
    const f = existing.data;
    setForm({ name: f.name, colour: f.colour, is_active: f.is_active, notes: f.notes || "", reps: (f.assignments || []).map((a: any) => a.rep), place_name: f.place_name || "", source: f.source, osm_ref: f.osm_ref || "" });
    setShape(toShape(f));
    setMode(f.type === "circle" ? "circle" : "draw");
    reframe();
  }

  // who falls inside, refreshed as the shape changes — the number that tells you the area is right
  const [inside, setInside] = useState<{ count: number; pins: MapPin[] } | null>(null);
  const previewTimer = useRef<any>(null);
  useEffect(() => {
    clearTimeout(previewTimer.current);
    previewTimer.current = setTimeout(async () => {
      try {
        const r = await api.post("/api/v1/geofences/preview", { ...shapeToBody(shape) }, true);
        setInside({ count: r.count, pins: (r.customers || []).map((c: any) => ({ id: c.id, name: c.name, lat: c.lat, lng: c.lng, covered: true, sub: c.code })) });
      } catch { setInside(null); }
    }, 500);
    return () => clearTimeout(previewTimer.current);
  }, [JSON.stringify(shape)]);

  const save = async () => {
    setErr(null);
    if (!form.name.trim()) return toast.error("Give the area a name — the reps will see it.");
    setSaving(true);
    try {
      const body = { ...form, ...shapeToBody(shape), reps: form.reps };
      const saved = id ? await api.patch(`/api/v1/geofences/${id}`, { ...body, version: existing.data?.version }) : await api.post("/api/v1/geofences", body, true);
      toast.success(id ? "Area saved" : "Area created");
      nav(`/app/customers/areas/${saved.id}`);
    } catch (e: any) { setErr(e.message); toast.error(e.message); }
    finally { setSaving(false); }
  };

  if (id && existing.isLoading) return <Spinner />;

  return (
    <>
      <PageHeader title={id ? `Edit ${existing.data?.name || "area"}` : "New working area"}
        crumbs={[{ label: "Working areas", to: "/app/customers/areas" }, { label: id ? existing.data?.name : "New" }]}
        actions={<>
          {id && rights.delete && <button className="btn btn-outline-danger me-2" onClick={() => setDel(true)}><i className="ti ti-trash me-1" />Delete</button>}
          <button className="btn btn-light me-2" onClick={() => nav(-1)}>Cancel</button>
          <button className="btn btn-primary" disabled={saving} onClick={save}><i className="ti ti-check me-1" />{saving ? "Saving…" : "Save area"}</button>
        </>} />
      <SaveError error={err} what="working area" />

      <div className="row g-3">
        <div className="col-lg-7">
          <div className="card"><div className="card-body p-2">
            <FenceMap
              editing={shape}
              onChange={setShape}
              pins={inside?.pins || []}
              fitNonce={fitNonce}
              onMapClick={(lat, lng) => {
                if (mode === "draw") {
                  const pts = shape.type === "polygon" ? shape.points : [];
                  setShape({ type: "polygon", points: [...pts, { lat: +lat.toFixed(7), lng: +lng.toFixed(7) }] });
                } else if (shape.type === "circle") {
                  setShape({ ...shape, lat: +lat.toFixed(7), lng: +lng.toFixed(7) });
                }
              }}
              height={540}
            />
            <div className="d-flex justify-content-between align-items-center px-2 pt-2 fs-12 text-muted">
              <span>{mode === "draw" ? "Click to drop a corner · drag a corner to move it · right-click a corner to remove it" : "Click the map to move the pin · drag the handle to change the distance"}</span>
              <span className={inside && inside.count ? "text-success fw-medium" : ""}>{inside ? `${inside.count} customer${inside.count === 1 ? "" : "s"} inside` : "…"}</span>
            </div>
          </div></div>
        </div>

        <div className="col-lg-5">
          <div className="card mb-3"><div className="card-body">
            <Field label="Name *" hint="What the reps will see, e.g. “Lahore — Gulberg”">
              <input className="form-control" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Gulberg & Garden Town" />
            </Field>

            <label className="form-label">How is this area defined?</label>
            <div className="btn-group w-100 mb-2" role="group">
              {MODES.map((m) => (
                <button key={m.key} type="button" className={`btn btn-sm ${mode === m.key ? "btn-primary" : "btn-outline-secondary"}`}
                  onClick={() => {
                    setMode(m.key);
                    if (m.key === "circle" && shape.type !== "circle") { setShape({ type: "circle", lat: DEFAULT_CENTRE.lat, lng: DEFAULT_CENTRE.lng, radius_m: 5000 }); reframe(); }
                    if (m.key === "draw" && shape.type !== "polygon") { setShape({ type: "polygon", points: squareAround(shape) }); reframe(); }
                  }}>
                  <i className={`ti ${m.icon} me-1`} />{m.label}
                </button>
              ))}
            </div>
            <div className="form-text mb-3">{MODES.find((m) => m.key === mode)?.hint}</div>

            {mode === "circle" && shape.type === "circle" && (
              <>
                <Field label={`How far does it reach? — ${(shape.radius_m / 1000).toFixed(1)} km`}>
                  <input type="range" className="form-range" min={200} max={50000} step={100} value={shape.radius_m}
                    onChange={(e) => setShape({ ...shape, radius_m: Number(e.target.value) })} />
                  <div className="d-flex justify-content-between fs-11 text-muted"><span>200 m</span><span>50 km</span></div>
                </Field>
                <div className="row g-2">
                  <div className="col-6"><Field label="Latitude"><input className="form-control form-control-sm" value={shape.lat} onChange={(e) => setShape({ ...shape, lat: Number(e.target.value) })} /></Field></div>
                  <div className="col-6"><Field label="Longitude"><input className="form-control form-control-sm" value={shape.lng} onChange={(e) => setShape({ ...shape, lng: Number(e.target.value) })} /></Field></div>
                </div>
                <button className="btn btn-sm btn-outline-secondary" onClick={() => navigator.geolocation?.getCurrentPosition(
                  (p) => { setShape({ type: "circle", lat: +p.coords.latitude.toFixed(7), lng: +p.coords.longitude.toFixed(7), radius_m: shape.radius_m }); reframe(); },
                  () => toast.error("Could not read your location — click the map instead."))}>
                  <i className="ti ti-current-location me-1" />Use my location
                </button>
              </>
            )}

            {mode === "place" && <PlaceSearch onPick={(p, typed) => {
              setForm((f: any) => ({ ...f, name: f.name || typed || p.name, place_name: p.label, source: "osm", osm_ref: p.osm_ref }));
              // the published outline when the place has one, otherwise the box the place occupies — either way
              // you start from the area that was searched for and adjust it, never from a guessed radius
              if (p.points?.length >= 3) setShape({ type: "polygon", points: p.points });
              else if (p.bbox) setShape({ type: "polygon", points: boxPoints(p.bbox) });
              else setShape({ type: "circle", lat: p.lat, lng: p.lng, radius_m: p.radius_m || 2000 });
              reframe();
            }} />}

            {mode === "place" && shape.type === "polygon" && (
              asBox(shape.points)
                ? <BoxSides shape={shape} onChange={(sh) => setShape(sh)} />
                : <div className="fs-13 mb-3"><i className="ti ti-polygon me-1 text-success" />Using the published outline — {shape.points.length} corners. Drag any corner on the map, or switch to <strong>Draw it</strong>.</div>
            )}

            {mode === "draw" && (
              <div className="mb-3">
                <div className="fs-13">{shape.type === "polygon" ? `${shape.points.length} corner${shape.points.length === 1 ? "" : "s"}` : "No corners yet"}</div>
                {shape.type === "polygon" && shape.points.length < 3 && <Alert kind="warning">Click the map until there are at least three corners.</Alert>}
                {shape.type === "polygon" && shape.points.length >= 3 && (
                  <button className="btn btn-sm btn-outline-secondary mt-2" onClick={() => setShape({ type: "polygon", points: [] })}><i className="ti ti-eraser me-1" />Start again</button>
                )}
              </div>
            )}
          </div></div>

          <div className="card mb-3"><div className="card-body">
            <Field label="Who covers this area?" hint="Their orders, cash collections and new shops are checked against it. A rep with no area is not restricted.">
              <div className="border rounded p-2" style={{ maxHeight: 190, overflowY: "auto" }}>
                {(reps.data || []).map((r: any) => (
                  <label key={r.id} className="form-check d-flex align-items-center gap-2 mb-1">
                    <input className="form-check-input mt-0" type="checkbox" checked={form.reps.includes(r.id)}
                      onChange={(e) => setForm({ ...form, reps: e.target.checked ? [...form.reps, r.id] : form.reps.filter((x: string) => x !== r.id) })} />
                    <span className="fs-13">{r.name}<span className="text-muted fs-11 ms-1">{r.supplier_name}</span></span>
                  </label>
                ))}
                {!(reps.data || []).length && <div className="text-muted fs-12 p-2">No active reps yet.</div>}
              </div>
            </Field>
            <div className="row g-2">
              <div className="col-6"><Field label="Colour on the map"><input type="color" className="form-control form-control-color w-100" value={form.colour} onChange={(e) => setForm({ ...form, colour: e.target.value })} /></Field></div>
              <div className="col-6"><Field label="Active"><div className="form-check form-switch pt-2"><input className="form-check-input" type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} /><span className="fs-12 text-muted ms-2">{form.is_active ? "checks are on" : "paused for everyone"}</span></div></Field></div>
            </div>
            <Field label="Notes"><textarea className="form-control" rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
          </div></div>
        </div>
      </div>

      <ConfirmDialog open={del} title="Delete this working area" danger
        message="The reps who hold it stop being checked against it. Nothing else changes."
        onClose={() => setDel(false)}
        onConfirm={async () => { await api.delete(`/api/v1/geofences/${id}`); toast.success("Area deleted"); nav("/app/customers/areas"); }} />
    </>
  );
}

const KM_PER_DEG = 111.32;
const kmWide = (b: any) => (b.east - b.west) * KM_PER_DEG * Math.cos(((b.north + b.south) / 2 * Math.PI) / 180);
const kmTall = (b: any) => (b.north - b.south) * KM_PER_DEG;
const boxSize = (b: any) => `${kmWide(b).toFixed(1)} × ${kmTall(b).toFixed(1)} km`;

/**
 * The area that was searched for, as a box you push out or pull in one side at a time — the same thing the
 * handles on the map do, for when you want it exact rather than dragged.
 */
function BoxSides({ shape, onChange }: { shape: Extract<Shape, { type: "polygon" }>; onChange: (s: Shape) => void }) {
  const box = asBox(shape.points)!;
  const nudge = (side: "north" | "south" | "east" | "west", km: number) => {
    const mid = (box.north + box.south) / 2;
    const deg = side === "north" || side === "south" ? km / KM_PER_DEG : km / (KM_PER_DEG * Math.cos((mid * Math.PI) / 180));
    const next = { ...box };
    // "+" always makes the area bigger, whichever side it is
    next[side] = side === "north" || side === "east" ? next[side] + deg : next[side] - deg;
    if (next.north - next.south < 0.002 || next.east - next.west < 0.002) return;  // ~200 m floor
    onChange({ type: "polygon", points: boxPoints(next) });
  };
  const Row = ({ side, label }: { side: "north" | "south" | "east" | "west"; label: string }) => (
    <div className="d-flex align-items-center justify-content-between py-1">
      <span className="fs-13">{label}</span>
      <div className="btn-group btn-group-sm">
        <button type="button" className="btn btn-outline-secondary" title="pull this side in" onClick={() => nudge(side, -0.5)}>−</button>
        <button type="button" className="btn btn-outline-primary" title="push this side out" onClick={() => nudge(side, 0.5)}>+</button>
      </div>
    </div>
  );
  return (
    <div className="mb-3">
      <div className="d-flex justify-content-between align-items-center mb-1">
        <span className="fs-13 fw-medium">Area found</span><span className="fs-12 text-muted">{boxSize(box)}</span>
      </div>
      <div className="border rounded px-2 py-1">
        <Row side="north" label="North side" />
        <Row side="south" label="South side" />
        <Row side="east" label="East side" />
        <Row side="west" label="West side" />
      </div>
      <div className="form-text">Each press moves that side 500 m. On the map, drag a side, a corner, or the middle to move the whole area.</div>
    </div>
  );
}

/** Look a place up by name. OpenStreetMap has an outline for some places and only a point for others. */
function PlaceSearch({ onPick }: { onPick: (p: any, typed: string) => void }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [rows, setRows] = useState<any[] | null>(null);
  const run = async () => {
    if (q.trim().length < 3) return;
    setBusy(true);
    try { setRows((await api.get("/api/v1/geofences/search", { q })).results); }
    catch { toast.error("Could not reach the place lookup — drop a pin instead."); }
    finally { setBusy(false); }
  };
  return (
    <div className="mb-3">
      <div className="input-group mb-2">
        <input className="form-control" placeholder="Clifton, Karachi" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && run()} />
        <button className="btn btn-primary" onClick={run} disabled={busy}><i className="ti ti-search" /></button>
      </div>
      {busy && <div className="fs-12 text-muted">Looking…</div>}
      {rows?.length === 0 && <Alert kind="warning">Nothing found. Try the town's name, or drop a pin with “Radius”.</Alert>}
      <div className="list-group">
        {(rows || []).map((p, i) => (
          <button key={i} className="list-group-item list-group-item-action py-2" onClick={() => onPick(p, q.trim())}>
            <div className="fw-medium fs-13">{p.name} <span className="badge bg-light text-dark fs-11">{p.kind}</span></div>
            <div className="text-muted fs-11">{p.label}</div>
            <div className="fs-11 mt-1">{p.points?.length >= 3
              ? <span className="text-success"><i className="ti ti-polygon me-1" />published outline ({p.points.length} corners)</span>
              : p.bbox
                ? <span className="text-primary"><i className="ti ti-crop me-1" />{boxSize(p.bbox)} area — stretch any side after picking it</span>
                : <span className="text-warning"><i className="ti ti-circle me-1" />no shape — a circle you can resize</span>}</div>
          </button>
        ))}
      </div>
    </div>
  );
}

export function GeofenceDetailPage() {
  const { id } = useParams();
  const q = useDoc("/api/v1/geofences", id);
  const inside = useQuery({ queryKey: ["fence-customers", id], queryFn: () => api.get(`/api/v1/geofences/${id}/customers`), enabled: Boolean(id) });
  const events = useQuery({ queryKey: ["fence-events", id], queryFn: () => api.get("/api/v1/geofences/events", { page_size: 20 }), enabled: Boolean(id) });
  const rights = pageRights("geofences");
  const [assign, setAssign] = useState(false);
  if (q.isLoading) return <Spinner />;
  const f = q.data;
  if (!f) return null;
  const pins: MapPin[] = (inside.data?.results || []).map((c: any) => ({ id: c.id, name: c.name, lat: c.gps.lat, lng: c.gps.lng, covered: true, sub: c.code }));
  return (
    <>
      <PageHeader title={f.name} badge={<StatusBadge status={f.is_active ? "active" : "inactive"} />}
        crumbs={[{ label: "Working areas", to: "/app/customers/areas" }, { label: f.name }]}
        actions={<>{pageRights("customers").edit && <button className="btn btn-outline-primary me-2" onClick={() => setAssign(true)}><i className="ti ti-user-plus me-1" />Assign stores to a rep</button>}{rights.edit && <Link className="btn btn-primary" to={`/app/customers/areas/${f.id}/edit`}><i className="ti ti-edit me-1" />Edit area</Link>}</>} />
      <div className="row g-3 mb-3">
        <div className="col-6 col-md-3"><StatCard label="Size" value={`${f.area_km2} km²`} icon="ti-shape" color="primary" sub={f.type === "circle" ? `${(f.radius_m / 1000).toFixed(1)} km radius` : `${(f.points || []).length} corners`} /></div>
        <div className="col-6 col-md-3"><StatCard label="Customers inside" value={inside.data?.count ?? "…"} icon="ti-users" color="success" /></div>
        <div className="col-6 col-md-3"><StatCard label="Covered by" value={f.assignments?.length || 0} icon="ti-user-check" color="info" sub={(f.assignments || []).map((a: any) => a.rep_name).join(", ")} /></div>
        <div className="col-6 col-md-3"><StatCard label="Source" value={f.source === "osm" ? "Place name" : "Drawn"} icon="ti-map-pin" color="secondary" sub={f.place_name} /></div>
      </div>
      <div className="row g-3">
        <div className="col-lg-7"><div className="card"><div className="card-body p-2"><FenceMap fences={[asFence(f)]} pins={pins} height={420} /></div></div></div>
        <div className="col-lg-5">
          <div className="card mb-3"><div className="card-header py-2 d-flex justify-content-between align-items-center"><strong className="fs-13">Stores inside</strong><span className="text-muted fs-12">{inside.data?.count ?? 0}</span></div>
            <div className="card-body p-0 table-responsive" style={{ maxHeight: 200, overflow: "auto" }}>
              <table className="table table-sm fs-12 mb-0"><tbody>
                {(inside.data?.results || []).map((c: any) => <tr key={c.id}><td><Link to={`/app/customers/${c.id}`}>{c.name}</Link><div className="text-muted fs-11">{c.code}</div></td><td className="text-muted fs-11">{(c.reps || []).map((r: any) => r.name).join(", ") || "no rep"}</td></tr>)}
                {!pins.length && <tr><td className="text-muted p-3">No customer pins fall inside this area yet.</td></tr>}
              </tbody></table>
            </div>
          </div>
          <div className="card"><div className="card-header py-2"><strong className="fs-13">Recent location checks</strong></div>
            <div className="card-body p-0 table-responsive" style={{ maxHeight: 240, overflow: "auto" }}>
              <table className="table table-sm fs-12 mb-0"><tbody>
                {(events.data?.results || []).map((e: any) => (
                  <tr key={e.id}>
                    <td>{e.rep_name}<div className="text-muted fs-11">{e.action}</div></td>
                    <td>{e.verdict === "inside" ? <span className="text-success">inside</span> : e.verdict === "no_fix" ? <span className="text-muted">no location</span> : <span className="text-danger">{((e.distance_m || 0) / 1000).toFixed(1)} km out</span>}</td>
                    <td className="text-end text-muted fs-11">{e.blocked ? "refused" : "allowed"}</td>
                  </tr>
                ))}
                {!(events.data?.results || []).length && <tr><td className="text-muted p-3">Nothing checked yet.</td></tr>}
              </tbody></table>
            </div>
          </div>
        </div>
      </div>
      <AssignStoresModal open={assign} fence={f} onClose={() => setAssign(false)} onDone={() => { setAssign(false); inside.refetch(); }} />
    </>
  );
}

export function CoverageMapPage() {
  const q = useQuery({ queryKey: ["fence-map"], queryFn: () => api.get("/api/v1/geofences/map") });
  if (q.isLoading) return <Spinner />;
  const d = q.data;
  const fences: MapFence[] = (d?.fences || []).map(asFence);
  const pins: MapPin[] = (d?.customers || []).map((c: any) => ({ id: c.id, name: c.name, lat: c.lat, lng: c.lng, covered: c.covered, sub: c.route }));
  return (
    <>
      <PageHeader title="Coverage map" crumbs={[{ label: "Working areas", to: "/app/customers/areas" }, { label: "Coverage" }]}
        actions={<Link to="/app/customers/areas/new" className="btn btn-primary"><i className="ti ti-plus me-1" />New area</Link>} />
      <div className="row g-3 mb-3">
        <div className="col-6 col-md-3"><StatCard label="Areas" value={fences.length} icon="ti-map-2" color="primary" /></div>
        <div className="col-6 col-md-3"><StatCard label="Customers on the map" value={pins.length} icon="ti-map-pin" color="success" /></div>
        <div className="col-6 col-md-3"><StatCard label="In no area" value={d?.uncovered ?? 0} icon="ti-alert-triangle" color={d?.uncovered ? "warning" : "secondary"} /></div>
        <div className="col-6 col-md-3"><StatCard label="No pin at all" value={d?.without_pin ?? 0} icon="ti-map-pin-off" color={d?.without_pin ? "warning" : "secondary"} sub="add a location on the customer" /></div>
      </div>
      <div className="card"><div className="card-body p-2">
        <FenceMap fences={fences} pins={pins} height={620} />
        <div className="fs-12 text-muted px-2 pt-2">
          <span className="me-3"><i className="ti ti-circle-filled text-success me-1" />inside an area</span>
          <span><i className="ti ti-circle text-danger me-1" />in no area</span>
        </div>
      </div></div>
    </>
  );
}

/** The shape as the API stores it. */
function shapeToBody(shape: Shape) {
  return shape.type === "circle"
    ? { type: "circle", centre_lat: shape.lat, centre_lng: shape.lng, radius_m: Math.round(shape.radius_m), points: [] }
    : { type: "polygon", points: shape.points, radius_m: 0 };
}

/** Switching from a circle to a drawn area starts from the circle's own box, so nothing is lost. */
function squareAround(shape: Shape): { lat: number; lng: number }[] {
  if (shape.type === "polygon") return shape.points;
  const d = (shape.radius_m || 3000) / 111_320;
  const dx = d / Math.cos((shape.lat * Math.PI) / 180);
  return [
    { lat: +(shape.lat + d).toFixed(7), lng: +(shape.lng - dx).toFixed(7) },
    { lat: +(shape.lat + d).toFixed(7), lng: +(shape.lng + dx).toFixed(7) },
    { lat: +(shape.lat - d).toFixed(7), lng: +(shape.lng + dx).toFixed(7) },
    { lat: +(shape.lat - d).toFixed(7), lng: +(shape.lng - dx).toFixed(7) },
  ];
}

/** The areas a rep covers, and the stores inside them — including the ones not yet handed to this rep. */
export function RepAreasTab({ repId }: { repId: string }) {
  const q = useQuery({ queryKey: ["rep-areas", repId], queryFn: () => api.list("/api/v1/geofences", { rep: repId, page_size: 50 }) });
  const stores = useQuery({ queryKey: ["rep-area-stores", repId], queryFn: () => api.get("/api/v1/customers/map"), enabled: Boolean(repId) });
  const may = usePage("geofences");
  const [assignFence, setAssignFence] = useState<any>(null);
  if (q.isLoading) return <Spinner />;
  const rows = q.data?.results || [];
  const all = stores.data?.stores || [];
  const inArea = (fenceId: string) => all.filter((s: any) => (s.fences || []).includes(fenceId));
  const mine = (s: any) => (s.reps || []).some((r: any) => r.id === repId);
  if (!rows.length) {
    return <Alert kind="info">This rep covers no area, so nothing they do is checked against one. {may.edit && <Link to="/app/customers/areas">Assign an area</Link>}</Alert>;
  }
  return (
    <div className="row g-3">
      <div className="col-lg-7">
        <table className="table table-sm fs-12 align-middle">
          <thead className="table-light"><tr><th>Area</th><th className="num">Size</th><th className="num">Stores inside</th><th className="num">Theirs</th><th /></tr></thead>
          <tbody>{rows.map((f: any) => {
            const inside = inArea(f.id);
            const theirs = inside.filter(mine).length;
            return (
              <tr key={f.id}>
                <td><Link to={`/app/customers/areas/${f.id}`}>{f.name}</Link>
                  <div className="text-muted fs-11">{f.type === "circle" ? `${(f.radius_m / 1000).toFixed(1)} km radius` : "drawn"}{(f.assignments || []).find((a: any) => a.rep === repId)?.paused_until ? " · paused" : ""}</div></td>
                <td className="num">{f.area_km2} km²</td>
                <td className="num">{stores.isLoading ? "…" : inside.length}</td>
                <td className="num">{stores.isLoading ? "…" : <span className={theirs < inside.length ? "text-warning fw-medium" : "text-success"}>{theirs}</span>}</td>
                <td className="text-end">{may.edit && <button className="btn btn-sm btn-outline-primary" onClick={() => setAssignFence(f)}>Assign stores</button>}</td>
              </tr>
            );
          })}</tbody>
        </table>
        {!stores.isLoading && rows.some((f: any) => inArea(f.id).some((s: any) => !mine(s))) && (
          <Alert kind="warning">Some stores inside these areas are not assigned to this rep yet — they will not see them.</Alert>
        )}
      </div>
      <div className="col-lg-5"><FenceMap fences={rows.map(asFence)} pins={rows.flatMap((f: any) => inArea(f.id)).map((s: any) => ({ id: s.id, name: s.name, lat: s.lat, lng: s.lng, covered: mine(s), sub: mine(s) ? "theirs" : "not assigned" }))} height={320} /></div>
      <AssignStoresModal open={Boolean(assignFence)} fence={assignFence} rep={repId} onClose={() => setAssignFence(null)} onDone={() => { setAssignFence(null); stores.refetch(); }} />
    </div>
  );
}
