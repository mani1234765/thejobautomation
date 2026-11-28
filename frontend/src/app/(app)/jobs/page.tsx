"use client";

import { Wallet, Bookmark, ChevronLeft, ChevronRight, GitCompareArrows, ListFilter, Plus, Radar, Search, Settings2, X } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Dialog as DialogPrimitive } from "radix-ui";
import { Suspense, useEffect, useMemo, useState } from "react";
import { MatchExplanation, JobDetailPanel } from "@/components/jobs/job-detail";
import { SourcesDialogContent } from "@/components/jobs/sources-dialog";
import { Button } from "@/components/ui/button";
import { Checkbox, ChipGroup, Segmented } from "@/components/ui/controls";
import { Dialog, DialogContent, Popover, PopoverContent, PopoverTrigger, SheetContent, Tooltip } from "@/components/ui/overlay";
import { Badge, Card, Field, Input, Kbd, NativeSelect } from "@/components/ui/primitives";
import { ScoreRing } from "@/components/ui/score";
import { EmptyState, ErrorState, ListSkeleton, PageHeader } from "@/components/ui/states";
import { api } from "@/lib/api";
import {
  cn, EMPLOYMENT_LABEL, EMPLOYMENT_OPTIONS, EXPERIENCE_LEVELS, formatExperience, formatSalary, pluralize, relativeTime, STATUS_META,
  WORK_MODE_LABEL, WORK_MODE_OPTIONS,
} from "@/lib/format";
import { DEFAULT_JOBS_STATE, jobsStateFromParams, jobsStateToParams, notStatedFacts, type JobsUrlState } from "@/lib/job-filters";
import { keys, useAction, useJobs, type JobFilters } from "@/lib/queries";
import type { JobCard, JobDetail } from "@/lib/types";
import { useQueries } from "@tanstack/react-query";

const PAGE_SIZE = 25;

function useDebounced<T>(value: T, ms = 250) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function JobRow({ job, active, selected, notStated, onOpen, onToggleCompare }: {
  job: JobCard; active: boolean; selected: boolean; notStated: string[]; onOpen: () => void; onToggleCompare: () => void;
}) {
  const salary = formatSalary(job);
  const exp = formatExperience(job.experience_min, job.experience_max);
  const save = useAction((state: "saved" | null) => api.put(`/jobs/${job.id}/state`, { state }), { invalidate: [["jobs"]] });
  const meta = [job.location, job.work_mode && WORK_MODE_LABEL[job.work_mode], job.is_internship ? "Internship" : job.employment_type && EMPLOYMENT_LABEL[job.employment_type], exp, salary].filter(Boolean);
  return (
    <li className={cn("group relative flex items-center gap-3 px-4 py-3 transition-colors hover:bg-surface-2", active && "bg-surface-2")}>
      <Checkbox checked={selected} onCheckedChange={onToggleCompare} aria-label={`Compare ${job.title}`}
        className={cn("transition-opacity", selected ? "opacity-100" : "opacity-0 group-hover:opacity-100 focus-visible:opacity-100")} />
      <ScoreRing score={job.match_score} />
      <button onClick={onOpen} className="min-w-0 flex-1 text-left after:absolute after:inset-0 after:content-['']" aria-label={`${job.title} at ${job.company}`}>
        <span className="flex items-center gap-2">
          <span className="truncate text-[13px] font-medium text-fg">{job.title}</span>
          {job.application_status && <Badge tone="accent">{STATUS_META[job.application_status as keyof typeof STATUS_META]?.label}</Badge>}
          {job.eligibility === "ineligible" && <Badge tone="danger">Outside criteria</Badge>}
          {notStated.map((n) => <Badge key={n} tone="neutral">{n}</Badge>)}
        </span>
        <span className="mt-0.5 flex min-w-0 items-center gap-1 text-xs text-fg-muted">
          <span className="truncate"><span className="font-medium text-fg/80">{job.company}</span>{meta.length ? ` · ${meta.join(" · ")}` : ""}</span>
          {job.eligibility === "review" && (
            <span className="relative z-10 shrink-0">
              <Tooltip content="Needs your review: open the job to see which rule and why">
                <Wallet className="size-3 text-warning" aria-label="Needs your review" />
              </Tooltip>
            </span>
          )}
        </span>
      </button>
      <div className="relative hidden max-w-[280px] shrink-0 items-center justify-end gap-1 overflow-hidden lg:flex">
        {[...job.matched_skills.map((s) => [s, "positive"] as const), ...job.missing_skills.map((s) => [s, "warning"] as const)]
          .slice(0, 3).map(([s, tone]) => <Badge key={s} tone={tone}>{s}</Badge>)}
      </div>
      <span className="relative hidden w-24 shrink-0 text-right text-xs whitespace-nowrap text-fg-subtle sm:block">{relativeTime(job.posted_at ?? job.first_seen_at)}</span>
      <Tooltip content={job.saved_state === "saved" ? "Unsave" : "Save"}>
        <Button variant="ghost" size="icon-sm" className="relative" aria-label={job.saved_state === "saved" ? "Unsave job" : "Save job"}
          aria-pressed={job.saved_state === "saved"} onClick={() => save.mutate(job.saved_state === "saved" ? null : "saved")}>
          <Bookmark className={job.saved_state === "saved" ? "fill-accent text-accent" : undefined} />
        </Button>
      </Tooltip>
    </li>
  );
}

function CompareDialog({ ids }: { ids: string[] }) {
  const results = useQueries({ queries: ids.map((id) => ({ queryKey: keys.job(id), queryFn: () => api.get<JobDetail>(`/jobs/${id}`) })) });
  const jobs = results.map((r) => r.data).filter(Boolean) as JobDetail[];
  const rows: [string, (j: JobDetail) => React.ReactNode][] = [
    ["Match", (j) => <ScoreRing score={j.match_score} size={32} />],
    ["Company", (j) => j.company],
    ["Location", (j) => j.location || "Not stated"],
    ["Employment type", (j) => (j.is_internship ? "Internship" : j.employment_type ? EMPLOYMENT_LABEL[j.employment_type] ?? j.employment_type : "Not stated")],
    ["Work mode", (j) => (j.work_mode ? WORK_MODE_LABEL[j.work_mode] : "Not stated")],
    ["Experience", (j) => formatExperience(j.experience_min, j.experience_max) ?? "Not stated"],
    ["Pay", (j) => formatSalary(j) ?? "Not disclosed"],
    ["Eligibility", (j) => <Badge tone={j.eligibility === "eligible" ? "positive" : j.eligibility === "review" ? "warning" : "danger"}>{j.eligibility}</Badge>],
    ["Matched skills", (j) => j.matched_skills.join(", ") || "—"],
    ["Missing", (j) => j.missing_skills.join(", ") || "—"],
  ];
  return (
    <DialogContent wide className="max-w-5xl" title="Compare jobs" description="Side by side, using the same scoring.">
      {jobs.length < ids.length ? <ListSkeleton rows={3} /> : (
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr>
                <th className="w-28" />
                {jobs.map((j) => <th key={j.id} className="min-w-48 px-3 pb-3 text-left align-bottom font-semibold">{j.title}</th>)}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map(([label, f]) => (
                <tr key={label}>
                  <th className="py-2.5 pr-3 text-left text-xs font-medium text-fg-muted">{label}</th>
                  {jobs.map((j) => <td key={j.id} className="px-3 py-2.5 align-top">{f(j)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-6 grid gap-6 md:grid-cols-2">
            {jobs.slice(0, 2).map((j) => <div key={j.id}><p className="mb-3 text-xs font-medium text-fg-muted">{j.title}</p><MatchExplanation job={j} /></div>)}
          </div>
        </div>
      )}
    </DialogContent>
  );
}

function JobsInner() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const openJob = params.get("job");
  const [sourcesOpen, setSourcesOpen] = useState(params.get("sources") === "1");
  // Filters live in the URL (replaced, not pushed), so a refresh or a shared link keeps them.
  const [f, setFilters] = useState<JobsUrlState>(() => jobsStateFromParams(new URLSearchParams(params.toString())));
  const set = <K extends keyof JobsUrlState>(k: K) => (v: JobsUrlState[K]) =>
    setFilters((prev) => ({ ...prev, [k]: v, page: k === "page" ? (v as number) : 1 })); // any filter change → page 1
  const { q, location, work_modes: workModes, employment_types: types, experience_levels: levels, min_salary: minSalary,
    posted, min_score: minScore, eligibility, state, sort, page } = f;
  const [setQ, setLocation, setWorkModes, setTypes, setLevels, setMinSalary, setPosted, setMinScore, setEligibility, setState, setSort] = [
    set("q"), set("location"), set("work_modes"), set("employment_types"), set("experience_levels"), set("min_salary"), set("posted"),
    set("min_score"), set("eligibility"), set("state"), set("sort"),
  ];
  const [compare, setCompare] = useState<string[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);

  const debouncedQ = useDebounced(q);
  const debouncedLoc = useDebounced(location);
  const setPage = (fn: (p: number) => number) => set("page")(fn(page));
  useEffect(() => {
    const next = jobsStateToParams({ ...f, q: debouncedQ, location: debouncedLoc }, new URLSearchParams(window.location.search));
    if (next.toString() !== window.location.search.replace(/^\?/, "")) {
      router.replace(`${pathname}${next.toString() ? `?${next}` : ""}`, { scroll: false });
    }
  }, [f, debouncedQ, debouncedLoc, pathname, router]);
  const filters: JobFilters = useMemo(() => ({
    q: debouncedQ || undefined, location: debouncedLoc || undefined, work_modes: workModes, employment_types: types,
    experience_levels: levels, min_salary_lpa: minSalary ? Number(minSalary) : undefined,
    posted_within_days: posted ? Number(posted) : undefined, min_score: minScore ? Number(minScore) : undefined,
    eligibility: eligibility || undefined, state, sort, page, page_size: PAGE_SIZE,
  }), [debouncedQ, debouncedLoc, workModes, types, levels, minSalary, posted, minScore, eligibility, state, sort, page]);

  const { data, error, refetch, isPending, isFetching } = useJobs(filters);
  const moreCount = [location, minSalary, posted, minScore, eligibility].filter(Boolean).length + levels.length;
  const anyFilter = !!(q || moreCount || workModes.length || types.length);

  const setJob = (id: string | null) => {
    const next = new URLSearchParams(params.toString());
    if (id) next.set("job", id); else next.delete("job");
    next.delete("sources");
    router.replace(`${pathname}${next.toString() ? `?${next}` : ""}`, { scroll: false });
  };
  const toggleCompare = (id: string) =>
    setCompare((c) => (c.includes(id) ? c.filter((x) => x !== id) : c.length >= 4 ? c : [...c, id]));
  const clearAll = () => setFilters((prev) => ({ ...DEFAULT_JOBS_STATE, state: prev.state, sort: prev.sort }));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "/" && !["INPUT", "TEXTAREA"].includes((e.target as HTMLElement).tagName)) {
        e.preventDefault();
        document.getElementById("job-search")?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  return (
    <>
      <PageHeader
        title="Discover Jobs"
        description={data ? `${pluralize(data.total, "role")} from ${pluralize(data.sources, "followed board")}, ranked by fit` : "Roles from the boards you follow, ranked by fit"}
        actions={<Button onClick={() => setSourcesOpen(true)}><Settings2 /> Manage sources</Button>}
      />

      <div className="sticky top-12 z-20 -mx-4 mb-4 flex flex-col gap-2.5 border-b border-transparent bg-bg/95 px-4 pt-1 pb-3 backdrop-blur sm:-mx-6 sm:px-6 lg:top-0 lg:-mx-8 lg:px-8">
        <div className="flex flex-col gap-2 sm:flex-row">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-fg-subtle" />
            <Input id="job-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search title, company or team"
              className="h-9 pl-8 pr-10 text-sm" aria-label="Search jobs" />
            <Kbd className="absolute top-1/2 right-2.5 -translate-y-1/2">/</Kbd>
          </div>
          <div className="flex gap-2">
            <Popover>
              <PopoverTrigger asChild>
                <Button className="h-9"><ListFilter /> Filters{moreCount > 0 && <Badge tone="accent">{moreCount}</Badge>}</Button>
              </PopoverTrigger>
              <PopoverContent align="end" className="w-80 space-y-3">
                <Field label="Location" htmlFor="f-loc"><Input id="f-loc" value={location} onChange={(e) => setLocation(e.target.value)} placeholder="City or country" /></Field>
                <Field label="Experience"><ChipGroup ariaLabel="Experience" value={levels} onChange={setLevels} options={EXPERIENCE_LEVELS} /></Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Min pay (₹ LPA)" htmlFor="f-sal" hint="Undisclosed pay stays visible">
                    <Input id="f-sal" type="number" min={0} value={minSalary} onChange={(e) => setMinSalary(e.target.value)} />
                  </Field>
                  <Field label="Posted within" htmlFor="f-posted">
                    <NativeSelect id="f-posted" value={posted} onChange={(e) => setPosted(e.target.value)}>
                      <option value="">Any time</option><option value="1">24 hours</option><option value="7">7 days</option>
                      <option value="14">14 days</option><option value="30">30 days</option>
                    </NativeSelect>
                  </Field>
                  <Field label="Min match" htmlFor="f-score">
                    <NativeSelect id="f-score" value={minScore} onChange={(e) => setMinScore(e.target.value)}>
                      <option value="">Any</option><option value="40">40+</option><option value="55">55+</option><option value="70">70+</option><option value="85">85+</option>
                    </NativeSelect>
                  </Field>
                  <Field label="Eligibility" htmlFor="f-elig">
                    <NativeSelect id="f-elig" value={eligibility} onChange={(e) => setEligibility(e.target.value)}>
                      <option value="">Any</option><option value="eligible">Eligible</option><option value="review">Needs review</option><option value="ineligible">Outside criteria</option>
                    </NativeSelect>
                  </Field>
                </div>
              </PopoverContent>
            </Popover>
            <Segmented ariaLabel="Sort" value={sort} onChange={setSort} className="h-9"
              options={[{ value: "match", label: "Best match" }, { value: "recent", label: "Newest" }]} />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <ChipGroup ariaLabel="Work mode" value={workModes} onChange={setWorkModes} options={WORK_MODE_OPTIONS} />
          <span className="hidden h-4 w-px bg-border sm:block" />
          <ChipGroup ariaLabel="Employment type" value={types} onChange={setTypes} options={EMPLOYMENT_OPTIONS} />
          {anyFilter && <Button variant="ghost" size="sm" onClick={clearAll}><X /> Clear</Button>}
          <Segmented ariaLabel="List" value={state} onChange={setState} className="ml-auto"
            options={[{ value: "active", label: "All" }, { value: "saved", label: "Saved" }, { value: "dismissed", label: "Dismissed" }]} />
        </div>
      </div>

      <Card className={cn("overflow-hidden transition-opacity", isFetching && !isPending && "opacity-70")}>
        {error ? <ErrorState error={error} onRetry={() => refetch()} /> : isPending ? <ListSkeleton rows={8} /> : data.sources === 0 ? (
          <EmptyState icon={Radar} title="Follow your first company"
            description="JobPilot discovers roles from company job boards on Greenhouse, Lever and Ashby, then scores each one against your profile."
            action={<Button variant="primary" onClick={() => setSourcesOpen(true)}><Plus /> Follow a board</Button>} />
        ) : data.items.length === 0 ? (
          <EmptyState icon={Search} title={state === "saved" ? "No saved jobs" : state === "dismissed" ? "Nothing dismissed" : "No jobs match these filters"}
            description={anyFilter ? "Try removing a filter or broadening your search." : "Sync your sources or follow more companies."}
            action={anyFilter ? <Button onClick={clearAll}>Clear filters</Button> : undefined} />
        ) : (
          <ul className="divide-y divide-border">
            {data.items.map((j) => (
              <JobRow key={j.id} job={j} active={openJob === j.id} selected={compare.includes(j.id)} notStated={notStatedFacts(j, f)}
                onOpen={() => setJob(j.id)} onToggleCompare={() => toggleCompare(j.id)} />
            ))}
          </ul>
        )}
      </Card>

      {data && pages > 1 && (
        <nav className="mt-4 flex items-center justify-between text-xs text-fg-muted" aria-label="Pagination">
          <span>{(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, data.total)} of {data.total.toLocaleString("en-IN")}</span>
          <div className="flex gap-1">
            <Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}><ChevronLeft /> Previous</Button>
            <Button size="sm" variant="secondary" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next <ChevronRight /></Button>
          </div>
        </nav>
      )}

      {compare.length > 0 && (
        <div className="fixed bottom-5 left-1/2 z-30 flex -translate-x-1/2 items-center gap-3 rounded-full border border-border bg-surface py-1.5 pr-1.5 pl-4 shadow-lg lg:ml-[116px]">
          <span className="text-[13px]">{compare.length} selected <span className="text-fg-subtle">(max 4)</span></span>
          <Button size="sm" variant="ghost" onClick={() => setCompare([])}>Clear</Button>
          <Button size="sm" variant="primary" className="rounded-full" disabled={compare.length < 2} onClick={() => setCompareOpen(true)}>
            <GitCompareArrows /> Compare
          </Button>
        </div>
      )}

      <Dialog open={sourcesOpen} onOpenChange={setSourcesOpen}><SourcesDialogContent /></Dialog>
      <Dialog open={compareOpen} onOpenChange={setCompareOpen}>{compareOpen && <CompareDialog ids={compare} />}</Dialog>
      <DialogPrimitive.Root open={!!openJob} onOpenChange={(o) => !o && setJob(null)}>
        {openJob && (
          <SheetContent title="Job details">
            <JobDetailPanel jobId={openJob} compareSelected={compare.includes(openJob)} onToggleCompare={() => toggleCompare(openJob)} />
          </SheetContent>
        )}
      </DialogPrimitive.Root>
    </>
  );
}

export default function JobsPage() {
  return (
    <Suspense>
      <JobsInner />
    </Suspense>
  );
}
