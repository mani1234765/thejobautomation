/**
 * Minimal toast store. Replaces a third-party toaster that injected a <style> element at runtime,
 * which a strict Content-Security-Policy blocks. Rendering lives in components/ui/toaster.tsx.
 */

export type ToastItem = { id: number; kind: "success" | "error"; message: string };

type Listener = (items: ToastItem[]) => void;

let items: ToastItem[] = [];
let nextId = 1;
const listeners = new Set<Listener>();

function emit() {
  for (const l of listeners) l(items);
}

function push(kind: ToastItem["kind"], message: string) {
  items = [...items.slice(-3), { id: nextId++, kind, message }];
  emit();
}

export function dismiss(id: number) {
  items = items.filter((t) => t.id !== id);
  emit();
}

export function subscribe(l: Listener): () => void {
  listeners.add(l);
  l(items);
  return () => listeners.delete(l);
}

export const toast = {
  success: (message: string) => push("success", message),
  error: (message: string) => push("error", message),
};
