import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

/**
 * City picker backed by the shared city list (Pakistan seeded), but it stores plain text — so an unlisted town
 * can still be typed. Searching shows the province, which keeps "Hyderabad" and "Mirpur" unambiguous.
 */
export function CitySelect({ value, onChange, placeholder = "Search a city…", disabled }: { value: string; onChange: (city: string) => void; disabled?: boolean; placeholder?: string }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [debounced, setDebounced] = useState("");
  const box = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(text), 200);
    return () => clearTimeout(t);
  }, [text]);
  useEffect(() => {
    const h = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);
  const { data } = useQuery<any[]>({ queryKey: ["cities", debounced], queryFn: () => api.get("/api/v1/cities", { search: debounced || undefined }), enabled: open, staleTime: 300_000 });
  const rows = data || [];
  const pick = (name: string) => { onChange(name); setOpen(false); setText(""); };
  return (
    <div className="entity-select" ref={box}>
      <div className="input-group">
        <input
          className="form-control"
          disabled={disabled}
          placeholder={value || placeholder}
          value={open ? text : value || ""}
          onFocus={() => { setOpen(true); setText(""); }}
          onChange={(e) => { setText(e.target.value); setOpen(true); }}
          onKeyDown={(e) => { if (e.key === "Enter" && text.trim()) { e.preventDefault(); pick(text.trim()); } if (e.key === "Escape") setOpen(false); }}
        />
        {value && !disabled && <button type="button" className="btn btn-outline-secondary" tabIndex={-1} onClick={() => onChange("")}><i className="ti ti-x" /></button>}
      </div>
      {open && (
        <div className="es-menu">
          {rows.map((c) => (
            <div key={c.id} className="es-item" onMouseDown={() => pick(c.name)}>
              {c.name}
              {c.province && <div className="text-muted fs-11">{c.province}</div>}
            </div>
          ))}
          {text.trim() && !rows.some((c) => c.name.toLowerCase() === text.trim().toLowerCase()) && (
            <div className="es-item" onMouseDown={() => pick(text.trim())}><i className="ti ti-plus me-1" />Use “{text.trim()}”</div>
          )}
          {!rows.length && !text.trim() && <div className="es-item text-muted">Type to search cities…</div>}
        </div>
      )}
    </div>
  );
}
