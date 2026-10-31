import { money } from "@/lib/format";

/** " (15%)" after a discount label — the document's own % (owner, 27 Oct 2026: "in total discount text also show (%)"). */
const pctNote = (p: any) => (Number(p) > 0 ? ` (${Number(p)}%)` : "");

/** Advance income tax 236G/236H on the Grand Total at the customer's %, to the paisa, half up — the server's
 * `advance_income_tax`, for live previews; a saved document carries its own figure (owner, 31 Oct 2026). */
export const advanceTax = (grand: number, rate?: number | string | null) =>
  Number(rate) > 0 && grand > 0 ? Math.round((grand * Number(rate) / 100 + Number.EPSILON) * 100) / 100 : 0;
const advanceLabel = (rate?: number | string | null) => `Advance Income Tax${Number(rate) > 0 ? ` (${Number(rate)}%)` : ""}`;

/**
 * A tax document's footer — the owner's sample (26 Oct 2026), the same on every screen and print:
 * Sub Total → Discount → Net Total → GST (18%) → FED / Extra Tax / Further Tax (when charged) → Total Tax → Grand Total
 * → Advance Income Tax (the customer's %, on the Grand Total — owner, 31 Oct 2026) → Less: WHT (when the buyer
 * withholds) → Total Payable. `advance` is a saved document's figure; a live form passes `advanceRate` and the footer
 * works it out. `span` is the number of columns before the amount column; `trail` adds that many empty cells after
 * it (a remove-button column, a Delivered column…).
 */
export function TaxFooterRows({ span, trail = 0, sub, discount = 0, discountLabel = "Discount", discountPct, scheme = 0, net, gst, gstPct = "", fed = 0, extra = 0, further = 0, wht = 0, advance, advanceRate, payableLabel = "Total Payable", className = "" }: {
  span: number; trail?: number; sub: number; discount?: number; discountLabel?: string; discountPct?: number | string | null; scheme?: number; net: number; gst: number; gstPct?: string;
  fed?: number; extra?: number; further?: number; wht?: number; advance?: number; advanceRate?: number | string | null; payableLabel?: string; className?: string;
}) {
  const totalTax = gst + fed + extra + further;
  const grand = net + totalTax;
  const adv = advance ?? advanceTax(grand, advanceRate);
  const row = (label: string, v: string, bold = false) => (
    <tr className={bold ? "fw-bold" : ""}><td colSpan={span} className={`text-end ${className}`}>{label}</td><td className={`num ${className}`}>{v}</td>{Array.from({ length: trail }, (_, i) => <td key={i} />)}</tr>
  );
  return <>
    {row("Sub Total", money(sub))}
    {discount > 0.004 && row(discountLabel + pctNote(discountPct), `−${money(discount)}`)}
    {scheme > 0.004 && row("Scheme", `−${money(scheme)}`)}
    {row("Net Total", money(net))}
    {row(gstPct ? `GST (${gstPct})` : "GST", money(gst))}
    {fed > 0.004 && row("FED", money(fed))}
    {extra > 0.004 && row("Extra Tax", money(extra))}
    {further > 0.004 && row("Further Tax", money(further))}
    {row("Total Tax", money(totalTax))}
    {row("Grand Total", money(grand))}
    {adv > 0.004 && row(advanceLabel(advanceRate), money(adv))}
    {wht > 0.004 && row("Less: WHT (sales tax withheld at source)", `−${money(wht)}`)}
    {row(payableLabel, money(grand + adv - wht), true)}
  </>;
}

/**
 * A document WITHOUT tax (owner, 27 Oct 2026: "when no tax then show discount on total … show subtotal, total
 * discount and grand total and payable"): each line is Qty × Rate, and every discount — the product's own, the
 * customer's % and schemes — is taken once, on the total. `hideZeroDiscount` is for documents that never carry
 * one (a credit note). Advance income tax follows the Grand Total as on a tax document: `advance` (saved) or
 * `advanceRate` (live). `payable` is a saved document's own total; left out, it is Grand Total + advance tax.
 */
export function PlainFooterRows({ span, trail = 0, sub, discount = 0, discountPct, payable, advance, advanceRate, payableLabel = "Total Payable", hideZeroDiscount = false, className = "" }: {
  span: number; trail?: number; sub: number; discount?: number; discountPct?: number | string | null; payable?: number; advance?: number; advanceRate?: number | string | null; payableLabel?: string; hideZeroDiscount?: boolean; className?: string;
}) {
  const grand = sub - discount;
  const adv = advance ?? advanceTax(grand, advanceRate);
  const row = (label: string, v: string, bold = false) => (
    <tr className={bold ? "fw-bold" : ""}><td colSpan={span} className={`text-end ${className}`}>{label}</td><td className={`num ${className}`}>{v}</td>{Array.from({ length: trail }, (_, i) => <td key={i} />)}</tr>
  );
  return <>
    {row("Sub Total", money(sub))}
    {!(hideZeroDiscount && discount <= 0.004) && row("Total Discount" + pctNote(discountPct), discount > 0.004 ? `−${money(discount)}` : money(0))}
    {row("Grand Total", money(grand))}
    {adv > 0.004 && row(advanceLabel(advanceRate), money(adv))}
    {row(payableLabel, money(payable ?? grand + adv), true)}
  </>;
}
