"use client";

import { Check, X } from "lucide-react";
import { Checkbox as CheckboxPrimitive, Switch as SwitchPrimitive, Tabs as TabsPrimitive } from "radix-ui";
import * as React from "react";
import { cn } from "@/lib/format";

export function Switch({ className, ...props }: React.ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      className={cn(
        "inline-flex h-[18px] w-8 shrink-0 cursor-pointer items-center rounded-full border border-transparent bg-border-strong transition-colors data-[state=checked]:bg-accent disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="block size-3.5 translate-x-0.5 rounded-full bg-white shadow-sm transition-transform data-[state=checked]:translate-x-[15px]" />
    </SwitchPrimitive.Root>
  );
}

export function Checkbox({ className, ...props }: React.ComponentProps<typeof CheckboxPrimitive.Root>) {
  return (
    <CheckboxPrimitive.Root
      className={cn(
        "flex size-4 shrink-0 items-center justify-center rounded-[4px] border border-border-strong bg-surface shadow-sm transition-colors data-[state=checked]:border-accent data-[state=checked]:bg-accent data-[state=checked]:text-accent-fg",
        className,
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator>
        <Check className="size-3" strokeWidth={3} />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}

export const Tabs = TabsPrimitive.Root;

export function TabsList({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.List>) {
  return <TabsPrimitive.List className={cn("flex items-center gap-4 border-b border-border", className)} {...props} />;
}

export function TabsTrigger({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        "-mb-px flex h-9 items-center gap-1.5 border-b-2 border-transparent text-[13px] font-medium text-fg-muted transition-colors hover:text-fg data-[state=active]:border-fg data-[state=active]:text-fg",
        className,
      )}
      {...props}
    />
  );
}

export const TabsContent = TabsPrimitive.Content;

/** Compact segmented control for 2–5 mutually exclusive options. */
export function Segmented<T extends string>({
  value, onChange, options, className, ariaLabel,
}: {
  value: T; onChange: (v: T) => void; options: { value: T; label: React.ReactNode }[]; className?: string; ariaLabel: string;
}) {
  return (
    <div role="radiogroup" aria-label={ariaLabel} className={cn("inline-flex h-8 items-center rounded-md bg-surface-3 p-0.5", className)}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "h-7 rounded-[5px] px-2.5 text-xs font-medium text-fg-muted transition-all",
            value === o.value ? "bg-surface text-fg shadow-sm" : "hover:text-fg",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Multi-select pill group (filters, preferences). */
export function ChipGroup<T extends string>({
  value, onChange, options, ariaLabel,
}: {
  value: T[]; onChange: (v: T[]) => void; options: readonly { value: T; label: string }[]; ariaLabel: string;
}) {
  return (
    <div role="group" aria-label={ariaLabel} className="flex flex-wrap gap-1.5">
      {options.map((o) => {
        const on = value.includes(o.value);
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(on ? value.filter((v) => v !== o.value) : [...value, o.value])}
            className={cn(
              "h-7 rounded-full border px-2.5 text-xs font-medium transition-colors",
              on ? "border-accent bg-accent-soft text-accent-soft-fg" : "border-border bg-surface text-fg-muted hover:border-border-strong hover:text-fg",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** Free-form tag entry: type and press Enter or comma. */
export function TagInput({
  value, onChange, placeholder, id, max = 15,
}: { value: string[]; onChange: (v: string[]) => void; placeholder?: string; id?: string; max?: number }) {
  const [draft, setDraft] = React.useState("");
  const add = (raw: string) => {
    const t = raw.trim().replace(/,$/, "");
    if (!t || value.some((v) => v.toLowerCase() === t.toLowerCase()) || value.length >= max) return;
    onChange([...value, t]);
  };
  return (
    <div className="flex min-h-8 flex-wrap items-center gap-1 rounded-md border border-border bg-surface px-1.5 py-1 shadow-sm focus-within:border-accent focus-within:ring-3 focus-within:ring-ring">
      {value.map((t) => (
        <span key={t} className="inline-flex h-6 items-center gap-1 rounded-sm bg-surface-3 pl-2 pr-1 text-xs">
          {t}
          <button type="button" aria-label={`Remove ${t}`} onClick={() => onChange(value.filter((v) => v !== t))}
            className="rounded-sm p-0.5 text-fg-subtle hover:bg-border hover:text-fg">
            <X className="size-3" />
          </button>
        </span>
      ))}
      <input
        id={id}
        value={draft}
        placeholder={value.length ? "" : placeholder}
        onChange={(e) => {
          if (e.target.value.endsWith(",")) {
            add(e.target.value);
            setDraft("");
          } else setDraft(e.target.value);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            add(draft);
            setDraft("");
          } else if (e.key === "Backspace" && !draft && value.length) {
            onChange(value.slice(0, -1));
          }
        }}
        onBlur={() => {
          add(draft);
          setDraft("");
        }}
        className="h-6 min-w-24 flex-1 bg-transparent px-1 text-[13px] outline-none placeholder:text-fg-subtle"
      />
    </div>
  );
}
