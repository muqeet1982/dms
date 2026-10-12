import { useAuth } from "@/lib/auth";
import { toast } from "@/lib/toast";
import type { Paginated, Problem } from "./types";

const FIELD_LABELS: Record<string, string> = { sku: "Code / SKU", tax_class: "Tax class", base_uom: "Base unit", cost_price: "D.P (cost price)", sale_price: "T.P (sale price)", retail_price: "R.P (retail price)", expected_date: "Expected date", due_date: "Due date", opening_qty: "Opening quantity", qty_ordered: "Quantity", unit_price: "Unit price" };

/** "expected_date" → "Expected date", "lines.0.variant" → "Line 1 · variant", "non_field_errors" → "" */
function fieldLabel(key: string): string {
  if (key === "non_field_errors" || key === "detail") return "";
  if (FIELD_LABELS[key]) return FIELD_LABELS[key];
  return key
    .split(".")
    .map((part, i, all) => {
      if (/^\d+$/.test(part)) return `${Number(part) + 1}`;
      const words = part.replace(/_/g, " ").trim();
      const nextIsIndex = /^\d+$/.test(all[i + 1] || "");
      const singular = nextIsIndex ? words.replace(/s$/, "") : words;
      return i === 0 ? singular.charAt(0).toUpperCase() + singular.slice(1) : singular;
    })
    .join(" ")
    .replace(/ (\d+) /, " $1 · ");
}

/**
 * One readable sentence for an API problem.
 *
 * The backend sends two different kinds. A **validation error** has a generic `detail` ("One or more fields
 * are invalid.") and the real information in `errors`, so the flattened field list *is* the message. A
 * **business rule** carries a sentence in `detail` written for the person reading it — "You are 1,044 km
 * outside Lahore — Gulberg" — and puts ids and measurements in `errors` for the screen to use.
 *
 * Building from `errors` first threw those sentences away and showed the diagnostics instead: an out-of-area
 * refusal read "Distance M: 1032780 · Fence: Lahore — Gulberg", and a duplicate delivery note read as a raw
 * UUID. So a written detail now wins, and the field list is used where it is the only thing there.
 */
export function describeProblem(problem: Problem): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(problem.errors || {})) {
    const nested = (val: any, prefix: string): void => {
      if (Array.isArray(val)) val.forEach((x, i) => (typeof x === "object" && x ? nested(x, `${prefix}.${i}`) : parts.push(`${fieldLabel(prefix)}${fieldLabel(prefix) ? ": " : ""}${String(x)}`)));
      else if (val && typeof val === "object") Object.entries(val).forEach(([kk, vv]) => nested(vv, `${prefix}.${kk}`));
      else parts.push(`${fieldLabel(prefix)}${fieldLabel(prefix) ? ": " : ""}${String(val)}`);
    };
    nested(v, k);
  }
  const unique = [...new Set(parts.filter(Boolean))];
  const flattened = unique.slice(0, 4).join(" · ") + (unique.length > 4 ? ` (+${unique.length - 4} more)` : "");
  const written = (problem.detail || "").trim();
  // a validation error's detail says nothing; anything else was written to be read
  if (written && problem.type !== "validation_error") return written;
  if (!unique.length) return written || problem.title || "Request failed";
  return flattened;
}

export class ApiError extends Error {
  problem: Problem;
  status: number;
  constructor(problem: Problem) {
    super(describeProblem(problem));
    this.problem = problem;
    this.status = problem.status;
  }
  fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    const errs = this.problem.errors || {};
    for (const [k, v] of Object.entries(errs)) out[k] = Array.isArray(v) ? v.map(String).join(" ") : typeof v === "object" ? JSON.stringify(v) : String(v);
    return out;
  }
}

/**
 * An empty optional date from a form arrives as "" and the API answers "Date has wrong format" — send null instead.
 * Only `*_date` / `date` keys are touched; blank text fields keep their "" (the API stores those as empty strings).
 */
function normalizeBody(value: any): any {
  if (Array.isArray(value)) return value.map(normalizeBody);
  if (value && typeof value === "object" && Object.getPrototypeOf(value) === Object.prototype) {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, v === "" && /(^|_)date$/.test(k) ? null : normalizeBody(v)]));
  }
  return value;
}

const BASE = (import.meta as any).env?.VITE_API_BASE || "";
const LS_KEY = "dms.auth";
const LOCK_KEY = "dms.auth.refreshing";
const TAB = Math.random().toString(36).slice(2);

/** "ok" = there is a usable access token now · "retry" = a transient failure, the session is intact · "dead" = signed out. */
type RefreshResult = "ok" | "retry" | "dead";
let refreshing: Promise<RefreshResult> | null = null;
let lastRefreshAt = 0;

const expiryOf = (jwt: string | null | undefined): number => {
  try {
    return (JSON.parse(atob(jwt!.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))).exp || 0) * 1000;
  } catch {
    return 0;
  }
};
const storedAuth = (): any => {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) || "{}");
  } catch {
    return {};
  }
};

/**
 * Tokens rotate, and every tab shares the same localStorage copy. If another tab refreshed while this one was
 * idle, its tokens are the valid ones — take them instead of sending our rotated token to the server.
 */
function adoptTokensFromOtherTab(): boolean {
  const { access, setTokens } = useAuth.getState();
  const saved = storedAuth();
  if (!saved.access || !saved.refresh || saved.access === access) return false;
  if (expiryOf(saved.access) > expiryOf(access) && expiryOf(saved.access) - Date.now() > 30_000) {
    setTokens(saved.access, saved.refresh);
    return true;
  }
  return false;
}

/** Wait a moment if another tab is mid-refresh, so two tabs don't present the same refresh token. */
async function waitForOtherTab(): Promise<void> {
  for (let i = 0; i < 40; i++) {
    let lock = "";
    try {
      lock = localStorage.getItem(LOCK_KEY) || "";
    } catch {
      return;
    }
    const [owner, at] = lock.split(":");
    if (!lock || owner === TAB || Date.now() - Number(at || 0) > 10_000) return;
    if (adoptTokensFromOtherTab()) return;
    await new Promise((r) => setTimeout(r, 150));
  }
}

async function doRefresh(): Promise<RefreshResult> {
  if (refreshing) return refreshing;
  // A refresh that landed a moment ago already fixed the caller's problem — rotating again would be a second
  // token in flight for nothing (and is what used to end in a surprise logout).
  if (Date.now() - lastRefreshAt < 5_000) return "ok";
  if (adoptTokensFromOtherTab()) return "ok";
  if (!useAuth.getState().refresh) return "dead";
  refreshing = (async (): Promise<RefreshResult> => {
    try {
      await waitForOtherTab();
      if (adoptTokensFromOtherTab()) return "ok";
      const token = storedAuth().refresh || useAuth.getState().refresh;
      if (!token) return "dead";
      try {
        localStorage.setItem(LOCK_KEY, `${TAB}:${Date.now()}`);
      } catch {
        /* private mode */
      }
      const r = await fetch(`${BASE}/api/v1/auth/refresh`, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ refresh: token }) });
      if (r.ok) {
        const d = await r.json();
        useAuth.getState().setTokens(d.access, d.refresh);
        lastRefreshAt = Date.now();
        return "ok";
      }
      if (r.status === 401 || r.status === 403) {
        if (adoptTokensFromOtherTab()) return "ok";
        useAuth.getState().logout();
        return "dead";
      }
      // 429 (throttled) or 5xx / offline: the login is still good, so keep it and let the caller report the error.
      return "retry";
    } catch {
      return "retry";
    } finally {
      try {
        if ((localStorage.getItem(LOCK_KEY) || "").startsWith(TAB)) localStorage.removeItem(LOCK_KEY);
      } catch {
        /* ignore */
      }
      refreshing = null;
    }
  })();
  return refreshing;
}

/** Returns an access token that is valid for at least ~2 more minutes (refreshes if needed). Use before raw XHR/fetch uploads. */
export async function getFreshAccess(force = false): Promise<string | null> {
  const { access } = useAuth.getState();
  if (!access) return null;
  if (force) return (await doRefresh()) === "ok" ? useAuth.getState().access : null;
  const exp = expiryOf(access);
  if (exp && exp - Date.now() < 120_000 && (await doRefresh()) !== "ok") return null;
  return useAuth.getState().access;
}

/** Refresh before a call when the token is about to die, instead of letting a page's parallel calls all 401. */
async function ensureFreshToken(path: string): Promise<void> {
  if (path.startsWith("/api/v1/auth/")) return;
  const { access } = useAuth.getState();
  if (!access) return;
  const exp = expiryOf(access);
  if (exp && exp - Date.now() < 45_000) await doRefresh();
}

export function authHeaders(): Record<string, string> {
  const { access, tenantId } = useAuth.getState();
  const h: Record<string, string> = {};
  if (access) h["Authorization"] = `Bearer ${access}`;
  if (tenantId) h["X-Tenant-ID"] = tenantId;
  return h;
}

export type RequestOptions = { method?: string; body?: any; query?: Record<string, any>; headers?: Record<string, string>; raw?: boolean; idempotent?: boolean; noTenant?: boolean };

export function qs(query?: Record<string, any>): string {
  if (!query) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, String(x)));
    else p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

/** A stale company (an impersonation window that ran out, a membership that was removed) must not look like a logout. */
function dropStaleTenant(detail?: string): void {
  useAuth.getState().setTenant(null);
  toast.warning(detail || "Your session inside that company has ended.");
}

export async function request<T = any>(path: string, opts: RequestOptions = {}, retry = true): Promise<T> {
  if (retry) await ensureFreshToken(path);
  const { access, tenantId } = useAuth.getState();
  const headers: Record<string, string> = { Accept: "application/json", ...(opts.headers || {}) };
  const isForm = typeof FormData !== "undefined" && opts.body instanceof FormData;
  if (opts.body !== undefined && !isForm) headers["Content-Type"] = "application/json";
  if (access) headers["Authorization"] = `Bearer ${access}`;
  if (tenantId && !opts.noTenant) headers["X-Tenant-ID"] = tenantId;
  if (opts.idempotent && !headers["Idempotency-Key"]) headers["Idempotency-Key"] = crypto.randomUUID();
  const url = `${BASE}${path}${qs(opts.query)}`;
  const res = await fetch(url, { method: opts.method || (opts.body !== undefined ? "POST" : "GET"), headers, body: opts.body === undefined ? undefined : isForm ? opts.body : JSON.stringify(normalizeBody(opts.body)) });
  if (res.status === 401 && retry && access) {
    const outcome = await doRefresh();
    if (outcome === "ok") return request<T>(path, opts, false);
    if (outcome === "dead") {
      useAuth.getState().logout();
      window.location.assign("/login");
    }
    // "retry": throttled or the server/network hiccuped — keep the login and report this one call as failed.
  }
  if (res.status === 403 && retry && tenantId && !opts.noTenant) {
    const problem = await res.clone().json().catch(() => null);
    const code = String(problem?.type || "");
    if (code.includes("impersonation_expired") || code.includes("tenant_not_member")) {
      dropStaleTenant(problem?.detail);
      return request<T>(path, opts, false);
    }
  }
  if (opts.raw) {
    if (!res.ok) throw new ApiError(await safeProblem(res));
    return res as any;
  }
  if (res.status === 204) return undefined as any;
  const text = await res.text();
  const data = text ? safeJson(text) : null;
  if (!res.ok) throw new ApiError(data && data.status ? data : { type: "error", title: res.statusText, status: res.status, detail: typeof data === "string" ? data : data?.detail || res.statusText, errors: data?.errors || {} });
  return data as T;
}

function safeJson(t: string) {
  try {
    return JSON.parse(t);
  } catch {
    return t;
  }
}
async function safeProblem(res: Response): Promise<Problem> {
  try {
    return await res.json();
  } catch {
    return { type: "error", title: res.statusText, status: res.status, detail: res.statusText, errors: {} };
  }
}


/**
 * Show a fetched document, falling back to a download when the tab is blocked.
 *
 * `window.open` runs after the `await` that fetched the file, so the click's user activation may already have
 * expired — a slow PDF render then ends in a blocked popup and, as far as the user can tell, a button that
 * does nothing. `open` returns null in that case, so save the file instead.
 */
function openBlobUrl(url: string, path: string): void {
  const win = window.open(url, "_blank");
  if (win) return;
  const a = document.createElement("a");
  a.href = url;
  const last = path.split("?")[0].replace(/\/$/, "").split("/").pop() || "document";
  a.download = /\.[a-z0-9]{2,5}$/i.test(last) ? last : `${last}.pdf`;
  a.click();
  toast.info("Your browser blocked the preview tab, so the file was downloaded instead.");
}

/**
 * Run a document fetch and make a failure visible.
 *
 * Every print and export button in the app is `onClick={() => api.download(...)}` with no `catch`, so a
 * rejected promise was dropped on the floor: a dead endpoint looked exactly like a button that does nothing.
 * Four export endpoints 404'd for months that way. Reporting it here covers every call site at once, instead
 * of relying on each one to remember.
 */
async function withReport<T>(what: string, run: () => Promise<T>): Promise<T | undefined> {
  try {
    return await run();
  } catch (e: any) {
    toast.error(e?.problem?.detail || e?.message || `${what} could not be produced.`);
    return undefined;
  }
}

export const api = {
  get: <T = any>(path: string, query?: Record<string, any>) => request<T>(path, { query }),
  list: <T = any>(path: string, query?: Record<string, any>) => request<Paginated<T>>(path, { query }),
  post: <T = any>(path: string, body?: any, idempotent = false) => request<T>(path, { method: "POST", body: body ?? {}, idempotent }),
  patch: <T = any>(path: string, body: any) => request<T>(path, { method: "PATCH", body }),
  put: <T = any>(path: string, body: any) => request<T>(path, { method: "PUT", body }),
  delete: <T = any>(path: string) => request<T>(path, { method: "DELETE" }),
  upload: <T = any>(path: string, form: FormData, method = "POST") => request<T>(path, { method, body: form }),
  /** POST JSON and open the returned binary (e.g. label PDFs). */
  postBlob: (path: string, body: any, filename?: string) => withReport("That document", async () => {
    const res = (await request<Response>(path, { method: "POST", body, raw: true })) as unknown as Response;
    const url = URL.createObjectURL(await res.blob());
    if (filename) { const a = document.createElement("a"); a.href = url; a.download = filename; a.click(); } else openBlobUrl(url, path);
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }),
  /** Fetch a binary (PDF/CSV/XLSX) with auth headers and open/download it. */
  download: (path: string, filename?: string, query?: Record<string, any>) => withReport("That file", async () => {
    const res = (await request<Response>(path, { query, raw: true })) as unknown as Response;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    if (filename) {
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
    } else openBlobUrl(url, path);
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }),
  /**
   * Open a document in a new tab, with the print dialog ready.
   *
   * The tab is opened **synchronously, on the click**, and only then filled in: a `window.open` after an
   * `await` has lost the click's user activation and is silently blocked, which is why this used to print from
   * a hidden iframe instead. Opening it first and pointing it at the document when it arrives keeps the tab and
   * gives the person the thing itself — they can read it, print it, or keep it open beside the screen.
   */
  printDoc: (path: string, query?: Record<string, any>) => {
    const tab = window.open("", "_blank");
    if (tab) {
      tab.document.write('<!doctype html><title>Preparing…</title><body style="font:14px Helvetica,Arial,sans-serif;color:#6B7280;padding:24px">Preparing the document…</body>');
      tab.document.close();
    }
    return withReport("That document", async () => {
      let url = "";
      try {
        const res = (await request<Response>(path, { query, raw: true })) as unknown as Response;
        url = URL.createObjectURL(await res.blob());
      } catch (e) {
        tab?.close();
        throw e;
      }
      if (!tab) {
        // the browser refused the tab: fall back to showing it however it will, rather than losing the document
        openBlobUrl(url, path);
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
        return;
      }
      tab.location.replace(url);
      // the viewer needs a moment to lay the PDF out; printing before that prints a blank page
      setTimeout(() => {
        try {
          tab.focus();
          tab.print();
        } catch {
          /* the tab has the document either way, and its own print button */
        }
      }, 900);
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    });
  },
};
