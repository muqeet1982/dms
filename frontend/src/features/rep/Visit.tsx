import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { toast } from "@/lib/toast";

export type Visit = {
  gps: { lat: number; lng: number } | null;
  accuracy: number | null;
  status: "idle" | "locating" | "ok" | "denied";
  photo: string | null;          // a data URL, sent with the order
  photoAt: string | null;        // when it was taken — the server refuses one that is not from a moment ago
  setPhoto: (p: string | null) => void;
  locate: () => void;
  areas: { policy: string; location_required: boolean; areas: any[] } | undefined;
  inside: boolean | null;        // against the rep's own working areas
  distance_m: number | null;
  areaName: string;
};

/** Metres between two points — the same haversine the server uses, so the screen and the rule agree. */
function distance(a: { lat: number; lng: number }, b: { lat: number; lng: number }) {
  const R = 6371008.8, rad = (d: number) => (d * Math.PI) / 180;
  const h = Math.sin(rad(b.lat - a.lat) / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(rad(b.lng - a.lng) / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

/** Metres from a point to the nearest edge of a shape — so "outside" can say how far, exactly as the server does. */
function distanceToRing(p: { lat: number; lng: number }, ring: { lat: number; lng: number }[]) {
  const R = 6371008.8, rad = (d: number) => (d * Math.PI) / 180;
  const mx = Math.cos(rad(p.lat)) * R;                       // metres per radian of longitude here
  const xy = (q: { lat: number; lng: number }) => [rad(q.lng) * mx, rad(q.lat) * R];
  const [px, py] = xy(p);
  let best = Infinity;
  for (let i = 0; i < ring.length; i++) {
    const [ax, ay] = xy(ring[i]);
    const [bx, by] = xy(ring[(i + 1) % ring.length]);
    const dx = bx - ax, dy = by - ay;
    const t = dx === 0 && dy === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)));
    best = Math.min(best, Math.hypot(px - (ax + t * dx), py - (ay + t * dy)));
  }
  return best;
}

function inPolygon(p: { lat: number; lng: number }, ring: { lat: number; lng: number }[]) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[i], b = ring[j];
    if ((a.lat > p.lat) !== (b.lat > p.lat) && p.lng < ((b.lng - a.lng) * (p.lat - a.lat)) / (b.lat - a.lat) + a.lng) inside = !inside;
  }
  return inside;
}

/**
 * The rep's visit: where they are, and the photo of the shop.
 *
 * The same check the server runs is done here first, so a rep is told they are outside their area *before* they
 * fill a cart, rather than having the order refused at the end.
 */
export function useVisit(): Visit {
  const [gps, setGps] = useState<{ lat: number; lng: number } | null>(null);
  const [accuracy, setAccuracy] = useState<number | null>(null);
  const [status, setStatus] = useState<Visit["status"]>("idle");
  const [photo, setPhotoRaw] = useState<string | null>(null);
  const [photoAt, setPhotoAt] = useState<string | null>(null);
  // the capture time is stamped here, where the photo arrives, so a check-in cannot claim a stale picture is live
  const setPhoto = (p: string | null) => { setPhotoRaw(p); setPhotoAt(p ? new Date().toISOString() : null); };
  const asked = useRef(false);
  const areas = useQuery({ queryKey: ["my-areas"], queryFn: () => api.get("/ext/v1/my-areas"), staleTime: 300_000, retry: false });

  const locate = () => {
    if (!navigator.geolocation) return setStatus("denied");
    setStatus("locating");
    navigator.geolocation.getCurrentPosition(
      (p) => { setGps({ lat: +p.coords.latitude.toFixed(7), lng: +p.coords.longitude.toFixed(7) }); setAccuracy(Math.round(p.coords.accuracy)); setStatus("ok"); },
      () => setStatus("denied"),
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 30000 },
    );
  };
  useEffect(() => { if (!asked.current) { asked.current = true; locate(); } }, []);

  let inside: boolean | null = null;
  let distance_m: number | null = null;
  let areaName = "";
  const list = areas.data?.areas || [];
  if (gps && list.length) {
    inside = false;
    for (const a of list) {
      const ring = a.points || [];
      const d = a.type === "circle" && a.centre
        ? Math.max(0, distance(gps, a.centre) - (a.radius_m || 0))
        : ring.length >= 3
          ? (inPolygon(gps, ring) ? 0 : distanceToRing(gps, ring))
          : null;
      if (d === 0) { inside = true; distance_m = 0; areaName = a.name; break; }
      if (d !== null && (distance_m === null || d < distance_m)) { distance_m = Math.round(d); areaName = a.name; }
    }
    if (!inside && !areaName) areaName = list[0].name;
  }
  return { gps, accuracy, status, photo, photoAt, setPhoto, locate, areas: areas.data, inside, distance_m, areaName };
}

/** The strip at the top of a visit screen: where you are, whether that is inside your area, and the shop photo. */
export function VisitCard({ visit, required }: { visit: Visit; required?: boolean }) {
  const file = useRef<HTMLInputElement>(null);
  const needPhoto = required ?? visit.areas?.policy !== "off";
  const take = (f: File | null) => {
    if (!f) return;
    if (f.size > 6_000_000) return toast.error("That photo is very large — try again.");
    const reader = new FileReader();
    reader.onload = () => visit.setPhoto(String(reader.result));
    reader.readAsDataURL(f);
  };
  // the owner's rules (20 Oct 2026): areas say nothing to a rep, and "at the shop" is a state EARNED by
  // checking in — never claimed from a GPS fix. This strip states position facts only.
  const tone = visit.status === "ok" ? "success" : visit.status === "denied" ? "warning" : "secondary";
  return (
    <div className={`card mb-3 border-${tone}`}><div className="card-body py-2">
      <div className="d-flex flex-wrap align-items-center gap-3">
        <div className="flex-grow-1">
          <div className="fs-13 fw-medium">
            {visit.status === "locating" && <><i className="ti ti-current-location me-1 text-primary" />Finding where you are…</>}
            {visit.status === "denied" && <><i className="ti ti-map-pin-off me-1 text-warning" />Location is off — turn it on so your visit and orders are recorded properly</>}
            {visit.status === "ok" && <><i className="ti ti-map-pin me-1 text-success" />Location is on</>}
          </div>
          <div className="fs-11 text-muted">
            {visit.gps ? `${visit.gps.lat.toFixed(5)}, ${visit.gps.lng.toFixed(5)}${visit.accuracy ? ` · ±${visit.accuracy} m` : ""}` : "no location yet"}
          </div>
        </div>
        {visit.status !== "ok" && <button className="btn btn-sm btn-outline-primary" onClick={visit.locate}><i className="ti ti-refresh me-1" />Try again</button>}
        {needPhoto && (
          <div className="d-flex align-items-center gap-2">
            {visit.photo
              ? <><img src={visit.photo} alt="shop" style={{ height: 44, width: 44, objectFit: "cover", borderRadius: 6 }} />
                  <button className="btn btn-sm btn-outline-secondary" onClick={() => visit.setPhoto(null)}>Retake</button></>
              : <button className="btn btn-sm btn-outline-primary" onClick={() => file.current?.click()}><i className="ti ti-camera me-1" />Shop photo</button>}
            <input ref={file} type="file" accept="image/*" capture="environment" className="d-none" onChange={(e) => take(e.target.files?.[0] || null)} />
          </div>
        )}
      </div>
    </div></div>
  );
}
