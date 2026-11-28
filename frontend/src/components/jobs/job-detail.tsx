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
  EMPLOYMENT_LABEL, formatDate, formatExperience, formatPolicyValue, formatSalary, groupPolicyChanges, POLICY_SETTING,
  relativeTime, salarySource, STATUS_META, WORK_MODE_LABEL,
} from "@/lib/format";
import { keys, useAction, useJob, useResumes } from "@/lib/queries";
import type {
  Application, CompensationDetails, EligibilityCheck, JobDetail as JobDetailT, ResumeVersion, SkillEvidence,
} from "@/lib/types";

const FACTOR_LABEL: Record<string, string> = {
  skills: "Skills", experience: "Experience", role: "Role fit", location: "Location", level: "Level", employment: "Employment type",
};

const OUTDATED_REASON: Record<string, string> = {
  policy: "your eligibility settings changed", engine: "JobPilot's eligibility rules were updated",
  profile: "your profile changed", listing: "the job listing changed",
  untracked: "JobPilot started tracking what each decision is based on",
};

const RULE_LABEL: Record<EligibilityCheck["rule"], string> = {
  SALARY: "Salary", STIPEND: "Stipend", COMPENSATION: "Compensation", SKILLS: "Skills", EXPERIENCE: "Experience", SENIORITY: "Seniority",
  LOCATION: "Location", EMPLOYMENT: "Employment type",
};
const OUTCOME: Record<EligibilityCheck["outcome"], { label: string; tone: "positive" | "danger" | "warning" | "neutral"; Icon: typeof CircleCheck }> = {
  pass: { label: "Pass", tone: "positive", Icon: CircleCheck },
  reject: { label: "Rejects", tone: "danger", Icon: CircleX },
  review: { label: "Review", tone: "warning", Icon: AlertTriangle },
  unknown: { label: "Unknown", tone: "neutral", Icon: CircleHelp },
};

const VERIFICATION: Record<CompensationDetails["verification"], { label: string; tone: "positive" | "warning" | "neutral" }> = {
  EMPLOYER_VERIFIED: { label: "Employer-verified", tone: "positive" },
  ADVERTISED_UNVERIFIED: { label: "Advertised · unverified", tone: "warning" },
  NOT_DISCLOSED: { label: "Not disclosed", tone: "neutral" },
};

function policyText(p: CompensationDetails["policy"]): string {
  if (p.applied === "apprenticeship stipend minimum") {
    const min = p.minimum == null ? "no minimum" : `minimum ${p.currency ?? "currency not set"} ${Number(p.minimum).toLocaleString("en-IN")}/${p.period}`;
    return `Apprenticeship stipend policy: ${min} (meets or exceeds) · advertised stipends: ${p.advertised_stipends === "review" ? "always review" : "compared"}${p.advertised_stipends !== "review" ? ` · advertised and meeting the minimum: ${p.advertised_meeting_minimum}` : ""}`;
  }
  if (p.applied === "salary threshold") return `Salary threshold: ₹${p.min_ctc_lpa} LPA (strictly greater)`;
  return "No compensation rule applied";
}

/** Which compensation rule an apprenticeship decision used, and what it was based on. */
function CompensationFacts({ d }: { d: CompensationDetails }) {
  const v = VERIFICATION[d.verification];
  const amount = d.amount_min == null ? null
    : d.amount_max != null && d.amount_max !== d.amount_min ? `${d.amount_min.toLocaleString("en-IN")}–${d.amount_max.toLocaleString("en-IN")}`
    : d.amount_min.toLocaleString("en-IN");
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 rounded-md bg-surface-2 px-2.5 py-2 text-2xs">
      <dt className="text-fg-subtle">Pay described as</dt><dd>{d.compensation_type}</dd>
      <dt className="text-fg-subtle">Amount</dt>
      <dd>{amount ? `${amount} · ${d.currency_identified ? d.currency : `currency ${d.currency ? `unclear (${d.currency})` : "not stated"}`} · ${d.period ? `per ${d.period}` : "period not stated"}` : "Not disclosed"}</dd>
      <dt className="text-fg-subtle">Source</dt><dd>{d.source ?? "—"}</dd>
      <dt className="text-fg-subtle">Verification</dt><dd><Badge tone={v.tone}>{v.label}</Badge></dd>
      <dt className="text-fg-subtle">Rule applied</dt><dd>{policyText(d.policy)}</dd>
    </dl>
  );
}

/** The eligibility verdict: every rule, why, the listing's own words, and what was missing. */
function EligibilityPanel({ job }: { job: JobDetailT }) {
  const e = job.eligibility_detail!;
  const reevaluate = useAction(() => api.post(`/jobs/${job.id}/reevaluate`), {
    invalidate: [keys.job(job.id), ["jobs"], keys.dashboard], success: "Re-evaluated with your current policy",
  });
  const why = (job.eligibility_outdated_reasons ?? []).map((r) => OUTDATED_REASON[r]).filter(Boolean);
  const groups = groupPolicyChanges(job.eligibility_policy_changes ?? []);
  const onlyApprenticeship = groups.length > 0 && groups.every((g) => g.group === "apprenticeship");
  const outdated = job.eligibility_outdated && (
    <Callout tone="neutral" icon={History} title="This decision may be out of date">
      <p>{e.evaluated_at ? `Evaluated on ${formatDate(e.evaluated_at)}. ` : e.policy ? "" : "Evaluated before eligibility rules were updated. "}
        {groups.length ? "It's outdated because your eligibility settings changed since then" : `Since then ${why.length ? why.join(", ") : "something it was based on has changed"}`}
        {groups.length && why.length > 1 ? ` (and ${why.filter((w) => w !== OUTDATED_REASON.policy).join(", ")})` : ""}.</p>
      {groups.map((g) => (
        <div key={g.group} className="mt-2">
          <p className="text-xs font-medium text-fg">{g.label}</p>
          <ul className="mt-0.5 space-y-0.5 text-xs">
            {g.changes.map((c) => (
              <li key={c.key}>{POLICY_SETTING[c.key] ?? c.key}: <span className="text-fg-subtle line-through">{formatPolicyValue(c.key, c.before)}</span> → <span className="font-medium text-fg">{formatPolicyValue(c.key, c.after)}</span></li>
            ))}
          </ul>
        </div>
      ))}
      {onlyApprenticeship && why.length === 1 && job.employment_type !== "apprenticeship" && (
        <p className="mt-2 text-xs">These settings only apply to apprenticeship listings, so re-evaluating this job shouldn&apos;t change its result.</p>
      )}
      <p className="mt-2">The decision is kept as it was: JobPilot never re-evaluates a saved or applied job on its own. Re-evaluate to apply your current settings.</p>
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
              {c.details && "reason_code" in c.details && <CompensationFacts d={c.details as CompensationDetails} />}
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

const TIER: { key: SkillEvidence["tier"]; label: string; hint: string }[] = [
  { key: "mandatory", label: "Required", hint: "In the requirements, or stated as required" },
  { key: "preferred", label: "Nice to have", hint: "Preferred, a plus, or under nice-to-have" },
  { key: "mentioned", label: "Mentioned", hint: "Named elsewhere — never counted against you" },
];

/** What JobPilot read from the listing, with the listing's own words behind each fact. */
function ListingFacts({ job }: { job: JobDetailT }) {
  const ev = job.field_evidence ?? {};
  const skills = ev.skills ?? {};
  const tiers: Record<SkillEvidence["tier"], string[]> = {
    mandatory: job.required_skills, preferred: job.preferred_skills, mentioned: job.mentioned_skills ?? [],
  };
  const facts = [
    job.work_mode && ev.work_mode && { label: WORK_MODE_LABEL[job.work_mode], quote: ev.work_mode },
    job.remote_region && ev.remote_region && { label: `Remote from: ${job.remote_region}`, quote: ev.remote_region },
    job.employment_type && ev.employment_type && { label: EMPLOYMENT_LABEL[job.employment_type] ?? job.employment_type, quote: ev.employment_type },
    !job.employment_type && ev.employment_type && { label: "Employment type unclear", quote: ev.employment_type },
    job.experience_min != null && ev.experience && { label: `Requires ${formatExperience(job.experience_min, job.experience_max)}`, quote: ev.experience },
    job.experience_preferred_min != null && ev.experience_preferred && {
      label: `Prefers ${job.experience_preferred_min}+ yrs (not required)`, quote: ev.experience_preferred,
    },
  ].filter(Boolean) as { label: string; quote: string }[];
  const anySkills = TIER.some((t) => tiers[t.key].length > 0);
  if (!anySkills && facts.length === 0 && job.mentioned_skills != null) return null;
  return (
    <section aria-labelledby="facts-h" className="space-y-3">
      <div>
        <h3 id="facts-h" className="text-[13px] font-semibold">From the listing</h3>
        <p className="text-xs text-fg-muted">What JobPilot read from the description. Expand any item to see the wording it came from.</p>
      </div>
      {job.mentioned_skills == null && (
        <Callout tone="info" icon={RefreshCw}>
          These details were read before JobPilot&apos;s latest extraction update. They refresh the next time this board syncs.
        </Callout>
      )}
      {anySkills && (
        <div className="space-y-2">
          {TIER.filter((t) => tiers[t.key].length > 0).map((t) => (
            <div key={t.key} className="flex flex-wrap items-center gap-1.5">
              <span className="w-full text-xs font-medium text-fg-muted">{t.label} <span className="font-normal text-fg-subtle">· {t.hint}</span></span>
              {tiers[t.key].map((s) => (
                <Badge key={s} tone={t.key === "mandatory" ? "accent" : "neutral"}>{s}</Badge>
              ))}
            </div>
          ))}
          {Object.keys(skills).length > 0 && (
            <details className="rounded-md border border-border px-3 py-2 text-xs">
              <summary className="cursor-pointer text-fg-muted">Where each skill came from</summary>
              <ul className="mt-2 space-y-1.5">
                {TIER.flatMap((t) => tiers[t.key].filter((s) => skills[s]).map((s) => (
                  <li key={s}>
                    <span className="font-medium">{s}</span>
                    <span className="text-fg-subtle"> · {t.label.toLowerCase()}{skills[s].section ? ` (under “${skills[s].section}”)` : ""}
                      {skills[s].term.toLowerCase() !== s.toLowerCase() ? ` · written as “${skills[s].term}”` : ""}</span>
                    <p className="text-fg-muted">{skills[s].quote}</p>
                  </li>
                )))}
              </ul>
            </details>
          )}
        </div>
      )}
      {facts.length > 0 && (
        <ul className="space-y-1.5 text-xs">
          {facts.map((f) => (
            <li key={f.label}>
              <details>
                <summary className="cursor-pointer"><span className="font-medium">{f.label}</span></summary>
                <p className="mt-1 pl-4 text-fg-muted">{f.quote}</p>
              </details>
            </li>
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
            {job.work_mode && <span>{WORK_MODE_LABEL[job.work_mode]}{job.remote_region ? ` · ${job.remote_region}` : ""}</span>}
            {(job.is_internship || job.employment_type) && <span className="flex items-center gap-1"><Briefcase className="size-3" />{job.is_internship ? "Internship" : EMPLOYMENT_LABEL[job.employment_type!]}</span>}
            {exp && <span>{exp}</span>}
            {salary && (
              <Tooltip content={job.salary_disclosure === "verified" ? "Published in the employer's structured pay field" : "Found in the description text — not verified"}>
                <span className="flex items-center gap-1"><Wallet className="size-3" />{salarySource(job)} · {salary}</span>
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
      <ListingFacts job={job} />
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
