import { useState } from "react";
import { Link } from "react-router-dom";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { hasPerm } from "@/lib/auth";
import { datetime } from "@/lib/format";
import { toast } from "@/lib/toast";

type Step = {
  key: string; title: string; state: "done" | "current" | "todo" | "blocked" | "skipped";
  at: string | null; by: string; detail: string;
  docs: { label: string; to: string }[];
  /** `alt` is a quieter second way to do the same step — "raise it as a draft" beside "create invoice". */
  action: { label: string; post?: string; to?: string; then?: string; perm?: string; alt?: { label: string; post: string; body?: any } } | null;
};

const ICONS: Record<string, string> = {
  // sales chain
  order: "ti-file-text", load_form: "ti-clipboard-list", issue: "ti-package-export", invoice: "ti-receipt",
  dispatch: "ti-truck-loading", out_for_delivery: "ti-truck-delivery", delivered: "ti-circle-check",
  // buying chain
  approved: "ti-circle-check", receiving: "ti-package-import", put_away: "ti-building-warehouse",
  billed: "ti-file-invoice", paid: "ti-cash",
};
const TONE: Record<Step["state"], { ring: string; text: string; bg: string }> = {
  done: { ring: "#16a34a", text: "#16a34a", bg: "#ecfdf5" },
  current: { ring: "#F26522", text: "#F26522", bg: "#fff7ed" },
  todo: { ring: "#D1D5DB", text: "#9CA3AF", bg: "#F9FAFB" },
  blocked: { ring: "#dc2626", text: "#dc2626", bg: "#fef2f2" },
  skipped: { ring: "#E5E7EB", text: "#D1D5DB", bg: "#F9FAFB" },
};

/**
 * The order's journey: sales order → load form → warehouse issue → invoice → dispatch → out for delivery →
 * delivery confirmation. Each step carries its documents and the one action that moves it along, so the whole
 * sequence can be run from the order without hunting through menus.
 */
export function WorkflowStepper({ orderId, endpoint = "/api/v1/sales-orders", title = "Order to delivery", compact, onData }: {
  orderId: string;
  /** Which document's chain to show — sales orders by default, purchase orders with `/api/v1/purchase-orders`. */
  endpoint?: string;
  title?: string;
  compact?: boolean;
  onData?: (data: any) => void;
}) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [open, setOpen] = useState(!compact);
  const q = useQuery<{ steps: Step[] }>({ queryKey: ["workflow", endpoint, orderId], queryFn: () => api.get(`${endpoint}/${orderId}/workflow`) });
  const act = useMutation({
    mutationFn: (v: { path: string; body?: any; then?: string }) => api.post(v.path, v.body || {}, true),
    onSuccess: (d: any, v) => {
      toast.success("Done");
      // a step that CREATES a document opens it for review instead of finishing it (owner, 27 Oct 2026)
      if (v.then && d?.id) nav(v.then.replace("{id}", d.id));
      qc.invalidateQueries({ queryKey: ["workflow", endpoint, orderId] });
      qc.invalidateQueries({ queryKey: ["doc"] });
      qc.invalidateQueries({ queryKey: ["list"] });
      if (v.path.endsWith("/load-form")) qc.invalidateQueries({ queryKey: ["list", "/api/v1/pick-lists"] });
    },
    onError: (e: any) => toast.error(e.message),
  });
  const steps = q.data?.steps || [];
  if (q.data && onData) onData(q.data);
  if (!steps.length) return null;
  const doneCount = steps.filter((s) => s.state === "done").length;
  const next = steps.find((s) => s.state === "current" && s.action);
  return (
    <div className="card mb-3">
      <div className="card-header d-flex align-items-center justify-content-between">
        <strong><i className="ti ti-route me-2 text-primary" />{title}</strong>
        <div className="d-flex align-items-center gap-2">
          <span className="fs-12 text-muted">{doneCount} of {steps.length} done</span>
          {compact && <button className="btn btn-sm btn-outline-secondary" onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"}</button>}
        </div>
      </div>
      {open && (
        <div className="card-body">
          <div className="d-flex flex-wrap flex-lg-nowrap align-items-stretch gap-2">
            {steps.map((s, i) => {
              const tone = TONE[s.state];
              const allowed = !s.action?.perm || hasPerm(s.action.perm);
              return (
                <div key={s.key} className="flex-grow-1 position-relative" style={{ minWidth: 150, flexBasis: 0 }}>
                  {i > 0 && <span className="d-none d-lg-block position-absolute" style={{ left: -8, top: 18, width: 16, height: 2, background: steps[i - 1].state === "done" ? "#16a34a" : "#E5E7EB" }} />}
                  <div className="h-100 rounded p-2" style={{ background: tone.bg, border: `1px solid ${tone.ring}33` }}>
                    <div className="d-flex align-items-center gap-2 mb-1">
                      <span className="d-inline-flex align-items-center justify-content-center rounded-circle flex-shrink-0" style={{ width: 28, height: 28, border: `2px solid ${tone.ring}`, color: tone.text, background: "#fff" }}>
                        <i className={`ti ${s.state === "done" ? "ti-check" : ICONS[s.key] || "ti-point"}`} style={{ fontSize: 14 }} />
                      </span>
                      <div className="lh-sm">
                        <div className="fw-semibold fs-12">{s.title}</div>
                        <div className="fs-11" style={{ color: tone.text }}>{s.state === "current" ? "in progress" : s.state}</div>
                      </div>
                    </div>
                    {s.detail && <div className="fs-11 text-muted" title={s.detail} style={{ overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>{s.detail}</div>}
                    {s.at && <div className="fs-11 text-muted mt-1"><i className="ti ti-clock me-1" />{datetime(s.at)}{s.by ? ` · ${s.by}` : ""}</div>}
                    {s.docs.map((d) => <div key={d.to} className="fs-11 mt-1"><Link to={d.to}><i className="ti ti-external-link me-1" />{d.label}</Link></div>)}
                    {s.action && allowed && (
                      <button
                        className={`btn btn-sm w-100 mt-2 ${s.state === "current" ? "btn-primary" : "btn-outline-primary"}`}
                        disabled={act.isPending}
                        onClick={() => (s.action!.post ? act.mutate({ path: s.action!.post!, then: s.action!.then }) : nav(s.action!.to!))}
                      >
                        {s.action.label}
                      </button>
                    )}
                    {s.action?.alt && allowed && (
                      <button
                        className="btn btn-sm btn-link w-100 p-0 mt-1 fs-11 text-decoration-none"
                        disabled={act.isPending}
                        onClick={() => act.mutate({ path: s.action!.alt!.post, body: s.action!.alt!.body })}
                      >
                        {s.action.alt.label}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          {next && <div className="fs-12 text-muted mt-2"><i className="ti ti-arrow-right me-1" />Next: <strong>{next.action!.label}</strong> — {next.detail}</div>}
        </div>
      )}
    </div>
  );
}
