import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

export type EntityOption = { id: string; label: string; sub?: string; raw?: any };

/**
 * Searchable async select backed by a list endpoint. `mapOption` turns a row into {id,label,sub}.
 * Works with paginated (`results`) and plain-array endpoints.
 */
export function EntitySelect({ endpoint, value, onChange, mapOption, placeholder = "Search…", query, disabled, allowClear = true, size, autoFocus, onSelectRaw, className = "" }: {
  endpoint: string; value: string | null | undefined; onChange: (id: string | null, raw?: any) => void; mapOption: (row: any) => EntityOption; placeholder?: string; query?: Record<string, any>; disabled?: boolean; allowClear?: boolean; size?: "sm"; autoFocus?: boolean; onSelectRaw?: (raw: any) => void; className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [debounced, setDebounced] = useState("");
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(text), 250);
    return () => clearTimeout(t);
  }, [text]);
  const { data, isFetching } = useQuery({
    queryKey: ["entity-select", endpoint, debounced, query],
    queryFn: async () => {
      const d = await api.get(endpoint, { search: debounced || undefined, page_size: 20, ...(query || {}) });
      return (Array.isArray(d) ? d : d.results || []).map(mapOption) as EntityOption[];
    },
    enabled: open || Boolean(value),
    staleTime: 10_000,
    refetchOnMount: "always",
  });
  const selected = useQuery({
    queryKey: ["entity-select-one", endpoint, value],
    queryFn: async () => mapOption(await api.get(`${endpoint.replace(/\?.*$/, "")}/${value}`)),
    enabled: Boolean(value) && !(data || []).some((o) => o.id === value),
    staleTime: 60_000,
  });
  const current = (data || []).find((o) => o.id === value) || selected.data;
  useEffect(() => {
    const h = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);
  const options = data || [];
  const pick = (o: EntityOption) => {
    onChange(o.id, o.raw);
    onSelectRaw?.(o.raw);
    setOpen(false);
    setText("");
  };
  return (
    <div className={`entity-select ${className}`} ref={box}>
      <div className="input-group">
        <input
          className={`form-control ${size === "sm" ? "form-control-sm" : ""}`}
          placeholder={current ? current.label : placeholder}
          value={open ? text : current?.label || ""}
          onFocus={() => { setOpen(true); setText(""); }}
          onChange={(e) => { setText(e.target.value); setActive(0); if (!open) setOpen(true); }}
          onKeyDown={(e) => {
            if (!open) return;
            if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, options.length - 1)); }
            if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
            if (e.key === "Enter" && options[active]) { e.preventDefault(); pick(options[active]); }
            if (e.key === "Escape") setOpen(false);
          }}
          disabled={disabled}
          autoFocus={autoFocus}
          autoComplete="off"
        />
        {allowClear && value && !disabled && (
          <button type="button" className={`btn btn-outline-secondary ${size === "sm" ? "btn-sm" : ""}`} onClick={() => onChange(null)} tabIndex={-1}><i className="ti ti-x" /></button>
        )}
      </div>
      {open && (
        <div className="es-menu">
          {isFetching && !options.length && <div className="es-item text-muted">Searching…</div>}
          {!isFetching && !options.length && <div className="es-item text-muted">No matches</div>}
          {options.map((o, i) => (
            <div key={o.id} className={`es-item ${i === active ? "active" : ""}`} onMouseDown={(e) => { e.preventDefault(); pick(o); }} onMouseEnter={() => setActive(i)}>
              <div>{o.label}</div>
              {o.sub && <div className="text-muted fs-11">{o.sub}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Small synchronous select for static option lists (with optional empty option). */
export function Select({ value, onChange, options, empty = "— Select —", className = "form-select", disabled, size }: { value: any; onChange: (v: string) => void; options: { value: string; label: string }[]; empty?: string | null; className?: string; disabled?: boolean; size?: "sm" }) {
  return (
    <select className={`${className} ${size === "sm" ? "form-select-sm" : ""}`} value={value ?? ""} onChange={(e) => onChange(e.target.value)} disabled={disabled}>
      {empty !== null && <option value="">{empty}</option>}
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}
