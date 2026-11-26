"use client";

import { AlertTriangle, Check, Download, FileCheck2, PenLine, RotateCcw, ShieldCheck, Sparkles, X } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/controls";
import { Badge, Card, CardHeader, Skeleton, Textarea } from "@/components/ui/primitives";
import { Callout, ErrorState } from "@/components/ui/states";
import { api } from "@/lib/api";
import { wordDiff } from "@/lib/diff";
import { cn, formatDate } from "@/lib/format";
import { keys, useAction, useVersion } from "@/lib/queries";
import type { ResumeChange, ResumeVersion } from "@/lib/types";

function Diff({ original, proposed }: { original: string; proposed: string }) {
  const parts = wordDiff(original, proposed);
  return (
    <div className="grid gap-2 md:grid-cols-2">
      <div className="rounded-md border border-border bg-surface-2/60 p-2.5">
        <p className="mb-1 text-2xs font-medium uppercase tracking-wide text-fg-subtle">Original</p>
        <p className="text-[13px] leading-relaxed">
          {parts.filter((p) => p.type !== "added").map((p, i) => (
            <span key={i} className={cn(p.type === "removed" && "rounded-sm bg-danger-soft text-danger line-through decoration-danger/50")}>{p.text}</span>
          ))}
        </p>
      </div>
      <div className="rounded-md border border-accent/30 bg-accent-soft/30 p-2.5">
        <p className="mb-1 text-2xs font-medium uppercase tracking-wide text-accent-soft-fg">Suggested</p>
        <p className="text-[13px] leading-relaxed">
          {parts.filter((p) => p.type !== "removed").map((p, i) => (
            <span key={i} className={cn(p.type === "added" && "rounded-sm bg-positive-soft text-positive")}>{p.text}</span>
          ))}
        </p>
      </div>
    </div>
  );
}

function ChangeCard({ v, c, locked }: { v: ResumeVersion; c: ResumeChange; locked: boolean }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(c.proposed);
  const [ack, setAck] = useState(false);
  const invalidate = [keys.version(v.id), keys.versions(), keys.versions(v.job_id ?? undefined)];
  const decide = useAction(
    (body: { status: string; edited_text?: string; acknowledge_flags?: boolean }) => api.put(`/resume-versions/${v.id}/changes/${c.id}`, body),
    { invalidate, onSuccess: () => setEditing(false) },
  );
  const flagged = c.flags.length > 0;
  return (
    <li className={cn("rounded-lg border bg-surface p-3 transition-colors",
      c.status === "accepted" ? "border-positive/40" : c.status === "rejected" ? "border-border opacity-60" : flagged ? "border-warning/50" : "border-border")}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Badge className="capitalize">{c.section.replace("_", " ")}</Badge>
        <Badge tone={c.kind === "reorder" ? "info" : c.kind === "user_edit" ? "neutral" : "accent"}>
          {c.kind === "reorder" ? "Reorder only" : c.kind === "user_edit" ? "Your edit" : "AI rewrite"}
        </Badge>
        {c.status !== "pending" && <Badge tone={c.status === "accepted" ? "positive" : "neutral"}>{c.status}</Badge>}
        <p className="min-w-0 flex-1 truncate text-xs text-fg-muted">{c.rationale}</p>
      </div>
      {editing ? (
        <Textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} autoFocus />
      ) : (
        <Diff original={c.original} proposed={c.proposed} />
      )}
      {flagged && (
        <Callout className="mt-2" tone="warning" icon={AlertTriangle} title="Not found in your resume">
          <ul className="list-disc pl-4">{c.flags.map((f) => <li key={f}>{f}</li>)}</ul>
          {c.status === "pending" && !locked && (
            <label className="mt-2 flex items-center gap-2 text-fg">
              <Checkbox checked={ack} onCheckedChange={(v) => setAck(v === true)} />
              <span className="text-xs">I confirm this is accurate</span>
            </label>
          )}
        </Callout>
      )}
      {!locked && (
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {editing ? (
            <>
              <Button size="sm" variant="primary" loading={decide.isPending} onClick={() => decide.mutate({ status: "pending", edited_text: text })}>Save edit</Button>
              <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setText(c.proposed); }}>Cancel</Button>
            </>
          ) : c.status === "pending" ? (
            <>
              <Button size="sm" variant="primary" disabled={flagged && !ack} loading={decide.isPending && decide.variables?.status === "accepted"}
                onClick={() => decide.mutate({ status: "accepted", acknowledge_flags: ack })}><Check /> Accept</Button>
              <Button size="sm" variant="secondary" onClick={() => decide.mutate({ status: "rejected" })}><X /> Reject</Button>
              <Button size="sm" variant="ghost" onClick={() => setEditing(true)}><PenLine /> Edit</Button>
            </>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => decide.mutate({ status: "pending" })}><RotateCcw /> Undo</Button>
          )}
        </div>
      )}
    </li>
  );
}

export function VersionReview({ versionId, formattingPreserved }: { versionId: string; formattingPreserved: boolean }) {
  const { data: v, error, refetch, isPending } = useVersion(versionId);
  const approve = useAction(() => api.post<ResumeVersion>(`/resume-versions/${versionId}/approve`), {
    invalidate: [keys.version(versionId), ["resume-versions"], keys.resumes], success: "Approved — ready to attach to an application",
  });
  if (error) return <ErrorState error={error} onRetry={() => refetch()} />;
  if (isPending || !v) return <div className="space-y-3"><Skeleton className="h-6 w-1/2" /><Skeleton className="h-32" /><Skeleton className="h-32" /></div>;
  const pending = v.changes.filter((c) => c.status === "pending").length;
  const accepted = v.changes.filter((c) => c.status === "accepted").length;
  const locked = v.status === "approved";
  const a = v.analysis;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs text-fg-muted">Tailored version · {formatDate(v.created_at)} · {v.generated_by === "deterministic" ? "Rule-based" : v.generated_by.replace("llm:", "AI · ")}</p>
          <h2 className="mt-0.5 text-base font-semibold">{v.label}</h2>
        </div>
        {locked ? (
          <Button asChild variant="primary"><a href={`/api/v1/resume-versions/${v.id}/download`}><Download /> Download {formattingPreserved ? "DOCX" : "text"}</a></Button>
        ) : (
          <Button variant="primary" disabled={pending > 0} loading={approve.isPending} onClick={() => approve.mutate(undefined)}>
            <ShieldCheck /> {pending > 0 ? `${pending} to review` : `Approve (${accepted} change${accepted === 1 ? "" : "s"})`}
          </Button>
        )}
      </div>

      {locked && (
        <Callout tone="positive" icon={FileCheck2} title="Approved">
          {formattingPreserved ? "Accepted edits were written into a copy of your original DOCX — layout, fonts, tables, headers and bullets are untouched." : "PDF resumes can't be edited in place; the download is the tailored text. Upload a DOCX to keep formatting."}
        </Callout>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <Card className="p-3">
          <p className="text-xs font-medium text-fg-muted">Job asks for — you have</p>
          <div className="mt-2 flex flex-wrap gap-1">
            {[...(a.matched_required ?? []), ...(a.matched_preferred ?? [])].map((s) => <Badge key={s} tone="positive">{s}</Badge>)}
            {!(a.matched_required?.length || a.matched_preferred?.length) && <span className="text-xs text-fg-subtle">None detected</span>}
          </div>
        </Card>
        <Card className="p-3">
          <p className="text-xs font-medium text-fg-muted">Job asks for — not in your resume</p>
          <div className="mt-2 flex flex-wrap gap-1">
            {[...(a.missing_required ?? []), ...(a.missing_preferred ?? [])].map((s) => <Badge key={s} tone="warning">{s}</Badge>)}
            {!(a.missing_required?.length || a.missing_preferred?.length) && <span className="text-xs text-fg-subtle">Nothing missing</span>}
          </div>
          {!!a.missing_required?.length && <p className="mt-2 text-xs text-fg-muted">{a.note}</p>}
        </Card>
      </div>
      {a.summary_of_fit && <Callout tone="accent" icon={Sparkles}>{a.summary_of_fit}</Callout>}
      {a.ai_note && <Callout tone="info">{a.ai_note}</Callout>}

      {v.changes.length === 0 ? (
        <Card className="p-6 text-center text-[13px] text-fg-muted">
          No changes suggested — your resume already presents the relevant skills well for this job.
        </Card>
      ) : (
        <Card>
          <CardHeader title="Suggested changes" description={`${accepted} accepted · ${pending} pending · ${v.changes.length - accepted - pending} rejected`} />
          <ul className="flex flex-col gap-3 px-4 pb-4">
            {v.changes.map((c) => <ChangeCard key={c.id} v={v} c={c} locked={locked} />)}
          </ul>
        </Card>
      )}
    </div>
  );
}
