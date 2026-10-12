import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { Spinner } from "@/components/ui/Bits";

type Action = { action: string; label: string; codename: string };
type Extra = { key: string; label: string; perm: string | string[] };
type Page = { key: string; label: string; path: string; note: string; actions: Action[]; extras: Extra[] };
type Group = { group: string; pages: Page[] };

const COLUMNS = ["view", "add", "edit", "delete"] as const;
const COLUMN_LABELS: Record<string, string> = { view: "View", add: "Add", edit: "Edit", delete: "Delete" };
const first = (perm: string | string[]) => (Array.isArray(perm) ? perm[0] : perm);
const allOf = (perm: string | string[]) => (Array.isArray(perm) ? perm : [perm]);

/**
 * Permissions as one switch per action per page — the shape the server enforces (`page_permissions.py`).
 * "View" is the gate: with it off the page is not reachable, so the other switches on that row go off with it,
 * and switching Add/Edit/Delete on turns View on.
 */
export function PermissionMatrix({ value, onChange }: { value: string[]; onChange: (codenames: string[]) => void }) {
  const q = useQuery<Group[]>({ queryKey: ["permission-pages"], queryFn: () => api.get("/api/v1/permission-pages"), staleTime: 600_000 });
  const [filter, setFilter] = useState("");
  const held = useMemo(() => new Set(value), [value]);
  if (q.isLoading) return <Spinner />;
  const groups = q.data || [];

  const set = (codes: string[], on: boolean) => {
    const next = new Set(held);
    codes.forEach((c) => (on ? next.add(c) : next.delete(c)));
    onChange([...next]);
  };
  const setCell = (p: Page, action: string, on: boolean) => {
    const next = new Set(held);
    const code = p.actions.find((a) => a.action === action)?.codename;
    if (!code) return;
    const view = p.actions.find((a) => a.action === "view")?.codename;
    if (on) {
      next.add(code);
      if (view) next.add(view); // you cannot act on a page you cannot open
    } else {
      next.delete(code);
      if (action === "view") {
        p.actions.forEach((a) => next.delete(a.codename));
        p.extras.forEach((e) => allOf(e.perm).forEach((c) => next.delete(c)));
      }
    }
    onChange([...next]);
  };
  const pageCodes = (p: Page) => [...p.actions.map((a) => a.codename), ...p.extras.map((e) => first(e.perm))];
  const rowOn = (p: Page) => p.actions.every((a) => held.has(a.codename));
  const setRow = (p: Page, on: boolean) => {
    const next = new Set(held);
    pageCodes(p).forEach((c) => (on ? next.add(c) : next.delete(c)));
    if (!on) p.extras.forEach((e) => allOf(e.perm).forEach((c) => next.delete(c)));
    onChange([...next]);
  };
  const setColumn = (g: Group, action: string, on: boolean) => {
    const next = new Set(held);
    g.pages.forEach((p) => {
      const code = p.actions.find((a) => a.action === action)?.codename;
      if (!code) return;
      if (on) {
        next.add(code);
        const view = p.actions.find((a) => a.action === "view")?.codename;
        if (view) next.add(view);
      } else {
        next.delete(code);
        if (action === "view") {
          p.actions.forEach((a) => next.delete(a.codename));
          p.extras.forEach((e) => allOf(e.perm).forEach((c) => next.delete(c)));
        }
      }
    });
    onChange([...next]);
  };

  const needle = filter.trim().toLowerCase();
  const visible = groups
    .map((g) => ({ ...g, pages: g.pages.filter((p) => !needle || p.label.toLowerCase().includes(needle) || g.group.toLowerCase().includes(needle)) }))
    .filter((g) => g.pages.length);
  const onCount = groups.reduce((n, g) => n + g.pages.reduce((m, p) => m + p.actions.filter((a) => held.has(a.codename)).length + p.extras.filter((e) => allOf(e.perm).some((c) => held.has(c))).length, 0), 0);
  const totalPages = groups.reduce((n, g) => n + g.pages.length, 0);
  const viewable = groups.reduce((n, g) => n + g.pages.filter((p) => held.has(p.actions.find((a) => a.action === "view")?.codename || "")).length, 0);

  return (
    <>
      <div className="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
        <div>
          <h6 className="mb-0">Permissions</h6>
          <div className="fs-12 text-muted">One switch per action per page. <strong>View</strong> decides whether the page opens at all; Add, Edit and Delete decide which buttons appear on it.</div>
        </div>
        <div className="d-flex align-items-center gap-2">
          <span className="badge bg-primary-light text-primary">{viewable} of {totalPages} pages · {onCount} switches on</span>
          <input className="form-control form-control-sm" style={{ width: 190 }} placeholder="Find a page…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
      </div>
      {visible.map((g) => (
        <div className="card mb-3" key={g.group}>
          <div className="card-header d-flex flex-wrap align-items-center justify-content-between gap-2 py-2">
            <strong className="fs-13">{g.group}</strong>
            <div className="d-flex align-items-center gap-3">
              {COLUMNS.map((c) => {
                const pages = g.pages.filter((p) => p.actions.some((a) => a.action === c));
                const all = pages.length > 0 && pages.every((p) => held.has(p.actions.find((a) => a.action === c)!.codename));
                return (
                  <label key={c} className="d-flex align-items-center gap-1 mb-0 fs-11 text-muted" style={{ cursor: pages.length ? "pointer" : "default" }}>
                    <input type="checkbox" className="form-check-input mt-0" disabled={!pages.length} checked={all} onChange={(e) => setColumn(g, c, e.target.checked)} />
                    all {COLUMN_LABELS[c].toLowerCase()}
                  </label>
                );
              })}
            </div>
          </div>
          <div className="table-responsive">
            <table className="table table-sm align-middle mb-0 fs-12 perm-matrix">
              <thead className="table-light">
                <tr>
                  <th style={{ minWidth: 220 }}>Page</th>
                  {COLUMNS.map((c) => <th key={c} className="text-center" style={{ width: 86 }}>{COLUMN_LABELS[c]}</th>)}
                  <th>Also allowed on this page</th>
                  <th style={{ width: 60 }} />
                </tr>
              </thead>
              <tbody>
                {g.pages.map((p) => {
                  const canView = held.has(p.actions.find((a) => a.action === "view")?.codename || "");
                  return (
                    <tr key={p.key} className={canView ? "" : "text-muted"}>
                      <td>
                        <div className="fw-medium">{p.label}</div>
                      </td>
                      {COLUMNS.map((c) => {
                        const action = p.actions.find((a) => a.action === c);
                        if (!action) return <td key={c} className="text-center text-muted">—</td>;
                        const on = held.has(action.codename);
                        const locked = c !== "view" && !canView;
                        return (
                          <td key={c} className="text-center">
                            <div className="form-check form-switch d-inline-flex justify-content-center mb-0" title={locked ? "Switch View on first" : action.codename}>
                              <input className="form-check-input" type="checkbox" checked={on} onChange={(e) => setCell(p, c, e.target.checked)} />
                            </div>
                          </td>
                        );
                      })}
                      <td>
                        <div className="d-flex flex-wrap gap-2">
                          {p.extras.length === 0 && <span className="text-muted fs-11">—</span>}
                          {p.extras.map((e) => {
                            const on = allOf(e.perm).some((c) => held.has(c));
                            return (
                              <button
                                type="button"
                                key={e.key}
                                className={`btn btn-sm py-0 px-2 fs-11 ${on ? "btn-primary" : "btn-outline-secondary"}`}
                                disabled={!canView}
                                title={allOf(e.perm).join(" / ")}
                                onClick={() => set(on ? allOf(e.perm) : [first(e.perm)], !on)}
                              >
                                <i className={`ti ${on ? "ti-check" : "ti-plus"} me-1`} />{e.label}
                              </button>
                            );
                          })}
                        </div>
                      </td>
                      <td className="text-end">
                        <button type="button" className="btn btn-sm btn-outline-secondary py-0 px-2 fs-11" onClick={() => setRow(p, !rowOn(p))}>{rowOn(p) ? "None" : "All"}</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </>
  );
}
