import { useOptions } from "@/lib/hooks";
import { pageRights } from "@/lib/perms";
import { EntitySelect } from "@/components/ui/EntitySelect";
import { money } from "@/lib/format";

/**
 * What happens to the money a return leaves the customer (owner, 30 Oct 2026: "give multiple options for customer
 * amount return"): keep it as credit for their next invoice, use it on another open invoice now, or refund it — in
 * cash, by bank transfer or by cheque. A blank amount means all of the credit (refund) / as much as the invoice
 * still owes (use). The same choice is made on the return form and later on the return's own page.
 */
export type CreditChoice = {
  choice: "keep" | "refund" | "apply";
  refund: { method: "cash" | "bank_transfer" | "cheque"; amount: string; cash_account: string; bank_account: string; cheque_no: string; cheque_date: string };
  apply: { invoice: string | null; amount: string };
};

export const emptyCreditChoice = (choice: CreditChoice["choice"] = "keep"): CreditChoice => ({
  choice, refund: { method: "cash", amount: "", cash_account: "", bank_account: "", cheque_no: "", cheque_date: "" }, apply: { invoice: null, amount: "" },
});

/** The API body for a choice: `{choice, refund}` or `{choice, apply: [{invoice, amount}]}`. */
export function creditBody(c: CreditChoice) {
  if (c.choice === "refund") {
    const { cash_account, bank_account, cheque_no, cheque_date, ...r } = c.refund;
    // only what the method uses: a bank picked before switching to cash must not pay the refund from the bank
    const money = r.method === "cash" ? { cash_account } : { bank_account, ...(r.method === "cheque" ? { cheque_no, cheque_date } : {}) };
    return { choice: "refund", refund: { ...r, ...money, amount: r.amount || undefined } };
  }
  if (c.choice === "apply" && c.apply.invoice) return { choice: "apply", apply: [{ invoice: c.apply.invoice, amount: c.apply.amount || undefined }] };
  return { choice: "keep" };
}

export function CreditOptions({ customer, exclude, value, onChange, keepLabel = "Keep as credit — used on their next invoice", available }: {
  customer: string | null; exclude?: string | null; value: CreditChoice; onChange: (c: CreditChoice) => void; keepLabel?: string | null; available?: number;
}) {
  const banks = useOptions("/api/v1/accounting/bank-accounts");
  const cashes = useOptions("/api/v1/accounting/cash-accounts");
  const canPay = pageRights("payments").extras.pay;
  const set = (patch: Partial<CreditChoice>) => onChange({ ...value, ...patch });
  const setRefund = (patch: Partial<CreditChoice["refund"]>) => onChange({ ...value, refund: { ...value.refund, ...patch } });
  const radio = (key: CreditChoice["choice"], label: string) => (
    <div className="form-check me-4">
      <input className="form-check-input" type="radio" id={`credit-${key}`} checked={value.choice === key} onChange={() => set({ choice: key })} />
      <label className="form-check-label" htmlFor={`credit-${key}`}>{label}</label>
    </div>
  );
  return (
    <div>
      <div className="d-flex flex-wrap mb-2">
        {keepLabel && radio("keep", keepLabel)}
        {canPay && radio("refund", "Refund to the customer")}
        {radio("apply", "Use on another open invoice")}
      </div>
      {value.choice === "refund" && canPay && (
        <div className="row g-2">
          <div className="col-md-3"><label className="form-label fs-12 mb-1">Refund by</label>
            <select className="form-select" value={value.refund.method} onChange={(e) => setRefund({ method: e.target.value as any })}>
              <option value="cash">Cash</option><option value="bank_transfer">Bank transfer</option><option value="cheque">Cheque</option>
            </select></div>
          {value.refund.method === "cash"
            ? <div className="col-md-3"><label className="form-label fs-12 mb-1">From cash account</label>
                <select className="form-select" value={value.refund.cash_account} onChange={(e) => setRefund({ cash_account: e.target.value })}>
                  <option value="">Cash in Hand</option>{(cashes.data || []).map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select></div>
            : <div className="col-md-3"><label className="form-label fs-12 mb-1">From bank account *</label>
                <select className="form-select" value={value.refund.bank_account} onChange={(e) => setRefund({ bank_account: e.target.value })}>
                  <option value="">Choose…</option>{(banks.data || []).map((b: any) => <option key={b.id} value={b.id}>{b.name}</option>)}
                </select></div>}
          <div className="col-md-3"><label className="form-label fs-12 mb-1">Amount</label>
            <input type="number" step="0.01" min="0" className="form-control text-end" value={value.refund.amount} onChange={(e) => setRefund({ amount: e.target.value })}
              placeholder={available !== undefined ? `all — ${money(available)}` : "all of the credit"} /></div>
          {value.refund.method === "cheque" && <div className="col-md-3"><label className="form-label fs-12 mb-1">Cheque no.</label>
            <input className="form-control" value={value.refund.cheque_no} onChange={(e) => setRefund({ cheque_no: e.target.value })} /></div>}
        </div>
      )}
      {value.choice === "apply" && (
        <div className="row g-2">
          <div className="col-md-6"><label className="form-label fs-12 mb-1">Open invoice *</label>
            <EntitySelect endpoint="/api/v1/invoices" query={{ open: 1, customer: customer || undefined }} value={value.apply.invoice}
              onChange={(v) => set({ apply: { ...value.apply, invoice: v } })}
              mapOption={(i: any) => (i.id === exclude ? { id: i.id, label: `${i.number} (this return's invoice)`, sub: "" } : { id: i.id, label: i.number, sub: `owes ${money(i.balance)}` })} /></div>
          <div className="col-md-3"><label className="form-label fs-12 mb-1">Amount</label>
            <input type="number" step="0.01" min="0" className="form-control text-end" value={value.apply.amount} onChange={(e) => set({ apply: { ...value.apply, amount: e.target.value } })} placeholder="as much as it owes" /></div>
        </div>
      )}
    </div>
  );
}
