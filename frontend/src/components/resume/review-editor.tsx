"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, ArrowRightLeft, Check, CheckCheck, ListChecks, Merge, MoreHorizontal, PenLine, Plus, RefreshCw, Scissors, Trash2, X,
} from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Checkbox, Switch, TagInput } from "@/components/ui/controls";
import { ConfirmDialog, Dialog, DialogContent, Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger, Tooltip } from "@/components/ui/overlay";
import { Badge, Card, CardHeader, Field, Input, NativeSelect, Skeleton, Textarea } from "@/components/ui/primitives";
import { Callout, EmptyState, ErrorState } from "@/components/ui/states";
import { api } from "@/lib/api";
import { cn, formatDate } from "@/lib/format";
import { keys, useAction, useResumeReview } from "@/lib/queries";
import type { ReextractPreview, ResumeReview, ReviewEntry, ReviewKind, ReviewTarget } from "@/lib/types";

const KIND_TITLE: Record<ReviewKind, string> = {
  experience: "Experience", projects: "Projects", education: "Education", certifications: "Certifications",
};
const KIND_ONE: Record<ReviewKind, string> = {
  experience: "experience", projects: "project", education: "education entry", certifications: "certification",
};
const KINDS: ReviewKind[] = ["experience", "projects", "education", "certifications"];

/** "Move to" choices: a section, optionally with the category inside it. */
const MOVE_OPTIONS: { value: string; to: ReviewTarget; category?: string; label: string }[] = [
  { value: "experience:professional", to: "experience", category: "professional", label: "Experience — professional" },
  { value: "experience:internship", to: "experience", category: "internship", label: "Experience — internship" },
  { value: "experience:freelance", to: "experience", category: "freelance", label: "Experience — freelance" },
  { value: "projects:personal", to: "projects", category: "personal", label: "Projects — personal" },
  { value: "projects:academic", to: "projects", category: "academic", label: "Projects — academic" },
  { value: "projects:professional", to: "projects", category: "professional", label: "Projects — professional" },
  { value: "education", to: "education", label: "Education" },
  { value: "certifications", to: "certifications", label: "Certifications" },
  { value: "summary", to: "summary", label: "Professional summary (adds the text to it)" },
  { value: "skills", to: "skills", label: "Skills (keeps only recognised skills)" },
];

const monthRange = (e: { start_date?: string | null; end_date?: string | null; is_current?: boolean }) => {
  if (!e.start_date && !e.end_date && !e.is_current) return null;
  const f = (d?: string | null) => (d ? formatDate(d, { month: "short", year: "numeric" }) : "?");
  return `${f(e.start_date)} – ${e.is_current ? "Present" : f(e.end_date)}`;
};

function bodyLines(kind: ReviewKind, e: ReviewEntry): string[] {
  if (kind === "experience") return (e as ResumeReview["entries"]["experience"][number]).bullets;
  if (kind === "projects") return ((e as ResumeReview["entries"]["projects"][number]).description ?? "").split("\n").filter((l) => l.trim());
  return (e as { details?: string[] }).details ?? [];
}

function meta(kind: ReviewKind, e: ReviewEntry): string {
  const x = e as unknown as Record<string, unknown>;
  const parts: (string | null | undefined)[] =
    kind === "experience" ? [String(x.category), x.location as string, monthRange(x)]
    : kind === "projects" ? [String(x.category), ((x.technologies as string[]) ?? []).slice(0, 6).join(", ")]
    : kind === "education" ? [x.field as string, x.grade as string, monthRange(x)]
    : [x.issuer as string];
  const s = parts.filter(Boolean).join(" · ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Mutations return the whole, freshly-saved review; only then does the UI change. */
function useReviewAction<V>(resumeId: string, fn: (v: V) => Promise<ResumeReview>, success?: string, onSuccess?: () => void) {
  const qc = useQueryClient();
  return useAction(fn, {
    invalidate: [keys.profile, keys.dashboard, ["jobs"]],
    success,
    onSuccess: (r) => { qc.setQueryData(keys.review(resumeId), r); onSuccess?.(); },
  });
}

// ── Entry form ─────────────────────────────────────────────────────────────────────────────────
type Draft = Record<string, unknown> & { id?: string };

const EMPTY: Record<ReviewKind, Draft> = {
  experience: { category: "professional", employer: "", title: "", location: "", start_date: null, end_date: null, is_current: false, bullets: [] },
  projects: { category: "personal", name: "", description: "", technologies: [], url: "" },
  education: { institution: "", degree: "", field: "", start_date: null, end_date: null, grade: "", details: [] },
  certifications: { name: "", issuer: "", issued_on: null, details: [] },
};
const FIELDS: Record<ReviewKind, string[]> = {
  experience: ["category", "employer", "title", "location", "start_date", "end_date", "is_current", "bullets", "verified"],
  projects: ["category", "name", "description", "technologies", "url", "verified"],
  education: ["institution", "degree", "field", "start_date", "end_date", "grade", "details", "verified"],
  certifications: ["name", "issuer", "issued_on", "details", "verified"],
};
const REQUIRED: Record<ReviewKind, string> = { experience: "employer", projects: "name", education: "institution", certifications: "name" };
const LIST_FIELD: Partial<Record<ReviewKind, string>> = { experience: "bullets", education: "details", certifications: "details" };

function toBody(kind: ReviewKind, d: Draft): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  for (const k of FIELDS[kind]) {
    const v = d[k];
    body[k] = typeof v === "string" && k !== REQUIRED[kind] && k !== "category" ? (v.trim() || null) : v;
  }
  const list = LIST_FIELD[kind];
  if (list) body[list] = String(d[`${list}Text`] ?? "").split("\n").map((l) => l.trim()).filter(Boolean);
  body.verified = true;
  return body;
}

function EntryDialog({ resumeId, kind, draft, onClose }: { resumeId: string; kind: ReviewKind; draft: Draft; onClose: () => void }) {
  const list = LIST_FIELD[kind];
  const [d, setD] = useState<Draft>(() => ({ ...draft, ...(list ? { [`${list}Text`]: ((draft[list] as string[]) ?? []).join("\n") } : {}) }));
  const set = (k: string, v: unknown) => setD((x) => ({ ...x, [k]: v }));
  const save = useReviewAction(resumeId, 
    () => d.id
      ? api.put<ResumeReview>(`/resumes/${resumeId}/review/entries/${kind}/${d.id}`, toBody(kind, d))
      : api.post<ResumeReview>(`/resumes/${resumeId}/review/entries`, { kind, data: toBody(kind, d) }),
    d.id ? "Correction saved" : "Entry added", onClose,
  );
  const start = d.start_date as string | null, end = d.end_date as string | null;
  const dateError = start && end && !d.is_current && end < start ? "The end date is before the start date." : undefined;
  const missing = !String(d[REQUIRED[kind]] ?? "").trim();
  const text = (k: string, label: string, optional = false) => (
    <Field label={label} optional={optional}><Input value={(d[k] as string) ?? ""} onChange={(e) => set(k, e.target.value)} /></Field>
  );
  const month = (k: string, label: string, disabled = false, error?: string) => (
    <Field label={label} optional error={error}>
      <Input type="month" disabled={disabled} value={((d[k] as string) ?? "").slice(0, 7)} onChange={(e) => set(k, e.target.value ? `${e.target.value}-01` : null)} />
    </Field>
  );
  return (
    <DialogContent wide title={`${d.id ? "Edit" : "Add"} ${KIND_ONE[kind]}`}
      description={d.id ? "Your correction is kept even if the resume is re-extracted." : undefined}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={missing || !!dateError} loading={save.isPending} onClick={() => save.mutate(undefined)}>Save</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        {kind === "experience" && <>
          <Field label="Type"><NativeSelect value={d.category as string} onChange={(e) => set("category", e.target.value)}><option value="professional">Professional</option><option value="internship">Internship</option><option value="freelance">Freelance</option></NativeSelect></Field>
          {text("employer", "Employer")}{text("title", "Job title", true)}{text("location", "Location", true)}
          {month("start_date", "Start")}{month("end_date", "End", !!d.is_current, dateError)}
          <label className="flex items-center gap-2 text-[13px] sm:col-span-2"><Switch checked={!!d.is_current} onCheckedChange={(v) => setD((x) => ({ ...x, is_current: v, end_date: v ? null : x.end_date }))} /> I currently work here</label>
        </>}
        {kind === "projects" && <>
          <Field label="Type"><NativeSelect value={d.category as string} onChange={(e) => set("category", e.target.value)}><option value="personal">Personal</option><option value="academic">Academic</option><option value="professional">Professional</option></NativeSelect></Field>
          {text("name", "Project name")}{text("url", "Link", true)}
          <Field label="Technologies" optional><TagInput max={40} value={(d.technologies as string[]) ?? []} onChange={(v) => set("technologies", v)} /></Field>
          <Field label="Description" optional className="sm:col-span-2"><Textarea rows={5} value={(d.description as string) ?? ""} onChange={(e) => set("description", e.target.value)} /></Field>
        </>}
        {kind === "education" && <>
          {text("institution", "Institution")}{text("degree", "Degree / qualification", true)}{text("field", "Field of study", true)}{text("grade", "Grade / CGPA", true)}
          {month("start_date", "Start")}{month("end_date", "Graduation", false, dateError)}
        </>}
        {kind === "certifications" && <>
          {text("name", "Certification")}{text("issuer", "Issuer", true)}
          <Field label="Issued" optional><Input type="month" value={((d.issued_on as string) ?? "").slice(0, 7)} onChange={(e) => set("issued_on", e.target.value ? `${e.target.value}-01` : null)} /></Field>
        </>}
        {list && (
          <Field label={kind === "experience" ? "Achievements (one per line)" : "Details (one per line)"} optional className="sm:col-span-2">
            <Textarea rows={kind === "experience" ? 6 : 3} value={(d[`${list}Text`] as string) ?? ""} onChange={(e) => set(`${list}Text`, e.target.value)} />
          </Field>
        )}
      </div>
    </DialogContent>
  );
}

// ── Move & split ───────────────────────────────────────────────────────────────────────────────
function MoveDialog({ resumeId, kind, entry, onClose }: { resumeId: string; kind: ReviewKind; entry: ReviewEntry; onClose: () => void }) {
  const current = "category" in entry ? `${kind}:${(entry as { category: string }).category}` : kind;
  const options = MOVE_OPTIONS.filter((o) => o.value !== current);
  const [value, setValue] = useState(options[0].value);
  const opt = MOVE_OPTIONS.find((o) => o.value === value)!;
  const move = useReviewAction(resumeId, 
    () => api.post<ResumeReview>(`/resumes/${resumeId}/review/entries/${kind}/${entry.id}/move`, { to: opt.to, category: opt.category ?? null }),
    `Moved to ${opt.label.split(" (")[0]}`, onClose,
  );
  return (
    <DialogContent title="Move to another section" description={entry.label}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={move.isPending} onClick={() => move.mutate(undefined)}><ArrowRightLeft /> Move</Button></>}>
      <Field label="Section" hint={entry.user_edited
        ? "Your edited text is carried over field by field."
        : "The original lines are re-read as the new section, e.g. a degree and grade are picked out for Education."}>
        <NativeSelect value={value} onChange={(e) => setValue(e.target.value)}>
          {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </NativeSelect>
      </Field>
      {opt.to === "skills" && <Callout tone="warning" className="mt-3">Only recognised skills are kept; the rest of this entry&apos;s text is removed.</Callout>}
    </DialogContent>
  );
}

function SplitDialog({ resumeId, kind, entry, onClose }: { resumeId: string; kind: ReviewKind; entry: ReviewEntry; onClose: () => void }) {
  const lines = bodyLines(kind, entry);
  const [at, setAt] = useState<number | null>(null);
  const split = useReviewAction(resumeId, 
    () => api.post<ResumeReview>(`/resumes/${resumeId}/review/entries/${kind}/${entry.id}/split`, { at }), "Split into two entries", onClose,
  );
  return (
    <DialogContent wide title="Split this entry" description="Pick the line where the second entry starts. That line becomes its heading (for example a role, dates or a project name)."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={at === null} loading={split.isPending} onClick={() => split.mutate(undefined)}><Scissors /> Split</Button></>}>
      <fieldset className="flex flex-col gap-1">
        <legend className="sr-only">Line where the new entry starts</legend>
        {lines.map((l, i) => (
          <label key={i} className={cn("flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 text-[13px]", at === i ? "bg-accent-soft" : "hover:bg-surface-3", at !== null && i >= at && "border-l-2 border-accent")}>
            <input type="radio" name="split-at" className="mt-1" checked={at === i} onChange={() => setAt(i)} />
            <span>{l}</span>
          </label>
        ))}
      </fieldset>
    </DialogContent>
  );
}

// ── Re-extraction ──────────────────────────────────────────────────────────────────────────────
function Row({ title, items, tone = "neutral" }: { title: string; items: string[]; tone?: "neutral" | "accent" | "warning" | "positive" }) {
  if (!items.length) return null;
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-fg-muted">{title} <span className="tabular text-fg-subtle">({items.length})</span></p>
      <div className="flex flex-wrap gap-1">{items.map((t, i) => <Badge key={i} tone={tone}>{t}</Badge>)}</div>
    </div>
  );
}

function ReextractDialog({ resumeId, onClose }: { resumeId: string; onClose: () => void }) {
  // A fresh preview every time the dialog opens; nothing is saved by fetching it.
  const { data: preview, error } = useQuery({
    queryKey: ["reextract-preview", resumeId],
    queryFn: () => api.get<ReextractPreview>(`/resumes/${resumeId}/reextract`),
    gcTime: 0,
    staleTime: 0,
  });
  const [removeStale, setRemoveStale] = useState(false);
  const [acceptSummary, setAcceptSummary] = useState(false);
  const apply = useReviewAction(resumeId, 
    () => api.post<ResumeReview & { changes: Record<string, number> }>(`/resumes/${resumeId}/reextract`, { remove_stale: removeStale, accept_summary: acceptSummary }),
    "Re-extracted. Your corrections were kept.", onClose,
  );
  return (
    <DialogContent wide title="Re-extract this resume" description="Reads the original file again with the latest parser. Nothing changes until you apply."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!preview} loading={apply.isPending} onClick={() => apply.mutate(undefined)}><RefreshCw /> Apply</Button></>}>
      {error ? <ErrorState error={error} /> : !preview ? <Skeleton className="h-40" /> : (
        <div className="flex flex-col gap-4">
          <Callout tone="positive" icon={Check} title="Your corrections are protected">
            {preview.kept.length} edited {preview.kept.length === 1 ? "entry is" : "entries are"} kept exactly as you left {preview.kept.length === 1 ? "it" : "them"}
            {preview.dismissed.length ? `, and ${preview.dismissed.length} you deleted stay deleted` : ""}.
          </Callout>
          <Row title="New entries (added for review)" tone="accent" items={preview.add.map((a) => `${KIND_TITLE[a.kind]}: ${a.label}`)} />
          <Row title="Unedited entries that will be refreshed" items={preview.update.map((u) => `${KIND_TITLE[u.kind]}: ${u.label}`)} />
          <Row title="Kept as you corrected them" tone="positive" items={preview.kept.filter((k) => k.differs).map((k) => `${KIND_TITLE[k.current_kind]}: ${k.current_label}`)} />
          <Row title="New skills" tone="accent" items={preview.skills} />
          {preview.stale.length > 0 && (
            <div className="flex flex-col gap-2">
              <Row title="No longer found in the resume" tone="warning" items={preview.stale.map((s) => `${KIND_TITLE[s.kind]}: ${s.label}`)} />
              <label className="flex items-center gap-2 text-[13px]"><Switch checked={removeStale} onCheckedChange={setRemoveStale} /> Remove these (otherwise they&apos;re kept and flagged for review)</label>
            </div>
          )}
          {preview.summary?.action === "fill" && <p className="text-[13px] text-fg-muted">Your summary is empty; the extracted summary will be added.</p>}
          {preview.summary?.action === "differs" && (
            <div className="flex flex-col gap-2">
              <p className="text-xs font-medium text-fg-muted">The extracted summary differs from yours</p>
              <p className="rounded-md bg-surface-3 p-2 text-[13px]">{preview.summary.extracted}</p>
              <label className="flex items-center gap-2 text-[13px]"><Switch checked={acceptSummary} onCheckedChange={setAcceptSummary} /> Replace my summary with this</label>
            </div>
          )}
          {!preview.add.length && !preview.update.length && !preview.stale.length && !preview.skills.length && !preview.summary && (
            <p className="text-[13px] text-fg-muted">Nothing new was found. {preview.unchanged} {preview.unchanged === 1 ? "entry is" : "entries are"} unchanged.</p>
          )}
        </div>
      )}
    </DialogContent>
  );
}

// ── Sections ───────────────────────────────────────────────────────────────────────────────────
function Confidence({ e, threshold }: { e: ReviewEntry; threshold: number }) {
  if (e.user_edited) return <Badge tone="positive"><Check className="size-3" /> Corrected</Badge>;
  if (e.needs_review) {
    const pct = e.confidence != null ? `${Math.round(e.confidence * 100)}%` : "";
    return <Badge tone="warning"><AlertTriangle className="size-3" /> Check · {pct}</Badge>;
  }
  if (e.confidence != null && e.confidence < 0.9 && e.confidence >= threshold)
    return <Tooltip content="Classification confidence"><span><Badge>{Math.round(e.confidence * 100)}%</Badge></span></Tooltip>;
  return null;
}

type Dialogs =
  | { type: "edit"; kind: ReviewKind; draft: Draft }
  | { type: "move" | "split" | "delete"; kind: ReviewKind; entry: ReviewEntry }
  | { type: "reextract" | "confirm" }
  | null;

function SectionCard({
  review, kind, onlyFlagged, open,
}: { review: ResumeReview; kind: ReviewKind; onlyFlagged: boolean; open: (d: Dialogs) => void }) {
  const [selected, setSelected] = useState<string[]>([]);
  const all = review.entries[kind] as ReviewEntry[];
  const items = onlyFlagged ? all.filter((e) => e.needs_review) : all;
  const merge = useReviewAction(review.resume_id, 
    () => api.post<ResumeReview>(`/resumes/${review.resume_id}/review/merge`, { kind, ids: selected }), "Entries merged", () => setSelected([]),
  );
  if (onlyFlagged && !items.length) return null;
  return (
    <Card>
      <CardHeader title={KIND_TITLE[kind]} description={`${all.length} ${all.length === 1 ? "entry" : "entries"}`}
        action={<div className="flex gap-2">
          {selected.length >= 2 && <Button size="sm" loading={merge.isPending} onClick={() => merge.mutate(undefined)}><Merge /> Merge {selected.length}</Button>}
          <Button size="sm" onClick={() => open({ type: "edit", kind, draft: { ...EMPTY[kind] } })}><Plus /> Add</Button>
        </div>} />
      {!items.length ? <p className="px-4 pb-4 text-[13px] text-fg-muted">Nothing here. Add an entry, or move one in from another section.</p> : (
        <ul className="divide-y divide-border border-t border-border">
          {items.map((e) => {
            const lines = bodyLines(kind, e);
            return (
              <li key={e.id} className={cn("flex gap-3 px-4 py-3", e.needs_review && "bg-warning-soft/30")} data-testid={`review-${kind}`}>
                <Checkbox className="mt-0.5" aria-label={`Select ${e.label} to merge`} checked={selected.includes(e.id)}
                  onCheckedChange={(v) => setSelected((s) => v ? [...s, e.id] : s.filter((x) => x !== e.id))} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-[13px] font-medium">{e.label}</p>
                    <Confidence e={e} threshold={review.review_threshold} />
                  </div>
                  {meta(kind, e) && <p className="text-xs text-fg-muted">{meta(kind, e)}</p>}
                  {!!e.review_reasons?.length && (
                    <ul className="mt-1 text-xs text-warning">{e.review_reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
                  )}
                  {!!lines.length && <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-[13px] text-fg-muted">{lines.slice(0, 6).map((l, i) => <li key={i}>{l}</li>)}{lines.length > 6 && <li className="list-none text-fg-subtle">+{lines.length - 6} more</li>}</ul>}
                  {!!e.issues.length && <p className="mt-1 text-xs text-fg-subtle">{e.issues.join(" ")}</p>}
                </div>
                <div className="flex shrink-0 items-start gap-1">
                  <Button variant="ghost" size="icon-sm" aria-label={`Edit ${e.label}`} onClick={() => open({ type: "edit", kind, draft: { ...(e as unknown as Draft) } })}><PenLine /></Button>
                  <Menu>
                    <MenuTrigger asChild><Button variant="ghost" size="icon-sm" aria-label={`More actions for ${e.label}`}><MoreHorizontal /></Button></MenuTrigger>
                    <MenuContent>
                      <MenuItem onSelect={() => open({ type: "move", kind, entry: e })}><ArrowRightLeft /> Move to…</MenuItem>
                      <MenuItem disabled={lines.length < 1} onSelect={() => open({ type: "split", kind, entry: e })}><Scissors /> Split…</MenuItem>
                      <MenuSeparator />
                      <MenuItem destructive onSelect={() => open({ type: "delete", kind, entry: e })}><Trash2 /> Delete</MenuItem>
                    </MenuContent>
                  </Menu>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function SummaryCard({ review }: { review: ResumeReview }) {
  const saved = review.summary.text ?? "";
  const [text, setText] = useState(saved); // remounted (keyed) whenever the saved text changes
  const save = useReviewAction(review.resume_id, 
    () => api.put<ResumeReview>(`/resumes/${review.resume_id}/review/summary`, { text }), "Summary saved",
  );
  const dirty = text !== saved;
  return (
    <Card>
      <CardHeader title="Professional summary" description="Used for application answers and as context when tailoring."
        action={<Button size="sm" variant="primary" disabled={!dirty} loading={save.isPending} onClick={() => save.mutate(undefined)}>Save</Button>} />
      <div className="flex flex-col gap-2 px-4 pb-4">
        {review.summary.needs_review && (
          <Callout tone="warning" title="Check this summary">{review.summary.review_reasons.join(" ") || "It was found with low confidence."}</Callout>
        )}
        <Textarea aria-label="Professional summary" rows={4} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)} placeholder="No summary was found. Write two or three lines about your focus and strengths." />
        {dirty && <p className="text-xs text-fg-muted">Unsaved changes</p>}
      </div>
    </Card>
  );
}

function SkillsCard({ review }: { review: ResumeReview }) {
  const [name, setName] = useState("");
  const rid = review.resume_id;
  const add = useReviewAction(rid, () => api.post<ResumeReview>(`/resumes/${rid}/review/skills`, { name }), undefined, () => setName(""));
  const remove = useReviewAction(rid, (id: string) => api.del<ResumeReview>(`/resumes/${rid}/review/skills/${id}`), "Skill removed");
  return (
    <Card>
      <CardHeader title="Skills" description="Removed skills stay removed when the resume is re-extracted." />
      <div className="px-4 pb-4">
        <div className="flex flex-wrap gap-1.5">
          {review.skills.map((s) => (
            <span key={s.id} className="inline-flex h-6 items-center gap-1 rounded-sm bg-surface-3 pl-2 pr-1 text-xs">
              {s.name}{!s.verified && <span className="text-warning" title="Imported, not yet confirmed">•</span>}
              <button className="rounded-sm p-0.5 text-fg-subtle hover:bg-border hover:text-fg disabled:opacity-50" disabled={remove.isPending}
                aria-label={`Remove ${s.name}`} onClick={() => remove.mutate(s.id)}><X className="size-3" /></button>
            </span>
          ))}
          {!review.skills.length && <p className="text-[13px] text-fg-muted">No skills yet.</p>}
        </div>
        <form className="mt-3 flex max-w-sm gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim()) add.mutate(undefined); }}>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Add a skill" aria-label="New skill" maxLength={120} />
          <Button type="submit" loading={add.isPending}>Add</Button>
        </form>
      </div>
    </Card>
  );
}

function UnclassifiedCard({ review }: { review: ResumeReview }) {
  const rid = review.resume_id;
  const [targets, setTargets] = useState<Record<number, ReviewTarget>>({});
  const assign = useReviewAction(rid, 
    (v: { to: ReviewTarget; source_ids: string[] }) => api.post<ResumeReview>(`/resumes/${rid}/review/assign`, v), "Added to your profile",
  );
  if (!review.unclassified.length) return null;
  return (
    <Card className="border-warning/50">
      <CardHeader title="Couldn't classify" description="This content sits under a heading JobPilot didn't recognise. Choose where it belongs." />
      <ul className="divide-y divide-border border-t border-border">
        {review.unclassified.map((u, i) => (
          <li key={u.source_ids.join()} className="flex flex-wrap items-start gap-3 px-4 py-3">
            <div className="min-w-0 flex-1">
              {u.heading && <p className="text-xs font-medium text-fg-muted">Under “{u.heading}”</p>}
              <p className="whitespace-pre-line text-[13px]">{u.text}</p>
            </div>
            <div className="flex gap-2">
              <NativeSelect aria-label="Section" value={targets[i] ?? "experience"} onChange={(e) => setTargets({ ...targets, [i]: e.target.value as ReviewTarget })}>
                {KINDS.map((k) => <option key={k} value={k}>{KIND_TITLE[k]}</option>)}<option value="summary">Summary</option>
              </NativeSelect>
              <Button size="sm" loading={assign.isPending} onClick={() => assign.mutate({ to: targets[i] ?? "experience", source_ids: u.source_ids })}>Add</Button>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

// ── Editor ─────────────────────────────────────────────────────────────────────────────────────
export function ReviewEditor({ resumeId }: { resumeId: string }) {
  const { data: review, error, refetch, isPending } = useResumeReview(resumeId);
  const [dialog, setDialog] = useState<Dialogs>(null);
  const [onlyFlagged, setOnlyFlagged] = useState(false);
  const close = () => setDialog(null);
  const start = useReviewAction(resumeId, () => api.post<ResumeReview>(`/resumes/${resumeId}/review/import`), "Ready to review");
  const confirm = useReviewAction(resumeId, () => api.post<ResumeReview>(`/resumes/${resumeId}/review/confirm`), "Marked as reviewed", close);
  const del = useReviewAction(resumeId, 
    (d: { kind: ReviewKind; id: string }) => api.del<ResumeReview>(`/resumes/${resumeId}/review/entries/${d.kind}/${d.id}`), "Entry deleted", close,
  );

  if (error) return <Card><ErrorState error={error} onRetry={() => refetch()} /></Card>;
  if (isPending || !review) return <Skeleton className="h-96 rounded-lg" />;
  if (!review.imported) {
    return (
      <Card>
        <EmptyState icon={ListChecks} title="Review what JobPilot extracted"
          description="Correct, move, split or merge anything that landed in the wrong section. JobPilot uses your corrected profile for matching, tailoring and application answers. Your original file is never changed." />
        <div className="flex justify-center pb-10"><Button variant="primary" loading={start.isPending} onClick={() => start.mutate(undefined)}>Start review</Button></div>
      </Card>
    );
  }
  return (
    <div className="flex flex-col gap-5">
      <Card className="flex flex-wrap items-center gap-3 p-4">
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-medium" aria-live="polite">
            {review.needs_review ? `${review.needs_review} ${review.needs_review === 1 ? "item needs" : "items need"} your review` : "Everything has been reviewed"}
          </p>
          <p className="text-xs text-fg-muted">
            Changes save to your profile immediately.{review.reviewed_at ? ` Last confirmed ${formatDate(review.reviewed_at)}.` : ""}
          </p>
        </div>
        <label className="flex items-center gap-2 text-[13px]"><Switch checked={onlyFlagged} onCheckedChange={setOnlyFlagged} /> Only show items to check</label>
        <Button size="sm" onClick={() => setDialog({ type: "reextract" })}><RefreshCw /> Re-extract…</Button>
        <Button size="sm" variant="primary" onClick={() => setDialog({ type: "confirm" })}><CheckCheck /> Mark all reviewed</Button>
      </Card>
      <UnclassifiedCard review={review} />
      {(!onlyFlagged || review.summary.needs_review) && <SummaryCard key={review.summary.text ?? ""} review={review} />}
      {KINDS.map((k) => <SectionCard key={k} review={review} kind={k} onlyFlagged={onlyFlagged} open={setDialog} />)}
      {!onlyFlagged && <SkillsCard review={review} />}

      <Dialog open={dialog?.type === "edit"} onOpenChange={(o) => !o && close()}>
        {dialog?.type === "edit" && <EntryDialog resumeId={resumeId} kind={dialog.kind} draft={dialog.draft} onClose={close} />}
      </Dialog>
      <Dialog open={dialog?.type === "move"} onOpenChange={(o) => !o && close()}>
        {dialog?.type === "move" && <MoveDialog resumeId={resumeId} kind={dialog.kind} entry={dialog.entry} onClose={close} />}
      </Dialog>
      <Dialog open={dialog?.type === "split"} onOpenChange={(o) => !o && close()}>
        {dialog?.type === "split" && <SplitDialog resumeId={resumeId} kind={dialog.kind} entry={dialog.entry} onClose={close} />}
      </Dialog>
      <Dialog open={dialog?.type === "reextract"} onOpenChange={(o) => !o && close()}>
        {dialog?.type === "reextract" && <ReextractDialog resumeId={resumeId} onClose={close} />}
      </Dialog>
      <ConfirmDialog open={dialog?.type === "delete"} onOpenChange={(o) => !o && close()} destructive confirmLabel="Delete"
        title="Delete this entry?" description="It's removed from your profile and won't come back if the resume is re-extracted. Your original file isn't changed."
        loading={del.isPending} onConfirm={() => dialog?.type === "delete" && del.mutate({ kind: dialog.kind, id: dialog.entry.id })} />
      <ConfirmDialog open={dialog?.type === "confirm"} onOpenChange={(o) => !o && close()} confirmLabel="Mark reviewed"
        title="Mark everything as reviewed?" description="All entries from this resume are marked as verified and no longer flagged."
        loading={confirm.isPending} onConfirm={() => confirm.mutate(undefined)} />
    </div>
  );
}
