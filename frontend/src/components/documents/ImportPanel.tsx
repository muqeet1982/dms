import { DateInput } from "@/components/ui/DateInput";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { money, titleCase } from "@/lib/format";
import { useWarehouses } from "@/lib/hooks";
import { toast } from "@/lib/toast";
import { Field } from "@/components/ui/Bits";
import { EntitySelect } from "@/components/ui/EntitySelect";
import { Modal } from "@/components/ui/Modal";

export const IMPORT_TYPES = ["products", "purchase_order", "customers", "suppliers", "opening_stock", "opening_balances", "price_list"];
const LABELS: Record<string, string> = { products: "Products / price list", purchase_order: "Purchase order (supplier order sheet)" };
export const importLabel = (t: string) => LABELS[t] || titleCase(t);

/**
 * Upload-and-import card shared by Settings → Imports and the "Import" buttons on the Products and Purchase orders pages.
 * Excel (.xlsx) or CSV; the products and purchase_order types accept the supplier's own sheets as they are.
 */
export function ImportPanel({ type: initialType, lockType, onDone }: { type?: string; lockType?: boolean; onDone?: (job: any) => void }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [type, setType] = useState(initialType || "products");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [opts, setOpts] = useState<any>({ brand: null, supplier: null, update_existing: true, warehouse: "", date: "", reference: "", qty_unit: "pieces", allow_duplicate: false });
  const fileRef = useRef<HTMLInputElement>(null);
  const warehouses = useWarehouses();
  const run = async () => {
    const fd = new FormData();
    fd.append("type", type); fd.append("file", file!);
    if (type === "products") fd.append("options", JSON.stringify({ brand: opts.brand || undefined, supplier: opts.supplier || undefined, update_existing: opts.update_existing, filename: file!.name }));
    if (type === "purchase_order") fd.append("options", JSON.stringify({ supplier: opts.supplier || undefined, warehouse: opts.warehouse || undefined, date: opts.date || undefined, reference: opts.reference || undefined, qty_unit: opts.qty_unit, allow_duplicate: opts.allow_duplicate, filename: file!.name }));
    setBusy(true);
    try {
      const r = await api.upload("/api/v1/imports", fd);
      const sum = r.summary || {};
      if (type === "products") toast[r.rows_failed ? "warning" : "success"](`${sum.created ?? 0} created, ${sum.updated ?? 0} updated${sum.categories_created ? `, ${sum.categories_created} new categories` : ""}${r.rows_failed ? `, ${r.rows_failed} failed` : ""}`);
      else if (type === "purchase_order") {
        if (sum.po_number) toast[r.rows_failed ? "warning" : "success"](`${sum.po_number} created${sum.supplier_ref ? ` for ${sum.supplier ? sum.supplier + " " : ""}doc ${sum.supplier_ref}` : ""}: ${sum.lines} lines, ${money(sum.total)}${r.rows_failed ? ` (${r.rows_failed} codes not found)` : ""}`);
        else toast.error(r.errors?.[0]?.error || "Import failed");
      } else toast[r.rows_failed ? "warning" : "success"](`${r.rows_ok} ok, ${r.rows_failed} failed`);
      setFile(null); if (fileRef.current) fileRef.current.value = "";
      qc.invalidateQueries({ queryKey: ["imports"] }); qc.invalidateQueries({ queryKey: ["list"] }); qc.invalidateQueries({ queryKey: ["po-stats"] }); qc.invalidateQueries({ queryKey: ["product-stats"] });
      onDone?.(r);
      if (type === "purchase_order" && sum.po_id && !onDone) nav(`/app/purchasing/orders/${sum.po_id}`);
    } catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
  };
  return (
    <>
      <div className="row">
        {!lockType && <Field label="Type" col="col-md-4"><select className="form-select" value={type} onChange={(e) => setType(e.target.value)}>{IMPORT_TYPES.map((t) => <option key={t} value={t}>{importLabel(t)}</option>)}</select></Field>}
        <Field label="File (.xlsx or .csv)" col={lockType ? "col-md-12" : "col-md-8"}><input ref={fileRef} type="file" accept=".xlsx,.csv" className="form-control" onChange={(e) => setFile(e.target.files?.[0] || null)} /></Field>
      </div>
      {type === "products" && <>
        <div className="row"><Field label="Brand (applied to every row)" col="col-md-4"><EntitySelect endpoint="/api/v1/brands" value={opts.brand} onChange={(v) => setOpts({ ...opts, brand: v })} mapOption={(b) => ({ id: b.id, label: b.name })} placeholder="from file / none" /></Field><Field label="Supplier (applied to every row)" col="col-md-4"><EntitySelect endpoint="/api/v1/suppliers" value={opts.supplier} onChange={(v) => setOpts({ ...opts, supplier: v })} mapOption={(s) => ({ id: s.id, label: s.name })} placeholder="from file / none" /></Field><div className="col-md-4 mb-3 d-flex align-items-end"><div className="form-check"><input className="form-check-input" type="checkbox" id="imp-upd" checked={opts.update_existing} onChange={(e) => setOpts({ ...opts, update_existing: e.target.checked })} /><label htmlFor="imp-upd" className="form-check-label fs-13">Update existing products (match by code)</label></div></div></div>
        <div className="alert alert-light border fs-12 py-2 mb-3"><i className="ti ti-info-circle me-1 text-primary" />Upload the supplier's <strong>Packing &amp; Price List</strong> workbook as-is: columns <code>Code · Product Name · UNIT · Packing · D.P · T.P · R.P</code>. Heading rows (e.g. <em>TINTA COLOR</em>) become categories; a date above the table is saved as the price-list date. Re-upload the next price list to update prices in place — products are matched by code.</div>
      </>}
      {type === "purchase_order" && <>
        <div className="row">
          <Field label="Supplier" col="col-md-4"><EntitySelect endpoint="/api/v1/suppliers" value={opts.supplier} onChange={(v) => setOpts({ ...opts, supplier: v })} mapOption={(s) => ({ id: s.id, label: s.name })} placeholder="from sheet title (e.g. Keune Pakistan)" /></Field>
          <Field label="Deliver to warehouse" col="col-md-4"><select className="form-select" value={opts.warehouse || warehouses.defaultId || ""} onChange={(e) => setOpts({ ...opts, warehouse: e.target.value })}>{warehouses.rows.map((w: any) => <option key={w.id} value={w.id}>{w.name}{w.is_default ? " (default)" : ""}</option>)}</select></Field>
          <Field label="Order date" col="col-md-4"><DateInput className="form-control" value={opts.date} onChange={(e) => setOpts({ ...opts, date: e.target.value })} /><div className="form-text fs-11">blank = date in the sheet or file name (e.g. <em>LHR-Order-20-9-26</em>)</div></Field>
          <Field label="Reference / title" col="col-md-8"><input className="form-control" value={opts.reference} onChange={(e) => setOpts({ ...opts, reference: e.target.value })} placeholder="from sheet title (e.g. Lahore Distribution Order)" /></Field>
          <Field label="Quantities are in" col="col-md-4"><select className="form-select" value={opts.qty_unit} onChange={(e) => setOpts({ ...opts, qty_unit: e.target.value })}><option value="pieces">pieces (units)</option><option value="cartons">cartons (× packing)</option></select></Field>
          <div className="col-md-4 mb-3 d-flex align-items-end"><div className="form-check"><input className="form-check-input" type="checkbox" id="imp-dup" checked={opts.allow_duplicate} onChange={(e) => setOpts({ ...opts, allow_duplicate: e.target.checked })} /><label htmlFor="imp-dup" className="form-check-label fs-13">Import anyway if this supplier document was already imported</label></div></div>
        </div>
        <div className="alert alert-light border fs-12 py-2 mb-3"><i className="ti ti-info-circle me-1 text-primary" />Upload the sheet as-is — both layouts work: your own <strong>order sheet</strong> (<code>Code · Product Name · UNIT</code> + a quantity column, headed <em>Qty</em>/<em>Order</em> or just the unlabeled numbers on the right) and the supplier's <strong>sales-order print</strong> (<code>Item · Item Description · Unit · Qty · Rate</code> with <em>S.O. NUMBER</em> / <em>S.O. DATE</em> in the header — both are picked up, the S.O. number becomes the supplier reference). Rows without a quantity are skipped; prices come from the <em>Rate</em>/<em>D.P</em> column when present, otherwise from each product's D.P. One <strong>draft purchase order</strong> is created with the file attached; unknown codes are listed per row so you can import the price list first.</div>
      </>}
      <div className="d-flex gap-2 flex-wrap"><button className="btn btn-outline-secondary" onClick={() => api.download(`/api/v1/imports/template/${type}?as=xlsx`, `${type}-template.xlsx`)}><i className="ti ti-file-spreadsheet me-1" />Excel template</button><button className="btn btn-outline-secondary" onClick={() => api.download(`/api/v1/imports/template/${type}`, `${type}-template.csv`)}><i className="ti ti-file-type-csv me-1" />CSV template</button><button className="btn btn-primary ms-auto" disabled={!file || busy} onClick={run}><i className="ti ti-upload me-1" />{busy ? "Importing…" : "Import"}</button></div>
    </>
  );
}

/** "Import" button + modal for a page (Products → price list, Purchase orders → order sheet). */
export function ImportButton({ type, label, icon = "ti-file-spreadsheet", onDone, className = "btn btn-outline-primary" }: { type: string; label: string; icon?: string; onDone?: (job: any) => void; className?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className={className} onClick={() => setOpen(true)}><i className={`ti ${icon} me-1`} />{label}</button>
      <Modal open={open} onClose={() => setOpen(false)} title={<><i className="ti ti-upload me-2 text-primary" />{label}</>} size="lg">
        <ImportPanel type={type} lockType onDone={(job) => { setOpen(false); onDone?.(job); }} />
      </Modal>
    </>
  );
}
