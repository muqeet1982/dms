import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { googleFailed, loadGoogleMaps, MapConfig, onGoogleFailure, setGoogleConfig, watchForRefusal } from "@/lib/googleMaps";

/**
 * The map behind working areas. One component covers both jobs:
 *
 * * **showing** areas and customer pins (the list, the coverage map, a rep's page);
 * * **editing** one area — drag the circle's edge to resize it, drag a corner to reshape a drawn area, click the
 *   map to add a corner, right-click a corner to remove it.
 *
 * Leaflet is loaded as a plain library rather than through a React wrapper: the map is one mutable object that
 * React should not try to re-render, so it is created once and told about changes through effects.
 */
export type Shape =
  | { type: "circle"; lat: number; lng: number; radius_m: number }
  | { type: "polygon"; points: { lat: number; lng: number }[] };

/**
 * Is this polygon a plain box — four corners, sides running north/south and east/west? That is what a place
 * search gives you, and a box is worth editing side by side rather than corner by corner.
 */
export function asBox(points: { lat: number; lng: number }[]) {
  if (points.length !== 4) return null;
  const lats = [...new Set(points.map((p) => +p.lat.toFixed(6)))];
  const lngs = [...new Set(points.map((p) => +p.lng.toFixed(6)))];
  if (lats.length !== 2 || lngs.length !== 2) return null;
  return { south: Math.min(...lats), north: Math.max(...lats), west: Math.min(...lngs), east: Math.max(...lngs) };
}

/** The four corners of a box, anticlockwise from the north-west — the shape a fence stores. */
export function boxPoints(b: { south: number; north: number; west: number; east: number }) {
  return [
    { lat: +b.north.toFixed(7), lng: +b.west.toFixed(7) },
    { lat: +b.north.toFixed(7), lng: +b.east.toFixed(7) },
    { lat: +b.south.toFixed(7), lng: +b.east.toFixed(7) },
    { lat: +b.south.toFixed(7), lng: +b.west.toFixed(7) },
  ];
}

export type MapFence = { id?: string; name: string; colour?: string; shape: Shape; faded?: boolean };
export type MapPin = { id: string; name: string; lat: number; lng: number; covered?: boolean; sub?: string; selected?: boolean };

// The tile source comes from the platform settings (Admin → Settings). This is only what the first paint uses,
// before /api/v1/branding has answered — so it has to be a source that always works.
const TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';

let tileConfig: MapConfig = { url: TILES, attribution: ATTRIBUTION };
const tileListeners = new Set<(c: typeof tileConfig) => void>();

/**
 * Called when the public branding loads. Maps already on screen swap their tiles there and then — the branding
 * usually answers a moment after the first map has been built, and a map that ignored it would keep the fallback.
 */
export function setMapTiles(cfg?: Partial<MapConfig> | null) {
  if (!cfg?.url) return;
  const next: MapConfig = { url: cfg.url, attribution: cfg.attribution || ATTRIBUTION, source: cfg.source, google_key: cfg.google_key };
  setGoogleConfig(next);
  if (next.url === tileConfig.url && next.source === tileConfig.source && next.google_key === tileConfig.google_key) return;
  tileConfig = next;
  tileListeners.forEach((fn) => fn(tileConfig));
}

/**
 * The base map under the areas and pins. Google's own map when the platform chose it and the key works — drawn by
 * GoogleMutant, which runs the official Google Maps JavaScript map beneath Leaflet so every editing tool here keeps
 * working. The ordinary tiles show while Google loads, and come back for good if Google refuses the key.
 * Returns the way to take it all off again.
 */
function attachBase(m: L.Map, cfg: MapConfig): () => void {
  let tiles: L.TileLayer | null = null;
  let google: L.Layer | null = null;
  let gone = false;
  let unwatch = () => {};
  const showTiles = () => {
    if (tiles || gone) return;
    tiles = L.tileLayer(cfg.url, { maxZoom: 19, attribution: cfg.attribution }).addTo(m);
    tiles.bringToBack();
  };
  showTiles();
  const off = onGoogleFailure(() => { google?.remove(); google = null; showTiles(); });
  if (cfg.source === "google" && cfg.google_key && !googleFailed()) {
    loadGoogleMaps(cfg.google_key).then(async () => {
      if (gone || googleFailed()) return;
      // the plugin registers itself on the global Leaflet, which a bundled import does not always set
      (window as any).L = (window as any).L || L;
      const { default: GoogleMutant } = await import("leaflet.gridlayer.googlemutant");
      if (gone || googleFailed()) return;
      google = new (GoogleMutant as any)({ type: "roadmap", maxZoom: 21 }).addTo(m);
      (google as any).bringToBack?.();
      unwatch = watchForRefusal(m.getContainer());
      tiles?.remove();
      tiles = null;
    }).catch(() => showTiles());
  }
  return () => { gone = true; off(); unwatch(); google?.remove(); tiles?.remove(); };
}
const LAHORE: [number, number] = [31.5204, 74.3587];

/** The handles are drawn in CSS rather than Leaflet's packaged marker images, which bundlers break. */
const handleIcon = (kind: "centre" | "edge" | "corner" | "side") => L.divIcon({ className: `fence-handle fence-handle-${kind}`, html: "<span></span>", iconSize: [20, 20], iconAnchor: [10, 10] });

function shapeLayer(fence: MapFence, colour: string) {
  const style = { color: colour, weight: 2, fillColor: colour, fillOpacity: fence.faded ? 0.05 : 0.15 };
  return fence.shape.type === "circle"
    ? L.circle([fence.shape.lat, fence.shape.lng], { ...style, radius: fence.shape.radius_m })
    : L.polygon(fence.shape.points.map((p) => [p.lat, p.lng] as [number, number]), style);
}

/** A dot for a store: filled when an area covers it, hollow when nothing does, ringed when it is the one selected. */
function pinMarker(pin: MapPin) {
  return L.circleMarker([pin.lat, pin.lng], {
    radius: pin.selected ? 9 : 5,
    color: pin.selected ? "#F26522" : pin.covered ? "#15803d" : "#b91c1c",
    fillColor: pin.covered ? "#22c55e" : "#fff",
    fillOpacity: 1,
    weight: pin.selected ? 4 : 2,
  }).bindTooltip(`${pin.name}${pin.sub ? ` · ${pin.sub}` : ""}`, { direction: "top" });
}

export function FenceMap({
  fences = [], pins = [], editing, onChange, height = 460, fit = true, fitNonce = 0, onMapClick, className = "",
  point, onPointChange, onPinClick, onPinMove, cursor,
}: {
  fences?: MapFence[];
  pins?: MapPin[];
  /** The shape being edited, if any — drag handles appear for it. */
  editing?: Shape | null;
  onChange?: (shape: Shape) => void;
  height?: number;
  fit?: boolean;
  /** Bump this to re-frame the map on the shape being edited — not on every drag, which would fight the user. */
  fitNonce?: number;
  onMapClick?: (lat: number, lng: number) => void;
  className?: string;
  /** One draggable pin — how a store's location is set on its own form. */
  point?: { lat: number; lng: number } | null;
  onPointChange?: (lat: number, lng: number) => void;
  onPinClick?: (id: string) => void;
  /** Lets a store pin be dragged to correct where it stands. */
  onPinMove?: (id: string, lat: number, lng: number) => void;
  /** A hint on the cursor, e.g. "crosshair" while placing something. */
  cursor?: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const shown = useRef<L.LayerGroup | null>(null);
  const edit = useRef<L.LayerGroup | null>(null);
  // while a handle is being dragged the layers are already up to date, so the effect must not rebuild them
  // underneath Leaflet — doing so leaves it holding a removed element
  const dragging = useRef(false);
  const latest = useRef({ editing, onChange, onMapClick, onPinClick, onPinMove, onPointChange });
  latest.current = { editing, onChange, onMapClick, onPinClick, onPinMove, onPointChange };
  const [ready, setReady] = useState(false);

  // create the map once
  useEffect(() => {
    if (!host.current || map.current) return;
    const m = L.map(host.current, { center: LAHORE, zoom: 11, zoomControl: true, attributionControl: true });
    let detach = attachBase(m, tileConfig);
    const swap = (cfg: MapConfig) => { detach(); detach = attachBase(m, cfg); };
    tileListeners.add(swap);
    shown.current = L.layerGroup().addTo(m);
    edit.current = L.layerGroup().addTo(m);
    m.on("click", (e: L.LeafletMouseEvent) => latest.current.onMapClick?.(e.latlng.lat, e.latlng.lng));
    map.current = m;
    setReady(true);
    // the container is often laid out after this runs (a card, a tab), so take a second look
    setTimeout(() => m.invalidateSize(), 120);
    return () => { tileListeners.delete(swap); detach(); m.remove(); map.current = null; };
  }, []);

  // the areas and pins to show
  useEffect(() => {
    if (!ready || !shown.current || !map.current) return;
    shown.current.clearLayers();
    const bounds: L.LatLngBounds[] = [];
    for (const f of fences) {
      const layer = shapeLayer(f, f.colour || "#F26522");
      layer.bindTooltip(f.name, { sticky: true });
      layer.addTo(shown.current);
      bounds.push(layer.getBounds());
    }
    for (const p of pins) {
      const marker = pinMarker(p).addTo(shown.current);
      if (latest.current.onPinClick) marker.on("click", (e: any) => { L.DomEvent.stopPropagation(e); latest.current.onPinClick!(p.id); });
      if (latest.current.onPinMove) {
        // dragging the dot itself would fight the map, so the move is done with a handle on the selected store
        marker.bindTooltip(`${p.name}${p.sub ? ` · ${p.sub}` : ""}${p.selected ? " — drag the orange handle to move it" : ""}`, { direction: "top" });
      }
    }
    if (fit && !editing && (bounds.length || pins.length)) {
      let box = bounds.length ? bounds[0] : L.latLngBounds([pins[0].lat, pins[0].lng], [pins[0].lat, pins[0].lng]);
      bounds.slice(1).forEach((b) => (box = box.extend(b)));
      pins.forEach((p) => (box = box.extend([p.lat, p.lng])));
      if (box.isValid()) map.current.fitBounds(box.pad(0.15), { maxZoom: 15 });
    }
  }, [ready, JSON.stringify(fences), JSON.stringify(pins), fit, Boolean(editing)]);

  // frame the shape being edited when the caller asks — on open, on a mode switch, after a place is picked
  useEffect(() => {
    if (!ready || !map.current || !editing) return;
    // worked out from the numbers, not from a layer: an unattached circle has no bounds to give
    const box = editing.type === "circle"
      ? L.latLng(editing.lat, editing.lng).toBounds(Math.max(400, editing.radius_m * 2))
      : L.latLngBounds(editing.points.map((p) => [p.lat, p.lng] as [number, number]));
    if (box.isValid()) map.current.fitBounds(box.pad(0.25), { maxZoom: 15 });
  }, [ready, fitNonce]);

  // one point: a store's own pin, dragged to where the shop stands
  useEffect(() => {
    if (!ready || !map.current) return;
    const group = L.layerGroup().addTo(map.current);
    if (point) {
      const marker = L.marker([point.lat, point.lng], { draggable: true, icon: handleIcon("centre"), title: "Drag to where the shop is" }).addTo(group);
      marker.on("dragstart", () => { dragging.current = true; });
      marker.on("dragend", (e: any) => {
        const p = e.target.getLatLng();
        dragging.current = false;
        (latest.current.onPointChange || latest.current.onPinMove) && latest.current.onPointChange?.(+p.lat.toFixed(7), +p.lng.toFixed(7));
      });
    }
    return () => { group.remove(); };
  }, [ready, point?.lat, point?.lng, Boolean(point)]);

  // the cursor says what a click will do
  useEffect(() => {
    if (!ready || !host.current) return;
    host.current.style.cursor = cursor || "";
  }, [ready, cursor]);

  // the shape being edited, with its handles
  useEffect(() => {
    if (!ready || !edit.current || !map.current || dragging.current) return;
    const group = edit.current;
    group.clearLayers();
    const shape = editing;
    if (!shape) return;
    const push = (s: Shape) => latest.current.onChange?.(s);

    if (shape.type === "circle") {
      const circle = L.circle([shape.lat, shape.lng], { radius: shape.radius_m, color: "#F26522", weight: 2, fillOpacity: 0.15 }).addTo(group);
      const centre = L.marker([shape.lat, shape.lng], { draggable: true, icon: handleIcon("centre"), title: "Drag to move the whole area" }).addTo(group);
      // a second handle on the edge: dragging it is how far the area reaches
      const edgeAt = (lat: number, lng: number, r: number) => L.latLng(lat, lng + (r / 111_320) / Math.cos((lat * Math.PI) / 180));
      const edge = L.marker(edgeAt(shape.lat, shape.lng, shape.radius_m), { draggable: true, icon: handleIcon("edge"), title: "Drag to change how far the area reaches" }).addTo(group);

      centre.on("dragstart", () => { dragging.current = true; });
      centre.on("drag", (e: any) => {
        const p = e.target.getLatLng();
        circle.setLatLng(p);
        edge.setLatLng(edgeAt(p.lat, p.lng, circle.getRadius()));
      });
      centre.on("dragend", (e: any) => {
        const p = e.target.getLatLng();
        dragging.current = false;
        push({ type: "circle", lat: +p.lat.toFixed(7), lng: +p.lng.toFixed(7), radius_m: Math.round(circle.getRadius()) });
      });

      edge.on("dragstart", () => { dragging.current = true; });
      edge.on("drag", (e: any) => circle.setRadius(Math.max(50, map.current!.distance(centre.getLatLng(), e.target.getLatLng()))));
      edge.on("dragend", (e: any) => {
        const c = centre.getLatLng();
        dragging.current = false;
        push({ type: "circle", lat: +c.lat.toFixed(7), lng: +c.lng.toFixed(7), radius_m: Math.max(50, Math.round(map.current!.distance(c, e.target.getLatLng()))) });
      });
      return;
    }

    const box = asBox(shape.points);
    if (box) {
      const poly = L.polygon(boxPoints(box).map((p) => [p.lat, p.lng] as [number, number]), { color: "#F26522", weight: 2, fillOpacity: 0.15 }).addTo(group);
      const push = (b: typeof box) => {
        // a side can be dragged past its opposite; keep the box the right way round
        const fixed = { south: Math.min(b.south, b.north), north: Math.max(b.south, b.north), west: Math.min(b.west, b.east), east: Math.max(b.west, b.east) };
        latest.current.onChange?.({ type: "polygon", points: boxPoints(fixed) });
      };
      const redraw = (b: typeof box) => poly.setLatLngs(boxPoints(b).map((p) => [p.lat, p.lng] as [number, number]));
      const mid = (a: number, c: number) => (a + c) / 2;

      // one handle per side: drag it to move that side alone
      const sides: { key: "north" | "south" | "east" | "west"; at: () => [number, number]; title: string }[] = [
        { key: "north", at: () => [box.north, mid(box.west, box.east)], title: "Drag to move the north side" },
        { key: "south", at: () => [box.south, mid(box.west, box.east)], title: "Drag to move the south side" },
        { key: "east", at: () => [mid(box.south, box.north), box.east], title: "Drag to move the east side" },
        { key: "west", at: () => [mid(box.south, box.north), box.west], title: "Drag to move the west side" },
      ];
      for (const side of sides) {
        const handle = L.marker(side.at(), { draggable: true, icon: handleIcon("side"), title: side.title }).addTo(group);
        const working = { ...box };
        handle.on("dragstart", () => { dragging.current = true; });
        handle.on("drag", (e: any) => {
          const p = e.target.getLatLng();
          if (side.key === "north" || side.key === "south") working[side.key] = p.lat;
          else working[side.key] = p.lng;
          redraw(working);
        });
        handle.on("dragend", () => { dragging.current = false; push(working); });
      }

      // and one per corner, which moves the two sides that meet there
      const corners: [("north" | "south"), ("east" | "west")][] = [["north", "west"], ["north", "east"], ["south", "east"], ["south", "west"]];
      for (const [ns, ew] of corners) {
        const handle = L.marker([box[ns], box[ew]], { draggable: true, icon: handleIcon("corner"), title: `Drag to move the ${ns}-${ew} corner` }).addTo(group);
        const working = { ...box };
        handle.on("dragstart", () => { dragging.current = true; });
        handle.on("drag", (e: any) => { const p = e.target.getLatLng(); working[ns] = p.lat; working[ew] = p.lng; redraw(working); });
        handle.on("dragend", () => { dragging.current = false; push(working); });
      }

      // the middle moves the whole box without changing its size
      const centre = L.marker([mid(box.south, box.north), mid(box.west, box.east)], { draggable: true, icon: handleIcon("centre"), title: "Drag to move the whole area" }).addTo(group);
      const span = { lat: box.north - box.south, lng: box.east - box.west };
      const moved = { ...box };
      centre.on("dragstart", () => { dragging.current = true; });
      centre.on("drag", (e: any) => {
        const p = e.target.getLatLng();
        Object.assign(moved, { south: p.lat - span.lat / 2, north: p.lat + span.lat / 2, west: p.lng - span.lng / 2, east: p.lng + span.lng / 2 });
        redraw(moved);
      });
      centre.on("dragend", () => { dragging.current = false; push(moved); });
      return;
    }

    const pts = shape.points;
    const poly = L.polygon(pts.map((p) => [p.lat, p.lng] as [number, number]), { color: "#F26522", weight: 2, fillOpacity: 0.15 }).addTo(group);
    pts.forEach((p, i) => {
      const handle = L.marker([p.lat, p.lng], { draggable: true, icon: handleIcon("corner"), title: `Corner ${i + 1} — drag to move, right-click to remove` }).addTo(group);
      handle.on("dragstart", () => { dragging.current = true; });
      handle.on("drag", (e: any) => {
        const q = e.target.getLatLng();
        poly.setLatLngs(pts.map((r, j) => (j === i ? [q.lat, q.lng] : [r.lat, r.lng]) as [number, number]));
      });
      handle.on("dragend", (e: any) => {
        const q = e.target.getLatLng();
        dragging.current = false;
        push({ type: "polygon", points: pts.map((r, j) => (j === i ? { lat: +q.lat.toFixed(7), lng: +q.lng.toFixed(7) } : r)) });
      });
      handle.on("contextmenu", () => {
        if (pts.length <= 3) return;  // three corners is the least an area can have
        push({ type: "polygon", points: pts.filter((_, j) => j !== i) });
      });
    });
  }, [ready, JSON.stringify(editing)]);

  return <div ref={host} className={`fence-map rounded border ${className}`} style={{ height }} />;
}

/** Where to drop a new area when the user has nothing selected: the middle of what is on screen, else Lahore. */
export const DEFAULT_CENTRE = { lat: LAHORE[0], lng: LAHORE[1] };
