import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { date, datetime, money, time } from "@/lib/format";
import { toast } from "@/lib/toast";
import { EmptyState, Spinner, Tabs } from "../ui/Bits";

/** Shared bottom tabs for every document page: attachments · history · journal(s) · stock movements. */
export function DocTabs({ model, id, journalSource, showJournal = true, showMovements = false, extra, defaultTab }: { model: string; id: string; journalSource?: string; showJournal?: boolean; showMovements?: boolean; extra?: { key: string; label: string; render: () => JSX.Element }[]; defaultTab?: string }) {
  // a document whose own trail is the point of opening it can land on History instead of Attachments
  const [tab, setTab] = useState(defaultTab || extra?.[0]?.key || "attachments");
  const tabs = [...(extra || []), { key: "attachments", label: "Attachments", render: () => <AttachmentsTab model={model} id={id} /> }, { key: "history", label: "History", render: () => <HistoryTab model={model} id={id} /> }];
  if (showJournal) tabs.push({ key: "journal", label: "Journal entries", render: () => <JournalTab model={journalSource || model} id={id} /> });
  if (showMovements) tabs.push({ key: "movements", label: "Stock movements", render: () => <MovementsTab model={model} id={id} /> });
  return (
    <div className="card">
      <div className="card-body">
        <Tabs tabs={tabs} active={tab} onChange={setTab} />
        {tabs.find((t) => t.key === tab)?.render()}
      </div>
    </div>
  );
}

export function AttachmentsTab({ model, id, kind }: { model: string; id: string; kind?: string }) {
  const qc = useQueryClient();
  const file = useRef<HTMLInputElement>(null);
  const q = useQuery({ queryKey: ["attachments", model, id], queryFn: () => api.list("/api/v1/attachments", { model, object_id: id, page_size: 100 }) });
  const upload = async (f: File) => {
    const fd = new FormData();
    fd.append("model", model); fd.append("object_id", id); fd.append("kind", kind || "document"); fd.append("file", f);
    try { await api.upload("/api/v1/attachments", fd); toast.success("Uploaded"); qc.invalidateQueries({ queryKey: ["attachments", model, id] }); } catch (e: any) { toast.error(e.message); }
  };
  return (
    <div>
      <div className="d-flex justify-content-between align-items-center mb-2">
        <span className="text-muted fs-13">Receipts, photos, signed documents (jpg, png, pdf, csv, xlsx — max 25 MB)</span>
        <button className="btn btn-sm btn-outline-primary" onClick={() => file.current?.click()}><i className="ti ti-upload me-1" />Upload</button>
        <input type="file" ref={file} hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
      </div>
      {q.isLoading ? <Spinner /> : !q.data?.results.length ? <EmptyState icon="ti-paperclip" text="No attachments" /> : (
        <ul className="list-group">
          {q.data.results.map((a: any) => (
            <li key={a.id} className="list-group-item d-flex justify-content-between align-items-center">
              <div><a href={a.url} target="_blank" rel="noreferrer"><i className="ti ti-file me-1" />{a.name}</a><span className="text-muted fs-12 ms-2">{(a.size / 1024).toFixed(0)} KB · {a.kind} · {a.uploaded_by_name} · {datetime(a.created_at)}</span></div>
              <button className="btn btn-sm btn-outline-danger" onClick={async () => { await api.delete(`/api/v1/attachments/${a.id}`); qc.invalidateQueries({ queryKey: ["attachments", model, id] }); }}><i className="ti ti-trash" /></button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * What happened to this document, a day at a time.
 *
 * A receipt keyed over three days, corrected and then posted, carries a long tail of entries, and this is the
 * tab a goods receipt opens on — the page was mostly history before anybody had scrolled. Each day is one line
 * you click to open, the most recent already open, so the document itself stays on screen.
 */
/**
 * What changed in one history entry, read as fields rather than a wall of JSON. Only the fields that actually
 * differ are shown, each as "field: old → new"; a create or delete shows just the values it has.
 */
function HistoryChanges({ before, after }: { before: any; after: any }) {
  const b = before || {};
  const a = after || {};
  const fmt = (v: any) => (v === null || v === undefined || v === "" ? "—" : typeof v === "object" ? Object.entries(v).map(([k, x]) => `${k}: ${x}`).join(", ") : String(v));
  const keys = Array.from(new Set([...Object.keys(b), ...Object.keys(a)])).filter((k) => JSON.stringify(b[k]) !== JSON.stringify(a[k]));
  if (!keys.length) return null;
  return (
    <div className="mt-1 ps-2 border-start border-2">
      {keys.map((k) => (
        <div key={k} className="fs-11">
          <span className="text-muted">{k.replace(/_/g, " ")}: </span>
          {k in b && b[k] !== null && b[k] !== "" && <span className="text-danger text-decoration-line-through">{fmt(b[k])}</span>}
          {k in b && b[k] !== null && b[k] !== "" && k in a ? <span className="text-muted"> → </span> : null}
          {k in a && <span className="fw-medium">{fmt(a[k])}</span>}
        </div>
      ))}
    </div>
  );
}

export function HistoryTab({ model, id }: { model: string; id: string }) {
  const q = useQuery({ queryKey: ["history", model, id], queryFn: () => api.get(`/api/v1/history/${model}/${id}`) });
  const [opened, setOpened] = useState<Record<string, boolean>>({});
  if (q.isLoading) return <Spinner />;
  if (!q.data?.length) return <EmptyState icon="ti-history" text="No history yet" />;

  const days: { day: string; items: any[] }[] = [];
  for (const h of [...q.data].sort((a: any, b: any) => (a.at < b.at ? 1 : -1))) {
    const day = date(h.at);
    const last = days[days.length - 1];
    if (last && last.day === day) last.items.push(h);
    else days.push({ day, items: [h] });
  }
  // the newest day is open unless it has been clicked shut; older days open on a click
  const isOpen = (day: string) => Boolean(opened[day]);
  const who = (items: any[]) => {
    const names = [...new Set(items.map((h) => h.actor_name || "system"))];
    return names.length <= 2 ? names.join(" and ") : `${names[0]} and ${names.length - 1} others`;
  };

  return (
    <div>
      {days.map((g, i) => {
        const open = isOpen(g.day);
        return (
          <div key={g.day} className="border rounded mb-2">
            <button
              type="button"
              className="btn w-100 text-start d-flex align-items-center justify-content-between px-3 py-2"
              onClick={() => setOpened((o) => ({ ...o, [g.day]: !open }))}
              aria-expanded={open}
            >
              <span className="fw-medium fs-13"><i className={`ti ti-chevron-${open ? "down" : "right"} me-1 text-muted`} />{g.day}</span>
              <span className="fs-12 text-muted">{g.items.length} change{g.items.length === 1 ? "" : "s"} · {who(g.items)}</span>
            </button>
            {open && (
              <ul className="status-timeline px-3 pb-3 mb-0">
                {g.items.map((h: any) => (
                  <li key={h.id}>
                    <div className="fw-medium fs-13">{h.action.replace(/[._]/g, " ")} <span className="text-muted fw-normal">by {h.actor_name || "system"}</span></div>
                    <div className="fs-12 text-muted">{time(h.at)}</div>
                    {(h.before || h.after) && <HistoryChanges before={h.before} after={h.after} />}
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function JournalTab({ model, id }: { model: string; id: string }) {
  const q = useQuery({ queryKey: ["journals-for", model, id], queryFn: () => api.list("/api/v1/accounting/journals", { source_type: model, source_id: id, page_size: 50 }) });
  if (q.isLoading) return <Spinner />;
  if (!q.data?.results.length) return <EmptyState icon="ti-book" text="No journal entries for this document" />;
  return (
    <div>
      {q.data.results.map((je: any) => (
        <div key={je.id} className="mb-3">
          <div className="d-flex justify-content-between"><Link to={`/app/accounting/journals/${je.id}`} className="fw-semibold">{je.number}</Link><span className="fs-12 text-muted">{date(je.date)} · {je.status} · {je.memo}</span></div>
          <table className="table table-sm fs-12 mb-0 mt-1"><thead className="table-light"><tr><th>Account</th><th>Description</th><th className="num">Debit</th><th className="num">Credit</th></tr></thead>
            <tbody>{je.lines.map((l: any) => <tr key={l.id}><td>{l.account_code} {l.account_name}</td><td>{l.description}</td><td className="num">{money(l.debit)}</td><td className="num">{money(l.credit)}</td></tr>)}</tbody></table>
        </div>
      ))}
    </div>
  );
}

export function MovementsTab({ model, id }: { model: string; id: string }) {
  const q = useQuery({ queryKey: ["movements-for", model, id], queryFn: () => api.get("/api/v1/inventory/movements", { reference_type: model, reference_id: id, page_size: 200 }) });
  if (q.isLoading) return <Spinner />;
  const rows = q.data?.results || [];
  if (!rows.length) return <EmptyState icon="ti-arrows-exchange" text="No stock movements" />;
  return (
    <table className="table table-sm fs-12 mb-0"><thead className="table-light"><tr><th>Date</th><th>Warehouse</th><th>Item</th><th>Batch</th><th>Type</th><th className="num">Qty</th><th className="num">Unit cost</th></tr></thead>
      <tbody>{rows.map((m: any) => <tr key={m.id}><td>{datetime(m.occurred_at)}</td><td>{m.warehouse_name}</td><td>{m.variant_name}</td><td>{m.batch_no}</td><td>{m.movement_type}</td><td className={`num ${Number(m.qty) < 0 ? "text-danger" : "text-success"}`}>{m.qty}</td><td className="num">{money(m.unit_cost)}</td></tr>)}</tbody></table>
  );
}
