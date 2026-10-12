import { create } from "zustand";
import type { Me, Membership, User } from "@/api/types";

type AuthState = {
  access: string | null;
  refresh: string | null;
  tenantId: string | null;
  user: User | null;
  memberships: Membership[];
  me: Me | null;
  setTokens: (access: string, refresh: string) => void;
  setLogin: (data: { access: string; refresh: string; user: User; memberships: Membership[] }) => void;
  setTenant: (tenantId: string | null) => void;
  setMe: (me: Me | null) => void;
  logout: () => void;
};

const LS = "dms.auth";
const load = () => {
  try {
    return JSON.parse(localStorage.getItem(LS) || "{}");
  } catch {
    return {};
  }
};
const persist = (s: Partial<AuthState>) => {
  try {
    localStorage.setItem(LS, JSON.stringify({ access: s.access, refresh: s.refresh, tenantId: s.tenantId, user: s.user, memberships: s.memberships }));
  } catch {
    /* ignore */
  }
};

const saved = load();
export const useAuth = create<AuthState>((set, get) => ({
  access: saved.access ?? null,
  refresh: saved.refresh ?? null,
  tenantId: saved.tenantId ?? null,
  user: saved.user ?? null,
  memberships: saved.memberships ?? [],
  me: null,
  setTokens: (access, refresh) => {
    set({ access, refresh });
    persist(get());
  },
  setLogin: ({ access, refresh, user, memberships }) => {
    // a different person on the same browser must not be shown the last one's lists
    if (get().user && get().user!.id !== user.id) dropCache();
    const tenantId = user.is_platform_admin ? null : memberships.length === 1 ? memberships[0].tenant_id : get().tenantId && memberships.some((m) => m.tenant_id === get().tenantId) ? get().tenantId : null;
    set({ access, refresh, user, memberships, tenantId, me: null });
    persist(get());
  },
  setTenant: (tenantId) => {
    // see lib/queryClient: the cache is not keyed by company, so changing company has to empty it
    if (get().tenantId !== tenantId) dropCache();
    set({ tenantId, me: null });
    persist(get());
  },
  setMe: (me) => set({ me }),
  logout: () => {
    dropCache();
    set({ access: null, refresh: null, tenantId: null, user: null, memberships: [], me: null });
    localStorage.removeItem(LS);
  },
}));

/** Empty the react-query cache. Imported lazily: nothing here should depend on React's render order. */
function dropCache() {
  import("./queryClient").then(({ queryClient }) => queryClient.clear()).catch(() => { /* cache will refetch anyway */ });
}

export const hasPerm = (codename: string | string[] | undefined | null): boolean => {
  if (!codename) return true;
  const me = useAuth.getState().me;
  if (!me) return false;
  if (me.is_owner) return true;
  const list = Array.isArray(codename) ? codename : [codename];
  return list.some((c) => me.permissions.includes(c));
};

export const hasFeature = (key: string) => Boolean(useAuth.getState().me?.features?.[key]);
export const isPlatformAdmin = () => Boolean(useAuth.getState().user?.is_platform_admin);
export const hasPlatformPerm = (codename: string) => isPlatformAdmin() && (useAuth.getState().me?.platform_permissions.includes(codename) ?? false);
