import { money } from "@/lib/format";

/**
 * A tax document's footer — the owner's sample (26 Oct 2026), the same on every screen and print:
 * Sub Total → Discount → Net Total → GST → FED / Extra Tax / Further Tax (when charged) → Total Tax → Grand Total
 * → Less: WHT (when the buyer withholds) → Total Payable. `span` is the number of columns before the amount column;
 * `trail` adds that many empty cells after it (a remove-button column, a Delivered column…).
 */
export function TaxFooterRows({ span, trail = 0, sub, discount = 0, discountLabel = "Discount", scheme = 0, net, gst, gstLabel = "", fed = 0, extra = 0, further = 0, wht = 0, payableLabel = "Total Payable", className = "" }: {
  span: number; trail?: number; sub: number; discount?: number; discountLabel?: string; scheme?: number; net: number; gst: number; gstLabel?: string;
  fed?: number; extra?: number; further?: number; wht?: number; payableLabel?: string; className?: string;
}) {
  const totalTax = gst + fed + extra + further;
  const grand = net + totalTax;
  const row = (label: string, v: string, bold = false) => (
    <tr className={bold ? "fw-bold" : ""}><td colSpan={span} className={`text-end ${className}`}>{label}</td><td className={`num ${className}`}>{v}</td>{Array.from({ length: trail }, (_, i) => <td key={i} />)}</tr>
  );
  return <>
    {row("Sub Total", money(sub))}
    {discount > 0.004 && row(discountLabel, `−${money(discount)}`)}
    {scheme > 0.004 && row("Scheme", `−${money(scheme)}`)}
    {row("Net Total", money(net))}
    {row(gstLabel ? `GST — ${gstLabel}` : "GST", money(gst))}
    {fed > 0.004 && row("FED", money(fed))}
    {extra > 0.004 && row("Extra Tax", money(extra))}
    {further > 0.004 && row("Further Tax", money(further))}
    {row("Total Tax", money(totalTax))}
    {row("Grand Total", money(grand))}
    {wht > 0.004 && row("Less: WHT (sales tax withheld at source)", `−${money(wht)}`)}
    {row(payableLabel, money(grand - wht), true)}
  </>;
}

/**
 * A document WITHOUT tax (owner, 27 Oct 2026: "when no tax then show discount on total … show subtotal, total
 * discount and grand total and payable"): each line is Qty × Rate, and every discount — the product's own, the
 * customer's % and schemes — is taken once, on the total. `hideZeroDiscount` is for documents that never carry
 * one (a credit note).
 */
export function PlainFooterRows({ span, trail = 0, sub, discount = 0, payable, payableLabel = "Total Payable", hideZeroDiscount = false, className = "" }: {
  span: number; trail?: number; sub: number; discount?: number; payable: number; payableLabel?: string; hideZeroDiscount?: boolean; className?: string;
}) {
  const row = (label: string, v: string, bold = false) => (
    <tr className={bold ? "fw-bold" : ""}><td colSpan={span} className={`text-end ${className}`}>{label}</td><td className={`num ${className}`}>{v}</td>{Array.from({ length: trail }, (_, i) => <td key={i} />)}</tr>
  );
  return <>
    {row("Sub Total", money(sub))}
    {!(hideZeroDiscount && discount <= 0.004) && row("Total Discount", discount > 0.004 ? `−${money(discount)}` : money(0))}
    {row("Grand Total", money(sub - discount))}
    {row(payableLabel, money(payable), true)}
  </>;
}
