import { ReactNode, useEffect } from "react";
import { createPortal } from "react-dom";

export function Modal({ open, title, onClose, children, footer, size = "lg" }: { open: boolean; title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; size?: "sm" | "md" | "lg" | "xl" }) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    document.body.classList.add("modal-open");
    return () => {
      window.removeEventListener("keydown", h);
      document.body.classList.remove("modal-open");
    };
  }, [open, onClose]);
  if (!open) return null;
  const w = { sm: 440, md: 620, lg: 820, xl: 1120 }[size];
  // Rendered into <body>: a modal opened from inside a form (e.g. "add a location" on the product form) would
  // otherwise nest a <form> in a <form>, and its Save button would submit the page's form instead of its own.
  return createPortal(
    <>
      <div className="modal-backdrop fade show" style={{ zIndex: 1050 }} />
      <div className="modal fade show d-block" tabIndex={-1} role="dialog" style={{ zIndex: 1055 }} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
        <div className="modal-dialog modal-dialog-scrollable" style={{ maxWidth: w }}>
          <div className="modal-content">
            <div className="modal-header">
              <h5 className="modal-title">{title}</h5>
              <button type="button" className="btn-close" aria-label="Close" onClick={onClose} />
            </div>
            <div className="modal-body">{children}</div>
            {footer && <div className="modal-footer">{footer}</div>}
          </div>
        </div>
      </div>
    </>,
    document.body,
  );
}


export function ConfirmDialog({ open, title = "Are you sure?", message, confirmText = "Confirm", danger, onConfirm, onClose, withReason, loading }: { open: boolean; title?: string; message?: ReactNode; confirmText?: string; danger?: boolean; onConfirm: (reason?: string) => void; onClose: () => void; withReason?: boolean; loading?: boolean }) {
  if (!open) return null;
  let reason = "";
  return (
    <Modal open={open} title={title} onClose={onClose} size="sm" footer={<><button className="btn btn-light" onClick={onClose}>Cancel</button><button className={`btn ${danger ? "btn-danger" : "btn-primary"}`} disabled={loading} onClick={() => onConfirm(reason)}>{loading ? "Working…" : confirmText}</button></>}>
      {message && <p className="mb-2">{message}</p>}
      {withReason && <textarea className="form-control" rows={3} placeholder="Reason (optional)" onChange={(e) => (reason = e.target.value)} />}
    </Modal>
  );
}
