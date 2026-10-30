import { DateInput } from "@/components/ui/DateInput";
import { DiscountPctSelect } from "@/features/sales/OrdersPages";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { date, datetime, fbrLineTax, fbrRateLabel, gstPct, gstPctOfLabels, LineTax, money, otherTaxLabel, qty } from "@/lib/format";
import { PlainFooterRows, TaxFooterRows } from "@/components/documents/TaxFooter";
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
import { CreditChoice, CreditOptions, creditBody, emptyCreditChoice } from "@/components/documents/CreditOptions";

export function InvoicesPage() {
  const stats = useQuery({ queryKey: ["invoice-stats"], queryFn: () => api.get("/api/v1/invoices/stats") });
  const cols: Column<any>[] = [
    { key: "number", header: "Invoice #", sortKey: "number", render: (i) => <span className="fw-semibold text-dark">{i.number}</span> },
    { key: "date", header: "Date", sortKey: "date", render: (i) => <>{date(i.date)}{i.due_date && i.due_date !== i.date && <div className="text-muted fs-11">due {date(i.due_date)}</div>}</> },
    { key: "customer_name", header: "Customer", render: (i) => <>{i.customer_name}<div className="text-muted fs-11">{i.customer_code}</div></> },
    { key: "order_number", header: "Order / DN", render: (i) => <>{i.order_number}<div className="text-muted fs-11">{i.delivery_note_number}</div></> },
    { key: "sales_person", header: "Sales person", render: (i: any) => i.sales_person || i.rep_name || <span className="text-muted">—</span> },
    { key: "total", header: "Total", num: true, sortKey: "total", render: (i) => money(i.total) },
    // a return is not a payment (owner, 30 Oct 2026): Paid is money received; what returns settled shows under it
    { key: "paid_amount", header: "Paid", num: true, render: (i) => <>{money(i.cash_paid ?? i.paid_amount)}{Number(i.credited_amount) > 0 && <div className="fs-11 text-warning">returned {money(i.credited_amount)}</div>}</> },
    { key: "balance", header: "Balance", num: true, render: (i) => <span className={Number(i.balance) > 0 ? "fw-semibold text-danger" : "text-success"}>{money(i.balance)}</span> },
    { key: "gst_pct", header: "FBR", render: (i) => i.gst_pct ? <>{i.gst_pct}{i.fbr?.status === "submitted" && <div className="fs-11 text-success">filed</div>}</> : <span className="text-muted">—</span> },
    { key: "status", header: "Status", render: (i) => <StatusBadge status={i.status} /> },
  ];
  return (
    <ListPage title="Sales invoices" page="invoices" endpoint="/api/v1/invoices" columns={cols} rowLink={(i) => `/app/sales/invoices/${i.id}`} createTo="/app/sales/invoices/new" createLabel="New invoice" defaultOrdering="-date" searchPlaceholder="Search invoice, customer, order…"
      filters={[{ name: "status", label: "Status", type: "select", options: ["draft", "posted", "partially_paid", "paid", "overdue", "cancelled"].map((s) => ({ value: s, label: s === "draft" ? "draft — to review & post" : s.replace(/_/g, " ") })) }, { name: "customer", label: "Customer", type: "entity", endpoint: "/api/v1/customers", mapOption: (c) => ({ id: c.id, label: c.name, sub: c.code }) }, { name: "customer_type", label: "Customer type", type: "select", options: [["retailer", "Retailer"], ["salon", "Salon"], ["wholesaler", "Wholesaler"], ["lmt", "LMT"], ["other", "Other"]].map(([value, label]) => ({ value, label })) }, { name: "open", label: "Open only", type: "toggle" }, { name: "overdue", label: "Overdue only", type: "toggle" }, { name: "date_from", label: "From", type: "date" }, { name: "date_to", label: "To", type: "date" }]}
      stats={stats.data && <div className="row g-3 mb-3"><div className="col-6 col-md-3"><StatCard label="Open invoices" value={stats.data.open_count} icon="ti-file-invoice" color="primary" /></div><div className="col-6 col-md-3"><StatCard label="Outstanding" value={money(stats.data.outstanding, 0)} icon="ti-receipt" color="warning" /></div><div className="col-6 col-md-3"><StatCard label="Overdue" value={money(stats.data.overdue, 0)} icon="ti-alert-circle" color="danger" /></div><div className="col-6 col-md-3"><StatCard label="Sales this month" value={money(stats.data.month_sales, 0)} icon="ti-chart-line" color="success" /></div></div>} />
  );
}

export function InvoiceDetailPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const { can, isOwner } = usePerms();
  const page = usePage("invoices");
  const creditNotes = usePage("credit_notes");
  const q = useDoc("/api/v1/invoices", id);
  const pays = useQuery({ queryKey: ["invoice-payments", id], queryFn: () => api.get(`/api/v1/invoices/${id}/payments`), enabled: Boolean(id) });
  const { run, busy } = useAction("/api/v1/invoices", id);
  const [cancel, setCancel] = useState(false);
  const [reopen, setReopen] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const contribs = useQuery({ queryKey: ["discount-rates"], queryFn: () => api.get("/api/v1/settings/discount-contributions/rates"), enabled: Boolean(id) });
  // What a posted invoice still allows: when it falls due, and what is written on it. The amounts belong to the
  // ledger — the dialog says so, because "Edit" that silently refuses to edit the figures is worse than no Edit.
  const [edit, setEdit] = useState<{ due_date: string; notes: string; date: string; lines: any[] } | null>(null);
  const [saving, setSaving] = useState(false);
  const [taxPct, setTaxPct] = useState("");
  const qc = useQueryClient();
  if (q.isLoading) return <Spinner />;
  const inv = q.data;
  if (!inv) return null;
  const open = ["posted", "partially_paid", "overdue"].includes(inv.status);
  // credit brought from another return goes back to it, so only money received or a return of these goods stops a reversal
  const reversible = Number(inv.cash_paid ?? inv.paid_amount) === 0 && !Number(inv.returns_count);
  return (
    <>
      <PageHeader title={inv.number} badge={<><StatusBadge status={inv.status} />{inv.delivery_status && <span className="badge bg-purple-light text-purple ms-1"><i className="ti ti-truck me-1" />{inv.delivery_status}</span>}</>} crumbs={[{ label: "Invoices", to: "/app/sales/invoices" }, { label: inv.number }]}
        actions={<>
          {inv.status === "draft" && page.can("discount") && <div className="btn-group">
            <select className="form-select form-select-sm" style={{ width: 150 }} value={String(Number(inv.discount_pct || 0))} onChange={(e) => run("discount", { action: e.target.value === "0" ? "remove" : "apply", percent: e.target.value }, { success: "Discount updated" })} title="Co-funded total discount">
              <option value="0">No discount</option>
              {(contribs.data || []).map((r: any) => <option key={r.id} value={String(Number(r.total_pct))}>{Number(r.total_pct)}%</option>)}
            </select>
          </div>}
          {open && pageRights("payments").extras.receive && <Link to={`/app/accounting/payments/new?direction=in&partner_type=customer&partner_id=${inv.customer}&invoice=${inv.id}&amount=${inv.balance}`} className="btn btn-success"><i className="ti ti-cash me-1" />Receive payment</Link>}
          {["posted", "partially_paid", "overdue", "paid", "returned"].includes(inv.status) && creditNotes.add && <Link to={`/app/sales/credit-notes/new?invoice=${inv.id}`} className="btn btn-outline-warning"><i className="ti ti-arrow-back-up me-1" />Credit note / return</Link>}
          {["draft", "posted", "overdue", "returned"].includes(inv.status) && reversible && inv.fbr?.status !== "submitted" && page.delete && <button className="btn btn-outline-danger" onClick={() => setRejecting(true)}><i className="ti ti-hand-stop me-1" />Customer rejected</button>}
          {inv.status !== "cancelled" && reversible && page.delete && <button className="btn btn-outline-danger" onClick={() => setCancel(true)}>Cancel</button>}
          {["posted", "overdue", "returned"].includes(inv.status) && reversible && (isOwner || can("sales.edit_posted_invoice")) && <button className="btn btn-outline-warning" disabled={!!busy} onClick={() => setReopen(true)}><i className="ti ti-lock-open me-1" />Reopen to edit</button>}
          {/* a posted, unpaid invoice can still become a tax invoice (owner, 26 Oct 2026): ONE atomic call
              reopens it and flips the switch — a refusal rolls the reopen back too, so nothing strands.
              Both rights are shown-for because the server demands edit_posted (reopen) + discount (tax) */}
          {["posted", "overdue"].includes(inv.status) && Number(inv.paid_amount) === 0 && (isOwner || can("sales.edit_posted_invoice")) && page.can("discount") && inv.fbr?.status !== "submitted" && (
            <button className={inv.force_tax_type ? "btn btn-outline-warning" : "btn btn-outline-success"} disabled={!!busy || saving}
              title={inv.force_tax_type ? "Reopens this invoice, removes the tax, and leaves it for you to review and re-post" : "Reopens this invoice, charges each item at its FBR rate, and leaves it for you to review and re-post"}
              onClick={async () => {
                setSaving(true);
                try {
                  await api.post(`/api/v1/invoices/${id}/tax`, { apply: !inv.force_tax_type }, true);
                  toast.success(inv.force_tax_type ? "Tax removed — review, then press Post to ledger" : "Tax on — review the figures, then press Post to ledger");
                } catch (e: any) { toast.error(e.message); } finally { setSaving(false); qc.invalidateQueries(); }
              }}><i className="ti ti-percentage me-1" />{inv.force_tax_type ? "Tax off" : "Tax on"}</button>)}
          {/* a posted tax invoice files with FBR in ONE click (owner, 27 Oct 2026: "after submit also give button to post
              to fbr"): the server validates and only on a pass files it — FBR's number and QR then print on the invoice */}
          {["posted", "overdue", "partially_paid", "paid"].includes(inv.status) && inv.force_tax_type && page.can("fbr_file") && inv.fbr?.status !== "submitted" && <>
            <button className="btn btn-info" disabled={!!busy || !inv.fbr_setup?.configured}
              title={inv.fbr_setup?.configured ? "Checks the invoice with FBR and, if it passes, files it" : "Set up FBR first under Settings → FBR e-invoicing"}
              onClick={() => run("fbr-post", {}, { success: inv.fbr_setup?.sandbox ? "Filed with FBR's SANDBOX — a test filing, not a legal one" : "Filed with FBR — number issued" })}>
              <i className="ti ti-send me-1" />Post to FBR{inv.fbr_setup?.sandbox ? " (sandbox)" : ""}</button>
            <button className="btn btn-outline-info" disabled={!!busy || !inv.fbr_setup?.configured} onClick={() => run("fbr-validate", {}, { success: "FBR accepted the dry run — ready to post" })}><i className="ti ti-building-bank me-1" />Validate only</button>
          </>}
          {inv.status === "draft" && page.can("discount") && (inv.force_tax_type
            ? <button className="btn btn-outline-warning" disabled={!!busy} onClick={() => run("tax", { apply: false }, { success: "Tax removed" })}><i className="ti ti-percentage me-1" />Tax off</button>
            : <button className="btn btn-outline-success" disabled={!!busy} title="Each item at its FBR rate from its HS classification — unclassified items carry no tax" onClick={() => run("tax", { apply: true }, { success: "Tax on — recalculated on every line" })}><i className="ti ti-percentage me-1" />Tax on</button>)}
          {inv.status === "draft" && page.add && <button className="btn btn-success" disabled={!!busy} onClick={() => run("post", {}, { success: `${inv.number} posted` })}><i className="ti ti-check me-1" />Post to ledger</button>}
          {!["cancelled", "paid"].includes(inv.status) && page.edit && <button className="btn btn-outline-primary" onClick={() => { setEdit({ due_date: inv.due_date || "", notes: inv.notes || "", date: inv.date || "", lines: (inv.lines || []).map((l: any) => ({ id: l.id, key: l.id, description: l.description, qty: String(Number(l.qty)), qty_was: Number(l.qty), disc_was: Number(l.discount_amount), unit_price: String(l.unit_price), discount_amount: String(l.discount_amount), product_discount_pct: String(Number(l.product_discount_pct) || 0), prod_pct_was: Number(l.product_discount_pct) || 0, tax_pct: l.tax_pct_override == null ? "" : String(Number(l.tax_pct_override)), tax_rate_now: Number(l.tax1_rate) || 0, fbr_rate: l.fbr_rate || "", fbr_sale_type: l.fbr_sale_type || "", mrp: l.mrp, others: { fed: l.product_fed_rate, extra: l.product_extra_tax_rate, further: l.product_further_tax_rate, wht: l.product_wht_rate } })) }); }}><i className="ti ti-edit me-1" />Edit</button>}
          <button className="btn btn-outline-secondary" onClick={() => api.printDoc(`/api/v1/invoices/${inv.id}/pdf`)}><i className="ti ti-printer me-1" />Print</button>
          <button className="btn btn-outline-secondary" onClick={() => api.download(`/api/v1/invoices/${inv.id}/pdf`)}><i className="ti ti-file-type-pdf me-1" />PDF</button>
        </>} />
      {inv.status === "draft" && <Alert kind="warning"><strong>Draft — not on the books yet.</strong> Review it{page.edit ? <>, press <strong>Edit</strong> to change prices or discounts</> : null}{page.can("discount") ? <>, switch the tax on or off</> : null}, then <strong>Post to ledger</strong>. The customer owes nothing on it until it is posted.</Alert>}
      {["posted", "overdue", "partially_paid", "paid"].includes(inv.status) && inv.force_tax_type && !inv.fbr && <Alert kind="info"><strong>Tax invoice — not filed with FBR yet.</strong> {inv.fbr_setup?.configured ? <>Press <strong>Post to FBR</strong>{inv.fbr_setup.sandbox ? " (FBR is set to its sandbox — a test filing)" : ""}; FBR's number and QR then print on the invoice.</> : <>Set up FBR under Settings → FBR e-invoicing to file it.</>}</Alert>}
      {inv.fbr && <Alert kind={inv.fbr.status === "submitted" ? "success" : inv.fbr.status === "failed" ? "danger" : "info"}>
        <i className="ti ti-building-bank me-1" />FBR: <strong>{inv.fbr.status}</strong>{inv.fbr.sandbox ? " (SANDBOX — not a legal filing)" : ""}{inv.fbr.fbr_invoice_number ? <> · № <span className="font-monospace">{inv.fbr.fbr_invoice_number}</span></> : null}{inv.fbr.error ? <> — {inv.fbr.error}</> : null}
      </Alert>}
      <div className="row g-3 mb-3">
        <div className="col-sm-6 col-xl-3"><div className="card mb-0" style={{ borderTop: "3px solid #F26522" }}><div className="card-body py-3 text-center"><div className="fw-bold text-primary fs-18">{money(inv.total)}</div><div className="text-muted fs-12">Invoice total</div></div></div></div>
        <div className="col-sm-6 col-xl-3"><div className="card mb-0" style={{ borderTop: "3px solid #03C95A" }}><div className="card-body py-3 text-center"><div className="fw-bold text-success fs-18">{money(inv.cash_paid ?? inv.paid_amount)}</div><div className="text-muted fs-12">Paid{Number(inv.credited_amount) > 0 && <> · <span className="text-warning">returned {money(inv.credited_amount)}</span></>}</div></div></div></div>
        <div className="col-sm-6 col-xl-3"><div className="card mb-0" style={{ borderTop: "3px solid #E70D0D" }}><div className="card-body py-3 text-center"><div className="fw-bold text-danger fs-18">{money(inv.balance)}</div><div className="text-muted fs-12">{inv.due_date && inv.due_date !== inv.date ? <>Balance due {date(inv.due_date)}</> : "Balance due"}</div></div></div></div>
        <div className="col-sm-6 col-xl-3"><div className="card mb-0" style={{ borderTop: "3px solid #1B84FF" }}><div className="card-body py-3 text-center"><div className="fw-bold fs-16"><Link to={`/app/customers/${inv.customer}`}>{inv.customer_name}</Link></div><div className="text-muted fs-12">{inv.customer_code}</div></div></div></div>
      </div>
      {inv.credit_applications?.length > 0 && <div className="card mb-3"><div className="card-body py-2 fs-13">
        <i className="ti ti-arrow-back-up me-1 text-warning" />Settled by returns: {inv.credit_applications.map((a: any, k: number) => <span key={k} className="me-3"><Link to={`/app/sales/credit-notes/${a.credit_note}`}>{a.number}</Link> {money(a.amount)} <span className="text-muted">({date(a.date)})</span></span>)}
      </div></div>}
      <div className="card mb-3"><div className="card-body">
        <div className="row g-3 mb-3"><InfoItem label="Order">{inv.order_number ? <Link to={`/app/sales/orders/${inv.order}`}>{inv.order_number}</Link> : "—"}</InfoItem><InfoItem label="Delivery note">{inv.delivery_note_number ? <Link to={`/app/delivery/notes/${inv.delivery_note}`}>{inv.delivery_note_number}</Link> : "—"}</InfoItem><InfoItem label="Load form / dispatch">{inv.picklist_number ? <Link to={`/app/delivery/pick-lists/${inv.picklist}`}>{inv.picklist_number}</Link> : "—"}{inv.trip_number ? <div className="fs-12"><Link to={`/app/delivery/trips/${inv.trip}`}>{inv.trip_number}</Link></div> : null}</InfoItem><InfoItem label="Rep">{inv.rep_name || "—"}</InfoItem><InfoItem label="Journal">{inv.journal_number ? <Link to={`/app/accounting/journals/${inv.journal}`}>{inv.journal_number}</Link> : "—"}</InfoItem>{inv.force_tax_type && <InfoItem label="Tax" col="col-md-2"><span className="badge bg-success-light text-success"><i className="ti ti-percentage me-1" />Tax invoice — FBR rates</span></InfoItem>}</div>
        {(() => {
          const taxed = Boolean(inv.force_tax_type) || Number(inv.tax1_total) > 0 || Number(inv.tax2_total) > 0;
          if (taxed) {
            /* the tax-invoice face (owner's layout, 26 Oct 2026): Disc is the line's WHOLE discount,
               Taxable = Qty × Price − Disc is what the GST was computed on and filed with FBR, Total =
               Taxable + GST. A 3rd Schedule line's GST can read higher than Taxable × rate — the law
               taxes those on the printed retail price. */
            const taxTotal = (Number(inv.tax1_total) || 0) + (Number(inv.tax2_total) || 0);
            const discTotal = inv.lines.reduce((a: number, l: any) => a + (Number(l.discount_amount) || 0), 0);
            const taxableSum = Number(inv.subtotal) - Number(inv.discount);
            const otherOf = (l: any) => (Number(l.fed_amount) || 0) + (Number(l.extra_tax_amount) || 0) + (Number(l.further_tax_amount) || 0);
            const showOther = inv.lines.some((l: any) => otherOf(l) > 0);
            const span = showOther ? 8 : 7;
            return <table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>#</th><th>Item</th><th className="num">Qty</th><th className="num">Price</th><th className="num">Disc</th><th className="num">Taxable</th><th className="num">GST</th>{showOther && <th className="num" title="FED + Extra Tax + Further Tax">Other Tax</th>}<th className="num">Line Total</th></tr></thead>
              <tbody>{inv.lines.map((l: any, i: number) => { const taxAmt = (Number(l.tax1_amount) || 0) + (Number(l.tax2_amount) || 0); const disc = Number(l.discount_amount) || 0; return <tr key={l.id}><td>{i + 1}</td><td>{l.description}<div className="text-muted fs-11">{l.variant_sku}{(l as any).hs_code ? ` · HS ${(l as any).hs_code}` : ""}</div></td><td className="num">{qty(l.qty)}</td><td className="num">{money(l.unit_price)}</td><td className="num">{disc > 0.004 ? money(disc) : "—"}</td><td className="num">{money(Number(l.line_total) - taxAmt - otherOf(l))}</td><td className="num">{taxAmt ? <>{money(taxAmt)}<div className="text-muted fs-11">{l.tax_pct_override == null && (l as any).fbr_rate ? fbrRateLabel((l as any).fbr_rate) : `${Number(l.tax1_rate)}%`}</div></> : "—"}</td>{showOther && <td className="num">{otherOf(l) ? <>{money(otherOf(l))}<div className="text-muted fs-11">{otherTaxLabel({ fed: l.product_fed_rate, extra: l.product_extra_tax_rate, further: l.product_further_tax_rate })}</div></> : "—"}</td>}<td className="num fw-medium">{money(l.line_total)}</td></tr>; })}</tbody>
              <tfoot>
                <TaxFooterRows span={span} sub={taxableSum + discTotal} discount={discTotal} discountPct={inv.discount_pct} net={taxableSum} gst={taxTotal} gstPct={gstPct(inv.lines.map((l: any) => l.tax1_rate))}
                  fed={Number(inv.fed_total)} extra={Number(inv.extra_tax_total)} further={Number(inv.further_tax_total)} wht={Number(inv.wht_total)} />
                {Number(inv.company_claims_total) > 0 && <tr className="text-success"><td colSpan={span} className="text-end">Claimed from manufacturers</td><td className="num"><Link to="/app/sales/company-claims">{money(inv.company_claims_total)}</Link></td></tr>}
              </tfoot></table>;
          }
          /* no tax (owner, 27 Oct 2026: "when no tax then show discount on total"): each line is Qty × Price, and
             every discount — the product's own and the customer's % alike — is taken once, on the total */
          const grossOf = (l: any) => Number(l.qty) * Number(l.unit_price);
          const sub = inv.lines.reduce((a: number, l: any) => a + grossOf(l), 0);
          return <table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>#</th><th>Item</th><th className="num">Qty</th><th className="num">Price</th><th className="num">Amount</th></tr></thead>
            <tbody>{inv.lines.map((l: any, i: number) => <tr key={l.id}><td>{i + 1}</td><td>{l.description}<div className="text-muted fs-11">{l.variant_sku}</div></td><td className="num">{qty(l.qty)}</td><td className="num">{money(l.unit_price)}</td><td className="num fw-medium">{money(grossOf(l))}</td></tr>)}</tbody>
            <tfoot>
              <PlainFooterRows span={4} sub={sub} discount={Number(inv.discount)} discountPct={inv.discount_pct} payable={Number(inv.total)} />
              {Number(inv.company_claims_total) > 0 && <tr className="text-success"><td colSpan={4} className="text-end">Claimed from manufacturers</td><td className="num"><Link to="/app/sales/company-claims">{money(inv.company_claims_total)}</Link></td></tr>}
            </tfoot></table>;
        })()}
      </div></div>
      <DocTabs model="SalesInvoice" id={inv.id} showMovements extra={[{ key: "payments", label: "Payments", render: () => (
        <div>{!pays.data?.payments?.length && !pays.data?.rep_collections?.length && <p className="text-muted fs-13">No payments yet</p>}
          {pays.data?.payments?.length > 0 && <table className="table table-sm fs-12"><thead className="table-light"><tr><th>Receipt</th><th>Date</th><th>Method</th><th>By</th><th className="num">Amount</th></tr></thead><tbody>{pays.data.payments.map((p: any) => <tr key={p.id}><td><Link to={`/app/accounting/payments/${p.id}`}>{p.number}</Link></td><td>{date(p.date)}</td><td>{p.method}{p.cheque_no ? ` ${p.cheque_no} (${p.cheque_status})` : ""}</td><td>{p.collected_by_name}</td><td className="num">{money(p.allocations.find((a: any) => a.document_id === inv.id)?.amount || p.amount)}</td></tr>)}</tbody></table>}
          {pays.data?.rep_collections?.length > 0 && <><h6 className="fs-13 mt-3">Rep collections</h6><table className="table table-sm fs-12"><thead className="table-light"><tr><th>Collection</th><th>Date</th><th>Rep</th><th>Status</th><th className="num">Amount</th></tr></thead><tbody>{pays.data.rep_collections.map((c: any) => <tr key={c.id}><td>{c.number}</td><td>{date(c.date)}</td><td>{c.rep_name}</td><td><StatusBadge status={c.status} /></td><td className="num">{money(c.allocations.find((a: any) => a.invoice === inv.id)?.amount || c.amount)}</td></tr>)}</tbody></table></>}
        </div>) }]} />
      <Modal open={Boolean(edit)} title={`Edit ${inv.number}`} onClose={() => setEdit(null)} size={inv.status === "draft" ? (inv.force_tax_type ? "xl" : "lg") : "md"}
        footer={<><button className="btn btn-light" onClick={() => setEdit(null)}>Cancel</button>
          <button className="btn btn-primary" disabled={saving} onClick={async () => {
            setSaving(true);
            try {
              const body: any = { due_date: edit!.due_date || null, notes: edit!.notes };
              if (inv.status === "draft") {
                body.date = edit!.date || undefined;
                body.lines = edit!.lines.filter((l: any) => !(l.isNew && l.removed)).map((l: any) => l.isNew
                  ? { variant: l.variant, qty: l.qty, unit_price: l.unit_price }
                  : l.removed ? { id: l.id, remove: true }
                  : { id: l.id, qty: l.qty, unit_price: l.unit_price, discount_amount: l.discount_amount, product_discount_pct: l.product_discount_pct, ...(inv.force_tax_type ? { tax_pct: l.tax_pct } : {}) });
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
              <label className="form-label fs-12 fw-semibold">Lines</label>
              {(() => {
                /* live figures, mirroring update_draft: the typed discount, then a changed Prod % moves it by gross ×
                   the change; Taxable = qty × rate − discount; on a tax invoice GST is the FBR engine's (or the typed
                   Tax %), Line Total = Taxable + GST. PREVIEW — the server recomputes on Save. */
                const taxed = Boolean(inv.force_tax_type);
                // anything may change on a draft (owner, 27 Oct 2026) — quantity, items added or taken off; the delivery
                // note, stock and the order follow on Save. Delivered goods keep their quantities (a return is a credit note).
                const delivered = /^(delivered|partially delivered|returned)$/i.test(inv.delivery_status || "");
                const fig = edit.lines.map((l: any) => {
                  if (l.removed) return { gross: 0, disc: 0, taxable: 0, gst: null, total: 0 };
                  const gross = Math.round(Number(l.qty) * Number(l.unit_price || 0) * 10000) / 10000;
                  const base = l.isNew ? gross * Number(inv.discount_pct || 0) / 100 : Number(l.discount_amount || 0);
                  const disc = Math.max(0, base + gross * (Number(l.product_discount_pct || 0) - l.prod_pct_was) / 100);
                  const taxable = gross - disc;
                  const gst = taxed ? fbrLineTax({ rate: l.fbr_rate, taxable, qty: Number(l.qty), saleType: l.fbr_sale_type, mrp: l.mrp, overridePct: l.tax_pct, others: l.others }) : null;
                  return { gross, disc, taxable, gst, total: taxable + (gst?.amount || 0) + (gst?.other || 0) };
                });
                const sum = (f: (x: any) => number) => fig.reduce((a: number, x: any) => a + f(x), 0);
                const gross = sum((x) => x.gross), disc = sum((x) => x.disc), net = sum((x) => x.taxable), tax = sum((x) => x.gst?.amount || 0);
                const showOther = taxed && sum((x) => x.gst?.other || 0) > 0;
                const span = taxed ? 8 + (showOther ? 1 : 0) : 5;
                const set = (i: number, patch: any) => { const ls = [...edit.lines]; ls[i] = { ...ls[i], ...patch }; setEdit({ ...edit, lines: ls }); };
                // a changed quantity carries its discount along, until a discount is typed by hand
                const setQty = (i: number, v: string) => { const l = edit.lines[i]; set(i, { qty: v, ...(!l.isNew && !l.disc_typed && l.qty_was ? { discount_amount: String(Math.round(l.disc_was * Number(v || 0) / l.qty_was * 100) / 100) } : {}) }); };
                const addItem = async (v: any) => {
                  let price = v.sale_price;
                  try { price = (await api.get(`/api/v1/variants/${v.id}/price`, { customer: inv.customer })).unit_price; } catch { /* list price */ }
                  // functional: the price fetch is async, and whatever was typed meanwhile must survive
                  setEdit((cur: any) => cur && { ...cur, lines: [...cur.lines, { key: `new-${Date.now()}`, isNew: true, variant: v.id, description: v.name, qty: "1", unit_price: String(price ?? ""), discount_amount: "0", product_discount_pct: "0", prod_pct_was: 0, tax_pct: "",
                    fbr_rate: v.fbr_rate_desc || "", fbr_sale_type: v.fbr_sale_type || "", mrp: v.mrp_ex_tax, others: { fed: v.fbr_fed_rate, extra: v.fbr_extra_tax_rate, further: v.fbr_further_tax_rate, wht: v.fbr_wht_rate } }] });
                };
                return <div className="table-responsive"><table className="table table-sm align-middle mb-1">
                  <thead className="table-light"><tr><th>Item</th><th className="num" style={{ width: 80 }}>Qty</th><th className="num" style={{ width: 120 }}>Rate</th><th className="num" style={{ width: 120 }}>Discount</th><th className="num" style={{ width: 90 }} title="The product's own standing discount — changing it adjusts the discount amount">Prod %</th>{taxed && <th className="num" style={{ width: 90 }} title="This line's tax % — blank follows the product's FBR rate">Tax %</th>}{taxed && <th className="num" style={{ width: 110 }}>Taxable</th>}{taxed && <th className="num" style={{ width: 100 }}>GST</th>}{showOther && <th className="num" style={{ width: 100 }} title="FED + Extra Tax + Further Tax">Other Tax</th>}<th className="num" style={{ width: 110 }}>{taxed ? "Line Total" : "Amount"}</th><th style={{ width: 36 }} /></tr></thead>
                  <tbody>{edit.lines.map((l: any, i: number) => (
                    l.removed ? null : <tr key={l.key}>
                      <td className="fs-12">{l.description}</td>
                      <td><input type="number" step="any" min="0" className="form-control form-control-sm text-end" value={l.qty} disabled={delivered && !l.isNew} title={delivered ? "Delivered — a quantity change is a return (credit note)" : undefined} onChange={(e) => setQty(i, e.target.value)} /></td>
                      <td><input type="number" step="0.01" min="0" className="form-control form-control-sm text-end" value={l.unit_price} onChange={(e) => set(i, { unit_price: e.target.value })} /></td>
                      <td><input type="number" step="0.01" min="0" className="form-control form-control-sm text-end" value={l.isNew ? String(Math.round(fig[i].disc * 100) / 100) : l.discount_amount} readOnly={l.isNew} onChange={(e) => set(i, { discount_amount: e.target.value, disc_typed: true })} /></td>
                      <td><input type="number" step="0.01" min="0" className="form-control form-control-sm text-end" value={l.product_discount_pct ?? "0"} onChange={(e) => set(i, { product_discount_pct: e.target.value })} /></td>
                      {taxed && <td><input type="number" step="0.01" min="0" max="100" className="form-control form-control-sm text-end" placeholder={String(l.tax_rate_now)} title="blank = the product's FBR rate" value={l.tax_pct} onChange={(e) => set(i, { tax_pct: e.target.value })} /></td>}
                      {taxed && <td className="num fs-12">{money(fig[i].taxable)}</td>}
                      {taxed && <td className="num fs-12">{fig[i].gst?.label ? <>{money(fig[i].gst.amount)}<div className="text-muted fs-11">{fig[i].gst.label}</div></> : <span className="text-warning" title="No FBR rate — this item carries no GST; classify it on the product's FBR card (HS code → rate)">—</span>}</td>}
                      {showOther && <td className="num fs-12">{fig[i].gst?.other ? <>{money(fig[i].gst.other)}<div className="text-muted fs-11">{otherTaxLabel(l.others || {})}</div></> : "—"}</td>}
                      <td className="num fs-12 fw-medium">{money(taxed ? fig[i].total : fig[i].gross)}</td>
                      <td>{!(delivered && !l.isNew) && <button type="button" className="btn btn-sm btn-outline-danger" title="Take this item off" onClick={() => set(i, { removed: true })}><i className="ti ti-x" /></button>}</td>
                    </tr>))}</tbody>
                  <tfoot>{taxed ? <TaxFooterRows span={span} trail={1} className="fs-12" sub={gross} discount={disc} discountPct={inv.discount_pct} net={net} gst={tax} gstPct={gstPctOfLabels(fig.map((x: any) => x.gst?.label))}
                      fed={sum((x) => x.gst?.fed || 0)} extra={sum((x) => x.gst?.extra || 0)} further={sum((x) => x.gst?.further || 0)} wht={sum((x) => x.gst?.wht || 0)} /> : <PlainFooterRows span={span} trail={1} className="fs-12" sub={gross} discount={disc} discountPct={inv.discount_pct} payable={net} />}</tfoot>
                </table>
                  {!delivered && <div style={{ maxWidth: 420 }}><EntitySelect endpoint="/api/v1/variants" query={{ is_active: true }} value={null} size="sm" placeholder="Add an item…" onChange={() => {}} onSelectRaw={addItem} mapOption={(v: any) => ({ id: v.id, label: v.name, sub: v.sku })} /></div>}
                </div>;
              })()}
            </div>
          </> : <div className="col-12"><Alert kind="info">The amounts cannot be changed here. This invoice is already in the ledger and owed by the customer, so a wrong figure is corrected with a <strong>credit note</strong>, which the customer also receives.</Alert></div>}
        </div>}
      </Modal>
      <ConfirmDialog open={reopen} title="Reopen to edit" withReason confirmText="Reopen invoice" message="Everything this invoice booked — its posting, COGS, manufacturer claims and their net-offs — is reversed and it returns to Draft. Edit the figures, then Post to ledger again and the books are rebuilt from the corrected invoice. Receipts must be removed before reopening." onClose={() => setReopen(false)} onConfirm={async (r) => { await run("reopen", { reason: r }, { success: "Back to draft — edit and post again" }); setReopen(false); }} />
      <ConfirmDialog open={rejecting} title="Customer rejected the goods" withReason danger confirmText="Reverse the sale"
        message={<>The sale of <strong>{inv.number}</strong> is reversed: the goods come back to stock once, its entries come off the books and its order closes as Rejected. No return and no payment are recorded.</>}
        onClose={() => setRejecting(false)} onConfirm={async (r) => { await run("reject", { reason: r }, { success: "Sale reversed — customer rejected" }); setRejecting(false); }} />
      <ConfirmDialog open={cancel} title="Cancel invoice" withReason danger confirmText="Cancel invoice" onClose={() => setCancel(false)} onConfirm={async (r) => { await run("cancel", { reason: r }); setCancel(false); }} />
    </>
  );
}


/** New invoice = invoice an order that has issued/delivered goods not yet invoiced — that is where invoices
 * come from in this flow. The button appears for anyone whose role holds the Invoices "Add" switch. */
/**
 * One order line as the invoice will bill it — mirrors InvoiceService.from_delivery_note(basis="loaded"): the
 * ISSUED qty, free goods taken out pro rata, the order line's discount + scheme scaled by the same ratio, and a
 * changed Discount % re-applied the way set_discount does (gross × (% + the product's own %)). With the tax
 * switch on, GST is the FBR engine's (fbrLineTax). PREVIEW only — the server computes the real invoice.
 */
function billedLine(l: any, applyTax: boolean, newPct: number | null) {
  const r4 = (n: number) => Math.round(n * 10000) / 10000;
  const issued = Number(l.qty_dispatched) || Number(l.qty_delivered) || 0;
  const ordered = (Number(l.qty_ordered) || 0) + (Number(l.free_qty) || 0);
  const ratio = ordered ? issued / ordered : 0;
  const free = Math.min((Number(l.free_qty) || 0) * ratio, issued);
  const paid = issued - free;
  const gross = r4(paid * (Number(l.unit_price) || 0));
  const disc = newPct === null
    ? r4(((Number(l.discount_amount) || 0) + (Number(l.scheme_discount) || 0)) * ratio)
    : r4(gross * (newPct + (Number(l.product_discount_pct) || 0)) / 100);
  const taxable = gross - disc;
  const others = { fed: l.product_fed_rate, extra: l.product_extra_tax_rate, further: l.product_further_tax_rate, wht: l.product_wht_rate };
  const gst = applyTax ? fbrLineTax({ rate: l.product_fbr_rate, taxable, qty: paid, saleType: l.product_fbr_sale_type, mrp: l.product_mrp, others }) : null;
  return { issued, gross, disc, taxable, gst, others, total: taxable + (gst?.amount || 0) + (gst?.other || 0) };
}

export function InvoiceCreatePage() {
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const page = usePage("invoices");
  const [order, setOrder] = useState<string | null>(sp.get("order"));
  const [busy, setBusy] = useState(false);
  const [applyTax, setApplyTax] = useState(false);
  const [taxPct, setTaxPct] = useState("");
  const [discOverride, setDiscOverride] = useState<string>("");
  const detail = useDoc("/api/v1/sales-orders", order || undefined);
  // the order may have asked for a tax invoice when it was taken — seed the switch from it (still changeable here)
  useEffect(() => { if (detail.data) setApplyTax(Boolean(detail.data.charge_tax)); }, [detail.data?.id]);
  const uninvoiced = (detail.data?.delivery_notes || []).filter((d: any) => !d.invoiced);
  // the % the invoice will carry: the order's own (seeded from the customer) unless changed right here
  const effDisc = discOverride !== "" ? discOverride : detail.data?.discount_pct != null ? String(Number(detail.data.discount_pct)) : "";
  return (
    <>
      <PageHeader title="New invoice" crumbs={[{ label: "Invoices", to: "/app/sales/invoices" }, { label: "New" }]} />
      <div className="card mb-3"><div className="card-body">
        <div className="row">
          <Field label="Sales order *" col="col-md-5"><EntitySelect endpoint="/api/v1/sales-orders" query={{ uninvoiced: 1, status__in: "issued" }} value={order} onChange={(v) => { setOrder(v); setDiscOverride(""); setApplyTax(false); }} mapOption={(o: any) => ({ id: o.id, label: o.number, sub: `${o.customer_name} · ${money(o.total)} · ${o.status.replace(/_/g, " ")}` })} placeholder="search order…" /></Field>
          {order && page.can("discount") && <Field label="Discount %" col="col-md-3"><DiscountPctSelect value={effDisc} onChange={setDiscOverride} /></Field>}
          {order && page.can("discount") && <Field label="Tax" col="col-md-3"><div className="form-check form-switch mt-2"><input className="form-check-input" type="checkbox" id="chg-tax" checked={applyTax} onChange={(e) => setApplyTax(e.target.checked)} /><label className="form-check-label fs-13" htmlFor="chg-tax">Charge tax — each item at its FBR rate (from its HS classification); unclassified items carry none</label></div></Field>}
        </div>
        {order && detail.data && (
          uninvoiced.length
            ? <>
                <div className="fs-13 mb-2">Billing <strong>{detail.data.customer_name}</strong> for {uninvoiced.length} delivery note(s): {uninvoiced.map((d: any) => d.number).join(", ")}{applyTax && <span className="badge bg-success-light text-success ms-2"><i className="ti ti-percentage me-1" />Tax invoice — FBR rates</span>}</div>
                {(() => {
                  // the invoice carries the order's % unless it is changed here, and only a change re-prices the discount
                  const orderPct = detail.data.discount_pct != null ? Number(detail.data.discount_pct) : null;
                  const newPct = discOverride !== "" && Number(discOverride) !== orderPct ? Number(discOverride) : null;
                  const rows = (detail.data.lines || []).map((l: any) => ({ l, b: billedLine(l, applyTax, newPct) })).filter((r: any) => r.b.issued > 0);
                  const sum = (f: (b: any) => number) => rows.reduce((a: number, r: any) => a + f(r.b), 0);
                  const gross = sum((b) => b.gross), disc = sum((b) => b.disc), net = sum((b) => b.taxable), tax = sum((b) => b.gst?.amount || 0);
                  const showOther = applyTax && sum((b) => b.gst?.other || 0) > 0;
                  const span = applyTax ? 8 + (showOther ? 1 : 0) : 5;
                  /* the tax-invoice face, as on the order and the printed invoice (owner, 26 Oct 2026): Disc is the
                     line's whole discount, Taxable = what the GST is computed on, Total = Taxable + GST */
                  return <table className="table table-sm fs-12 mb-0">
                    <thead className="table-light"><tr><th>#</th><th>Item</th><th className="num">Ordered</th><th className="num">Issued</th><th className="num">Rate</th>{applyTax && <><th className="num">Disc</th><th className="num">Taxable</th><th className="num">GST</th></>}{showOther && <th className="num" title="FED + Extra Tax + Further Tax">Other Tax</th>}<th className="num">{applyTax ? "Line Total" : "Amount"}</th></tr></thead>
                    <tbody>{rows.map(({ l, b }: any, i: number) => <tr key={l.id}>
                      <td>{i + 1}</td>
                      <td>{l.variant_name}<div className="text-muted fs-11">{l.variant_sku}{applyTax && l.hs_code ? ` · HS ${l.hs_code}` : ""}</div></td>
                      <td className="num">{qty(l.qty_ordered)}</td><td className="num">{qty(b.issued)}</td><td className="num">{money(l.unit_price)}</td>
                      {applyTax && <><td className="num">{b.disc > 0.004 ? money(b.disc) : "—"}</td><td className="num">{money(b.taxable)}</td><td className="num">{b.gst?.label ? <>{money(b.gst.amount)}<div className="text-muted fs-11">{b.gst.label}</div></> : <span className="text-warning" title="No FBR rate — this item carries no GST; classify it on the product's FBR card (HS code → rate)">—</span>}</td></>}
                      {showOther && <td className="num">{b.gst?.other ? <>{money(b.gst.other)}<div className="text-muted fs-11">{otherTaxLabel(b.others)}</div></> : "—"}</td>}
                      <td className="num fw-medium">{money(applyTax ? b.total : b.gross)}</td>
                    </tr>)}</tbody>
                    <tfoot>
                      {applyTax ? <TaxFooterRows span={span} sub={gross} discount={disc} discountPct={effDisc} net={net} gst={tax} gstPct={gstPctOfLabels(rows.map((r: any) => r.b.gst?.label))}
                          fed={sum((b) => b.gst?.fed || 0)} extra={sum((b) => b.gst?.extra || 0)} further={sum((b) => b.gst?.further || 0)} wht={sum((b) => b.gst?.wht || 0)} /> : <PlainFooterRows span={span} sub={gross} discount={disc} discountPct={effDisc} payable={net} />}
                    </tfoot></table>;
                })()}
              </>
            : <div className="fs-13 text-warning">Every delivery on this order is already invoiced — it will not be offered again.</div>
        )}
      </div></div>
      <div className="d-flex gap-2 justify-content-end">
        <button className="btn btn-light" onClick={() => nav(-1)}>Cancel</button>
        <button className="btn btn-primary" disabled={!order || busy || (detail.data && !uninvoiced.length)} onClick={async () => {
          setBusy(true);
          try {
            // apply_tax is sent BOTH ways: absent means "follow the order's own flag", so unticking the
            // seeded switch must say false out loud or a flagged order gets taxed anyway (audit, 26 Oct 2026)
            // saved as a DRAFT (owner, 27 Oct 2026: "i want to see or edit before create invoice") — it opens for
            // review and editing, and goes to the ledger only when Post to ledger is pressed there
            const r = await api.post(`/api/v1/sales-orders/${order}/invoice`, { ...(effDisc === "" ? {} : { discount_pct: effDisc }), apply_tax: applyTax, post: false }, true);
            const made = Array.isArray(r) ? r : [r];
            toast.success(made.length > 1 ? `${made.length} draft invoices saved — review, then post` : `${made[0]?.number || "Invoice"} saved as draft — review, then post`);
            nav(made[0] ? `/app/sales/invoices/${made[0].id}` : "/app/sales/invoices");
          } catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
        }}><i className="ti ti-device-floppy me-1" />Save invoice</button>
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
  const nav = useNavigate();
  if (q.isLoading) return <Spinner />;
  const cn = q.data;
  if (!cn) return null;
  return (
    <>
      <PageHeader title={cn.number} badge={<StatusBadge status={cn.status} />} crumbs={[{ label: "Credit notes", to: "/app/sales/credit-notes" }, { label: cn.number }]} actions={cn.status === "draft" && <>
        {pageRights("credit_notes").add && <button className="btn btn-success" disabled={!!busy} onClick={() => run("post", {}, { success: "Credit note posted" })}><i className="ti ti-check me-1" />Post</button>}
        {pageRights("credit_notes").delete && <button className="btn btn-outline-danger" disabled={!!busy} onClick={async () => {
          if (!window.confirm(`Delete the draft ${cn.number}? It moved no stock and booked nothing.`)) return;
          try { await api.delete(`/api/v1/credit-notes/${cn.id}`); toast.success("Draft deleted"); nav("/app/sales/credit-notes"); } catch (e: any) { toast.error(e.message); }
        }}><i className="ti ti-trash me-1" />Delete draft</button>}
      </>} />
      <div className="card mb-3"><div className="card-body">
        <div className="row g-3 mb-3"><InfoItem label="Customer"><Link to={`/app/customers/${cn.customer}`}>{cn.customer_name}</Link></InfoItem><InfoItem label="Invoice">{cn.invoice_number ? <Link to={`/app/sales/invoices/${cn.invoice}`}>{cn.invoice_number}</Link> : "—"}</InfoItem><InfoItem label="Date">{date(cn.date)}</InfoItem><InfoItem label="Reason">{cn.reason || "—"}</InfoItem></div>
        {(() => {
          /* a credit note against a tax invoice credits its tax back, so it reads like one: Taxable → GST → Line Total */
          const taxTotal = (Number(cn.tax1_total) || 0) + (Number(cn.tax2_total) || 0);
          const otherOf = (l: any) => (Number(l.fed_amount) || 0) + (Number(l.extra_tax_amount) || 0) + (Number(l.further_tax_amount) || 0);
          const showOther = cn.lines.some((l: any) => otherOf(l) > 0);
          const taxed = taxTotal > 0 || showOther || Number(cn.wht_total) > 0;
          const span = (taxed ? 8 : 6) + (showOther ? 1 : 0);
          return <table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>Item</th><th className="num">Qty</th><th className="num">Price</th><th>Restock</th><th>Condition</th><th>Reason</th>{taxed && <><th className="num">Taxable</th><th className="num">GST</th></>}{showOther && <th className="num" title="FED + Extra Tax + Further Tax">Other Tax</th>}<th className="num">{taxed ? "Line Total" : "Total"}</th></tr></thead>
            <tbody>{cn.lines.map((l: any) => { const t = (Number(l.tax1_amount) || 0) + (Number(l.tax2_amount) || 0); return <tr key={l.id}><td>{l.variant_name}<div className="text-muted fs-11">{l.variant_sku}</div></td><td className="num">{qty(l.qty)}</td><td className="num">{money(l.unit_price)}</td><td>{l.stock_back ? <span className="text-muted" title="Refused at the door or the order was cancelled — the goods were already back">already back</span> : l.restock ? "yes" : "no"}</td><td>{l.condition}</td><td>{l.reason}</td>{taxed && <><td className="num">{money(Number(l.line_total) - t - otherOf(l))}</td><td className="num">{t ? <>{money(t)}{Number(l.tax1_rate) > 0 && <div className="text-muted fs-11">{Number(l.tax1_rate)}%</div>}</> : "—"}</td></>}{showOther && <td className="num">{otherOf(l) ? money(otherOf(l)) : "—"}</td>}<td className="num">{money(l.line_total)}</td></tr>; })}</tbody>
            <tfoot>{taxed ? <TaxFooterRows span={span} sub={Number(cn.subtotal)} discount={Number(cn.discount) || 0} net={Number(cn.subtotal) - (Number(cn.discount) || 0)} gst={taxTotal} gstPct={gstPct(cn.lines.map((l: any) => l.tax1_rate))} fed={Number(cn.fed_total)} extra={Number(cn.extra_tax_total)}
                further={Number(cn.further_tax_total)} wht={Number(cn.wht_total)} payableLabel="Total Credit" /> : <PlainFooterRows span={span} sub={Number(cn.subtotal)} discount={Number(cn.discount) || 0} payable={Number(cn.total)} payableLabel="Total Credit" hideZeroDiscount />}</tfoot></table>;
        })()}
      </div></div>
      {cn.status === "posted" && <CustomerCreditCard cn={cn} onDone={() => q.refetch()} />}
      <DocTabs model="CreditNote" id={cn.id} showMovements />
    </>
  );
}

/** Where a posted return's credit went — its invoice, other invoices, refunds — and what is left to use or refund. */
function CustomerCreditCard({ cn, onDone }: { cn: any; onDone: () => void }) {
  const [choice, setChoice] = useState<CreditChoice>(emptyCreditChoice("refund"));
  const [busy, setBusy] = useState(false);
  const left = Number(cn.available_credit) || 0;
  const may = pageRights("credit_notes").add;
  const go = async () => {
    const body: any = creditBody(choice);
    if (body.choice === "keep") return;
    setBusy(true);
    try {
      if (body.choice === "refund") await api.post(`/api/v1/credit-notes/${cn.id}/refund`, body.refund);
      else await api.post(`/api/v1/credit-notes/${cn.id}/apply`, { allocations: body.apply });
      toast.success(body.choice === "refund" ? "Refund recorded" : "Credit used on the invoice");
      setChoice(emptyCreditChoice("refund"));
      onDone();
    } catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="card mb-3"><div className="card-body">
      <div className="row g-3 mb-2">
        <InfoItem label="Credit">{money(cn.total)}</InfoItem>
        <InfoItem label="Settled invoices">{money(cn.applied_amount)}{(cn.applications || []).map((a: any, i: number) => <div key={i} className="fs-12"><Link to={`/app/sales/invoices/${a.invoice}`}>{a.invoice_number}</Link> {money(a.amount)}</div>)}</InfoItem>
        <InfoItem label="Refunded">{money(cn.refunded_amount)}{(cn.refunds || []).map((r: any) => <div key={r.id} className="fs-12"><Link to={`/app/accounting/payments/${r.id}`}>{r.number}</Link> {money(r.amount)} · {r.method.replace("_", " ")}</div>)}</InfoItem>
        <InfoItem label="Customer credit left"><span className={left > 0 ? "fw-bold text-success" : ""}>{money(left)}</span>{left > 0 && <div className="fs-12 text-muted">used on their next invoice unless refunded or used now</div>}</InfoItem>
      </div>
      {left > 0 && may && <>
        <CreditOptions customer={cn.customer} exclude={cn.invoice} value={choice} onChange={setChoice} keepLabel={null} available={left} />
        <button className="btn btn-primary btn-sm mt-2" disabled={busy || (choice.choice === "apply" && !choice.apply.invoice)} onClick={go}>
          <i className={`ti ${choice.choice === "refund" ? "ti-cash" : "ti-file-invoice"} me-1`} />{choice.choice === "refund" ? "Record the refund" : "Use the credit"}
        </button>
      </>}
    </div></div>
  );
}

export function CreditNoteFormPage() {
  const [err, setErr] = useState<string | null>(null);
  const nav = useNavigate();
  const sp = new URLSearchParams(window.location.search);
  const invoiceId = sp.get("invoice");
  const [form, setForm] = useState<any>({ customer: null, invoice: invoiceId, warehouse: "", date: new Date().toISOString().slice(0, 10), reason: "", applied_to_invoice: true, notes: "" });
  const [lines, setLines] = useState<(EditLine & { restock?: boolean; condition?: string; reason?: string })[]>([]);
  const [credit, setCredit] = useState<CreditChoice>(emptyCreditChoice());
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
    // what the invoice charged per unit of each item, after its discount — a return credits exactly that (owner,
    // 30 Oct 2026); free units count, as the server counts them, so the preview and the credit agree
    const net: Record<string, { units: number; value: number }> = {};
    for (const il of inv.data.lines) {
      const n = net[il.variant] || (net[il.variant] = { units: 0, value: 0 });
      n.units += (Number(il.qty) || 0) + (Number(il.free_qty) || 0);
      n.value += (Number(il.qty) || 0) * (Number(il.unit_price) || 0) - (Number(il.discount_amount) || 0);
    }
    const unitNet = (v: string) => (net[v]?.units ? Math.round((net[v].value / net[v].units) * 10000) / 10000 : 0);
    setLines(inv.data.lines.map((l: any) => ({ key: l.id, variant: l.variant, variant_name: l.description, sku: l.variant_sku, qty: String((Number(l.qty) || 0) + (Number(l.free_qty) || 0)), unit_price: String(unitNet(l.variant)), batch: l.batch, restock: true, condition: "good", reason: "", extra: { fbr_others: { fed: l.product_fed_rate, extra: l.product_extra_tax_rate, further: l.product_further_tax_rate, wht: l.product_wht_rate } } })));
  }

  // a return against a tax invoice credits the tax the invoice CHARGED, per unit and per variant — mirrors
  // CreditNoteService.create; an item the invoice never carried takes the FBR rate when the invoice is switched on
  const invTaxed = Boolean(form.invoice && inv.data && (inv.data.force_tax_type || Number(inv.data.tax1_total) + Number(inv.data.tax2_total) > 0));
  const KEYS = ["t1", "t2", "fed", "extra", "further", "wht"] as const;
  const FIELD = { t1: "tax1_amount", t2: "tax2_amount", fed: "fed_amount", extra: "extra_tax_amount", further: "further_tax_amount", wht: "wht_amount" } as const;
  const perUnit: Record<string, Record<string, number>> = {};
  for (const il of (form.invoice && inv.data?.lines) || []) {
    const c = perUnit[il.variant] || (perUnit[il.variant] = { q: 0, t1: 0, t2: 0, fed: 0, extra: 0, further: 0, wht: 0 });
    c.q += (Number(il.qty) || 0) + (Number(il.free_qty) || 0);
    for (const k of KEYS) c[k] += Number(il[FIELD[k]]) || 0;
  }
  const creditTax = (l: EditLine, q: number, taxable: number): LineTax | null => {
    const c = l.variant ? perUnit[l.variant] : undefined;
    const r2 = (n: number) => Math.round(n * 100) / 100;
    if (c && c.q) {
      const u = (k: string) => r2((c[k] / c.q) * q);
      const fed = u("fed"), extra = u("extra"), further = u("further");
      return { label: "as invoiced", amount: u("t1") + u("t2"), fed, extra, further, wht: u("wht"), other: r2(fed + extra + further) };
    }
    if (inv.data?.force_tax_type) return fbrLineTax({ rate: l.extra?.fbr_rate, taxable, qty: q, saleType: l.extra?.fbr_sale_type, mrp: l.extra?.fbr_mrp, others: l.extra?.fbr_others });
    return null;
  };
  const submit = async (post: boolean) => {
    const valid = lines.filter((l) => l.variant && Number(l.qty) > 0);
    if (!form.customer || !valid.length) return toast.error("Select a customer and at least one line with quantity");
    try {
      const r = await api.post("/api/v1/credit-notes", { ...form, warehouse: form.warehouse || null, post, settlement: post ? creditBody(credit) : undefined, lines: valid.map((l) => ({ variant: l.variant, batch: l.batch || null, qty: l.qty, unit_price: l.unit_price, restock: l.restock ?? true, condition: l.condition || "good", reason: l.reason || "" })) }, true);
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
        <LinesEditor lines={lines} onChange={setLines as any} showDiscount={false} chargeTax={invTaxed} lineTax={creditTax} payableLabel="Total Credit" discountless />
        {lines.length > 0 && <div className="table-responsive mt-2"><table className="table table-sm fs-12"><thead className="table-light"><tr><th>Item</th><th>Restock?</th><th>Condition</th><th>Line reason</th></tr></thead><tbody>{lines.map((l) => <tr key={l.key}><td>{l.variant_name || "—"}</td><td><input type="checkbox" className="form-check-input" checked={(l.condition || "good") === "good" ? (l.restock ?? true) : false} disabled={(l.condition || "good") !== "good"} title={(l.condition || "good") !== "good" ? "Broken or expired goods are written off, not put back in stock" : ""} onChange={(e) => setLines(lines.map((x) => (x.key === l.key ? { ...x, restock: e.target.checked } : x)))} /></td><td><select className="form-select form-select-sm" value={l.condition || "good"} onChange={(e) => setLines(lines.map((x) => (x.key === l.key ? { ...x, condition: e.target.value, restock: e.target.value === "good" } : x)))}><option value="good">good</option><option value="damaged">damaged</option><option value="expired">expired</option></select></td><td><input className="form-control form-control-sm" value={l.reason || ""} onChange={(e) => setLines(lines.map((x) => (x.key === l.key ? { ...x, reason: e.target.value } : x)))} /></td></tr>)}</tbody></table></div>}
      </div></div>
      {form.customer && (!form.invoice || Number(inv.data?.paid_amount) > 0) && <div className="card mb-3"><div className="card-body">
        <h6 className="fs-13 mb-2">Money the customer is owed after this return</h6>
        <CreditOptions customer={form.customer} exclude={form.invoice} value={credit} onChange={setCredit} />
      </div></div>}
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
