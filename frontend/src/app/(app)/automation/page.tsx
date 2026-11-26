"use client";

import { ChevronRight, Pause, Play, Plus, Radar, Sparkles, Square, Trash2, Workflow, Zap } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ChipGroup, TagInput } from "@/components/ui/controls";
import { ConfirmDialog, Dialog, DialogContent, Tooltip } from "@/components/ui/overlay";
import { Badge, Card, CardHeader, Field, Input, NativeSelect } from "@/components/ui/primitives";
import { Callout, EmptyState, ErrorState, ListSkeleton, PageHeader } from "@/components/ui/states";
import { api } from "@/lib/api";
import { cn, formatDateTime, relativeTime } from "@/lib/format";
import { keys, useAction, useAutomations, useExecutions, useSettings } from "@/lib/queries";
import type { AutomationConfig, AutomationExecution } from "@/lib/types";

const INTERVALS = [
  { value: 60, label: "Every hour" }, { value: 180, label: "Every 3 hours" }, { value: 360, label: "Every 6 hours" },
  { value: 720, label: "Twice a day" }, { value: 1440, label: "Daily" },
];
const intervalLabel = (m: number) => INTERVALS.find((i) => i.value === m)?.label ?? `Every ${m} min`;

type Draft = { id?: string; name: string; kind: "discovery" | "preparation"; interval_minutes: number; filters: Record<string, unknown> };

function ConfigDialog({ draft, onClose }: { draft: Draft; onClose: () => void }) {
  const [d, setD] = useState<Draft>(draft);
  const f = d.filters as Record<string, never>;
  const setF = (k: string, v: unknown) => setD({ ...d, filters: { ...d.filters, [k]: v } });
  const save = useAction(() => (d.id ? api.put(`/automations/${d.id}`, d) : api.post("/automations", d)), {
    invalidate: [keys.automations], success: d.id ? "Automation updated" : "Automation created", onSuccess: onClose,
  });
  return (
    <DialogContent wide title={d.id ? "Edit automation" : "New automation"}
      description={d.kind === "discovery" ? "Syncs your boards, scores new roles and shortlists the strongest." : "Prepares shortlisted applications and asks for your approval. Never submits."}
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} onClick={() => save.mutate(undefined)}>Save</Button></>}>
      <div className="grid gap-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name" htmlFor="name"><Input id="name" value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} /></Field>
          <Field label="Schedule" htmlFor="int"><NativeSelect id="int" value={d.interval_minutes} onChange={(e) => setD({ ...d, interval_minutes: Number(e.target.value) })}>
            {INTERVALS.map((i) => <option key={i.value} value={i.value}>{i.label}</option>)}
          </NativeSelect></Field>
        </div>
        {d.kind === "discovery" ? (
          <>
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="Notify at score ≥" htmlFor="n"><Input id="n" type="number" min={0} max={100} value={f.notify_min_score ?? 75} onChange={(e) => setF("notify_min_score", Number(e.target.value))} /></Field>
              <Field label="Shortlist at score ≥" htmlFor="s"><Input id="s" type="number" min={0} max={100} value={f.min_score ?? 70} onChange={(e) => setF("min_score", Number(e.target.value))} /></Field>
              <Field label="Max shortlisted per run" htmlFor="m"><Input id="m" type="number" min={0} max={25} value={f.max_shortlist ?? 5} onChange={(e) => setF("max_shortlist", Number(e.target.value))} /></Field>
            </div>
            <Field label="Only titles containing" hint="Leave empty to use every role from your boards."><TagInput value={(f.title_keywords as string[]) ?? []} onChange={(v) => setF("title_keywords", v)} placeholder="backend, platform…" /></Field>
            <Field label="Locations" hint="Add “Remote” to include remote roles anywhere."><TagInput value={(f.locations as string[]) ?? []} onChange={(v) => setF("locations", v)} placeholder="Bengaluru, Remote…" /></Field>
            <Field label="Work modes"><ChipGroup ariaLabel="Work modes" value={(f.work_modes as string[]) ?? []} onChange={(v) => setF("work_modes", v)} options={[{ value: "remote", label: "Remote" }, { value: "hybrid", label: "Hybrid" }, { value: "onsite", label: "On-site" }]} /></Field>
            <Field label="Employment types"><ChipGroup ariaLabel="Employment types" value={(f.employment_types as string[]) ?? []} onChange={(v) => setF("employment_types", v)} options={[{ value: "full_time", label: "Full-time" }, { value: "internship", label: "Internship" }, { value: "contract", label: "Contract" }]} /></Field>
          </>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Prepare when score ≥" htmlFor="ps"><Input id="ps" type="number" min={0} max={100} value={f.min_score ?? 70} onChange={(e) => setF("min_score", Number(e.target.value))} /></Field>
            <Field label="Max prepared per run" htmlFor="pm" hint="Also capped by your daily application limit."><Input id="pm" type="number" min={0} max={25} value={f.max_per_run ?? 3} onChange={(e) => setF("max_per_run", Number(e.target.value))} /></Field>
          </div>
        )}
      </div>
    </DialogContent>
  );
}

function ExecutionRow({ ex, name }: { ex: AutomationExecution; name?: string }) {
  const [open, setOpen] = useState(false);
  const tone = ex.status === "succeeded" ? "positive" : ex.status === "failed" ? "danger" : ex.status === "partial" ? "warning" : "info";
  const stats = Object.entries(ex.stats ?? {}).filter(([, v]) => typeof v === "number");
  return (
    <li>
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-surface-2">
        <ChevronRight className={cn("size-3.5 text-fg-subtle transition-transform", open && "rotate-90")} />
        <Badge tone={tone} dot>{ex.status}</Badge>
        <span className="min-w-0 flex-1 truncate text-[13px]">{name ?? ex.kind} <span className="text-fg-muted">· {ex.trigger}</span></span>
        <span className="hidden gap-3 text-xs text-fg-muted md:flex">{stats.slice(0, 3).map(([k, v]) => <span key={k}>{k.replace(/_/g, " ")} <span className="tabular text-fg">{v}</span></span>)}</span>
        <span className="w-24 text-right text-xs text-fg-subtle">{relativeTime(ex.started_at)}</span>
      </button>
      {open && (
        <div className="border-t border-border bg-surface-2/60 px-4 py-3">
          {ex.error && <Callout tone="danger" className="mb-2">{ex.error}</Callout>}
          <ol className="space-y-1 font-mono text-[11.5px]">
            {ex.log.map((l, i) => {
              const { ts, level, event, ...rest } = l;
              return (
                <li key={i} className="flex gap-3">
                  <span className="shrink-0 text-fg-subtle">{new Date(ts).toLocaleTimeString()}</span>
                  <span className={cn("w-10 shrink-0 uppercase", level === "error" ? "text-danger" : level === "warn" ? "text-warning" : "text-fg-subtle")}>{level}</span>
                  <span className="text-fg">{event}</span>
                  <span className="truncate text-fg-muted">{Object.entries(rest).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(" ")}</span>
                </li>
              );
            })}
          </ol>
        </div>
      )}
    </li>
  );
}

export default function AutomationPage() {
  const { data: configs, isPending, error, refetch } = useAutomations();
  const { data: executions } = useExecutions();
  const { data: settings } = useSettings();
  const [editing, setEditing] = useState<Draft | null>(null);
  const [deleting, setDeleting] = useState<AutomationConfig | null>(null);
  const control = useAction((v: { id: string; action: string }) => api.post(`/automations/${v.id}/${v.action}`), {
    invalidate: [keys.automations, keys.executions],
  });
  const remove = useAction((id: string) => api.del(`/automations/${id}`), { invalidate: [keys.automations], onSuccess: () => setDeleting(null) });
  const names = Object.fromEntries((configs ?? []).map((c) => [c.id, c.name]));

  return (
    <>
      <PageHeader title="Automation Centre" description="Scheduled discovery and preparation. Automations never submit applications or send messages."
        actions={<>
          <Button onClick={() => setEditing({ name: "Prepare top matches", kind: "preparation", interval_minutes: 1440, filters: { min_score: 70, max_per_run: 3 } })}><Sparkles /> Preparation</Button>
          <Button variant="primary" onClick={() => setEditing({ name: "Daily discovery", kind: "discovery", interval_minutes: 720, filters: { min_score: 70, notify_min_score: 75, max_shortlist: 5 } })}><Plus /> Discovery</Button>
        </>} />
      <div className="mb-5 grid gap-3 sm:grid-cols-3">
        <Card className="p-3"><p className="text-xs text-fg-muted">Daily application limit</p><p className="tabular mt-1 text-lg font-semibold">{settings?.daily_application_limit ?? "—"}</p><p className="text-2xs text-fg-subtle">Change in Settings → Policies</p></Card>
        <Card className="p-3"><p className="text-xs text-fg-muted">Approval</p><p className="mt-1 text-lg font-semibold">Always required</p><p className="text-2xs text-fg-subtle">Enforced by the server</p></Card>
        <Card className="p-3"><p className="text-xs text-fg-muted">Worker</p><p className="mt-1 text-[13px] font-medium">Runs schedules in the background</p><p className="text-2xs text-fg-subtle">“Run now” queues a run immediately</p></Card>
      </div>
      <Card>
        <CardHeader title="Automations" />
        {error ? <ErrorState error={error} onRetry={() => refetch()} /> : isPending ? <ListSkeleton rows={2} /> : !configs.length ? (
          <EmptyState icon={Workflow} title="No automations yet" description="Start with daily discovery: it checks your boards and shortlists strong new matches." />
        ) : (
          <ul className="divide-y divide-border border-t border-border">
            {configs.map((c) => (
              <li key={c.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <span className={cn("flex size-8 items-center justify-center rounded-md", c.kind === "discovery" ? "bg-info-soft text-info" : "bg-accent-soft text-accent-soft-fg")}>
                  {c.kind === "discovery" ? <Radar className="size-4" /> : <Sparkles className="size-4" />}
                </span>
                <button className="min-w-0 flex-1 text-left" onClick={() => setEditing({ id: c.id, name: c.name, kind: c.kind, interval_minutes: c.interval_minutes, filters: c.filters })}>
                  <span className="flex items-center gap-2 text-[13px] font-medium">{c.name}
                    <Badge tone={c.state === "active" ? "positive" : c.state === "paused" ? "warning" : "neutral"} dot>{c.state}</Badge></span>
                  <span className="block text-xs text-fg-muted">
                    {intervalLabel(c.interval_minutes)} · {c.state === "active" && c.next_run_at ? `next ${relativeTime(c.next_run_at)}` : "not scheduled"} · last run {c.last_run_at ? relativeTime(c.last_run_at) : "never"}
                  </span>
                </button>
                <div className="flex gap-1">
                  <Tooltip content="Run now"><Button size="icon-sm" variant="ghost" aria-label="Run now" disabled={c.state === "stopped"} onClick={() => control.mutate({ id: c.id, action: "run" })}><Zap /></Button></Tooltip>
                  {c.state === "active" ? (
                    <Tooltip content="Pause"><Button size="icon-sm" variant="ghost" aria-label="Pause" onClick={() => control.mutate({ id: c.id, action: "pause" })}><Pause /></Button></Tooltip>
                  ) : (
                    <Tooltip content="Resume"><Button size="icon-sm" variant="ghost" aria-label="Resume" onClick={() => control.mutate({ id: c.id, action: "resume" })}><Play /></Button></Tooltip>
                  )}
                  {c.state !== "stopped" && <Tooltip content="Stop"><Button size="icon-sm" variant="ghost" aria-label="Stop" onClick={() => control.mutate({ id: c.id, action: "stop" })}><Square /></Button></Tooltip>}
                  <Tooltip content="Delete"><Button size="icon-sm" variant="ghost" aria-label="Delete" onClick={() => setDeleting(c)}><Trash2 /></Button></Tooltip>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card className="mt-6">
        <CardHeader title="Execution history" description="Every run with its structured log. Refreshes automatically." />
        {!executions?.length ? <p className="px-4 pb-4 text-[13px] text-fg-muted">No runs yet.</p> : (
          <ul className="divide-y divide-border border-t border-border">{executions.map((e) => <ExecutionRow key={e.id} ex={e} name={e.config_id ? names[e.config_id] : undefined} />)}</ul>
        )}
      </Card>
      <Dialog open={!!editing} onOpenChange={(o) => !o && setEditing(null)}>{editing && <ConfigDialog draft={editing} onClose={() => setEditing(null)} />}</Dialog>
      <ConfirmDialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)} destructive confirmLabel="Delete" title={`Delete “${deleting?.name}”?`}
        description="Its run history is kept." loading={remove.isPending} onConfirm={() => deleting && remove.mutate(deleting.id)} />
      <p className="mt-4 text-xs text-fg-subtle">Scheduled at {formatDateTime(new Date().toISOString())} local time. Runs are idempotent: a restarted worker never repeats a scheduled slot.</p>
    </>
  );
}
