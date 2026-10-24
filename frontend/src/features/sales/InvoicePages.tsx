import { DateInput } from "@/components/ui/DateInput";
import { DiscountPctSelect } from "@/features/sales/OrdersPages";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { date, datetime, money, qty } from "@/lib/format";
import { useAction, useDoc, useOptions, useWarehouses } from "@/lib/hooks";
import { pageRights, usePage, usePerms } from "@/lib/perms";
import { toast } from "@/lib/toast";
import { Alert, Field, InfoItem, PageHeader, Spinner, StatCard, StatusBadge, SaveError } from "@/components/ui/Bits";
import { Column } from "@/components/ui/DataTable";
import { EntitySelect } from "@/components/ui/EntitySelect";
import { ListPage } from "@/components/ui/ListPage";
import { ConfirmDialog, Modal } from "@/components/ui/Modal";
import { DocTabs } from "@/components/documents/DocTabs";
import { EditLine, LinesEditor } from "@/components/documents/LinesEditor";

export function InvoicesPage() {
  const stats = useQuery({ queryKey: ["invoice-stats"], queryFn: () => api.get("/api/v1/invoices/stats") });
  const cols: Column<any>[] = [
    { key: "number", header: "Invoice #", sortKey: "number", render: (i) => <span className="fw-semibold text-dark">{i.number}</span> },
    { key: "date", header: "Date", sortKey: "date", render: (i) => <>{date(i.date)}{i.due_date && i.due_date !== i.date && <div className="text-muted fs-11">due {date(i.due_date)}</div>}</> },
    { key: "customer_name", header: "Customer", render: (i) => <>{i.customer_name}<div className="text-muted fs-11">{i.customer_code}</div></> },
    { key: "order_number", header: "Order / DN", render: (i) => <>{i.order_number}<div className="text-muted fs-11">{i.delivery_note_number}</div></> },
    { key: "sales_person", header: "Sales person", render: (i: any) => i.sales_person || i.rep_name || <span className="text-muted">—</span> },
    { key: "total", header: "Total", num: true, sortKey: "total", render: (i) => money(i.total) },
    { key: "paid_amount", header: "Paid", num: true, render: (i) => money(i.paid_amount) },
    { key: "balance", header: "Balance", num: true, render: (i) => <span className={Number(i.balance) > 0 ? "fw-semibold text-danger" : "text-success"}>{money(i.balance)}</span> },
    { key: "status", header: "Status", render: (i) => <><StatusBadge status={i.status} />{i.delivery_status && <div className="fs-11 text-muted mt-1"><i className="ti ti-truck me-1" />{i.delivery_status}</div>}</> },
  ];
  return (
    <ListPage title="Sales invoices" page="invoices" endpoint="/api/v1/invoices" columns={cols} rowLink={(i) => `/app/sales/invoices/${i.id}`} createTo="/app/sales/invoices/new" createLabel="New invoice" defaultOrdering="-date" searchPlaceholder="Search invoice, customer, order…"
      filters={[{ name: "status", label: "Status", type: "select", options: ["posted", "partially_paid", "paid", "overdue", "cancelled"].map((s) => ({ value: s, label: s.replace(/_/g, " ") })) }, { name: "customer", label: "Customer", type: "entity", endpoint: "/api/v1/customers", mapOption: (c) => ({ id: c.id, label: c.name, sub: c.code }) }, { name: "open", label: "Open only", type: "toggle" }, { name: "overdue", label: "Overdue only", type: "toggle" }, { name: "date_from", label: "From", type: "date" }, { name: "date_to", label: "To", type: "date" }]}
      stats={stats.data && <div className="row g-3 mb-3"><div className="col-6 col-md-3"><StatCard label="Open invoices" value={stats.data.open_count} icon="ti-file-invoice" color="primary" /></div><div className="col-6 col-md-3"><StatCard label="Outstanding" value={money(stats.data.outstanding, 0)} icon="ti-receipt" color="warning" /></div><div className="col-6 col-md-3"><StatCard label="Overdue" value={money(stats.data.overdue, 0)} icon="ti-alert-circle" color="danger" /></div><div className="col-6 col-md-3"><StatCard label="Sales this month" value={money(stats.data.month_sales, 0)} icon="ti-chart-line" color="success" /></div></div>} />
  );
}

export function InvoiceDetailPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const { can } = usePerms();
  const page = usePage("invoices");
  const creditNotes = usePage("credit_notes");
  const q = useDoc("/api/v1/invoices", id);
  const pays = useQuery({ queryKey: ["invoice-payments", id], queryFn: () => api.get(`/api/v1/invoices/${id}/payments`), enabled: Boolean(id) });
  const { run, busy } = useAction("/api/v1/invoices", id);
  const [cancel, setCancel] = useState(false);
  const contribs = useQuery({ queryKey: ["crud", "/api/v1/settings/discount-contributions"], queryFn: () => api.get("/api/v1/settings/discount-contributions", { page_size: 100 }), enabled: Boolean(id) });
  // What a posted invoice still allows: when it falls due, and what is written on it. The amounts belong to the
  // ledger — the dialog says so, because "Edit" that silently refuses to edit the figures is worse than no Edit.
  const [edit, setEdit] = useState<{ due_date: string; notes: string; date: string; lines: any[] } | null>(null);
  const [saving, setSaving] = useState(false);
  const qc = useQueryClient();
  if (q.isLoading) return <Spinner />;
  const inv = q.data;
  if (!inv) return null;
  const open = ["posted", "partially_paid", "overdue"].includes(inv.status);
  return (
    <>
      <PageHeader title={inv.number} badge={<><StatusBadge status={inv.status} />{inv.delivery_status && <span className="badge bg-purple-light text-purple ms-1"><i className="ti ti-truck me-1" />{inv.delivery_status}</span>}</>} crumbs={[{ label: "Invoices", to: "/app/sales/invoices" }, { label: inv.number }]}
        actions={<>
          {inv.status === "draft" && page.can("discount") && <div className="btn-group">
            <select className="form-select form-select-sm" style={{ width: 150 }} value={String(Number(inv.discount_pct || 0))} onChange={(e) => run("discount", { action: e.target.value === "0" ? "remove" : "apply", percent: e.target.value }, { success: "Discount updated" })} title="Co-funded total discount">
              <option value="0">No discount</option>
              {(contribs.data || []).filter((r: any) => r.is_active).map((r: any) => <option key={r.id} value={String(Number(r.total_pct))}>{Number(r.total_pct)}% (co {Number(r.company_pct)}%)</option>)}
            </select>
          </div>}
          {open && pageRights("payments").extras.receive && <Link to={`/app/accounting/payments/new?direction=in&partner_type=customer&partner_id=${inv.customer}&invoice=${inv.id}&amount=${inv.balance}`} className="btn btn-success"><i className="ti ti-cash me-1" />Receive payment</Link>}
          {["posted", "partially_paid", "overdue", "paid"].includes(inv.status) && creditNotes.add && <Link to={`/app/sales/credit-notes/new?invoice=${inv.id}`} className="btn btn-outline-warning"><i className="ti ti-arrow-back-up me-1" />Credit note / return</Link>}
          {inv.status !== "cancelled" && Number(inv.paid_amount) === 0 && page.delete && <button className="btn btn-outline-danger" onClick={() => setCancel(true)}>Cancel</button>}
          {inv.status === "draft" && page.add && <button className="btn btn-success" disabled={!!busy} onClick={() => run("post", {}, { success: `${inv.number} posted` })}><i className="ti ti-check me-1" />Post to ledger</button>}
          {!["cancelled", "paid"].includes(inv.status) && page.edit && <button className="btn btn-outline-primary" onClick={() => { setEdit({ due_date: inv.due_date || "", notes: inv.notes || "", date: inv.date || "", lines: (inv.lines || []).map((l: any) => ({ id: l.id, description: l.description, qty: l.qty, unit_price: String(l.unit_price), discount_amount: String(l.discount_amount), product_discount_pct: String(Number(l.product_discount_pct) || 0) })) }); }}><i className="ti ti-edit me-1" />Edit</button>}
          <button className="btn btn-outline-secondary" onClick={() => api.printDoc(`/api/v1/invoices/${inv.id}/pdf`)}><i className="ti ti-printer me-1" />Print</button>
          <button className="btn btn-outline-secondary" onClick={() => api.download(`/api/v1/invoices/${inv.id}/pdf`)}><i className="ti ti-file-type-pdf me-1" />PDF</button>
        </>} />
      <div className="row g-3 mb-3">
        <div className="col-sm-6 col-xl-3"><div className="card mb-0" style={{ borderTop: "3px solid #F26522" }}><div className="card-body py-3 text-center"><div className="fw-bold text-primary fs-18">{money(inv.total)}</div><div className="text-muted fs-12">Invoice total</div></div></div></div>
        <div className="col-sm-6 col-xl-3"><div className="card mb-0" style={{ borderTop: "3px solid #03C95A" }}><div className="card-body py-3 text-center"><div className="fw-bold text-success fs-18">{money(inv.paid_amount)}</div><div className="text-muted fs-12">Paid</div></div></div></div>
        <div className="col-sm-6 col-xl-3"><div className="card mb-0" style={{ borderTop: "3px solid #E70D0D" }}><div className="card-body py-3 text-center"><div className="fw-bold text-danger fs-18">{money(inv.balance)}</div><div className="text-muted fs-12">{inv.due_date && inv.due_date !== inv.date ? <>Balance due {date(inv.due_date)}</> : "Balance due"}</div></div></div></div>
        <div className="col-sm-6 col-xl-3"><div className="card mb-0" style={{ borderTop: "3px solid #1B84FF" }}><div className="card-body py-3 text-center"><div className="fw-bold fs-16"><Link to={`/app/customers/${inv.customer}`}>{inv.customer_name}</Link></div><div className="text-muted fs-12">{inv.customer_code}</div></div></div></div>
      </div>
      <div className="card mb-3"><div className="card-body">
        <div className="row g-3 mb-3"><InfoItem label="Order">{inv.order_number ? <Link to={`/app/sales/orders/${inv.order}`}>{inv.order_number}</Link> : "—"}</InfoItem><InfoItem label="Delivery note">{inv.delivery_note_number ? <Link to={`/app/delivery/notes/${inv.delivery_note}`}>{inv.delivery_note_number}</Link> : "—"}</InfoItem><InfoItem label="Load form / dispatch">{inv.picklist_number ? <Link to={`/app/delivery/pick-lists/${inv.picklist}`}>{inv.picklist_number}</Link> : "—"}{inv.trip_number ? <div className="fs-12"><Link to={`/app/delivery/trips/${inv.trip}`}>{inv.trip_number}</Link></div> : null}</InfoItem><InfoItem label="Rep">{inv.rep_name || "—"}</InfoItem><InfoItem label="Journal">{inv.journal_number ? <Link to={`/app/accounting/journals/${inv.journal}`}>{inv.journal_number}</Link> : "—"}</InfoItem></div>
        <table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>#</th><th>Item</th><th className="num">Qty</th><th className="num">Price</th><th className="num">Disc</th>{Number(inv.tax1_total) > 0 && <th className="num">Tax</th>}<th className="num">Amount</th></tr></thead>
          {/* the owner's rule: a line's Disc cell carries only the product's OWN discount; the customer-level
              % appears once, after the subtotal — amounts add the customer part back so the column sums */}
          <tbody>{inv.lines.map((l: any, i: number) => { const prodD = Math.min(Number(l.qty) * Number(l.unit_price) * (Number(l.product_discount_pct) || 0) / 100, Number(l.discount_amount) || 0); const custD = (Number(l.discount_amount) || 0) - prodD; const taxed = Number(inv.tax1_total) > 0; const taxAmt = (Number(l.tax1_amount) || 0) + (Number(l.tax2_amount) || 0); return <tr key={l.id}><td>{i + 1}</td><td>{l.description}<div className="text-muted fs-11">{l.variant_sku}</div></td><td className="num">{qty(l.qty)}</td><td className="num">{money(l.unit_price)}</td><td className="num">{prodD > 0 ? <>{money(prodD)}<div className="text-muted fs-11">prod {Number(l.product_discount_pct)}%</div></> : "—"}</td>{taxed && <td className="num">{taxAmt ? <>{money(taxAmt)}<div className="text-muted fs-11">{Number(l.tax1_rate)}%</div></> : "—"}</td>}<td className="num fw-medium">{money(Number(l.line_total) + custD - (taxed ? taxAmt : 0))}</td></tr>; })}</tbody>
          <tfoot>{(() => { const custTotal = inv.lines.reduce((a: number, l: any) => a + ((Number(l.discount_amount) || 0) - Math.min(Number(l.qty) * Number(l.unit_price) * (Number(l.product_discount_pct) || 0) / 100, Number(l.discount_amount) || 0)), 0); const taxTotal = (Number(inv.tax1_total) || 0) + (Number(inv.tax2_total) || 0); const span = taxTotal > 0 ? 6 : 5; return <>{(custTotal > 0.004 || taxTotal > 0) && <tr><td colSpan={span} className="text-end">Subtotal</td><td className="num">{money(Number(inv.total) + custTotal - taxTotal)}</td></tr>}{custTotal > 0.004 && <tr><td colSpan={span} className="text-end">Discount{Number(inv.discount_pct) > 0 ? ` (${Number(inv.discount_pct)}%)` : ""}</td><td className="num">−{money(custTotal)}</td></tr>}{taxTotal > 0 && <tr><td colSpan={span} className="text-end">Tax{inv.tax1_name ? ` — ${inv.tax1_name}` : ""}</td><td className="num">{money(taxTotal)}</td></tr>}{Number(inv.company_claims_total) > 0 && <tr className="text-success"><td colSpan={span} className="text-end">Claimed from manufacturers</td><td className="num"><Link to="/app/sales/company-claims">{money(inv.company_claims_total)}</Link></td></tr>}<tr className="fw-bold"><td colSpan={span} className="text-end">Total</td><td className="num">{money(inv.total)}</td></tr></>; })()}</tfoot></table>
      </div></div>
      <DocTabs model="SalesInvoice" id={inv.id} showMovements extra={[{ key: "payments", label: "Payments", render: () => (
        <div>{!pays.data?.payments?.length && !pays.data?.rep_collections?.length && <p className="text-muted fs-13">No payments yet</p>}
          {pays.data?.payments?.length > 0 && <table className="table table-sm fs-12"><thead className="table-light"><tr><th>Receipt</th><th>Date</th><th>Method</th><th>By</th><th className="num">Amount</th></tr></thead><tbody>{pays.data.payments.map((p: any) => <tr key={p.id}><td><Link to={`/app/accounting/payments/${p.id}`}>{p.number}</Link></td><td>{date(p.date)}</td><td>{p.method}{p.cheque_no ? ` ${p.cheque_no} (${p.cheque_status})` : ""}</td><td>{p.collected_by_name}</td><td className="num">{money(p.allocations.find((a: any) => a.document_id === inv.id)?.amount || p.amount)}</td></tr>)}</tbody></table>}
          {pays.data?.rep_collections?.length > 0 && <><h6 className="fs-13 mt-3">Rep collections</h6><table className="table table-sm fs-12"><thead className="table-light"><tr><th>Collection</th><th>Date</th><th>Rep</th><th>Status</th><th className="num">Amount</th></tr></thead><tbody>{pays.data.rep_collections.map((c: any) => <tr key={c.id}><td>{c.number}</td><td>{date(c.date)}</td><td>{c.rep_name}</td><td><StatusBadge status={c.status} /></td><td className="num">{money(c.allocations.find((a: any) => a.invoice === inv.id)?.amount || c.amount)}</td></tr>)}</tbody></table></>}
        </div>) }]} />
      <Modal open={Boolean(edit)} title={`Edit ${inv.number}`} onClose={() => setEdit(null)} size="md"
        footer={<><button className="btn btn-light" onClick={() => setEdit(null)}>Cancel</button>
          <button className="btn btn-primary" disabled={saving} onClick={async () => {
            setSaving(true);
            try {
              const body: any = { due_date: edit!.due_date || null, notes: edit!.notes };
              if (inv.status === "draft") {
                body.date = edit!.date || undefined;
                body.lines = edit!.lines.map((l) => ({ id: l.id, unit_price: l.unit_price, discount_amount: l.discount_amount, product_discount_pct: l.product_discount_pct }));
              }
              await api.patch(`/api/v1/invoices/${inv.id}`, body);
              toast.success("Invoice updated");
              qc.invalidateQueries({ queryKey: ["doc", "/api/v1/invoices", inv.id] });
              qc.invalidateQueries({ queryKey: ["list", "/api/v1/invoices"] });
              setEdit(null);
            } catch (e: any) {
              toast.error(e.message);
            } finally {
              setSaving(false);
            }
          }}>Save</button></>}>
        {edit && <div className="row">
          <Field label="Due date" col="col-md-6" hint={`Invoice dated ${date(inv.date)}`}>
            <DateInput className="form-control" value={edit.due_date} onChange={(e) => setEdit({ ...edit, due_date: e.target.value })} />
          </Field>
          <Field label="Notes on the invoice" col="col-12" hint="Printed under the totals">
            <textarea className="form-control" rows={3} value={edit.notes} onChange={(e) => setEdit({ ...edit, notes: e.target.value })} />
          </Field>
          {inv.status === "draft" ? <>
            <Field label="Invoice date" col="col-md-6"><DateInput className="form-control" value={edit.date} onChange={(e) => setEdit({ ...edit, date: e.target.value })} /></Field>
            <div className="col-12">
              <label className="form-label fs-12 fw-semibold">Prices</label>
              <div className="table-responsive"><table className="table table-sm align-middle mb-1">
                <thead className="table-light"><tr><th>Item</th><th className="num" style={{ width: 90 }}>Qty</th><th className="num" style={{ width: 130 }}>Rate</th><th className="num" style={{ width: 130 }}>Discount</th><th className="num" style={{ width: 100 }} title="The product's own standing discount — changing it adjusts the discount amount">Prod %</th></tr></thead>
                <tbody>{edit.lines.map((l: any, i: number) => (
                  <tr key={l.id}>
                    <td className="fs-12">{l.description}</td>
                    <td className="num fs-12">{qty(l.qty)}</td>
                    <td><input type="number" step="0.01" min="0" className="form-control form-control-sm text-end" value={l.unit_price} onChange={(e) => { const ls = [...edit.lines]; ls[i] = { ...l, unit_price: e.target.value }; setEdit({ ...edit, lines: ls }); }} /></td>
                    <td><input type="number" step="0.01" min="0" className="form-control form-control-sm text-end" value={l.discount_amount} onChange={(e) => { const ls = [...edit.lines]; ls[i] = { ...l, discount_amount: e.target.value }; setEdit({ ...edit, lines: ls }); }} /></td><td><input type="number" step="0.01" min="0" className="form-control form-control-sm text-end" value={l.product_discount_pct ?? "0"} onChange={(e) => { const ls = [...edit.lines]; ls[i] = { ...l, product_discount_pct: e.target.value }; setEdit({ ...edit, lines: ls }); }} /></td>
                  </tr>))}</tbody>
              </table></div>
              <div className="form-text">Quantities are what the warehouse issued and are set by the delivery note. Tax is recalculated from the corrected figures when you save.</div>
            </div>
          </> : <div className="col-12"><Alert kind="info">The amounts cannot be changed here. This invoice is already in the ledger and owed by the customer, so a wrong figure is corrected with a <strong>credit note</strong>, which the customer also receives.</Alert></div>}
        </div>}
      </Modal>
      <ConfirmDialog open={cancel} title="Cancel invoice" withReason danger confirmText="Cancel invoice" onClose={() => setCancel(false)} onConfirm={async (r) => { await run("cancel", { reason: r }); setCancel(false); }} />
    </>
  );
}


/** New invoice = invoice an order that has issued/delivered goods not yet invoiced — that is where invoices
 * come from in this flow. The button appears for anyone whose role holds the Invoices "Add" switch. */
export function InvoiceCreatePage() {
  const nav = useNavigate();
  const [order, setOrder] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [applyTax, setApplyTax] = useState(false);
  const [discOverride, setDiscOverride] = useState<string>("");
  const detail = useDoc("/api/v1/sales-orders", order || undefined);
  const uninvoiced = (detail.data?.delivery_notes || []).filter((d: any) => !d.invoiced);
  // the % the invoice will carry: the order's own (seeded from the customer) unless changed right here
  const effDisc = discOverride !== "" ? discOverride : detail.data?.discount_pct != null ? String(Number(detail.data.discount_pct)) : "";
  return (
    <>
      <PageHeader title="New invoice" crumbs={[{ label: "Invoices", to: "/app/sales/invoices" }, { label: "New" }]} />
      <div className="card mb-3"><div className="card-body">
        <Alert kind="info">An invoice bills the goods the warehouse has issued, before dispatch — your flow: order → load form → issue → invoice → dispatch. Only orders at the Issued stage with an unbilled delivery are offered; a delivery completed without an invoice is billed automatically at delivery confirmation.</Alert>
        <div className="row">
          <Field label="Sales order *" col="col-md-5"><EntitySelect endpoint="/api/v1/sales-orders" query={{ uninvoiced: 1, status__in: "issued" }} value={order} onChange={(v) => { setOrder(v); setDiscOverride(""); setApplyTax(false); }} mapOption={(o: any) => ({ id: o.id, label: o.number, sub: `${o.customer_name} · ${money(o.total)} · ${o.status.replace(/_/g, " ")}` })} placeholder="search order…" /></Field>
          {order && <Field label="Discount %" col="col-md-3" hint="from the order (the customer's rate) — change it for this invoice"><DiscountPctSelect value={effDisc} onChange={setDiscOverride} /></Field>}
          {order && <Field label="Tax" col="col-md-3" hint="charges the company's sales tax on every line and shows it on the invoice"><div className="form-check form-switch mt-2"><input className="form-check-input" type="checkbox" id="chg-tax" checked={applyTax} onChange={(e) => setApplyTax(e.target.checked)} /><label className="form-check-label fs-13" htmlFor="chg-tax">Charge tax</label></div></Field>}
        </div>
        {order && detail.data && (
          uninvoiced.length
            ? <>
                <div className="fs-13 mb-2">Billing <strong>{detail.data.customer_name}</strong> for {uninvoiced.length} delivery note(s): {uninvoiced.map((d: any) => d.number).join(", ")}</div>
                <table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>#</th><th>Item</th><th className="num">Ordered</th><th className="num">Issued / delivered</th><th className="num">Rate</th><th className="num">Discount</th><th className="num">Amount</th></tr></thead>
                  <tbody>{(detail.data.lines || []).map((l: any, i: number) => <tr key={l.id}><td>{i + 1}</td><td>{l.variant_name}<div className="text-muted fs-11">{l.variant_sku}</div></td><td className="num">{Number(l.qty_ordered)}</td><td className="num">{Number(l.qty_delivered) || Number(l.qty_dispatched)}</td><td className="num">{money(l.unit_price)}</td><td className="num">{money(Number(l.discount_amount) + Number(l.scheme_discount))}</td><td className="num fw-medium">{money(l.line_total)}</td></tr>)}</tbody>
                  <tfoot><tr className="fw-bold"><td colSpan={6} className="text-end">Order total</td><td className="num">{money(detail.data.total)}</td></tr></tfoot></table>
                <div className="text-muted fs-11 mt-1">The invoice bills the issued quantities of the deliveries above — check it, then issue.</div>
              </>
            : <div className="fs-13 text-warning">Every delivery on this order is already invoiced — it will not be offered again.</div>
        )}
      </div></div>
      <div className="d-flex gap-2 justify-content-end">
        <button className="btn btn-light" onClick={() => nav(-1)}>Cancel</button>
        <button className="btn btn-primary" disabled={!order || busy || (detail.data && !uninvoiced.length)} onClick={async () => {
          setBusy(true);
          try {
            const r = await api.post(`/api/v1/sales-orders/${order}/invoice`, { ...(effDisc === "" ? {} : { discount_pct: effDisc }), ...(applyTax ? { apply_tax: true } : {}) }, true);
            const made = Array.isArray(r) ? r : [r];
            toast.success(made.length > 1 ? `${made.length} invoices created` : `${made[0]?.number || "Invoice"} created`);
            nav(made[0] ? `/app/sales/invoices/${made[0].id}` : "/app/sales/invoices");
          } catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
        }}><i className="ti ti-file-invoice me-1" />Issue Invoice</button>
      </div>
    </>
  );
}

export function CreditNotesPage() {
  const { can } = usePerms();
  const cols: Column<any>[] = [
    { key: "number", header: "Credit note #", render: (c) => <span className="fw-semibold text-dark">{c.number}</span> },
    { key: "date", header: "Date", render: (c) => date(c.date) }, { key: "customer_name", header: "Customer" }, { key: "invoice_number", header: "Invoice" }, { key: "reason", header: "Reason" },
    { key: "total", header: "Total", num: true, render: (c) => money(c.total) }, { key: "status", header: "Status", render: (c) => <StatusBadge status={c.status} /> },
  ];
  return <ListPage title="Credit notes & sales returns" page="credit_notes" endpoint="/api/v1/credit-notes" columns={cols} rowLink={(c) => `/app/sales/credit-notes/${c.id}`} createTo="/app/sales/credit-notes/new" createLabel="New credit note" defaultOrdering="-date" filters={[{ name: "status", label: "Status", type: "select", options: ["draft", "posted", "cancelled"].map((s) => ({ value: s, label: s })) }]} />;
}

export function CreditNoteDetailPage() {
  const { id } = useParams();
  const { can } = usePerms();
  const q = useDoc("/api/v1/credit-notes", id);
  const { run, busy } = useAction("/api/v1/credit-notes", id);
  if (q.isLoading) return <Spinner />;
  const cn = q.data;
  if (!cn) return null;
  return (
    <>
      <PageHeader title={cn.number} badge={<StatusBadge status={cn.status} />} crumbs={[{ label: "Credit notes", to: "/app/sales/credit-notes" }, { label: cn.number }]} actions={cn.status === "draft" && pageRights("credit_notes").add && <button className="btn btn-success" disabled={!!busy} onClick={() => run("post", {}, { success: "Credit note posted" })}><i className="ti ti-check me-1" />Post</button>} />
      <div className="card mb-3"><div className="card-body">
        <div className="row g-3 mb-3"><InfoItem label="Customer"><Link to={`/app/customers/${cn.customer}`}>{cn.customer_name}</Link></InfoItem><InfoItem label="Invoice">{cn.invoice_number ? <Link to={`/app/sales/invoices/${cn.invoice}`}>{cn.invoice_number}</Link> : "—"}</InfoItem><InfoItem label="Date">{date(cn.date)}</InfoItem><InfoItem label="Reason">{cn.reason || "—"}</InfoItem></div>
        <table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>Item</th><th className="num">Qty</th><th className="num">Price</th><th>Restock</th><th>Condition</th><th>Reason</th><th className="num">Total</th></tr></thead>
          <tbody>{cn.lines.map((l: any) => <tr key={l.id}><td>{l.variant_name}<div className="text-muted fs-11">{l.variant_sku}</div></td><td className="num">{qty(l.qty)}</td><td className="num">{money(l.unit_price)}</td><td>{l.restock ? "yes" : "no"}</td><td>{l.condition}</td><td>{l.reason}</td><td className="num">{money(l.line_total)}</td></tr>)}</tbody>
          <tfoot><tr className="fw-bold"><td colSpan={6} className="text-end">Total</td><td className="num">{money(cn.total)}</td></tr></tfoot></table>
      </div></div>
      <DocTabs model="CreditNote" id={cn.id} showMovements />
    </>
  );
}

export function CreditNoteFormPage() {
  const [err, setErr] = useState<string | null>(null);
  const nav = useNavigate();
  const sp = new URLSearchParams(window.location.search);
  const invoiceId = sp.get("invoice");
  const [form, setForm] = useState<any>({ customer: null, invoice: invoiceId, warehouse: "", date: new Date().toISOString().slice(0, 10), reason: "", applied_to_invoice: true, notes: "" });
  const [lines, setLines] = useState<(EditLine & { restock?: boolean; condition?: string; reason?: string })[]>([]);
  // whichever way an invoice is chosen — the URL (from the invoice page) or the dropdown — its items load,
  // with their full quantities, ready to be trimmed, repriced or removed before issuing the credit note
  const inv = useDoc("/api/v1/invoices", form.invoice || undefined);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const wh = useWarehouses();
  // restock targets are sellable warehouses — damaged/expired returns are routed to quarantine by the service
  const restockable = wh.rows.filter((w: any) => !["quarantine", "van"].includes(w.type));
  if (invoiceId && inv.isLoading && !loadedFor) return <Spinner />;
  if (form.invoice && inv.data && inv.data.id === form.invoice && loadedFor !== form.invoice) {
    setLoadedFor(form.invoice);
    setForm((f: any) => ({ ...f, customer: inv.data.customer }));
    setLines(inv.data.lines.map((l: any) => ({ key: l.id, variant: l.variant, variant_name: l.description, sku: l.variant_sku, qty: String(Number(l.qty)), unit_price: String(l.unit_price), batch: l.batch, restock: true, condition: "good", reason: "" })));
  }

  const submit = async (post: boolean) => {
    const valid = lines.filter((l) => l.variant && Number(l.qty) > 0);
    if (!form.customer || !valid.length) return toast.error("Select a customer and at least one line with quantity");
    try {
      const r = await api.post("/api/v1/credit-notes", { ...form, warehouse: form.warehouse || null, post, lines: valid.map((l) => ({ variant: l.variant, batch: l.batch || null, qty: l.qty, unit_price: l.unit_price, restock: l.restock ?? true, condition: l.condition || "good", reason: l.reason || "" })) }, true);
      toast.success("Credit note created");
      nav(`/app/sales/credit-notes/${r.id}`);
    } catch (e: any) { setErr(e.message); toast.error(e.message); }
  };
  return (
    <>
      <PageHeader title="New credit note / sales return" crumbs={[{ label: "Credit notes", to: "/app/sales/credit-notes" }, { label: "New" }]} />
      <SaveError error={err} what="credit note" />
      <div className="card mb-3"><div className="card-body">
        <div className="row">
          <Field label="Customer *" col="col-md-4"><EntitySelect endpoint="/api/v1/customers" value={form.customer} onChange={(v) => setForm({ ...form, customer: v })} mapOption={(c) => ({ id: c.id, label: c.name, sub: c.code })} disabled={Boolean(form.invoice)} /></Field>
          <Field label="Against invoice" col="col-md-3"><EntitySelect endpoint="/api/v1/invoices" query={{ returnable: 1, customer: form.customer || undefined }} value={form.invoice} onChange={(v) => setForm({ ...form, invoice: v })} mapOption={(i) => ({ id: i.id, label: i.number, sub: `${i.customer_name} · balance ${money(i.balance)}` })} placeholder="optional" /></Field>
          <Field label="Restock to warehouse" col="col-md-2"><select className="form-select" value={form.warehouse || wh.defaultId || ""} onChange={(e) => setForm({ ...form, warehouse: e.target.value })}>{!restockable.length && <option value="">—</option>}{restockable.map((w: any) => <option key={w.id} value={w.id}>{w.name}{w.is_default ? " (default)" : ""}</option>)}</select></Field>
          <Field label="Date" col="col-md-2"><DateInput className="form-control" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} /></Field>
          <Field label="Reason" col="col-md-6"><input className="form-control" value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} placeholder="e.g. damaged in transit, wrong shade" /></Field>
          <Field label="Notes" col="col-md-3"><input className="form-control" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} placeholder="optional" /></Field>
          <Field label=" " col="col-md-3"><div className="form-check form-check-md mt-2"><input className="form-check-input" type="checkbox" id="applied" checked={form.applied_to_invoice} onChange={(e) => setForm({ ...form, applied_to_invoice: e.target.checked })} /><label htmlFor="applied" className="form-check-label">Apply to the invoice balance</label></div></Field>
        </div>
        {!form.invoice && <div className="alert alert-info py-2 fs-12 mb-2"><i className="ti ti-info-circle me-1" />No invoice selected — this is a <strong>blind return</strong>: add the returned items below. Use it when the goods weren't sold on a traceable invoice. Good stock goes back on hand; broken or expired stock is written off.</div>}
        <LinesEditor lines={lines} onChange={setLines as any} showDiscount={false} />
        {lines.length > 0 && <div className="table-responsive mt-2"><table className="table table-sm fs-12"><thead className="table-light"><tr><th>Item</th><th>Restock?</th><th>Condition</th><th>Line reason</th></tr></thead><tbody>{lines.map((l) => <tr key={l.key}><td>{l.variant_name || "—"}</td><td><input type="checkbox" className="form-check-input" checked={(l.condition || "good") === "good" ? (l.restock ?? true) : false} disabled={(l.condition || "good") !== "good"} title={(l.condition || "good") !== "good" ? "Broken or expired goods are written off, not put back in stock" : ""} onChange={(e) => setLines(lines.map((x) => (x.key === l.key ? { ...x, restock: e.target.checked } : x)))} /></td><td><select className="form-select form-select-sm" value={l.condition || "good"} onChange={(e) => setLines(lines.map((x) => (x.key === l.key ? { ...x, condition: e.target.value, restock: e.target.value === "good" } : x)))}><option value="good">good</option><option value="damaged">damaged</option><option value="expired">expired</option></select></td><td><input className="form-control form-control-sm" value={l.reason || ""} onChange={(e) => setLines(lines.map((x) => (x.key === l.key ? { ...x, reason: e.target.value } : x)))} /></td></tr>)}</tbody></table></div>}
      </div></div>
      <div className="d-flex gap-2 justify-content-end"><button className="btn btn-light" onClick={() => nav(-1)}>Cancel</button><button className="btn btn-outline-primary" onClick={() => submit(false)}>Save Draft</button><button className="btn btn-primary" onClick={() => submit(true)}>Issue Credit Note</button></div>
    </>
  );
}

export function RepCollectionsPage() {
  const qc = useQueryClient();
  const { can } = usePerms();
  const collectionsPage = usePage("rep_collections");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<any>({ rep: null, customer: null, amount: "", method: "cash", reference: "", notes: "" });
  const unsettled = useQuery({ queryKey: ["rep-unsettled"], queryFn: () => api.get("/api/v1/rep-collections/unsettled"), enabled: collectionsPage.can("settle") });
  const cols: Column<any>[] = [
    { key: "number", header: "#", render: (c) => <span className="fw-semibold text-dark">{c.number}</span> }, { key: "date", header: "Date", render: (c) => date(c.date) }, { key: "sales_person", header: "Sales person", render: (i: any) => i.sales_person || i.rep_name || <span className="text-muted">—</span> }, { key: "customer_name", header: "Customer" },
    { key: "method", header: "Method", render: (c) => <>{c.method}{c.reference ? <div className="text-muted fs-11">{c.reference}</div> : null}</> }, { key: "allocations", header: "Invoices", render: (c) => c.allocations.map((a: any) => a.number).join(", ") || "advance" },
    { key: "amount", header: "Amount", num: true, render: (c) => money(c.amount) }, { key: "status", header: "Status", render: (c) => <><StatusBadge status={c.status} />{c.settlement_number && <div className="fs-11 text-muted">{c.settlement_number}</div>}</> },
  ];
  return (
    <>
      <ListPage title="Rep collections" page="rep_collections" endpoint="/api/v1/rep-collections" columns={cols} defaultOrdering="-date" filters={[{ name: "status", label: "Status", type: "select", options: [{ value: "held_by_rep", label: "Held by rep" }, { value: "settled", label: "Settled" }] }, { name: "rep", label: "Rep", type: "entity", endpoint: "/api/v1/supplier-reps", mapOption: (r) => ({ id: r.id, label: r.name }) }]}
        actions={<>{collectionsPage.can("settle") && <Link to="/app/sales/rep-settlements" className="btn btn-outline-primary"><i className="ti ti-cash-banknote me-1" />Settle</Link>}{collectionsPage.add && <button className="btn btn-primary" onClick={() => setOpen(true)}><i className="ti ti-plus me-1" />Record collection</button>}</>}
        stats={unsettled.data && <div className="row g-3 mb-3">{unsettled.data.by_rep.map((r: any) => <div className="col-6 col-md-3" key={r.rep_id}><StatCard label={`Cash with ${r.rep}`} value={money(r.total, 0)} icon="ti-user-dollar" color="warning" sub={`${r.count} unsettled collection(s)`} /></div>)}{!unsettled.data.by_rep.length && <div className="col-12"><Alert kind="success">No cash held by reps.</Alert></div>}</div>} />
      <Modal open={open} title="Record rep collection (on behalf of a rep)" onClose={() => setOpen(false)} size="md" footer={<><button className="btn btn-light" onClick={() => setOpen(false)}>Cancel</button><button className="btn btn-primary" onClick={async () => { try { await api.post("/api/v1/rep-collections", { ...form, gps: undefined }, true); toast.success("Collection recorded"); setOpen(false); qc.invalidateQueries({ queryKey: ["list", "/api/v1/rep-collections"] }); qc.invalidateQueries({ queryKey: ["rep-unsettled"] }); } catch (e: any) { toast.error(e.message); } }}>Record</button></>}>
        <Field label="Rep *"><EntitySelect endpoint="/api/v1/supplier-reps" query={{ is_active: true }} value={form.rep} onChange={(v) => setForm({ ...form, rep: v })} mapOption={(r) => ({ id: r.id, label: r.name, sub: r.supplier_name })} /></Field>
        <Field label="Customer *"><EntitySelect endpoint="/api/v1/customers" query={{ assigned_reps: form.rep || undefined }} value={form.customer} onChange={(v) => setForm({ ...form, customer: v })} mapOption={(c) => ({ id: c.id, label: c.name, sub: `${c.code} · outstanding ${money(c.outstanding)}` })} /></Field>
        <div className="row"><Field label="Amount *" col="col-6"><input type="number" step="0.01" className="form-control" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} /></Field><Field label="Method" col="col-6"><select className="form-select" value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value })}><option value="cash">Cash</option><option value="cheque">Cheque</option><option value="online">Online</option></select></Field></div>
        <Field label="Reference"><input className="form-control" value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} /></Field>
        <div className="form-text">Allocated to the customer's oldest open invoices (FIFO). The amount sits in Rep Cash Clearing until settled.</div>
      </Modal>
    </>
  );
}

export function RepSettlementsPage() {
  const qc = useQueryClient();
  const { can } = usePerms();
  const settlementsPage = usePage("rep_settlements");
  const [open, setOpen] = useState(false);
  const [rep, setRep] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [cashAccount, setCashAccount] = useState("");
  const cashAccounts = useOptions("/api/v1/accounting/cash-accounts");
  const unsettled = useQuery({ queryKey: ["rep-unsettled", rep], queryFn: () => api.get("/api/v1/rep-collections/unsettled", { rep: rep || undefined }), enabled: open });
  const cols: Column<any>[] = [
    { key: "number", header: "#", render: (s) => <span className="fw-semibold text-dark">{s.number}</span> }, { key: "date", header: "Date", render: (s) => date(s.date) }, { key: "sales_person", header: "Sales person", render: (i: any) => i.sales_person || i.rep_name || <span className="text-muted">—</span> }, { key: "received_by_name", header: "Received by" },
    { key: "collection_numbers", header: "Collections", render: (s) => s.collection_numbers?.map((c: any) => `${c.number} (${c.customer})`).join(", ") }, { key: "amount", header: "Amount", num: true, render: (s) => money(s.amount) }, { key: "journal_number", header: "Journal" },
  ];
  const total = (unsettled.data?.collections || []).filter((c: any) => selected.includes(c.id)).reduce((s: number, c: any) => s + Number(c.amount), 0);
  return (
    <>
      <ListPage title="Rep settlements" page="rep_settlements" crumbs={[{ label: "Rep collections", to: "/app/sales/rep-collections" }, { label: "Settlements" }]} endpoint="/api/v1/rep-settlements" columns={cols} defaultOrdering="-date" actions={settlementsPage.add && <button className="btn btn-primary" onClick={() => setOpen(true)}><i className="ti ti-cash-banknote me-1" />Settle rep cash</button>} />
      <Modal open={open} title="Rep hands over cash" onClose={() => setOpen(false)} size="lg" footer={<><button className="btn btn-light" onClick={() => setOpen(false)}>Cancel</button><button className="btn btn-primary" disabled={!selected.length} onClick={async () => { try { await api.post("/api/v1/rep-settlements", { rep, collections: selected, cash_account: cashAccount || null }, true); toast.success("Settlement recorded"); setOpen(false); setSelected([]); qc.invalidateQueries({ queryKey: ["list", "/api/v1/rep-settlements"] }); qc.invalidateQueries({ queryKey: ["rep-unsettled"] }); } catch (e: any) { toast.error(e.message); } }}>Settle {money(total)}</button></>}>
        <div className="row"><Field label="Rep *" col="col-md-6"><EntitySelect endpoint="/api/v1/supplier-reps" value={rep} onChange={(v) => { setRep(v); setSelected([]); }} mapOption={(r) => ({ id: r.id, label: r.name, sub: r.supplier_name })} /></Field><Field label="Deposit into" col="col-md-6"><select className="form-select" value={cashAccount} onChange={(e) => setCashAccount(e.target.value)}><option value="">Cash in hand</option>{cashAccounts.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field></div>
        {rep && <table className="table table-sm fs-12"><thead className="table-light"><tr><th><input type="checkbox" className="form-check-input" onChange={(e) => setSelected(e.target.checked ? (unsettled.data?.collections || []).map((c: any) => c.id) : [])} /></th><th>Collection</th><th>Date</th><th>Customer</th><th>Method</th><th className="num">Amount</th></tr></thead>
          <tbody>{(unsettled.data?.collections || []).map((c: any) => <tr key={c.id}><td><input type="checkbox" className="form-check-input" checked={selected.includes(c.id)} onChange={(e) => setSelected(e.target.checked ? [...selected, c.id] : selected.filter((x) => x !== c.id))} /></td><td>{c.number}</td><td>{date(c.date)}</td><td>{c.customer_name}</td><td>{c.method}</td><td className="num">{money(c.amount)}</td></tr>)}{unsettled.data && !unsettled.data.collections.length && <tr><td colSpan={6} className="text-muted text-center">Nothing to settle for this rep</td></tr>}</tbody></table>}
      </Modal>
    </>
  );
}
