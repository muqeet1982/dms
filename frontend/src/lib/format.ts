import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";

dayjs.extend(relativeTime);

export const money = (v: any, digits = 2): string => {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return n.toLocaleString("en-PK", { minimumFractionDigits: digits, maximumFractionDigits: digits });
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
