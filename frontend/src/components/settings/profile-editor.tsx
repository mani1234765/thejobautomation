"use client";

import { Check, PenLine, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ChipGroup, Switch, TagInput } from "@/components/ui/controls";
import { Dialog, DialogContent, Tooltip } from "@/components/ui/overlay";
import { Badge, Card, CardHeader, Field, Input, NativeSelect, Progress, Skeleton, Textarea } from "@/components/ui/primitives";
import { Callout } from "@/components/ui/states";
import { api } from "@/lib/api";
import { EXPERIENCE_LEVELS, formatDate } from "@/lib/format";
import { keys, useAction, useProfile } from "@/lib/queries";
import type { Education, Experience, Profile, Project } from "@/lib/types";

const inv = [keys.profile, keys.dashboard, ["jobs"]];

function BasicsForm({ profile }: { profile: Profile }) {
  const [d, setD] = useState(profile);
  const set = <K extends keyof Profile>(k: K, v: Profile[K]) => setD({ ...d, [k]: v });
  const save = useAction(() => {
    const { verified_at: _v, ...body } = d;
    return api.put("/profile", { ...body, linkedin_url: body.linkedin_url || null, portfolio_url: body.portfolio_url || null });
  }, { invalidate: inv, success: "Profile saved" });
  const dirty = JSON.stringify(d) !== JSON.stringify(profile);
  return (
    <Card>
      <CardHeader title="Basics & preferences" description="Used for matching and to fill application forms."
        action={<Button size="sm" variant="primary" disabled={!dirty} loading={save.isPending} onClick={() => save.mutate(undefined)}>Save</Button>} />
      <div className="grid gap-4 px-4 pb-4 sm:grid-cols-2">
        <Field label="Full name"><Input value={d.full_name ?? ""} onChange={(e) => set("full_name", e.target.value)} /></Field>
        <Field label="Headline" optional><Input value={d.headline ?? ""} onChange={(e) => set("headline", e.target.value)} placeholder="Backend engineer · Python, AWS" /></Field>
        <Field label="Phone"><Input value={d.phone ?? ""} onChange={(e) => set("phone", e.target.value)} /></Field>
        <Field label="Location"><Input value={d.location ?? ""} onChange={(e) => set("location", e.target.value)} /></Field>
        <Field label="Work authorization"><Input value={d.work_authorization ?? ""} onChange={(e) => set("work_authorization", e.target.value)} /></Field>
        <Field label="Notice period" optional><Input value={d.notice_period ?? ""} onChange={(e) => set("notice_period", e.target.value)} /></Field>
        <Field label="LinkedIn URL" optional><Input type="url" value={d.linkedin_url ?? ""} onChange={(e) => set("linkedin_url", e.target.value)} /></Field>
        <Field label="Portfolio / GitHub" optional><Input type="url" value={d.portfolio_url ?? ""} onChange={(e) => set("portfolio_url", e.target.value)} /></Field>
        <Field label="Years of professional experience"><Input type="number" min={0} step={0.5} value={d.years_experience ?? ""} onChange={(e) => set("years_experience", e.target.value === "" ? null : Number(e.target.value))} /></Field>
        <Field label="Expected CTC (₹ LPA)" optional><Input type="number" min={0} value={d.salary_expectation_lpa ?? ""} onChange={(e) => set("salary_expectation_lpa", e.target.value === "" ? null : Number(e.target.value))} /></Field>
        <Field label="Target job titles" className="sm:col-span-2"><TagInput value={d.target_titles} onChange={(v) => set("target_titles", v)} /></Field>
        <Field label="Preferred locations" className="sm:col-span-2"><TagInput value={d.preferred_locations} onChange={(v) => set("preferred_locations", v)} /></Field>
        <Field label="Work modes"><ChipGroup ariaLabel="Work modes" value={d.work_modes} onChange={(v) => set("work_modes", v)} options={[{ value: "remote", label: "Remote" }, { value: "hybrid", label: "Hybrid" }, { value: "onsite", label: "On-site" }]} /></Field>
        <Field label="Employment types"><ChipGroup ariaLabel="Employment types" value={d.employment_types} onChange={(v) => set("employment_types", v)} options={[{ value: "full_time", label: "Full-time" }, { value: "internship", label: "Internship" }, { value: "contract", label: "Contract" }, { value: "part_time", label: "Part-time" }]} /></Field>
        <Field label="Experience levels" className="sm:col-span-2"><ChipGroup ariaLabel="Experience levels" value={d.experience_levels} onChange={(v) => set("experience_levels", v)} options={EXPERIENCE_LEVELS} /></Field>
        <Field label="Summary" className="sm:col-span-2" optional><Textarea rows={3} value={d.summary ?? ""} onChange={(e) => set("summary", e.target.value)} /></Field>
        <Field label="Career objectives" className="sm:col-span-2" optional><Textarea rows={2} value={d.career_objectives ?? ""} onChange={(e) => set("career_objectives", e.target.value)} /></Field>
      </div>
    </Card>
  );
}

type ExpDraft = Omit<Experience, "id" | "resume_id"> & { id?: string };
const emptyExp: ExpDraft = { category: "professional", employer: "", title: "", location: "", start_date: null, end_date: null, is_current: false, bullets: [], verified: true };

function ExperienceDialog({ draft, onClose }: { draft: ExpDraft; onClose: () => void }) {
  const [d, setD] = useState({ ...draft, bulletsText: draft.bullets.join("\n") });
  const save = useAction(() => {
    const body = { ...d, bullets: d.bulletsText.split("\n").map((b) => b.trim()).filter(Boolean), verified: true, title: d.title || null, location: d.location || null };
    return d.id ? api.put(`/profile/experiences/${d.id}`, body) : api.post("/profile/experiences", body);
  }, { invalidate: inv, success: "Experience saved", onSuccess: onClose });
  return (
    <DialogContent wide title={d.id ? "Edit experience" : "Add experience"} footer={<Button variant="primary" disabled={!d.employer.trim()} loading={save.isPending} onClick={() => save.mutate(undefined)}>Save</Button>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Type"><NativeSelect value={d.category} onChange={(e) => setD({ ...d, category: e.target.value as ExpDraft["category"] })}><option value="professional">Professional</option><option value="internship">Internship</option><option value="freelance">Freelance</option></NativeSelect></Field>
        <Field label="Employer"><Input value={d.employer} onChange={(e) => setD({ ...d, employer: e.target.value })} /></Field>
        <Field label="Title"><Input value={d.title ?? ""} onChange={(e) => setD({ ...d, title: e.target.value })} /></Field>
        <Field label="Location" optional><Input value={d.location ?? ""} onChange={(e) => setD({ ...d, location: e.target.value })} /></Field>
        <Field label="Start"><Input type="month" value={d.start_date?.slice(0, 7) ?? ""} onChange={(e) => setD({ ...d, start_date: e.target.value ? `${e.target.value}-01` : null })} /></Field>
        <Field label="End"><Input type="month" disabled={d.is_current} value={d.end_date?.slice(0, 7) ?? ""} onChange={(e) => setD({ ...d, end_date: e.target.value ? `${e.target.value}-01` : null })} /></Field>
        <label className="flex items-center gap-2 text-[13px] sm:col-span-2"><Switch checked={d.is_current} onCheckedChange={(v) => setD({ ...d, is_current: v, end_date: v ? null : d.end_date })} /> I currently work here</label>
        <Field label="Achievements (one per line)" className="sm:col-span-2"><Textarea rows={5} value={d.bulletsText} onChange={(e) => setD({ ...d, bulletsText: e.target.value })} /></Field>
      </div>
    </DialogContent>
  );
}

function SimpleItemDialog({ kind, onClose }: { kind: "projects" | "education" | "certifications"; onClose: () => void }) {
  const [v, setV] = useState<Record<string, string>>({ category: "personal" });
  const save = useAction(() => {
    const body =
      kind === "projects" ? { category: v.category, name: v.name, description: v.description || null, technologies: (v.technologies ?? "").split(",").map((t) => t.trim()).filter(Boolean), url: v.url || null }
      : kind === "education" ? { institution: v.institution, degree: v.degree || null, field: v.field || null, grade: v.grade || null }
      : { name: v.name, issuer: v.issuer || null };
    return api.post(`/profile/${kind}`, body);
  }, { invalidate: inv, success: "Added", onSuccess: onClose });
  const F = (k: string, label: string, optional = false) => <Field label={label} optional={optional}><Input value={v[k] ?? ""} onChange={(e) => setV({ ...v, [k]: e.target.value })} /></Field>;
  return (
    <DialogContent title={`Add ${kind === "education" ? "education" : kind.slice(0, -1)}`} footer={<Button variant="primary" loading={save.isPending} onClick={() => save.mutate(undefined)}>Add</Button>}>
      <div className="grid gap-3">
        {kind === "projects" && <>
          <Field label="Type"><NativeSelect value={v.category} onChange={(e) => setV({ ...v, category: e.target.value })}><option value="personal">Personal</option><option value="academic">Academic</option><option value="professional">Professional</option></NativeSelect></Field>
          {F("name", "Name")}{F("technologies", "Technologies (comma-separated)", true)}{F("url", "Link", true)}
          <Field label="Description" optional><Textarea rows={3} value={v.description ?? ""} onChange={(e) => setV({ ...v, description: e.target.value })} /></Field>
        </>}
        {kind === "education" && <>{F("institution", "Institution")}{F("degree", "Degree", true)}{F("field", "Field of study", true)}{F("grade", "Grade / CGPA", true)}</>}
        {kind === "certifications" && <>{F("name", "Certification")}{F("issuer", "Issuer", true)}</>}
      </div>
    </DialogContent>
  );
}

export function ProfileEditor() {
  const { data, isPending } = useProfile();
  const [exp, setExp] = useState<ExpDraft | null>(null);
  const [simple, setSimple] = useState<"projects" | "education" | "certifications" | null>(null);
  const [skill, setSkill] = useState("");
  const del = useAction((path: string) => api.del(path), { invalidate: inv });
  const addSkill = useAction(() => api.post("/profile/skills", { name: skill, verified: true }), { invalidate: inv, onSuccess: () => setSkill("") });
  const verifySkill = useAction((s: { id: string; name: string }) => api.patch(`/profile/skills/${s.id}`, { name: s.name, verified: true }), { invalidate: inv });
  const verifyAll = useAction(() => api.post("/profile/verify"), { invalidate: inv, success: "Everything marked as verified" });

  if (isPending || !data) return <div className="space-y-4"><Skeleton className="h-64" /><Skeleton className="h-40" /></div>;
  const unverified = [...data.experiences, ...data.projects, ...data.education, ...data.certifications, ...data.skills].filter((x) => !x.verified).length;
  const VerifiedBadge = ({ ok }: { ok: boolean }) => ok ? null : <Tooltip content="Imported from your resume — confirm it's accurate"><span><Badge tone="warning">Unverified</Badge></span></Tooltip>;
  return (
    <div className="flex flex-col gap-5">
      <Card className="flex flex-wrap items-center gap-4 p-4">
        <div className="w-40"><p className="text-xs text-fg-muted">Profile completeness</p><p className="tabular text-lg font-semibold">{data.completeness.percent}%</p><Progress value={data.completeness.percent} label="Profile completeness" /></div>
        <div className="flex flex-1 flex-wrap gap-1.5">
          {Object.entries(data.completeness.checks).filter(([, ok]) => !ok).map(([k]) => <Badge key={k} tone="warning">{k.replace(/_/g, " ")}</Badge>)}
        </div>
        {unverified > 0 && <Button size="sm" onClick={() => verifyAll.mutate(undefined)} loading={verifyAll.isPending}><Check /> Verify {unverified} imported items</Button>}
      </Card>
      <BasicsForm key={JSON.stringify(data.profile)} profile={data.profile} />

      <Card>
        <CardHeader title="Experience" description="Professional, internship and freelance experience are kept separate."
          action={<Button size="sm" onClick={() => setExp(emptyExp)}><Plus /> Add</Button>} />
        {!data.experiences.length ? <p className="px-4 pb-4 text-[13px] text-fg-muted">No experience added.</p> : (
          <ul className="divide-y divide-border border-t border-border">
            {data.experiences.map((e) => (
              <li key={e.id} className="flex items-center gap-3 px-4 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="flex items-center gap-2 truncate text-[13px] font-medium">{e.title ?? "Role"} · {e.employer} <VerifiedBadge ok={e.verified} /></p>
                  <p className="text-xs text-fg-muted">{e.category} · {formatDate(e.start_date, { month: "short", year: "numeric" })} – {e.is_current ? "Present" : formatDate(e.end_date, { month: "short", year: "numeric" })}</p>
                </div>
                <Button variant="ghost" size="icon-sm" aria-label="Edit" onClick={() => setExp({ ...e, title: e.title ?? "", location: e.location ?? "" })}><PenLine /></Button>
                <Button variant="ghost" size="icon-sm" aria-label="Delete" onClick={() => del.mutate(`/profile/experiences/${e.id}`)}><Trash2 /></Button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {([["projects", "Projects", data.projects.map((p: Project) => ({ id: p.id, title: p.name, sub: `${p.category} · ${p.technologies.join(", ")}`, ok: p.verified }))],
        ["education", "Education", data.education.map((e: Education) => ({ id: e.id, title: e.institution, sub: [e.degree, e.field, e.grade].filter(Boolean).join(" · "), ok: e.verified }))],
        ["certifications", "Certifications", data.certifications.map((c) => ({ id: c.id, title: c.name, sub: c.issuer ?? "", ok: c.verified }))]] as const).map(([kind, title, items]) => (
        <Card key={kind}>
          <CardHeader title={title} action={<Button size="sm" onClick={() => setSimple(kind)}><Plus /> Add</Button>} />
          {!items.length ? <p className="px-4 pb-4 text-[13px] text-fg-muted">None added.</p> : (
            <ul className="divide-y divide-border border-t border-border">
              {items.map((i) => (
                <li key={i.id} className="flex items-center gap-3 px-4 py-2.5">
                  <div className="min-w-0 flex-1"><p className="flex items-center gap-2 truncate text-[13px] font-medium">{i.title} <VerifiedBadge ok={i.ok} /></p><p className="truncate text-xs text-fg-muted">{i.sub}</p></div>
                  <Button variant="ghost" size="icon-sm" aria-label="Delete" onClick={() => del.mutate(`/profile/${kind}/${i.id}`)}><Trash2 /></Button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      ))}

      <Card>
        <CardHeader title="Skills" description="Verified skills count fully in matching; unverified ones are flagged in match explanations." />
        <div className="px-4 pb-4">
          <div className="flex flex-wrap gap-1.5">
            {data.skills.map((s) => (
              <span key={s.id} className="inline-flex h-6 items-center gap-1 rounded-sm bg-surface-3 pl-2 pr-1 text-xs">
                {s.name}
                {!s.verified && <button className="rounded-sm px-1 text-warning hover:bg-border" onClick={() => verifySkill.mutate(s)} aria-label={`Verify ${s.name}`}>verify</button>}
                <button className="rounded-sm p-0.5 text-fg-subtle hover:bg-border hover:text-fg" aria-label={`Remove ${s.name}`} onClick={() => del.mutate(`/profile/skills/${s.id}`)}>×</button>
              </span>
            ))}
          </div>
          <form className="mt-3 flex max-w-sm gap-2" onSubmit={(e) => { e.preventDefault(); if (skill.trim()) addSkill.mutate(undefined); }}>
            <Input value={skill} onChange={(e) => setSkill(e.target.value)} placeholder="Add a skill" aria-label="New skill" />
            <Button type="submit" loading={addSkill.isPending}>Add</Button>
          </form>
          <Callout tone="neutral" className="mt-3">Only add skills you can talk about in an interview — JobPilot treats them as facts.</Callout>
        </div>
      </Card>
      <Dialog open={!!exp} onOpenChange={(o) => !o && setExp(null)}>{exp && <ExperienceDialog draft={exp} onClose={() => setExp(null)} />}</Dialog>
      <Dialog open={!!simple} onOpenChange={(o) => !o && setSimple(null)}>{simple && <SimpleItemDialog kind={simple} onClose={() => setSimple(null)} />}</Dialog>
    </div>
  );
}
