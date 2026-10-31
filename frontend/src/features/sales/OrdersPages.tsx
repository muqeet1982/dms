import { DateInput } from "@/components/ui/DateInput";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { date, datetime, fbrRateLabel, gstPct, money, otherTaxLabel, qty } from "@/lib/format";
import { useAction, useDoc, useOptions, useWarehouses } from "@/lib/hooks";
import { usePage, usePerms } from "@/lib/perms";
import { toast } from "@/lib/toast";
import { Alert, Field, InfoItem, PageHeader, Spinner, StatCard, StatusBadge, SaveError } from "@/components/ui/Bits";
import { Column } from "@/components/ui/DataTable";
import { EntitySelect } from "@/components/ui/EntitySelect";
import { ListPage } from "@/components/ui/ListPage";
import { ConfirmDialog, Modal } from "@/components/ui/Modal";
import { DocTabs } from "@/components/documents/DocTabs";
import { EditLine, LinesEditor, newLine } from "@/components/documents/LinesEditor";
import { DiscountPctSelect } from "@/components/documents/DiscountPick";
import { PlainFooterRows, TaxFooterRows } from "@/components/documents/TaxFooter";
import { WorkflowStepper } from "@/components/documents/WorkflowStepper";

const STATUS_OPTIONS = ["draft", "submitted", "under_review", "confirmed", "on_hold_credit", "on_hold_stock", "dispatched", "partially_delivered", "delivered", "invoiced", "cancelled", "rejected"].map((s) => ({ value: s, label: s.replace(/_/g, " ") }));

// the picker lives with the other document parts; re-exported for the pages that import it from here
export { DiscountPctSelect };

const far = (m: any) => (m === null || m === undefined ? "" : Number(m) >= 1000 ? `${(Number(m) / 1000).toFixed(1)} km` : `${Math.round(Number(m))} m`);

/**
 * Where the order was taken (owner, 27 Oct 2026: "when order is not created from location then mention it").
 * From the location check the rep app's order passed: at the shop, away from it, no GPS, not checked (the shop has
 * no pin), typed in the office, or no record (orders older than the check). `compact` = the list's marker, shown
 * only when the order was NOT confirmed at the shop.
 */
export function LocationCheck({ check, compact }: { check?: { verdict: string; distance_m?: number | null; gps?: { lat: number; lng: number } | null }; compact?: boolean }) {
  if (!check) return null;
  const map = check.gps ? `https://www.google.com/maps?q=${check.gps.lat},${check.gps.lng}` : null;
  const look: Record<string, { tone: string; icon: string; text: string }> = {
    inside: { tone: "success", icon: "ti-map-pin-check", text: `At the shop${check.distance_m != null ? ` · ${far(check.distance_m)} from its pin` : ""}` },
    outside: { tone: "danger", icon: "ti-map-pin-off", text: `Away from the shop${check.distance_m != null ? ` · ${far(check.distance_m)}` : ""}` },
    no_fix: { tone: "warning", icon: "ti-map-pin-question", text: "No GPS location sent" },
    skipped: { tone: "warning", icon: "ti-map-pin-question", text: "Location not checked — the shop has no pin" },
    unchecked: { tone: "warning", icon: "ti-map-pin-question", text: `Not checked — rep may order anywhere${check.distance_m != null ? ` · taken ${far(check.distance_m)} from the shop` : ""}` },
    office: { tone: "secondary", icon: "ti-building", text: "Entered in the office" },
    unknown: { tone: "secondary", icon: "ti-map-pin", text: "No location record" },
  };
  const l = look[check.verdict] || look.unknown;
  if (compact) {
    if (!["outside", "no_fix", "skipped", "unchecked"].includes(check.verdict)) return null;
    return <i className={`ti ${l.icon} text-${l.tone} fs-13 ms-1`} title={l.text} />;
  }
  return <><span className={`badge bg-${l.tone}-light text-${l.tone}`}><i className={`ti ${l.icon} me-1`} />{l.text}</span>
    {map && <div className="fs-11 mt-1"><a href={map} target="_blank" rel="noreferrer"><i className="ti ti-external-link me-1" />Open on map</a></div>}</>;
}

const orderColumns: Column<any>[] = [
  { key: "number", header: "Order #", sortKey: "number", render: (o) => <><span className="fw-semibold text-dark">{o.number}</span> <i className={`ti ${o.source === "rep_app" ? "ti-device-mobile text-success" : "ti-world text-info"} fs-12`} title={o.source === "rep_app" ? "Taken on the mobile app" : "Entered on the web"} /><LocationCheck check={o.location_check} compact /></> },
  { key: "order_date", header: "Date", sortKey: "order_date", render: (o) => date(o.order_date) },
  { key: "customer_name", header: "Customer", render: (o) => <><div>{o.customer_name}</div><div className="text-muted fs-11">{o.customer_code}</div></> },
  { key: "sales_person", header: "Sales person", render: (o) => o.sales_person || o.rep_name || <span className="text-muted">—</span> },
  { key: "trip_number", header: "Trip", render: (o) => o.trip_number || "—" },
  { key: "total", header: "Total", num: true, sortKey: "total", render: (o) => <span className="fw-semibold">{money(o.total)}</span> },
  { key: "status", header: "Status", sortKey: "status", render: (o) => <><StatusBadge status={o.status} />{o.delivery_status && <div className="fs-11 text-muted mt-1"><i className="ti ti-truck me-1" />{o.delivery_status}</div>}{o.hold_reason && <div className="text-warning fs-11 mt-1" style={{ maxWidth: 220 }}>{o.hold_reason}</div>}</> },
];

export function OrdersPage() {
  const stats = useQuery({ queryKey: ["orders-stats"], queryFn: () => api.get("/api/v1/sales-orders/stats") });
  return (
    <ListPage title="Sales orders" endpoint="/api/v1/sales-orders" columns={orderColumns} rowLink={(o) => `/app/sales/orders/${o.id}`} page="sales_orders" createTo="/app/sales/orders/new" createLabel="New order" defaultOrdering="-order_date" searchPlaceholder="Search number, customer, external ref…"
      filters={[{ name: "status", label: "Status", type: "select", options: STATUS_OPTIONS }, { name: "customer", label: "Customer", type: "entity", endpoint: "/api/v1/customers", mapOption: (c) => ({ id: c.id, label: c.name, sub: c.code }) }, { name: "rep", label: "Rep", type: "entity", endpoint: "/api/v1/supplier-reps", mapOption: (r) => ({ id: r.id, label: r.name, sub: r.supplier_name }) }, { name: "date_from", label: "From", type: "date" }, { name: "date_to", label: "To", type: "date" }]}
      stats={stats.data && <div className="row g-3 mb-3"><div className="col-6 col-md-3"><StatCard label="Inbox" value={stats.data.inbox} icon="ti-inbox" color="warning" to="/app/sales/inbox" /></div><div className="col-6 col-md-3"><StatCard label="Confirmed (to dispatch)" value={stats.data.by_status.confirmed || 0} icon="ti-circle-check" color="primary" /></div><div className="col-6 col-md-3"><StatCard label="Dispatched" value={stats.data.dispatched} icon="ti-truck" color="purple" /></div><div className="col-6 col-md-3"><StatCard label="Today" value={stats.data.today} icon="ti-calendar" color="success" sub={money(stats.data.today_value)} /></div></div>} />
  );
}

export function OrdersInboxPage() {
  return (
    <ListPage title="Orders inbox" crumbs={[{ label: "Sales orders", to: "/app/sales/orders" }, { label: "Inbox" }]} endpoint="/api/v1/sales-orders/inbox" columns={orderColumns} rowLink={(o) => `/app/sales/orders/${o.id}`} defaultOrdering="created_at" searchPlaceholder="Search…"
      filters={[{ name: "status", label: "Status", type: "select", options: ["submitted", "under_review", "on_hold_credit", "on_hold_stock"].map((s) => ({ value: s, label: s.replace(/_/g, " ") })) }, { name: "rep", label: "Rep", type: "entity", endpoint: "/api/v1/supplier-reps", mapOption: (r) => ({ id: r.id, label: r.name }) }]}>
      <Alert kind="info">Orders submitted by manufacturer reps, the external API or staff land here. Review, adjust prices/quantities if allowed, then confirm, hold or reject. The rep is notified on every change.</Alert>
    </ListPage>
  );
}

export function OrderDetailPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { can, isOwner } = usePerms();
  const page = usePage("sales_orders");
  const notesPage = usePage("delivery_notes");
  const tripsPage = usePage("trips");
  const q = useDoc("/api/v1/sales-orders", id);
  const { run, busy } = useAction("/api/v1/sales-orders", id, [["orders-stats"], ["orders-stats-badge"], ["dashboard"]]);
  const [dlg, setDlg] = useState<null | "reject" | "cancel" | "hold" | "deliver" | "confirm" | "delete" | "rejected" | "again">(null);
  const [deliver, setDeliver] = useState({ receiver_name: "", cash_collected: "" });
  const avail = useQuery({ queryKey: ["availability", id], queryFn: () => api.get(`/api/v1/sales-orders/${id}/availability`), enabled: Boolean(q.data && ["submitted", "under_review", "on_hold_stock", "on_hold_credit", "confirmed"].includes(q.data.status)) });
  if (q.isLoading) return <Spinner />;
  const o = q.data;
  if (!o) return null;
  // confirmed is editable until the warehouse starts on it — the server refuses once a load form exists
  // past confirmation an order is the owner's to change — editing issued/dispatched work pulls the goods
  // back into stock and cancels the load form / delivery note, then the office re-issues the edited lines
  const editable = ["draft", "submitted", "under_review", "on_hold_credit", "on_hold_stock"].includes(o.status)
    || (["confirmed", "issued", "dispatched"].includes(o.status) && (Boolean(isOwner) || can("sales.edit_confirmed_order")) && !(o.invoices || []).length);
  const confirmable = ["submitted", "under_review", "on_hold_credit", "on_hold_stock", "draft"].includes(o.status);
  // a stop that did not reach the customer waits for the office: Deliver again (same goods, same invoice) or
  // Customer rejected — a second delivery beside it would bill the order twice
  const failedDn = ["issued", "dispatched", "confirmed"].includes(o.status) ? (o.delivery_notes || []).find((d: any) => ["failed", "returned"].includes(d.status)) : null;
  const deliverAgain = async (reason?: string) => {
    try {
      await api.post(`/api/v1/delivery-notes/${failedDn.id}/deliver-again`, { reason: reason || "" }, true);
      toast.success(`${failedDn.number} goes out again`);
      qc.invalidateQueries({ queryKey: ["doc", "/api/v1/sales-orders", id] });
      setDlg(null);
    } catch (e: any) { toast.error(e.message); }
  };
  return (
    <>
      <PageHeader title={o.number} badge={<><StatusBadge status={o.status} />{o.delivery_status && <span className="badge bg-purple-light text-purple ms-1"><i className="ti ti-truck me-1" />{o.delivery_status}</span>}</>} crumbs={[{ label: "Sales orders", to: "/app/sales/orders" }, { label: o.number }]}
        actions={<>
          {editable && page.edit && <Link to={`/app/sales/orders/${o.id}/edit`} className="btn btn-outline-primary"><i className="ti ti-edit me-1" />Edit</Link>}
          {o.status === "draft" && page.add && <button className="btn btn-primary" disabled={!!busy} onClick={() => run("submit")}>Submit</button>}
          {o.status === "submitted" && page.can("confirm") && <button className="btn btn-outline-info" disabled={!!busy} onClick={() => run("review")}>Mark under review</button>}
          {confirmable && page.can("confirm") && <button className="btn btn-success" disabled={!!busy} onClick={() => (o.status === "on_hold_credit" ? setDlg("confirm") : run("confirm", {}, { success: "Order confirmed" }))}><i className="ti ti-circle-check me-1" />Confirm</button>}
          {confirmable && page.can("confirm") && <button className="btn btn-outline-warning" disabled={!!busy} onClick={() => setDlg("hold")}>Hold</button>}
          {confirmable && page.can("hold") && <button className="btn btn-outline-danger" disabled={!!busy} onClick={() => setDlg("reject")}>Reject</button>}
          {failedDn && notesPage.can("complete") && <button className="btn btn-primary" disabled={!!busy} onClick={() => setDlg("again")}><i className="ti ti-truck-return me-1" />Deliver again</button>}
          {["confirmed", "dispatched", "partially_delivered"].includes(o.status) && !failedDn && notesPage.can("complete") && <button className="btn btn-primary" onClick={() => setDlg("deliver")}><i className="ti ti-package-export me-1" />Mark delivered</button>}
          {o.status === "confirmed" && !o.trip && !failedDn && tripsPage.add && <Link to={`/app/delivery/trips/new?order=${o.id}&warehouse=${o.warehouse}`} className="btn btn-outline-primary"><i className="ti ti-truck me-1" />Add to trip</Link>}
          {o.trip && <Link to={`/app/delivery/trips/${o.trip}`} className="btn btn-outline-secondary"><i className="ti ti-truck me-1" />View trip {o.trip_number}</Link>}
          {(["draft", "submitted", "under_review"].includes(o.status) ? page.delete
   : ["confirmed", "on_hold_credit", "on_hold_stock", "issued", "dispatched"].includes(o.status) ? (isOwner || can("sales.cancel_confirmed_order"))
   : false) && <button className="btn btn-outline-danger" disabled={!!busy} onClick={() => setDlg("cancel")}>Cancel</button>}{(["issued", "dispatched"].includes(o.status) || failedDn) && (isOwner || can("sales.cancel_confirmed_order")) && <button className="btn btn-outline-danger" disabled={!!busy} onClick={() => setDlg("rejected")}><i className="ti ti-hand-stop me-1" />Customer rejected</button>}{["draft", "submitted", "under_review", "rejected"].includes(o.status) && page.delete && <button className="btn btn-outline-danger" disabled={!!busy} onClick={() => setDlg("delete")}><i className="ti ti-trash me-1" />Delete</button>}
          <button className="btn btn-outline-secondary" onClick={() => api.printDoc(`/api/v1/sales-orders/${o.id}/pdf`)}><i className="ti ti-printer me-1" />Print</button>
          <button className="btn btn-outline-secondary" onClick={() => api.download(`/api/v1/sales-orders/${o.id}/pdf`)}><i className="ti ti-file-type-pdf me-1" />PDF</button>
        </>} />
      {o.hold_reason && <Alert kind="warning">{o.hold_reason}</Alert>}
      {o.rejection_reason && <Alert kind="danger">{o.rejection_reason}</Alert>}
      {!["draft", "cancelled", "rejected"].includes(o.status) && <WorkflowStepper orderId={o.id} />}
      {o.price_flags?.length > 0 && <Alert kind="warning">Price differences flagged: {o.price_flags.map((f: any) => `${f.variant} requested ${money(f.requested)} vs list ${money(f.list)}`).join("; ")}</Alert>}
      {avail.data && !avail.data.ok && <Alert kind="warning">Stock short: {avail.data.rows.filter((r: any) => Number(r.short) > 0).map((r: any) => `${r.sku} short ${qty(r.short)}`).join(", ")}</Alert>}
      <div className="row g-3">
        <div className="col-xl-9">
          <div className="card mb-3"><div className="card-header"><strong>Order details</strong></div>
            <div className="card-body">
              <div className="row g-3 mb-3">
                <InfoItem label="Customer"><Link to={`/app/customers/${o.customer}`}>{o.customer_name}</Link><div className="text-muted fs-12">{o.customer_phone} · {o.customer_address}</div></InfoItem>
                <InfoItem label="Sales person / supplier" col="col-md-2">{o.sales_person || o.rep_name || "—"}<div className="text-muted fs-12">{o.supplier_name}</div></InfoItem>
                <InfoItem label="Taken at" col="col-md-2"><LocationCheck check={o.location_check} /></InfoItem>
                <InfoItem label="Dates" col="col-md-2">{date(o.order_date)}<div className="text-muted fs-12">deliver by {date(o.requested_delivery_date)}</div></InfoItem>
                <InfoItem label="Warehouse" col="col-md-2">{o.warehouse_name}</InfoItem><InfoItem label="Payment term" col="col-md-2">{o.payment_terms || "—"}</InfoItem><InfoItem label="Delivery person" col="col-md-2">{o.delivery_person || "—"}</InfoItem>
                {o.charge_tax && <InfoItem label="Tax" col="col-md-2"><span className="badge bg-success-light text-success"><i className="ti ti-percentage me-1" />Tax invoice — FBR rates</span></InfoItem>}
                <InfoItem label="Trip" col="col-md-3">{o.trip_number ? <Link to={`/app/delivery/trips/${o.trip}`}>{o.trip_number}</Link> : "—"}{o.driver_name && <div className="text-muted fs-12">{o.driver_name} · {o.vehicle_reg}</div>}</InfoItem>
              </div>
              <div className="table-responsive">
                {(() => {
                  /* a tax-invoice order reads like the invoice that will bill it (owner, 26 Oct 2026):
                     Taxable = Qty × Price − Disc is what the GST is computed on, Total = Taxable + GST */
                  // the tax face follows the customer's flag alone (owner 26 Oct: "if customer tax is off then do not show")
                  const taxed = Boolean(o.charge_tax);
                  const otherOf = (l: any) => (Number(l.fed_amount) || 0) + (Number(l.extra_tax_amount) || 0) + (Number(l.further_tax_amount) || 0);
                  // FED / Extra / Further get a column once any line carries them
                  const showOther = taxed && o.lines.some((l: any) => otherOf(l) > 0);
                  const span = taxed ? 9 + (showOther ? 1 : 0) : 6;
                  const taxTotal = (Number(o.tax1_total) || 0) + (Number(o.tax2_total) || 0);
                  return <table className="table table-sm align-middle mb-0 fs-12" style={{ minWidth: taxed ? 900 : 760 }}>
                    <thead className="table-light"><tr><th>#</th><th>Item</th><th className="num">Qty</th><th className="num">Disp.</th><th className="num">Deliv.</th><th className="num">Price</th>{taxed && <th className="num">Disc</th>}{taxed && <th className="num">Taxable</th>}{taxed && <th className="num">GST</th>}{showOther && <th className="num" title="FED + Extra Tax + Further Tax">Other Tax</th>}<th className="num">{taxed ? "Line Total" : "Amount"}</th></tr></thead>
                    <tbody>{o.lines.map((l: any, i: number) => { const taxAmt = (Number(l.tax1_amount) || 0) + (Number(l.tax2_amount) || 0); return <tr key={l.id}><td>{i + 1}</td><td>{l.variant_name}<div className="text-muted fs-11">{l.variant_sku}{taxed && l.hs_code ? ` · HS ${l.hs_code}` : ""}{l.packaging_name ? ` · ${l.packaging_name}` : ""}{l.scheme_names?.length ? ` · ${l.scheme_names.join(", ")}` : ""}{Number(l.qty_allocated) > 0 ? ` · reserved ${qty(l.qty_allocated)}` : ""}</div></td><td className="num">{qty(l.qty_ordered)}</td><td className="num">{qty(l.qty_dispatched)}</td><td className="num">{qty(l.qty_delivered)}</td><td className="num">{money(l.unit_price)}{l.requested_price && Number(l.requested_price) !== Number(l.unit_price) && <div className="text-warning fs-11">req {money(l.requested_price)}</div>}</td>{taxed && <td className="num">{money(Number(l.discount_amount) + Number(l.scheme_discount))}</td>}{taxed && <td className="num">{money(Number(l.line_total) - taxAmt - otherOf(l))}</td>}{taxed && <td className="num">{taxAmt ? <>{money(taxAmt)}<div className="text-muted fs-11">{l.product_fbr_rate ? fbrRateLabel(l.product_fbr_rate) : `${Number(l.tax1_rate)}%`}</div></> : "—"}</td>}{showOther && <td className="num">{otherOf(l) ? <>{money(otherOf(l))}<div className="text-muted fs-11">{otherTaxLabel({ fed: l.product_fed_rate, extra: l.product_extra_tax_rate, further: l.product_further_tax_rate })}</div></> : "—"}</td>}<td className="num fw-medium">{money(taxed ? l.line_total : Number(l.qty_ordered) * Number(l.unit_price))}</td></tr>; })}</tbody>
                    <tfoot>{taxed
                      ? <TaxFooterRows span={span} sub={Number(o.subtotal)} discount={Number(o.discount)} discountPct={o.discount_pct} scheme={Number(o.scheme_discount)}
                          net={Number(o.subtotal) - Number(o.discount) - Number(o.scheme_discount)} gst={taxTotal} gstPct={gstPct(o.lines.map((l: any) => l.tax1_rate))} fed={Number(o.fed_total)} extra={Number(o.extra_tax_total)}
                          further={Number(o.further_tax_total)} wht={Number(o.wht_total)} advance={Number(o.advance_tax_total || 0)} advanceRate={o.advance_tax_rate} payableLabel={`Total Payable (${o.currency})`} />
                      : <PlainFooterRows span={span} sub={Number(o.subtotal)} discount={Number(o.discount) + Number(o.scheme_discount)} discountPct={o.discount_pct} payable={Number(o.total)} advance={Number(o.advance_tax_total || 0)} advanceRate={o.advance_tax_rate} payableLabel={`Total Payable (${o.currency})`} />}</tfoot>
                  </table>;
                })()}
              </div>
              {o.notes && <p className="text-muted fs-13 mt-2 mb-0"><i className="ti ti-note me-1" />{o.notes}</p>}
            </div></div>
          <DocTabs model="SalesOrder" id={o.id} showMovements extra={[{ key: "deliveries", label: "Deliveries & invoices", render: () => (
            <div className="row g-3"><div className="col-md-6"><h6 className="fs-13">Delivery notes</h6>{!o.delivery_notes.length && <p className="text-muted fs-13">None yet</p>}<ul className="list-group">{o.delivery_notes.map((d: any) => <li key={d.id} className="list-group-item d-flex justify-content-between"><Link to={`/app/delivery/notes/${d.id}`}>{d.number}</Link><span className="fs-12 text-muted">{d.trip || "no trip"} · {datetime(d.delivered_at)}</span><StatusBadge status={d.status} /></li>)}</ul></div>
              <div className="col-md-6"><h6 className="fs-13">Invoices</h6>{!o.invoices.length && <p className="text-muted fs-13">None yet</p>}<ul className="list-group">{o.invoices.map((i: any) => <li key={i.id} className="list-group-item d-flex justify-content-between"><Link to={`/app/sales/invoices/${i.id}`}>{i.number}</Link><span className="fs-12">{money(i.total)} · paid {money(i.paid_amount)}</span><StatusBadge status={i.status} /></li>)}</ul></div></div>) }]} />
        </div>
        <div className="col-xl-3">
          <div className="card mb-3"><div className="card-header"><strong>Timeline</strong></div><div className="card-body">
            <ul className="status-timeline mb-0">
              <li><div className="fs-13 fw-medium">Created</div><div className="fs-12 text-muted">{datetime(o.created_at)} · {o.created_by_name || o.source}</div></li>
              {o.confirmed_at && <li><div className="fs-13 fw-medium">Confirmed</div><div className="fs-12 text-muted">{datetime(o.confirmed_at)} · {o.confirmed_by_name}</div></li>}
              {o.dispatched_at && <li><div className="fs-13 fw-medium">Dispatched (stock out)</div><div className="fs-12 text-muted">{datetime(o.dispatched_at)}</div></li>}
              {o.delivered_at && <li><div className="fs-13 fw-medium">Delivered</div><div className="fs-12 text-muted">{datetime(o.delivered_at)}</div></li>}
            </ul>
          </div></div>
          {avail.data && <div className="card"><div className="card-header"><strong>Availability</strong> <span className={`badge ms-2 ${avail.data.ok ? "bg-success" : "bg-warning"}`}>{avail.data.ok ? "in stock" : "short"}</span></div><div className="card-body p-0"><table className="table table-sm fs-12 mb-0"><tbody>{avail.data.rows.map((r: any) => <tr key={r.line_id}><td>{r.sku}</td><td className="num">{qty(r.ordered)}</td><td className={`num ${Number(r.short) > 0 ? "text-danger" : "text-success"}`}>{qty(r.available)}</td></tr>)}</tbody></table></div></div>}
        </div>
      </div>
      <ConfirmDialog open={dlg === "reject"} title="Reject order" withReason danger confirmText="Reject" onClose={() => setDlg(null)} onConfirm={async (r) => { await run("reject", { reason: r }); setDlg(null); }} />
      <ConfirmDialog open={dlg === "delete"} title={`Delete ${o.number}?`} danger confirmText="Delete order" message="This removes the order completely. Only an order that has not been confirmed, delivered or invoiced can be deleted — otherwise cancel it instead." onClose={() => setDlg(null)} onConfirm={async () => {
        try { await api.delete(`/api/v1/sales-orders/${o.id}`); toast.success(`${o.number} deleted`); qc.invalidateQueries({ queryKey: ["list", "/api/v1/sales-orders"] }); nav("/app/sales/orders"); }
        catch (e: any) { toast.error(e.message); }
      }} />
      {failedDn && <ConfirmDialog open={dlg === "again"} title={`Deliver ${failedDn.number} again`} withReason confirmText="Deliver again"
        message={`The same goods and the same invoice${failedDn.invoice_number ? ` (${failedDn.invoice_number})` : ""} go out again — on the next trip, or deliver it from its delivery note.`}
        onClose={() => setDlg(null)} onConfirm={deliverAgain} />}
      <ConfirmDialog open={dlg === "rejected"} title="Customer rejected the goods" withReason danger confirmText="Reverse the sale"
        message="The goods come back to stock once, the order's unpaid invoice is reversed (its entries come off the books) and the order closes as Rejected. No return and no payment are recorded."
        onClose={() => setDlg(null)} onConfirm={async (r) => { await run("customer-rejected", { reason: r }); setDlg(null); }} />
      <ConfirmDialog open={dlg === "cancel"} title="Cancel order" message={["issued", "dispatched"].includes(o.status) ? "The goods the warehouse issued come back into stock, and the load form and delivery note are cancelled with the order." : undefined} withReason danger confirmText="Cancel order" onClose={() => setDlg(null)} onConfirm={async (r) => { await run("cancel", { reason: r }); setDlg(null); }} />
      <ConfirmDialog open={dlg === "hold"} title="Put on hold" withReason confirmText="Hold" onClose={() => setDlg(null)} onConfirm={async (r) => { await run("hold", { reason: r }); setDlg(null); }} />
      <ConfirmDialog open={dlg === "confirm"} title="Override credit hold?" message={<>{o.hold_reason}<br />Confirming will override the credit check.</>} confirmText="Override & confirm" onClose={() => setDlg(null)} onConfirm={async () => { await run("confirm", { override_credit: true }); setDlg(null); }} />
      <Modal open={dlg === "deliver"} title={`Mark ${o.number} delivered`} onClose={() => setDlg(null)} size="sm" footer={<><button className="btn btn-light" onClick={() => setDlg(null)}>Cancel</button><button className="btn btn-primary" disabled={!!busy} onClick={async () => { await run("mark-delivered", { receiver_name: deliver.receiver_name, cash_collected: deliver.cash_collected || 0 }, { success: "Delivered — review the draft invoice, then post it" }); setDlg(null); }}>Mark delivered</button></>}>
        <p className="fs-13 text-muted">All remaining quantities will be delivered now without a trip: stock leaves the warehouse (if not already dispatched) and a delivery note is created. An invoice not raised yet is saved as a draft for you to review and post.</p>
        <Field label="Receiver name"><input className="form-control" value={deliver.receiver_name} onChange={(e) => setDeliver({ ...deliver, receiver_name: e.target.value })} /></Field>
        <Field label="Cash collected now" hint="Receipted on the customer — applied to the invoice once it is posted"><input type="number" step="0.01" className="form-control" value={deliver.cash_collected} onChange={(e) => setDeliver({ ...deliver, cash_collected: e.target.value })} /></Field>
      </Modal>
    </>
  );
}


/** The order's sale person comes from the customer's linked rep; it is changed on the customer, not per order. */
function SalePersonAuto({ customer, editingRep }: { customer: string | null; editingRep?: string | null }) {
  const c = useDoc("/api/v1/customers", customer || undefined);
  const name = c.data?.rep_names?.[0];
  return (
    <div className="form-control-plaintext fs-13">
      {name ? <span className="fw-medium">{name}</span> : customer ? <span className="text-muted">none linked{" "}{c.data && <Link to={`/app/customers/${customer}/edit`} className="fs-12">set on customer</Link>}</span> : <span className="text-muted">pick a customer</span>}
    </div>
  );
}

export function OrderFormPage() {
  const [err, setErr] = useState<string | null>(null);
  const { id } = useParams();
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const { can } = usePerms();
  const page = usePage("sales_orders");
  const existing = useDoc("/api/v1/sales-orders", id);
  const warehouses = useWarehouses();
  const [form, setForm] = useState<any>(null);
  const [lines, setLines] = useState<EditLine[]>([]);
  // Most orders have no discount on the line, so the column is out of the way until somebody asks for it —
  // and it shows itself when an order being edited already carries one.
  const [showDiscount, setShowDiscount] = useState(false);
  const [saving, setSaving] = useState(false);
  // the tax-invoice choice lives on the CUSTOMER (owner, 26 Oct 2026) and the order inherits it — the form
  // fetches the flag so GST previews on each line as items are picked; an existing order keeps its own flag
  const cust = useDoc("/api/v1/customers", form?.customer || undefined);
  const chargeTax = Boolean(id ? existing.data?.charge_tax : cust.data?.charge_tax);
  if (id && existing.isLoading) return <Spinner />;
  if (!form) {
    const o = existing.data;
    setForm(o ? { customer: o.customer, warehouse: o.warehouse, rep: o.rep, order_date: o.order_date, requested_delivery_date: o.requested_delivery_date || "", notes: o.notes || "", version: o.version, apply_customer_discount: true, discount_pct: o.discount_pct != null ? String(Number(o.discount_pct)) : (o.apply_customer_discount ? "" : "0") } : { customer: sp.get("customer") || null, warehouse: "", rep: null, order_date: new Date().toISOString().slice(0, 10), requested_delivery_date: "", notes: "", apply_customer_discount: true, discount_pct: "" });
    if (o) setLines(o.lines.map((l: any) => ({ key: l.id, variant: l.variant, variant_name: l.variant_name, sku: l.variant_sku, packaging: l.packaging, packagings: [], qty: String(l.packaging ? Number(l.qty_ordered) / 1 : Number(l.qty_ordered)), unit_price: l.unit_price, discount_pct: l.discount_pct, extra: { fbr_rate: l.product_fbr_rate || "", fbr_sale_type: l.product_fbr_sale_type || "", fbr_mrp: l.product_mrp, fbr_others: { fed: l.product_fed_rate, extra: l.product_extra_tax_rate, further: l.product_further_tax_rate, wht: l.product_wht_rate } } })));
    return <Spinner />;
  }
  const submit = async (confirm: boolean) => {
    if (!form.customer) return toast.error("Select a customer");
    const valid = lines.filter((l) => l.variant && Number(l.qty) > 0);
    if (!valid.length) return toast.error("Add at least one line");
    setSaving(true);
    try {
      const body = { ...form, warehouse: form.warehouse || null, confirm, discount_pct: form.discount_pct === "" ? null : form.discount_pct, lines: valid.map((l) => ({ variant: l.variant, packaging: l.packaging || null, qty: l.qty, unit_price: l.unit_price || null, discount_pct: l.discount_pct || 0, product_discount_pct: l.product_discount_pct === undefined || l.product_discount_pct === "" ? undefined : l.product_discount_pct, price_per_packaging: Boolean(l.packaging) })) };
      const r = id ? await api.patch(`/api/v1/sales-orders/${id}`, body) : await api.post("/api/v1/sales-orders", body, true);
      toast.success(id ? "Order updated" : "Order created");
      nav(`/app/sales/orders/${r.id}`);
    } catch (e: any) { setErr(e.message); toast.error(e.message); } finally { setSaving(false); }
  };
  return (
    <>
      <PageHeader title={id ? `Edit ${existing.data?.number}` : "New sales order"} crumbs={[{ label: "Sales orders", to: "/app/sales/orders" }, { label: id ? "Edit" : "New" }]} />
      <SaveError error={err} what="order" />
      <div className="card mb-3"><div className="card-body">
        <div className="row">
          <Field label="Customer *" col="col-md-4"><EntitySelect endpoint="/api/v1/customers" query={{ status: "active" }} value={form.customer} onChange={(v, raw) => setForm({ ...form, customer: v, discount_pct: raw ? String(Number(raw.default_discount_pct) || 0) : form.discount_pct })} mapOption={(c) => ({ id: c.id, label: c.name, sub: `${c.code} · ${c.city || ""} · outstanding ${money(c.outstanding)}`, raw: c })} /></Field>
          <Field label="Warehouse" col="col-md-2"><select className="form-select" value={form.warehouse || warehouses.defaultId || ""} onChange={(e) => setForm({ ...form, warehouse: e.target.value })}>{!warehouses.rows.length && <option value="">—</option>}{warehouses.rows.map((w: any) => <option key={w.id} value={w.id}>{w.name}{w.is_default ? " (default)" : ""}</option>)}</select></Field>
          <Field label="Sale person" col="col-md-2" hint="linked on the customer — change it there"><SalePersonAuto customer={form.customer} editingRep={form.rep} /></Field>
          <Field label="Order date" col="col-md-2"><DateInput className="form-control" value={form.order_date} onChange={(e) => setForm({ ...form, order_date: e.target.value })} /></Field>
          <Field label="Requested delivery" col="col-md-2"><DateInput className="form-control" value={form.requested_delivery_date} onChange={(e) => setForm({ ...form, requested_delivery_date: e.target.value })} /></Field>
        </div>
        {chargeTax && <div className="mb-2"><span className="badge bg-success-light text-success"><i className="ti ti-percentage me-1" />Tax invoice — FBR rates</span><span className="text-muted fs-12 ms-2">this customer is charged sales tax — GST shows on each line as you add items</span></div>}
        <LinesEditor lines={lines} onChange={setLines} customerId={form.customer} advanceRate={cust.data?.advance_income_tax_rate} showDiscount={page.can("override_discount") && (showDiscount || lines.some((l) => Number(l.discount_pct || 0) || Number((l as any).discount_amount || 0)))} onToggleDiscount={page.can("override_discount") ? () => setShowDiscount((v) => !v) : undefined} discountShown={showDiscount} variantQuery={{ with_stock: 1 }} blockOutOfStock chargeTax={chargeTax} defaultDiscountPct={form.discount_pct !== "" && form.discount_pct != null ? form.discount_pct : cust.data?.default_discount_pct || 0} />
        {/* the tax-invoice choice lives on the CUSTOMER (owner, 26 Oct 2026) — the order inherits it */}
        <Field label="Discount %" col="col-md-4" hint="seeded from the customer's standing rate — change it for this order; applies to lines with no discount of their own">
          <DiscountPctSelect value={form.discount_pct ?? ""} onChange={(v) => setForm({ ...form, discount_pct: v })} autoLabel="Customer's standing rate (auto)" />
        </Field>
        <Field label="Notes"><textarea className="form-control" rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
        {!page.can("override_price") && <div className="form-text">Prices you enter are advisory — the price list applies unless you have the price-override permission.</div>}
      </div></div>
      <div className="d-flex gap-2 justify-content-end">
        <button className="btn btn-light" onClick={() => nav(-1)}>Cancel</button>
        <button className="btn btn-outline-primary" disabled={saving} onClick={() => submit(false)}>{id ? "Save changes" : "Save & submit"}</button>
        {!id && page.can("confirm") && <button className="btn btn-primary" disabled={saving} onClick={() => submit(true)}>Save & confirm</button>}
      </div>
    </>
  );
}
