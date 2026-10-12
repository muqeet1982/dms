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
