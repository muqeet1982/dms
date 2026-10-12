import { useState } from "react";
import { api } from "@/api/client";
import { toast } from "@/lib/toast";
import { FenceMap } from "@/components/map/FenceMap";

/**
 * Where the shop is, on the customer's own form. Three ways to set it, because a distributor fills this in from
 * three different places: standing outside the shop (use my location), looking at a map (click or drag), or
 * reading an address off a list (search it).
 *
 * The pin is what makes a store fall inside a rep's working area, so it is worth more than two number boxes.
 */
export function StorePinPicker({ lat, lng, name, onChange }: {
  lat: any; lng: any; name?: string; onChange: (lat: number | null, lng: number | null) => void;
}) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const point = lat !== "" && lat !== null && lat !== undefined && lng !== "" && lng !== null && lng !== undefined
    ? { lat: Number(lat), lng: Number(lng) } : null;
  const [nonce, setNonce] = useState(0);
  const put = (la: number, ln: number) => { onChange(+la.toFixed(7), +ln.toFixed(7)); setNonce((n) => n + 1); };

  const find = async () => {
    const text = q.trim() || name || "";
    if (text.length < 3) return toast.error("Type an address or a landmark to search for.");
    setBusy(true);
    try {
      const r = await api.get("/api/v1/geofences/search", { q: text });
      const hit = r.results?.[0];
      if (!hit) return toast.error("Nothing found — click the map instead.");
      put(hit.lat, hit.lng);
      toast.success(`Pin moved to ${hit.label?.split(",").slice(0, 2).join(",")}`);
    } catch { toast.error("Could not reach the place lookup — click the map instead."); }
    finally { setBusy(false); }
  };

  return (
    <div className="mb-2">
      <div className="d-flex flex-wrap gap-2 mb-2">
        <div className="input-group" style={{ maxWidth: 420 }}>
          <input className="form-control" placeholder="Search an address or landmark" value={q} onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); find(); } }} />
          <button type="button" className="btn btn-outline-secondary" disabled={busy} onClick={find}><i className="ti ti-search" /></button>
        </div>
        <button type="button" className="btn btn-outline-secondary" onClick={() => navigator.geolocation?.getCurrentPosition(
          (p) => put(p.coords.latitude, p.coords.longitude),
          () => toast.error("Could not read your location — click the map instead."))}>
          <i className="ti ti-current-location me-1" />Use my location
        </button>
        {point && <button type="button" className="btn btn-outline-danger" onClick={() => onChange(null, null)}><i className="ti ti-trash me-1" />Clear pin</button>}
      </div>
      <FenceMap
        point={point}
        onPointChange={put}
        onMapClick={(la, ln) => put(la, ln)}
        fitNonce={nonce}
        cursor={point ? undefined : "crosshair"}
        height={300}
      />
      <div className="form-text">
        {point ? <>Pin at {point.lat.toFixed(5)}, {point.lng.toFixed(5)} — drag it or click elsewhere to move it.</>
               : <>Click the map where the shop is. Without a pin this store cannot fall inside any working area.</>}
      </div>
    </div>
  );
}
