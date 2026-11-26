import Link from "next/link";
import { cn, PIPELINE, STATUS_META } from "@/lib/format";
import type { AppStatus } from "@/lib/types";

/** Stage-by-stage pipeline strip. Counts are live from the API; empty stages stay visible but quiet. */
export function PipelineStrip({ counts }: { counts: { status: AppStatus; count: number }[] }) {
  const by = Object.fromEntries(counts.map((c) => [c.status, c.count])) as Record<AppStatus, number>;
  const total = PIPELINE.reduce((a, s) => a + (by[s] ?? 0), 0);
  const closed: AppStatus[] = ["rejected", "withdrawn", "failed"];
  return (
    <div>
      <ol className="grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-5 xl:grid-cols-9">
        {PIPELINE.map((s) => {
          const n = by[s] ?? 0;
          return (
            <li key={s} className="bg-surface">
              <Link href={`/applications?status=${s}`}
                className="group flex h-full flex-col gap-2 px-3 py-2.5 transition-colors hover:bg-surface-2">
                <span className="line-clamp-2 min-h-8 text-2xs leading-4 font-medium hyphens-auto [overflow-wrap:anywhere] text-fg-muted group-hover:text-fg">
                  {STATUS_META[s].label}
                </span>
                <span className={cn("tabular text-lg font-semibold leading-none", n ? "text-fg" : "text-fg-subtle/60")}>{n}</span>
                <span className="h-1 overflow-hidden rounded-full bg-surface-3">
                  <span className={cn("block h-full rounded-full", s === "offer" ? "bg-positive" : s === "awaiting_approval" ? "bg-warning" : "bg-accent")}
                    style={{ width: total ? `${(n / total) * 100}%` : 0 }} />
                </span>
              </Link>
            </li>
          );
        })}
      </ol>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-muted">
        {closed.map((s) => (
          <Link key={s} href={`/applications?status=${s}`} className="hover:text-fg">
            {STATUS_META[s].label} <span className="tabular font-medium text-fg">{by[s] ?? 0}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}
