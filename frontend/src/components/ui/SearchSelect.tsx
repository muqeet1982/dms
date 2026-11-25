import { useEffect, useRef, useState } from "react";

export type SearchOption = { value: string; label: string; /** also found by this (a code, a phone) */ search?: string };

/**
 * A dropdown of a known list that looks like every other dropdown and can be searched once open
 * (owner, 24 and 25 Nov 2026: the Sale Person filter).
 */
export function SearchSelect({ value, onChange, options, empty = "all", size, disabled }: { value: string; onChange: (v: string) => void; options: SearchOption[]; empty?: string; size?: "sm"; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);
  useEffect(() => { list.current?.querySelector(".es-item.active")?.scrollIntoView({ block: "nearest" }); }, [active, open]);
  const want = text.trim().toLowerCase();
  const all: SearchOption[] = [...(want ? [] : [{ value: "", label: empty }]), ...options.filter((o) => !want || `${o.label} ${o.search || ""}`.toLowerCase().includes(want))];
  const current = options.find((o) => o.value === value);
  const show = () => { setText(""); setActive(Math.max(0, [{ value: "" }, ...options].findIndex((o) => o.value === value))); setOpen(true); };
  const pick = (o: SearchOption) => { onChange(o.value); setOpen(false); box.current?.querySelector("button")?.focus(); };
  return (
    <div className="entity-select" ref={box}>
      <button type="button" className={`form-select ${size === "sm" ? "form-select-sm" : ""} text-start text-truncate`} disabled={disabled} aria-haspopup="listbox" aria-expanded={open} title={current?.label}
        onClick={() => (open ? setOpen(false) : show())} onKeyDown={(e) => { if (!open && (e.key === "ArrowDown" || e.key === "ArrowUp")) { e.preventDefault(); show(); } }}>{current?.label ?? empty}</button>
      {open && (
        <div className="es-menu" style={{ minWidth: 220, overflow: "hidden", maxHeight: "none" }}>
          <div className="p-2 border-bottom">
            <input className="form-control form-control-sm" autoFocus autoComplete="off" placeholder="Search…" value={text}
              onChange={(e) => { setText(e.target.value); setActive(0); }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, all.length - 1)); }
                if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
                if (e.key === "Enter") { e.preventDefault(); if (all[active]) pick(all[active]); }
                if (e.key === "Escape") { e.preventDefault(); setOpen(false); box.current?.querySelector("button")?.focus(); }
                if (e.key === "Tab") setOpen(false);
              }} />
          </div>
          <div ref={list} role="listbox" style={{ maxHeight: 240, overflowY: "auto" }}>
            {!all.length && <div className="es-item text-muted">No matches</div>}
            {all.map((o, i) => <div key={o.value} role="option" aria-selected={o.value === value} className={`es-item ${i === active ? "active" : ""} ${o.value === value ? "fw-semibold" : ""}`} onMouseDown={(e) => { e.preventDefault(); pick(o); }} onMouseEnter={() => setActive(i)}>{o.label}</div>)}
          </div>
        </div>
      )}
    </div>
  );
}
