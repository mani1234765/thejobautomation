"use client";

import { Briefcase, Columns3, List, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo } from "react";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/controls";
import { Tooltip } from "@/components/ui/overlay";
import { Badge, Card } from "@/components/ui/primitives";
import { ScoreRing } from "@/components/ui/score";
import { EmptyState, ErrorState, ListSkeleton, PageHeader } from "@/components/ui/states";
import { cn, PIPELINE, relativeTime, STATUS_META } from "@/lib/format";
import { useApplications, useApprovals } from "@/lib/queries";
import type { Application, AppStatus } from "@/lib/types";

const CLOSED: AppStatus[] = ["rejected", "withdrawn", "failed"];

function AppCard({ a }: { a: Application }) {
  return (
    <Link href={`/applications/${a.id}`}
      className="block rounded-md border border-border bg-surface p-2.5 shadow-sm transition-[border,box-shadow] hover:border-border-strong hover:shadow-md">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] font-medium">{a.job.title}</p>
          <p className="truncate text-xs text-fg-muted">{a.job.company}</p>
        </div>
        <ScoreRing score={a.job.match_score} size={28} />
      </div>
      <div className="mt-2 flex items-center gap-1.5 text-2xs text-fg-subtle">
        <span>{relativeTime(a.status_changed_at)}</span>
        {a.pending_review > 0 && <Badge tone="warning">{a.pending_review} to review</Badge>}
        {a.submission_state === "attempted" && <Badge tone="info">Confirm submission</Badge>}
        {a.created_by === "automation" && <Tooltip content="Added by an automation"><Badge>auto</Badge></Tooltip>}
      </div>
    </Link>
  );
}

function Board({ apps }: { apps: Application[] }) {
  const by = useMemo(() => {
    const m = new Map<AppStatus, Application[]>();
    for (const a of apps) m.set(a.status, [...(m.get(a.status) ?? []), a]);
    return m;
  }, [apps]);
  const closed = CLOSED.flatMap((s) => by.get(s) ?? []);
  return (
    <div className="-mx-4 overflow-x-auto px-4 pb-2 sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
      <div className="flex min-w-max gap-3">
        {[...PIPELINE, "closed" as const].map((s) => {
          const items = s === "closed" ? closed : by.get(s) ?? [];
          const label = s === "closed" ? "Closed" : STATUS_META[s].label;
          return (
            <section key={s} className="flex w-60 shrink-0 flex-col rounded-lg bg-surface-2/70 p-2" aria-label={label}>
              <header className="flex items-center justify-between px-1 pb-2">
                <h2 className="flex items-center gap-1.5 text-xs font-medium">
                  <span className={cn("size-2 rounded-full", s === "awaiting_approval" ? "bg-warning" : s === "offer" ? "bg-positive" : s === "closed" ? "bg-fg-subtle" : "bg-accent/60")} />
                  {label}
                </h2>
                <span className="tabular text-xs text-fg-subtle">{items.length}</span>
              </header>
              <div className="flex flex-col gap-2">
                {items.map((a) => <AppCard key={a.id} a={a} />)}
                {items.length === 0 && <p className="rounded-md border border-dashed border-border px-2 py-4 text-center text-2xs text-fg-subtle">Empty</p>}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}

function Table({ apps }: { apps: Application[] }) {
  return (
    <Card className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead className="border-b border-border text-xs text-fg-muted">
          <tr>
            <th className="px-4 py-2.5 text-left font-medium">Role</th>
            <th className="px-3 py-2.5 text-left font-medium">Status</th>
            <th className="hidden px-3 py-2.5 text-left font-medium md:table-cell">Submission</th>
            <th className="px-3 py-2.5 text-right font-medium">Match</th>
            <th className="hidden px-4 py-2.5 text-right font-medium sm:table-cell">Updated</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {apps.map((a) => (
            <tr key={a.id} className="group relative hover:bg-surface-2">
              <td className="px-4 py-2.5">
                <Link href={`/applications/${a.id}`} className="after:absolute after:inset-0">
                  <span className="block font-medium">{a.job.title}</span>
                  <span className="block text-xs text-fg-muted">{a.job.company}{a.job.location ? ` · ${a.job.location}` : ""}</span>
                </Link>
              </td>
              <td className="px-3"><Badge tone={STATUS_META[a.status].tone} dot>{STATUS_META[a.status].label}</Badge></td>
              <td className="hidden px-3 text-xs text-fg-muted capitalize md:table-cell">{a.submission_state.replace("_", " ")}</td>
              <td className="tabular px-3 text-right">{a.job.match_score ?? "—"}</td>
              <td className="hidden px-4 text-right text-xs text-fg-muted sm:table-cell">{relativeTime(a.status_changed_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function Approvals() {
  const { data, isPending, error, refetch } = useApprovals();
  if (error) return <Card><ErrorState error={error} onRetry={() => refetch()} /></Card>;
  if (isPending) return <Card><ListSkeleton rows={3} /></Card>;
  if (!data.length) return <Card><EmptyState icon={ShieldCheck} title="No approvals waiting" description="When an application is ready, it lands here. Nothing is submitted until you approve it." /></Card>;
  return (
    <Card>
      <ul className="divide-y divide-border">
        {data.map((r) => (
          <li key={r.id} className="flex items-center gap-3 px-4 py-3">
            <ShieldCheck className="size-4 text-warning" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13px] font-medium">{r.job_title}</p>
              <p className="text-xs text-fg-muted">{r.company} · requested {relativeTime(r.created_at)} by {r.requested_by}</p>
            </div>
            <Button asChild variant="primary" size="sm"><Link href={`/applications/${r.application_id}`}>Review</Link></Button>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function ApplicationsInner() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const view = (params.get("view") as "board" | "list" | "approvals") ?? "board";
  const status = params.get("status");
  const { data, error, isPending, refetch } = useApplications();
  const { data: approvals } = useApprovals();
  const setView = (v: string) => router.replace(`${pathname}?view=${v}`, { scroll: false });
  const apps = (data ?? []).filter((a) => !status || a.status === status);

  return (
    <>
      <PageHeader title="My Applications"
        description="Every application, from shortlist to offer. Submissions only count once acknowledged."
        actions={
          <Segmented ariaLabel="View" value={view} onChange={setView} options={[
            { value: "board", label: <span className="flex items-center gap-1.5"><Columns3 className="size-3.5" />Board</span> },
            { value: "list", label: <span className="flex items-center gap-1.5"><List className="size-3.5" />List</span> },
            { value: "approvals", label: <span className="flex items-center gap-1.5">Approvals{approvals?.length ? <Badge tone="warning">{approvals.length}</Badge> : null}</span> },
          ]} />
        }>
        {status && (
          <div className="flex items-center gap-2 text-[13px]">
            Showing <Badge tone={STATUS_META[status as AppStatus]?.tone}>{STATUS_META[status as AppStatus]?.label ?? status}</Badge>
            <Button variant="link" size="sm" onClick={() => router.replace(`${pathname}?view=list`)}>Show all</Button>
          </div>
        )}
      </PageHeader>
      {view === "approvals" ? <Approvals /> : error ? <Card><ErrorState error={error} onRetry={() => refetch()} /></Card>
        : isPending ? <Card><ListSkeleton /></Card>
        : !data.length ? (
          <Card><EmptyState icon={Briefcase} title="No applications yet"
            description="Open a job in Discover and choose Prepare application. Automations can also shortlist strong matches for you."
            action={<Button asChild variant="primary"><Link href="/jobs">Discover jobs</Link></Button>} /></Card>
        ) : view === "list" || status ? <Table apps={apps} /> : <Board apps={apps} />}
    </>
  );
}

export default function ApplicationsPage() {
  return <Suspense><ApplicationsInner /></Suspense>;
}
