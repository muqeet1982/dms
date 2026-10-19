import { DateInput } from "@/components/ui/DateInput";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { boxes, date, datetime, money, qty } from "@/lib/format";
import { useAction, useDoc, useOptions, useWarehouses } from "@/lib/hooks";
import { pageRights, usePerms } from "@/lib/perms";
import { toast } from "@/lib/toast";
import { Alert, Field, InfoItem, LINES_SCROLL_FROM, PageHeader, SaveError, Spinner, StatCard, StatusBadge } from "@/components/ui/Bits";
import { Column } from "@/components/ui/DataTable";
import { EntitySelect } from "@/components/ui/EntitySelect";
import { ListPage } from "@/components/ui/ListPage";
import { ConfirmDialog, Modal } from "@/components/ui/Modal";
import { DocTabs } from "@/components/documents/DocTabs";
import { ImportButton } from "@/components/documents/ImportPanel";
import { EditLine, LinesEditor } from "@/components/documents/LinesEditor";
import { WorkflowStepper } from "@/components/documents/WorkflowStepper";

/** Add up one column of a line table — the totals row at the end of a document. */
const sumBy = (rows: any[], pick: (r: any) => any) => (rows || []).reduce((n, r) => n + (Number(pick(r)) || 0), 0);

const supFilter = { name: "supplier", label: "Supplier", type: "entity" as const, endpoint: "/api/v1/suppliers", mapOption: (s: any) => ({ id: s.id, label: s.name }) };

export function PurchaseOrdersPage() {
  const stats = useQuery({ queryKey: ["po-stats"], queryFn: () => api.get("/api/v1/purchase-orders/stats") });
  const { can } = usePerms();
  const [imported, setImported] = useState<any>(null);
  const cols: Column<any>[] = [{ key: "number", header: "PO #", sortKey: "number", render: (p) => <><span className="fw-semibold text-dark">{p.number}</span>{p.reference && <div className="text-muted fs-11">{p.reference}</div>}</> }, { key: "date", header: "Date", sortKey: "date", render: (p) => <>{date(p.date)}<div className="text-muted fs-11">exp {date(p.expected_date)}</div></> }, { key: "supplier_name", header: "Supplier" }, { key: "warehouse_name", header: "Warehouse" }, { key: "lines_count", header: "Lines", num: true }, { key: "grns_count", header: "GRNs", num: true }, { key: "total", header: "Total", num: true, sortKey: "total", render: (p) => money(p.total) }, { key: "status", header: "Status", render: (p) => <StatusBadge status={p.status} /> }, { key: "print", header: "", className: "no-row-click text-end", render: (p) => <button className="btn btn-sm btn-outline-secondary py-0" title="Print this PO" onClick={(e) => { e.stopPropagation(); api.printDoc(`/api/v1/purchase-orders/${p.id}/pdf`); }}><i className="ti ti-printer" /></button> }];
  return (
    <>
      <ListPage page="purchase_orders" title="Purchase orders" endpoint="/api/v1/purchase-orders" columns={cols} rowLink={(p) => `/app/purchasing/orders/${p.id}`} createTo="/app/purchasing/orders/new" createLabel="New PO" defaultOrdering="-date"
        actions={pageRights("purchase_orders").add && <ImportButton type="purchase_order" label="Import order sheet (Excel)" onDone={setImported} />}
        filters={[{ name: "status", label: "Status", type: "select", options: ["draft", "sent", "partially_received", "received", "cancelled"].map((s) => ({ value: s, label: s.replace(/_/g, " ") })) }, supFilter, { name: "date_from", label: "From", type: "date" }, { name: "date_to", label: "To", type: "date" }]}
        stats={stats.data && <div className="row g-3 mb-3"><div className="col-6 col-md-3"><StatCard label="Draft" value={stats.data.draft} icon="ti-file" color="secondary" /></div><div className="col-6 col-md-3"><StatCard label="Open (sent / partial)" value={stats.data.open} icon="ti-clock" color="warning" /></div><div className="col-6 col-md-3"><StatCard label="Received this month" value={stats.data.received_month} icon="ti-package-import" color="success" /></div><div className="col-6 col-md-3"><StatCard label="Value this month" value={money(stats.data.value_month, 0)} icon="ti-coins" color="primary" /></div></div>} />
      <ImportResultModal job={imported} onClose={() => setImported(null)} />
    </>
  );
}

/**
 * What an uploaded order sheet turned into. The rows the sheet could not place — codes that are not in the
 * catalogue, and lines carrying no quantity — used to be written into the order's notes; they describe the upload
 * and not the order, so they are reported here from the import job and the notes stay the buyer's own.
 */
function ImportResultModal({ job, onClose }: { job: any; onClose: () => void }) {
  const nav = useNavigate();
  const sum = job?.summary || {};
  // row 0 is the import itself stopping (a supplier document already imported, an unreadable sheet) — not a line
  const stopper = (job?.errors || []).find((e: any) => !Number(e.row));
  const failed = (job?.errors || []).filter((e: any) => Number(e.row) > 0).map((e: any) => ({ row: e.row, sku: e.data?.sku || "", name: e.data?.name || "", qty: e.data?.qty, price: e.data?.unit_price, why: e.error }));
  const noQty: any[] = sum.skipped_rows || [];
  const amount = (r: any) => Number(r.qty || 0) * Number(r.price || 0);
  return (
    <Modal open={Boolean(job)} title={<><i className="ti ti-file-spreadsheet me-2 text-primary" />{sum.po_number ? `${sum.po_number} created from the order sheet` : "The order sheet was not imported"}</>} onClose={onClose} size="xl"
      footer={<><button className="btn btn-light" onClick={onClose}>Close</button>{sum.po_id && <button className="btn btn-primary" onClick={() => { onClose(); nav(`/app/purchasing/orders/${sum.po_id}`); }}>Open {sum.po_number}</button>}</>}>
      {stopper && <Alert kind="danger">{stopper.error}</Alert>}
      {sum.po_id && <>
        <div className="row g-3 mb-3"><InfoItem label="Supplier">{sum.supplier}</InfoItem><InfoItem label="Supplier document">{sum.supplier_ref || "—"}</InfoItem><InfoItem label="Reference">{sum.reference || "—"}</InfoItem><InfoItem label="Warehouse / date">{sum.warehouse} · {date(sum.date)}</InfoItem></div>
        <div className="row g-3 mb-3"><div className="col-sm-4"><StatCard label="Lines imported" value={qty(sum.lines)} icon="ti-list-check" color="success" /></div><div className="col-sm-4"><StatCard label="Units ordered" value={qty(sum.units)} icon="ti-package" color="info" sub={sum.qty_unit === "cartons" ? "sheet read as cartons" : undefined} /></div><div className="col-sm-4"><StatCard label="Order value" value={money(sum.total)} icon="ti-coins" color="primary" /></div></div>
      </>}
      {failed.length > 0 && <>
        <div className="fs-13 fw-semibold mb-1">Left off the order — {failed.length} row{failed.length === 1 ? "" : "s"} the sheet could not place</div>
        <div className={failed.length > LINES_SCROLL_FROM ? "table-scroll" : "table-responsive"}>
          <table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>Row</th><th>Item</th><th className="num">Qty</th><th className="num">Rate</th><th className="num">Amount</th><th>Why</th></tr></thead>
            <tbody>{failed.map((r: any) => <tr key={r.row}><td>{r.row}</td><td>{r.name || "—"}<div className="text-muted fs-11 font-monospace">{r.sku}</div></td><td className="num">{qty(r.qty)}</td><td className="num">{money(r.price)}</td><td className="num">{money(r.price ? amount(r) : null)}</td><td className="fs-11 text-danger">{r.why}</td></tr>)}</tbody>
            {/* what is missing from the order adds up too, so the sheet's own total can be accounted for */}
            <tfoot><tr className="fw-semibold"><td colSpan={2} className="text-end">{failed.length} row{failed.length === 1 ? "" : "s"}</td><td className="num">{qty(sumBy(failed, (r: any) => r.qty))}</td><td className="text-end text-muted fs-11">units</td><td className="num">{money(sumBy(failed, amount))}</td><td /></tr></tfoot>
          </table>
        </div>
      </>}
      {Number(sum.skipped_no_qty) > 0 && (
        <details className="fs-12 mt-2"><summary className="cursor-pointer">{qty(sum.skipped_no_qty)} row(s) on the sheet carried no quantity and were left off{noQty.length > 0 && noQty.length < Number(sum.skipped_no_qty) ? ` — the first ${noQty.length} with a code:` : noQty.length > 0 ? ":" : ""}</summary>
          <ul className="mb-0 mt-1">{noQty.map((r: any, i: number) => <li key={i}><span className="font-monospace">{r.sku}</span> {r.name}</li>)}</ul>
        </details>
      )}
      {sum.po_id && !failed.length && !Number(sum.skipped_no_qty) && <div className="fs-13 text-success"><i className="ti ti-circle-check me-1" />Every row on the sheet is on the order.</div>}
    </Modal>
  );
}

export function PurchaseOrderFormPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const existing = useDoc("/api/v1/purchase-orders", id);
  const warehouses = useWarehouses();
  // A new order opens on the first supplier the picker would list — same endpoint, filter and ordering, so it is
  // the row the dropdown shows first. Not asked for when the order already has one (editing, or started from a
  // supplier's page), so nothing is overwritten.
  const firstSupplier = useQuery({ queryKey: ["first-supplier"], queryFn: () => api.list("/api/v1/suppliers", { is_active: true, page_size: 1 }), enabled: !id && !sp.get("supplier") });
  const [form, setForm] = useState<any>(null);
  const [lines, setLines] = useState<EditLine[]>([]);
  const [err, setErr] = useState<string | null>(null);
  if ((id && existing.isLoading) || firstSupplier.isLoading) return <Spinner />;
  if (!form) {
    const o = existing.data;
    // freight and supplier_ref are no longer typed here, but an edit keeps whatever the document already has
    // (an imported supplier order sheet carries its S.O. number in supplier_ref)
    setForm(o ? { supplier: o.supplier, warehouse: o.warehouse, date: o.date, expected_date: o.expected_date || "", freight: o.freight, supplier_ref: o.supplier_ref, reference: o.reference || "", notes: o.notes, version: o.version } : { supplier: sp.get("supplier") || firstSupplier.data?.results?.[0]?.id || null, warehouse: "", date: new Date().toISOString().slice(0, 10), expected_date: "", reference: "", notes: "" });
    if (o) setLines(o.lines.map((l: any) => ({ key: l.id, variant: l.variant, variant_name: l.variant_name, sku: l.variant_sku, packaging: null, packagings: [], qty: String(Number(l.qty_ordered)), unit_price: l.unit_price, discount_pct: l.discount_pct })));
    return <Spinner />;
  }
  const submit = async () => {
    const valid = lines.filter((l) => l.variant && Number(l.qty) > 0);
    // the select shows warehouses.defaultId as a fallback; honour it so a PO with the default picked does not
    // fail validation just because the user never touched the field
    const warehouse = form.warehouse || warehouses.defaultId || "";
    const missing = [!form.supplier && "supplier", !warehouse && "warehouse", !valid.length && "at least one line with a quantity"].filter(Boolean);
    if (missing.length) { const m = `Please fill in: ${missing.join(", ")}.`; setErr(m); return toast.error(m); }
    setErr(null);
    try {
      const body = { ...form, warehouse, lines: valid.map((l) => ({ variant: l.variant, packaging: l.packaging || null, qty: l.qty, unit_price: l.unit_price || 0, discount_pct: l.discount_pct || 0, price_per_packaging: Boolean(l.packaging) })) };
      const r = id ? await api.patch(`/api/v1/purchase-orders/${id}`, body) : await api.post("/api/v1/purchase-orders", body, true);
      toast.success("Saved");
      nav(`/app/purchasing/orders/${r.id}`);
    } catch (e: any) { setErr(e.message); toast.error(e.message); }
  };
  return (
    <>
      <PageHeader title={id ? `Edit ${existing.data?.number}` : "New purchase order"} crumbs={[{ label: "Purchase orders", to: "/app/purchasing/orders" }, { label: id ? "Edit" : "New" }]} />
      <SaveError error={err} what="purchase order" />
      <div className="card mb-3"><div className="card-body"><div className="row">
        <Field label="Supplier *" col="col-md-4"><EntitySelect endpoint="/api/v1/suppliers" query={{ is_active: true }} value={form.supplier} onChange={(v) => setForm({ ...form, supplier: v })} mapOption={(s) => ({ id: s.id, label: s.name, sub: s.code })} /></Field>
        <Field label="Deliver to warehouse *" col="col-md-3"><select className="form-select" value={form.warehouse || warehouses.defaultId || ""} onChange={(e) => setForm({ ...form, warehouse: e.target.value })}>{warehouses.rows.map((w: any) => <option key={w.id} value={w.id}>{w.name}{w.is_default ? " (default)" : ""}</option>)}</select></Field>
        <Field label="Order date" col="col-md-2"><DateInput className="form-control" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} /></Field><Field label="Expected (optional)" col="col-md-2"><DateInput className="form-control" value={form.expected_date} onChange={(e) => setForm({ ...form, expected_date: e.target.value })} /></Field>
        <Field label="Reference / title" col="col-md-4"><input className="form-control" value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} placeholder="e.g. Lahore Distribution Order" /></Field><Field label="Notes" col="col-md-8"><input className="form-control" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field></div>
        {/* Scoped to this supplier, but `for_supplier` also lets through anything nobody has assigned a supplier
            to: the strict filter hid every such product, and a catalogue imported from a price list has no
            supplier column — a whole company's search came back empty and looked broken. */}
        <LinesEditor lines={lines} onChange={setLines} priceFor="cost" variantQuery={form.supplier ? { for_supplier: form.supplier } : undefined} />
      </div></div>
      <div className="d-flex gap-2 justify-content-end"><button className="btn btn-light" onClick={() => nav(-1)}>Cancel</button><button className="btn btn-primary" onClick={submit}>Save</button></div>
    </>
  );
}

export function PurchaseOrderDetailPage() {
  const { id } = useParams();
  const { can } = usePerms();
  const q = useDoc("/api/v1/purchase-orders", id);
  const { run, busy } = useAction("/api/v1/purchase-orders", id, [["po-stats"], ["po-receiving", id]]);
  const [cancel, setCancel] = useState(false);
  const [closeRest, setCloseRest] = useState(false);
  const [flow, setFlow] = useState<any>(null);
  const nav = useNavigate();
  if (q.isLoading) return <Spinner />;
  const po = q.data;
  if (!po) return null;
  return (
    <>
      <PageHeader title={po.reference ? `${po.number} — ${po.reference}` : po.number} badge={<StatusBadge status={po.status} />} crumbs={[{ label: "Purchase orders", to: "/app/purchasing/orders" }, { label: po.number }]}
        actions={<><button className="btn btn-outline-secondary" onClick={() => api.printDoc(`/api/v1/purchase-orders/${po.id}/pdf`)}><i className="ti ti-printer me-1" />Print</button><button className="btn btn-outline-secondary" onClick={() => api.download(`/api/v1/purchase-orders/${po.id}/pdf`, `${po.number}.pdf`)}><i className="ti ti-file-type-pdf me-1" />PDF</button>{["draft", "sent"].includes(po.status) && pageRights("purchase_orders").edit && <Link to={`/app/purchasing/orders/${po.id}/edit`} className="btn btn-outline-primary"><i className="ti ti-edit me-1" />Edit</Link>}{po.status === "draft" && pageRights("purchase_orders").extras.approve && <button className="btn btn-success" disabled={!!busy} onClick={() => run("send", {}, { success: "PO approved & sent" })}><i className="ti ti-send me-1" />Approve / send</button>}{["sent", "partially_received"].includes(po.status) && pageRights("grns").add && <Link to={`/app/purchasing/grns/new?po=${po.id}`} className="btn btn-primary"><i className="ti ti-package-import me-1" />Receive goods</Link>}{po.status === "partially_received" && pageRights("purchase_orders").edit && <button className="btn btn-outline-warning" onClick={() => setCloseRest(true)}><i className="ti ti-flag-off me-1" />Close remaining</button>}{!["received", "cancelled"].includes(po.status) && pageRights("purchase_orders").delete && <button className="btn btn-outline-danger" onClick={() => setCancel(true)}>Cancel</button>}</>} />
      {po.status !== "cancelled" && <WorkflowStepper orderId={po.id} endpoint="/api/v1/purchase-orders" title="Order to payment" onData={setFlow} />}
      <div className="row g-3 mb-3"><div className="col-sm-6 col-xl-3"><StatCard label="Order value" value={money(po.total)} icon="ti-coins" color="primary" /></div><div className="col-sm-6 col-xl-3"><StatCard label="Goods receipts" value={po.grns_count} icon="ti-package-import" color="success" /></div><div className="col-sm-6 col-xl-3"><StatCard label="Supplier" value={<span className="d-inline-block text-truncate" style={{ maxWidth: "100%" }} title={po.supplier_name}>{po.supplier_name}</span>} icon="ti-building-factory-2" color="info" /></div><div className="col-sm-6 col-xl-3"><StatCard label="Expected" value={date(po.expected_date)} icon="ti-calendar" color="secondary" sub={`ordered ${date(po.date)}`} /></div></div>
      <div className="card mb-3"><div className="card-body">
        <div className="row g-3 mb-3"><InfoItem label="Warehouse">{po.warehouse_name}</InfoItem><InfoItem label="Reference">{po.reference || "—"}</InfoItem>{po.supplier_ref && <InfoItem label="Supplier ref">{po.supplier_ref}</InfoItem>}<InfoItem label="Created by">{po.created_by_name}</InfoItem><InfoItem label="Notes">{po.notes || "—"}</InfoItem></div>
        <div className={po.lines.length > LINES_SCROLL_FROM ? "table-scroll" : "table-responsive"}>
        <table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>#</th><th>Item</th><th className="num">Ordered</th><th className="num">Received</th><th className="num">Rejected</th><th className="num">Not received</th><th className="num">Pending</th><th>Placed in</th><th className="num">D.P (unit)</th><th className="num">Total</th></tr></thead>
          <tbody>{po.lines.map((l: any, i: number) => {
            const p = (flow?.lines || []).find((r: any) => r.line_id === l.id);
            const cell = (v: any, cls = "") => <td className={`num ${cls}`}>{boxes(v, l.packing)}{l.packing > 1 && Number(v) > 0 && <div className="text-muted fs-11">{qty(v)} units</div>}</td>;
            return (
              <tr key={l.id} className={l.closed ? "text-muted" : ""}>
                <td>{i + 1}</td>
                <td>{l.variant_name}<div className="text-muted fs-11 font-monospace">{l.item_code || l.upc || l.variant_sku}{l.unit_size ? ` · ${l.unit_size}` : ""}{l.packing > 1 ? ` · ${l.packing}/box` : ""}</div>{l.closed && <div className="fs-11 text-warning">closed{l.closed_reason ? `: ${l.closed_reason}` : ""}</div>}</td>
                {cell(l.qty_ordered)}
                {cell(l.qty_received, "text-success")}
                {cell(p?.rejected?.units || 0, "text-danger")}
                {cell(p?.not_received?.units || 0, "text-warning")}
                {cell(l.qty_pending, Number(l.qty_pending) > 0 ? "text-warning fw-medium" : "text-muted")}
                <td className="fs-11">{(p?.placed || []).length ? (p.placed as any[]).map((x) => <div key={x.where}>{x.where} <span className="text-muted">({boxes(x.qty, l.packing)})</span></div>) : <span className="text-muted">—</span>}</td>
                <td className="num">{money(l.unit_price)}</td>
                <td className="num fw-medium">{money(l.line_total)}</td>
              </tr>
            );
          })}</tbody>
          <tfoot>
            {/* the quantity columns add up too, so the end of the table ties out against the supplier's own
                document — their challan says "TOTAL : 18,395" and you can check it here without a calculator.
                Totals are in units: boxes cannot be added up across items with different box sizes. */}
            <tr className="fw-semibold">
              <td />
              <td className="text-end">{po.lines.length} line{po.lines.length === 1 ? "" : "s"}</td>
              <td className="num">{qty(sumBy(po.lines, (l: any) => l.qty_ordered))}</td>
              <td className="num text-success">{qty(sumBy(po.lines, (l: any) => l.qty_received))}</td>
              <td className="num text-danger">{qty(sumBy(flow?.lines || [], (r: any) => r.rejected?.units))}</td>
              <td className="num text-warning">{qty(sumBy(flow?.lines || [], (r: any) => r.not_received?.units))}</td>
              <td className="num text-warning">{qty(sumBy(po.lines, (l: any) => l.qty_pending))}</td>
              <td colSpan={2} className="text-end text-muted fs-11">units</td>
              <td className="num">{money(sumBy(po.lines, (l: any) => l.line_total))}</td>
            </tr>
                        {Number(po.discount) > 0 && <tr><td colSpan={9} className="text-end">Discount</td><td className="num">-{money(po.discount)}</td></tr>}
                                    {Number(po.freight) > 0 && <tr><td colSpan={9} className="text-end">Freight</td><td className="num">{money(po.freight)}</td></tr>}
            <tr className="fw-bold"><td colSpan={9} className="text-end">Total</td><td className="num">{money(po.total)}</td></tr>
          </tfoot></table>
        </div>
      </div></div>
      <DocTabs model="PurchaseOrder" id={po.id} showJournal={false} extra={[{ key: "receiving", label: "Receiving", render: () => <ReceivingPanel po={po} /> }]} />
      <ConfirmDialog open={closeRest} title="Close what is still outstanding" message="The supplier is not sending the rest: every line still waiting is closed and the order counts as received." withReason confirmText="Close remaining" onClose={() => setCloseRest(false)} onConfirm={async (r) => { await run("close-remaining", { reason: r }, { success: "Order closed" }); setCloseRest(false); }} />
      <ConfirmDialog open={cancel} title="Cancel purchase order" withReason danger onClose={() => setCancel(false)} onConfirm={async (r) => { await run("cancel", { reason: r }); setCancel(false); }} />
    </>
  );
}

/**
 * One order, one receiving record: what is still owed, every delivery against the order, and where each line stands.
 *
 * The owner's question is "how much is still to come", so pending leads — a count of receipt documents answers
 * nothing. The deliveries stay listed underneath because stock and the books move as each one is posted, and a
 * cancelled row is struck through so it cannot be read as goods that arrived.
 */
function ReceivingPanel({ po }: { po: any }) {
  const q = useQuery({ queryKey: ["po-receiving", po.id], queryFn: () => api.get(`/api/v1/purchase-orders/${po.id}/receiving`) });
  if (q.isLoading) return <Spinner />;
  if (q.isError) return <Alert kind="danger">{(q.error as any)?.message || "The receiving record could not be loaded."}</Alert>;
  const r = q.data;
  if (!r) return null;
  const lines: any[] = r.lines || [];
  const deliveries: any[] = r.deliveries || [];
  const open = r.open_receipt;
  // 179 rows stay readable by dropping the columns that are empty all the way down: rejected and not-received are
  // the exception rather than the rule, and they only ever come off a delivery that has been posted
  const anyRejected = lines.some((l) => Number(l.rejected?.units) > 0);
  const anyMissing = lines.some((l) => Number(l.not_received?.units) > 0);
  const donePct = Number(r.ordered_units) > 0 ? Math.min(100, (Number(r.received_units) / Number(r.ordered_units)) * 100) : 0;
  const cell = (v: any, cls = "", dashIfZero = false) => <td className={`num ${cls}`}>{dashIfZero && !Number(v?.units) ? <span className="text-muted">—</span> : boxes(v?.units, v?.packing)}{Number(v?.packing) > 1 && Number(v?.units) > 0 && <div className="text-muted fs-11">{qty(v.units)} units</div>}</td>;
  return (
    <div>
      <div className="d-flex flex-wrap align-items-end gap-4 mb-3">
        <div><div className="fs-24 fw-bold text-warning lh-1">{qty(r.pending_units)}</div><div className="fs-12 text-muted mt-1">units still to come</div></div>
        <div><div className="fs-18 fw-semibold lh-1">{qty(r.received_units)} <span className="fs-13 fw-normal text-muted">of {qty(r.ordered_units)}</span></div><div className="fs-12 text-muted mt-1">units received</div></div>
        <div><div className="fs-18 fw-semibold lh-1">{qty(r.lines_complete)} <span className="fs-13 fw-normal text-muted">of {qty(r.lines_total)}</span></div><div className="fs-12 text-muted mt-1">lines complete</div></div>
        <div className="flex-grow-1" style={{ minWidth: 140 }}><div className="progress" style={{ height: 6 }}><div className="progress-bar bg-success" style={{ width: `${donePct}%` }} /></div></div>
        {open
          ? pageRights("grns").edit && <Link to={`/app/purchasing/grns/${open.id}/edit`} className="btn btn-primary"><i className="ti ti-pencil me-1" />Continue {open.number}</Link>
          : ["sent", "partially_received"].includes(po.status) && pageRights("grns").add && <Link to={`/app/purchasing/grns/new?po=${po.id}`} className="btn btn-outline-primary"><i className="ti ti-package-import me-1" />Receive goods</Link>}
      </div>

      <div className="fs-13 fw-semibold mb-1">Deliveries <span className="fw-normal text-muted">— {deliveries.length} against this order, oldest first</span></div>
      <div className={`mb-1 ${deliveries.length > LINES_SCROLL_FROM ? "table-scroll" : "table-responsive"}`}>
        <table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>Date</th><th>Receipt</th><th>Challan</th><th className="num">Items</th><th className="num">Units</th><th>Received by</th><th>Status</th><th /></tr></thead>
          <tbody>{deliveries.map((d) => {
            // a cancelled delivery reversed its stock, so it is struck through cell by cell — on the row alone the
            // table's own cell colour wins and it reads like goods that arrived
            const dim = d.status === "cancelled" ? "text-muted text-decoration-line-through" : "";
            return (
              <tr key={d.id} className={d.is_open ? "table-warning" : ""}>
                <td className={dim}>{date(d.date)}</td>
                <td className={dim}><Link to={`/app/purchasing/grns/${d.id}`} className={dim || "fw-semibold"}>{d.number}</Link></td>
                <td className={dim}>{d.challan || <span className="text-muted">—</span>}</td>
                <td className={`num ${dim}`}>{qty(d.items)}</td>
                <td className={`num ${dim}`}>{qty(d.units)}{Number(d.rejected) > 0 && <div className="fs-11 text-danger">{qty(d.rejected)} rejected</div>}</td>
                <td className={dim}>{d.received_by || "—"}</td>
                <td><StatusBadge status={d.status} label={d.is_open ? "Still being keyed" : undefined} /></td>
                <td className="text-end">{d.is_open && pageRights("grns").edit && <Link to={`/app/purchasing/grns/${d.id}/edit`} className="btn btn-sm btn-primary">Continue</Link>}</td>
              </tr>
            );
          })}
          {!deliveries.length && <tr><td colSpan={8} className="text-muted text-center py-3">Nothing has been delivered against this order yet.</td></tr>}</tbody>
        </table>
      </div>
      <div className="fs-11 text-muted mb-3">{open ? `One order keeps one receipt open: everything keyed into ${open.number} adds up until it is posted, and posting is what moves the stock in. The next delivery then starts a fresh receipt.` : "Each delivery is posted on its own day — posting is what moves the stock in."}</div>

      <div className="fs-13 fw-semibold mb-1">Lines <span className="fw-normal text-muted">— {lines.length}, {qty(r.pending_units)} unit(s) still owed</span></div>
      <div className={lines.length > LINES_SCROLL_FROM ? "table-scroll" : "table-responsive"}>
        <table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>#</th><th>Item</th><th className="num">Ordered</th><th className="num">Received</th>{anyRejected && <th className="num">Rejected</th>}{anyMissing && <th className="num">Not received</th>}<th className="num">Pending</th></tr></thead>
          <tbody>{lines.map((l, i) => (
            <tr key={l.line_id} className={l.closed ? "text-muted" : ""}>
              <td>{i + 1}</td>
              <td>{l.name}<div className="text-muted fs-11 font-monospace">{l.item_code || l.upc || l.sku}{Number(l.packing) > 1 ? ` · ${l.packing}/box` : ""}</div>{l.closed && <div className="fs-11 text-warning"><i className="ti ti-flag-off me-1" />closed — the rest is not coming{l.closed_reason ? `: ${l.closed_reason}` : ""}</div>}</td>
              {cell(l.ordered)}
              {cell(l.received, Number(l.received?.units) > 0 ? "text-success" : "", true)}
              {anyRejected && cell(l.rejected, "text-danger", true)}
              {anyMissing && cell(l.not_received, "text-warning", true)}
              {cell(l.pending, l.closed ? "text-muted" : Number(l.pending?.units) > 0 ? "text-warning fw-medium" : "text-success", true)}
            </tr>
          ))}</tbody>
          {/* the quantity columns add up in units, so the end of the table can be checked against the supplier's
              own challan total; boxes cannot be added across items with different box sizes */}
          <tfoot><tr className="fw-semibold">
            <td />
            <td className="text-end">{lines.length} line{lines.length === 1 ? "" : "s"}</td>
            <td className="num">{qty(r.ordered_units)}</td>
            <td className="num text-success">{qty(r.received_units)}</td>
            {anyRejected && <td className="num text-danger">{qty(sumBy(lines, (l: any) => l.rejected?.units))}</td>}
            {anyMissing && <td className="num text-warning">{qty(sumBy(lines, (l: any) => l.not_received?.units))}</td>}
            <td className="num text-warning">{qty(r.pending_units)}</td>
          </tr></tfoot>
        </table>
      </div>
    </div>
  );
}

export function GrnsPage() {
  const { can } = usePerms();
  const cols: Column<any>[] = [{ key: "number", header: "GRN #", render: (g) => <span className="fw-semibold text-dark">{g.number}</span> }, { key: "date", header: "Date", render: (g) => date(g.date) }, { key: "supplier_name", header: "Supplier" }, { key: "po_number", header: "PO" }, { key: "warehouse_name", header: "Warehouse" }, { key: "supplier_challan_no", header: "Challan" }, { key: "total_value", header: "Value", num: true, render: (g) => money(g.total_value) }, { key: "billed", header: "Billed", render: (g) => (g.billed ? <span className="text-success">yes</span> : <span className="text-muted">no</span>) }, { key: "status", header: "Status", render: (g) => <StatusBadge status={g.status} /> }];
  return <ListPage page="grns" title="Goods receipts (GRN)" endpoint="/api/v1/goods-receipts" columns={cols} rowLink={(g) => `/app/purchasing/grns/${g.id}`} createTo="/app/purchasing/grns/new" createLabel="New GRN" defaultOrdering="-date" filters={[{ name: "status", label: "Status", type: "select", options: ["draft", "posted", "cancelled"].map((s) => ({ value: s, label: s })) }, supFilter, { name: "billed", label: "Billed", type: "select", options: [{ value: "true", label: "Billed" }, { value: "false", label: "Unbilled" }] }]} />;
}

export function GrnFormPage() {
  const [err, setErr] = useState<string | null>(null);
  const nav = useNavigate();
  const qc = useQueryClient();
  const { id } = useParams();
  const editing = Boolean(id);
  const [sp] = useSearchParams();
  const poId = sp.get("po");
  const warehouses = useWarehouses();
  const existing = useDoc("/api/v1/goods-receipts", id);
  const [seeded, setSeeded] = useState(false);
  const [form, setForm] = useState<any>({ warehouse: "", po: poId, date: new Date().toISOString().slice(0, 10), supplier_challan_no: "", notes: "", freight: "", freight_alloc: "by_qty" });
  // nothing is listed up front: rows appear as cartons are scanned — in a continuation too, where what the receipt
  // already holds is listed separately above and anything keyed here is *added* to it
  const [rows, setRows] = useState<ReceiveRow[]>([]);
  /** The receipt's own rows, opened for correction. `null` while they are read-only — the two paths never mix. */
  const [fixRows, setFixRows] = useState<ReceiveRow[] | null>(null);
  const [fixing, setFixing] = useState(false);
  const [scan, setScan] = useState("");
  const [scans, setScans] = useState(0);
  const [askExpiry, setAskExpiry] = useState<string | null>(null);
  const [addManual, setAddManual] = useState(false);
  const [confirming, setConfirming] = useState(false);
  /** The deliberate stop before stock moves: post, save without posting, or go back. */
  const [posting, setPosting] = useState(false);
  /** The supplier's challan is already on another receipt: their decision to make, so it is offered, never retried silently. */
  const [dup, setDup] = useState<{ detail: string; grn: string; number: string; post: boolean } | null>(null);
  const expiryRefs = useRef<Record<string, HTMLInputElement | null>>({});
  const scanRef = useRef<HTMLInputElement | null>(null);
  const po = useDoc("/api/v1/purchase-orders", form.po);
  // The order's receiving record: the receipt already open on it, and what every line has had against it so far.
  // Shares its key with the Receiving panel on the order, so moving between the two screens does not refetch.
  const recv = useQuery({ queryKey: ["po-receiving", form.po], queryFn: () => api.get(`/api/v1/purchase-orders/${form.po}/receiving`), enabled: Boolean(form.po) });
  // The open record itself, whichever screen we came in on: the document being corrected, or the one still open on
  // the order that this count will be added into. Editing, the key is the same as `existing` — one cached read.
  const heldId = editing ? id : recv.data?.open_receipt?.id || null;
  const held = useDoc("/api/v1/goods-receipts", heldId);
  const heldLines: any[] = held.data?.lines || [];

  /**
   * A continuation opens with an empty keying area. Only the header is seeded — seeded once, keyed on the document
   * and not on every refetch, so a background refresh cannot overwrite what the receiver is typing.
   *
   * The rows are deliberately *not* seeded: the storeman opens the receipt to key more of the delivery, and finding
   * the last product already in the list read as a correction. What is already counted is listed above instead.
   */
  useEffect(() => {
    const g = existing.data;
    if (!g || seeded) return;
    if (g.status !== "draft") {
      toast.error(g.status === "posted" ? `${g.number} is posted, so its stock is already in. Cancel it — that reverses the stock and the journal — then receive the delivery again.` : `${g.number} was ${g.status} and can no longer be corrected.`);
      nav(`/app/purchasing/grns/${g.id}`, { replace: true });
      return;
    }
    const freight = (g.landed_costs || []).find((lc: any) => lc.type === "freight");
    setForm({ warehouse: g.warehouse, po: g.po, date: g.date, supplier_challan_no: g.supplier_challan_no || "", notes: g.notes || "", freight: freight ? String(Number(freight.amount)) : "", freight_alloc: freight?.allocation || "by_qty" });
    setSeeded(true);
  }, [existing.data?.id]);

  // a receipt that cannot be read (deleted, or outside this user's warehouses) must not leave the page spinning
  if (editing && !seeded) return existing.isError ? <Alert kind="danger">{(existing.error as any)?.message || "This goods receipt could not be opened."}</Alert> : <Spinner />;
  if (form.po && po.isLoading && !rows.length) return <Spinner />;
  const orderLines: any[] = (po.data?.lines || []).filter((l: any) => !l.closed);
  const set = (key: string, patch: Partial<ReceiveRow>) => setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  const drop = (key: string) => setRows((rs) => rs.filter((r) => r.key !== key));
  const setFix = (key: string, patch: Partial<ReceiveRow>) => setFixRows((rs) => (rs || []).map((r) => (r.key === key ? { ...r, ...patch } : r)));
  const dropFix = (key: string) => setFixRows((rs) => (rs || []).filter((r) => r.key !== key));

  // Everything counted against the order but not yet posted: the rows on the open receipt and the rows keyed in
  // this sitting, which are two separate lists and are never the same row twice. `received` from the API counts
  // posted deliveries only, so these are what has to be added on top of it to say what a line is still owed.
  const unposted: Tally[] = [...heldLines.map(tallyOf), ...rows.map(tallyRow)];
  const sumOf = (list: Tally[], lineId?: string, variant?: string, pick: (t: Tally) => number = (t) => t.accepted) => list.filter((t) => (t.po_line ? t.po_line === lineId : t.variant === variant)).reduce((n, t) => n + pick(t), 0);
  const allRecvLines: any[] = recv.data?.lines || [];

  /** Where one order line stands — ordered, what posted deliveries brought, what is counted here, what is left. */
  const statFor = (lineId?: string, variant?: string, counted?: number): LineStat | null => {
    const rl = allRecvLines.find((l: any) => l.line_id === lineId) || allRecvLines.find((l: any) => l.variant === variant);
    if (!rl) return null;
    const ordered = Number(rl.ordered?.units || 0);
    const posted = Number(rl.received?.units || 0);
    const c = counted === undefined ? sumOf(unposted, rl.line_id, rl.variant) : counted;
    return { ordered, posted, counted: c, left: ordered - posted - c, packing: Number(rl.packing || 1) };
  };
  const rowStat = (r: ReceiveRow) => statFor(r.po_line, r.variant);
  // while a correction is open its own figures are what the line would stand at if it were saved
  const fixStat = (r: ReceiveRow) => statFor(r.po_line, r.variant, sumOf([...(fixRows || []).map(tallyRow), ...rows.map(tallyRow)], r.po_line, r.variant));

  /** Received so far for one order line, across the open receipt and this sitting both (a line can arrive in several batches). */
  const receivedFor = (lineId: string, variant?: string) => sumOf(unposted, lineId, variant);
  const rejectedFor = (lineId: string, variant?: string) => sumOf(unposted, lineId, variant, (t) => t.off);

  const addRow = (line: any, patch: Partial<ReceiveRow> = {}) => {
    const key = `${line.id}-${patch.batch_no || ""}-${Date.now()}`;
    const row: ReceiveRow = { ...receiveRow(line), key, ...patch };
    setRows((rs) => [...rs, row]);
    return row;
  };

  /**
   * One scan. The server reads the label: a unit barcode counts one, a box barcode or the item code printed on the
   * carton counts a whole box, and a label like `16418.01#195654` carries its batch too (item code before the dot,
   * batch after it). The item is matched against this order; the batch lands on the row and the expiry is asked
   * for, starting empty.
   */
  const onScan = async (code: string) => {
    code = code.trim();
    if (!code) return;
    setScan("");
    if (!form.po) return toast.error("Pick the purchase order first — the receipt is matched against it.");
    try {
      const hit = await api.get("/api/v1/barcodes/resolve", { code });
      const line = orderLines.find((l: any) => l.variant === hit.variant?.id);
      if (!line) {
        toast.error(`${hit.variant?.name || code} is not on ${po.data?.number || "this order"}.`);
        return;
      }
      const box = hit.unit === "box";
      const batch = hit.batch_no || "";
      // the same item in the same batch keeps adding to one row; a loose piece carries no batch of its own, so it
      // joins the row already open for that item — unless several batch rows are open, when there is no telling
      // which carton it came out of
      const sameItem = rows.filter((r) => r.variant === hit.variant?.id);
      const existing = sameItem.find((r) => (r.batch_no || "") === batch) || (!batch && sameItem.length === 1 ? sameItem[0] : undefined);
      const target = existing || addRow(line, { batch_no: batch, expiry_date: hit.expiry_date || "" });
      const per = target.packing > 1 ? target.packing : 1;
      const patch: Partial<ReceiveRow> = box
        ? { accepted_boxes: String(Number(target.accepted_boxes || 0) + 1) }
        : { accepted: String(Number(target.accepted || 0) + 1) };
      if (batch && !target.batch_no) patch.batch_no = batch;
      if (hit.expiry_date && !target.expiry_date) patch.expiry_date = hit.expiry_date;
      setRows((rs) => rs.map((r) => (r.key === target.key ? { ...r, ...patch } : r)));
      setScans((n) => n + 1);
      const needsExpiry = Boolean(batch) && !(hit.expiry_date || target.expiry_date);
      setAskExpiry(needsExpiry ? target.key : null);
      if (needsExpiry) setTimeout(() => expiryRefs.current[target.key]?.focus(), 60);
      // the storeman is holding the carton and wants to know whether that line is now complete, so the scan says
      // where the *line* stands and not only what the scan added. `unposted` is read before this scan lands in
      // state, so what it just counted is added on top of it rather than waiting for the next render.
      const s = statFor(line.id, hit.variant?.id, sumOf(unposted, line.id, hit.variant?.id) + (box ? per : 1));
      toast.success(`${hit.variant?.name}: +${box ? `1 box (${per})` : "1"}${batch ? ` · batch ${batch}` : ""}${s ? ` — ${boxes(s.counted, s.packing)} of ${boxes(s.ordered - s.posted, s.packing)} due, ${leftText(s)}` : ""}`);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  /**
   * Which warehouse the goods go into: what the receiver picked, else the one on the order, else the warehouse the
   * scanned product is kept in, else the default. The select shows it and can still be changed.
   */
  const productWh = rows.find((r) => r.default_warehouse)?.default_warehouse || "";
  const warehouseId = form.warehouse || po.data?.warehouse || productWh || warehouses.defaultId || "";
  const warehouseFrom = form.warehouse ? "" : po.data?.warehouse ? `from ${po.data.number}` : productWh ? "where the item is kept" : "default";

  const differences = orderLines
    .map((l: any) => ({ line: l, ordered: Number(l.qty_pending), received: receivedFor(l.id, l.variant), rejected: rejectedFor(l.id, l.variant) }))
    .map((x) => ({ ...x, diff: x.received - x.ordered }));
  const shortLines = differences.filter((x) => x.diff < 0);
  const overLines = differences.filter((x) => x.diff > 0);
  const sittingUnits = rows.reduce((n, r) => n + unitsOf(r, "accepted"), 0);
  const heldUnits = heldLines.reduce((n: number, l: any) => n + Number(l.qty_accepted || 0), 0);
  // what the receipt would hold once this sitting is added to it — the figure the progress bar is about
  const receiptUnits = heldUnits + sittingUnits;

  // How far through the pile this count is. A line counts as untouched when nothing is keyed against it here and
  // no posted delivery has covered it either — so a half-done count reads half-done instead of looking finished.
  const recvLines: any[] = allRecvLines.filter((l: any) => !l.closed);
  const blankLines = recvLines.filter((l) => !Number(l.received?.units) && !sumOf(unposted, l.line_id, l.variant));
  const dueUnits = Number(recv.data?.pending_units || 0);
  // in a correction the open receipt *is* this one, so there is nothing to send the receiver off to
  const openReceipt = !editing && recv.data?.open_receipt ? recv.data.open_receipt : null;
  const g0 = existing.data;
  const freightWas = Number((g0?.landed_costs || []).find((lc: any) => lc.type === "freight")?.amount || 0);
  // the header is saved on its own, so a continuation that changes nothing but the rows sends no header at all
  const headerChanged = Boolean(editing && g0) && (form.date !== g0.date || (form.supplier_challan_no || "") !== (g0.supplier_challan_no || "") || (form.notes || "") !== (g0.notes || "") || warehouseId !== g0.warehouse || Number(form.freight || 0) !== freightWas);
  const anyOff = heldLines.some((l: any) => Number(l.qty_damaged) > 0 || Number(l.qty_short) > 0);
  const headerBody = () => ({ warehouse: warehouseId, date: form.date, supplier_challan_no: form.supplier_challan_no, notes: form.notes, landed_costs: Number(form.freight) > 0 ? [{ type: "freight", description: "Freight", amount: form.freight, allocation: form.freight_alloc }] : [] });

  /** Open the receipt's own rows for correction: the whole set goes back, which is what makes a mistyped quantity fixable. */
  const startFix = () => {
    setErr(null);
    // Split each base-unit quantity back into boxes + loose so a carton-received item shows as boxes to correct,
    // not the whole figure dumped in the loose field. `unitsOf` recombines boxes*packing + loose, so this
    // round-trips exactly: 120 of a 12-pack -> 10 boxes, 0 loose.
    const split = (total: number, packing: number) => {
      const per = packing > 1 ? packing : 1;
      return per > 1 ? { boxes: String(Math.floor(total / per)), loose: String(total % per) } : { boxes: "0", loose: String(total) };
    };
    setFixRows(heldLines.map((l: any) => {
      const packing = Number(l.packing || 1);
      const a = split(Number(l.qty_accepted), packing), d = split(Number(l.qty_damaged), packing), m = split(Number(l.qty_short), packing);
      return {
        key: l.id, po_line: l.po_line || undefined, variant: l.variant, name: l.variant_name, sku: l.variant_sku, item_code: l.item_code || "", upc: "", packing, ordered: 0, pending: 0, unit_price: l.unit_cost,
        accepted: a.loose, accepted_boxes: a.boxes, rejected: d.loose, rejected_boxes: d.boxes, missing: m.loose, missing_boxes: m.boxes,
        reject_reason: l.reject_reason || "", note: l.note || "", close_line: false, batch_no: l.batch_no || "", expiry_date: l.expiry_date || "", bin: l.bin || null, default_bin_name: l.bin_name || "", default_warehouse: "", needs_expiry: Boolean(l.is_expiry_tracked),
      };
    }));
  };

  /**
   * Save a correction. This is the *replacing* path — `PATCH` rewrites the whole line set — so it sends every row
   * that is to remain on the receipt, corrected. Keying more goes the other way (see `submit`): sending a
   * correction as an addition would double the quantity it was meant to fix.
   */
  const saveFix = async () => {
    const lines = (fixRows || []).map((r) => ({ r, accepted: unitsOf(r, "accepted"), rejected: unitsOf(r, "rejected"), missing: unitsOf(r, "missing") })).filter((x) => x.accepted > 0 || x.rejected > 0 || x.missing > 0);
    if (!lines.length) return toast.error(`A receipt cannot be emptied here — correcting replaces its rows, so leaving none would wipe ${g0?.number}. Cancel the receipt instead.`);
    const needsReason = lines.find((x) => (x.rejected > 0 || x.missing > 0) && !x.r.reject_reason.trim());
    if (needsReason) return toast.error(`Give a reason for the rejected / missing ${needsReason.r.name}.`);
    setErr(null);
    setFixing(true);
    try {
      await api.patch(`/api/v1/goods-receipts/${id}`, { version: g0.version, lines: lines.map((x) => rowPayload(x.r)) });
      qc.invalidateQueries({ queryKey: ["doc", "/api/v1/goods-receipts", id] });
      qc.invalidateQueries({ queryKey: ["po-receiving", form.po] });
      setFixRows(null);
      toast.success(`${g0?.number} corrected`);
    } catch (e: any) {
      const msg = e.problem?.type === "conflict" ? `${g0?.number || "This receipt"} was changed by someone else while you had it open. Reload the page and make the correction again.` : e.message;
      setErr(msg);
      toast.error(msg);
    } finally {
      setFixing(false);
    }
  };

  /**
   * Save what was keyed in this sitting. This is the *adding* path: `POST` with the same order fills the receipt
   * already open on it, so five hundred counted before the break and five hundred after end up as one receipt of
   * a thousand. The replacing path is only ever used by `saveFix` and by the header, which carries no rows.
   */
  const submit = async (post: boolean, confirmChallan = false) => {
    setErr(null);
    setDup(null);
    const lines = rows
      .map((r) => ({ r, accepted: unitsOf(r, "accepted"), rejected: unitsOf(r, "rejected"), missing: unitsOf(r, "missing") }))
      .filter((x) => x.accepted > 0 || x.rejected > 0 || x.missing > 0);
    if (!form.po && !editing) return toast.error("Pick the purchase order this delivery is against.");
    if (!warehouseId) return toast.error("Pick the warehouse the goods went into.");
    if (!lines.length && !editing) return toast.error("Scan what arrived, or add a line by hand.");
    // nothing keyed and nothing changed: saying "saved" would claim a write that never happened. Posting with an
    // empty keying area is the one legitimate case — it posts what the receipt already holds.
    if (!lines.length && editing && !headerChanged && !post) return toast.error(`Nothing new was keyed, so ${g0?.number} is unchanged. Scan what has arrived, or use "Correct a quantity" to change what is already counted.`);
    if (fixRows) return toast.error("Save or cancel the correction above first — it rewrites the rows this would be added to.");
    // Posting moves stock, and the server refuses a flagged line with no date. Say so here instead, naming the
    // item — a draft can still be saved and finished when someone has the box in hand.
    if (post) {
      const missingDate = lines.filter((x) => x.r.needs_expiry && !x.r.expiry_date && x.accepted > 0);
      if (missingDate.length) {
        return toast.error(`${missingDate.length === 1 ? missingDate[0].r.name : `${missingDate.length} items`} need an expiry date before this receipt can be posted. Save it as a draft if you have to check the carton.`);
      }
      // a row already on the receipt can be missing its date too, and it is posted along with the rest
      const heldNoDate = heldLines.filter((l: any) => l.is_expiry_tracked && !l.expiry_date && Number(l.qty_accepted) > 0);
      if (heldNoDate.length) {
        return toast.error(`${heldNoDate.length === 1 ? heldNoDate[0].variant_name : `${heldNoDate.length} items`} already on this receipt need an expiry date. Fix the row above, then post.`);
      }
    }
    const needsReason = lines.find((x) => (x.rejected > 0 || x.missing > 0) && !x.r.reject_reason.trim());
    if (needsReason) return toast.error(`Give a reason for the rejected / missing ${needsReason.r.name}.`);
    try {
      const body: any = { warehouse: warehouseId, po: form.po, date: form.date, supplier_challan_no: form.supplier_challan_no, notes: form.notes, post, lines: lines.map((x) => rowPayload(x.r)) };
      if (confirmChallan) body.confirm_duplicate_challan = true;
      if (Number(form.freight) > 0) body.landed_costs = [{ type: "freight", description: "Freight", amount: form.freight, allocation: form.freight_alloc }];
      if (editing) {
        // The header is corrected in place: a PATCH carrying no `lines` cannot touch the rows, and it is the only
        // way to *clear* a challan or the freight. The rows then go through the adding path, and posting is the
        // receipt's own action — only create honours a `post` flag.
        if (headerChanged) await api.patch(`/api/v1/goods-receipts/${id}`, { ...headerBody(), version: g0.version });
        if (lines.length) {
          // No order behind the receipt means no open record for the server to find — it would start a second
          // receipt. One without an order is added to by sending what it holds plus what was just keyed.
          if (form.po) await api.post("/api/v1/goods-receipts", { ...body, post: false }, true);
          else await api.patch(`/api/v1/goods-receipts/${id}`, { lines: [...heldLines.map(heldPayload), ...lines.map((x) => rowPayload(x.r))] });
        }
        if (post) await api.post(`/api/v1/goods-receipts/${id}/post`, {});
        qc.invalidateQueries({ queryKey: ["doc", "/api/v1/goods-receipts", id] });
        qc.invalidateQueries({ queryKey: ["po-receiving", form.po] });
        toast.success(post ? "Received — stock is in" : lines.length ? `${qty(sittingUnits)} unit(s) added to ${g0?.number}` : "Saved");
        return nav(`/app/purchasing/grns/${id}`);
      }
      const r = await api.post("/api/v1/goods-receipts", body, true);
      qc.invalidateQueries({ queryKey: ["po-receiving", form.po] });
      // one order keeps one open receipt, and a second submission now *adds* to it rather than replacing what is
      // there — so the message says which receipt this joined instead of announcing one that was never created
      toast.success(r.reused ? `Added to ${r.number}, which was already open for this order` : post ? "Received — stock is in" : "Saved as a draft");
      nav(`/app/purchasing/grns/${r.id}`);
    } catch (e: any) {
      // the same challan on a *new* receipt means one delivery is being keyed twice. `describeProblem` would read
      // out the ids the server sends back, so the receiver is shown the sentence that names the earlier receipt —
      // minus its closing note to a programmer, which the "record it anyway" button says in words instead.
      if (e.problem?.type === "duplicate_challan") {
        setDup({ detail: String(e.problem.detail || "").split("If the supplier has reused")[0].trim(), grn: e.problem.errors?.grn?.[0] || "", number: e.problem.errors?.number?.[0] || "", post });
        return toast.error("That challan number is already on another receipt.");
      }
      // a stale version means someone else moved the document on; sending the same body again would bury their edit
      const msg = e.problem?.type === "conflict" ? `${g0?.number || "This receipt"} was changed by someone else while you had it open. Reload the page and make the correction again.` : e.message;
      setErr(msg); toast.error(msg);
    }
  };

  return (
    <>
      <PageHeader title={editing ? `Continue ${g0?.number}` : po.data ? `Receive ${po.data.number}` : "Receive goods"} crumbs={editing ? [{ label: "GRNs", to: "/app/purchasing/grns" }, { label: g0?.number, to: `/app/purchasing/grns/${id}` }, { label: "Continue" }] : [{ label: "Purchase orders", to: "/app/purchasing/orders" }, { label: po.data ? po.data.number : "Receipt" }, { label: "Receive" }]} />
      <SaveError error={err} what="goods receipt" />

      {/* Resuming is the route, not starting again: anything keyed here lands in the receipt already open on the
          order, so the receiver has to be told it exists before they count five hundred items into a blank form. */}
      {openReceipt && (
        <Alert kind="warning" icon="ti-progress-check">
          <div className="d-flex flex-wrap align-items-center gap-2">
            <div className="flex-grow-1">
              <strong>{openReceipt.number} is already open on {po.data?.number || "this order"}</strong> — {qty(openReceipt.items)} item(s) · {qty(openReceipt.units)} unit(s) keyed, started {date(openReceipt.date)}{openReceipt.received_by ? ` by ${openReceipt.received_by}` : ""}.
              <div className="fs-12">One order keeps one receipt open, so whatever you key here is <strong>added</strong> to it. Open it first to see what is already counted.</div>
            </div>
            <Link to={`/app/purchasing/grns/${openReceipt.id}/edit`} className="btn btn-primary"><i className="ti ti-pencil me-1" />Continue {openReceipt.number}</Link>
          </div>
        </Alert>
      )}

      {dup && (
        <Alert kind="danger" icon="ti-copy">
          <div className="d-flex flex-wrap align-items-center gap-2">
            <div className="flex-grow-1">
              {dup.detail}{dup.grn && <> <Link to={`/app/purchasing/grns/${dup.grn}`}>Open {dup.number || "that receipt"}</Link>.</>}
              <div className="fs-12">Record it anyway only if the supplier really has printed the same number on two separate deliveries.</div>
            </div>
            <button className="btn btn-light" onClick={() => setDup(null)}>Change the challan</button>
            <button className="btn btn-outline-danger" onClick={() => submit(dup.post, true)}>Record it anyway</button>
          </div>
        </Alert>
      )}

      <div className="card mb-3"><div className="card-body"><div className="row">
        <Field label="Purchase order *" col="col-md-5" hint={editing ? `from ${po.data?.supplier_name || "the supplier"} · fixed once the receipt is saved` : po.data ? `from ${po.data.supplier_name}` : "type a number, supplier, reference or item code"}>
          <EntitySelect
            endpoint="/api/v1/purchase-orders"
            query={{ status__in: "sent,partially_received", ordering: "-date" }}
            value={form.po}
            onChange={(v) => { setForm({ ...form, po: v, warehouse: "" }); setRows([]); }}
            mapOption={(p) => ({
              id: p.id,
              label: p.number,
              // the picker only lists orders you can still receive, so say which supplier and what is left
              sub: [p.supplier_name, p.reference, `${qty(p.units_due)} units due`].filter(Boolean).join(" · "),
            })}
            placeholder="search an approved order"
            disabled={editing}
          />
        </Field>
        <Field label="Warehouse" col="col-md-2" hint={warehouseFrom}><select className="form-select" value={warehouseId} onChange={(e) => setForm({ ...form, warehouse: e.target.value })}>{warehouses.rows.map((w: any) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
        <Field label="Date" col="col-md-2"><DateInput className="form-control" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} /></Field>
        <Field label={<><i className="ti ti-file-text me-1 text-primary" /><strong>Supplier challan #</strong></>} col="col-md-3" hint="The number on the supplier's own delivery note. It is what tells a second delivery apart from the same one keyed twice, so a new receipt reusing it is refused. Leave it blank only if the note carries no number."><input className="form-control" placeholder="e.g. DC-501348" value={form.supplier_challan_no} onChange={(e) => { setForm({ ...form, supplier_challan_no: e.target.value }); setDup(null); }} /></Field>
      </div></div></div>

      {/* What is already counted, kept out of the keying area and above it: the storeman reads down the page —
          this is on the receipt, below is what I am adding. Correcting happens here, where the rows are, because
          it is the one action that sends the whole set back; the scan box below only ever adds. */}
      {editing && heldLines.length > 0 && (
        <div className="card mb-3 border-secondary">
          <div className="card-header py-2 d-flex flex-wrap justify-content-between align-items-center gap-2">
            <div>
              <strong className="fs-13"><i className="ti ti-list-check me-1" />Already on {g0?.number}</strong>
              <span className="text-muted fs-12 ms-2">{heldLines.length} item(s) · {qty(heldUnits)} unit(s) counted, not yet posted{g0?.supplier_challan_no ? ` · challan ${g0.supplier_challan_no}` : ""}{g0?.received_by_name ? ` · by ${g0.received_by_name}` : ""}</span>
            </div>
            {fixRows
              ? <div className="d-flex gap-2"><button className="btn btn-sm btn-light" onClick={() => { setFixRows(null); setErr(null); }}>Leave as it is</button><button className="btn btn-sm btn-secondary" disabled={fixing} onClick={saveFix}><i className="ti ti-device-floppy me-1" />Save corrections</button></div>
              : <button className="btn btn-sm btn-outline-primary" onClick={startFix} title="Change what was already counted on this receipt — box and loose quantities, batch or expiry — before it is posted"><i className="ti ti-pencil me-1" />Edit received quantities</button>}
          </div>
          {fixRows ? (
            <>
              <div className="px-3 pt-2 fs-11 text-muted">Type the <strong>correct</strong> box and loose amounts for each item — this replaces what was counted before, so enter the right total, not the difference. Scanning below <strong>adds</strong> instead.</div>
              <ReceiveRowsTable rows={fixRows} set={setFix} drop={dropFix} warehouse={warehouseId} stat={fixStat} />
            </>
          ) : (
            <div className={`card-body p-0 ${heldLines.length > LINES_SCROLL_FROM ? "table-scroll" : "table-responsive"}`}>
              <table className="table table-sm fs-12 mb-0">
                <thead className="table-light"><tr><th>Item</th><th className="num">Received</th>{anyOff && <th className="num">Rejected</th>}{anyOff && <th className="num">Not received</th>}<th>Batch / expiry</th><th>Placed in</th><th>Note</th></tr></thead>
                <tbody>{heldLines.map((l: any) => (
                  <tr key={l.id}>
                    <td>{l.variant_name}<div className="text-muted fs-11 font-monospace">{l.item_code || l.variant_sku}{l.packing > 1 ? ` · ${l.packing}/box` : ""}</div></td>
                    <td className="num fw-medium">{boxes(l.qty_accepted, l.packing)}{l.packing > 1 && Number(l.qty_accepted) > 0 && <div className="text-muted fs-11">{qty(l.qty_accepted)} units</div>}</td>
                    {anyOff && <td className="num text-danger">{Number(l.qty_damaged) ? boxes(l.qty_damaged, l.packing) : <span className="text-muted">—</span>}</td>}
                    {anyOff && <td className="num text-warning">{Number(l.qty_short) ? boxes(l.qty_short, l.packing) : <span className="text-muted">—</span>}</td>}
                    <td>{l.batch_no || <span className="text-muted">—</span>}{l.expiry_date ? <div className="fs-11 text-muted">exp {date(l.expiry_date)}</div> : l.is_expiry_tracked && Number(l.qty_accepted) > 0 ? <div className="fs-11 text-warning">expiry needed</div> : null}</td>
                    <td className="fs-11">{l.bin_name || <span className="text-muted">anywhere</span>}</td>
                    <td className="fs-11">{l.note || l.reject_reason || <span className="text-muted">—</span>}</td>
                  </tr>
                ))}</tbody>
                <tfoot><tr className="fw-semibold"><td className="text-end">{heldLines.length} line{heldLines.length === 1 ? "" : "s"}</td><td className="num">{qty(heldUnits)}</td>{anyOff && <td className="num text-danger">{qty(sumBy(heldLines, (l: any) => l.qty_damaged))}</td>}{anyOff && <td className="num text-warning">{qty(sumBy(heldLines, (l: any) => l.qty_short))}</td>}<td colSpan={3} className="text-muted fs-11">units · posting is what moves this stock in</td></tr></tfoot>
              </table>
            </div>
          )}
        </div>
      )}

      <div className="card mb-3 border-primary"><div className="card-body">
        <div className="d-flex flex-wrap align-items-end gap-3">
          <div className="flex-grow-1" style={{ minWidth: 280 }}>
            {/* the banner at the top scrolls away, and this is where the counting actually happens */}
            <label className="form-label fw-semibold mb-1"><i className="ti ti-barcode me-1 text-primary" />Scan each carton{(openReceipt || editing) && <span className="badge bg-warning-light text-warning border border-warning fs-11 ms-2">adds to {openReceipt?.number || g0?.number}</span>}</label>
            <input ref={scanRef} className="form-control form-control-lg" placeholder={form.po ? "Scan a barcode or the item code…" : "Pick the purchase order above first"} value={scan} autoFocus disabled={!form.po} onChange={(e) => setScan(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); onScan(scan); } }} />
          </div>
          <div className="text-center px-3"><div className="fs-20 fw-bold text-primary">{scans}</div><div className="fs-11 text-muted">scans</div></div>
          <div className="text-center px-3"><div className="fs-20 fw-bold">{rows.length}</div><div className="fs-11 text-muted">items keyed now</div></div>
          <div className="text-center px-3"><div className="fs-20 fw-bold">{qty(sittingUnits)}</div><div className="fs-11 text-muted">units keyed now</div></div>
          {editing && <div className="text-center px-3" title="What the receipt will hold once this sitting is added to it"><div className="fs-20 fw-bold text-secondary">{qty(receiptUnits)}</div><div className="fs-11 text-muted">on the receipt</div></div>}
          {form.po && recvLines.length > 0 && <div className="text-center px-3" title="Order lines with nothing keyed against them and nothing received on an earlier delivery"><div className={`fs-20 fw-bold ${blankLines.length ? "text-warning" : "text-success"}`}>{qty(blankLines.length)}</div><div className="fs-11 text-muted">lines untouched</div></div>}
          <button className="btn btn-outline-secondary" disabled={!form.po} onClick={() => setAddManual(true)}><i className="ti ti-plus me-1" />Add by hand</button>
        </div>
        {/* against the pile in front of them: a count that is a tenth of the way through has to look it */}
        {form.po && dueUnits > 0 && (
          <div className="mt-2">
            <div className="progress" style={{ height: 6 }}><div className={`progress-bar ${receiptUnits >= dueUnits ? "bg-success" : "bg-warning"}`} style={{ width: `${Math.min(100, (receiptUnits / dueUnits) * 100)}%` }} /></div>
            <div className="fs-11 text-muted mt-1">{qty(receiptUnits)} of the {qty(dueUnits)} unit(s) still owed on {po.data?.number || "the order"} are on this receipt · {blankLines.length ? `${qty(blankLines.length)} of ${qty(recvLines.length)} order line(s) have nothing on them yet` : `all ${qty(recvLines.length)} order line(s) have something on them`}</div>
          </div>
        )}
        <div className="fs-12 text-muted mt-2">
          A <strong>unit</strong> barcode adds 1 · a <strong>box</strong> barcode or the <strong>item code</strong> adds a whole box · a label like <code>16418.01#195654</code> also fills the batch — item code before the dot, batch after it. Batch, expiry and the note are all optional.
        </div>
      </div></div>

      {rows.length > 0 && (
        <div className="card mb-3"><div className="card-header py-2"><strong className="fs-13">{editing ? "Keyed now" : "Scanned"}</strong><span className="text-muted fs-12 ms-2">{editing ? `adjust anything that needs it — saving adds these to ${g0?.number}` : "adjust anything that needs it"}</span></div>
          <ReceiveRowsTable rows={rows} set={set} drop={drop} warehouse={form.warehouse} askExpiry={askExpiry} setAskExpiry={setAskExpiry} expiryRefs={expiryRefs} onExpiryDone={() => scanRef.current?.focus()} stat={rowStat} />
        </div>
      )}

      {form.po && orderLines.length > 0 && (
        <div className="card mb-3"><div className="card-header py-2 d-flex flex-wrap justify-content-between align-items-center">
          <strong className="fs-13">Against {po.data?.number}</strong>
          <span className="fs-12">
            {(() => { const done = differences.filter((d) => d.diff === 0 && d.received > 0).length; return done > 0 ? <span className="text-success me-3"><i className="ti ti-circle-check me-1" />{done} item(s) fully received</span> : null; })()}
                  {shortLines.length === 0 && overLines.length === 0 ? <span className="text-success"><i className="ti ti-circle-check me-1" />everything on the order is accounted for</span> : (
              <>
                {shortLines.length > 0 && <span className="text-warning me-3">{shortLines.length} item(s) short</span>}
                {overLines.length > 0 && <span className="text-danger">{overLines.length} item(s) over</span>}
              </>
            )}
          </span>
        </div>
          <div className={`card-body p-0 ${differences.length > LINES_SCROLL_FROM ? "table-scroll" : "table-responsive"}`}>
            <table className="table table-sm mb-0 fs-12">
              <thead className="table-light"><tr><th>Item</th><th className="num">Ordered (due)</th><th className="num">On this receipt</th><th className="num">Rejected / missing</th><th className="num">Difference</th></tr></thead>
              <tbody>
                {differences.filter(({ received, diff }) => !(diff === 0 && received > 0)).map(({ line, ordered, received, rejected, diff }) => (
                  <tr key={line.id} className={diff > 0 ? "table-danger" : received === 0 ? "" : diff < 0 ? "table-warning" : ""}>
                    <td>{line.variant_name}<div className="text-muted fs-11 font-monospace">{line.item_code || line.variant_sku}{line.packing > 1 ? ` · ${line.packing}/box` : ""}</div></td>
                    <td className="num">{boxes(ordered, line.packing)}</td>
                    <td className="num fw-medium">{boxes(received, line.packing)}</td>
                    <td className="num text-muted">{rejected ? boxes(rejected, line.packing) : "—"}</td>
                    <td className={`num ${diff === 0 ? "text-success" : diff > 0 ? "text-danger fw-bold" : "text-warning fw-semibold"}`}>
                      {diff === 0 ? "none" : `${diff > 0 ? "+" : ""}${boxes(diff, line.packing)}`}
                      {diff < 0 && received === 0 && <div className="fs-11">nothing counted</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="card mb-3"><div className="card-body py-2"><div className="row">
        <Field label="Freight / landed cost" col="col-md-3" hint="spread over the received lines"><input type="number" step="0.01" className="form-control" value={form.freight} onChange={(e) => setForm({ ...form, freight: e.target.value })} /></Field>
        <Field label="Notes" col="col-md-9"><input className="form-control" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
      </div></div></div>

      <div className="d-flex flex-wrap gap-2 justify-content-end align-items-center">
        <span className="fs-12 text-muted me-auto">{rows.length} item(s) · {qty(sittingUnits)} unit(s) · {scans} scan(s){editing ? ` keyed now · ${qty(receiptUnits)} unit(s) on ${g0?.number} after saving` : ""}{form.po && recvLines.length > 0 ? ` · ${qty(blankLines.length)} of ${qty(recvLines.length)} order line(s) with nothing on them` : ""}</span>
        <button className="btn btn-light" onClick={() => nav(-1)}>Cancel</button>
        <button className="btn btn-outline-primary" onClick={() => submit(false)}>{editing ? (rows.length ? `Add to ${g0?.number}` : "Save changes") : "Save as draft"}</button>
        {/* Posting moves the stock and writes the journal, and it cannot be undone except by cancelling the whole
            receipt — so it is not offered until the receipt exists as a saved draft. Save first, check what is on
            it, then post it. */}
        {editing
          ? <button className="btn btn-success" onClick={() => setPosting(true)}><i className="ti ti-check me-1" />Post receipt</button>
          : <span className="fs-12 text-muted">Save it first — posting puts the stock in, and is the step after</span>}
      </div>

      <Modal open={addManual} onClose={() => setAddManual(false)} title="Add a line by hand" size="md"
        footer={<button className="btn btn-light" onClick={() => setAddManual(false)}>Close</button>}>
        <p className="fs-13 text-muted">For a carton whose barcode will not read. Pick the item from the order and type the quantity in the table.</p>
        <div className="list-group">
          {orderLines.map((l: any) => {
            const s = statFor(l.id, l.variant);
            return (
              <button key={l.id} className="list-group-item list-group-item-action d-flex justify-content-between align-items-center" onClick={() => { addRow(l); setAddManual(false); }}>
                <span>{l.variant_name}<div className="text-muted fs-11 font-monospace">{l.item_code || l.variant_sku}</div></span>
                <span className="fs-12 text-muted">due {boxes(l.qty_pending, l.packing)}{s && s.counted > 0 ? ` · ${boxes(s.counted, s.packing)} counted, ${leftText(s)}` : ""}</span>
              </button>
            );
          })}
          {!orderLines.length && <div className="list-group-item text-muted">Pick the purchase order first.</div>}
        </div>
      </Modal>

      <Modal open={posting} onClose={() => setPosting(false)} title={`Post ${g0?.number || "this receipt"}?`} size="md"
        footer={<>
          <button className="btn btn-light" onClick={() => setPosting(false)}>Cancel</button>
          <button className="btn btn-outline-primary" onClick={() => { setPosting(false); submit(false); }}>
            {rows.length ? "Just save, do not post" : "Leave it as a draft"}
          </button>
          <button className="btn btn-success" onClick={() => { setPosting(false); if (shortLines.length || overLines.length) setConfirming(true); else submit(true); }}>
            <i className="ti ti-check me-1" />Post it
          </button>
        </>}>
        <p className="fs-13 mb-2">
          This puts <strong>{qty(receiptUnits)} unit(s)</strong> into <strong>{warehouses.data?.find((w: any) => w.id === warehouseId)?.name || "the warehouse"}</strong> and
          writes the journal entry for them. {rows.length > 0 && <>The {qty(sittingUnits)} unit(s) keyed here are added first. </>}
        </p>
        <p className="fs-13 text-muted mb-0">
          A posted receipt cannot be edited. Correcting one afterwards means cancelling it — which reverses the
          stock and the journal — and receiving the delivery again. If the count is not finished, leave it as a
          draft and come back to it.
        </p>
      </Modal>

      <Modal open={confirming} onClose={() => setConfirming(false)} title="The receipt does not match the order" size="md"
        footer={<><button className="btn btn-light" onClick={() => setConfirming(false)}>Back to scanning</button><button className="btn btn-success" onClick={() => { setConfirming(false); submit(true); }}>Confirm anyway</button></>}>
        {overLines.length > 0 && (
          <>
            <p className="fs-13 mb-1 text-danger"><strong>More than ordered:</strong></p>
            <ul className="fs-13">{overLines.map((x) => <li key={x.line.id}>{x.line.variant_name}: {boxes(x.received, x.line.packing)} against {boxes(x.ordered, x.line.packing)} due</li>)}</ul>
          </>
        )}
        {shortLines.length > 0 && (
          <>
            <p className="fs-13 mb-1 text-warning"><strong>Short of the order:</strong></p>
            <ul className="fs-13">{shortLines.map((x) => <li key={x.line.id}>{x.line.variant_name}: {boxes(x.received, x.line.packing)} of {boxes(x.ordered, x.line.packing)}{x.received === 0 ? " (nothing counted)" : ""}</li>)}</ul>
            <div className="form-check">
              <input className="form-check-input" type="checkbox" id="close-short-all" onChange={(e) => setRows((rs) => rs.map((r) => ({ ...r, close_line: e.target.checked })))} />
              <label className="form-check-label fs-13" htmlFor="close-short-all">Don't expect the rest — close these order lines</label>
            </div>
            <div className="fs-11 text-muted mt-1">Leave it unticked and the balance stays on the order for a later delivery.</div>
          </>
        )}
      </Modal>
    </>
  );
}

type ReceiveRow = {
  key: string; po_line?: string; variant: string; name: string; sku: string; item_code: string; upc: string;
  packing: number; ordered: number; pending: number; unit_price: string;
  accepted: string; accepted_boxes: string; rejected: string; rejected_boxes: string; missing: string; missing_boxes: string;
  reject_reason: string; note: string; close_line: boolean; batch_no: string; expiry_date: string; bin: string | null; default_bin_name: string; default_warehouse: string;
  /** an expiry-tracked product cannot be posted without a date — the field is required, not optional, for it */
  needs_expiry?: boolean;
};

/** Units on a row: the box count times the box size, plus the loose pieces typed beside it. */
const unitsOf = (r: ReceiveRow, field: "accepted" | "rejected" | "missing") => Number(r[`${field}_boxes`] || 0) * (r.packing > 1 ? r.packing : 1) + Number(r[field] || 0);

/** One row flattened to what the per-line arithmetic needs, whichever list it came out of. */
type Tally = { po_line?: string; variant: string; accepted: number; off: number };
const tallyRow = (r: ReceiveRow): Tally => ({ po_line: r.po_line, variant: r.variant, accepted: unitsOf(r, "accepted"), off: unitsOf(r, "rejected") + unitsOf(r, "missing") });
const tallyOf = (l: any): Tally => ({ po_line: l.po_line || undefined, variant: l.variant, accepted: Number(l.qty_accepted || 0), off: Number(l.qty_damaged || 0) + Number(l.qty_short || 0) });

/** Where an order line stands: `counted` is what this unposted receipt holds, on top of what posted deliveries brought. */
type LineStat = { ordered: number; posted: number; counted: number; left: number; packing: number };

/** The answer to "is this line done?", which is what the storeman holding the carton is asking. */
const leftText = (s: LineStat) => (s.left > 0 ? `${boxes(s.left, s.packing)} still to come` : s.left === 0 ? "line complete" : `${boxes(-s.left, s.packing)} over`);

/** A row as the API takes it: quantities in base units, with the boxes column already folded in by `unitsOf`. */
const rowPayload = (r: ReceiveRow) => ({
  po_line: r.po_line || null, variant: r.variant, qty_accepted: unitsOf(r, "accepted"), qty_damaged: unitsOf(r, "rejected"), qty_short: unitsOf(r, "missing"),
  reject_reason: r.reject_reason || "", note: r.note || "", close_line: r.close_line, bin: r.bin || null,
  batch_no: r.batch_no || "", expiry_date: r.expiry_date || null, unit_cost: r.unit_price || null,
});

/** A row the receipt already holds, sent back unchanged — a replacing save has to carry what it is keeping. */
const heldPayload = (l: any) => ({
  po_line: l.po_line || null, variant: l.variant, qty_accepted: l.qty_accepted, qty_damaged: l.qty_damaged, qty_short: l.qty_short,
  reject_reason: l.reject_reason || "", note: l.note || "", bin: l.bin || null, batch_no: l.batch_no || "", expiry_date: l.expiry_date || null, unit_cost: l.unit_cost,
});

/** A scanned (or hand-picked) order line, with nothing filled in until the receiver says what arrived. */
function receiveRow(l: any): ReceiveRow {
  return {
    key: l.id, po_line: l.id, variant: l.variant, name: l.variant_name, sku: l.variant_sku,
    item_code: l.item_code || "", upc: l.upc || "", packing: Number(l.packing || 1),
    ordered: Number(l.qty_ordered || 0), pending: Number(l.qty_pending || 0), unit_price: l.unit_price,
    accepted: "0", accepted_boxes: "0", rejected: "0", rejected_boxes: "0", missing: "0", missing_boxes: "0",
    reject_reason: "", note: "", close_line: false, batch_no: "", expiry_date: "", bin: l.default_bin || null, default_bin_name: l.default_bin_name || "",
    needs_expiry: Boolean(l.is_expiry_tracked),
    default_warehouse: l.default_warehouse || "",
  };
}

/**
 * The rows of a count, everything on them editable. Used twice on the receiving screen, which is the point of it
 * being one table: the keying area, where what is typed is *added* to the receipt, and the correction of the rows
 * the receipt already holds, which *replaces* them. A quantity is typed the same way whichever one it is; what
 * differs is the button it is saved with, so the two cards say which they are.
 */
function ReceiveRowsTable({ rows, set, drop, warehouse, askExpiry, setAskExpiry, expiryRefs, onExpiryDone, stat }: {
  rows: ReceiveRow[]; set: (key: string, patch: Partial<ReceiveRow>) => void; drop: (key: string) => void; warehouse?: string;
  askExpiry?: string | null; setAskExpiry?: (k: string | null) => void; expiryRefs?: React.MutableRefObject<Record<string, HTMLInputElement | null>>; onExpiryDone?: () => void;
  stat?: (r: ReceiveRow) => LineStat | null;
}) {
  // the order's own figures are only there when the receipt is against one, so the column comes and goes with them
  const showStat = Boolean(stat) && rows.some((r) => stat!(r));
  return (
    <div className={`card-body p-0 ${rows.length > LINES_SCROLL_FROM ? "table-scroll" : "table-responsive"}`}>
      <table className="table table-sm align-middle mb-0 fs-12">
        <thead className="table-light">
          <tr>
            <th style={{ minWidth: 190 }}>Item</th>
            <th className="text-center" style={{ width: 140 }}>Received<div className="fs-11 fw-normal text-muted">box + loose</div></th>
            {showStat && <th style={{ minWidth: 150 }}>On this order line<div className="fs-11 fw-normal text-muted">counted · still to come</div></th>}
            <th className="text-center" style={{ width: 96 }}>Rejected</th>
            <th className="text-center" style={{ width: 96 }}>Not received</th>
            <th style={{ minWidth: 140 }}>Reason</th>
            <th style={{ minWidth: 140 }}>Batch / expiry<div className="fs-11 fw-normal text-muted">{rows.some((r) => r.needs_expiry) ? "expiry required where marked" : "both optional"}</div></th>
            <th style={{ minWidth: 120 }}>Note</th>
            <th style={{ minWidth: 140 }}>Placed in</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const s = stat ? stat(r) : null;
            return (
              <tr key={r.key}>
                <td>
                  <div className="fw-medium">{r.name}</div>
                  <div className="text-muted fs-11 font-monospace">{r.item_code || r.upc || r.sku}{r.packing > 1 ? ` · ${r.packing} per box` : ""}</div>
                </td>
                <td>
                  <div className="d-flex gap-1">
                    {r.packing > 1 && <input type="number" min="0" inputMode="numeric" className="form-control form-control-sm" style={{ width: 58 }} title="boxes" placeholder="0" value={r.accepted_boxes === "0" ? "" : r.accepted_boxes} onFocus={(e) => e.target.select()} onChange={(e) => set(r.key, { accepted_boxes: e.target.value || "0" })} />}
                    <input type="number" min="0" inputMode="numeric" className="form-control form-control-sm" style={{ width: 58 }} title={r.packing > 1 ? "loose units" : "units"} placeholder="0" value={r.accepted === "0" ? "" : r.accepted} onFocus={(e) => e.target.select()} onChange={(e) => set(r.key, { accepted: e.target.value || "0" })} />
                  </div>
                  {r.packing > 1 && <div className="fs-11 text-muted">{qty(unitsOf(r, "accepted"))} units</div>}
                </td>
                {/* what the carton in their hands still owes: ordered, counted on this receipt, and the rest */}
                {showStat && <td>{s ? <><div className={`fw-medium ${s.left > 0 ? "text-warning" : s.left === 0 ? "text-success" : "text-danger"}`}>{leftText(s)}</div><div className="fs-11 text-muted">{boxes(s.counted, s.packing)} of {boxes(s.ordered - s.posted, s.packing)} due{s.posted > 0 ? ` · ${boxes(s.posted, s.packing)} on earlier deliveries` : ""}</div></> : <span className="text-muted">not on the order</span>}</td>}
                <td><input type="number" min="0" inputMode="numeric" className="form-control form-control-sm" placeholder="0" value={r.rejected === "0" ? "" : r.rejected} onFocus={(e) => e.target.select()} onChange={(e) => set(r.key, { rejected: e.target.value || "0" })} /></td>
                <td><input type="number" min="0" inputMode="numeric" className="form-control form-control-sm" placeholder="0" value={r.missing === "0" ? "" : r.missing} onFocus={(e) => e.target.select()} onChange={(e) => set(r.key, { missing: e.target.value || "0" })} /></td>
                <td><input className="form-control form-control-sm" placeholder={unitsOf(r, "rejected") || unitsOf(r, "missing") ? "required" : ""} value={r.reject_reason} onChange={(e) => set(r.key, { reject_reason: e.target.value })} /></td>
                <td>
                  <input className="form-control form-control-sm mb-1" placeholder="batch" title="Leave blank unless the carton shows one — a batch-tracked item then uses the challan or receipt number" value={r.batch_no} onChange={(e) => set(r.key, { batch_no: e.target.value })} />
                  <DateInput ref={(el) => { if (expiryRefs) expiryRefs.current[r.key] = el; }} className={`form-control form-control-sm ${r.needs_expiry && !r.expiry_date ? "border-warning" : askExpiry === r.key ? "border-primary" : ""}`} title={r.needs_expiry ? "Expiry — this product is expiry-tracked, so it is needed before the receipt can be posted" : "Expiry — leave empty if the carton does not show one"} value={r.expiry_date}
                    onChange={(e) => { set(r.key, { expiry_date: e.target.value }); setAskExpiry?.(null); }}
                    onKeyDown={(e) => { if (e.key === "Enter") { setAskExpiry?.(null); onExpiryDone?.(); } }} />
                  {r.needs_expiry && !r.expiry_date
                    ? <div className="fs-11 text-warning">expiry needed — read it off the box</div>
                    : askExpiry === r.key && <div className="fs-11 text-primary">expiry? Enter to skip</div>}
                </td>
                <td><input className="form-control form-control-sm" placeholder="note" value={r.note} onChange={(e) => set(r.key, { note: e.target.value })} /></td>
                <td><EntitySelect endpoint="/api/v1/settings/bins" query={{ warehouse: warehouse || undefined }} value={r.bin} onChange={(v) => set(r.key, { bin: v })} mapOption={(b: any) => ({ id: b.id, label: b.code, sub: b.name })} placeholder={r.default_bin_name || "anywhere"} /></td>
                <td className="text-end"><button className="btn btn-sm btn-outline-danger" title="remove" onClick={() => drop(r.key)}><i className="ti ti-x" /></button></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function GrnDetailPage() {
  const { id } = useParams();
  const { can } = usePerms();
  const q = useDoc("/api/v1/goods-receipts", id);
  // posting or cancelling a receipt changes what the order is still owed, so the order's Receiving panel is stale
  const { run, busy } = useAction("/api/v1/goods-receipts", id, [["po-receiving"]]);
  const [cancel, setCancel] = useState(false);
  const [del, setDel] = useState(false);
  /** Posting is irreversible except by cancelling the whole receipt, so it is never one click. */
  const [posting, setPosting] = useState(false);
  const nav = useNavigate();
  const g = q.data;
  // The receipt says it is billed but not by which bill, and the bills cannot be filtered by receipt. The
  // supplier's recent bills name the receipts they cover, so the one quoting this receipt is found among them —
  // a `bills` field on the receipt would replace the search. Only asked for once the receipt says it is billed.
  const bill = useQuery({ queryKey: ["bill-for-grn", id], enabled: Boolean(g?.billed && g?.supplier), queryFn: async () => (await api.list("/api/v1/purchase-bills", { supplier: g.supplier, page_size: 100, ordering: "-bill_date" })).results.find((b: any) => (b.grn_numbers || []).includes(g.number)) || null });
  if (q.isLoading) return <Spinner />;
  if (!g) return null;
  return (
    <>
      <PageHeader title={g.number} badge={<StatusBadge status={g.status} />} crumbs={[{ label: "GRNs", to: "/app/purchasing/grns" }, { label: g.number }]}
        actions={<><button className="btn btn-outline-secondary" onClick={() => api.printDoc(`/api/v1/goods-receipts/${g.id}/pdf`)}><i className="ti ti-printer me-1" />Print</button><button className="btn btn-outline-secondary" onClick={() => api.download(`/api/v1/goods-receipts/${g.id}/pdf`, `${g.number}.pdf`)}><i className="ti ti-file-type-pdf me-1" />PDF</button>{g.status === "draft" && pageRights("grns").edit && <Link to={`/app/purchasing/grns/${g.id}/edit`} className="btn btn-outline-primary"><i className="ti ti-pencil me-1" />Edit</Link>}{g.status === "draft" && pageRights("grns").add && <button className="btn btn-success" disabled={!!busy} onClick={() => setPosting(true)}><i className="ti ti-check me-1" />Post</button>}{g.status === "posted" && !g.billed && pageRights("bills").extras.post && <Link to={`/app/purchasing/bills/new?grn=${g.id}&supplier=${g.supplier}`} className="btn btn-primary"><i className="ti ti-file-invoice me-1" />Create bill</Link>}{g.status !== "cancelled" && !g.billed && pageRights("grns").add && <button className="btn btn-outline-danger" onClick={() => setCancel(true)}>Cancel</button>}{g.status === "draft" && pageRights("grns").delete && <button className="btn btn-outline-danger" onClick={() => setDel(true)}><i className="ti ti-trash me-1" />Delete</button>}<button className="btn btn-outline-secondary" onClick={() => api.download(`/api/v1/goods-receipts/${g.id}/pdf`)}><i className="ti ti-printer me-1" />PDF</button></>} />
      <div className="card mb-3"><div className="card-body">
        {/* What a receipt is judged by, each on its own: the supplier's challan is the number an argument about a
            delivery is settled with, and when it was posted is when the stock actually moved. */}
        <div className="row g-3 mb-3">
          <InfoItem label="Supplier"><Link to={`/app/suppliers/${g.supplier}`}>{g.supplier_name}</Link></InfoItem>
          <InfoItem label="Purchase order">{g.po_number ? <Link to={`/app/purchasing/orders/${g.po}`}>{g.po_number}</Link> : <span className="text-muted">direct receipt — no order behind it</span>}</InfoItem>
          <InfoItem label="Warehouse">{g.warehouse_name}</InfoItem>
          <InfoItem label="Received on">{date(g.date)}</InfoItem>
          <InfoItem label="Supplier challan #">{g.supplier_challan_no || <span className="text-muted">none on the delivery note</span>}</InfoItem>
          <InfoItem label="Received by">{g.received_by_name || <span className="text-muted">—</span>}</InfoItem>
          <InfoItem label="Posted">{g.status === "posted" ? <>{datetime(g.posted_at)}{g.journal_number && <div className="fs-11 text-muted">journal {g.journal_number}</div>}</> : g.status === "cancelled" ? <span className="text-muted">cancelled — the stock was reversed</span> : <span className="text-warning">not posted — this stock is not in yet</span>}</InfoItem>
          <InfoItem label="Bill">{bill.data ? <><Link to={`/app/purchasing/bills/${bill.data.id}`}>{bill.data.number}</Link><div className="fs-11 text-muted">{date(bill.data.bill_date)} · {money(bill.data.total)} · {String(bill.data.status).replace(/_/g, " ")}</div></> : g.billed ? <span className="text-muted">{bill.isLoading ? "looking it up…" : "billed"}</span> : <span className="text-muted">not billed yet</span>}</InfoItem>
        </div>
        {/* the whole list is here — a long one scrolls inside the card rather than being cut off */}
        <div className="fs-12 text-muted mb-2">{g.lines.length} line{g.lines.length === 1 ? "" : "s"} · received {qty(sumBy(g.lines, (l: any) => l.qty_accepted))} units{Number(sumBy(g.lines, (l: any) => l.qty_damaged)) > 0 ? ` · rejected ${qty(sumBy(g.lines, (l: any) => l.qty_damaged))}` : ""}{Number(sumBy(g.lines, (l: any) => l.qty_short)) > 0 ? ` · not received ${qty(sumBy(g.lines, (l: any) => l.qty_short))}` : ""} · {money(g.total_value)}</div>
        <div className={g.lines.length > LINES_SCROLL_FROM ? "table-scroll" : "table-responsive"}>
        <table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>Item</th><th className="num">Received</th><th className="num">Rejected</th><th className="num">Not received</th><th>Reason</th><th>Placed in</th><th>Batch / expiry</th><th className="num">D.P (unit)</th><th className="num">Value</th></tr></thead><tbody>{g.lines.map((l: any) => <tr key={l.id}><td>{l.variant_name}<div className="text-muted fs-11 font-monospace">{l.item_code || l.variant_sku}{l.packing > 1 ? ` · ${l.packing}/box` : ""}</div></td><td className="num">{boxes(l.qty_accepted, l.packing)}{l.packing > 1 && Number(l.qty_accepted) > 0 && <div className="fs-11 text-muted">{qty(l.qty_accepted)} units</div>}</td><td className="num text-danger">{Number(l.qty_damaged) ? boxes(l.qty_damaged, l.packing) : "—"}</td><td className="num text-warning">{Number(l.qty_short) ? boxes(l.qty_short, l.packing) : "—"}</td><td className="fs-11">{l.reject_reason || "—"}</td><td>{l.bin_name || <span className="text-muted">{g.warehouse_name}</span>}</td><td>{l.batch_no || <span className="text-muted">—</span>}{l.expiry_date ? <div className="fs-11 text-muted">exp {date(l.expiry_date)}</div> : null}</td><td className="num">{money(l.unit_cost)}</td><td className="num fw-medium">{money(Number(l.qty_accepted) * Number(l.unit_cost) + Number(l.landed_cost_share))}</td></tr>)}</tbody><tfoot>
          <tr className="fw-bold">
            <td className="text-end">{g.lines.length} line{g.lines.length === 1 ? "" : "s"}</td>
            <td className="num">{qty(sumBy(g.lines, (l: any) => l.qty_accepted))}</td>
            <td className="num text-danger">{qty(sumBy(g.lines, (l: any) => l.qty_damaged))}</td>
            <td className="num text-warning">{qty(sumBy(g.lines, (l: any) => l.qty_short))}</td>
            <td colSpan={3} />
            <td className="text-end">Total value</td>
            <td className="num">{money(g.total_value)}</td>
          </tr>
        </tfoot></table>
        </div>
        {g.landed_costs?.length > 0 && <div className="fs-12 mt-2 text-muted">Landed costs: {g.landed_costs.map((lc: any) => `${lc.type} ${money(lc.amount)} (${lc.allocation.replace("_", " ")})`).join(", ")}</div>}</div></div>
      {g.po && <WorkflowStepper orderId={g.po} endpoint="/api/v1/purchase-orders" title="Order to payment" compact />}
      <DocTabs model="GoodsReceipt" id={g.id} showMovements defaultTab="history" />
      <ConfirmDialog open={posting} title={`Post ${g.number}?`} confirmText="Post it" loading={!!busy}
        message={<>This puts <strong>{qty(g.lines.reduce((n: number, l: any) => n + Number(l.qty_accepted || 0), 0))} unit(s)</strong> into <strong>{g.warehouse_name}</strong> and writes the journal entry.
          A posted receipt cannot be edited — correcting one afterwards means cancelling it, which reverses the stock and the journal, and receiving the delivery again.</>}
        onClose={() => setPosting(false)} onConfirm={async () => { await run("post", {}, { success: "Posted — stock received" }); setPosting(false); }} />
      <ConfirmDialog open={cancel} title="Cancel GRN" message="Posted GRNs reverse their stock and journal." withReason danger onClose={() => setCancel(false)} onConfirm={async (r) => { await run("cancel", { reason: r }); setCancel(false); }} />
      <ConfirmDialog open={del} title="Delete receipt" message={`${g.number} is a draft — it has posted nothing, so deleting removes it entirely. For a posted duplicate, use Cancel instead.`} danger confirmText="Delete" onClose={() => setDel(false)} onConfirm={async () => { try { await api.delete(`/api/v1/goods-receipts/${g.id}`); toast.success("Receipt deleted"); nav("/app/purchasing/grns"); } catch (e: any) { toast.error(e.message); } setDel(false); }} />
    </>
  );
}

export function BillsPage() {
  const { can } = usePerms();
  const cols: Column<any>[] = [{ key: "number", header: "Bill #", render: (b) => <span className="fw-semibold text-dark">{b.number}</span> }, { key: "bill_date", header: "Date", sortKey: "bill_date", render: (b) => <>{date(b.bill_date)}<div className="text-muted fs-11">due {date(b.due_date)}</div></> }, { key: "supplier_name", header: "Supplier" }, { key: "supplier_invoice_no", header: "Supplier inv" }, { key: "grn_numbers", header: "GRNs", render: (b) => b.grn_numbers?.join(", ") }, { key: "total", header: "Total", num: true, sortKey: "total", render: (b) => money(b.total) }, { key: "balance", header: "Balance", num: true, render: (b) => <span className={Number(b.balance) > 0 ? "fw-semibold text-danger" : "text-success"}>{money(b.balance)}</span> }, { key: "status", header: "Status", render: (b) => <StatusBadge status={b.status} /> }];
  return <ListPage page="bills" title="Purchase bills" endpoint="/api/v1/purchase-bills" columns={cols} rowLink={(b) => `/app/purchasing/bills/${b.id}`} createTo="/app/purchasing/bills/new" createLabel="New bill" defaultOrdering="-bill_date" filters={[{ name: "status", label: "Status", type: "select", options: ["draft", "posted", "partially_paid", "paid", "cancelled"].map((s) => ({ value: s, label: s.replace(/_/g, " ") })) }, supFilter, { name: "overdue", label: "Overdue", type: "toggle" }]} />;
}

export function BillFormPage() {
  const [err, setErr] = useState<string | null>(null);
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const [form, setForm] = useState<any>({ supplier: sp.get("supplier") || null, grns: sp.get("grn") ? [sp.get("grn")] : [], bill_date: new Date().toISOString().slice(0, 10), due_date: "", supplier_invoice_no: "", discount: "0", freight: "0", notes: "" });
  const grns = useQuery({ queryKey: ["unbilled-grns", form.supplier], queryFn: () => api.list("/api/v1/goods-receipts", { supplier: form.supplier, status: "posted", billed: false, page_size: 100 }), enabled: Boolean(form.supplier) });
  return (
    <>
      <PageHeader title="New purchase bill" crumbs={[{ label: "Bills", to: "/app/purchasing/bills" }, { label: "New" }]} />
      <SaveError error={err} what="purchase bill" />
      <div className="card mb-3"><div className="card-body"><div className="row">
        <Field label="Supplier *" col="col-md-4"><EntitySelect endpoint="/api/v1/suppliers" value={form.supplier} onChange={(v) => setForm({ ...form, supplier: v, grns: [] })} mapOption={(s) => ({ id: s.id, label: s.name })} /></Field>
        <Field label="Bill date" col="col-md-2"><DateInput className="form-control" value={form.bill_date} onChange={(e) => setForm({ ...form, bill_date: e.target.value })} /></Field><Field label="Due date" col="col-md-2" hint="blank = supplier terms"><DateInput className="form-control" value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} /></Field>
        <Field label="Supplier invoice #" col="col-md-2"><input className="form-control" value={form.supplier_invoice_no} onChange={(e) => setForm({ ...form, supplier_invoice_no: e.target.value })} /></Field><Field label="Freight on bill" col="col-md-2"><input type="number" step="0.01" className="form-control" value={form.freight} onChange={(e) => setForm({ ...form, freight: e.target.value })} /></Field>
        <Field label="Discount" col="col-md-2"><input type="number" step="0.01" className="form-control" value={form.discount} onChange={(e) => setForm({ ...form, discount: e.target.value })} /></Field><Field label="Notes" col="col-md-10"><input className="form-control" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field></div>
        <h6 className="fs-13">Unbilled goods receipts</h6>
        <table className="table table-sm fs-12"><thead className="table-light"><tr><th /><th>GRN</th><th>Date</th><th>PO</th><th>Challan</th><th className="num">Value</th></tr></thead><tbody>{grns.data?.results.map((g: any) => <tr key={g.id}><td><input type="checkbox" className="form-check-input" checked={form.grns.includes(g.id)} onChange={(e) => setForm({ ...form, grns: e.target.checked ? [...form.grns, g.id] : form.grns.filter((x: string) => x !== g.id) })} /></td><td>{g.number}</td><td>{date(g.date)}</td><td>{g.po_number}</td><td>{g.supplier_challan_no}</td><td className="num">{money(g.total_value)}</td></tr>)}{grns.data && !grns.data.results.length && <tr><td colSpan={6} className="text-muted text-center py-3">No unbilled receipts for this supplier</td></tr>}</tbody></table>
        <div className="form-text">Taxes are computed per line from each product's tax class (or the supplier override). Variance between GRN cost and PO price posts to Purchase Price Variance.</div>
      </div></div>
      <div className="d-flex gap-2 justify-content-end"><button className="btn btn-light" onClick={() => nav(-1)}>Cancel</button><button className="btn btn-outline-primary" disabled={!form.grns.length} onClick={async () => { try { const r = await api.post("/api/v1/purchase-bills", { ...form, due_date: form.due_date || null }, true); nav(`/app/purchasing/bills/${r.id}`); } catch (e: any) { setErr(e.message); toast.error(e.message); } }}>Save draft</button><button className="btn btn-primary" disabled={!form.grns.length} onClick={async () => { try { const r = await api.post("/api/v1/purchase-bills", { ...form, due_date: form.due_date || null, post: true }, true); toast.success("Bill posted"); nav(`/app/purchasing/bills/${r.id}`); } catch (e: any) { setErr(e.message); toast.error(e.message); } }}>Save & post</button></div>
    </>
  );
}

export function BillDetailPage() {
  const { id } = useParams();
  const { can } = usePerms();
  const q = useDoc("/api/v1/purchase-bills", id);
  const pays = useQuery({ queryKey: ["bill-payments", id], queryFn: () => api.get(`/api/v1/purchase-bills/${id}/payments`), enabled: Boolean(id) });
  const { run, busy } = useAction("/api/v1/purchase-bills", id);
  const [cancel, setCancel] = useState(false);
  if (q.isLoading) return <Spinner />;
  const b = q.data;
  if (!b) return null;
  return (
    <>
      <PageHeader title={b.number} badge={<StatusBadge status={b.status} />} crumbs={[{ label: "Bills", to: "/app/purchasing/bills" }, { label: b.number }]}
        actions={<>{b.status === "draft" && pageRights("bills").extras.post && <button className="btn btn-success" disabled={!!busy} onClick={() => run("post", {}, { success: "Bill posted to payables" })}><i className="ti ti-check me-1" />Post</button>}{["posted", "partially_paid"].includes(b.status) && pageRights("payments").extras.pay && <Link to={`/app/accounting/payments/new?direction=out&partner_type=supplier&partner_id=${b.supplier}&bill=${b.id}&amount=${b.balance}`} className="btn btn-primary"><i className="ti ti-cash me-1" />Pay</Link>}{b.status !== "cancelled" && Number(b.paid_amount) === 0 && pageRights("bills").extras.post && <button className="btn btn-outline-danger" onClick={() => setCancel(true)}>Cancel</button>}</>} />
      <div className="row g-3 mb-3"><div className="col-6 col-md-3"><StatCard label="Total" value={money(b.total)} icon="ti-coins" color="primary" /></div><div className="col-6 col-md-3"><StatCard label="Paid" value={money(b.paid_amount)} icon="ti-cash" color="success" /></div><div className="col-6 col-md-3"><StatCard label="Balance" value={money(b.balance)} icon="ti-receipt" color="danger" sub={`due ${date(b.due_date)}`} /></div><div className="col-6 col-md-3"><StatCard label="Supplier" value={b.supplier_name} icon="ti-building-factory-2" color="info" sub={b.supplier_invoice_no} /></div></div>
      <div className="card mb-3"><div className="card-body"><div className="row g-3 mb-3"><InfoItem label="GRNs">{b.grn_numbers?.join(", ") || "manual"}</InfoItem><InfoItem label="Bill date">{date(b.bill_date)}</InfoItem><InfoItem label="Journal">{b.journal_number ? <Link to={`/app/accounting/journals/${b.journal}`}>{b.journal_number}</Link> : "—"}</InfoItem><InfoItem label="Notes">{b.notes || "—"}</InfoItem></div>
        <div className={(b.lines || []).length > LINES_SCROLL_FROM ? "table-scroll" : "table-responsive"}>
        <table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>GRN</th><th>Item</th><th className="num">Qty</th><th className="num">Price</th><th className="num">Disc %</th><th className="num">Taxable</th><th className="num">{b.tax1_name || "Tax 1"}</th><th className="num">{b.tax2_name || "Tax 2"}</th><th className="num">Total</th></tr></thead><tbody>{b.lines.map((l: any, i: number) => <tr key={i}><td>{l.grn}</td><td>{l.name}<div className="text-muted fs-11">{l.sku}</div></td><td className="num">{qty(l.qty)}</td><td className="num">{money(l.unit_price)}</td><td className="num">{l.discount_pct}</td><td className="num">{money(l.taxable)}</td><td className="num">{money(l.tax1)}</td><td className="num">{money(l.tax2)}</td><td className="num fw-medium">{money(l.total)}</td></tr>)}</tbody>
          <tfoot>
            <tr className="fw-semibold">
              <td className="text-end">{(b.lines || []).length} line{(b.lines || []).length === 1 ? "" : "s"}</td>
              <td />
              <td className="num">{qty(sumBy(b.lines || [], (l: any) => l.qty))}</td>
              <td colSpan={2} className="text-end text-muted fs-11">units</td>
              <td className="num">{money(sumBy(b.lines || [], (l: any) => l.taxable))}</td>
              <td className="num">{money(sumBy(b.lines || [], (l: any) => l.tax1))}</td>
              <td className="num">{money(sumBy(b.lines || [], (l: any) => l.tax2))}</td>
              <td className="num">{money(sumBy(b.lines || [], (l: any) => l.total))}</td>
            </tr>
            <tr><td colSpan={8} className="text-end">Subtotal</td><td className="num">{money(b.subtotal)}</td></tr>
            {Number(b.tax1_total) > 0 && <tr><td colSpan={8} className="text-end">{b.tax1_name}</td><td className="num">{money(b.tax1_total)}</td></tr>}
            {Number(b.tax2_total) > 0 && <tr><td colSpan={8} className="text-end">{b.tax2_name}</td><td className="num">{money(b.tax2_total)}</td></tr>}
            {Number(b.freight) > 0 && <tr><td colSpan={8} className="text-end">Freight</td><td className="num">{money(b.freight)}</td></tr>}
            <tr className="fw-bold"><td colSpan={8} className="text-end">Total</td><td className="num">{money(b.total)}</td></tr>
          </tfoot></table>
        </div></div></div>
      <DocTabs model="PurchaseBill" id={b.id} extra={[{ key: "payments", label: "Payments", render: () => (pays.data?.length ? <table className="table table-sm fs-12"><thead className="table-light"><tr><th>Payment</th><th>Date</th><th>Method</th><th className="num">Amount</th></tr></thead><tbody>{pays.data.map((p: any) => <tr key={p.id}><td><Link to={`/app/accounting/payments/${p.id}`}>{p.number}</Link></td><td>{date(p.date)}</td><td>{p.method}</td><td className="num">{money(p.allocations.find((a: any) => a.document_id === b.id)?.amount || p.amount)}</td></tr>)}</tbody></table> : <p className="text-muted fs-13">No payments yet</p>) }]} />
      <ConfirmDialog open={cancel} title="Cancel bill" withReason danger onClose={() => setCancel(false)} onConfirm={async (r) => { await run("cancel", { reason: r }); setCancel(false); }} />
    </>
  );
}

export function PurchaseReturnsPage() {
  const { can } = usePerms();
  const cols: Column<any>[] = [{ key: "number", header: "Return #", render: (r) => <span className="fw-semibold text-dark">{r.number}</span> }, { key: "date", header: "Date", render: (r) => date(r.date) }, { key: "supplier_name", header: "Supplier" }, { key: "warehouse_name", header: "Warehouse" }, { key: "reason", header: "Reason" }, { key: "is_debit_note_only", header: "Kind", render: (r) => (r.is_debit_note_only ? "Debit note (claim)" : "Stock return") }, { key: "total", header: "Total", num: true, render: (r) => money(r.total) }, { key: "status", header: "Status", render: (r) => <StatusBadge status={r.status} /> }];
  return <ListPage page="purchase_returns" title="Purchase returns & claims" endpoint="/api/v1/purchase-returns" columns={cols} rowLink={(r) => `/app/purchasing/returns/${r.id}`} createTo="/app/purchasing/returns/new" createLabel="New return" defaultOrdering="-date" filters={[supFilter, { name: "status", label: "Status", type: "select", options: ["draft", "posted"].map((s) => ({ value: s, label: s })) }]} />;
}

export function PurchaseReturnFormPage() {
  const [err, setErr] = useState<string | null>(null);
  const nav = useNavigate();
  const warehouses = useWarehouses();
  const [form, setForm] = useState<any>({ supplier: null, warehouse: "", date: new Date().toISOString().slice(0, 10), reason_code: "", reason: "", is_debit_note_only: false, notes: "" });
  const [lines, setLines] = useState<EditLine[]>([]);
  const reasons = useOptions("/api/v1/settings/reason-codes", { used_for: "purchase_return", is_active: true });
  return (
    <>
      <PageHeader title="New purchase return" crumbs={[{ label: "Returns", to: "/app/purchasing/returns" }, { label: "New" }]} />
      <SaveError error={err} what="purchase return" />
      <div className="card mb-3"><div className="card-body"><div className="row">
        <Field label="Supplier *" col="col-md-4"><EntitySelect endpoint="/api/v1/suppliers" value={form.supplier} onChange={(v) => setForm({ ...form, supplier: v })} mapOption={(s) => ({ id: s.id, label: s.name })} /></Field>
        <Field label="From warehouse *" col="col-md-3"><select className="form-select" value={form.warehouse || warehouses.defaultId || ""} onChange={(e) => setForm({ ...form, warehouse: e.target.value })}>{warehouses.rows.map((w: any) => <option key={w.id} value={w.id}>{w.name}{w.is_default ? " (default)" : ""}</option>)}</select></Field>
        <Field label="Date" col="col-md-2"><DateInput className="form-control" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} /></Field>
        <Field label=" " col="col-md-3"><div className="form-check form-check-md mt-2"><input className="form-check-input" type="checkbox" id="dn" checked={form.is_debit_note_only} onChange={(e) => setForm({ ...form, is_debit_note_only: e.target.checked })} /><label htmlFor="dn" className="form-check-label">Claim only (no stock leaves)</label></div></Field>
        <Field label="Reason *" col="col-md-4"><select className="form-select" value={form.reason_code} onChange={(e) => setForm({ ...form, reason_code: e.target.value })}><option value="">Select a reason…</option>{reasons.data?.map((r: any) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></Field>
        <Field label="Details" col="col-md-8"><input className="form-control" value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} placeholder="e.g. expired stock from June batch, damaged cartons on arrival…" /></Field></div>
        <LinesEditor lines={lines} onChange={setLines} priceFor="cost" showDiscount={false} variantQuery={{ with_stock: 1 }} reasons={(reasons.data || []).map((r: any) => ({ id: r.id, name: r.name }))} />
      </div></div>
      <div className="d-flex gap-2 justify-content-end"><button className="btn btn-light" onClick={() => nav(-1)}>Cancel</button><button className="btn btn-primary" onClick={async () => { const valid = lines.filter((l) => l.variant && Number(l.qty) > 0); if (!form.supplier || !form.warehouse || !form.reason_code || !valid.length) { const m = "Please fill in: supplier, warehouse, reason and at least one line."; setErr(m); return toast.error(m); } setErr(null); try { const r = await api.post("/api/v1/purchase-returns", { ...form, post: true, lines: valid.map((l) => ({ variant: l.variant, batch: l.batch || null, qty: l.qty, unit_cost: l.unit_price || null, reason_code: l.extra?.reason_code || form.reason_code, reason: "" })) }, true); toast.success("Return posted"); nav(`/app/purchasing/returns/${r.id}`); } catch (e: any) { setErr(e.message); toast.error(e.message); } }}>Save & post</button></div>
    </>
  );
}

export function PurchaseReturnDetailPage() {
  const { id } = useParams();
  const { can } = usePerms();
  const q = useDoc("/api/v1/purchase-returns", id);
  const { run, busy } = useAction("/api/v1/purchase-returns", id);
  if (q.isLoading) return <Spinner />;
  const r = q.data;
  if (!r) return null;
  return (
    <>
      <PageHeader title={r.number} badge={<StatusBadge status={r.status} />} crumbs={[{ label: "Returns", to: "/app/purchasing/returns" }, { label: r.number }]} actions={r.status === "draft" && pageRights("purchase_returns").add && <button className="btn btn-success" disabled={!!busy} onClick={() => run("post")}>Post</button>} />
      <div className="card mb-3"><div className="card-body"><div className="row g-3 mb-3"><InfoItem label="Supplier"><Link to={`/app/suppliers/${r.supplier}`}>{r.supplier_name}</Link></InfoItem><InfoItem label="Warehouse">{r.warehouse_name}</InfoItem><InfoItem label="Reason">{r.reason_name || "—"}{r.reason && <div className="fs-12 text-muted">{r.reason}</div>}</InfoItem><InfoItem label="Date">{date(r.date)}</InfoItem><InfoItem label="Reason">{r.reason}{r.is_debit_note_only && <div className="fs-12 text-muted">claim / debit note only</div>}</InfoItem></div>
        <table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>Item</th><th>Batch</th><th className="num">Qty</th><th className="num">D.P (unit)</th><th className="num">Value</th></tr></thead><tbody>{r.lines.map((l: any) => <tr key={l.id}><td>{l.variant_name}<div className="text-muted fs-11">{l.variant_sku}</div></td><td>{l.batch_no}</td><td className="num">{qty(l.qty)}</td><td className="num">{money(l.unit_cost)}</td><td className="num">{money(Number(l.qty) * Number(l.unit_cost))}</td></tr>)}</tbody><tfoot><tr className="fw-bold"><td colSpan={4} className="text-end">Total</td><td className="num">{money(r.total)}</td></tr></tfoot></table></div></div>
      <DocTabs model="PurchaseReturn" id={r.id} showMovements />
    </>
  );
}