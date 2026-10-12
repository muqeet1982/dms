import { ReactNode, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { usePage } from "@/lib/perms";
import { toast } from "@/lib/toast";
import { PageHeader } from "./Bits";
import { Column, DataTable } from "./DataTable";
import { ConfirmDialog } from "./Modal";
import { FieldDef, ResourceModal } from "./ResourceForm";

/**
 * Small master-data page: unpaginated list + modal create/edit + delete. Used for categories, brands, UoMs,
 * territories, routes, vehicles, drivers, reason codes, tax classes, bins, cost centres, bank/cash accounts…
 */
export function CrudPage<T extends { id: string }>({ title, crumbs, endpoint, columns, fields, canEdit = true, canDelete = true, page, transform, extraActions, initialFor, deleteLabel = "Delete", intro, paginated, onSaved }: {
  title: string; crumbs?: { label: string; to?: string }[]; endpoint: string; columns: Column<T>[]; fields: FieldDef[]; canEdit?: boolean; canDelete?: boolean;
  /** Page key from the permission matrix: Add / Edit / Delete appear only when that page's switch is on. */
  page?: string;
  transform?: (v: Record<string, any>) => Record<string, any>; extraActions?: (row: T) => ReactNode; initialFor?: (row: T) => Record<string, any>; deleteLabel?: string; intro?: ReactNode; paginated?: boolean; onSaved?: (row: any) => void;
}) {
  const rights = usePage(page || "");
  const qc = useQueryClient();
  const [modal, setModal] = useState<{ open: boolean; row?: T | null }>({ open: false });
  const [del, setDel] = useState<T | null>(null);
  const [search, setSearch] = useState("");
  const q = useQuery({ queryKey: ["crud", endpoint, search], queryFn: async () => { const d = await api.get(endpoint, { search: search || undefined, page_size: 500 }); return (Array.isArray(d) ? d : d.results) as T[]; } });
  // also refresh the dropdowns elsewhere that read this endpoint (warehouses, price lists, reason codes…)
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["crud", endpoint] });
    qc.invalidateQueries({ queryKey: ["options", endpoint] });
    qc.invalidateQueries({ queryKey: ["entity-select", endpoint] });
  };
  const mayAdd = canEdit && (!page || rights.add);
  const mayEdit = canEdit && (!page || rights.edit);
  const mayDelete = canDelete && (!page || rights.delete);
  const cols: Column<T>[] = [...columns, { key: "_actions", header: "", className: "text-end", render: (r) => (
    <div className="d-flex gap-1 justify-content-end no-row-click">
      {extraActions?.(r)}
      {mayEdit && <button className="btn btn-sm btn-outline-secondary" onClick={() => setModal({ open: true, row: r })} title="Edit"><i className="ti ti-edit" /></button>}
      {mayDelete && <button className="btn btn-sm btn-outline-danger" onClick={() => setDel(r)} title={deleteLabel}><i className="ti ti-trash" /></button>}
    </div>
  ) }];
  return (
    <>
      <PageHeader title={title} crumbs={crumbs || [{ label: title }]} actions={mayAdd && <button className="btn btn-primary" onClick={() => setModal({ open: true, row: null })}><i className="ti ti-plus me-1" />Add</button>} />
      {intro}
      <div className="card mb-3"><div className="card-body py-2"><input className="form-control" placeholder="Search…" value={search} onChange={(e) => setSearch(e.target.value)} /></div></div>
      <div className="card"><div className="card-body p-0"><DataTable columns={cols} rows={q.data} loading={q.isLoading} onRowClick={mayEdit ? (r) => setModal({ open: true, row: r }) : undefined} /></div></div>
      <ResourceModal open={modal.open} onClose={() => setModal({ open: false })} title={modal.row ? `Edit ${title.replace(/s$/, "")}` : `New ${title.replace(/s$/, "")}`} endpoint={endpoint} fields={fields} id={modal.row?.id} initial={modal.row ? (initialFor ? initialFor(modal.row) : (modal.row as any)) : undefined} onSaved={(row) => { invalidate(); onSaved?.(row); }} transform={transform} />
      <ConfirmDialog open={Boolean(del)} title={`${deleteLabel}?`} message={<>This will remove <strong>{(del as any)?.name || (del as any)?.code || "this record"}</strong>.</>} danger confirmText={deleteLabel} onClose={() => setDel(null)} onConfirm={async () => { try { await api.delete(`${endpoint}/${del!.id}`); toast.success("Deleted"); invalidate(); } catch (e: any) { toast.error(e.message); } setDel(null); }} />
    </>
  );
}
