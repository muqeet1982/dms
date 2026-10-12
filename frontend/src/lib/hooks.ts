import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { toast } from "./toast";

/** Load one document. */
export function useDoc<T = any>(endpoint: string, id?: string | null, opts: { enabled?: boolean } = {}) {
  return useQuery<T>({ queryKey: ["doc", endpoint, id], queryFn: () => api.get(`${endpoint}/${id}`), enabled: Boolean(id) && opts.enabled !== false });
}

/** Load a small option list (unpaginated or first page). */
export function useOptions(endpoint: string, query?: Record<string, any>, enabled = true) {
  return useQuery<any[]>({
    queryKey: ["options", endpoint, query],
    queryFn: async () => { const d = await api.get(endpoint, { page_size: 500, ...(query || {}) }); return Array.isArray(d) ? d : d.results; },
    // cached between renders, but always re-read when a form opens — a warehouse or price list added elsewhere
    // must appear in the dropdown without a hard refresh (we never poll in the background).
    staleTime: 30_000,
    refetchOnMount: "always",
    enabled,
  });
}

/** POST an action on a document (confirm, post, cancel …) with toast + invalidation. */
export function useAction(endpoint: string, id?: string | null, extraKeys: any[][] = []) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (action: string, body: any = {}, opts: { success?: string; silent?: boolean } = {}) => {
    setBusy(action);
    try {
      const r = await api.post(`${endpoint}/${id}/${action}`, body);
      if (!opts.silent) toast.success(opts.success || `Done: ${action.replace(/-/g, " ")}`);
      qc.invalidateQueries({ queryKey: ["doc", endpoint, id] });
      qc.invalidateQueries({ queryKey: ["list", endpoint] });
      qc.invalidateQueries({ queryKey: ["history"] });
      qc.invalidateQueries({ queryKey: ["journals-for"] });
      qc.invalidateQueries({ queryKey: ["movements-for"] });
      for (const k of extraKeys) qc.invalidateQueries({ queryKey: k });
      return r;
    } catch (e: any) {
      toast.error(e.message);
      throw e;
    } finally { setBusy(null); }
  };
  return { run, busy };
}

export function useSave(endpoint: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body, method }: { id?: string | null; body: any; method?: "patch" | "put" }) => (id ? (method === "put" ? api.put(`${endpoint}/${id}`, body) : api.patch(`${endpoint}/${id}`, body)) : api.post(endpoint, body, true)),
    onSuccess: (_d, v) => { qc.invalidateQueries({ queryKey: ["list", endpoint] }); if (v.id) qc.invalidateQueries({ queryKey: ["doc", endpoint, v.id] }); },
  });
}


/** Active warehouses with the tenant's default first — forms preselect `defaultId`. */
export function useWarehouses(query?: Record<string, any>) {
  const q = useOptions("/api/v1/settings/warehouses", { is_active: true, ...(query || {}) });
  const rows = q.data || [];
  return { ...q, rows, defaultId: (rows.find((w: any) => w.is_default) || rows[0])?.id as string | undefined };
}
