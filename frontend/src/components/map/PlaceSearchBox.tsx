import { useEffect, useRef, useState } from "react";
import { toast } from "@/lib/toast";
import { Place, searchPlaces, suggestPlaces, Suggestion } from "@/lib/googleMaps";

/**
 * A search box that answers like Google's (owner, 28 Oct 2026: "search is not working like google search for
 * address"): suggestions drop down as you type and a click picks the place. With Google off or refusing the key
 * it searches on Enter / the button instead and lists what the OpenStreetMap lookup found.
 */
export function PlaceSearchBox({ onPick, placeholder = "Shop, street or landmark…", initial = "" }: {
  onPick: (p: Place) => void; placeholder?: string; initial?: string;
}) {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<{ key: string; main: string; secondary: string; go: () => Promise<Place | null> }[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [live, setLive] = useState(true);   // false once Google has said no — then Enter searches
  const session = useRef<{ token?: any }>({});
  const box = useRef<HTMLDivElement>(null);

  // type-ahead: a quarter-second after the last key
  useEffect(() => {
    const text = q.trim();
    if (!live || text.length < 3) { if (live) setRows(null); return; }
    let stale = false;
    const t = setTimeout(async () => {
      const s: Suggestion[] | null = await suggestPlaces(text, session.current);
      if (stale) return;
      if (s === null) { setLive(false); setRows(null); return; }
      setRows(s.map((x) => ({ key: x.id, main: x.main, secondary: x.secondary, go: x.pick })));
    }, 250);
    return () => { stale = true; clearTimeout(t); };
  }, [q, live]);

  // a click outside closes the list
  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setRows(null); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const search = async () => {
    const text = q.trim() || initial;
    if (text.length < 3) return toast.error("Type an address or a landmark to search for.");
    setBusy(true);
    try {
      const found = await searchPlaces(text);
      if (!found.length) return toast.error("Nothing found — zoom the map and click the shop, or paste a location link.");
      setRows(found.map((p, i) => ({ key: String(i), main: p.name || p.label.split(",")[0], secondary: p.label, go: async () => p })));
    } catch { toast.error("Could not reach the place lookup — click the map instead."); }
    finally { setBusy(false); }
  };

  const choose = async (r: NonNullable<typeof rows>[number]) => {
    setRows(null);
    setBusy(true);
    try {
      const p = await r.go();
      if (!p) return toast.error("That place has no location — try another.");
      setQ(r.main);
      onPick(p);
    } catch { toast.error("Could not open that place — try again."); }
    finally { setBusy(false); }
  };

  return (
    <div className="position-relative" ref={box}>
      <div className="input-group">
        <input className="form-control" placeholder={placeholder} value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); if (rows?.length) choose(rows[0]); else search(); }
            if (e.key === "Escape") setRows(null);
          }} />
        <button type="button" className="btn btn-outline-secondary" disabled={busy} onClick={search}><i className={`ti ${busy ? "ti-loader-2" : "ti-search"}`} /></button>
      </div>
      {rows && rows.length > 0 && (
        <div className="list-group position-absolute w-100 shadow-sm" style={{ zIndex: 1060, maxHeight: 280, overflowY: "auto" }}>
          {rows.map((r) => (
            <button key={r.key} type="button" className="list-group-item list-group-item-action py-2" onClick={() => choose(r)}>
              <div className="fs-13 fw-medium"><i className="ti ti-map-pin me-1 text-muted" />{r.main}</div>
              {r.secondary && r.secondary !== r.main && <div className="fs-11 text-muted">{r.secondary}</div>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
