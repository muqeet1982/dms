import { labelCase } from "@/lib/format";
import { DateInput } from "@/components/ui/DateInput";
import { Fragment, ReactNode, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "@/api/client";
import { toast } from "@/lib/toast";
import { EntitySelect, Select } from "./EntitySelect";
import { Modal } from "./Modal";

export type FieldDef = {
  name: string; label: string; type?: "text" | "number" | "money" | "textarea" | "select" | "entity" | "date" | "checkbox" | "email" | "password" | "json" | "custom";
  options?: { value: string; label: string }[]; endpoint?: string; mapOption?: (r: any) => { id: string; label: string; sub?: string }; query?: Record<string, any>;
  required?: boolean; col?: string; placeholder?: string; hint?: string; default?: any; readOnly?: boolean; render?: (value: any, set: (v: any) => void, values: Record<string, any>, setField: (name: string, v: any) => void) => ReactNode; showIf?: (values: Record<string, any>) => boolean;
  /** Starts a new block in the form with this heading (and optional help text under it). */
  section?: string; sectionHint?: string; sectionIcon?: string; sectionCollapsed?: boolean;
  /** Input affordances: a fixed prefix/suffix inside the control, an icon, and typing help for phones and keypads. */
  prefix?: string; suffix?: string; icon?: string; inputMode?: "text" | "numeric" | "tel" | "email" | "decimal"; autoComplete?: string; maxLength?: number;
  /** Formats as you type and checks on blur: Pakistani CNIC / NTN / STRN / phone formats. */
  mask?: "cnic" | "ntn" | "strn" | "phone";
  /** Extra client-side check; return a message to block saving. */
  validate?: (value: any, values: Record<string, any>) => string | null;
  /** Run `validate` even when the field is empty — for cross-field rules like "NTN or CNIC". */
  alwaysValidate?: boolean;
};

const digits = (v: string) => (v || "").replace(/\D/g, "");
/** Format while typing — people read grouped identifiers far faster than a run of digits. */
export const applyMask = (mask: FieldDef["mask"], raw: string): string => {
  if (!mask) return raw;
  if (mask === "phone") {
    const d = digits(raw).replace(/^0+/, "");
    const local = d.startsWith("92") ? d.slice(2) : d;
    const p = local.slice(0, 10);
    return p ? `+92 ${p.slice(0, 3)}${p.length > 3 ? " " + p.slice(3) : ""}` : "";
  }
  const d = digits(raw);
  if (mask === "cnic") return [d.slice(0, 5), d.slice(5, 12), d.slice(12, 13)].filter(Boolean).join("-");
  if (mask === "ntn") return d.length > 7 ? `${d.slice(0, 7)}-${d.slice(7, 8)}` : d;
  if (mask === "strn") return [d.slice(0, 2), d.slice(2, 4), d.slice(4, 8), d.slice(8, 11), d.slice(11, 13)].filter(Boolean).join("-");
  return raw;
};
const MASK_RULES: Record<string, { test: (v: string) => boolean; message: string }> = {
  cnic: { test: (v) => digits(v).length === 13, message: "A CNIC has 13 digits — 35202-1234567-1" },
  ntn: { test: (v) => [7, 8].includes(digits(v).length), message: "An NTN is 7 digits plus a check digit — 1234567-8" },
  strn: { test: (v) => digits(v).length === 13, message: "An STRN has 13 digits — 03-01-2345-678-90" },
  phone: { test: (v) => digits(v).length >= 9, message: "Enter the full number, e.g. +92 300 1234567" },
};

/** Uncontrolled-ish generic form driven by FieldDefs with server-side error mapping. */
export function ResourceForm({ fields, initial, onSubmit, submitLabel = "Save", onCancel, busy, inline, dense }: { fields: FieldDef[]; initial?: Record<string, any>; onSubmit: (values: Record<string, any>) => Promise<void> | void; submitLabel?: string; onCancel?: () => void; busy?: boolean; inline?: boolean; dense?: boolean }) {
  const [values, setValues] = useState<Record<string, any>>(() => Object.fromEntries(fields.map((f) => [f.name, initial?.[f.name] ?? f.default ?? (f.type === "checkbox" ? false : "")])));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (initial) setValues((v) => ({ ...v, ...Object.fromEntries(fields.filter((f) => initial[f.name] !== undefined).map((f) => [f.name, initial[f.name] ?? ""])) }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial]);
  const set = (name: string, v: any) => setValues((s) => ({ ...s, [name]: v }));
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const errs: Record<string, string> = {};
    const missing: string[] = [];
    for (const f of fields) {
      if (f.showIf && !f.showIf(values)) continue;
      const value = values[f.name];
      const empty = value === "" || value === null || value === undefined;
      if (f.required && empty) {
        errs[f.name] = "Required";
        missing.push(f.label);
        continue;
      }
      if (!empty && f.mask && MASK_RULES[f.mask] && !MASK_RULES[f.mask].test(String(value))) errs[f.name] = MASK_RULES[f.mask].message;
      const custom = (!empty || f.alwaysValidate) && f.validate ? f.validate(value, values) : null;
      if (custom) errs[f.name] = custom;
    }
    // every complaint lands in the same submit — and a folded section holding one unfolds itself
    const reveal = (bad: Record<string, string>) => {
      const sections = new Set(fields.filter((f) => bad[f.name]).map((f) => f.section || ""));
      if (sections.size) setClosed((c) => ({ ...c, ...Object.fromEntries([...sections].map((s) => [s, false])) }));
    };
    reveal(errs);
    if (!missing.length && Object.keys(errs).length) {
      setErrors(errs);
      setFormError("Some fields need a correction — see the messages in red.");
      return;
    }
    if (missing.length) {
      setErrors(errs);
      setFormError(`Please fill in: ${missing.join(", ")}.`);
      return;
    }
    setErrors({});
    setFormError(null);
    setSaving(true);
    try {
      const out: Record<string, any> = {};
      for (const f of fields) {
        if (f.showIf && !f.showIf(values)) continue;
        let v = values[f.name];
        if (f.type === "json" && typeof v === "string") { try { v = v ? JSON.parse(v) : {}; } catch { setErrors({ [f.name]: "Invalid JSON" }); setSaving(false); return; } }
        if ((f.type === "entity" || f.type === "select" || f.type === "date") && v === "") v = null;
        // an emptied number falls back to the field's default (0 / 1); only truly optional inputs are sent as null
        if ((f.type === "number" || f.type === "money") && v === "") v = f.default ?? null;
        out[f.name] = v;
      }
      await onSubmit(out);
    } catch (err: any) {
      if (err instanceof ApiError) { const fe = err.fieldErrors(); setErrors(fe); reveal(fe); setFormError(err.message); toast.error(err.message); } else { setFormError(String(err?.message || err)); toast.error(String(err?.message || err)); }
    } finally { setSaving(false); }
  };
  const shownNames = new Set(fields.map((f) => f.name));
  const otherErrors = Object.entries(errors).filter(([k]) => !shownNames.has(k));
  const visible = fields.filter((f) => !f.showIf || f.showIf(values));
  // group the fields into the blocks their `section` declares; fields without one share a single unnamed block
  const groups: { key: string; title?: string; hint?: string; icon?: string; collapsible: boolean; fields: FieldDef[] }[] = [];
  for (const f of visible) {
    const key = f.section || "";
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.fields.push(f);
    else groups.push({ key, title: f.section, hint: f.sectionHint, icon: f.sectionIcon, collapsible: Boolean(f.sectionCollapsed), fields: [f] });
  }
  const toggle = (key: string) => setClosed((c) => ({ ...c, [key]: !(c[key] ?? true) }));
  const isClosed = (g: { key: string; collapsible: boolean }) => g.collapsible && (closed[g.key] ?? true);
  const errorsIn = (g: { fields: FieldDef[] }) => g.fields.filter((f) => errors[f.name]).length;

  const control = (f: FieldDef) => {
    const invalid = errors[f.name] ? "is-invalid" : "";
    const common = { id: `rf-${f.name}`, className: `form-control ${invalid}`, placeholder: f.placeholder, readOnly: f.readOnly, autoComplete: f.autoComplete };
    const text = (
      <input
        {...common}
        type={f.mask === "phone" ? "tel" : f.type === "money" || f.type === "number" ? "number" : f.type || "text"}
        inputMode={f.inputMode || (f.mask && f.mask !== "phone" ? "numeric" : undefined)}
        maxLength={f.maxLength}
        step={f.type === "money" ? "0.01" : f.type === "number" ? "any" : undefined}
        value={values[f.name] ?? ""}
        onChange={(e) => set(f.name, f.mask ? applyMask(f.mask, e.target.value) : e.target.value)}
        onBlur={() => {
          const v = String(values[f.name] ?? "");
          if (!v) return;
          const rule = f.mask ? MASK_RULES[f.mask] : null;
          const message = rule && !rule.test(v) ? rule.message : f.validate ? f.validate(values[f.name], values) : null;
          setErrors((e) => ({ ...e, [f.name]: message || "" }));
        }}
      />
    );
    const wrapped = f.prefix || f.suffix || f.icon
      ? <div className={`input-group ${invalid ? "has-validation" : ""}`}>
          {f.icon && <span className="input-group-text"><i className={`ti ${f.icon}`} /></span>}
          {f.prefix && <span className="input-group-text fs-13">{f.prefix}</span>}
          {text}
          {f.suffix && <span className="input-group-text fs-13">{f.suffix}</span>}
        </div>
      : text;
    if (!f.type || ["text", "email", "password", "number", "money"].includes(f.type)) return wrapped;
    if (f.type === "textarea") return <textarea className={`form-control ${invalid}`} rows={3} value={values[f.name] ?? ""} onChange={(e) => set(f.name, e.target.value)} placeholder={f.placeholder} readOnly={f.readOnly} />;
    if (f.type === "json") return <textarea className={`form-control font-monospace fs-12 ${invalid}`} rows={4} value={typeof values[f.name] === "string" ? values[f.name] : JSON.stringify(values[f.name] ?? {}, null, 2)} onChange={(e) => set(f.name, e.target.value)} />;
    if (f.type === "date") return <DateInput className={`form-control ${invalid}`} value={values[f.name] ?? ""} onChange={(e) => set(f.name, e.target.value)} readOnly={f.readOnly} />;
    if (f.type === "select") return <Select value={values[f.name]} onChange={(v) => set(f.name, v)} options={f.options || []} className={`form-select ${invalid}`} empty={f.required ? "— Select —" : "— None —"} />;
    if (f.type === "entity") return <EntitySelect endpoint={f.endpoint!} mapOption={f.mapOption!} query={f.query} value={values[f.name]} onChange={(v) => set(f.name, v)} placeholder={f.placeholder || `Select ${f.label.toLowerCase()}`} />;
    if (f.type === "checkbox") return <div className="form-check form-switch mt-2"><input className="form-check-input" type="checkbox" id={`rf-${f.name}`} checked={Boolean(values[f.name])} onChange={(e) => set(f.name, e.target.checked)} /><label className="form-check-label fs-13" htmlFor={`rf-${f.name}`}>{labelCase(f.label)}</label></div>;
    if (f.type === "custom") return f.render?.(values[f.name], (v) => set(f.name, v), values, set);
    return null;
  };

  const block = (g: (typeof groups)[number]) => (
    <div className="row">
      {g.fields.map((f) => (
        <div key={f.name} className={`${f.col || (inline ? "col-md-6" : "col-md-6")} mb-3`}>
          {f.type !== "checkbox" && <label className="form-label fs-13 fw-medium" htmlFor={`rf-${f.name}`}>{labelCase(f.label)}{f.required && <span className="text-danger"> *</span>}</label>}
          {control(f)}
          {errors[f.name] && <div className="invalid-feedback d-block">{errors[f.name]}</div>}
          {f.hint && !errors[f.name] && <div className="form-text fs-11">{f.hint}</div>}
        </div>
      ))}
    </div>
  );

  return (
    <form onSubmit={submit} noValidate className={dense ? "form-dense" : undefined}>
      {(formError || otherErrors.length > 0) && (
        <div className="alert alert-danger py-2 fs-13">
          {formError && <div>{formError}</div>}
          {otherErrors.map(([k, v]) => <div key={k}>{k === "non_field_errors" || k === "detail" ? v : <><strong>{k.replace(/_/g, " ")}:</strong> {v}</>}</div>)}
        </div>
      )}
      {groups.map((g) =>
        g.title ? (
          <div className="card border mb-3" key={g.key}>
            <div className={`card-header bg-white d-flex align-items-start gap-2 ${g.collapsible ? "cursor-pointer" : ""}`} onClick={() => g.collapsible && toggle(g.key)}>
              {g.icon && <span className="d-inline-flex align-items-center justify-content-center rounded bg-light flex-shrink-0" style={{ width: 32, height: 32 }}><i className={`ti ${g.icon} text-primary`} /></span>}
              <div className="flex-grow-1">
                <div className="fw-semibold">{g.title}{errorsIn(g) > 0 && <span className="badge bg-danger-light text-danger ms-2">{errorsIn(g)} to fix</span>}</div>
                {g.hint && <div className="fs-12 text-muted">{g.hint}</div>}
              </div>
              {g.collapsible && <i className={`ti ${isClosed(g) ? "ti-chevron-down" : "ti-chevron-up"} text-muted mt-1`} />}
            </div>
            {!isClosed(g) && <div className="card-body pb-0">{block(g)}</div>}
          </div>
        ) : (
          <Fragment key={g.key}>{block(g)}</Fragment>
        ),
      )}
      {errors.non_field_errors && <div className="alert alert-danger py-2">{errors.non_field_errors}</div>}
      <div className={`d-flex gap-2 justify-content-end align-items-center ${inline ? "" : "py-2 bg-white border-top"}`} style={inline ? undefined : { position: "sticky", bottom: 0, zIndex: 5 }}>
        {Object.values(errors).filter(Boolean).length > 0 && <span className="text-danger fs-12 me-auto"><i className="ti ti-alert-circle me-1" />{Object.values(errors).filter(Boolean).length} field(s) need attention</span>}
        {onCancel && <button type="button" className="btn btn-light" onClick={onCancel}>Cancel</button>}
        <button type="submit" className="btn btn-primary" disabled={saving || busy}>{saving ? "Saving…" : submitLabel}</button>
      </div>
    </form>
  );
}

/** Modal create/edit dialog bound to a REST endpoint. */
export function ResourceModal({ open, onClose, title, endpoint, fields, id, initial, onSaved, transform }: { open: boolean; onClose: () => void; title: string; endpoint: string; fields: FieldDef[]; id?: string | null; initial?: Record<string, any>; onSaved?: (row: any) => void; transform?: (values: Record<string, any>) => Record<string, any> }) {
  const qcRef = useQueryClient();
  return (
    <Modal open={open} onClose={onClose} title={title} size="lg">
      <ResourceForm
        fields={fields}
        initial={initial}
        onCancel={onClose}
        onSubmit={async (values) => {
          const body = transform ? transform(values) : values;
          const row = id ? await api.patch(`${endpoint}/${id}`, body) : await api.post(endpoint, body);
          qcRef.invalidateQueries({ queryKey: ["options", endpoint] });
          qcRef.invalidateQueries({ queryKey: ["entity-select", endpoint] });
          toast.success(id ? "Saved" : "Created");
          onSaved?.(row);
          onClose();
        }}
      />
    </Modal>
  );
}
