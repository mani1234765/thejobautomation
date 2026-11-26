"use client";

import { Label as LabelPrimitive, Separator as SeparatorPrimitive } from "radix-ui";
import * as React from "react";
import { cn, type Tone } from "@/lib/format";

const field =
  "w-full rounded-md border border-border bg-surface px-2.5 text-[13px] text-fg shadow-sm placeholder:text-fg-subtle transition-colors hover:border-border-strong focus-visible:border-accent focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring disabled:opacity-50 aria-invalid:border-danger";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => <input ref={ref} className={cn(field, "h-8", className)} {...props} />,
);
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea ref={ref} className={cn(field, "min-h-20 py-2 leading-relaxed", className)} {...props} />
  ),
);
Textarea.displayName = "Textarea";

export const NativeSelect = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => (
    <select ref={ref} className={cn(field, "h-8 pr-7 appearance-none bg-[length:12px] bg-[right_0.6rem_center] bg-no-repeat", className)}
      style={{ backgroundImage: "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")" }}
      {...props}>
      {children}
    </select>
  ),
);
NativeSelect.displayName = "NativeSelect";

export function Label({ className, ...props }: React.ComponentProps<typeof LabelPrimitive.Root>) {
  return <LabelPrimitive.Root className={cn("text-xs font-medium text-fg", className)} {...props} />;
}

export function Field({
  label, hint, error, children, htmlFor, className, optional,
}: {
  label: string; hint?: React.ReactNode; error?: string; children: React.ReactNode; htmlFor?: string;
  className?: string; optional?: boolean;
}) {
  // Bind the label to a single form control automatically when no explicit id is given.
  const autoId = React.useId();
  const only = React.Children.count(children) === 1 && React.isValidElement(children) ? (children as React.ReactElement<{ id?: string }>) : null;
  const bindable = only && typeof only.type !== "string" ? true : only?.type === "input" || only?.type === "textarea" || only?.type === "select";
  const controlId = htmlFor ?? only?.props.id ?? (bindable ? autoId : undefined);
  const control = only && bindable && !only.props.id && !htmlFor ? React.cloneElement(only, { id: controlId }) : children;
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label htmlFor={controlId}>
        {label}
        {optional && <span className="ml-1 font-normal text-fg-subtle">Optional</span>}
      </Label>
      {control}
      {error ? (
        <p className="text-xs text-danger" role="alert">{error}</p>
      ) : hint ? (
        <p className="text-xs text-fg-muted">{hint}</p>
      ) : null}
    </div>
  );
}

const toneClass: Record<Tone, string> = {
  neutral: "bg-surface-3 text-fg-muted",
  accent: "bg-accent-soft text-accent-soft-fg",
  positive: "bg-positive-soft text-positive",
  warning: "bg-warning-soft text-warning",
  danger: "bg-danger-soft text-danger",
  info: "bg-info-soft text-info",
};

export function Badge({
  tone = "neutral", dot, className, children, ...props
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: Tone; dot?: boolean }) {
  return (
    <span
      className={cn("inline-flex h-5 items-center gap-1 rounded-sm px-1.5 text-2xs font-medium whitespace-nowrap", toneClass[tone], className)}
      {...props}
    >
      {dot && <span className="size-1.5 rounded-full bg-current" aria-hidden />}
      {children}
    </span>
  );
}

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-lg border border-border bg-surface shadow-sm", className)} {...props} />;
}

export function CardHeader({
  title, description, action, className,
}: { title: React.ReactNode; description?: React.ReactNode; action?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-start justify-between gap-3 px-4 pt-3.5 pb-2", className)}>
      <div className="min-w-0">
        <h2 className="text-[13px] font-semibold text-fg">{title}</h2>
        {description && <p className="mt-0.5 text-xs text-fg-muted">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("animate-shimmer rounded-md bg-surface-3", className)} aria-hidden {...props} />;
}

export function Separator({ className, ...props }: React.ComponentProps<typeof SeparatorPrimitive.Root>) {
  return <SeparatorPrimitive.Root className={cn("shrink-0 bg-border data-[orientation=horizontal]:h-px data-[orientation=horizontal]:w-full data-[orientation=vertical]:w-px data-[orientation=vertical]:self-stretch", className)} {...props} />;
}

export function Kbd({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <kbd className={cn("inline-flex h-4.5 min-w-4.5 items-center justify-center rounded-sm border border-border bg-surface-2 px-1 font-sans text-[10px] font-medium text-fg-muted", className)}>
      {children}
    </kbd>
  );
}

export function Progress({ value, tone = "accent", className, label }: { value: number; tone?: Tone; className?: string; label?: string }) {
  const bar: Record<Tone, string> = {
    neutral: "bg-fg-subtle", accent: "bg-accent", positive: "bg-positive", warning: "bg-warning", danger: "bg-danger", info: "bg-info",
  };
  const v = Math.max(0, Math.min(100, value));
  return (
    <div className={cn("h-1.5 w-full overflow-hidden rounded-full bg-surface-3", className)} role="progressbar"
      aria-valuenow={Math.round(v)} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
      <div className={cn("h-full rounded-full transition-[width] duration-500", bar[tone])} style={{ width: `${v}%` }} />
    </div>
  );
}
