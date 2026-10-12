import { useState } from "react";
import { Field } from "./Bits";

const RULES = [
  { key: "length", label: "8 characters or more", test: (v: string) => v.length >= 8 },
  { key: "lower", label: "a lowercase letter", test: (v: string) => /[a-z]/.test(v) },
  { key: "upper", label: "an uppercase letter", test: (v: string) => /[A-Z]/.test(v) },
  { key: "digit", label: "a number", test: (v: string) => /\d/.test(v) },
];

export const passwordProblems = (value: string) => RULES.filter((r) => !r.test(value || "")).map((r) => r.label);
export const passwordOk = (value: string) => passwordProblems(value).length === 0;
export const suggestPassword = () => {
  const words = ["Falcon", "Harbor", "Lumen", "Cobalt", "Zephyr", "Quartz", "Nimbus", "Orchid", "Cedar", "Vertex"];
  const pick = () => words[Math.floor(Math.random() * words.length)];
  return `${pick()}-${Math.floor(1000 + Math.random() * 8999)}-${pick().toLowerCase()}`;
};

/**
 * Password + confirmation with the rules shown as you type. The same rules are enforced on the server, so what
 * the ticks say here is exactly what will be accepted.
 */
export function PasswordFields({
  value, onChange, confirm, onConfirmChange, label = "Password", confirmLabel = "Confirm password", hint, col = "col-md-6", required,
}: {
  value: string; onChange: (v: string) => void; confirm?: string; onConfirmChange?: (v: string) => void;
  label?: string; confirmLabel?: string; hint?: string; col?: string; required?: boolean;
}) {
  const [show, setShow] = useState(false);
  const problems = passwordProblems(value);
  const strength = 4 - problems.length;
  const mismatch = onConfirmChange !== undefined && Boolean(value) && Boolean(confirm) && value !== confirm;
  return (
    <>
      <Field label={`${label}${required ? " *" : ""}`} col={col} hint={hint}>
        <div className="input-group">
          <input type={show ? "text" : "password"} className={`form-control ${value && problems.length ? "is-invalid" : ""}`} value={value} autoComplete="new-password" onChange={(e) => onChange(e.target.value)} />
          <button type="button" className="btn btn-outline-secondary" tabIndex={-1} title={show ? "Hide" : "Show"} onClick={() => setShow(!show)}><i className={`ti ${show ? "ti-eye-off" : "ti-eye"}`} /></button>
          <button type="button" className="btn btn-outline-secondary" tabIndex={-1} title="Suggest a strong password" onClick={() => { const p = suggestPassword(); onChange(p); onConfirmChange?.(p); setShow(true); }}><i className="ti ti-refresh" /></button>
        </div>
        {value && (
          <>
            <div className="progress mt-2" style={{ height: 4 }}>
              <div className={`progress-bar ${strength >= 4 ? "bg-success" : strength >= 3 ? "bg-warning" : "bg-danger"}`} style={{ width: `${(strength / 4) * 100}%` }} />
            </div>
            <div className="fs-11 mt-1">
              {RULES.map((r) => (
                <span key={r.key} className={`me-2 ${r.test(value) ? "text-success" : "text-muted"}`}>
                  <i className={`ti ${r.test(value) ? "ti-check" : "ti-point"} me-1`} />{r.label}
                </span>
              ))}
            </div>
          </>
        )}
      </Field>
      {onConfirmChange !== undefined && (
        <Field label={`${confirmLabel}${required ? " *" : ""}`} col={col}>
          <input type={show ? "text" : "password"} className={`form-control ${mismatch ? "is-invalid" : ""}`} value={confirm ?? ""} autoComplete="new-password" onChange={(e) => onConfirmChange(e.target.value)} />
          {mismatch && <div className="invalid-feedback d-block">The two passwords do not match.</div>}
        </Field>
      )}
    </>
  );
}
