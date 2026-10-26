import { DateInput } from "@/components/ui/DateInput";
import { useState } from "react";
import { api } from "@/api/client";
import { fbrLineTax, LineTax, money, otherTaxLabel } from "@/lib/format";
import { TaxFooterRows } from "./TaxFooter";
import { useScanListener } from "@/lib/scan";
import { toast } from "@/lib/toast";
import { EntitySelect } from "../ui/EntitySelect";
import { LINES_SCROLL_FROM } from "../ui/Bits";

export type EditLine = { key: string; variant: string | null; variant_name?: string; sku?: string; packaging?: string | null; packagings?: { id: string; name: string; qty_in_base: string }[]; qty: string; unit_price?: string; discount_pct?: string; product_discount_pct?: string; batch?: string | null; batch_no?: string; expiry_date?: string; extra?: Record<string, any> };

/** The product's own discount for this quantity: the best live dated offer, else the standard % from its
 * minimum. Mirrors apps/catalog/models.product_discount_for so the cell shows what the server will write. */
export const prodPctFor = (l: EditLine): number => {
  if (l.product_discount_pct !== undefined && l.product_discount_pct !== null && l.product_discount_pct !== "") return Number(l.product_discount_pct) || 0;
  const rule = (l.extra as any)?.prod_rule;
  if (!rule) return 0;
  const qty = Number(l.qty || 0);
  const today = new Date().toISOString().slice(0, 10);
  const live = (rule.offers || []).filter((o: any) => o.start_date <= today && today <= o.end_date && qty >= Number(o.min_qty || 0)).map((o: any) => Number(o.discount_pct) || 0);
  if (live.length) return Math.max(...live);
  return rule.pct && qty >= (rule.min_qty || 0) ? rule.pct : 0;
};

export const newLine = (): EditLine => ({ key: crypto.randomUUID(), variant: null, qty: "1", unit_price: "", discount_pct: "0", product_discount_pct: "0" });

/**
 * Reusable document lines grid: product search (or barcode scan), packaging, qty, price, discount.
 * `showPrice`/`showDiscount`/`showBatch` toggle columns. Scans (HID or camera) resolve via /barcodes/resolve.
 */
export function LinesEditor({ lines, onChange, showPrice = true, showDiscount = true, showBatch = false, onToggleDiscount, discountShown, priceFor, readOnly, customerId, variantQuery, priceLabel, reasons, blockOutOfStock, chargeTax, defaultDiscountPct, lineTax, payableLabel = "Total Payable" }: {
  lines: EditLine[]; onChange: (lines: EditLine[]) => void; showPrice?: boolean; showDiscount?: boolean; showBatch?: boolean; priceFor?: "sale" | "cost"; readOnly?: boolean; customerId?: string | null; variantQuery?: Record<string, any>; blockOutOfStock?: boolean;
  /** When given, a switch appears to show or hide the discount column — off until a deal actually needs it. */
  onToggleDiscount?: () => void; discountShown?: boolean;
  /** Column header for the price — purchase documents buy at D.P (distributor price). */
  priceLabel?: string;
  /** Optional per-line reason picker (purchase returns: expired, damaged, wrong item…). */
  reasons?: { id: string; name: string }[];
  /** Tax-invoice customer: each line shows Taxable → GST → Line Total and the footer foots Sub Total → Total Payable. */
  chargeTax?: boolean;
  /** The document's own discount % — applies to lines with no discount of their own, as the server prices them. */
  defaultDiscountPct?: string | number;
  /** A document whose tax is not the FBR engine's (a credit note mirrors its invoice's per-unit tax) supplies its own. */
  lineTax?: (l: EditLine, baseQty: number, taxable: number) => LineTax | null;
  /** The footer's last row — a credit note credits rather than asks for payment. */
  payableLabel?: string;
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
    const basePiece = priceFor === "cost" ? v.cost_price : v.sale_price; return { variant: v.id, variant_name: v.name, sku: v.sku, packagings: v.packagings || [], packaging: packaging?.id || null, unit_price: String(price ?? ""), product_discount_pct: undefined, extra: { ...(prev?.extra || {}), prod_rule: { pct: Number(v.product_discount_pct || 0), min_qty: Number(v.product_discount_min_qty || 0), offers: v.product_offers || [] }, is_batch_tracked: v.is_batch_tracked, is_expiry_tracked: v.is_expiry_tracked, base_price: String(basePiece ?? ""), fbr_rate: v.fbr_rate_desc || "", fbr_sale_type: v.fbr_sale_type || "", fbr_mrp: v.mrp_ex_tax, fbr_others: { fed: v.fbr_fed_rate, extra: v.fbr_extra_tax_rate, further: v.fbr_further_tax_rate, wht: v.fbr_wht_rate } } };
  };
  /**
   * Re-price a line when its packaging changes. A carton is the piece price times the box size; a customer's
   * price list can set a carton price of its own, so when there is a customer the server is asked (it applies
   * the list and the slabs), and otherwise the piece base is multiplied locally.
   */
  const reprice = async (line: EditLine, packagingId: string | null) => {
    const pk = (line.packagings || []).find((x) => x.id === packagingId);
    const size = pk ? Number(pk.qty_in_base) || 1 : 1;
    let price: any = null;
    if (priceFor !== "cost" && customerId && line.variant) {
      try { const r = await api.get(`/api/v1/variants/${line.variant}/price`, { customer: customerId, packaging: packagingId || undefined }); price = r.unit_price; } catch { /* fall through */ }
    }
    if (price === null || price === undefined || price === "") {
      const base = Number(line.extra?.base_price ?? line.unit_price ?? 0);
      price = base * size;
    }
    update(line.key, { packaging: packagingId, unit_price: String(price) });
  };

  // the owner's rule (22 Oct 2026): an out-of-stock item stays visible in the search, but picking it is
  // refused here with a message — and the server refuses it again at save, whatever client asked
  const outOfStock = (v: any) => blockOutOfStock && v?.stock_on_hand !== undefined && v?.stock_on_hand !== null && Number(v.stock_on_hand) <= 0;
  const applyVariant = async (key: string, v: any, packaging?: any) => {
    if (outOfStock(v)) { toast.error(`${v.name} is not in stock — it cannot be added to the order`); return; }
    const row = lines.find((l) => l.key === key);
    const dup = duplicateOf(key, v.id, packaging?.id || null, row?.batch_no);
    if (dup) return mergeInto(dup, key, Number(row?.qty || 1) || 1, v.name);
    update(key, await variantPatch(v, packaging, row));
  };
  const handleScan = async (code: string) => {
    try {
      const r = await api.get("/api/v1/barcodes/resolve", { code });
      if (outOfStock(r.variant)) { toast.error(`${r.variant.name} is not in stock — it cannot be added to the order`); return; }
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
  // a line with no discount of its own takes the document's % — mirrors OrderService.effective_discount_pct
  const defPct = Number(defaultDiscountPct || 0);
  const discPct = (l: EditLine) => (Number(l.discount_pct || 0) || defPct) + prodPctFor(l);
  const lineNet = (l: EditLine) => Number(l.qty || 0) * Number(l.unit_price || 0) * (1 - discPct(l) / 100);
  /** Live GST preview for a tax-invoice customer (fbrLineTax mirrors the engine; the server recomputes at
   * save) — the engine counts in base units, so a carton line's qty is converted first. */
  const lineGst = (l: EditLine) => {
    const pk = (l.packagings || []).find((x) => x.id === l.packaging);
    const baseQty = Number(l.qty || 0) * (pk ? Number(pk.qty_in_base) || 1 : 1);
    if (lineTax) return lineTax(l, baseQty, lineNet(l));
    return fbrLineTax({ rate: String(l.extra?.fbr_rate || ""), taxable: lineNet(l), qty: baseQty, saleType: l.extra?.fbr_sale_type, mrp: l.extra?.fbr_mrp, others: l.extra?.fbr_others });
  };
  const gstShown = Boolean(chargeTax && showPrice);
  const taxOf = (l: EditLine) => (gstShown && l.variant ? lineGst(l) : null);
  const total = lines.reduce((s, l) => s + lineNet(l), 0);
  const sumTax = (k: "amount" | "fed" | "extra" | "further" | "wht" | "other") => lines.reduce((s, l) => s + (taxOf(l)?.[k] || 0), 0);
  const taxTotal = sumTax("amount");
  const grossTotal = lines.reduce((s, l) => s + Number(l.qty || 0) * Number(l.unit_price || 0), 0);
  // an Other Tax column (FED + Extra + Further) only once some line carries one
  const otherShown = gstShown && sumTax("other") > 0;
  // every column before Total, so the footer labels line up under the right cell
  const preTotalCols = 3 + (showPrice ? 1 : 0) + (showDiscount ? 2 : 0) + (reasons ? 1 : 0) + (showBatch ? 2 : 0) + (gstShown ? 2 : 0) + (otherShown ? 1 : 0);
  return (
    <div className="line-editor">
      {!readOnly && (
        <div className="d-flex gap-2 mb-2 align-items-center">
          <div className="input-group" style={{ maxWidth: 360 }}>
            <span className="input-group-text"><i className="ti ti-scan" /></span>
            <input className="form-control" placeholder="Scan or type barcode / SKU and press Enter" value={scan} onChange={(e) => setScan(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && scan.trim()) { e.preventDefault(); handleScan(scan.trim()); } }} />
          </div>
          <button type="button" className="btn btn-outline-primary" onClick={() => onChange([newLine(), ...lines])}><i className="ti ti-plus me-1" />Add line</button>
          {onToggleDiscount && (
            <div className="form-check form-switch ms-1 mb-0">
              <input className="form-check-input" type="checkbox" id="disc-toggle" checked={Boolean(discountShown || showDiscount)} onChange={onToggleDiscount} />
              <label className="form-check-label fs-12 text-muted" htmlFor="disc-toggle">Discount</label>
            </div>
          )}
        </div>
      )}
      <div className={lines.length > LINES_SCROLL_FROM ? "table-scroll" : "lines-editor-wrap"}>
        <table className="table table-sm align-middle mb-0">
          <thead className="table-light"><tr><th style={{ minWidth: 260 }}>Item</th><th style={{ width: 130 }}>Packaging</th><th style={{ width: 110 }} className="num">Qty</th>{showPrice && <th style={{ width: 130 }} className="num">{priceHeader}</th>}{showDiscount && <th style={{ width: 90 }} className="num">Disc %</th>}{showDiscount && <th style={{ width: 90 }} className="num" title="The product's own standing discount — set on the product, changeable here">Prod %</th>}{reasons && <th style={{ width: 170 }}>Reason</th>}{showBatch && <th style={{ width: 150 }}>Batch</th>}{showBatch && <th style={{ width: 150 }}>Expiry</th>}{gstShown && <th className="num" style={{ width: 110 }}>Taxable</th>}{gstShown && <th className="num" style={{ width: 110 }}>GST</th>}{otherShown && <th className="num" style={{ width: 110 }} title="FED + Extra Tax + Further Tax">Other Tax</th>}{showPrice && <th className="num" style={{ width: 130 }}>{gstShown ? "Line Total" : "Total"}</th>}{!readOnly && <th style={{ width: 40 }} />}</tr></thead>
          <tbody>
            {lines.map((l) => (
              <tr key={l.key}>
                <td>{readOnly ? <>{l.variant_name} <span className="text-muted fs-11">{l.sku}</span></> : <EntitySelect endpoint="/api/v1/variants" query={{ is_active: true, ...(variantQuery || {}) }} value={l.variant} onChange={() => {}} onSelectRaw={(v) => applyVariant(l.key, v)} mapOption={(v) => ({ id: v.id, label: v.name, sub: outOfStock(v) ? `${v.sku} · NOT IN STOCK` : `${v.sku}${v.stock_on_hand !== undefined && v.stock_on_hand !== null ? " · stock " + v.stock_on_hand : ""}`, raw: v })} size="sm" allowClear={false} placeholder="Search product…" />}</td>
                <td>{l.packagings?.length ? <select className="form-select form-select-sm" value={l.packaging || ""} onChange={(e) => { const pk = e.target.value || null; const dup = l.variant ? duplicateOf(l.key, l.variant, pk, l.batch_no) : undefined; if (dup) mergeInto(dup, l.key, Number(l.qty || 1) || 1, l.variant_name); else reprice(l, pk); }} disabled={readOnly}><option value="">base unit</option>{l.packagings.map((p) => <option key={p.id} value={p.id}>{p.name} ×{Number(p.qty_in_base)}</option>)}</select> : <span className="text-muted fs-12">—</span>}</td>
                <td><input type="number" step="any" min="0" className="form-control form-control-sm text-end" value={l.qty} onChange={(e) => update(l.key, { qty: e.target.value })} readOnly={readOnly} /></td>
                {showPrice && <td><input type="number" step="0.01" className="form-control form-control-sm text-end" value={l.unit_price ?? ""} onChange={(e) => update(l.key, { unit_price: e.target.value })} readOnly={readOnly} /></td>}
                {showDiscount && <td><input type="number" step="0.01" className="form-control form-control-sm text-end" value={l.discount_pct ?? "0"} onChange={(e) => update(l.key, { discount_pct: e.target.value })} readOnly={readOnly} /></td>}{showDiscount && <td><input type="number" step="0.01" className="form-control form-control-sm text-end" value={l.product_discount_pct ?? String(prodPctFor(l))} onChange={(e) => update(l.key, { product_discount_pct: e.target.value })} readOnly={readOnly} title="the product\u2019s own rule fills this from quantity and offers; typing overrides it" /></td>}
                {reasons && <td><select className="form-select form-select-sm" value={l.extra?.reason_code || ""} onChange={(e) => update(l.key, { extra: { ...(l.extra || {}), reason_code: e.target.value || null } })} disabled={readOnly}><option value="">same as document</option>{reasons.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></td>}
                {showBatch && <td><input className="form-control form-control-sm" placeholder={l.extra?.is_batch_tracked ? "required" : "optional"} value={l.batch_no || ""} onChange={(e) => update(l.key, { batch_no: e.target.value })} readOnly={readOnly} /></td>}
                {showBatch && <td><DateInput className="form-control form-control-sm" value={l.expiry_date || ""} onChange={(e) => update(l.key, { expiry_date: e.target.value })} readOnly={readOnly} /></td>}
                {gstShown && <td className="num">{l.variant ? money(lineNet(l)) : <span className="text-muted">—</span>}</td>}
                {gstShown && <td className="num">{(() => { const t = taxOf(l); return t && t.label ? <>{money(t.amount)}<div className="text-muted fs-11">{t.label}</div></> : l.variant ? <span className="text-warning" title="No FBR rate — this item will carry no GST; classify it on the product's FBR card (HS code → rate)">—</span> : <span className="text-muted">—</span>; })()}</td>}
                {otherShown && <td className="num">{(() => { const t = taxOf(l); return t && t.other ? <>{money(t.other)}<div className="text-muted fs-11">{otherTaxLabel(l.extra?.fbr_others || {})}</div></> : <span className="text-muted">—</span>; })()}</td>}
                {showPrice && <td className="num fw-medium">{money(lineNet(l) + (taxOf(l)?.amount || 0) + (taxOf(l)?.other || 0))}</td>}
                {!readOnly && <td><button type="button" className="btn btn-sm btn-outline-danger" onClick={() => remove(l.key)}><i className="ti ti-x" /></button></td>}
              </tr>
            ))}
            {!lines.length && <tr><td colSpan={9} className="text-center text-muted py-4">No lines — scan a barcode or add a line.</td></tr>}
          </tbody>
          {showPrice && lines.length > 0 && <tfoot>
            {gstShown ? <TaxFooterRows span={preTotalCols} trail={readOnly ? 0 : 1} sub={grossTotal} discount={grossTotal - total} net={total} gst={taxTotal}
                fed={sumTax("fed")} extra={sumTax("extra")} further={sumTax("further")} wht={sumTax("wht")} payableLabel={payableLabel} /> : <tr><td colSpan={preTotalCols} className="text-end fw-semibold">Subtotal (before tax)</td><td className="num fw-bold">{money(total)}</td>{!readOnly && <td />}</tr>}
          </tfoot>}
        </table>
      </div>
      <div className="form-text mt-1">Quantities are in the selected packaging; the server converts to base units, applies price lists, schemes and taxes.{gstShown ? " Taxable, GST, Other Tax and Line Total are a live preview — the saved document carries the exact figures." : ""}</div>
    </div>
  );
}
