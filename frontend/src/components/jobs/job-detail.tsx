"use client";

import {
  AlertTriangle, ArrowUpRight, Bookmark, BookmarkCheck, Briefcase, CircleCheck, CircleHelp, CircleX, EyeOff, FileText,
  GitCompareArrows, History, MapPin, RefreshCw, Wallet,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/overlay";
import { Badge, Separator, Skeleton } from "@/components/ui/primitives";
import { FactorBar, ScoreRing } from "@/components/ui/score";
import { Callout, ErrorState } from "@/components/ui/states";
import { api } from "@/lib/api";
import {
  EMPLOYMENT_LABEL, formatDate, formatExperience, formatSalary, relativeTime, STATUS_META, WORK_MODE_LABEL,
} from "@/lib/format";
import { keys, useAction, useJob, useResumes } from "@/lib/queries";
import type { Application, EligibilityCheck, JobDetail as JobDetailT, ResumeVersion } from "@/lib/types";

const FACTOR_LABEL: Record<string, string> = {
  skills: "Skills", experience: "Experience", role: "Role fit", location: "Location", level: "Level", employment: "Employment type",
};

const OUTDATED_REASON: Record<string, string> = {
  policy: "your eligibility settings changed", engine: "JobPilot's eligibility rules were updated",
  profile: "your profile changed", listing: "the job listing changed",
  untracked: "JobPilot started tracking what each decision is based on",
};

const RULE_LABEL: Record<EligibilityCheck["rule"], string> = {
  SALARY: "Salary", STIPEND: "Stipend", SKILLS: "Skills", EXPERIENCE: "Experience", SENIORITY: "Seniority",
  LOCATION: "Location", EMPLOYMENT: "Employment type",
};
const OUTCOME: Record<EligibilityCheck["outcome"], { label: string; tone: "positive" | "danger" | "warning" | "neutral"; Icon: typeof CircleCheck }> = {
  pass: { label: "Pass", tone: "positive", Icon: CircleCheck },
  reject: { label: "Rejects", tone: "danger", Icon: CircleX },
  review: { label: "Review", tone: "warning", Icon: AlertTriangle },
  unknown: { label: "Unknown", tone: "neutral", Icon: CircleHelp },
};

/** The eligibility verdict: every rule, why, the listing's own words, and what was missing. */
function EligibilityPanel({ job }: { job: JobDetailT }) {
  const e = job.eligibility_detail!;
  const reevaluate = useAction(() => api.post(`/jobs/${job.id}/reevaluate`), {
    invalidate: [keys.job(job.id), ["jobs"], keys.dashboard], success: "Re-evaluated with your current policy",
  });
  const why = (job.eligibility_outdated_reasons ?? []).map((r) => OUTDATED_REASON[r]).filter(Boolean);
  const outdated = job.eligibility_outdated && (
    <Callout tone="neutral" icon={History} title="This decision may be out of date">
      <p>{e.policy ? `Evaluated with a ₹${e.policy.min_ctc_lpa} LPA minimum${e.evaluated_at ? ` on ${formatDate(e.evaluated_at)}` : ""}.` : "Evaluated before eligibility rules were updated."}{" "}
        Since then {why.length ? why.join(", ") : "something it was based on has changed"}. It&apos;s kept as it was until you re-evaluate.</p>
      <Button size="sm" className="mt-2" loading={reevaluate.isPending} onClick={() => reevaluate.mutate(undefined)}><RefreshCw /> Re-evaluate</Button>
    </Callout>
  );
  if (!e.checks) {
    // A decision kept from before explainable verdicts existed.
    return (
      <div className="space-y-2">
        {outdated}
        {e.status === "ineligible" ? (
          <Callout tone="danger" icon={AlertTriangle} title="Outside your criteria"><ul className="list-disc pl-4">{e.blocks.map((b) => <li key={b}>{b}</li>)}</ul></Callout>
        ) : <Callout tone={e.status === "review" ? "warning" : "positive"} icon={Wallet}>{e.salary.reason}</Callout>}
      </div>
    );
  }
  const head = e.verdict === "REJECTED" ? { tone: "danger" as const, title: "Rejected by your policy", Icon: CircleX }
    : e.verdict === "MANUAL_REVIEW" ? { tone: "warning" as const, title: "Needs your review", Icon: AlertTriangle }
    : { tone: "positive" as const, title: "Eligible", Icon: CircleCheck };
  return (
    <section aria-labelledby="elig-h" className="space-y-3">
      {outdated}
      <h3 id="elig-h" className="sr-only">Eligibility</h3>
      <Callout tone={head.tone} icon={head.Icon} title={head.title}>
        {e.summary}
        {!!e.decided_by?.length && <p className="mt-1 text-xs">Rule{e.decided_by.length > 1 ? "s" : ""}: {e.decided_by.map((r) => RULE_LABEL[r as EligibilityCheck["rule"]] ?? r).join(", ")}</p>}
      </Callout>
      <ul className="divide-y divide-border rounded-lg border border-border">
        {e.checks.map((c) => {
          const o = OUTCOME[c.outcome];
          return (
            <li key={c.rule} className="space-y-1 px-3 py-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium">{RULE_LABEL[c.rule] ?? c.rule}</span>
                <Tooltip content={e.rules?.[c.rule]}><span><Badge tone={o.tone}><o.Icon className="size-3" /> {o.label}</Badge></span></Tooltip>
              </div>
              <p className="text-xs text-fg-muted">{c.explanation}</p>
              {c.evidence.map((q) => <p key={q} className="border-l-2 border-border pl-2 text-xs text-fg-muted italic">{q}</p>)}
              {c.missing.length > 0 && <p className="text-2xs text-fg-subtle">Missing: {c.missing.join(", ")}</p>}
              <p className="font-mono text-2xs text-fg-subtle">{c.code}</p>
            </li>
          );
        })}
      </ul>
      {e.policy && (
        <p className="text-2xs text-fg-subtle">
          Policy: minimum ₹{e.policy.min_ctc_lpa} LPA (strictly greater) · engine {e.policy.engine}
          {e.policy.fx ? ` · exchange rates ${e.policy.fx.source}, ${e.policy.fx.as_of}` : " · no exchange-rate snapshot (foreign pay → review)"}
        </p>
      )}
    </section>
  );
}

export function MatchExplanation({ job }: { job: JobDetailT }) {
  const m = job.match;
  const e = job.eligibility_detail;
  if (!m || !e) return null;
  return (
    <section aria-labelledby="match-h" className="space-y-4">
      <div className="flex items-center gap-4">
        <ScoreRing score={m.score} size={56} />
        <div className="min-w-0">
          <h3 id="match-h" className="text-[13px] font-semibold">Why this score</h3>
          <p className="text-xs text-fg-muted">
            Weighted, deterministic scoring (v{m.scoring_version}).{" "}
            {m.unknown_factors.length
              ? `The listing doesn't say enough about ${m.unknown_factors.map((f) => FACTOR_LABEL[f]?.toLowerCase()).join(", ")}, so the score is pulled toward neutral.`
              : "Every factor could be assessed."}
          </p>
          {e.confidence < 0.6 && <Badge tone="warning" className="mt-1.5">Low confidence · {Math.round(e.confidence * 100)}% of factors known</Badge>}
        </div>
      </div>

      <div className="space-y-2.5 rounded-lg border border-border p-3">
        {m.factors.map((f) => (
          <div key={f.key} className="grid grid-cols-[104px_1fr_32px] items-start gap-3">
            <span className="pt-0.5 text-xs text-fg-muted">{FACTOR_LABEL[f.key]} <span className="text-fg-subtle">· {f.weight}</span></span>
            <div className="pt-1.5">
              <FactorBar value={f.score} />
              <p className="mt-1 text-xs text-fg-muted">{f.summary}</p>
            </div>
            <span className="tabular pt-0.5 text-right text-xs font-medium">{f.score == null ? "?" : Math.round(f.score * 100)}</span>
          </div>
        ))}
      </div>

      <div className="space-y-2">
        {m.matched_skills.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="w-full text-xs font-medium text-fg-muted">Matched</span>
            {m.matched_skills.map((s) => <Badge key={s} tone="positive">{s}</Badge>)}
          </div>
        )}
        {m.related_skills.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="w-full text-xs font-medium text-fg-muted">Adjacent (half credit)</span>
            {m.related_skills.map((r) => <Badge key={r.required} tone="accent">{r.required} ← {r.have}</Badge>)}
          </div>
        )}
        {(m.missing_required.length > 0 || m.missing_preferred.length > 0) && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="w-full text-xs font-medium text-fg-muted">Missing</span>
            {m.missing_required.map((s) => <Badge key={s} tone="warning">{s}</Badge>)}
            {m.missing_preferred.map((s) => <Badge key={s}>{s} · nice to have</Badge>)}
          </div>
        )}
      </div>

      <EligibilityPanel job={job} />
      {m.concerns.filter((c) => !c.startsWith("Missing required")).length > 0 && (
        <ul className="space-y-1 text-xs text-fg-muted">
          {m.concerns.filter((c) => !c.startsWith("Missing required")).map((c) => (
            <li key={c} className="flex gap-1.5"><CircleHelp className="mt-0.5 size-3 shrink-0" />{c}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function JobDetailPanel({ jobId, compareSelected, onToggleCompare }: {
  jobId: string; compareSelected?: boolean; onToggleCompare?: () => void;
}) {
  const router = useRouter();
  const { data: job, error, refetch, isPending } = useJob(jobId);
  const { data: resumes } = useResumes();
  const invalidate = [keys.job(jobId), ["jobs"], keys.dashboard];
  const setState = useAction((state: "saved" | "dismissed" | null) => api.put(`/jobs/${jobId}/state`, { state }), {
    invalidate,
  });
  const createApp = useAction(() => api.post<Application>("/applications", { job_id: jobId, status: "shortlisted" }), {
    invalidate: [...invalidate, keys.applications],
    onSuccess: (a) => router.push(`/applications/${a.id}`),
  });
  const primary = resumes?.find((r) => r.is_primary) ?? resumes?.[0];
  const tailor = useAction(() => api.post<ResumeVersion>(`/resumes/${primary!.id}/tailor`, { job_id: jobId }), {
    success: "Tailored draft created — review each suggestion.",
    onSuccess: (v) => router.push(`/resume?version=${v.id}`),
  });

  if (error) return <ErrorState error={error} onRetry={() => refetch()} />;
  if (isPending || !job) {
    return (
      <div className="space-y-4 p-6" aria-busy="true">
        <Skeleton className="h-5 w-2/3" /><Skeleton className="h-3 w-1/3" /><Skeleton className="h-40" /><Skeleton className="h-64" />
      </div>
    );
  }
  const salary = formatSalary(job);
  const exp = formatExperience(job.experience_min, job.experience_max);
  return (
    <div className="p-6">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-[13px] text-fg-muted">
            {job.company}{job.department ? ` · ${job.department}` : ""}
            <a href={job.url} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex items-center gap-0.5 text-xs text-accent hover:underline">
              Original listing <ArrowUpRight className="size-3" />
            </a>
          </p>
          <h2 className="mt-0.5 text-lg font-semibold leading-snug tracking-[-0.01em]">{job.title}</h2>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-muted">
            {job.location && <span className="flex items-center gap-1"><MapPin className="size-3" />{job.location}</span>}
            {job.work_mode && <span>{WORK_MODE_LABEL[job.work_mode]}</span>}
            {(job.is_internship || job.employment_type) && <span className="flex items-center gap-1"><Briefcase className="size-3" />{job.is_internship ? "Internship" : EMPLOYMENT_LABEL[job.employment_type!]}</span>}
            {exp && <span>{exp}</span>}
            {salary && (
              <Tooltip content={job.salary_disclosure === "verified" ? "Published in the employer's structured pay field" : "Found in the description text — not verified"}>
                <span className="flex items-center gap-1"><Wallet className="size-3" />{salary}{job.salary_disclosure === "advertised" && " (advertised)"}</span>
              </Tooltip>
            )}
            <span>Posted {relativeTime(job.posted_at ?? job.first_seen_at)}</span>
          </div>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {job.application_id ? (
          <Button asChild variant="primary"><Link href={`/applications/${job.application_id}`}>
            View application · {STATUS_META[job.application_status as keyof typeof STATUS_META]?.label}
          </Link></Button>
        ) : (
          <Button variant="primary" loading={createApp.isPending} onClick={() => createApp.mutate(undefined)} disabled={job.closed}>
            <Briefcase /> Prepare application
          </Button>
        )}
        <Tooltip content={!primary ? "Upload a resume first" : undefined}>
          <span>
            <Button onClick={() => tailor.mutate(undefined)} loading={tailor.isPending} disabled={!primary || primary.parse_status !== "parsed"}>
              <FileText /> Tailor resume
            </Button>
          </span>
        </Tooltip>
        <Button variant="ghost" onClick={() => setState.mutate(job.saved_state === "saved" ? null : "saved")}
          aria-pressed={job.saved_state === "saved"}>
          {job.saved_state === "saved" ? <BookmarkCheck className="text-accent" /> : <Bookmark />} {job.saved_state === "saved" ? "Saved" : "Save"}
        </Button>
        <Button variant="ghost" onClick={() => setState.mutate(job.saved_state === "dismissed" ? null : "dismissed")}>
          <EyeOff /> {job.saved_state === "dismissed" ? "Restore" : "Dismiss"}
        </Button>
        {onToggleCompare && (
          <Button variant="ghost" aria-pressed={compareSelected} onClick={onToggleCompare}>
            <GitCompareArrows className={compareSelected ? "text-accent" : undefined} /> {compareSelected ? "Comparing" : "Compare"}
          </Button>
        )}
      </div>
      {job.closed && <Callout className="mt-4" tone="warning" icon={AlertTriangle}>The employer has closed this posting.</Callout>}

      <Separator className="my-6" />
      <MatchExplanation job={job} />
      <Separator className="my-6" />

      <section aria-label="Job description">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-[13px] font-semibold">Job description</h3>
          <span className="text-2xs text-fg-subtle">From {job.provider} · shown as published</span>
        </div>
        {job.description_html ? (
          // Sanitised server-side with an allow-list (nh3); links are rel=noopener.
          <div className="prose-jd text-[13px]" dangerouslySetInnerHTML={{ __html: job.description_html }} />
        ) : (
          <p className="whitespace-pre-wrap text-[13px] leading-relaxed">{job.description_text}</p>
        )}
      </section>
    </div>
  );
}
