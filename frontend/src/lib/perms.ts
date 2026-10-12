import { useAuth, hasPerm, hasFeature } from "./auth";

/** Re-renders when `me` changes; returns permission helpers. */
export function usePerms() {
  const me = useAuth((s) => s.me);
  return {
    me,
    can: (codename?: string | string[] | null) => (me ? (me.is_owner ? true : !codename ? true : (Array.isArray(codename) ? codename : [codename]).some((c) => me.permissions.includes(c))) : false),
    feature: (k: string) => Boolean(me?.features?.[k]),
    isOwner: Boolean(me?.is_owner),
    isRep: Boolean(me?.supplier_rep),
  };
}
export { hasPerm, hasFeature };

export type PageRights = { view: boolean; add: boolean; edit: boolean; delete: boolean; extras: Record<string, boolean> };
const NONE: PageRights = { view: false, add: false, edit: false, delete: false, extras: {} };

/**
 * What the signed-in user may do on one page, straight from `/me` (`apps/accounts/page_permissions.py` decides).
 * The server enforces the same switches, so hiding a button here and refusing the call there can never disagree.
 */
export function usePage(key: string): PageRights & { can: (extra: string) => boolean } {
  const me = useAuth((s) => s.me);
  const rights = (me?.pages?.[key] as PageRights | undefined) || NONE;
  return { ...NONE, ...rights, can: (extra: string) => Boolean(rights.extras?.[extra]) };
}

/** Same thing outside a component (event handlers, route loaders). */
export function pageRights(key: string): PageRights {
  return { ...NONE, ...((useAuth.getState().me?.pages?.[key] as PageRights | undefined) || NONE) };
}
