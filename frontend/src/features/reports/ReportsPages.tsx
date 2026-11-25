import { DateInput } from "@/components/ui/DateInput";
import { useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { date, DATE_PRESETS, labelCase, money, presetRange, qty, startOfMonth, titleCase, today } from "@/lib/format";
import { pageRights, usePerms } from "@/lib/perms";
import { toast } from "@/lib/toast";
import { Alert, PageHeader, Spinner, StatCard } from "@/components/ui/Bits";
import { EntitySelect } from "@/components/ui/EntitySelect";
import { Modal } from "@/components/ui/Modal";
import { SearchSelect } from "@/components/ui/SearchSelect";
import { Pagination } from "@/components/ui/DataTable";

// columns that hold codes, not amounts, however they look (a UPC, an NTN, a phone number) — exporters.IDENT_COL
export const IDENT_COL = /(^|_)(code|sku|upc|barcode|ntn|strn|cnic|phone|mobile|whatsapp|number|no|batch|reference|ref)(_|$)/i;

const AREA_ICON: Record<string, string> = { sales: "ti-shopping-cart", purchasing: "ti-shopping-bag", inventory: "ti-box", delivery: "ti-truck", receivables: "ti-receipt", payables: "ti-building-factory-2", financial: "ti-report-money" };

export function ReportsPage() {
  const q = useQuery({ queryKey: ["report-catalog"], queryFn: () => api.get("/api/v1/reports") });
  if (q.isLoading) return <Spinner />;
  const areas: Record<string, any[]> = {};
  for (const r of q.data || []) (areas[r.area] ||= []).push(r);
  return (
    <>
      <PageHeader title="Reports" crumbs={[{ label: "Reports" }]} />
      <div className="row g-3">{Object.entries(areas).map(([area, reports]) => (
        <div className="col-md-6 col-xl-4" key={area}><div className="card h-100"><div className="card-header d-flex align-items-center gap-2"><i className={`ti ${AREA_ICON[area] || "ti-chart-bar"} text-primary`} /><strong>{titleCase(area)}</strong><span className="badge bg-light text-dark ms-auto">{reports.length}</span></div><div className="list-group list-group-flush">{reports.map((r) => <Link key={r.key} to={`/app/reports/${r.key}`} className="list-group-item list-group-item-action fs-13">{r.title}</Link>)}</div></div></div>
      ))}</div>
    </>
  );
}

const ENTITY_ENDPOINTS: Record<string, { endpoint: string; query?: Record<string, any>; map: (r: any) => { id: string; label: string; sub?: string } }> = {
  warehouse: { endpoint: "/api/v1/settings/warehouses", map: (w) => ({ id: w.id, label: w.name }) }, customer: { endpoint: "/api/v1/customers", map: (c) => ({ id: c.id, label: c.name, sub: c.code }) }, supplier: { endpoint: "/api/v1/suppliers", map: (s) => ({ id: s.id, label: s.name }) },
  variant: { endpoint: "/api/v1/variants", map: (v) => ({ id: v.id, label: v.name, sub: v.sku }) }, category: { endpoint: "/api/v1/categories", map: (c) => ({ id: c.id, label: c.name }) }, account: { endpoint: "/api/v1/accounting/accounts", query: { is_group: false }, map: (a) => ({ id: a.id, label: `${a.code} ${a.name}` }) }, cost_centre: { endpoint: "/api/v1/accounting/cost-centres", map: (c) => ({ id: c.id, label: c.name }) }, rep: { endpoint: "/api/v1/supplier-reps", map: (r) => ({ id: r.id, label: r.name, sub: r.code }) },
};

/** The sale person filter: a dropdown like the report's other filters (owner, 24 Nov 2026), searchable by name or code
 *  (25 Nov). Two of the same name read with their code, as the reports name them. */
function RepSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const reps = useQuery({ queryKey: ["report-reps"], queryFn: () => api.list("/api/v1/supplier-reps", { is_active: true, page_size: 500 }), staleTime: 60_000 });
  const active = reps.data?.results || [];
  // someone no longer active, opened from a name on the summary: the filter still says who it is
  const one = useQuery({ queryKey: ["report-rep", value], queryFn: () => api.get(`/api/v1/supplier-reps/${value}`), enabled: Boolean(value && reps.data && !active.some((r: any) => r.id === value)), staleTime: 60_000, retry: false });
  const rows = [...active, ...(one.data && !active.some((r: any) => r.id === one.data.id) ? [one.data] : [])].sort((a: any, b: any) => String(a.name).localeCompare(String(b.name)));
  const seen: Record<string, number> = {};
  rows.forEach((r: any) => { const k = String(r.name).trim().toLowerCase(); seen[k] = (seen[k] || 0) + 1; });
  return <SearchSelect size="sm" value={value} onChange={onChange} options={rows.map((r: any) => ({ value: r.id, label: seen[String(r.name).trim().toLowerCase()] > 1 ? `${r.name} (${r.code})` : r.name, search: r.code }))} />;
}

type Out = "print" | "pdf" | "xlsx" | "csv";
const OUT_LABEL: Record<Out, string> = { print: "Print", pdf: "Download PDF", xlsx: "Download Excel", csv: "Download CSV" };

/** Which columns go in the file (owner, 25 Nov 2026, Sales by Sale Person). Opens on what was left out last time. */
function ColumnPicker({ out, reportKey, columns, onClose, onGo }: { out: Out; reportKey: string; columns: { key: string; label: string }[]; onClose: () => void; onGo: (hide: string[]) => void }) {
  const LS = `report.hide.${reportKey}`;
  const [hide, setHide] = useState<string[]>(() => { try { const v = JSON.parse(localStorage.getItem(LS) || "[]"); return Array.isArray(v) ? v : []; } catch { return []; } });
  const left = columns.filter((c) => !hide.includes(c.key));
  const go = () => { try { localStorage.setItem(LS, JSON.stringify(hide)); } catch { /* private window: not remembered */ } onGo(columns.filter((c) => hide.includes(c.key)).map((c) => c.key)); };
  return (
    <Modal open title="Columns" onClose={onClose} size="md" footer={<><button type="button" className="btn btn-light" onClick={onClose}>Cancel</button><button type="button" className="btn btn-primary" disabled={!left.length} onClick={go}>{OUT_LABEL[out]}</button></>}>
      <div className="d-flex gap-2 mb-3"><button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => setHide([])}>Select all</button><button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => setHide(columns.map((c) => c.key))}>Clear all</button><span className="ms-auto text-muted fs-12 align-self-center">{left.length} of {columns.length}</span></div>
      <div className="row g-2">{columns.map((c) => <div className="col-sm-6" key={c.key}><div className="form-check"><input className="form-check-input" type="checkbox" id={`col-${c.key}`} checked={!hide.includes(c.key)} onChange={(e) => setHide(e.target.checked ? hide.filter((k) => k !== c.key) : [...hide, c.key])} /><label className="form-check-label" htmlFor={`col-${c.key}`}>{c.label}</label></div></div>)}</div>
    </Modal>
  );
}

export function ReportViewPage() {
  const { key } = useParams();
  const [sp, setSp] = useSearchParams();
  const { can } = usePerms();
  const catalog = useQuery({ queryKey: ["report-catalog"], queryFn: () => api.get("/api/v1/reports") });
  const spec = catalog.data?.find((r: any) => r.key === key);
  const defaults = useMemo(() => { const d: Record<string, string> = {}; for (const p of spec?.params || []) { if (p.name === "date_from") d[p.name] = spec.opens_on === "month" ? startOfMonth() : today(); if (p.name === "date_to" || p.name === "as_of" || p.name === "date") d[p.name] = today(); if (p.default !== undefined) d[p.name] = p.default === "today" ? today() : String(p.default); } return d; }, [spec]);
  const params = { ...defaults, ...Object.fromEntries(sp.entries()) };
  const [draft, setDraft] = useState<Record<string, string> | null>(null);
  const [picked, setPicked] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [picking, setPicking] = useState<Out | null>(null);
  const form = draft || params;
  const q = useQuery({ queryKey: ["report", key, params], queryFn: () => api.get(`/api/v1/reports/${key}`, params), enabled: Boolean(spec) });
  if (catalog.isLoading) return <Spinner />;
  if (!spec) return <Alert kind="danger">Unknown report.</Alert>;
  const run = (values: Record<string, string> = form) => { setPage(1); setSp(new URLSearchParams(Object.fromEntries(Object.entries(values).filter(([, v]) => v !== ""))), { replace: true }); setDraft(null); };
  const hasRange = spec.params.some((p: any) => p.name === "date_from") && spec.params.some((p: any) => p.name === "date_to");
  const fits = (name: string) => { const [from, to] = presetRange(name); return form.date_from === from && form.date_to === to; };
  // on the 1st This Month and Today are the same dates: keep the one that was picked
  const preset = picked && fits(picked) ? picked : DATE_PRESETS.find(fits) || "";
  const pickPreset = (name: string) => { if (!name) return; const [date_from, date_to] = presetRange(name); setPicked(name); run({ ...form, date_from, date_to }); };
  const cols: string[] = q.data?.columns || [];
  // a column under a heading is named with it: "Developer 60 ml", "Keratin Straight Pack"
  const colHeads: string[] = (q.data?.header_groups || []).flatMap((g: any) => Array(g.span).fill(g.label || ""));
  const pickable = cols.map((c, i) => ({ key: c, label: `${colHeads[i] || ""} ${q.data?.column_labels?.[c] ?? titleCase(c)}`.trim() }));
  const send = (out: Out, hide: string[] = []) => {
    const query = { ...params, format: out === "print" ? "pdf" : out, ...(hide.length ? { hide_columns: hide.join(",") } : {}) };
    if (out === "print") api.printDoc(`/api/v1/reports/${key}?${new URLSearchParams(query).toString()}`);
    else api.download(`/api/v1/reports/${key}`, `${key}.${out}`, query);
  };
  const ask = (out: Out) => (spec.pick_columns && cols.length ? setPicking(out) : send(out));
  const pageCount = Math.max(1, Math.ceil((q.data?.rows.length || 0) / size));
  const isNum = (v: any) => (typeof v === "number" && Number.isFinite(v)) || (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v) && v.length < 20);
  // words left, figures right, by column (owner, 8 Nov 2026) — the same rule as the PDF (exporters.numeric_columns)
  const numCols = new Set(cols.filter((c) => !(q.data?.text_columns || []).includes(c) && !IDENT_COL.test(c) && (q.data?.rows || []).some((r: any) => isNum(r[c])) && (q.data?.rows || []).every((r: any) => r[c] === null || r[c] === undefined || r[c] === "" || isNum(r[c]))));
  // amounts arrive with four decimals and quantities with three (the API's money and quantity fields): an amount
  // always reads 1,234.56 (owner, 27 Oct 2026), a quantity keeps its own shape (12, 1.5)
  // a column of units or counts keeps its shape too ("12", never "12.00") — same rule as the PDF (exporters.QTY_COL)
  const qtyCol = (c?: string) => !!c && /(^|_)(qty|quantity|per_box|packing|count|units|boxes|pieces|days|lines|stops|orders|invoices)(_|$)/i.test(c);
  const fmtCell = (v: any, c?: string) => (v === null || v === undefined ? "" : typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) ? date(v) : isNum(v) ? (/\.\d{3}$/.test(v) || qtyCol(c) ? qty(v) : money(v)) : typeof v === "boolean" ? (v ? "yes" : "no") : typeof v === "object" ? JSON.stringify(v) : String(v));
  return (
    <>
      <PageHeader title={q.data?.title || spec.title} crumbs={[{ label: "Reports", to: "/app/reports" }, { label: spec.title }]} actions={pageRights("reports").extras.export && <><button className="btn btn-outline-secondary" onClick={() => ask("print")}><i className="ti ti-printer me-1" />Print</button><button className="btn btn-outline-secondary" onClick={() => ask("pdf")}><i className="ti ti-file-type-pdf me-1" />PDF</button><button className="btn btn-outline-success" onClick={() => ask("xlsx")}><i className="ti ti-file-type-xls me-1" />Excel</button><button className="btn btn-outline-secondary" onClick={() => ask("csv")}><i className="ti ti-file-type-csv me-1" />CSV</button></>} />
      {picking && <ColumnPicker out={picking} reportKey={spec.key} columns={pickable} onClose={() => setPicking(null)} onGo={(hide) => { const out = picking; setPicking(null); send(out, hide); }} />}
      {spec.params.length > 0 && <div className="card mb-3"><div className="card-body py-2"><form className="row g-2 align-items-end" onSubmit={(e) => { e.preventDefault(); run(); }}>
        {hasRange && <div className="col-md-2 col-6"><label className="form-label fs-11 mb-1">Preset</label><select className="form-select form-select-sm" value={preset} onChange={(e) => pickPreset(e.target.value)}><option value="">Custom</option>{DATE_PRESETS.map((n) => <option key={n} value={n}>{n}</option>)}</select></div>}
        {spec.params.map((p: any) => <div className="col-md-2 col-6" key={p.name}><label className="form-label fs-11 mb-1">{p.label ?? titleCase(p.name)}</label>{p.type === "date" ? <DateInput className="form-control form-control-sm" value={form[p.name] || ""} onChange={(e) => setDraft({ ...form, [p.name]: e.target.value })} /> : p.type === "number" ? <input type="number" className="form-control form-control-sm" value={form[p.name] || ""} onChange={(e) => setDraft({ ...form, [p.name]: e.target.value })} /> : p.type === "select" ? <select className="form-select form-select-sm" value={form[p.name] || ""} onChange={(e) => setDraft({ ...form, [p.name]: e.target.value })}>{p.options.map((o: string) => <option key={o} value={o}>{o || "all"}</option>)}</select> : p.type === "rep" ? <RepSelect value={form[p.name] || ""} onChange={(v) => setDraft({ ...form, [p.name]: v })} /> : ENTITY_ENDPOINTS[p.type] ? <EntitySelect endpoint={ENTITY_ENDPOINTS[p.type].endpoint} query={ENTITY_ENDPOINTS[p.type].query} mapOption={ENTITY_ENDPOINTS[p.type].map} value={form[p.name] || null} onChange={(v) => setDraft({ ...form, [p.name]: v || "" })} size="sm" placeholder="all" /> : <input className="form-control form-control-sm" value={form[p.name] || ""} onChange={(e) => setDraft({ ...form, [p.name]: e.target.value })} />}</div>)}
        <div className="col-auto d-flex gap-1"><button className="btn btn-primary btn-sm" type="submit"><i className="ti ti-player-play me-1" />Run</button></div>
      </form></div></div>}
      {/* a report that leads with its own cards does not repeat its totals as a second row of them (owner, 24 Nov 2026) */}
      {q.data?.totals && !q.data.totals_in_rows && !q.data.summary?.length && Object.keys(q.data.totals).length > 0 && <div className="d-flex flex-wrap gap-2 mb-3">{Object.entries(q.data.totals).map(([k, v]) => <div key={k} className="border rounded bg-white px-3 py-2"><div className="fs-11 text-muted">{q.data.column_labels?.[k] ?? titleCase(k)}</div><div className={`fw-bold ${k === "balanced" ? (v ? "text-success" : "text-danger") : ""}`}>{fmtCell(v, k)}</div></div>)}</div>}
      {/* a report can lead with a few figures (Pending payments, 24 Nov 2026) */}
      {q.data?.summary?.length ? <div className="row g-3 mb-3">{q.data.summary.map((c: any) => <div className="col-6 col-md" key={c.label}><StatCard label={labelCase(c.label)} value={money(c.value)} sub={c.sub ?? (c.count != null ? `${c.count} invoice${c.count === 1 ? "" : "s"}` : undefined)} icon={c.tone === "danger" ? "ti-alert-triangle" : c.tone === "warning" ? "ti-calendar-due" : "ti-coins"} color={c.tone || "primary"} /></div>)}</div> : null}
      <div className="card"><div className="card-body p-0">{q.isLoading ? <Spinner /> : <div className="table-responsive"><table className={`table table-sm table-hover report-table mb-0 ${q.data.header_groups && !q.data.plain_header ? "report-grouped" : ""}`}><thead className={q.data.header_groups && !q.data.plain_header ? "" : "table-light"}>{q.data.header_groups && (() => { const seps: number[] = []; let at = 0; q.data.header_groups.forEach((g: any) => { if (at > 0 && g.span > 1) seps.push(at); at += g.span; }); (q.data as any)._seps = seps; return <tr className="rg-top">{q.data.header_groups.map((g: any, i: number) => <th key={i} colSpan={g.span} className={`text-center ${g.span > 1 && i > 0 ? "rg-sep" : ""}`}>{g.label}</th>)}</tr>; })()}<tr className={q.data.header_groups ? "rg-sub" : ""}>{cols.map((c, i) => <th key={c} className={`${numCols.has(c) ? "num" : ""} ${((q.data as any)._seps || []).includes(i) ? "rg-sep" : ""}`}>{q.data.column_labels?.[c] ?? titleCase(c)}</th>)}</tr></thead>
        <tbody>{q.data?.rows.slice((Math.min(page, pageCount) - 1) * size, Math.min(page, pageCount) * size).map((r: any, i: number) => <tr key={i} className={r.is_group ? "fw-semibold table-light" : ""}>{cols.map((c, ci) => <td key={c} className={`${numCols.has(c) ? "num" : ""} ${q.data.faint_zeros && isNum(r[c]) && Number(r[c]) === 0 ? "opacity-25" : ""} ${((q.data as any)._seps || []).includes(ci) ? "rg-sep" : ""} ${({ success: "text-success", danger: "text-danger", bold: "fw-bold" } as any)[q.data.column_styles?.[c]] || ""}`} style={c === "name" && r.level ? { paddingLeft: 8 + (r.level - 1) * 16 } : undefined}>{q.data.links?.[c] && (r[q.data.links[c].href] || r[q.data.links[c].id]) ? <Link target="_blank" rel="noopener noreferrer" to={q.data.links[c].href ? r[q.data.links[c].href] : `${q.data.links[c].to}${r[q.data.links[c].id]}${q.data.links[c].query ? `?${q.data.links[c].query}` : ""}`}>{c === "view" ? <><i className="ti ti-eye me-1" />{r[c]}</> : fmtCell(r[c], c)}</Link> : c === "journal" && r.journal_id ? <Link to={`/app/accounting/journals/${r.journal_id}`}>{r[c]}</Link> : c === "name" && r.account_id && !r.is_group ? <Link to={`/app/accounting/accounts/${r.account_id}`}>{r[c]}</Link> : c === q.data.drill?.column && r.key && r.key !== "None" ? <Link to={`/app/reports/${q.data.drill.report}?${new URLSearchParams({ ...params, [q.data.drill.param]: r.key }).toString()}`}>{fmtCell(r[c], c)}</Link> : fmtCell(r[c], c)}{q.data.notes?.[c] && r[q.data.notes[c]] ? <div className="text-muted fs-11">{r[q.data.notes[c]]}</div> : null}</td>)}</tr>)}{q.data && !q.data.rows.length && <tr><td colSpan={cols.length || 1} className="text-muted text-center py-4">No data for these parameters</td></tr>}</tbody></table></div>}
        {(q.data?.rows.length || 0) > size && <Pagination page={Math.min(page, pageCount)} pages={pageCount} count={q.data.rows.length} pageSize={size} onPage={setPage} onPageSize={(n) => { setSize(n); setPage(1); }} />}</div></div>
      {q.data?.compare && <div className="card mt-3"><div className="card-header"><strong>Comparison period</strong></div><div className="card-body"><div className="d-flex flex-wrap gap-2">{Object.entries(q.data.compare.totals).map(([k, v]) => <div key={k} className="border rounded px-3 py-2"><div className="fs-11 text-muted">{titleCase(k)}</div><div className="fw-bold">{fmtCell(v, k)}</div></div>)}</div></div></div>}
    </>
  );
}
