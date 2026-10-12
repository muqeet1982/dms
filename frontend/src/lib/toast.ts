import { create } from "zustand";

export type ToastKind = "success" | "error" | "info" | "warning";
type Toast = { id: number; kind: ToastKind; text: string };
type State = { toasts: Toast[]; push: (kind: ToastKind, text: string) => void; remove: (id: number) => void };

let seq = 1;
export const useToasts = create<State>((set) => ({
  toasts: [],
  push: (kind, text) => {
    const id = seq++;
    set((s) => ({ toasts: [...s.toasts, { id, kind, text }] }));
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), kind === "error" ? 6000 : 3000);
  },
  remove: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

export const toast = {
  success: (t: string) => useToasts.getState().push("success", t),
  error: (t: string) => useToasts.getState().push("error", t),
  info: (t: string) => useToasts.getState().push("info", t),
  warning: (t: string) => useToasts.getState().push("warning", t),
};
