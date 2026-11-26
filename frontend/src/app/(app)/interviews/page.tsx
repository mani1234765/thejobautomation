"use client";

import { CalendarClock, CheckCircle2, Circle, Info, Mic, Plus, Sparkles } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/overlay";
import { Badge, Card, CardHeader, Field, NativeSelect, Progress, Skeleton, Textarea } from "@/components/ui/primitives";
import { Callout, EmptyState, ErrorState, PageHeader } from "@/components/ui/states";
import { api } from "@/lib/api";
import { cn, formatDateTime, relativeTime } from "@/lib/format";
import { keys, useAction, useApplications, useInterviewSession, useInterviewSessions } from "@/lib/queries";
import type { InterviewQuestion, InterviewSession } from "@/lib/types";

const PROVENANCE: Record<InterviewQuestion["provenance"], string> = {
  job_description: "Generated from the job description",
  skill_template: "From a required skill",
  standard: "Common question",
};

function QuestionCard({ session, q }: { session: InterviewSession; q: InterviewQuestion }) {
  const [answer, setAnswer] = useState(q.answer ?? "");
  const submit = useAction(() => api.post(`/interview/sessions/${session.id}/answer`, { question_id: q.id, answer }), {
    invalidate: [keys.interview(session.id), keys.interviews],
  });
  const f = q.feedback;
  return (
    <li className="rounded-lg border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={q.category === "technical" || q.category === "role" ? "info" : q.category === "behavioural" ? "accent" : "neutral"} className="capitalize">{q.category}</Badge>
        <span className="text-2xs text-fg-subtle">{PROVENANCE[q.provenance]}</span>
        {q.answer && <CheckCircle2 className="ml-auto size-4 text-positive" aria-label="Answered" />}
      </div>
      <p className="mt-2 text-[14px] font-medium leading-snug">{q.question}</p>
      <p className="mt-1 text-xs text-fg-muted">{q.rationale}</p>
      <Textarea className="mt-3" rows={4} value={answer} onChange={(e) => setAnswer(e.target.value)}
        placeholder={q.category === "behavioural" ? "Situation → Task → Action → Result. Write how you'd say it." : "Write how you'd answer out loud."} />
      <Button className="mt-2" size="sm" variant={f ? "secondary" : "primary"} disabled={answer.trim().length < 10} loading={submit.isPending}
        onClick={() => submit.mutate(undefined)}>{f ? "Get new feedback" : "Get feedback"}</Button>
      {f && (
        <div className="mt-4 space-y-3 border-t border-border pt-4">
          {f.scores && (
            <div className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-5">
              {Object.entries(f.scores).map(([k, v]) => (
                <div key={k}>
                  <p className="text-2xs capitalize text-fg-muted">{k.replace("_", " ")}</p>
                  <Progress value={v * 20} tone={v >= 4 ? "positive" : v >= 3 ? "accent" : "warning"} className="mt-1" label={k} />
                </div>
              ))}
            </div>
          )}
          {f.star && (
            <div className="flex flex-wrap gap-3 text-xs">
              {Object.entries(f.star).map(([k, v]) => (
                <span key={k} className={cn("flex items-center gap-1 capitalize", v ? "text-positive" : "text-fg-subtle")}>
                  {v ? <CheckCircle2 className="size-3.5" /> : <Circle className="size-3.5" />}{k}
                </span>
              ))}
            </div>
          )}
          {!!f.strengths?.length && <div><p className="text-xs font-medium">Strengths</p><ul className="mt-1 list-disc pl-4 text-[13px] text-fg-muted">{f.strengths.map((s) => <li key={s}>{s}</li>)}</ul></div>}
          {!!f.improvements?.length && <div><p className="text-xs font-medium">Improve</p><ul className="mt-1 list-disc pl-4 text-[13px] text-fg-muted">{f.improvements.map((s) => <li key={s}>{s}</li>)}</ul></div>}
          {f.improved_answer && <Callout tone="accent" icon={Sparkles} title="Stronger version (your facts only)"><p className="whitespace-pre-wrap">{f.improved_answer}</p></Callout>}
          {f.note && <p className="text-xs text-fg-subtle">{f.note}</p>}
        </div>
      )}
    </li>
  );
}

function InterviewsInner() {
  const params = useSearchParams();
  const router = useRouter();
  const { data: sessions, isPending, error, refetch } = useInterviewSessions();
  const { data: apps } = useApplications();
  const upcoming = useQuery({ queryKey: keys.upcoming, queryFn: () => api.get<{ id: string; stage: string; scheduled_at: string | null; company: string | null; job_title: string | null; application_id: string }[]>("/interview/upcoming") });
  const [activeId, setActiveId] = useState<string | null>(null);
  const [newOpen, setNewOpen] = useState(params.get("application") !== null);
  const [appChoice, setAppChoice] = useState(params.get("application") ?? "");
  const active = activeId ?? sessions?.[0]?.id ?? null;
  const { data: session } = useInterviewSession(active);
  const create = useAction(() => api.post<InterviewSession>("/interview/sessions", appChoice ? { application_id: appChoice } : {}), {
    invalidate: [keys.interviews], success: "Practice plan ready", onSuccess: (s) => { setActiveId(s.id); setNewOpen(false); router.replace("/interviews"); },
  });
  const eligibleApps = (apps ?? []).filter((a) => !["discovered", "withdrawn"].includes(a.status));

  return (
    <>
      <PageHeader title="Interview Preparation" description="Practice with questions built from the real job description and your own experience."
        actions={<Button variant="primary" onClick={() => setNewOpen(true)}><Plus /> New practice plan</Button>} />
      {!!upcoming.data?.length && (
        <Card className="mb-5">
          <CardHeader title="Upcoming interviews" />
          <ul className="divide-y divide-border border-t border-border">
            {upcoming.data.filter((i) => !i.scheduled_at || new Date(i.scheduled_at) > new Date()).map((i) => (
              <li key={i.id} className="flex items-center gap-3 px-4 py-2.5">
                <CalendarClock className="size-4 text-accent" />
                <span className="flex-1 text-[13px]">{i.stage} · {i.job_title} at {i.company}</span>
                <span className="text-xs text-fg-muted">{formatDateTime(i.scheduled_at)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {error ? <Card><ErrorState error={error} onRetry={() => refetch()} /></Card> : isPending ? <Skeleton className="h-80 rounded-lg" /> : !sessions.length ? (
        <Card><EmptyState icon={Mic} title="No practice plans yet" description="Create one for an application to get role-specific technical, behavioural and HR questions with feedback."
          action={<Button variant="primary" onClick={() => setNewOpen(true)}>Create a plan</Button>} /></Card>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[240px_minmax(0,1fr)]">
          <ul className="flex flex-col gap-0.5">
            {sessions.map((s) => (
              <li key={s.id}>
                <button onClick={() => setActiveId(s.id)} className={cn("w-full rounded-md px-2 py-2 text-left", s.id === active ? "bg-surface-3" : "hover:bg-surface-3/60")}>
                  <span className="block truncate text-[13px] font-medium">{s.title}</span>
                  <span className="mt-1 flex items-center gap-2"><Progress value={(s.progress.answered / Math.max(1, s.progress.total)) * 100} className="h-1" label="Progress" />
                    <span className="tabular text-2xs text-fg-subtle">{s.progress.answered}/{s.progress.total}</span></span>
                  <span className="block text-2xs text-fg-subtle">{relativeTime(s.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
          {!session ? <Skeleton className="h-80" /> : (
            <div className="flex min-w-0 flex-col gap-4">
              <div>
                <h2 className="text-base font-semibold">{session.title}</h2>
                <p className="text-xs text-fg-muted">{session.generated_by.startsWith("llm") ? `Generated by ${session.generated_by.slice(4)}` : "From JobPilot's question bank (connect AI for job-specific questions)"} · {session.progress.answered} of {session.progress.total} answered</p>
              </div>
              <Callout tone="info" icon={Info}>These are practice questions, not questions this company is known to ask.</Callout>
              {!!session.focus_areas.length && (
                <div className="flex flex-wrap items-center gap-1.5"><span className="text-xs text-fg-muted">Focus areas:</span>{session.focus_areas.map((f) => <Badge key={f} tone="accent">{f}</Badge>)}</div>
              )}
              <ul className="flex flex-col gap-3">{session.questions.map((q) => <QuestionCard key={q.id} session={session} q={q} />)}</ul>
            </div>
          )}
        </div>
      )}
      <Dialog open={newOpen} onOpenChange={setNewOpen}>
        <DialogContent title="New practice plan" description="Pick an application for job-specific questions, or practise generally."
          footer={<Button variant="primary" loading={create.isPending} onClick={() => create.mutate(undefined)}>Create plan</Button>}>
          <Field label="Application" htmlFor="app">
            <NativeSelect id="app" value={appChoice} onChange={(e) => setAppChoice(e.target.value)}>
              <option value="">General practice</option>
              {eligibleApps.map((a) => <option key={a.id} value={a.id}>{a.job.title} — {a.job.company}</option>)}
            </NativeSelect>
          </Field>
        </DialogContent>
      </Dialog>
    </>
  );
}

export default function InterviewsPage() {
  return <Suspense><InterviewsInner /></Suspense>;
}
