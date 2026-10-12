import { useEffect, useRef } from "react";

/**
 * Global HID scanner listener (keyboard wedge): fast keystrokes ending with Enter → onScan(code).
 * Ignores typing inside inputs unless `captureInInputs` is set.
 */
export function useScanListener(onScan: (code: string) => void, opts: { enabled?: boolean; captureInInputs?: boolean; minLength?: number } = {}) {
  const buf = useRef("");
  const last = useRef(0);
  const cb = useRef(onScan);
  cb.current = onScan;
  useEffect(() => {
    if (opts.enabled === false) return;
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const inInput = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      if (inInput && !opts.captureInInputs) return;
      const now = Date.now();
      if (now - last.current > 80) buf.current = "";
      last.current = now;
      if (e.key === "Enter") {
        const code = buf.current.trim();
        buf.current = "";
        if (code.length >= (opts.minLength ?? 4)) {
          e.preventDefault();
          cb.current(code);
        }
        return;
      }
      if (e.key.length === 1) buf.current += e.key;
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [opts.enabled, opts.captureInInputs, opts.minLength]);
}
