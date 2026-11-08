import { DateInput } from "@/components/ui/DateInput";
import { useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { date, DATE_PRESETS, money, presetRange, qty, startOfMonth, titleCase, today } from "@/lib/format";
import { pageRights, usePerms } from "@/lib/perms";
import { toast } from "@/lib/toast";
import { Alert, PageHeader, Spinner } from "@/components/ui/Bits";
import { EntitySelect } from "@/components/ui/EntitySelect";
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

export function ReportViewPage() {
  const { key } = useParams();
  const [sp, setSp] = useSearchParams();
  const { can } = usePerms();
  const catalog = useQuery({ queryKey: ["report-catalog"], queryFn: () => api.get("/api/v1/reports") });
  const spec = catalog.data?.find((r: any) => r.key === key);
  const defaults = useMemo(() => { const d: Record<string, string> = {}; for (const p of spec?.params || []) { if (p.name === "date_from") d[p.name] = startOfMonth(); if (p.name === "date_to" || p.name === "as_of" || p.name === "date") d[p.name] = today(); if (p.default !== undefined) d[p.name] = String(p.default); } return d; }, [spec]);
  const params = { ...defaults, ...Object.fromEntries(sp.entries()) };
  const [draft, setDraft] = useState<Record<string, string> | null>(null);
  const [picked, setPicked] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
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
  const dl = (fmt: string) => api.download(`/api/v1/reports/${key}`, `${key}.${fmt}`, { ...params, format: fmt });
  const cols: string[] = q.data?.columns || [];
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
      <PageHeader title={q.data?.title || spec.title} crumbs={[{ label: "Reports", to: "/app/reports" }, { label: spec.title }]} actions={pageRights("reports").extras.export && <><button className="btn btn-outline-secondary" onClick={() => api.printDoc(`/api/v1/reports/${key}?${new URLSearchParams({ ...params, format: "pdf" }).toString()}`)}><i className="ti ti-printer me-1" />Print</button><button className="btn btn-outline-secondary" onClick={() => dl("pdf")}><i className="ti ti-file-type-pdf me-1" />PDF</button><button className="btn btn-outline-success" onClick={() => dl("xlsx")}><i className="ti ti-file-type-xls me-1" />Excel</button><button className="btn btn-outline-secondary" onClick={() => dl("csv")}><i className="ti ti-file-type-csv me-1" />CSV</button></>} />
      {spec.params.length > 0 && <div className="card mb-3"><div className="card-body py-2"><form className="row g-2 align-items-end" onSubmit={(e) => { e.preventDefault(); run(); }}>
        {hasRange && <div className="col-md-2 col-6"><label className="form-label fs-11 mb-1">Preset</label><select className="form-select form-select-sm" value={preset} onChange={(e) => pickPreset(e.target.value)}><option value="">Custom</option>{DATE_PRESETS.map((n) => <option key={n} value={n}>{n}</option>)}</select></div>}
        {spec.params.map((p: any) => <div className="col-md-2 col-6" key={p.name}><label className="form-label fs-11 mb-1">{titleCase(p.name)}</label>{p.type === "date" ? <DateInput className="form-control form-control-sm" value={form[p.name] || ""} onChange={(e) => setDraft({ ...form, [p.name]: e.target.value })} /> : p.type === "number" ? <input type="number" className="form-control form-control-sm" value={form[p.name] || ""} onChange={(e) => setDraft({ ...form, [p.name]: e.target.value })} /> : p.type === "select" ? <select className="form-select form-select-sm" value={form[p.name] || ""} onChange={(e) => setDraft({ ...form, [p.name]: e.target.value })}>{p.options.map((o: string) => <option key={o} value={o}>{o || "all"}</option>)}</select> : ENTITY_ENDPOINTS[p.type] ? <EntitySelect endpoint={ENTITY_ENDPOINTS[p.type].endpoint} query={ENTITY_ENDPOINTS[p.type].query} mapOption={ENTITY_ENDPOINTS[p.type].map} value={form[p.name] || null} onChange={(v) => setDraft({ ...form, [p.name]: v || "" })} size="sm" placeholder="all" /> : <input className="form-control form-control-sm" value={form[p.name] || ""} onChange={(e) => setDraft({ ...form, [p.name]: e.target.value })} />}</div>)}
        <div className="col-auto d-flex gap-1"><button className="btn btn-primary btn-sm" type="submit"><i className="ti ti-player-play me-1" />Run</button></div>
      </form></div></div>}
      {q.data?.totals && !q.data.totals_in_rows && Object.keys(q.data.totals).length > 0 && <div className="d-flex flex-wrap gap-2 mb-3">{Object.entries(q.data.totals).map(([k, v]) => <div key={k} className="border rounded bg-white px-3 py-2"><div className="fs-11 text-muted">{titleCase(k)}</div><div className={`fw-bold ${k === "balanced" ? (v ? "text-success" : "text-danger") : ""}`}>{fmtCell(v, k)}</div></div>)}</div>}
      <div className="card"><div className="card-body p-0">{q.isLoading ? <Spinner /> : <div className="table-responsive"><table className={`table table-sm table-hover report-table mb-0 ${q.data.header_groups && !q.data.plain_header ? "report-grouped" : ""}`}><thead className={q.data.header_groups && !q.data.plain_header ? "" : "table-light"}>{q.data.header_groups && (() => { const seps: number[] = []; let at = 0; q.data.header_groups.forEach((g: any) => { if (at > 0 && g.span > 1) seps.push(at); at += g.span; }); (q.data as any)._seps = seps; return <tr className="rg-top">{q.data.header_groups.map((g: any, i: number) => <th key={i} colSpan={g.span} className={`text-center ${g.span > 1 && i > 0 ? "rg-sep" : ""}`}>{g.label}</th>)}</tr>; })()}<tr className={q.data.header_groups ? "rg-sub" : ""}>{cols.map((c, i) => <th key={c} className={`${numCols.has(c) ? "num" : ""} ${((q.data as any)._seps || []).includes(i) ? "rg-sep" : ""}`}>{q.data.column_labels?.[c] ?? titleCase(c)}</th>)}</tr></thead>
        <tbody>{q.data?.rows.slice((Math.min(page, pageCount) - 1) * size, Math.min(page, pageCount) * size).map((r: any, i: number) => <tr key={i} className={r.is_group ? "fw-semibold table-light" : ""}>{cols.map((c, ci) => <td key={c} className={`${numCols.has(c) ? "num" : ""} ${q.data.faint_zeros && isNum(r[c]) && Number(r[c]) === 0 ? "opacity-25" : ""} ${((q.data as any)._seps || []).includes(ci) ? "rg-sep" : ""} ${({ success: "text-success", danger: "text-danger", bold: "fw-bold" } as any)[q.data.column_styles?.[c]] || ""}`} style={c === "name" && r.level ? { paddingLeft: 8 + (r.level - 1) * 16 } : undefined}>{c === "journal" && r.journal_id ? <Link to={`/app/accounting/journals/${r.journal_id}`}>{r[c]}</Link> : c === "name" && r.account_id && !r.is_group ? <Link to={`/app/accounting/accounts/${r.account_id}`}>{r[c]}</Link> : c === q.data.drill?.column && r.key && r.key !== "None" ? <Link to={`/app/reports/${q.data.drill.report}?${new URLSearchParams({ ...params, [q.data.drill.param]: r.key }).toString()}`}>{fmtCell(r[c], c)}</Link> : fmtCell(r[c], c)}{q.data.notes?.[c] && r[q.data.notes[c]] ? <div className="text-muted fs-11">{r[q.data.notes[c]]}</div> : null}</td>)}</tr>)}{q.data && !q.data.rows.length && <tr><td colSpan={cols.length || 1} className="text-muted text-center py-4">No data for these parameters</td></tr>}</tbody></table></div>}
        {(q.data?.rows.length || 0) > size && <Pagination page={Math.min(page, pageCount)} pages={pageCount} count={q.data.rows.length} pageSize={size} onPage={setPage} onPageSize={(n) => { setSize(n); setPage(1); }} />}</div></div>
      {q.data?.compare && <div className="card mt-3"><div className="card-header"><strong>Comparison period</strong></div><div className="card-body"><div className="d-flex flex-wrap gap-2">{Object.entries(q.data.compare.totals).map(([k, v]) => <div key={k} className="border rounded px-3 py-2"><div className="fs-11 text-muted">{titleCase(k)}</div><div className="fw-bold">{fmtCell(v, k)}</div></div>)}</div></div></div>}
    </>
  );
}
