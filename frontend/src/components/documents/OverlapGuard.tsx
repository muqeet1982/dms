import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import { date, labelCase, qty } from "@/lib/format";
import { ConfirmDialog } from "@/components/ui/Modal";

type Overlap = { id: string; number: string; status: string; status_label: string; date: string; items: { name: string; qty: string; left: string }[] };

/**
 * Before Confirm, Save & confirm or a balance's load form: the customer's other orders whose same items have not been
 * issued from the warehouse yet (owner, 12 Nov 2026). A warning, never a block: `guard(params, go)` runs `go` straight
 * away when there is nothing to say, else asks first.
 */
export function useOverlapGuard() {
  const [state, setState] = useState<null | { list: Overlap[]; go: () => unknown }>(null);
  const guard = async (params: Record<string, any>, go: () => unknown) => {
    let list: Overlap[] = [];
    try { list = await api.get("/api/v1/sales-orders/overlaps", params); } catch { list = []; }
    if (!list?.length) return go();
    setState({ list, go });
  };
  const dialog = (
    <ConfirmDialog open={!!state} title={labelCase("same items on another order")} confirmText="Continue anyway"
      onClose={() => setState(null)}
      onConfirm={async () => { const go = state?.go; setState(null); await go?.(); }}
      message={<div className="fs-13">
        {(state?.list || []).map((o) => (
          <div key={o.id} className="mb-2">
            <Link to={`/app/sales/orders/${o.id}`} target="_blank" className="fw-semibold">{o.number}</Link> <span className="text-muted">· {o.status_label} · {date(o.date)}</span>
            {o.items.map((it) => <div key={it.name} className="ms-2">{it.name} × {qty(it.qty)}{Number(it.left) !== Number(it.qty) ? ` (${qty(it.left)} not issued yet)` : ""}</div>)}
          </div>
        ))}
      </div>} />
  );
  return { guard, dialog };
}
