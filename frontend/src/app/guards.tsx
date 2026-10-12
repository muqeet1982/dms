import { useEffect, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useAuth } from "@/lib/auth";
import type { Me } from "@/api/types";
import { Spinner } from "@/components/ui/Bits";

/** The two tenant front ends behind one login: the office app at /app and the rep portal at /rep. */
export type Side = "office" | "rep";
const SIDE_KEY = "dms.side";

/**
 * Which side(s) a login may open: `/me.has_office_role` for the office app (an office role or ownership — the
 * server resolves it from the roles themselves, so a field role that happens to grant the rep's own check-in
 * screens is not mistaken for office work) and the rep link for the portal. `dual` is deliberately narrower than
 * `office && rep`: an owner gets into /rep by ownership rather than by being a rep, and does not want a switcher
 * for a portal that has no customers of its own.
 */
export function sidesOf(me?: Me | null) {
  // `pages` is the fallback for a client that outlives the server it was built against: deployments are not
  // atomic, and without it a dual-role login would be forced back into /rep until the backend caught up.
  const office = me?.has_office_role ?? (Boolean(me?.is_owner) || Object.keys(me?.pages || {}).some((k) => me?.pages?.[k]?.view && k !== "visits" && k !== "tracking"));
  return { office, rep: Boolean(me?.supplier_rep) || Boolean(me?.is_owner), dual: office && Boolean(me?.supplier_rep) };
}

/** Which shell a URL belongs to. The path is the truth about where the user is and cannot disagree with itself. */
export function sideOfPath(pathname: string): Side | null {
  return pathname.startsWith("/rep") ? "rep" : pathname.startsWith("/app") ? "office" : null;
}

export function useSides() {
  return sidesOf(useAuth((s) => s.me));
}

export function savedSide(): Side | null {
  try {
    const v = localStorage.getItem(SIDE_KEY);
    return v === "office" || v === "rep" ? v : null;
  } catch {
    return null;
  }
}

/** `null` forgets the choice, so a login that no longer holds both sides is never sent to one it cannot open. */
export function rememberSide(side: Side | null) {
  try {
    if (side) localStorage.setItem(SIDE_KEY, side);
    else localStorage.removeItem(SIDE_KEY);
  } catch {
    /* ignore */
  }
}

// Login, "/" and company-select each pick a shell without asking: that first screen is the only one a saved side
// may override. Everything after it is a link the user chose — including the switcher — so it is left alone.
let landed = false;

/** Loads /me for the current token + tenant and stores it; redirects to login when unauthenticated. */
export function RequireAuth({ mode }: { mode: "tenant" | "platform" | "rep" | "any" }) {
  const { access, tenantId, memberships, user, setMe, me, setTenant } = useAuth();
  const loc = useLocation();
  const [landing] = useState(() => !landed);
  useEffect(() => { landed = true; }, []);
  // The access token is part of the key so that `/me` is re-read when the credential changes — but the token
  // rotates every ~14 minutes (15-minute lifetime, refreshed pre-emptively), and without `placeholderData` the
  // new key starts empty, the guard below falls to its spinner, and `<Outlet/>` unmounts: every open form is
  // destroyed mid-use. That cost a storeman an afternoon of counting on the goods-receipt screen.
  //
  // The placeholder belongs to the observer rather than to the shape of the key, so it would carry an answer
  // across a *company* change just as willingly — leaving `cur` describing the company the user just left. So
  // it is handed back only when the previous answer was for this same tenant.
  const q = useQuery<Me>({ queryKey: ["me", tenantId, access?.slice(-12)], queryFn: () => api.get("/api/v1/me"), enabled: Boolean(access), staleTime: 60_000, placeholderData: (prev, prevQuery) => (prevQuery?.queryKey?.[1] === tenantId ? prev : undefined), retry: (count, err: any) => count < 2 && (err?.status ?? 0) >= 500 });
  useEffect(() => { if (q.data) setMe(q.data); }, [q.data, setMe]);
  // A platform admin only "enters" a tenant through an impersonation session; when it has ended, drop the tenant and go back to the admin panel.
  const admin = Boolean(user?.is_platform_admin);
  const staleImpersonation = admin && Boolean(tenantId) && Boolean(q.data) && !q.data!.impersonating;
  useEffect(() => { if (staleImpersonation) setTenant(null); }, [staleImpersonation, setTenant]);
  if (!access) return <Navigate to="/login" state={{ from: loc }} replace />;
  if (user?.must_change_password && loc.pathname !== "/change-password") return <Navigate to="/change-password" replace />;
  if (staleImpersonation) return <Navigate to="/admin" replace />;
  // A failed /me is not a logout: a real auth failure already cleared the tokens (and lands on /login above), so
  // anything left here is the server or the network — offer a retry instead of throwing the user out.
  // ...and only when there is nothing to carry on with. A failed refetch at the rotation mark used to put this
  // card up in place of the page — the same destroyed form as above, through a different door, and one dropped
  // request is enough because a network error (status 0) is not retried. With a workspace already loaded the
  // failure is not worth throwing the user's work away for; the next rotation tries again.
  if (q.isError && !me) {
    const problem = q.error as any;
    return (
      <div className="vh-100 d-flex align-items-center justify-content-center text-center p-4">
        <div style={{ maxWidth: 420 }}>
          <i className="ti ti-cloud-off fs-18 text-muted" />
          <h5 className="mt-2">Couldn't load your workspace</h5>
          <p className="text-muted fs-13">{problem?.message || "The server did not answer."} You are still signed in.</p>
          <button className="btn btn-primary" onClick={() => q.refetch()}><i className="ti ti-refresh me-1" />Try again</button>
          <button className="btn btn-link text-muted d-block mx-auto mt-1 fs-12" onClick={() => { useAuth.getState().logout(); window.location.assign("/login"); }}>Sign out instead</button>
        </div>
      </div>
    );
  }
  // Only a genuine first load has nothing to show. During a token rotation the stored `me` is still accurate,
  // so the page stays mounted and keeps whatever the user was typing.
  if (!q.data && !me) return <div className="vh-100 d-flex align-items-center justify-content-center"><Spinner text="Loading your workspace…" /></div>;
  const cur = (q.data || me)!; // freshest /me, falling back to the store while a new token's copy loads
  if (mode === "platform" && !admin) return <Navigate to="/" replace />;
  if (mode === "rep" && admin) return <Navigate to={tenantId ? "/app" : "/admin"} replace />; // admins impersonate the owner, never a rep
  if (mode === "tenant" && admin && !tenantId) return <Navigate to="/admin" replace />;
  if ((mode === "tenant" || mode === "rep") && !tenantId) {
    if (memberships.length > 1) return <Navigate to="/select-company" replace />;
    if (memberships.length === 0) return <div className="vh-100 d-flex align-items-center justify-content-center text-center"><div><i className="ti ti-building-off fs-1 text-muted" /><p className="mt-2">Your account is not a member of any company. Contact your administrator.</p></div></div>;
  }
  const sides = sidesOf(cur);
  const side: Side | null = mode === "tenant" ? "office" : mode === "rep" ? "rep" : null;
  // A rep with no office pages has nothing to open in /app, so the portal is still the whole of their app; a rep
  // who also holds office pages now stays wherever they are and uses the switcher in the menu.
  if (mode === "tenant" && !admin && sides.rep && !sides.office) return <Navigate to="/rep" replace />;
  if (mode === "rep" && !sides.rep) return <Navigate to="/app" replace />;
  // Someone who holds both comes back to the side they last worked on — only on the landing screen and only from
  // the shell's own root, so a deep link into the other side still opens what it points at.
  const saved = landing && !admin && sides.dual ? savedSide() : null;
  if (side && saved && saved !== side && loc.pathname === (side === "rep" ? "/rep" : "/app")) return <Navigate to={saved === "rep" ? "/rep" : "/app"} replace />;
  return <Outlet />;
}

export function HomeRedirect() {
  const { access, user, memberships, tenantId } = useAuth();
  if (!access) return <Navigate to="/login" replace />;
  // A platform admin who is *inside* a company belongs in that company's home, not back in the admin panel.
  // Ejecting them was read as being signed out of the tenant — which it effectively was, since leaving drops
  // the impersonation context they were working in.
  if (user?.is_platform_admin && !tenantId) return <Navigate to="/admin" replace />;
  if (!tenantId && memberships.length > 1) return <Navigate to="/select-company" replace />;
  // /me is not loaded here (the guard fetches it), so the saved side is the only thing that tells a dual-role
  // login apart from a plain rep; the guard checks it against /me and corrects it when it has gone stale.
  // Without one, the account's own type decides: a login created as a rep opens the portal, office staff the app.
  const saved = savedSide();
  return <Navigate to={(saved ? saved === "rep" : user?.user_type === "EXTERNAL") ? "/rep" : "/app"} replace />;
}

/**
 * A page the user has no switch for is not reachable, however they got to the URL. `action` defaults to "view";
 * an "add" page (a create form) needs the Add switch as well, so typing the URL is not a way around the matrix.
 */
export function RequirePage({ page, action = "view", children }: { page: string; action?: "view" | "add" | "edit" | "delete"; children: JSX.Element }) {
  const me = useAuth((s) => s.me);
  const rights = me?.pages?.[page];
  if (rights?.view && (action === "view" || rights?.[action])) return children;
  const label = { view: "View", add: "Add", edit: "Edit", delete: "Delete" }[action];
  return (
    <div className="card"><div className="card-body text-center py-5">
      <i className="ti ti-lock fs-18 text-muted" />
      <h5 className="mt-2">Not allowed</h5>
      <p className="text-muted fs-13 mb-0">You don't have permission for this page. Ask an administrator to switch “{label}” on for it.</p>
    </div></div>
  );
}

export function RequirePerm({ perm, children }: { perm: string | string[]; children: JSX.Element }) {
  const me = useAuth((s) => s.me);
  const ok = me?.is_owner || (Array.isArray(perm) ? perm : [perm]).some((p) => me?.permissions.includes(p));
  if (!ok) return <div className="card"><div className="card-body text-center py-5"><i className="ti ti-lock fs-1 text-muted" /><h5 className="mt-2">Not allowed</h5><p className="text-muted">You don't have permission to view this page.</p></div></div>;
  return children;
}
