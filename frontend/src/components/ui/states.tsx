import { AlertTriangle, type LucideIcon } from "lucide-react";
import type * as React from "react";
import { cn, type Tone } from "@/lib/format";
import { Button } from "./button";
import { Skeleton } from "./primitives";

export function PageHeader({
  title, description, actions, children,
}: { title: string; description?: React.ReactNode; actions?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <header className="flex flex-col gap-3 pb-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold tracking-[-0.01em] text-fg">{title}</h1>
          {description && <p className="mt-0.5 text-[13px] text-fg-muted">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </header>
  );
}

export function EmptyState({
  icon: Icon, title, description, action, className, compact,
}: {
  icon: LucideIcon; title: string; description?: React.ReactNode; action?: React.ReactNode; className?: string; compact?: boolean;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center text-center", compact ? "px-4 py-8" : "px-6 py-16", className)}>
      <div className="mb-3 flex size-9 items-center justify-center rounded-lg border border-border bg-surface-2 text-fg-muted">
        <Icon className="size-4" aria-hidden />
      </div>
      <p className="text-[13px] font-medium text-fg">{title}</p>
      {description && <p className="mt-1 max-w-sm text-[13px] leading-relaxed text-fg-muted">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  const message = error instanceof Error ? error.message : "Something went wrong.";
  return (
    <div role="alert" className={cn("flex flex-col items-center justify-center px-6 py-12 text-center", className)}>
      <div className="mb-3 flex size-9 items-center justify-center rounded-lg bg-danger-soft text-danger">
        <AlertTriangle className="size-4" aria-hidden />
      </div>
      <p className="text-[13px] font-medium">Couldn&apos;t load this</p>
      <p className="mt-1 max-w-sm text-[13px] text-fg-muted">{message}</p>
      {onRetry && (
        <Button className="mt-4" size="sm" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}

export function Callout({
  tone = "info", icon: Icon, title, children, action, className,
}: { tone?: Tone; icon?: LucideIcon; title?: string; children?: React.ReactNode; action?: React.ReactNode; className?: string }) {
  const tones: Record<Tone, string> = {
    neutral: "bg-surface-2 border-border",
    info: "bg-info-soft/60 border-info/20",
    accent: "bg-accent-soft/60 border-accent/20",
    positive: "bg-positive-soft/60 border-positive/20",
    warning: "bg-warning-soft/70 border-warning/25",
    danger: "bg-danger-soft/70 border-danger/20",
  };
  const iconTone: Record<Tone, string> = {
    neutral: "text-fg-muted", info: "text-info", accent: "text-accent", positive: "text-positive", warning: "text-warning", danger: "text-danger",
  };
  return (
    <div className={cn("flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-[13px]", tones[tone], className)}>
      {Icon && <Icon className={cn("mt-0.5 size-4 shrink-0", iconTone[tone])} aria-hidden />}
      <div className="min-w-0 flex-1">
        {title && <p className="font-medium text-fg">{title}</p>}
        {children && <div className={cn("text-fg-muted leading-relaxed", title && "mt-0.5")}>{children}</div>}
      </div>
      {action}
    </div>
  );
}

export function ListSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="divide-y divide-border" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 px-4 py-3.5">
          <Skeleton className="size-8 rounded-md" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-3 w-1/3" />
            <Skeleton className="h-2.5 w-1/2" />
          </div>
          <Skeleton className="h-5 w-12" />
        </div>
      ))}
    </div>
  );
}
