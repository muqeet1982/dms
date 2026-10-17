import { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { EmptyState, Spinner } from "./Bits";

export type Column<T> = { key: string; header: ReactNode; render?: (row: T) => ReactNode; className?: string; num?: boolean; sortKey?: string; hidden?: boolean };

export function DataTable<T extends { id?: string }>({ columns, rows, loading, rowLink, onRowClick, emptyText, ordering, onSort, footer, small, rowClass }: {
  columns: Column<T>[]; rows: T[] | undefined; loading?: boolean; rowLink?: (r: T) => string | undefined; onRowClick?: (r: T) => void; emptyText?: ReactNode; ordering?: string; onSort?: (k: string) => void; footer?: ReactNode; small?: boolean; rowClass?: (r: T) => string;
}) {
  const nav = useNavigate();
  const cols = columns.filter((c) => !c.hidden);
  return (
    <div className="table-responsive">
      <table className={`table table-hover align-middle mb-0 ${small ? "table-sm" : ""}`}>
        <thead className="table-light">
          <tr>
            {cols.map((c) => {
              const active = ordering && c.sortKey && ordering.replace("-", "") === c.sortKey;
              return (
                <th key={c.key} className={`${c.num ? "num " : ""}${c.className || ""} ${c.sortKey ? "cursor-pointer" : ""}`} onClick={() => c.sortKey && onSort?.(active && !ordering!.startsWith("-") ? `-${c.sortKey}` : c.sortKey!)}>
                  {c.header}{active && <i className={`ti ${ordering!.startsWith("-") ? "ti-arrow-down" : "ti-arrow-up"} ms-1 fs-11`} />}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {loading && (!rows || !rows.length) && <tr><td colSpan={cols.length}><Spinner /></td></tr>}
          {!loading && rows && rows.length === 0 && <tr><td colSpan={cols.length}><EmptyState text={emptyText || "No records found."} /></td></tr>}
          {rows?.map((r, i) => {
            const link = rowLink?.(r);
            const clickable = Boolean(link || onRowClick);
            return (
              <tr key={(r as any).id ?? i} className={`${clickable ? "cursor-pointer" : ""} ${rowClass?.(r) || ""}`} onClick={(e) => { if ((e.target as HTMLElement).closest("a,button,input,select,.no-row-click")) return; if (link) nav(link); else onRowClick?.(r); }}>
                {cols.map((c) => <td key={c.key} className={`${c.num ? "num " : ""}${c.className || ""}`}>{c.render ? c.render(r) : ((r as any)[c.key] ?? "—")}</td>)}
              </tr>
            );
          })}
        </tbody>
        {footer && <tfoot>{footer}</tfoot>}
      </table>
    </div>
  );
}

export function Pagination({ page, pages, count, pageSize, onPage, onPageSize }: { page: number; pages: number; count: number; pageSize: number; onPage: (p: number) => void; onPageSize?: (n: number) => void }) {
  if (!count) return null;
  // a window of up to 5 page numbers centred on the current page: 1st ‹ 3 4 [5] 6 7 › Last
  const start = Math.max(1, Math.min(page - 2, pages - 4));
  const nums = Array.from({ length: Math.min(5, pages) }, (_, i) => start + i);
  const go = (p: number) => { if (p >= 1 && p <= pages && p !== page) onPage(p); };
  return (
    <div className="d-flex align-items-center justify-content-between flex-wrap gap-2 p-3 border-top">
      <div className="text-muted fs-13 d-flex align-items-center gap-2 flex-wrap">
        <span>Showing <strong>{(page - 1) * pageSize + 1}–{Math.min(page * pageSize, count)}</strong> of <strong>{count}</strong> records</span>
        <span>· Page {page} of {pages}</span>
        {onPageSize && (
          <select className="form-select form-select-sm w-auto" value={pageSize} onChange={(e) => onPageSize(Number(e.target.value))}>
            {[10, 25, 50, 100, 200].map((n) => <option key={n} value={n}>{n} / page</option>)}
          </select>
        )}
      </div>
      <nav>
        <ul className="pagination pagination-sm mb-0 justify-content-end flex-wrap">
          <li className={`page-item ${page <= 1 ? "disabled" : ""}`}><a className="page-link cursor-pointer" title="First page" onClick={() => go(1)}>1st</a></li>
          <li className={`page-item ${page <= 1 ? "disabled" : ""}`}><a className="page-link cursor-pointer" title="Previous page" onClick={() => go(page - 1)}>Prev</a></li>
          {nums.map((n) => <li key={n} className={`page-item ${n === page ? "active" : ""}`}><a className="page-link cursor-pointer" onClick={() => go(n)}>{n}</a></li>)}
          <li className={`page-item ${page >= pages ? "disabled" : ""}`}><a className="page-link cursor-pointer" title="Next page" onClick={() => go(page + 1)}>Next</a></li>
          <li className={`page-item ${page >= pages ? "disabled" : ""}`}><a className="page-link cursor-pointer" title="Last page" onClick={() => go(pages)}>Last</a></li>
        </ul>
      </nav>
    </div>
  );
}
