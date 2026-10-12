import { DateInput } from "@/components/ui/DateInput";
import { useState } from "react";
import { api } from "@/api/client";
import { money } from "@/lib/format";
import { useScanListener } from "@/lib/scan";
import { toast } from "@/lib/toast";
import { EntitySelect } from "../ui/EntitySelect";
import { LINES_SCROLL_FROM } from "../ui/Bits";

export type EditLine = { key: string; variant: string | null; variant_name?: string; sku?: string; packaging?: string | null; packagings?: { id: string; name: string; qty_in_base: string }[]; qty: string; unit_price?: string; discount_pct?: string; batch?: string | null; batch_no?: string; expiry_date?: string; extra?: Record<string, any> };

export const newLine = (): EditLine => ({ key: crypto.randomUUID(), variant: null, qty: "1", unit_price: "", discount_pct: "0" });

/**
 * Reusable document lines grid: product search (or barcode scan), packaging, qty, price, discount.
 * `showPrice`/`showDiscount`/`showBatch` toggle columns. Scans (HID or camera) resolve via /barcodes/resolve.
 */
export function LinesEditor({ lines, onChange, showPrice = true, showDiscount = true, showBatch = false, onToggleDiscount, discountShown, priceFor, readOnly, customerId, variantQuery, priceLabel, reasons }: {
  lines: EditLine[]; onChange: (lines: EditLine[]) => void; showPrice?: boolean; showDiscount?: boolean; showBatch?: boolean; priceFor?: "sale" | "cost"; readOnly?: boolean; customerId?: string | null; variantQuery?: Record<string, any>;
  /** When given, a switch appears to show or hide the discount column — off until a deal actually needs it. */
  onToggleDiscount?: () => void; discountShown?: boolean;
  /** Column header for the price — purchase documents buy at D.P (distributor price). */
  priceLabel?: string;
  /** Optional per-line reason picker (purchase returns: expired, damaged, wrong item…). */
  reasons?: { id: string; name: string }[];
}) {
  const priceHeader = priceLabel || (priceFor === "cost" ? "D.P (unit)" : "Unit price");
  const [scan, setScan] = useState("");
  const update = (key: string, patch: Partial<EditLine>) => onChange(lines.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const remove = (key: string) => onChange(lines.filter((l) => l.key !== key));
  /** A line already holding the same item (same variant + packaging + batch) — picking that item again adds to it. */
  const duplicateOf = (key: string, variant: string, packaging: string | null, batchNo?: string) =>
    lines.find((l) => l.key !== key && l.variant === variant && (l.packaging || null) === (packaging || null) && (l.batch_no || "") === (batchNo || ""));
  /** Move the row's quantity onto the existing line and drop the row, so one item never appears twice. */
  const mergeInto = (target: EditLine, rowKey: string, addQty: number, label?: string) => {
    const qty = Number(target.qty || 0) + (addQty || 1);
    onChange(lines.filter((l) => l.key !== rowKey).map((l) => (l.key === target.key ? { ...l, qty: String(qty) } : l)));
    toast.info(`${label || target.variant_name || "Item"} is already on this document — quantity increased to ${qty}`);
  };
  /** Everything a line needs once an item is chosen (price comes from the customer's price list when there is one). */
  const variantPatch = async (v: any, packaging?: any, prev?: EditLine): Promise<Partial<EditLine>> => {
    let price = priceFor === "cost" ? v.cost_price : v.sale_price;
    if (priceFor !== "cost" && customerId) {
      try { const p = await api.get(`/api/v1/variants/${v.id}/price`, { customer: customerId, packaging: packaging?.id }); price = p.unit_price; } catch { /* ignore */ }
    }
    return { variant: v.id, variant_name: v.name, sku: v.sku, packagings: v.packagings || [], packaging: packaging?.id || null, unit_price: String(price ?? ""), extra: { ...(prev?.extra || {}), is_batch_tracked: v.is_batch_tracked, is_expiry_tracked: v.is_expiry_tracked } };
  };
  const applyVariant = async (key: string, v: any, packaging?: any) => {
    const row = lines.find((l) => l.key === key);
    const dup = duplicateOf(key, v.id, packaging?.id || null, row?.batch_no);
    if (dup) return mergeInto(dup, key, Number(row?.qty || 1) || 1, v.name);
    update(key, await variantPatch(v, packaging, row));
  };
  const handleScan = async (code: string) => {
    try {
      const r = await api.get("/api/v1/barcodes/resolve", { code });
      const label = `${r.variant.name}${r.packaging ? " (" + r.packaging.name + ")" : ""}`;
      const existing = lines.find((l) => l.variant === r.variant.id && (l.packaging || null) === (r.packaging?.id || null) && (l.batch_no || "") === (r.batch?.batch_no || ""));
      if (existing) {
        const qty = Number(existing.qty || 0) + 1;
        update(existing.key, { qty: String(qty) });
        toast.success(`${label} → ${qty}`);
      } else {
        const line: EditLine = { ...newLine(), ...(await variantPatch(r.variant, r.packaging)), ...(r.batch ? { batch: r.batch.id, batch_no: r.batch.batch_no } : {}) };
        onChange([...lines, line]);
        toast.success(`Scanned ${label}`);
      }
    } catch (e: any) { toast.error(e.message); }
    setScan("");
  };
  useScanListener(handleScan, { enabled: !readOnly });
  const total = lines.reduce((s, l) => s + Number(l.qty || 0) * Number(l.unit_price || 0) * (1 - Number(l.discount_pct || 0) / 100), 0);
  return (
    <div className="line-editor">
      {!readOnly && (
        <div className="d-flex gap-2 mb-2 align-items-center">
          <div className="input-group" style={{ maxWidth: 360 }}>
            <span className="input-group-text"><i className="ti ti-scan" /></span>
            <input className="form-control" placeholder="Scan or type barcode / SKU and press Enter" value={scan} onChange={(e) => setScan(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && scan.trim()) { e.preventDefault(); handleScan(scan.trim()); } }} />
          </div>
          <button type="button" className="btn btn-outline-primary" onClick={() => onChange([...lines, newLine()])}><i className="ti ti-plus me-1" />Add line</button>
          {onToggleDiscount && (
            <div className="form-check form-switch ms-1 mb-0">
              <input className="form-check-input" type="checkbox" id="disc-toggle" checked={Boolean(discountShown || showDiscount)} onChange={onToggleDiscount} />
              <label className="form-check-label fs-12 text-muted" htmlFor="disc-toggle">Discount</label>
            </div>
          )}
        </div>
      )}
      <div className={lines.length > LINES_SCROLL_FROM ? "table-scroll" : "table-responsive"}>
        <table className="table table-sm align-middle mb-0">
          <thead className="table-light"><tr><th style={{ minWidth: 260 }}>Item</th><th style={{ width: 130 }}>Packaging</th><th style={{ width: 110 }} className="num">Qty</th>{showPrice && <th style={{ width: 130 }} className="num">{priceHeader}</th>}{showDiscount && <th style={{ width: 90 }} className="num">Disc %</th>}{reasons && <th style={{ width: 170 }}>Reason</th>}{showBatch && <th style={{ width: 150 }}>Batch</th>}{showBatch && <th style={{ width: 150 }}>Expiry</th>}{showPrice && <th className="num" style={{ width: 130 }}>Total</th>}{!readOnly && <th style={{ width: 40 }} />}</tr></thead>
          <tbody>
            {lines.map((l) => (
              <tr key={l.key}>
                <td>{readOnly ? <>{l.variant_name} <span className="text-muted fs-11">{l.sku}</span></> : <EntitySelect endpoint="/api/v1/variants" query={{ is_active: true, ...(variantQuery || {}) }} value={l.variant} onChange={() => {}} onSelectRaw={(v) => applyVariant(l.key, v)} mapOption={(v) => ({ id: v.id, label: v.name, sub: `${v.sku}${v.stock_on_hand !== undefined && v.stock_on_hand !== null ? " · stock " + v.stock_on_hand : ""}`, raw: v })} size="sm" allowClear={false} placeholder="Search product…" />}</td>
                <td>{l.packagings?.length ? <select className="form-select form-select-sm" value={l.packaging || ""} onChange={(e) => { const pk = e.target.value || null; const dup = l.variant ? duplicateOf(l.key, l.variant, pk, l.batch_no) : undefined; if (dup) mergeInto(dup, l.key, Number(l.qty || 1) || 1, l.variant_name); else update(l.key, { packaging: pk }); }} disabled={readOnly}><option value="">base unit</option>{l.packagings.map((p) => <option key={p.id} value={p.id}>{p.name} ×{Number(p.qty_in_base)}</option>)}</select> : <span className="text-muted fs-12">—</span>}</td>
                <td><input type="number" step="any" min="0" className="form-control form-control-sm text-end" value={l.qty} onChange={(e) => update(l.key, { qty: e.target.value })} readOnly={readOnly} /></td>
                {showPrice && <td><input type="number" step="0.01" className="form-control form-control-sm text-end" value={l.unit_price ?? ""} onChange={(e) => update(l.key, { unit_price: e.target.value })} readOnly={readOnly} /></td>}
                {showDiscount && <td><input type="number" step="0.01" className="form-control form-control-sm text-end" value={l.discount_pct ?? "0"} onChange={(e) => update(l.key, { discount_pct: e.target.value })} readOnly={readOnly} /></td>}
                {reasons && <td><select className="form-select form-select-sm" value={l.extra?.reason_code || ""} onChange={(e) => update(l.key, { extra: { ...(l.extra || {}), reason_code: e.target.value || null } })} disabled={readOnly}><option value="">same as document</option>{reasons.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></td>}
                {showBatch && <td><input className="form-control form-control-sm" placeholder={l.extra?.is_batch_tracked ? "required" : "optional"} value={l.batch_no || ""} onChange={(e) => update(l.key, { batch_no: e.target.value })} readOnly={readOnly} /></td>}
                {showBatch && <td><DateInput className="form-control form-control-sm" value={l.expiry_date || ""} onChange={(e) => update(l.key, { expiry_date: e.target.value })} readOnly={readOnly} /></td>}
                {showPrice && <td className="num fw-medium">{money(Number(l.qty || 0) * Number(l.unit_price || 0) * (1 - Number(l.discount_pct || 0) / 100))}</td>}
                {!readOnly && <td><button type="button" className="btn btn-sm btn-outline-danger" onClick={() => remove(l.key)}><i className="ti ti-x" /></button></td>}
              </tr>
            ))}
            {!lines.length && <tr><td colSpan={9} className="text-center text-muted py-4">No lines — scan a barcode or add a line.</td></tr>}
          </tbody>
          {showPrice && lines.length > 0 && <tfoot><tr><td colSpan={showDiscount ? (showBatch ? 7 : 5) : showBatch ? 6 : 4} className="text-end fw-semibold">Subtotal (before tax)</td><td className="num fw-bold">{money(total)}</td>{!readOnly && <td />}</tr></tfoot>}
        </table>
      </div>
      <div className="form-text mt-1">Quantities are in the selected packaging; the server converts to base units, applies price lists, schemes and taxes.</div>
    </div>
  );
}
