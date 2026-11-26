"use client";

import {
  CircleDot, Download, FileText, FileUp, GraduationCap, Layers, MoreHorizontal, RefreshCw, Star, Trash2, UserCheck, Wand2,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ReviewEditor } from "@/components/resume/review-editor";
import { UploadZone } from "@/components/resume/upload-zone";
import { VersionReview } from "@/components/resume/version-review";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/controls";
import { ConfirmDialog, Dialog, DialogContent, Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger, Tooltip } from "@/components/ui/overlay";
import { Badge, Card, CardHeader, Progress, Skeleton } from "@/components/ui/primitives";
import { Callout, EmptyState, ErrorState, PageHeader } from "@/components/ui/states";
import { api } from "@/lib/api";
import { cn, formatDate, pluralize, relativeTime } from "@/lib/format";
import { keys, useAction, useResume, useResumes, useVersions } from "@/lib/queries";
import type { ResumeDetail } from "@/lib/types";

const SECTION_LABEL: Record<string, string> = {
  summary: "Summary", experience: "Experience", internships: "Internships", freelance: "Freelance", projects: "Projects",
  academic_projects: "Academic projects", education: "Education", skills: "Skills", certifications: "Certifications",
  achievements: "Achievements", publications: "Publications", leadership: "Leadership & activities", languages: "Languages",
  interests: "Interests", contact: "Contact", unknown: "Unrecognised heading",
};
const CATEGORY_LABEL: Record<string, string> = {
  professional: "Professional experience", internship: "Internships", freelance: "Freelance",
};

function Analysis({ r }: { r: ResumeDetail }) {
  const q = r.quality;
  const m = (q.metrics ?? {}) as Record<string, number>;
  const x = r.extracted;
  const exps = x.experiences ?? [];
  const groups = ["professional", "internship", "freelance"].map((c) => [c, exps.filter((e) => e.category === c)] as const).filter(([, l]) => l.length);
  const skills = Object.keys(x.skills ?? {});
  const score = q.score ?? 0;
  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
      <div className="flex min-w-0 flex-col gap-5">
        <Card>
          <CardHeader title="Quality review" description="Deterministic checks — nothing is sent to an AI for this." />
          <div className="grid gap-4 px-4 pb-4 sm:grid-cols-[140px_1fr]">
            <div>
              <p className="tabular text-3xl font-semibold tracking-[-0.03em]">{score}<span className="text-base text-fg-subtle">/100</span></p>
              <Progress className="mt-2" value={score} tone={score >= 80 ? "positive" : score >= 60 ? "accent" : "warning"} label="Resume quality" />
              <dl className="mt-3 space-y-1 text-xs text-fg-muted">
                <div className="flex justify-between"><dt>Bullets</dt><dd className="tabular text-fg">{m.bullets ?? 0}</dd></div>
                <div className="flex justify-between"><dt>With metrics</dt><dd className="tabular text-fg">{m.quantified_bullets ?? 0}</dd></div>
                <div className="flex justify-between"><dt>Action verbs</dt><dd className="tabular text-fg">{m.action_verb_bullets ?? 0}</dd></div>
                <div className="flex justify-between"><dt>Words</dt><dd className="tabular text-fg">{m.word_count ?? 0}</dd></div>
              </dl>
            </div>
            <ul className="flex flex-col gap-2">
              {(q.issues ?? []).length === 0 && <li className="text-[13px] text-fg-muted">No issues found.</li>}
              {(q.issues ?? []).map((i, k) => (
                <li key={k} className="flex gap-2 text-[13px]">
                  <CircleDot className={cn("mt-0.5 size-3.5 shrink-0", i.severity === "high" ? "text-danger" : i.severity === "medium" ? "text-warning" : "text-fg-subtle")} />
                  <span>{i.message}</span>
                </li>
              ))}
            </ul>
          </div>
        </Card>

        {groups.map(([cat, list]) => (
          <Card key={cat}>
            <CardHeader title={CATEGORY_LABEL[cat]} description={pluralize(list.length, "entry", "entries")} />
            <ul className="divide-y divide-border border-t border-border">
              {list.map((e, i) => (
                <li key={i} className="px-4 py-3">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <p className="text-[13px] font-medium">{e.title ?? "Role"} <span className="font-normal text-fg-muted">· {e.employer}</span></p>
                    <p className="text-xs text-fg-subtle">{formatDate(e.start_date, { month: "short", year: "numeric" })} – {e.is_current ? "Present" : formatDate(e.end_date, { month: "short", year: "numeric" })}</p>
                  </div>
                  {e.bullets.length > 0 && (
                    <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-[13px] text-fg-muted">{e.bullets.map((b, j) => <li key={j}>{b}</li>)}</ul>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        ))}

        {!!x.projects?.length && (
          <Card>
            <CardHeader title="Projects" />
            <ul className="divide-y divide-border border-t border-border">
              {x.projects.map((p, i) => (
                <li key={i} className="flex flex-wrap items-center gap-2 px-4 py-2.5">
                  <span className="text-[13px] font-medium">{p.name}</span>
                  <Badge tone={p.category === "academic" ? "info" : "neutral"}>{p.category}</Badge>
                  <span className="ml-auto flex flex-wrap gap-1">{p.technologies.slice(0, 5).map((t) => <Badge key={t}>{t}</Badge>)}</span>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>

      <div className="flex flex-col gap-5">
        <Card className="p-4">
          <p className="text-xs font-medium text-fg-muted">Experience</p>
          <p className="mt-1 text-[13px]"><span className="tabular text-lg font-semibold">{x.years_experience ?? 0}</span> yrs professional</p>
          <p className="text-xs text-fg-muted">{x.internship_months ? `+ ${x.internship_months} months of internships (counted separately)` : "No internships detected"}</p>
          <Badge className="mt-2 capitalize">{x.seniority} level</Badge>
        </Card>
        <Card className="p-4">
          <p className="mb-2 text-xs font-medium text-fg-muted">Skills recognised <span className="text-fg-subtle">({skills.length})</span></p>
          <div className="flex flex-wrap gap-1">
            {skills.map((s) => (
              <Tooltip key={s} content={x.skills?.[s]?.[0] ? <>Found in: “{x.skills[s][0]}”</> : undefined}>
                <span><Badge tone="accent">{s}</Badge></span>
              </Tooltip>
            ))}
          </div>
          {!!x.unrecognised_skills?.length && (
            <>
              <p className="mt-3 mb-1.5 text-xs font-medium text-fg-muted">Not in the skills library</p>
              <div className="flex flex-wrap gap-1">{x.unrecognised_skills.map((s) => <Badge key={s}>{s}</Badge>)}</div>
            </>
          )}
        </Card>
        {!!x.education?.length && (
          <Card className="p-4">
            <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-fg-muted"><GraduationCap className="size-3.5" /> Education</p>
            {x.education.map((e, i) => <p key={i} className="text-[13px]">{e.degree ?? "Degree"} <span className="text-fg-muted">· {e.institution}{e.grade ? ` · ${e.grade}` : ""}</span></p>)}
          </Card>
        )}
        {!!x.missing?.length && (
          <Callout tone="warning" title="Missing information">
            {x.missing.map((k) => k.replace("section:", "").replace("_", " ")).join(", ")}
          </Callout>
        )}
      </div>
    </div>
  );
}

function Structure({ r }: { r: ResumeDetail }) {
  return (
    <Card>
      <CardHeader title="Detected structure" description="How each heading in your document was understood. Section names vary — JobPilot maps them by meaning." />
      <ul className="divide-y divide-border border-t border-border">
        {r.sections.map((s, i) => (
          <li key={i} className="grid grid-cols-[1fr_auto] gap-3 px-4 py-2.5 sm:grid-cols-[200px_1fr_auto]">
            <span className="truncate text-[13px] font-medium">“{s.raw_heading}”</span>
            <span className="hidden truncate text-xs text-fg-muted sm:block">{s.text.split("\n")[0]}</span>
            <span className="flex items-center gap-2">
              <Badge tone="accent">{SECTION_LABEL[s.canonical_type] ?? s.canonical_type}</Badge>
              <Tooltip content="Classification confidence"><span className="tabular w-8 text-right text-xs text-fg-subtle">{Math.round(s.confidence * 100)}%</span></Tooltip>
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function ResumeInner() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const versionId = params.get("version");
  const { data: resumes, error, refetch, isPending } = useResumes();
  const [selected, setSelected] = useState<string | null>(params.get("id"));
  const [uploadOpen, setUploadOpen] = useState(params.get("upload") === "1");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const { data: versions } = useVersions();
  const activeVersion = versions?.find((v) => v.id === versionId);
  const current = (versionId ? activeVersion?.resume_id : null) ?? selected ?? resumes?.find((r) => r.is_primary)?.id ?? resumes?.[0]?.id ?? null;
  const { data: r } = useResume(current);

  const inv = [keys.resumes, keys.resume(current ?? ""), keys.review(current ?? ""), keys.profile, keys.dashboard];
  const setPrimary = useAction(() => api.post(`/resumes/${current}/primary`), { invalidate: inv, success: "Set as primary resume" });
  const reparse = useAction(() => api.post(`/resumes/${current}/parse`), { invalidate: inv, success: "Re-analysed" });
  const apply = useAction(() => api.post<{ experiences: number; skills: number }>(`/resumes/${current}/apply-to-profile`, { replace: true }), {
    invalidate: inv, success: (s) => `Profile updated: ${s.experiences} roles, ${s.skills} skills (unverified until you review them)`,
  });
  const remove = useAction(() => api.del(`/resumes/${current}`), {
    invalidate: inv, success: "Resume deleted", onSuccess: () => { setSelected(null); setDeleteOpen(false); },
  });
  const openVersion = (id: string | null) => router.replace(id ? `${pathname}?version=${id}` : pathname, { scroll: false });

  return (
    <>
      <PageHeader title="Resume Studio" description="Understand your resume, fix weak spots, and tailor it per job without touching its design."
        actions={<Button variant="primary" onClick={() => setUploadOpen(true)}><FileUp /> Upload resume</Button>} />

      {error ? <Card><ErrorState error={error} onRetry={() => refetch()} /></Card> : isPending ? <Skeleton className="h-96 rounded-lg" /> : !resumes.length ? (
        <Card>
          <EmptyState icon={FileText} title="Upload your resume to begin"
            description="JobPilot reads PDF or DOCX, recognises sections however they're named, and keeps professional experience, internships and projects separate." />
          <div className="mx-auto max-w-lg px-4 pb-10"><UploadZone onUploaded={(x) => { setSelected(x.id); refetch(); }} /></div>
        </Card>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[240px_minmax(0,1fr)]">
          <nav aria-label="Resumes and versions" className="flex flex-col gap-5">
            <div>
              <p className="mb-1.5 px-2 text-2xs font-medium uppercase tracking-wide text-fg-subtle">Resumes</p>
              <ul className="flex flex-col gap-0.5">
                {resumes.map((x) => (
                  <li key={x.id}>
                    <button onClick={() => { setSelected(x.id); openVersion(null); }}
                      className={cn("flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors",
                        x.id === current && !versionId ? "bg-surface-3" : "hover:bg-surface-3/60")}>
                      <FileText className="size-4 shrink-0 text-fg-subtle" />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-1.5 truncate text-[13px] font-medium">{x.name}{x.is_primary && <Star className="size-3 fill-accent text-accent" aria-label="Primary" />}</span>
                        <span className="block text-2xs text-fg-subtle uppercase">{x.file_format} · {x.parse_status === "failed" ? "couldn't read" : `${x.quality_score ?? "–"}/100`}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <p className="mb-1.5 px-2 text-2xs font-medium uppercase tracking-wide text-fg-subtle">Tailored versions</p>
              {!versions?.length ? (
                <p className="px-2 text-xs text-fg-muted">Open a job and choose <span className="font-medium">Tailor resume</span>.</p>
              ) : (
                <ul className="flex flex-col gap-0.5">
                  {versions.map((v) => (
                    <li key={v.id}>
                      <button onClick={() => openVersion(v.id)}
                        className={cn("flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left", v.id === versionId ? "bg-surface-3" : "hover:bg-surface-3/60")}>
                        <Layers className="size-4 shrink-0 text-fg-subtle" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[13px]">{v.label}</span>
                          <span className="block text-2xs text-fg-subtle">{v.status === "approved" ? "Approved" : `${v.changes.filter((c) => c.status === "pending").length} to review`} · {relativeTime(v.created_at)}</span>
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </nav>

          <div className="min-w-0">
            {versionId ? (
              <VersionReview versionId={versionId} formattingPreserved={resumes.find((x) => x.id === activeVersion?.resume_id)?.file_format === "docx"} />
            ) : !r ? <Skeleton className="h-96 rounded-lg" /> : (
              <div className="flex flex-col gap-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="truncate text-base font-semibold">{r.name}</h2>
                    <p className="text-xs text-fg-muted">{r.original_filename} · uploaded {relativeTime(r.created_at)}
                      {r.formatting_preserved ? " · formatting-preserving tailoring" : " · PDF: tailoring produces text"}</p>
                  </div>
                  <div className="flex gap-2">
                    <Button onClick={() => apply.mutate(undefined)} loading={apply.isPending} disabled={r.parse_status !== "parsed"}><UserCheck /> Update profile</Button>
                    <Menu>
                      <MenuTrigger asChild><Button size="icon" aria-label="Resume actions"><MoreHorizontal /></Button></MenuTrigger>
                      <MenuContent>
                        {!r.is_primary && <MenuItem onSelect={() => setPrimary.mutate(undefined)}><Star /> Make primary</MenuItem>}
                        <MenuItem onSelect={() => reparse.mutate(undefined)}><RefreshCw /> Re-analyse file (corrections kept)</MenuItem>
                        <MenuItem asChild><a href={`/api/v1/resumes/${r.id}/download`}><Download /> Download original</a></MenuItem>
                        <MenuSeparator />
                        <MenuItem destructive onSelect={() => setDeleteOpen(true)}><Trash2 /> Delete</MenuItem>
                      </MenuContent>
                    </Menu>
                  </div>
                </div>
                {r.parse_status === "failed" ? (
                  <Callout tone="danger" title="We couldn't read this file">{r.parse_error}</Callout>
                ) : (
                  <Tabs defaultValue="review">
                    <TabsList>
                      <TabsTrigger value="review">Review &amp; edit</TabsTrigger>
                      <TabsTrigger value="analysis">Analysis</TabsTrigger>
                      <TabsTrigger value="structure">Structure</TabsTrigger>
                    </TabsList>
                    <TabsContent value="review" className="pt-5"><ReviewEditor key={r.id} resumeId={r.id} /></TabsContent>
                    <TabsContent value="analysis" className="pt-5">
                      <Callout tone="neutral" className="mb-5">This is what was read from the file. To fix anything, use <span className="font-medium">Review &amp; edit</span>; your corrections are what JobPilot uses.</Callout>
                      <Analysis r={r} />
                    </TabsContent>
                    <TabsContent value="structure" className="pt-5"><Structure r={r} /></TabsContent>
                  </Tabs>
                )}
                <Callout tone="neutral" icon={Wand2}>
                  To tailor for a role, open it in <Link href="/jobs" className="font-medium text-fg underline-offset-2 hover:underline">Discover Jobs</Link> and
                  choose Tailor resume. Suggestions never add skills or results you don&apos;t already have.
                </Callout>
              </div>
            )}
          </div>
        </div>
      )}
      <Dialog open={uploadOpen} onOpenChange={setUploadOpen}>
        <DialogContent title="Upload a resume" description="DOCX keeps your exact formatting when tailoring.">
          <UploadZone compact onUploaded={(x) => { setSelected(x.id); setUploadOpen(false); refetch(); openVersion(null); }} />
        </DialogContent>
      </Dialog>
      <ConfirmDialog open={deleteOpen} onOpenChange={setDeleteOpen} destructive confirmLabel="Delete" title="Delete this resume?"
        description="Its tailored versions are deleted too. Profile details already imported are kept." loading={remove.isPending}
        onConfirm={() => remove.mutate(undefined)} />
    </>
  );
}

export default function ResumePage() {
  return <Suspense><ResumeInner /></Suspense>;
}
