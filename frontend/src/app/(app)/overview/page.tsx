"use client";

import {
  ArrowRight, ArrowUpRight, Building2, CalendarClock, CheckCircle2, CircleDot, FileText, Inbox, Plus, Radar, Search,
  ShieldCheck, Sparkles,
} from "lucide-react";
import Link from "next/link";
import { BarList, SERIES_COLORS, TrendChart } from "@/components/charts";
import { PipelineStrip } from "@/components/pipeline";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/overlay";
import { Badge, Card, CardHeader, Skeleton } from "@/components/ui/primitives";
import { ScoreRing } from "@/components/ui/score";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { cn, formatDate, formatDateTime, relativeTime, STATUS_META, WORK_MODE_LABEL } from "@/lib/format";
import { useDashboard, useMe } from "@/lib/queries";
import type { AppStatus, Dashboard } from "@/lib/types";

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

function Metric({ label, value, hint, href, emphasis }: { label: string; value: number; hint: string; href: string; emphasis?: boolean }) {
  return (
    <Link href={href} className="group flex flex-col gap-1 bg-surface px-4 py-3 transition-colors hover:bg-surface-2">
      <Tooltip content={hint}>
        <span className="w-fit text-xs text-fg-muted">{label}</span>
      </Tooltip>
      <span className={cn("tabular text-[22px] font-semibold leading-tight tracking-[-0.02em]", emphasis && value > 0 && "text-warning")}>
        {value.toLocaleString("en-IN")}
      </span>
    </Link>
  );
}

function MetricsGrid({ m }: { m: Dashboard["metrics"] }) {
  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border shadow-sm sm:grid-cols-4">
      <Metric label="Jobs discovered" value={m.jobs_discovered} href="/jobs" hint={`Open postings across your ${m.sources} followed boards.`} />
      <Metric label="Matching jobs" value={m.matching_jobs} href="/jobs" hint={`Score ≥ ${m.match_threshold} and not ineligible under your policies.`} />
      <Metric label="Submitted" value={m.applications_submitted} href="/applications?status=submitted" hint="Only acknowledged submissions are counted." />
      <Metric label="Awaiting approval" value={m.awaiting_approval} href="/applications?view=approvals" hint="Applications waiting for your decision." emphasis />
      <Metric label="Interviews scheduled" value={m.interviews_scheduled} href="/interviews" hint="Upcoming interviews you've added." />
      <Metric label="Recruiter responses" value={m.recruiter_responses} href="/communications" hint="Classified replies from synced email and logged messages." />
      <Metric label="Rejections" value={m.rejections} href="/applications?status=rejected" hint="Applications marked rejected." />
      <Metric label="Pending actions" value={m.pending_actions} href="/applications" hint="Approvals, unanswered questions and due follow-ups." emphasis />
    </div>
  );
}

const EVENT_TEXT = (e: Dashboard["timeline"][number]) => {
  if (e.kind === "status_change" && e.to_status) {
    if (e.detail?.created) return `Added to pipeline as ${STATUS_META[e.to_status as AppStatus]?.label.toLowerCase()}`;
    return `Moved to ${STATUS_META[e.to_status as AppStatus]?.label ?? e.to_status}`;
  }
  if (e.kind === "submission") {
    const a = e.detail?.action;
    return a === "start" ? "Submission started" : a === "acknowledged" ? "Submission acknowledged" : a === "failed" ? "Submission failed" : "Submission updated";
  }
  if (e.kind === "document") return "Application materials prepared";
  if (e.kind === "interview") return "Interview scheduled";
  if (e.kind === "note") return "Note added";
  return e.kind;
};

export default function OverviewPage() {
  const { data: me } = useMe();
  const { data, error, refetch, isPending } = useDashboard();

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs text-fg-muted">{formatDate(new Date().toISOString(), { weekday: "long", day: "numeric", month: "long" })}</p>
          <h1 className="mt-0.5 text-lg font-semibold tracking-[-0.01em]">
            {greeting()}{me?.full_name ? `, ${me.full_name.split(" ")[0]}` : ""}
          </h1>
        </div>
        <div className="flex gap-2">
          <Button asChild variant="secondary"><Link href="/jobs?sources=1"><Plus /> Follow a board</Link></Button>
          <Button asChild variant="primary"><Link href="/jobs"><Search /> Discover jobs</Link></Button>
        </div>
      </header>

      {error ? (
        <Card><ErrorState error={error} onRetry={() => refetch()} /></Card>
      ) : isPending || !data ? (
        <OverviewSkeleton />
      ) : (
        <>
          {data.insights.next_actions.length > 0 && (
            <section aria-label="Recommended next actions" className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
              {data.insights.next_actions.slice(0, 3).map((a, i) => (
                <Link key={a.title} href={a.href}
                  className="group flex items-start gap-3 rounded-lg border border-border bg-surface p-3 shadow-sm transition-colors hover:border-border-strong">
                  <span className={cn("mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md",
                    i === 0 ? "bg-accent text-accent-fg" : "bg-surface-3 text-fg-muted")}>
                    <Sparkles className="size-3.5" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-[13px] font-medium">{a.title}</span>
                    <span className="block truncate text-xs text-fg-muted">{a.detail}</span>
                  </span>
                  <ArrowRight className="mt-1 size-3.5 text-fg-subtle transition-transform group-hover:translate-x-0.5" />
                </Link>
              ))}
            </section>
          )}

          <MetricsGrid m={data.metrics} />

          <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
            <div className="flex min-w-0 flex-col gap-6">
              <Card>
                <CardHeader title="Application pipeline" description="Where every application stands right now."
                  action={<Button asChild variant="ghost" size="sm"><Link href="/applications">Open board <ArrowUpRight /></Link></Button>} />
                <div className="px-4 pb-4"><PipelineStrip counts={data.pipeline} /></div>
              </Card>

              <Card>
                <CardHeader title="Activity" description="Last 30 days" />
                <div className="px-4 pb-4">
                  <TrendChart
                    data={data.activity}
                    xKey="date"
                    series={[
                      { key: "discovered", label: "Jobs discovered", color: SERIES_COLORS[0] },
                      { key: "applications", label: "Applications started", color: SERIES_COLORS[1] },
                      { key: "submitted", label: "Submitted", color: SERIES_COLORS[2] },
                    ]}
                    tickFormat={(d) => formatDate(d, { day: "numeric", month: "short" })}
                    labelFormat={(d) => formatDate(d, { weekday: "short", day: "numeric", month: "short" })}
                    empty={<EmptyState compact icon={Radar} title="No activity yet" description="Follow a company job board to start discovering roles." />}
                  />
                </div>
              </Card>

              <Card>
                <CardHeader title="Best matches" description={`Open roles scoring ${data.metrics.match_threshold}+ for your profile`}
                  action={<Button asChild variant="ghost" size="sm"><Link href="/jobs">All jobs <ArrowUpRight /></Link></Button>} />
                {data.recommended.length === 0 ? (
                  <EmptyState compact icon={Search} title="No strong matches yet"
                    description={data.metrics.sources === 0 ? "You're not following any job boards yet." : "Try widening your preferences or following more companies."}
                    action={<Button asChild size="sm" variant="primary"><Link href="/jobs?sources=1"><Plus /> Follow a board</Link></Button>} />
                ) : (
                  <ul className="divide-y divide-border border-t border-border">
                    {data.recommended.map((j) => (
                      <li key={j.job_id}>
                        <Link href={`/jobs?job=${j.job_id}`} className="flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-surface-2">
                          <ScoreRing score={j.score} size={32} />
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-[13px] font-medium">{j.title}</p>
                            <p className="truncate text-xs text-fg-muted">
                              {j.company}{j.location ? ` · ${j.location}` : ""}{j.work_mode ? ` · ${WORK_MODE_LABEL[j.work_mode]}` : ""}
                            </p>
                          </div>
                          <div className="hidden gap-1 md:flex">
                            {j.matched_skills.slice(0, 3).map((s) => <Badge key={s}>{s}</Badge>)}
                          </div>
                          {j.applied ? <Badge tone="accent">In pipeline</Badge> : j.eligibility === "review" ? <Badge tone="warning">Review pay</Badge> : null}
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>

            <div className="flex min-w-0 flex-col gap-6">
              <UpNext data={data} />

              <Card>
                <CardHeader title="Skills in demand"
                  description={data.insights.skill_demand.jobs_analysed
                    ? `Across ${data.insights.skill_demand.jobs_analysed.toLocaleString("en-IN")} discovered jobs`
                    : "Appears once jobs are discovered"} />
                <div className="px-2 pb-3">
                  {data.insights.skill_demand.top_skills.length ? (
                    <>
                      <BarList
                        items={data.insights.skill_demand.top_skills.slice(0, 8).map((s) => ({
                          label: s.skill, value: s.jobs, muted: !s.have,
                          hint: `${Math.round(s.share * 100)}% of jobs · ${s.have ? "in your profile" : "not in your profile"}`,
                        }))}
                        highlight={(l) => data.insights.skill_demand.top_skills.find((s) => s.skill === l)?.have ?? false}
                      />
                      <p className="mt-2 flex items-center gap-1.5 px-2 text-2xs text-fg-subtle">
                        <span className="h-2.5 w-[3px] rounded-full bg-accent" /> In your profile
                      </p>
                    </>
                  ) : (
                    <p className="px-2 pb-2 text-[13px] text-fg-muted">No data yet.</p>
                  )}
                </div>
              </Card>

              {data.insights.companies_hiring.length > 0 && (
                <Card>
                  <CardHeader title="Companies hiring for you" description="Matching open roles" />
                  <div className="px-2 pb-3">
                    <BarList items={data.insights.companies_hiring.map((c) => ({ label: c.company, value: c.matching_jobs }))} />
                  </div>
                </Card>
              )}

              <ResumeHealth data={data} />
              <Timeline data={data} />
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function UpNext({ data }: { data: Dashboard }) {
  const u = data.upcoming;
  const empty = !u.approvals.length && !u.interviews.length && !u.follow_ups.length;
  return (
    <Card>
      <CardHeader title="Up next" />
      {empty ? (
        <p className="px-4 pb-4 text-[13px] text-fg-muted">Nothing needs you right now.</p>
      ) : (
        <ul className="flex flex-col gap-0.5 px-2 pb-2">
          {u.approvals.map((a) => (
            <li key={a.id}>
              <Link href={`/applications/${a.application_id}`} className="flex items-center gap-2.5 rounded-md px-2 py-2 hover:bg-surface-2">
                <ShieldCheck className="size-4 shrink-0 text-warning" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px]">Approve {a.title}</span>
                  <span className="block truncate text-xs text-fg-muted">{a.company} · requested {relativeTime(a.created_at)}</span>
                </span>
              </Link>
            </li>
          ))}
          {u.interviews.map((i) => (
            <li key={i.id}>
              <Link href={`/applications/${i.application_id}`} className="flex items-center gap-2.5 rounded-md px-2 py-2 hover:bg-surface-2">
                <CalendarClock className="size-4 shrink-0 text-accent" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px]">{i.stage} · {i.company}</span>
                  <span className="block truncate text-xs text-fg-muted">{formatDateTime(i.scheduled_at)}</span>
                </span>
              </Link>
            </li>
          ))}
          {u.follow_ups.map((f) => (
            <li key={f.application_id}>
              <Link href={`/communications?compose=follow_up&application=${f.application_id}`} className="flex items-center gap-2.5 rounded-md px-2 py-2 hover:bg-surface-2">
                <Inbox className="size-4 shrink-0 text-info" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px]">Follow up with {f.company}</span>
                  <span className="block truncate text-xs text-fg-muted">{f.title} · due {relativeTime(f.due)}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function ResumeHealth({ data }: { data: Dashboard }) {
  const r = data.insights.resume;
  return (
    <Card>
      <CardHeader title="Resume health" action={r && <Badge tone={(r.score ?? 0) >= 80 ? "positive" : (r.score ?? 0) >= 60 ? "accent" : "warning"}>{r.score}/100</Badge>} />
      {!r ? (
        <EmptyState compact icon={FileText} title="No resume yet" action={<Button asChild size="sm" variant="primary"><Link href="/resume?upload=1">Upload resume</Link></Button>} />
      ) : r.top_issues.length === 0 ? (
        <p className="flex items-center gap-2 px-4 pb-4 text-[13px] text-fg-muted"><CheckCircle2 className="size-4 text-positive" /> No issues found in {r.name}.</p>
      ) : (
        <ul className="flex flex-col gap-2 px-4 pb-4">
          {r.top_issues.map((i) => (
            <li key={i.message} className="flex gap-2 text-[13px]">
              <CircleDot className={cn("mt-0.5 size-3.5 shrink-0", i.severity === "high" ? "text-danger" : i.severity === "medium" ? "text-warning" : "text-fg-subtle")} />
              <span className="text-fg-muted">{i.message}</span>
            </li>
          ))}
          <li><Link href="/resume" className="text-xs font-medium text-accent hover:underline">Open Resume Studio →</Link></li>
        </ul>
      )}
    </Card>
  );
}

function Timeline({ data }: { data: Dashboard }) {
  return (
    <Card>
      <CardHeader title="Recent activity" />
      {data.timeline.length === 0 ? (
        <p className="px-4 pb-4 text-[13px] text-fg-muted">Application events will appear here.</p>
      ) : (
        <ol className="relative mx-4 mb-4 border-l border-border">
          {data.timeline.slice(0, 8).map((e) => (
            <li key={e.id} className="relative pb-3 pl-4 last:pb-0">
              <span className="absolute top-1.5 -left-[3.5px] size-1.5 rounded-full bg-border-strong" />
              <Link href={`/applications/${e.application_id}`} className="block hover:underline">
                <span className="text-[13px]">{EVENT_TEXT(e)}</span>
              </Link>
              <span className="flex items-center gap-1 text-xs text-fg-muted">
                <Building2 className="size-3" /> {e.company} · {relativeTime(e.at)}
                {e.actor !== "user" && <Badge className="ml-1">{e.actor}</Badge>}
              </span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function OverviewSkeleton() {
  return (
    <div className="flex flex-col gap-6" aria-busy="true">
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="space-y-2 bg-surface px-4 py-3"><Skeleton className="h-3 w-20" /><Skeleton className="h-6 w-12" /></div>
        ))}
      </div>
      <div className="grid gap-6 xl:grid-cols-[1fr_340px]">
        <div className="space-y-6"><Skeleton className="h-32 rounded-lg" /><Skeleton className="h-64 rounded-lg" /></div>
        <div className="space-y-6"><Skeleton className="h-40 rounded-lg" /><Skeleton className="h-64 rounded-lg" /></div>
      </div>
    </div>
  );
}
