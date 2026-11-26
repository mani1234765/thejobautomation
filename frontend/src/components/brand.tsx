import { cn } from "@/lib/format";

/** JobPilot mark: a heading line climbing to a waypoint — direction with a destination. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={cn("size-6", className)} aria-hidden>
      <rect width="24" height="24" rx="6.5" fill="var(--fg)" />
      <path d="M6 17.5 L13 10.5" stroke="var(--bg)" strokeWidth="2.2" strokeLinecap="round" />
      <path d="M13 10.5 h-3.4 M13 10.5 v3.4" stroke="var(--bg)" strokeWidth="2.2" strokeLinecap="round" />
      <circle cx="17" cy="6.8" r="2.1" fill="var(--accent)" />
    </svg>
  );
}

export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <LogoMark />
      <span className="text-[15px] font-semibold tracking-[-0.02em]">JobPilot</span>
    </span>
  );
}
