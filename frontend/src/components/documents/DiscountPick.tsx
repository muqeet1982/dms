import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

/** Who funds a co-funded discount — the API's `discount_split`, or a row of the discount table. */
export type DiscountSplit = { total_pct?: any; distributor_pct: any; company_pct: any; in_table?: boolean; distributor: string; company: string };

/**
 * "Keune Pakistan 12% + Trafco Distribution Network 3%" (owner, 27 Oct 2026: "show how much will be from Keune and
 * how much from Trafco"). The split is the Settings → Discount contributions table — the same one an invoice reads
 * when it posts and claims the manufacturer's share. A % not in the table is given by the distributor alone.
 */
export function splitText(s: DiscountSplit | null | undefined): string {
  if (!s) return "";
  const c = Number(s.company_pct) || 0, d = Number(s.distributor_pct) || 0;
  if (c + d <= 0) return "";
  return c > 0 ? `${s.company} ${c}% + ${s.distributor} ${d}%` : `${s.distributor} ${d}% — no ${s.company === "Manufacturer" ? "manufacturer" : s.company} share`;
}

export function useDiscountRows() {
  const q = useQuery({ queryKey: ["crud", "/api/v1/settings/discount-contributions"], queryFn: () => api.get("/api/v1/settings/discount-contributions", { page_size: 100 }) });
  return ((q.data as any[]) || []).filter((r: any) => r.is_active)
    .map((r: any) => ({ ...r, distributor: r.distributor_name, company: r.company_name })) as (DiscountSplit & { id: string; total_pct: string })[];
}

/** The co-funded discount % picker: the configured totals plus "No discount"; "" = follow the customer's rate. A value
 * outside the table (an older customer) stays selectable. Totals only, everywhere (owner, 27 Oct 2026: "only show main
 * discount, no need for manufacturer and distribution %") — who funds it is DiscountSplitLine's job. */
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

/** Under a picker: who pays the chosen %, in words. */
export function DiscountSplitLine({ pct }: { pct: any }) {
  const rows = useDiscountRows();
  const n = Number(pct) || 0;
  if (n <= 0) return null;
  const row = rows.find((r) => Number(r.total_pct) === n);
  const first = rows[0];
  if (!row) {
    return <div className="form-text text-warning"><i className="ti ti-alert-triangle me-1" />{n}% is not in the discount table, so {first?.distributor || "you"} give all of it and no {first?.company && first.company !== "Manufacturer" ? first.company : "manufacturer"} claim is raised. Add it under Settings → Discount contributions to share it.</div>;
  }
  return (
    <div className="form-text">
      <span className="badge bg-primary-light text-primary me-1">{row.company} pays {Number(row.company_pct)}%</span>
      <span className="badge bg-success-light text-success me-1">{row.distributor} pays {Number(row.distributor_pct)}%</span>
      <span className="text-muted">— the manufacturer's share is claimed back on every invoice.</span>
    </div>
  );
}
