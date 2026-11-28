"use client";

import { useQueryClient } from "@tanstack/react-query";
import { motion } from "motion/react";
import { ArrowLeft, ArrowRight, Check, CircleAlert, FileCheck2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "@/lib/toast";
import { Logo } from "@/components/brand";
import { UploadZone } from "@/components/resume/upload-zone";
import { Button } from "@/components/ui/button";
import { ChipGroup, TagInput } from "@/components/ui/controls";
import { Badge, Field, Input, Skeleton, Textarea } from "@/components/ui/primitives";
import { Callout } from "@/components/ui/states";
import { api, ApiError } from "@/lib/api";
import { cn, EMPLOYMENT_OPTIONS, EXPERIENCE_LEVELS, formatDate } from "@/lib/format";
import { keys, useMe, useProfile } from "@/lib/queries";
import type { Profile, ResumeDetail } from "@/lib/types";

const STEPS = ["Resume", "About you", "Goals", "Preferences", "Review"] as const;

export default function Onboarding() {
  const router = useRouter();
  const qc = useQueryClient();
  const { data: me, error: meError } = useMe();
  const { data: full, refetch } = useProfile();
  const [step, setStep] = useState(0);
  const [edited, setDraft] = useState<Profile | null>(null);
  const draft = edited ?? full?.profile ?? null;
  const [saving, setSaving] = useState(false);
  const [imported, setImported] = useState<{ experiences: number; skills: number; projects: number; education: number } | null>(null);

  useEffect(() => {
    if (meError instanceof ApiError && meError.status === 401) router.replace("/login?next=/onboarding");
  }, [meError, router]);
  const set = <K extends keyof Profile>(k: K, v: Profile[K]) => setDraft(draft ? { ...draft, [k]: v } : null);

  const saveProfile = async () => {
    if (!draft) return;
    const { verified_at: _v, ...body } = draft;
    await api.put("/profile", { ...body, linkedin_url: body.linkedin_url || null, portfolio_url: body.portfolio_url || null });
  };

  const next = async () => {
    setSaving(true);
    try {
      if (step > 0) await saveProfile();
      if (step === STEPS.length - 1) {
        await api.post("/profile/verify");
        await api.post("/onboarding/complete");
        await qc.invalidateQueries({ queryKey: keys.me });
        toast.success("You're all set. Next: follow a few company job boards.");
        router.replace("/overview");
        return;
      }
      setStep((s) => s + 1);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't save. Try again.");
    } finally {
      setSaving(false);
    }
  };

  const onUploaded = async (r: ResumeDetail) => {
    if (r.parse_status !== "parsed") return;
    try {
      const s = await api.post<{ experiences: number; skills: number; projects: number; education: number }>(
        `/resumes/${r.id}/apply-to-profile`, { replace: true });
      setImported(s);
      const fresh = await refetch();
      if (fresh.data) setDraft(fresh.data.profile);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't import the resume.");
    }
  };

  const canContinue =
    step !== 2 || (draft?.target_titles.length ?? 0) > 0;

  return (
    <div className="min-h-dvh">
      <header className="mx-auto flex h-16 max-w-3xl items-center justify-between px-4">
        <Logo />
        <span className="text-xs text-fg-muted">Signed in as {me?.email}</span>
      </header>
      <div className="mx-auto max-w-3xl px-4 pb-16">
        <ol className="mb-8 grid grid-cols-5 gap-2" aria-label="Onboarding progress">
          {STEPS.map((s, i) => (
            <li key={s} className="flex flex-col gap-2" aria-current={i === step ? "step" : undefined}>
              <div className={cn("h-1 rounded-full transition-colors", i <= step ? "bg-accent" : "bg-surface-3")} />
              <span className={cn("text-xs", i === step ? "font-medium text-fg" : "text-fg-subtle")}>{s}</span>
            </li>
          ))}
        </ol>

        {!draft ? (
          <div className="space-y-3"><Skeleton className="h-6 w-1/3" /><Skeleton className="h-40" /></div>
        ) : (
          <motion.div key={step} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.18 }}>
            {step === 0 && (
              <section>
                <h1 className="text-xl font-semibold tracking-[-0.015em]">Start with your resume</h1>
                <p className="mt-1 text-[13px] text-fg-muted">
                  We&apos;ll read it to prefill your profile. Everything imported stays marked as unverified until you
                  review it in the last step.
                </p>
                <div className="mt-6">
                  {imported ? (
                    <Callout tone="positive" icon={FileCheck2} title="Resume imported">
                      Found {imported.experiences} roles, {imported.projects} projects, {imported.education} education
                      entries and {imported.skills} skills. You&apos;ll review them before finishing.
                    </Callout>
                  ) : (
                    <UploadZone onUploaded={onUploaded} />
                  )}
                </div>
                <p className="mt-4 text-xs text-fg-muted">No resume handy? You can skip this and add one later in Resume Studio.</p>
              </section>
            )}

            {step === 1 && (
              <section className="space-y-5">
                <div>
                  <h1 className="text-xl font-semibold tracking-[-0.015em]">About you</h1>
                  <p className="mt-1 text-[13px] text-fg-muted">Used to fill application forms. Only you can see this.</p>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Full name" htmlFor="full_name"><Input id="full_name" value={draft.full_name ?? ""} onChange={(e) => set("full_name", e.target.value)} /></Field>
                  <Field label="Phone" htmlFor="phone"><Input id="phone" value={draft.phone ?? ""} onChange={(e) => set("phone", e.target.value)} /></Field>
                  <Field label="Current location" htmlFor="location"><Input id="location" placeholder="Bengaluru, India" value={draft.location ?? ""} onChange={(e) => set("location", e.target.value)} /></Field>
                  <Field label="Work authorization" htmlFor="auth" hint="e.g. Indian citizen; no sponsorship needed">
                    <Input id="auth" value={draft.work_authorization ?? ""} onChange={(e) => set("work_authorization", e.target.value)} />
                  </Field>
                  <Field label="LinkedIn URL" htmlFor="li" optional><Input id="li" type="url" placeholder="https://linkedin.com/in/…" value={draft.linkedin_url ?? ""} onChange={(e) => set("linkedin_url", e.target.value)} /></Field>
                  <Field label="Portfolio or GitHub" htmlFor="pf" optional><Input id="pf" type="url" value={draft.portfolio_url ?? ""} onChange={(e) => set("portfolio_url", e.target.value)} /></Field>
                </div>
              </section>
            )}

            {step === 2 && (
              <section className="space-y-5">
                <div>
                  <h1 className="text-xl font-semibold tracking-[-0.015em]">What are you looking for?</h1>
                  <p className="mt-1 text-[13px] text-fg-muted">Target titles drive role matching. Add a few variations.</p>
                </div>
                <Field label="Target job titles" htmlFor="titles" hint="Press Enter after each title.">
                  <TagInput id="titles" value={draft.target_titles} onChange={(v) => set("target_titles", v)} placeholder="Backend Engineer, Software Engineer…" />
                </Field>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Years of professional experience" htmlFor="yrs" hint="Internships aren't counted.">
                    <Input id="yrs" type="number" min={0} max={60} step={0.5} value={draft.years_experience ?? ""}
                      onChange={(e) => set("years_experience", e.target.value === "" ? null : Number(e.target.value))} />
                  </Field>
                  <Field label="Notice period" htmlFor="notice" optional>
                    <Input id="notice" placeholder="Immediate, 30 days…" value={draft.notice_period ?? ""} onChange={(e) => set("notice_period", e.target.value)} />
                  </Field>
                </div>
                <Field label="Experience levels to target">
                  <ChipGroup ariaLabel="Experience levels" value={draft.experience_levels} onChange={(v) => set("experience_levels", v)} options={EXPERIENCE_LEVELS} />
                </Field>
                <Field label="Career objectives" htmlFor="obj" optional>
                  <Textarea id="obj" rows={3} value={draft.career_objectives ?? ""} onChange={(e) => set("career_objectives", e.target.value)}
                    placeholder="What you want next — domain, scope, the kind of team." />
                </Field>
              </section>
            )}

            {step === 3 && (
              <section className="space-y-5">
                <div>
                  <h1 className="text-xl font-semibold tracking-[-0.015em]">Preferences</h1>
                  <p className="mt-1 text-[13px] text-fg-muted">These shape match scores and automation filters. Change them any time.</p>
                </div>
                <Field label="Preferred locations" hint="Cities you'd work in. Add “Remote” if that's an option for you.">
                  <TagInput value={draft.preferred_locations} onChange={(v) => set("preferred_locations", v)} placeholder="Bengaluru, Hyderabad…" />
                </Field>
                <Field label="Work modes">
                  <ChipGroup ariaLabel="Work modes" value={draft.work_modes} onChange={(v) => set("work_modes", v)}
                    options={[{ value: "remote", label: "Remote" }, { value: "hybrid", label: "Hybrid" }, { value: "onsite", label: "On-site" }]} />
                </Field>
                <Field label="Employment types">
                  <ChipGroup ariaLabel="Employment types" value={draft.employment_types} onChange={(v) => set("employment_types", v)}
                    options={EMPLOYMENT_OPTIONS} />
                </Field>
                <Field label="Expected CTC (₹ LPA)" htmlFor="ctc" optional hint="Used only to answer application questions — your eligibility threshold lives in Settings.">
                  <Input id="ctc" type="number" min={0} className="max-w-40" value={draft.salary_expectation_lpa ?? ""}
                    onChange={(e) => set("salary_expectation_lpa", e.target.value === "" ? null : Number(e.target.value))} />
                </Field>
              </section>
            )}

            {step === 4 && full && (
              <section className="space-y-5">
                <div>
                  <h1 className="text-xl font-semibold tracking-[-0.015em]">Review what we found</h1>
                  <p className="mt-1 text-[13px] text-fg-muted">
                    Finishing marks these as verified. JobPilot will use them to match jobs and answer applications — so
                    remove anything that isn&apos;t accurate (you can edit details later in Settings → Profile).
                  </p>
                </div>
                {full.experiences.length === 0 && full.skills.length === 0 ? (
                  <Callout tone="info" icon={CircleAlert}>Nothing imported yet. Add experience and skills later from Settings → Profile.</Callout>
                ) : (
                  <>
                    <ReviewList title="Experience" items={full.experiences.map((e) => ({
                      id: e.id, primary: `${e.title ?? "Role"} · ${e.employer}`,
                      secondary: `${formatDate(e.start_date, { month: "short", year: "numeric" })} – ${e.is_current ? "Present" : formatDate(e.end_date, { month: "short", year: "numeric" })}`,
                      tag: e.category, path: `/profile/experiences/${e.id}`,
                    }))} onRemoved={() => refetch()} />
                    <ReviewList title="Education" items={full.education.map((e) => ({
                      id: e.id, primary: e.institution, secondary: [e.degree, e.grade].filter(Boolean).join(" · "), path: `/profile/education/${e.id}`,
                    }))} onRemoved={() => refetch()} />
                    <div>
                      <h2 className="mb-2 text-[13px] font-semibold">Skills <span className="font-normal text-fg-subtle">({full.skills.length})</span></h2>
                      <div className="flex flex-wrap gap-1.5">
                        {full.skills.map((s) => (
                          <span key={s.id} className="inline-flex h-6 items-center gap-1 rounded-sm bg-surface-3 pl-2 pr-1 text-xs">
                            {s.name}
                            <button aria-label={`Remove ${s.name}`} className="rounded-sm p-0.5 text-fg-subtle hover:bg-border hover:text-fg"
                              onClick={async () => { await api.del(`/profile/skills/${s.id}`); refetch(); }}>×</button>
                          </span>
                        ))}
                      </div>
                    </div>
                  </>
                )}
              </section>
            )}
          </motion.div>
        )}

        <div className="mt-10 flex items-center justify-between border-t border-border pt-5">
          <Button variant="ghost" onClick={() => setStep((s) => Math.max(0, s - 1))} disabled={step === 0}>
            <ArrowLeft /> Back
          </Button>
          <div className="flex items-center gap-3">
            {!canContinue && <span className="text-xs text-fg-muted">Add at least one target title</span>}
            <Button variant="primary" onClick={next} loading={saving} disabled={!canContinue || !draft}>
              {step === 0 && !imported ? "Skip for now" : step === STEPS.length - 1 ? <>Confirm & finish <Check /></> : <>Continue <ArrowRight /></>}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ReviewList({ title, items, onRemoved }: {
  title: string; items: { id: string; primary: string; secondary: string; tag?: string; path: string }[]; onRemoved: () => void;
}) {
  if (!items.length) return null;
  return (
    <div>
      <h2 className="mb-2 text-[13px] font-semibold">{title}</h2>
      <ul className="divide-y divide-border rounded-lg border border-border bg-surface">
        {items.map((i) => (
          <li key={i.id} className="flex items-center gap-3 px-3 py-2.5">
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13px] font-medium">{i.primary}</p>
              <p className="truncate text-xs text-fg-muted">{i.secondary}</p>
            </div>
            {i.tag && <Badge tone={i.tag === "internship" ? "info" : "neutral"}>{i.tag}</Badge>}
            <Button variant="ghost" size="sm" onClick={async () => { await api.del(i.path); onRemoved(); }}>Remove</Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
