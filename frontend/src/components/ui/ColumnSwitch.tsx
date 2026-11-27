import { useEffect, useRef, useState } from "react";

/** The columns left out of a table — on the page and in every file printed or downloaded from it (owner, 26 Nov 2026 for
 *  Sales by Sale Person, 27 Nov for every report and list: "column selection for all reports and for all table and pdf or
 *  print or csv excel"). Kept per browser, per table. */
export function loadHidden(key: string): string[] {
  try { const v = JSON.parse(localStorage.getItem(key) || "[]"); return Array.isArray(v) ? v.map(String) : []; } catch { return []; }
}
export function saveHidden(key: string, hide: string[]) {
  try { localStorage.setItem(key, JSON.stringify(hide)); } catch { /* private window: not remembered */ }
}

/** The switch above a table, on the right. One column always stays. */
export function ColumnSwitch({ columns, hidden, onChange }: { columns: { key: string; label: string; fileOnly?: boolean }[]; hidden: string[]; onChange: (hide: string[]) => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);
  const left = columns.filter((c) => !hidden.includes(c.key)).length;
  // the table keeps one column of its own: a column only the files carry does not count for it
  const onTable = columns.filter((c) => !c.fileOnly && !hidden.includes(c.key)).length;
  return (
    <div className="position-relative" ref={box}>
      <button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => setOpen(!open)}><i className="ti ti-columns me-1" />Columns <span className="badge bg-light text-dark ms-1">{left}/{columns.length}</span></button>
      {open && <div className="card shadow position-absolute end-0 mt-1" style={{ zIndex: 1050, width: 300 }}><div className="card-body p-2">
        <div className="d-flex gap-2 mb-2"><button type="button" className="btn btn-sm btn-light" onClick={() => onChange([])}>Select all</button><button type="button" className="btn btn-sm btn-light" onClick={() => onChange(columns.slice(1).map((c) => c.key))}>Clear all</button></div>
        <div style={{ maxHeight: 360, overflowY: "auto" }}>{columns.map((c, i) => { const on = !hidden.includes(c.key); return (
          <div key={c.key}>{c.fileOnly && !columns[i - 1]?.fileOnly && i > 0 && <div className="fs-11 text-muted text-uppercase border-top mt-2 pt-2 mb-1">Files only</div>}<div className="form-check"><input className="form-check-input" type="checkbox" id={`col-${c.key}`} checked={on} disabled={on && (left <= 1 || (!c.fileOnly && onTable <= 1))}
            onChange={(e) => onChange(e.target.checked ? hidden.filter((k) => k !== c.key) : [...hidden, c.key])} /><label className="form-check-label" htmlFor={`col-${c.key}`}>{c.label}</label></div></div>); })}</div>
      </div></div>}
    </div>
  );
}
