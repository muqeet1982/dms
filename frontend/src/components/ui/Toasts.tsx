import { useToasts } from "@/lib/toast";

export function Toasts() {
  const { toasts, remove } = useToasts();
  if (!toasts.length) return null;
  const icon = { success: "ti-circle-check", error: "ti-alert-circle", info: "ti-info-circle", warning: "ti-alert-triangle" };
  return (
    <div className="toast-stack">
      {toasts.map((t) => (
        <div key={t.id} className={`toast-item ${t.kind}`} onClick={() => remove(t.id)} role="status">
          <i className={`ti ${icon[t.kind]}`} />
          <span>{t.text}</span>
        </div>
      ))}
    </div>
  );
}
