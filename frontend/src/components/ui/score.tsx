import { cn, scoreTone } from "@/lib/format";

const ringColor = {
  positive: "var(--positive)", accent: "var(--accent)", warning: "var(--warning)", neutral: "var(--fg-subtle)",
  danger: "var(--danger)", info: "var(--info)",
} as const;

/** Circular match score. `null` renders as an explicit "not scored" state rather than 0. */
export function ScoreRing({ score, size = 36, className }: { score: number | null; size?: number; className?: string }) {
  const stroke = size >= 48 ? 4 : 3;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const tone = scoreTone(score);
  const value = score ?? 0;
  return (
    <div className={cn("relative inline-flex shrink-0 items-center justify-center", className)} style={{ width: size, height: size }}
      role="img" aria-label={score == null ? "Not scored" : `Match score ${score} out of 100`}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-3)" strokeWidth={stroke} />
        {score != null && (
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={ringColor[tone]} strokeWidth={stroke}
            strokeDasharray={c} strokeDashoffset={c * (1 - value / 100)} strokeLinecap="round"
            className="transition-[stroke-dashoffset] duration-700" />
        )}
      </svg>
      <span className={cn("absolute tabular font-semibold text-fg", size >= 48 ? "text-sm" : "text-[11px]")}>
        {score ?? "–"}
      </span>
    </div>
  );
}

/** Horizontal factor bar used in match explanations. */
export function FactorBar({ value }: { value: number | null }) {
  if (value == null) {
    return <div className="h-1.5 w-full rounded-full border border-dashed border-border-strong" aria-label="Unknown" />;
  }
  const tone = value >= 0.8 ? "bg-positive" : value >= 0.5 ? "bg-accent" : value >= 0.3 ? "bg-warning" : "bg-danger";
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-3">
      <div className={cn("h-full rounded-full", tone)} style={{ width: `${Math.round(value * 100)}%` }} />
    </div>
  );
}
