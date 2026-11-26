"use client";

import { CircleAlert, CircleCheck, X } from "lucide-react";
import { useEffect, useState } from "react";
import { dismiss, subscribe, type ToastItem } from "@/lib/toast";

const DURATION_MS = 4500;

function ToastRow({ t }: { t: ToastItem }) {
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (paused) return;
    const timer = setTimeout(() => dismiss(t.id), t.kind === "error" ? DURATION_MS * 1.5 : DURATION_MS);
    return () => clearTimeout(timer);
  }, [t, paused]);
  return (
    <li
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      className="pointer-events-auto flex items-start gap-2.5 rounded-lg border border-border bg-surface px-3.5 py-3 text-[13px] text-fg shadow-lg animate-in fade-in slide-in-from-bottom-2"
    >
      {t.kind === "success" ? (
        <CircleCheck className="mt-px size-4 shrink-0 text-positive" aria-hidden />
      ) : (
        <CircleAlert className="mt-px size-4 shrink-0 text-danger" aria-hidden />
      )}
      <p className="min-w-0 flex-1 leading-snug">{t.message}</p>
      <button type="button" aria-label="Dismiss notification" onClick={() => dismiss(t.id)}
        className="rounded-sm p-0.5 text-fg-subtle hover:bg-surface-3 hover:text-fg">
        <X className="size-3.5" />
      </button>
    </li>
  );
}

/**
 * Toasts are announced through ARIA live regions and never take focus or keyboard events, so Escape
 * keeps closing whatever dialog the user has open.
 */
export function Toaster() {
  const [items, setItems] = useState<ToastItem[]>([]);
  useEffect(() => subscribe(setItems), []);
  return (
    <section aria-label="Notifications" className="pointer-events-none fixed right-4 bottom-4 z-[100] w-[calc(100vw-32px)] max-w-sm">
      <div role="status" aria-live="polite">
        <ol className="flex flex-col gap-2">
          {items.filter((t) => t.kind === "success").map((t) => <ToastRow key={t.id} t={t} />)}
        </ol>
      </div>
      <div role="alert" aria-live="assertive">
        <ol className="mt-2 flex flex-col gap-2 empty:mt-0">
          {items.filter((t) => t.kind === "error").map((t) => <ToastRow key={t.id} t={t} />)}
        </ol>
      </div>
    </section>
  );
}
