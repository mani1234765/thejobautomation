"use client";

import { Info } from "lucide-react";
import { useMemo, useState } from "react";
import { BarList, SERIES_COLORS, TrendChart } from "@/components/charts";
import { Segmented } from "@/components/ui/controls";
import { Tooltip } from "@/components/ui/overlay";
import { Card, CardHeader, Skeleton } from "@/components/ui/primitives";
import { EmptyState, ErrorState, PageHeader } from "@/components/ui/states";
import { formatDate, percent, STATUS_META } from "@/lib/format";
import { useAnalytics } from "@/lib/queries";
import type { AppStatus } from "@/lib/types";
import { BarChart3 } from "lucide-react";

const RANGES = { "7": 7, "30": 30, "90": 90, "365": 365 } as const;

function Kpi({ label, value, definition, sub }: { label: string; value: string; definition?: string; sub?: string }) {
  return (
    <div className="bg-surface px-4 py-3">
      <p className="flex items-center gap-1 text-xs text-fg-muted">{label}
        {definition && <Tooltip content={definition}><Info className="size-3 text-fg-subtle" aria-label={definition} /></Tooltip>}
      </p>
      <p className="tabular mt-1 text-[22px] font-semibold tracking-[-0.02em]">{value}</p>
      {sub && <p className="text-2xs text-fg-subtle">{sub}</p>}
    </div>
  );
}

export default function AnalyticsPage() {
  const [days, setDays] = useState<keyof typeof RANGES>("30");
  const range = useMemo(() => {
    const end = new Date();
    const start = new Date(end.getTime() - RANGES[days] * 86400_000);
    return { start: start.toISOString(), end: end.toISOString() };
  }, [days]);
  const { data, error, refetch, isPending } = useAnalytics(range);

  return (
    <>
      <PageHeader title="Analytics" description="Computed from your stored jobs, applications and messages. Hover ⓘ for each definition."
        actions={<Segmented ariaLabel="Time range" value={days} onChange={setDays}
          options={[{ value: "7", label: "7d" }, { value: "30", label: "30d" }, { value: "90", label: "90d" }, { value: "365", label: "1y" }]} />} />
      {error ? <Card><ErrorState error={error} onRetry={() => refetch()} /></Card> : isPending || !data ? (
        <div className="space-y-6"><Skeleton className="h-20 rounded-lg" /><Skeleton className="h-72 rounded-lg" /></div>
      ) : (
        <div className="flex flex-col gap-6">
          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border shadow-sm md:grid-cols-4 xl:grid-cols-7">
            <Kpi label="Jobs discovered" value={data.totals.jobs_discovered.toLocaleString("en-IN")} />
            <Kpi label="Applications" value={String(data.totals.applications)} definition="Applications created in this range." />
            <Kpi label="Submitted" value={String(data.totals.submitted)} definition="Acknowledged submissions only." />
            <Kpi label="Interviews" value={String(data.totals.interviews)} />
            <Kpi label="Response rate" value={percent(data.conversion.response_rate)} definition={data.definitions.response_rate} />
            <Kpi label="App → interview" value={percent(data.conversion.application_to_interview)} definition={data.definitions.application_to_interview} />
            <Kpi label="Interview → offer" value={percent(data.conversion.interview_to_offer)} definition={data.definitions.interview_to_offer} />
          </div>
          <p className="-mt-3 text-2xs text-fg-subtle">— means there isn&apos;t enough data yet (the denominator is zero), not 0%.</p>

          <Card>
            <CardHeader title="Activity over time" description={`Per ${data.range.bucket}`} />
            <div className="px-4 pb-4">
              <TrendChart data={data.series} xKey="period" height={240}
                series={[
                  { key: "discovered", label: "Jobs discovered", color: SERIES_COLORS[0] },
                  { key: "created", label: "Applications", color: SERIES_COLORS[1] },
                  { key: "submitted", label: "Submitted", color: SERIES_COLORS[2] },
                ]}
                tickFormat={(d) => formatDate(d, { day: "numeric", month: "short" })}
                labelFormat={(d) => (data.range.bucket === "week" ? "Week of " : "") + formatDate(d, { day: "numeric", month: "short", year: "numeric" })}
                empty={<EmptyState compact icon={BarChart3} title="No activity in this range" />} />
            </div>
          </Card>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader title="Most requested skills" description="Across jobs discovered in range" />
              <div className="px-2 pb-3">
                {data.top_skills.length ? <BarList items={data.top_skills.map((s) => ({ label: s.skill, value: s.jobs, hint: `${s.jobs} jobs` }))} /> : <p className="px-2 text-[13px] text-fg-muted">No jobs in range.</p>}
              </div>
            </Card>
            <Card>
              <CardHeader title="Most targeted companies" description="By applications created" />
              <div className="px-2 pb-3">
                {data.top_companies.length ? <BarList items={data.top_companies.map((c) => ({ label: c.company, value: c.applications }))} /> : <p className="px-2 text-[13px] text-fg-muted">No applications in range.</p>}
              </div>
            </Card>
            <Card>
              <CardHeader title="Applications by status" />
              <div className="px-2 pb-3">
                {Object.keys(data.status_breakdown).length ? (
                  <BarList items={Object.entries(data.status_breakdown).map(([s, n]) => ({ label: STATUS_META[s as AppStatus]?.label ?? s, value: n }))} />
                ) : <p className="px-2 text-[13px] text-fg-muted">No applications in range.</p>}
              </div>
            </Card>
            <Card>
              <CardHeader title="Median time in stage" description="Days before moving to the next status" />
              <div className="px-2 pb-3">
                {Object.keys(data.stage_durations_days).length ? (
                  <BarList format={(v) => `${v}d`} items={Object.entries(data.stage_durations_days).map(([s, d]) => ({ label: STATUS_META[s as AppStatus]?.label ?? s, value: d }))} />
                ) : <p className="px-2 text-[13px] text-fg-muted">Appears once applications move between stages.</p>}
              </div>
            </Card>
          </div>

          <Card>
            <CardHeader title="Resume quality over time" description="Score of each parsed upload" />
            <div className="px-2 pb-3">
              {data.resume_progress.length ? (
                <BarList max={100} format={(v) => `${v}/100`} items={data.resume_progress.map((r) => ({ label: `${r.name} · ${formatDate(r.date)}`, value: r.score ?? 0 }))} />
              ) : <p className="px-2 text-[13px] text-fg-muted">Upload a resume to start tracking.</p>}
            </div>
          </Card>
        </div>
      )}
    </>
  );
}
