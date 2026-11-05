import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";

dayjs.extend(relativeTime);

// Every amount, everywhere, reads 1,234.56 — commas and exactly two decimals (owner, 27 Oct 2026: "all pdf, view,
// print amounts should be 2 digits after . and also add commas"). The second argument is accepted for the older
// call sites that asked for whole rupees, and deliberately ignored.
export const money = (v: any, _digits?: number): string => {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return n.toLocaleString("en-PK", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};
export const qty = (v: any): string => {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return Number.isInteger(n) ? n.toLocaleString() : n.toLocaleString("en-PK", { maximumFractionDigits: 3 });
};
/**
 * A quantity the way a warehouse counts it: "3 box + 4" when a box holds more than one unit, otherwise just the
 * number. `packing` is the product's units-per-box.
 */
export const boxes = (v: any, packing?: any): string => {
  const units = Number(v || 0);
  const per = Number(packing || 0);
  if (!per || per <= 1 || Number.isNaN(units)) return qty(v);
  const whole = Math.floor(Math.abs(units) / per);
  const loose = Math.abs(units) - whole * per;
  const sign = units < 0 ? "-" : "";
  if (!whole) return `${sign}${qty(loose)}`;
  return `${sign}${qty(whole)} box${loose ? ` + ${qty(loose)}` : ""}`;
};

/** "120 units · 10 box" for a tooltip or a second line under the figure. */
export const boxesLong = (v: any, packing?: any): string => {
  const per = Number(packing || 0);
  if (!per || per <= 1) return `${qty(v)} units`;
  return `${qty(v)} units · ${boxes(v, packing)} of ${per}`;
};

export const pct = (v: any) => (v === null || v === undefined || v === "" ? "—" : `${Number(v).toFixed(Number(v) % 1 ? 2 : 0)}%`);
// Day first, as everything in Pakistan is written and as the printed documents already read. `today`,
// `daysAgo` and `startOfMonth` below stay ISO on purpose: those feed date inputs and query strings, where the
// browser and the API both expect YYYY-MM-DD.
export const date = (v: any) => (v ? dayjs(v).format("DD/MM/YYYY") : "—");
export const datetime = (v: any) => (v ? dayjs(v).format("DD/MM/YYYY HH:mm") : "—");
export const time = (v: any) => (v ? dayjs(v).format("HH:mm") : "—");
export const ago = (v: any) => (v ? dayjs(v).fromNow() : "—");
export const today = () => dayjs().format("YYYY-MM-DD");
export const daysAgo = (n: number) => dayjs().subtract(n, "day").format("YYYY-MM-DD");
export const startOfMonth = () => dayjs().startOf("month").format("YYYY-MM-DD");
/** The ranges a report's Preset offers (owner, 6 Nov 2026), by the browser's own day. A week starts on Monday; a period still running ends today. */
export const DATE_PRESETS = ["Today", "Yesterday", "This Week", "Last Week", "This Month", "Last Month"];
export function presetRange(name: string): [string, string] {
  const f = (d: dayjs.Dayjs) => d.format("YYYY-MM-DD");
  const now = dayjs();
  const monday = now.subtract((now.day() + 6) % 7, "day");
  const lastMonth = now.subtract(1, "month");
  switch (name) {
    case "Yesterday": return [f(now.subtract(1, "day")), f(now.subtract(1, "day"))];
    case "This Week": return [f(monday), f(now)];
    case "Last Week": return [f(monday.subtract(7, "day")), f(monday.subtract(1, "day"))];
    case "This Month": return [f(now.startOf("month")), f(now)];
    case "Last Month": return [f(lastMonth.startOf("month")), f(lastMonth.endOf("month"))];
    default: return [f(now), f(now)];
  }
}
export const titleCase = (s: any) => (s ? String(s).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : "");
export const num = (v: any) => Number(v || 0);

/** Field names in Title Case — every word's first letter capital, the rest untouched (NTN, D.P stay as typed). */
export function labelCase(s: string): string {
  return s.replace(/(^|\s|\/|\()([a-z])/g, (_m, pre, ch) => pre + ch.toUpperCase());
}

// ── FBR rate strings ("18% along with rupees 60 per kilogram") ─────────────────────────────────────
// Mirrors apps/fbr/engine (gst_rate_num / fixed_tax_amount / rate_label) for PREVIEW and labels only;
// the server always recomputes the real figures.
export function fbrRateParts(rate: string): { pct: number; fixed: number } {
  const pm = /(\d+(?:\.\d+)?)\s*%/.exec(rate);
  const fm = /(?:\brs\.?|\brupees)\s*([\d,]+(?:\.\d+)?)/i.exec(rate);
  let pct = pm ? Number(pm[1]) : 0;
  // a bare number ("16") is a percentage — the engine reads it that way (gst_rate_num)
  if (!pm && !fm && rate.trim() !== "" && !Number.isNaN(Number(rate.trim()))) pct = Number(rate.trim());
  return { pct, fixed: fm ? Number(fm[1].replace(/,/g, "")) : 0 };
}

// "18% along with rupees 60 per kilogram" → "18% + Rs.60", same as the server's rate_label
export function fbrRateLabel(rate: string): string {
  const { pct, fixed } = fbrRateParts(rate);
  return fixed ? (pct ? `${pct}% + Rs.${fixed}` : `Rs.${fixed}`) : rate;
}

const r2 = (n: number) => Math.round(n * 100) / 100;

/**
 * "18%" — the GST % the footer prints beside GST (owner, 27 Oct 2026: "change GST to GST (N%)"). From the lines'
 * own rates; a document mixing rates lists each ("17%, 18%"). Empty when no line carries a % (fixed-rupee only).
 */
export function gstPct(rates: any[]): string {
  const set = [...new Set(rates.map((r) => Number(r) || 0).filter((r) => r > 0))].sort((a, b) => a - b);
  return set.map((r) => `${r}%`).join(", ");
}
/** The same from preview labels ("18% + Rs.60" → 18). */
export const gstPctOfLabels = (labels: any[]): string => gstPct(labels.map((l) => (l ? fbrRateParts(String(l)).pct : 0)));

/** The product's FED / Extra / Further / WHT rates (its FBR card), as any API row carries them. */
export type OtherRates = { fed?: any; extra?: any; further?: any; wht?: any };
export type LineTax = { label: string; amount: number; fed: number; extra: number; further: number; wht: number; other: number };

/**
 * One line's taxes for PREVIEW, mirroring InvoiceService.line_taxes / apps/fbr/engine.compute_line. GST: the % on
 * the taxable value — on MRP × qty instead for 3rd Schedule goods when that is higher — plus the fixed rupee part ×
 * qty; a typed `overridePct` is a plain % on the taxable value. FED / Extra / Further / WHT: each its rate on the
 * TAXABLE value, never on taxable + GST (guide §4). `other` = FED + Extra + Further, the part inside Line Total;
 * WHT is withheld by the buyer and comes off the document's total. 2 dp half-up throughout; `qty` in base units.
 * Null when the line carries no tax at all. The server always recomputes the real figures.
 */
export function fbrLineTax(o: { rate?: string; taxable: number; qty: number; overridePct?: string | number | null; others?: OtherRates }): LineTax | null {
  const base0 = r2(o.taxable);
  const pctOf = (rate: any) => r2((base0 * (Number(rate) || 0)) / 100);
  const fed = pctOf(o.others?.fed), extra = pctOf(o.others?.extra), further = pctOf(o.others?.further), wht = pctOf(o.others?.wht);
  const rest = { fed, extra, further, wht, other: r2(fed + extra + further) };
  if (o.overridePct !== undefined && o.overridePct !== null && o.overridePct !== "") {
    const p = Number(o.overridePct) || 0;
    return { label: `${p}%`, amount: r2((base0 * p) / 100), ...rest };
  }
  if (!o.rate) return fed || extra || further || wht ? { label: "", amount: 0, ...rest } : null;
  const { pct, fixed } = fbrRateParts(o.rate);
  // the tax is on the amount after discount (owner, 5 Nov 2026), 3rd Schedule goods too, as InvoiceService.line_taxes charges it
  return { label: fbrRateLabel(o.rate), amount: r2((base0 * pct) / 100 + o.qty * fixed), ...rest };
}

/**
 * The TP that includes the tax (owner, 1 Nov 2026; Settings → Rules "TP includes tax"): on a tax document the price
 * before tax is worked back from the TP at the item's own rates — the screen's mirror of InvoiceService.ex_tax_price
 * and of how recompute keeps the line to the paisa, so a preview shows what the saved document will charge.
 */
type TaxArgs = { rate?: string; overridePct?: string | number | null; others?: OtherRates };
export function exTaxUnit(tp: number, o: TaxArgs): number {
  if (!(tp > 0)) return tp || 0;
  const n = 1_000_000;
  const inside = (unit: number) => { const t = fbrLineTax({ ...o, taxable: unit * n, qty: n }); return t ? (t.amount + t.other) / n : 0; };
  let p = tp;
  for (let i = 0; i < 8; i++) {
    const slope = (inside(p + 0.01) - inside(p)) / 0.01;
    const next = Math.max(p - (p + inside(p) - tp) / (1 + slope), 0);
    if (Math.abs(next - p) < 1e-6) { p = next; break; }
    p = next;
  }
  return Math.round(p * 10000) / 10000;
}

/** One line priced inside its TP: `tp` per unit, `discTp` the discount on the TP. */
export function lineWithinTp(o: TaxArgs & { tp: number; qty: number; discTp: number }) {
  const unit = exTaxUnit(o.tp, o);
  const gross = r2(o.qty * unit);
  const first = o.tp ? r2((o.discTp * unit) / o.tp) : 0;
  const target = r2(o.qty * o.tp - o.discTp);
  // the last paisa of rounding: of the discounts a paisa or two either side, the one that lands on the TP — as the
  // server's fit_within_tp picks it
  let best: { miss: number; disc: number; gst: LineTax | null } | null = null;
  for (const step of [0, 1, -1, 2, -2]) {
    const d = r2(first + step * 0.01);
    if (d < 0 || d > gross) continue;
    const g = fbrLineTax({ ...o, taxable: r2(gross - d), qty: o.qty });
    const miss = Math.abs(r2(gross - d + (g?.amount || 0) + (g?.other || 0) - target));
    if (!best || miss < best.miss) best = { miss, disc: d, gst: g };
    if (miss === 0) break;
  }
  const disc = best ? best.disc : first;
  return { unit, gross, disc, taxable: r2(gross - disc), gst: best ? best.gst : fbrLineTax({ ...o, taxable: r2(gross - disc), qty: o.qty }) };
}

/** "FED 2% · Further 3%" — which of the other taxes a line carries, for the sub-label under its amount. */
export function otherTaxLabel(r: OtherRates): string {
  return ([["FED", r.fed], ["Extra", r.extra], ["Further", r.further]] as const).filter(([, v]) => Number(v) > 0).map(([k, v]) => `${k} ${Number(v)}%`).join(" · ");
}
