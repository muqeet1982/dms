import { DateInput } from "@/components/ui/DateInput";
import { ReactNode, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { usePage } from "@/lib/perms";
import { PageHeader } from "./Bits";
import { Column, DataTable, Pagination } from "./DataTable";
import { EntitySelect, Select } from "./EntitySelect";

export type FilterDef =
  | { name: string; label?: string; type: "select"; options: { value: string; label: string }[] }
  | { name: string; label?: string; type: "date" }
  | { name: string; label?: string; type: "entity"; endpoint: string; mapOption: (r: any) => { id: string; label: string; sub?: string }; query?: Record<string, any> }
  | { name: string; label?: string; type: "toggle" };

/** Hook: query-string backed list state (page, search, ordering, filters) + data. */
export function useListQuery<T = any>(endpoint: string, opts: { defaultOrdering?: string; extraQuery?: Record<string, any>; pageSize?: number; queryKey?: any[] } = {}) {
  const [sp, setSp] = useSearchParams();
  const page = Number(sp.get("page") || 1);
  const pageSize = Number(sp.get("page_size") || opts.pageSize || 25);
  const search = sp.get("search") || "";
  const ordering = sp.get("ordering") || opts.defaultOrdering || "";
  const filters = useMemo(() => Object.fromEntries([...sp.entries()].filter(([k]) => !["page", "page_size", "search", "ordering"].includes(k))), [sp]);
  const query = { page, page_size: pageSize, search: search || undefined, ordering: ordering || undefined, ...filters, ...(opts.extraQuery || {}) };
  const q = useQuery({ queryKey: ["list", endpoint, query, ...(opts.queryKey || [])], queryFn: () => api.list<T>(endpoint, query), placeholderData: (prev) => prev });
  const set = (patch: Record<string, any>, resetPage = true) => {
    const next = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(patch)) { if (v === undefined || v === null || v === "") next.delete(k); else next.set(k, String(v)); }
    if (resetPage && !("page" in patch)) next.delete("page");
    setSp(next, { replace: true });
  };
  return { ...q, page, pageSize, search, ordering, filters, set, query };
}

export function FilterBar({ filters, values, onChange, searchPlaceholder = "Search…", search, onSearch, extra }: { filters?: FilterDef[]; values: Record<string, any>; onChange: (patch: Record<string, any>) => void; searchPlaceholder?: string; search: string; onSearch: (s: string) => void; extra?: ReactNode }) {
  const [text, setText] = useState(search);
  return (
    <div className="card mb-3">
      <div className="card-body py-2">
        <form className="row g-2 align-items-center" onSubmit={(e) => { e.preventDefault(); onSearch(text); }}>
          <div className="col-md-4 col-12"><input className="form-control" placeholder={searchPlaceholder} value={text} onChange={(e) => setText(e.target.value)} /></div>
          {filters?.map((f) => (
            <div className="col-md-2 col-6" key={f.name}>
              {f.type === "select" && <Select value={values[f.name]} onChange={(v) => onChange({ [f.name]: v })} options={f.options} empty={f.label ? `All ${f.label.toLowerCase()}` : "All"} />}
              {f.type === "date" && <DateInput className="form-control" value={values[f.name] || ""} onChange={(e) => onChange({ [f.name]: e.target.value })} title={f.label} />}
              {f.type === "entity" && <EntitySelect endpoint={f.endpoint} query={f.query} value={values[f.name]} onChange={(v) => onChange({ [f.name]: v })} mapOption={f.mapOption} placeholder={f.label || "Select"} />}
              {f.type === "toggle" && <div className="form-check form-check-md mb-0"><input className="form-check-input" type="checkbox" id={`f-${f.name}`} checked={values[f.name] === "1"} onChange={(e) => onChange({ [f.name]: e.target.checked ? "1" : "" })} /><label className="form-check-label" htmlFor={`f-${f.name}`}>{f.label}</label></div>}
            </div>
          ))}
          <div className="col-auto d-flex gap-1">
            <button className="btn btn-outline-primary" type="submit"><i className="ti ti-search" /></button>
            <button className="btn btn-outline-secondary" type="button" onClick={() => { setText(""); onChange(Object.fromEntries([...Object.keys(values), "search"].map((k) => [k, ""]))); }}>Reset</button>
            {extra}
          </div>
        </form>
      </div>
    </div>
  );
}

/**
 * Config-driven list page: header + optional stat cards + filter bar + table + pagination.
 */
/**
 * Print · PDF · Excel for whatever the list is currently showing.
 *
 * The filters, the search and the ordering go with it, so the file and the screen cannot disagree — the page
 * number deliberately does not: nobody wants page 2 of their export.
 */
export function ListExports({ endpoint, title, query }: { endpoint: string; title: string; query: Record<string, any> }) {
  const params = { ...query, page: undefined, page_size: undefined, title };
  const file = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return (
    <div className="btn-group">
      <button className="btn btn-outline-secondary" title="Print this list" onClick={() => api.printDoc(`${endpoint}/export`, { ...params, format: "pdf" })}>
        <i className="ti ti-printer me-1" />Print
      </button>
      <button className="btn btn-outline-secondary" title="Download as PDF" onClick={() => api.download(`${endpoint}/export`, `${file}.pdf`, { ...params, format: "pdf" })}>
        <i className="ti ti-file-type-pdf me-1" />PDF
      </button>
      <button className="btn btn-outline-success" title="Download as a spreadsheet" onClick={() => api.download(`${endpoint}/export`, `${file}.xlsx`, { ...params, format: "xlsx" })}>
        <i className="ti ti-file-spreadsheet me-1" />Excel
      </button>
    </div>
  );
}

export function ListPage<T extends { id?: string }>({ title, crumbs, endpoint, columns, filters, searchPlaceholder, createTo, createLabel = "Add", rowLink, actions, stats, defaultOrdering, extraQuery, children, emptyText, canCreate = true, page, rowClass, exports = true }: {
  title: string; crumbs?: { label: string; to?: string }[]; endpoint: string; columns: Column<T>[]; filters?: FilterDef[]; searchPlaceholder?: string; createTo?: string; createLabel?: string; rowLink?: (r: T) => string | undefined;
  actions?: ReactNode; stats?: ReactNode; defaultOrdering?: string; extraQuery?: Record<string, any>; children?: ReactNode; emptyText?: ReactNode; canCreate?: boolean;
  /** Page key from the permission matrix: the Add button only shows when that page's "Add" switch is on. */
  page?: string; rowClass?: (r: T) => string;
  /** Print/PDF/Excel of the current list. On by default; pass false where a list is not worth a file. */
  exports?: boolean;
}) {
  const mayAdd = page ? usePage(page).add : true;
  const list = useListQuery<T>(endpoint, { defaultOrdering, extraQuery });
  return (
    <>
      <PageHeader title={title} crumbs={crumbs || [{ label: title }]} actions={<>{exports && <ListExports endpoint={endpoint} title={title} query={list.query} />}{actions}{createTo && canCreate && mayAdd && <Link to={createTo} className="btn btn-primary"><i className="ti ti-plus me-1" />{createLabel}</Link>}</>} />
      {stats}
      {children}
      <FilterBar filters={filters} values={list.filters} onChange={(p) => list.set(p)} search={list.search} onSearch={(s) => list.set({ search: s })} searchPlaceholder={searchPlaceholder} />
      <div className="card">
        <div className="card-body p-0">
          <DataTable columns={columns} rows={list.data?.results} loading={list.isLoading} rowLink={rowLink} ordering={list.ordering} onSort={(k) => list.set({ ordering: k })} emptyText={emptyText} rowClass={rowClass} />
          {list.data && <Pagination page={list.data.page} pages={list.data.pages} count={list.data.count} pageSize={list.pageSize} onPage={(p) => list.set({ page: p }, false)} onPageSize={(n) => list.set({ page_size: n })} />}
        </div>
      </div>
    </>
  );
}
