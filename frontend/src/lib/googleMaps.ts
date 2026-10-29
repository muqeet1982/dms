import { api } from "@/api/client";

/**
 * Google Maps for the map screens and the place search (owner, 28 Oct 2026: "use google map and google search now
 * instead of other map"). The platform setting chooses Google and the server hands over the browser key; the
 * script is loaded once, on the first map or search that needs it.
 *
 * The MAP and the SEARCH are separate Google services (Maps JavaScript API, Places API (New)) and a key can hold one
 * without the other — on 29 Oct the search worked while the map answered ApiNotActivatedMapError. So each falls back
 * on its own: a refused map goes back to the ordinary tiles (the search stays on Google), a refused search goes to
 * the OpenStreetMap lookup. Only a script that will not load at all turns both off.
 */
let loading: Promise<any> | null = null;
let scriptFailed = false;    // the script itself did not load — nothing Google can work
let mapFailed = false;       // Google refused to draw a map with this key
let mapCheck: Promise<boolean> | null = null;
const failures = new Set<() => void>();

/** Has Google refused the MAP (the search may still be fine)? */
export const googleFailed = () => mapFailed || scriptFailed;

/** Called when Google refuses the map — every map on screen goes back to the ordinary tiles. */
export function onGoogleFailure(fn: () => void) {
  failures.add(fn);
  return () => { failures.delete(fn); };
}

function failMap() {
  if (mapFailed) return;
  mapFailed = true;
  failures.forEach((fn) => fn());
}

/** Google's refusal box ("This page can't load Google Maps correctly") inside a map's element. */
const refusedIn = (el: HTMLElement) => /load Google Maps correctly/i.test(el.textContent || "");

/**
 * Keep an eye on a live map's element for a while: a refusal that shows up after the first check still sends
 * every map back to the ordinary tiles. Returns the way to stop watching.
 */
export function watchForRefusal(el: HTMLElement, ms = 20000): () => void {
  const obs = new MutationObserver(() => { if (refusedIn(el)) { failMap(); obs.disconnect(); } });
  obs.observe(el, { childList: true, subtree: true, characterData: true });
  const t = setTimeout(() => obs.disconnect(), ms);
  return () => { clearTimeout(t); obs.disconnect(); };
}

/** The Google script, loaded once. Resolves even when the map is refused — the search does not need the map. */
export function loadGoogleMaps(key: string): Promise<any> {
  const w = window as any;
  if (scriptFailed) return Promise.reject(new Error("Google Maps did not load"));
  if (w.google?.maps?.importLibrary) return Promise.resolve(w.google);
  if (loading) return loading;
  w.gm_authFailure = failMap;   // Google's own word that the key may not draw maps
  loading = new Promise((resolve, reject) => {
    w.__dmsGoogleReady = async () => {
      try { await w.google.maps.importLibrary("maps"); resolve(w.google); }
      catch (e) { scriptFailed = true; failMap(); reject(e); }
    };
    const s = document.createElement("script");
    s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&v=weekly&loading=async&libraries=places&region=PK&callback=__dmsGoogleReady`;
    s.async = true;
    s.onerror = () => { loading = null; scriptFailed = true; failMap(); reject(new Error("Google Maps did not load")); };
    document.head.appendChild(s);
  });
  return loading;
}

/**
 * May this key draw Google's map? Asked once per page. A project without billing gets a dimmed map and an error
 * box, never `gm_authFailure` (seen 28 Oct 2026: BillingNotEnabledMapError) — so a small map is drawn first and the
 * box looked for. It must be on screen (an off-screen map is never checked), so it is see-through and ignores the
 * mouse. A refusal shows within half a second; a second after the tiles with none, the map is good.
 */
export function googleMapUsable(key: string): Promise<boolean> {
  if (mapFailed || scriptFailed) return Promise.resolve(false);
  if (mapCheck) return mapCheck;
  mapCheck = loadGoogleMaps(key).then((g) => new Promise<boolean>((done) => {
    const div = document.createElement("div");
    div.style.cssText = "position:fixed;left:0;bottom:0;width:240px;height:240px;opacity:0;pointer-events:none;z-index:-1";
    document.body.appendChild(div);
    const probe = new g.maps.Map(div, { center: { lat: 31.52, lng: 74.35 }, zoom: 10, disableDefaultUI: true });
    let tilesAt = 0;
    g.maps.event.addListenerOnce(probe, "tilesloaded", () => { tilesAt = Date.now(); });
    let n = 0;
    const t = setInterval(() => {
      const refused = mapFailed || refusedIn(div);
      const settled = (tilesAt && Date.now() - tilesAt > 1000) || ++n >= 24;
      if (refused || settled) {
        clearInterval(t);
        div.remove();
        if (refused) failMap();
        done(!refused);
      }
    }, 250);
  }), () => false);
  return mapCheck;
}

/** What the map screens draw on, as the platform branding hands it over. */
export type MapConfig = { url: string; attribution: string; source?: string; google_key?: string };
let config: MapConfig | null = null;
export const setGoogleConfig = (cfg: MapConfig | null) => { config = cfg; };
/** The key for Google's SEARCH — independent of whether the map was refused. */
export const googleKey = () => (config?.source === "google" && config.google_key && !scriptFailed ? config.google_key : "");

/** A place as both lookups answer it: a centre always, a box when the place has an extent. */
export type Place = { name: string; label: string; kind: string; lat: number; lng: number; points: { lat: number; lng: number }[];
  bbox: { south: number; north: number; west: number; east: number } | null; radius_m: number };

/**
 * Search a place by name or address — Google Places when Google is on, else (or when Google refuses) the
 * server's OpenStreetMap lookup, which also knows published area outlines.
 */
export async function searchPlaces(q: string): Promise<Place[]> {
  const key = googleKey();
  if (key) {
    try {
      const g = await loadGoogleMaps(key);
      const { Place: GPlace } = await g.maps.importLibrary("places");
      const { places } = await GPlace.searchByText({
        textQuery: q, fields: ["displayName", "formattedAddress", "location", "viewport", "types"], region: "pk", maxResultCount: 6,
      });
      return (places || []).filter((p: any) => p.location).map((p: any) => {
        const vp = p.viewport, ne = vp?.getNorthEast(), sw = vp?.getSouthWest();
        return {
          name: p.displayName || "", label: p.formattedAddress || p.displayName || "",
          kind: String((p.types || [])[0] || "place").replace(/_/g, " "),
          lat: p.location.lat(), lng: p.location.lng(), points: [],
          bbox: vp ? { south: sw.lat(), north: ne.lat(), west: sw.lng(), east: ne.lng() } : null, radius_m: 0,
        };
      });
    } catch { /* the Places API not switched on for the key, or a quota — the OpenStreetMap lookup still answers */ }
  }
  return (await api.get("/api/v1/geofences/search", { q })).results;
}

/** One live suggestion while typing — like Google's own search box. `pick` resolves it to a place. */
export type Suggestion = { id: string; main: string; secondary: string; pick: () => Promise<Place | null> };

/**
 * Address suggestions as the user types (Google Places autocomplete), Pakistan first. `null` when Google is off
 * or refuses — the box then searches on Enter instead (the OpenStreetMap lookup does not allow type-ahead use).
 * One session token per search box ties the keystrokes and the final pick into one billed session.
 */
export async function suggestPlaces(input: string, session: { token?: any }): Promise<Suggestion[] | null> {
  const key = googleKey();
  if (!key) return null;
  try {
    const g = await loadGoogleMaps(key);
    const { AutocompleteSuggestion, AutocompleteSessionToken } = await g.maps.importLibrary("places");
    session.token = session.token || new AutocompleteSessionToken();
    const { suggestions } = await AutocompleteSuggestion.fetchAutocompleteSuggestions({
      input, sessionToken: session.token, includedRegionCodes: ["pk"], region: "pk",
    });
    return (suggestions || []).filter((s: any) => s.placePrediction).map((s: any) => {
      const p = s.placePrediction;
      return {
        id: p.placeId, main: p.mainText?.text || p.text?.text || "", secondary: p.secondaryText?.text || "",
        pick: async () => {
          const place = p.toPlace();
          await place.fetchFields({ fields: ["displayName", "formattedAddress", "location", "viewport", "types"] });
          session.token = undefined;   // a pick closes the session
          if (!place.location) return null;
          const vp = place.viewport, ne = vp?.getNorthEast(), sw = vp?.getSouthWest();
          return {
            name: place.displayName || "", label: place.formattedAddress || place.displayName || "",
            kind: String((place.types || [])[0] || "place").replace(/_/g, " "),
            lat: place.location.lat(), lng: place.location.lng(), points: [],
            bbox: vp ? { south: sw.lat(), north: ne.lat(), west: sw.lng(), east: ne.lng() } : null, radius_m: 0,
          };
        },
      };
    });
  } catch { return null; }
}
