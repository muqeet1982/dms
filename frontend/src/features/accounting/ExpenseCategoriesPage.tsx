import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { money, startOfMonth, today } from "@/lib/format";
import { usePage } from "@/lib/perms";
import { DateInput } from "@/components/ui/DateInput";
import { Alert, Field, PageHeader, Spinner } from "@/components/ui/Bits";
import { FieldDef, ResourceModal } from "@/components/ui/ResourceForm";

/**
 * The categories an expense is booked to.
 *
 * They are not a list of their own: an expense category **is** an expense account in the chart, which is why
 * every rupee spent lands in the ledger and shows up in the income statement without anybody reconciling two
 * lists. This page is the chart filtered down to the part a cashier deals with, with what has actually been
 * spent against each one beside it — the question people come here to answer.
 */
export function ExpenseCategoriesPage() {
  const page = usePage("accounts");
  const qc = useQueryClient();
  const [from, setFrom] = useState(startOfMonth());
  const [to, setTo] = useState(today());
  const [modal, setModal] = useState<{ open: boolean; row?: any }>({ open: false });

  const cats = useQuery({
    queryKey: ["expense-categories"],
    queryFn: () => api.get("/api/v1/accounting/accounts", { type: "expense", is_group: false }),
  });
  const spend = useQuery({
    queryKey: ["expense-by-category", from, to],
    queryFn: () => api.get("/api/v1/reports/expense_by_category", { date_from: from, date_to: to }),
  });

  const spentBy: Record<string, number> = {};
  for (const r of spend.data?.rows || []) {
    const key = String(r.category || r.account || r.name || "");
    spentBy[key] = Number(r.amount || r.total || 0);
  }
  const rows: any[] = cats.data || [];
  const total = rows.reduce((s, a) => s + (spentBy[a.name] || 0), 0);

  const fields: FieldDef[] = [
    { name: "code", label: "Code", required: true, col: "col-md-4", hint: "Where it sits in the chart, e.g. 5-02-001" },
    { name: "name", label: "Category name", required: true, col: "col-md-8", placeholder: "Fuel, Shop rent, Staff tea…" },
    { name: "parent", label: "Sits under", type: "entity", endpoint: "/api/v1/accounting/accounts", query: { is_group: true, type: "expense" },
      mapOption: (a: any) => ({ id: a.id, label: `${a.code} ${a.name}` }), col: "col-md-6", placeholder: "An expense group" },
    { name: "description", label: "What goes in here", col: "col-md-6", placeholder: "So the next person books it the same way" },
    { name: "is_active", label: "In use", type: "checkbox", default: true, col: "col-md-3" },
  ];

  return (
    <>
      <PageHeader title="Expense categories" crumbs={[{ label: "Accounting", to: "/app/accounting" }, { label: "Expense categories" }]}
        actions={<>
          <button className="btn btn-outline-secondary" onClick={() => api.printDoc("/api/v1/reports/expense_by_category", { date_from: from, date_to: to, format: "pdf" })}><i className="ti ti-printer me-1" />Print</button>
          <button className="btn btn-outline-secondary" onClick={() => api.download("/api/v1/reports/expense_by_category", "expense-by-category.pdf", { date_from: from, date_to: to, format: "pdf" })}><i className="ti ti-file-type-pdf me-1" />PDF</button>
          <button className="btn btn-outline-success" onClick={() => api.download("/api/v1/reports/expense_by_category", "expense-by-category.xlsx", { date_from: from, date_to: to, format: "xlsx" })}><i className="ti ti-file-spreadsheet me-1" />Excel</button>
          {page.add && <button className="btn btn-primary" onClick={() => setModal({ open: true })}><i className="ti ti-plus me-1" />Add category</button>}
        </>} />

      <Alert kind="info">A category is an expense account in the <Link to="/app/accounting/accounts">chart of accounts</Link>, so whatever is spent against it appears in the income statement without anybody keeping a second list.</Alert>

      <div className="card mb-3"><div className="card-body py-3"><div className="row align-items-end">
        <Field label="Spending from" col="col-6 col-md-3"><DateInput className="form-control" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
        <Field label="to" col="col-6 col-md-3"><DateInput className="form-control" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
        <div className="col-md-6 text-md-end fs-13 text-muted">Spent in this period <strong className="text-dark fs-16 ms-1">{money(total)}</strong></div>
      </div></div></div>

      {cats.isLoading ? <Spinner /> : (
        <div className="card"><div className="card-body p-0"><div className="table-responsive">
          <table className="table table-sm align-middle mb-0">
            <thead className="table-light"><tr>
              <th style={{ width: 130 }}>Code</th><th>Category</th><th>Sits under</th>
              <th className="num" style={{ width: 170 }}>Spent in period</th><th style={{ width: 90 }}>In use</th><th style={{ width: 60 }} />
            </tr></thead>
            <tbody>
              {rows.map((a: any) => (
                <tr key={a.id}>
                  <td className="font-monospace fs-12 text-muted">{a.code}</td>
                  <td className="fw-medium"><Link to={`/app/accounting/accounts/${a.id}`}>{a.name}</Link>{a.description && <div className="fs-11 text-muted">{a.description}</div>}</td>
                  <td className="fs-12 text-muted">{a.parent_name || "—"}</td>
                  <td className="num">{spentBy[a.name] ? money(spentBy[a.name]) : <span className="text-muted">—</span>}</td>
                  <td>{a.is_active ? <span className="badge bg-success-transparent fs-11">Yes</span> : <span className="badge bg-secondary-transparent fs-11">No</span>}</td>
                  <td className="text-end">{page.edit && <button className="btn btn-sm btn-link p-0" title="Edit" onClick={() => setModal({ open: true, row: a })}><i className="ti ti-edit" /></button>}</td>
                </tr>
              ))}
              {!rows.length && <tr><td colSpan={6} className="text-center text-muted py-4">No expense categories yet — add the ones you spend on.</td></tr>}
            </tbody>
          </table>
        </div></div></div>
      )}

      <ResourceModal open={modal.open} onClose={() => setModal({ open: false })}
        title={modal.row ? `Edit ${modal.row.name}` : "New expense category"}
        endpoint="/api/v1/accounting/accounts" id={modal.row?.id}
        initial={modal.row || { type: "expense", is_group: false, is_active: true, allow_manual_posting: true }}
        onSaved={() => { qc.invalidateQueries({ queryKey: ["expense-categories"] }); qc.invalidateQueries({ queryKey: ["coa-tree"] }); }}
        fields={fields} />
    </>
  );
}
