import { useState } from "react";
import { api } from "@/api/client";
import { toast } from "@/lib/toast";
import { FenceMap } from "@/components/map/FenceMap";

/**
 * The shop's place, designed as two columns (owner, 24 Oct 2026): the MAP on the left — zoom, click, drag
 * the pin, drag the circle's edge to widen the order radius — and the SETTINGS on the right: search, typed
 * Latitude/Longitude, a pasted WhatsApp / Google Maps link, and the radius as a slider with − / +.
 *
 * The pin is what every order's distance is measured against, so it is worth more than two number boxes.
 */
export function StorePinPicker({ lat, lng, name, onChange, compact, radius, onRadiusChange }: {
  lat: any; lng: any; name?: string; onChange: (lat: number | null, lng: number | null) => void; compact?: boolean;
  radius?: any; onRadiusChange?: (m: number | null) => void;
}) {
  const [q, setQ] = useState("");
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState(false);
  const [nonce, setNonce] = useState(0);
  const point = lat !== "" && lat !== null && lat !== undefined && lng !== "" && lng !== null && lng !== undefined
    ? { lat: Number(lat), lng: Number(lng) } : null;
  const r = Number(radius) > 0 ? Number(radius) : 50;
  const put = (la: number, ln: number) => { onChange(+la.toFixed(7), +ln.toFixed(7)); setNonce((n) => n + 1); };
  const setR = (m: number) => onRadiusChange?.(Math.min(1000, Math.max(10, Math.round(m))));

  const search = async () => {
    const text = q.trim() || name || "";
    if (text.length < 3) return toast.error("Type an address or a landmark to search for.");
    setBusy(true);
    try {
      const res = await api.get("/api/v1/geofences/search", { q: text });
      const hit = res.results?.[0];
      if (!hit) return toast.error("Nothing found — zoom the map and click the shop, or paste a location link.");
      put(hit.lat, hit.lng);
      toast.success(`Pin moved to ${hit.label?.split(",").slice(0, 2).join(",")}`);
    } catch { toast.error("Could not reach the place lookup — click the map instead."); }
    finally { setBusy(false); }
  };
  const parseLink = async () => {
    const text = link.trim();
    if (!text) return toast.error("Paste a WhatsApp or Google Maps location link first.");
    setBusy(true);
    try {
      const res = await api.post("/api/v1/customers/parse-location", { text });
      put(res.lat, res.lng);
      setLink("");
      toast.success("Pin set from the link");
    } catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
  };

  const label = (t: string) => <div className="form-label fs-12 mb-1 mt-2">{t}</div>;
  return (
    <div className="row g-3">
      <div className="col-md-7">
        <FenceMap
          editing={point && onRadiusChange ? { type: "circle", lat: point.lat, lng: point.lng, radius_m: r } : null}
          onChange={(s) => { if (s.type === "circle") { onChange(+s.lat.toFixed(7), +s.lng.toFixed(7)); setR(s.radius_m); } }}
          point={point && !onRadiusChange ? point : null}
          onPointChange={put}
          onMapClick={(la, ln) => { if (!point) put(la, ln); }}
          fit={false}
          fitNonce={nonce}
          cursor={point ? undefined : "crosshair"}
          height={compact ? 300 : 360}
        />
        <div className="form-text">
          {point ? <>Drag the pin to the shop door; drag the circle's edge to widen the order radius. Zoom with +/−.</>
                 : <>Zoom in with +/− and click where the shop is — or use the search, coordinates or a pasted link on the right.</>}
        </div>
      </div>
      <div className="col-md-5">
        {label("Search address")}
        <div className="input-group">
          <input className="form-control" placeholder="Shop, street or landmark…" value={q} onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); search(); } }} />
          <button type="button" className="btn btn-outline-secondary" disabled={busy} onClick={search}><i className="ti ti-search" /></button>
        </div>
        {label("Latitude / Longitude")}
        <div className="d-flex gap-2">
          <input type="number" step="0.0000001" className="form-control" placeholder="Latitude" value={lat ?? ""}
            onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value), lng === "" || lng === null || lng === undefined ? null : Number(lng))} />
          <input type="number" step="0.0000001" className="form-control" placeholder="Longitude" value={lng ?? ""}
            onChange={(e) => onChange(lat === "" || lat === null || lat === undefined ? null : Number(lat), e.target.value === "" ? null : Number(e.target.value))} />
        </div>
        {label("Paste a WhatsApp / Google Maps location")}
        <div className="input-group">
          <input className="form-control" placeholder="https://maps.google.com/?q=…  or  geo:…" value={link} onChange={(e) => setLink(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); parseLink(); } }} />
          <button type="button" className="btn btn-outline-secondary" disabled={busy} onClick={parseLink}><i className="ti ti-map-pin-plus" /></button>
        </div>
        {onRadiusChange && <>
          {label(`Order radius — rep must be within ${r} m to order`)}
          <div className="d-flex align-items-center gap-2">
            <button type="button" className="btn btn-sm btn-outline-secondary px-2" onClick={() => setR(r - 10)}>−</button>
            <input type="range" className="form-range" style={{ maxWidth: 150 }} min={10} max={300} step={5} value={Math.min(r, 300)} onChange={(e) => setR(Number(e.target.value))} />
            <button type="button" className="btn btn-sm btn-outline-secondary px-2" onClick={() => setR(r + 10)}>+</button>
            <div className="input-group flex-nowrap" style={{ width: 130, minWidth: 130 }}>
              <input type="number" className="form-control text-end" value={r} onChange={(e) => setR(Number(e.target.value) || 50)} />
              <span className="input-group-text">m</span>
            </div>
          </div>
        </>}
        <div className="d-flex flex-wrap gap-2 mt-3">
          <button type="button" className="btn btn-outline-secondary" onClick={() => navigator.geolocation?.getCurrentPosition(
            (p) => put(p.coords.latitude, p.coords.longitude),
            () => toast.error("Could not read your location — click the map instead."))}>
            <i className="ti ti-current-location me-1" />My location
          </button>
          {point ? <button type="button" className="btn btn-outline-danger" onClick={() => onChange(null, null)}><i className="ti ti-trash me-1" />Clear pin</button>
                 : <button type="button" className="btn btn-outline-secondary" onClick={() => onChange(null, null)} title="Save the shop without a pin — the rep's app offers 'Save where I am' on their first visit"><i className="ti ti-clock me-1" />Pin later</button>}
        </div>
        {!point && <div className="form-text mt-2">No pin — the shop can be saved now and pinned later: the rep's app offers "Save where I am" on their first visit.</div>}
      </div>
    </div>
  );
}
