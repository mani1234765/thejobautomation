"use client";

import {
  ArrowLeft, ArrowUpRight, Check, ChevronDown, CircleAlert, CircleCheck, ExternalLink, FileText, Loader2, MoreHorizontal,
  PenLine, Plus, ShieldCheck, Sparkles, X,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Switch, TagInput } from "@/components/ui/controls";
import {
  ConfirmDialog, Dialog, DialogContent, Menu, MenuContent, MenuItem, MenuLabel, MenuTrigger, Tooltip,
} from "@/components/ui/overlay";
import { Badge, Card, CardHeader, Field, Input, NativeSelect, Skeleton, Textarea } from "@/components/ui/primitives";
import { ScoreRing } from "@/components/ui/score";
import { Callout, ErrorState } from "@/components/ui/states";
import { api } from "@/lib/api";
import { cn, formatDateTime, formatSalary, relativeTime, STATUS_META, WORK_MODE_LABEL } from "@/lib/format";
import { keys, useAction, useAiStatus, useApplication, useResumes, useVersions } from "@/lib/queries";
import type { Answer, ApplicationDetail, AppStatus } from "@/lib/types";

const STEPS = [
  { key: "prepare", label: "Prepare", statuses: ["discovered", "shortlisted", "preparing"] },
  { key: "approve", label: "Approve", statuses: ["awaiting_approval"] },
  { key: "submit", label: "Submit", statuses: ["approved", "failed"] },
  { key: "track", label: "Track", statuses: ["submitted", "assessment", "interview", "offer", "rejected", "withdrawn"] },
] as const;

const CONF_META: Record<Answer["confidence"], { label: string; tone: "positive" | "info" | "warning"; hint: string }> = {
  verified: { label: "Verified", tone: "positive", hint: "From your verified profile or your own edit" },
  inferred: { label: "AI draft", tone: "info", hint: "Drafted from your profile — check it before approving" },
  unknown: { label: "Needs you", tone: "warning", hint: "JobPilot doesn't know this and won't guess" },
};

function useInvalidate(id: string) {
  return [keys.application(id), keys.applications, keys.approvals, keys.dashboard, keys.notifications];
}

function AnswerRow({ appId, a, editable }: { appId: string; a: Answer; editable: boolean }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(a.answer ?? "");
  const save = useAction(() => api.put(`/applications/${appId}/answers/${a.id}`, { answer: value }), {
    invalidate: useInvalidate(appId), onSuccess: () => setEditing(false),
  });
  const meta = CONF_META[a.confidence];
  return (
    <li className="px-4 py-3">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium text-fg-muted">{a.question}</p>
          {editing ? (
            <div className="mt-1.5 flex flex-col gap-2">
              <Textarea autoFocus rows={a.question.length > 40 ? 4 : 2} value={value} onChange={(e) => setValue(e.target.value)} />
              <div className="flex gap-2">
                <Button size="sm" variant="primary" loading={save.isPending} onClick={() => save.mutate(undefined)}>Save</Button>
                <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setValue(a.answer ?? ""); }}>Cancel</Button>
              </div>
            </div>
          ) : (
            <p className={cn("mt-0.5 text-[13px] whitespace-pre-wrap", !a.answer && "italic text-fg-subtle")}>{a.answer ?? "No answer yet"}</p>
          )}
        </div>
        <Tooltip content={meta.hint}><Badge tone={a.needs_review ? meta.tone : "positive"}>{a.needs_review ? meta.label : a.edited_by_user ? "Confirmed" : meta.label}</Badge></Tooltip>
        {editable && !editing && (
          <Button variant="ghost" size="icon-sm" aria-label={`Edit answer to ${a.question}`} onClick={() => setEditing(true)}><PenLine /></Button>
        )}
      </div>
    </li>
  );
}

function PreparePanel({ app }: { app: ApplicationDetail }) {
  const { data: resumes } = useResumes();
  const { data: versions } = useVersions(app.job.id);
  const { data: ai } = useAiStatus();
  const [resumeChoice, setResumeChoice] = useState<string>(
    app.resume_version_id ? `v:${app.resume_version_id}` : app.resume_id ? `r:${app.resume_id}` : "",
  );
  const [questions, setQuestions] = useState<string[]>([]);
  const [coverLetter, setCoverLetter] = useState(false);
  const [notes, setNotes] = useState<string[]>([]);
  const invalidate = useInvalidate(app.id);
  const prepare = useAction(() => api.post<{ notes: string[] }>(`/applications/${app.id}/prepare`, {
    resume_id: resumeChoice.startsWith("r:") ? resumeChoice.slice(2) : null,
    resume_version_id: resumeChoice.startsWith("v:") ? resumeChoice.slice(2) : null,
    questions, generate_cover_letter: coverLetter,
  }), { invalidate, success: "Application prepared", onSuccess: (r) => { setNotes(r.notes); setQuestions([]); } });
  const request = useAction(() => api.post(`/applications/${app.id}/request-approval`), { invalidate, success: "Sent for your approval" });
  const approved = (versions ?? []).filter((v) => v.status === "approved");
  const drafts = (versions ?? []).filter((v) => v.status === "draft");
  const prepared = app.status === "preparing";

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader title="Materials" description="Choose a resume and add the questions this application asks." />
        <div className="grid gap-4 px-4 pb-4">
          <Field label="Resume" htmlFor="resume" hint={drafts.length ? <>You have a tailored draft for this job — <Link className="text-accent hover:underline" href={`/resume?version=${drafts[0].id}`}>review it</Link> to make it selectable.</> : undefined}>
            <NativeSelect id="resume" value={resumeChoice} onChange={(e) => setResumeChoice(e.target.value)}>
              <option value="">Primary resume</option>
              {approved.map((v) => <option key={v.id} value={`v:${v.id}`}>Tailored · {v.label}</option>)}
              {(resumes ?? []).map((r) => <option key={r.id} value={`r:${r.id}`}>{r.name}{r.is_primary ? " (primary)" : ""}</option>)}
            </NativeSelect>
          </Field>
          <Field label="Application questions" hint="Paste each question from the form and press Enter. Profile basics (contact, experience, notice period…) are added automatically.">
            <TagInput value={questions} onChange={setQuestions} max={25} placeholder="Why do you want to work here?" />
          </Field>
          <label className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2">
            <span>
              <span className="block text-[13px] font-medium">Draft a cover letter</span>
              <span className="block text-xs text-fg-muted">{ai?.configured ? "Uses only facts from your profile." : "Requires an AI provider (Integrations)."}</span>
            </span>
            <Switch checked={coverLetter} onCheckedChange={setCoverLetter} disabled={!ai?.configured} />
          </label>
          <div className="flex flex-wrap gap-2">
            <Button variant={prepared ? "secondary" : "primary"} loading={prepare.isPending} onClick={() => prepare.mutate(undefined)}>
              <Sparkles /> {prepared ? "Update preparation" : "Prepare application"}
            </Button>
          </div>
          {notes.map((n) => <Callout key={n} tone="info">{n}</Callout>)}
        </div>
      </Card>

      {app.answers.length > 0 && (
        <Card>
          <CardHeader title="Answers" description="Answers marked “Needs you” must be completed — JobPilot never invents information."
            action={app.pending_review > 0 ? <Badge tone="warning">{app.pending_review} to review</Badge> : <Badge tone="positive">All reviewed</Badge>} />
          <ul className="divide-y divide-border border-t border-border">
            {app.answers.map((a) => <AnswerRow key={a.id} appId={app.id} a={a} editable />)}
          </ul>
        </Card>
      )}

      {prepared && <CoverLetter key={app.cover_letter ?? ""} app={app} />}

      {prepared && (
        <Card className="p-4">
          {app.readiness.length ? (
            <div className="space-y-2">
              <p className="text-[13px] font-medium">Before requesting approval</p>
              <ul className="space-y-1">
                {app.readiness.map((r) => <li key={r} className="flex gap-2 text-[13px] text-fg-muted"><CircleAlert className="mt-0.5 size-3.5 shrink-0 text-warning" />{r}</li>)}
              </ul>
            </div>
          ) : (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="flex items-center gap-2 text-[13px]"><CircleCheck className="size-4 text-positive" /> Ready for your approval.</p>
              <Button variant="primary" loading={request.isPending} onClick={() => request.mutate(undefined)}><ShieldCheck /> Request approval</Button>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

function CoverLetter({ app }: { app: ApplicationDetail }) {
  const [text, setText] = useState(app.cover_letter ?? "");
  const save = useAction(() => api.put(`/applications/${app.id}/cover-letter`, { cover_letter: text }), {
    invalidate: useInvalidate(app.id), success: "Cover letter saved",
  });
  const dirty = text !== (app.cover_letter ?? "");
  return (
    <Card>
      <CardHeader title="Cover letter" description={app.cover_letter_generated_by?.startsWith("llm") ? "AI draft — edit freely before approving." : "Optional."} />
      <div className="px-4 pb-4">
        <Textarea rows={8} value={text} onChange={(e) => setText(e.target.value)} placeholder="Write or paste a cover letter, or leave empty if not required." />
        {dirty && <Button className="mt-2" size="sm" variant="primary" loading={save.isPending} onClick={() => save.mutate(undefined)}>Save cover letter</Button>}
      </div>
    </Card>
  );
}

function ApprovePanel({ app }: { app: ApplicationDetail }) {
  const pending = app.approvals.find((r) => r.status === "pending");
  const [rejectOpen, setRejectOpen] = useState(false);
  const [note, setNote] = useState("");
  const invalidate = useInvalidate(app.id);
  const decide = useAction((approve: boolean) => api.post(`/approvals/${pending!.id}/decide`, { approve, note: note || null }), {
    invalidate, onSuccess: () => setRejectOpen(false),
  });
  return (
    <div className="flex flex-col gap-5">
      <Card className="border-warning/40">
        <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
          <span className="flex size-9 items-center justify-center rounded-lg bg-warning-soft text-warning"><ShieldCheck className="size-4" /></span>
          <div className="flex-1">
            <p className="text-[13px] font-semibold">Your approval is needed</p>
            <p className="text-xs text-fg-muted">Check the resume and answers below. Approving lets you submit; any later edit voids the approval.</p>
          </div>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => setRejectOpen(true)}><X /> Send back</Button>
            <Button variant="primary" loading={decide.isPending && decide.variables} onClick={() => decide.mutate(true)}><Check /> Approve</Button>
          </div>
        </div>
      </Card>
      <Card>
        <CardHeader title="What you're approving" />
        <ul className="divide-y divide-border border-t border-border">
          {app.answers.map((a) => <AnswerRow key={a.id} appId={app.id} a={a} editable />)}
        </ul>
        {app.cover_letter && (
          <div className="border-t border-border px-4 py-3">
            <p className="text-xs font-medium text-fg-muted">Cover letter</p>
            <p className="mt-1 text-[13px] whitespace-pre-wrap">{app.cover_letter}</p>
          </div>
        )}
      </Card>
      <ConfirmDialog open={rejectOpen} onOpenChange={setRejectOpen} title="Send back to preparation?" confirmLabel="Send back"
        description="The application returns to Preparing so you can change it." loading={decide.isPending} onConfirm={() => decide.mutate(false)}>
        <Textarea className="mt-3" rows={2} placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
      </ConfirmDialog>
    </div>
  );
}

function SubmitPanel({ app }: { app: ApplicationDetail }) {
  const invalidate = useInvalidate(app.id);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [failOpen, setFailOpen] = useState(false);
  const [reference, setReference] = useState("");
  const [reason, setReason] = useState("");
  const act = useAction((v: { action: string; reference?: string; reason?: string }) => api.post(`/applications/${app.id}/submission`, v), {
    invalidate, onSuccess: () => { setConfirmOpen(false); setFailOpen(false); },
  });
  const started = app.submission_state === "attempted" || app.submission_state === "unknown";
  const applyUrl = app.job.apply_url ?? app.job.url;
  return (
    <Card>
      <CardHeader title="Submit on the employer's site"
        description="JobPilot doesn't submit for you: candidate-side submission isn't offered by these job boards. Open the official form, use your approved materials, then confirm here." />
      <div className="flex flex-col gap-4 px-4 pb-4">
        {app.status === "failed" && <Callout tone="danger" icon={CircleAlert} title="Last attempt failed">You can retry. Retrying counts toward your daily limit.</Callout>}
        <ol className="space-y-3">
          <li className="flex items-start gap-3">
            <span className={cn("mt-0.5 flex size-5 items-center justify-center rounded-full text-2xs font-semibold", started ? "bg-positive text-white" : "bg-surface-3")}>{started ? <Check className="size-3" /> : 1}</span>
            <div className="flex-1">
              <p className="text-[13px] font-medium">Open the application form</p>
              <p className="text-xs text-fg-muted">Records a submission attempt. Your answers are on this page to copy from.</p>
              {!started ? (
                <Button className="mt-2" variant="primary" loading={act.isPending && act.variables?.action === "start"}
                  onClick={() => act.mutate({ action: "start" }, { onSuccess: () => window.open(applyUrl, "_blank", "noopener,noreferrer") })}>
                  <ExternalLink /> Open form & start
                </Button>
              ) : (
                <Button asChild className="mt-2" variant="secondary"><a href={applyUrl} target="_blank" rel="noopener noreferrer"><ExternalLink /> Open form again</a></Button>
              )}
            </div>
          </li>
          <li className="flex items-start gap-3">
            <span className="mt-0.5 flex size-5 items-center justify-center rounded-full bg-surface-3 text-2xs font-semibold">2</span>
            <div className="flex-1">
              <p className="text-[13px] font-medium">Record the outcome</p>
              <p className="text-xs text-fg-muted">An employer confirmation email, if Gmail is connected, also confirms it automatically.</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <Button variant="primary" disabled={!started} onClick={() => setConfirmOpen(true)}><Check /> I submitted it</Button>
                <Button variant="secondary" disabled={!started} onClick={() => setFailOpen(true)}>It failed</Button>
                <Button variant="ghost" disabled={!started || app.submission_state === "unknown"} loading={act.isPending && act.variables?.action === "unknown"}
                  onClick={() => act.mutate({ action: "unknown" })}>Not sure</Button>
              </div>
              {app.submission_state === "unknown" && <p className="mt-2 text-xs text-warning">Marked as unknown — confirm once you hear back.</p>}
            </div>
          </li>
        </ol>
        <details className="rounded-md border border-border">
          <summary className="flex cursor-pointer items-center justify-between px-3 py-2 text-[13px] font-medium">Your approved answers <ChevronDown className="size-4 text-fg-subtle" /></summary>
          <ul className="divide-y divide-border border-t border-border">
            {app.answers.map((a) => (
              <li key={a.id} className="flex items-start gap-3 px-3 py-2">
                <div className="flex-1"><p className="text-xs text-fg-muted">{a.question}</p><p className="text-[13px]">{a.answer ?? "—"}</p></div>
                {a.answer && <Button size="sm" variant="ghost" onClick={() => navigator.clipboard.writeText(a.answer!)}>Copy</Button>}
              </li>
            ))}
          </ul>
        </details>
      </div>
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent title="Confirm submission" description="Only confirm once the employer's form has accepted your application."
          footer={<><Button variant="secondary" onClick={() => setConfirmOpen(false)}>Cancel</Button>
            <Button variant="primary" loading={act.isPending} onClick={() => act.mutate({ action: "confirm", reference })}>Confirm submitted</Button></>}>
          <Field label="Confirmation reference" htmlFor="ref" optional hint="e.g. a confirmation number or the email subject."><Input id="ref" value={reference} onChange={(e) => setReference(e.target.value)} /></Field>
        </DialogContent>
      </Dialog>
      <Dialog open={failOpen} onOpenChange={setFailOpen}>
        <DialogContent title="Mark as failed" description="The application stays out of your submitted count."
          footer={<><Button variant="secondary" onClick={() => setFailOpen(false)}>Cancel</Button>
            <Button variant="danger" loading={act.isPending} onClick={() => act.mutate({ action: "fail", reason })}>Mark failed</Button></>}>
          <Field label="What happened?" htmlFor="reason" optional><Textarea id="reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function TrackPanel({ app }: { app: ApplicationDetail }) {
  const invalidate = useInvalidate(app.id);
  const [ivOpen, setIvOpen] = useState(false);
  const [iv, setIv] = useState({ stage: "Recruiter screen", scheduled_at: "", mode: "video", location_or_link: "" });
  const [note, setNote] = useState("");
  const addIv = useAction(() => api.post(`/applications/${app.id}/interviews`, {
    ...iv, scheduled_at: iv.scheduled_at ? new Date(iv.scheduled_at).toISOString() : null,
  }), { invalidate: [...invalidate, keys.upcoming], success: "Interview added", onSuccess: () => setIvOpen(false) });
  const addNote = useAction(() => api.post(`/applications/${app.id}/notes`, { text: note }), { invalidate, onSuccess: () => setNote("") });
  const canInterview = ["submitted", "assessment", "interview"].includes(app.status);
  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader title="Interviews" action={canInterview && <Button size="sm" onClick={() => setIvOpen(true)}><Plus /> Add</Button>} />
        {app.interviews.length === 0 ? (
          <p className="px-4 pb-4 text-[13px] text-fg-muted">No interviews yet. When you get an invitation, add it here to unlock tailored prep.</p>
        ) : (
          <ul className="divide-y divide-border border-t border-border">
            {app.interviews.map((i) => (
              <li key={i.id} className="flex items-center gap-3 px-4 py-2.5">
                <div className="flex-1">
                  <p className="text-[13px] font-medium">{i.stage}</p>
                  <p className="text-xs text-fg-muted">{formatDateTime(i.scheduled_at)}{i.mode ? ` · ${i.mode}` : ""}</p>
                </div>
                <Button asChild size="sm" variant="secondary"><Link href={`/interviews?application=${app.id}`}>Prepare</Link></Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card className="p-4">
        <Field label="Add a note" htmlFor="note">
          <Textarea id="note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Recruiter name, next steps, anything to remember…" />
        </Field>
        <Button className="mt-2" size="sm" disabled={!note.trim()} loading={addNote.isPending} onClick={() => addNote.mutate(undefined)}>Add note</Button>
      </Card>
      <Dialog open={ivOpen} onOpenChange={setIvOpen}>
        <DialogContent title="Add interview" footer={<Button variant="primary" loading={addIv.isPending} onClick={() => addIv.mutate(undefined)}>Add interview</Button>}>
          <div className="grid gap-3">
            <Field label="Stage" htmlFor="stage"><Input id="stage" value={iv.stage} onChange={(e) => setIv({ ...iv, stage: e.target.value })} /></Field>
            <Field label="When" htmlFor="when"><Input id="when" type="datetime-local" value={iv.scheduled_at} onChange={(e) => setIv({ ...iv, scheduled_at: e.target.value })} /></Field>
            <Field label="Format" htmlFor="mode">
              <NativeSelect id="mode" value={iv.mode} onChange={(e) => setIv({ ...iv, mode: e.target.value })}>
                <option value="video">Video</option><option value="phone">Phone</option><option value="onsite">On-site</option>
              </NativeSelect>
            </Field>
            <Field label="Link or location" htmlFor="loc" optional><Input id="loc" value={iv.location_or_link} onChange={(e) => setIv({ ...iv, location_or_link: e.target.value })} /></Field>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function eventLabel(e: ApplicationDetail["events"][number]) {
  if (e.kind === "status_change") return e.detail?.created ? `Created as ${STATUS_META[e.to_status as AppStatus]?.label}` : `${STATUS_META[e.from_status as AppStatus]?.label ?? e.from_status} → ${STATUS_META[e.to_status as AppStatus]?.label ?? e.to_status}`;
  if (e.kind === "submission") return `Submission ${String(e.detail?.action ?? "")}`.replace("start", "started");
  if (e.kind === "note") return String(e.detail?.text ?? "Note");
  if (e.kind === "document") return "Materials prepared";
  if (e.kind === "interview") return `Interview: ${String(e.detail?.stage ?? "")}`;
  return e.kind;
}

export default function ApplicationDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: app, error, refetch, isPending } = useApplication(id);
  const [moveTo, setMoveTo] = useState<AppStatus | null>(null);
  const move = useAction((to: AppStatus) => api.post(`/applications/${id}/transition`, { to }), {
    invalidate: useInvalidate(id), success: "Status updated", onSuccess: () => setMoveTo(null),
  });

  if (error) return <Card><ErrorState error={error} onRetry={() => refetch()} /></Card>;
  if (isPending || !app) return <div className="space-y-4" aria-busy="true"><Skeleton className="h-6 w-1/3" /><Skeleton className="h-12" /><Skeleton className="h-80" /></div>;

  const stepIndex = STEPS.findIndex((s) => (s.statuses as readonly string[]).includes(app.status));
  const meta = STATUS_META[app.status];
  const salary = formatSalary(app.job);
  return (
    <div className="flex flex-col gap-6">
      <div>
        <Link href="/applications" className="inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg"><ArrowLeft className="size-3" /> Applications</Link>
        <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-lg font-semibold tracking-[-0.01em]">{app.job.title}</h1>
            <p className="text-[13px] text-fg-muted">{app.job.company}{app.job.location ? ` · ${app.job.location}` : ""}</p>
          </div>
          <div className="flex items-center gap-2">
            <Badge tone={meta.tone} dot className="h-6 px-2 text-xs">{meta.label}</Badge>
            {app.allowed_next.length > 0 && (
              <Menu>
                <MenuTrigger asChild><Button size="icon" variant="secondary" aria-label="More actions"><MoreHorizontal /></Button></MenuTrigger>
                <MenuContent>
                  <MenuLabel>Move to</MenuLabel>
                  {app.allowed_next.map((s) => (
                    <MenuItem key={s} destructive={s === "withdrawn"} onSelect={() => (s === "withdrawn" || s === "rejected" ? setMoveTo(s) : move.mutate(s))}>
                      {STATUS_META[s].label}
                    </MenuItem>
                  ))}
                </MenuContent>
              </Menu>
            )}
          </div>
        </div>
      </div>

      <ol className="grid grid-cols-4 gap-2" aria-label="Application progress">
        {STEPS.map((s, i) => (
          <li key={s.key} className="flex flex-col gap-1.5" aria-current={i === stepIndex ? "step" : undefined}>
            <div className={cn("h-1 rounded-full", i < stepIndex ? "bg-accent" : i === stepIndex ? "bg-accent/60" : "bg-surface-3")} />
            <span className={cn("text-xs", i === stepIndex ? "font-medium" : "text-fg-subtle")}>{s.label}</span>
          </li>
        ))}
      </ol>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0">
          {["discovered", "shortlisted", "preparing"].includes(app.status) && <PreparePanel key={`${app.resume_id}:${app.resume_version_id}`} app={app} />}
          {app.status === "awaiting_approval" && <ApprovePanel app={app} />}
          {["approved", "failed"].includes(app.status) && <SubmitPanel app={app} />}
          {["submitted", "assessment", "interview", "offer", "rejected", "withdrawn"].includes(app.status) && <TrackPanel app={app} />}
        </div>

        <aside className="flex flex-col gap-5">
          <Card className="p-4">
            <div className="flex items-center gap-3">
              <ScoreRing score={app.job.match_score} size={44} />
              <div className="min-w-0 text-xs text-fg-muted">
                <p>{[app.job.work_mode && WORK_MODE_LABEL[app.job.work_mode], salary].filter(Boolean).join(" · ") || "Details from the listing"}</p>
                <Link href={`/jobs?job=${app.job.id}`} className="font-medium text-accent hover:underline">Match details</Link>
              </div>
            </div>
            <div className="mt-3 flex flex-col gap-1.5 border-t border-border pt-3 text-xs">
              <a href={app.job.url} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1 text-fg-muted hover:text-fg">Original listing <ArrowUpRight className="size-3" /></a>
              {app.resume_version_id ? (
                <a href={`/api/v1/resume-versions/${app.resume_version_id}/download`} className="flex items-center gap-1 text-fg-muted hover:text-fg"><FileText className="size-3" /> Tailored resume</a>
              ) : app.resume_id && (
                <a href={`/api/v1/resumes/${app.resume_id}/download`} className="flex items-center gap-1 text-fg-muted hover:text-fg"><FileText className="size-3" /> Attached resume</a>
              )}
              {app.submitted_at && <p className="text-fg-muted">Submitted {formatDateTime(app.submitted_at)}{app.submission_reference ? ` · ${app.submission_reference}` : ""}</p>}
            </div>
          </Card>
          <Card>
            <CardHeader title="History" />
            <ol className="relative mx-4 mb-4 border-l border-border">
              {app.events.map((e) => (
                <li key={e.id} className="relative pb-3 pl-4 last:pb-0">
                  <span className={cn("absolute top-1.5 -left-[3.5px] size-1.5 rounded-full", e.kind === "submission" ? "bg-accent" : "bg-border-strong")} />
                  <p className="text-[13px] leading-snug">{eventLabel(e)}</p>
                  <p className="text-2xs text-fg-subtle">{relativeTime(e.created_at)} · {e.actor}</p>
                </li>
              ))}
            </ol>
          </Card>
        </aside>
      </div>
      <ConfirmDialog open={!!moveTo} onOpenChange={(o) => !o && setMoveTo(null)} destructive confirmLabel={moveTo ? STATUS_META[moveTo].label : ""}
        title={moveTo === "withdrawn" ? "Withdraw this application?" : "Mark as rejected?"}
        description="This closes the application. You can't reopen it later." loading={move.isPending} onConfirm={() => moveTo && move.mutate(moveTo)} />
      {move.isPending && <Loader2 className="fixed right-6 bottom-6 size-4 animate-spin text-fg-muted" />}
    </div>
  );
}
