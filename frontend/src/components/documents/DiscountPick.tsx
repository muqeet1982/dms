import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

export function useDiscountRows() {
  const q = useQuery({ queryKey: ["crud", "/api/v1/settings/discount-contributions"], queryFn: () => api.get("/api/v1/settings/discount-contributions", { page_size: 100 }) });
  return ((q.data as any[]) || []).filter((r: any) => r.is_active) as { id: string; total_pct: string }[];
}

/** The co-funded discount % picker: the configured totals plus "No discount"; "" = follow the customer's rate. A value
 * outside the table (an older customer) stays selectable. Totals only, everywhere (owner, 27 Oct 2026: "only show main
 * discount, no need for manufacturer and distribution %"). Who funds each % is shown only where the splits are set,
 * Settings → Discount contributions (owner, 29 Oct 2026). */
export function DiscountPctSelect({ value, onChange, autoLabel }: { value: string; onChange: (v: string) => void; autoLabel?: string }) {
  const rows = useDiscountRows();
  const known = ["0", ...rows.map((r) => String(Number(r.total_pct)))];
  return (
    <select className="form-select" value={value} onChange={(e) => onChange(e.target.value)}>
      {autoLabel && <option value="">{autoLabel}</option>}
      <option value="0">No discount</option>
      {rows.map((r) => <option key={r.id} value={String(Number(r.total_pct))}>{Number(r.total_pct)}%</option>)}
      {value !== "" && !known.includes(value) && <option value={value}>{Number(value)}%</option>}
    </select>
  );
}
