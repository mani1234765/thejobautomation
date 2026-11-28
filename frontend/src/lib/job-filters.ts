import type { JobCard } from "./types";
import { EMPLOYMENT_OPTIONS, EXPERIENCE_LEVELS, WORK_MODE_OPTIONS } from "./format";

/** The multiple-choice filters shared by Discover Jobs and discovery automations, with the backend's values. */
export const FILTER_CHOICES = {
  employment_types: EMPLOYMENT_OPTIONS.map((o) => o.value as string),
  work_modes: WORK_MODE_OPTIONS.map((o) => o.value as string),
  experience_levels: EXPERIENCE_LEVELS.map((o) => o.value as string),
} as const;
export type ChoiceFilter = keyof typeof FILTER_CHOICES;

/** Discover Jobs filters as they appear in the URL, so a refresh or a shared link keeps them. */
export type JobsUrlState = {
  q: string; location: string; work_modes: string[]; employment_types: string[]; experience_levels: string[];
  min_salary: string; posted: string; min_score: string; eligibility: string;
  state: "active" | "saved" | "dismissed"; sort: "match" | "recent"; page: number;
};

export const DEFAULT_JOBS_STATE: JobsUrlState = {
  q: "", location: "", work_modes: [], employment_types: [], experience_levels: [], min_salary: "", posted: "",
  min_score: "", eligibility: "", state: "active", sort: "match", page: 1,
};

const URL_KEYS = Object.keys(DEFAULT_JOBS_STATE) as (keyof JobsUrlState)[];
const ONE_OF = {
  posted: ["1", "7", "14", "30"], min_score: ["40", "55", "70", "85"], eligibility: ["eligible", "review", "ineligible"],
  state: ["active", "saved", "dismissed"], sort: ["match", "recent"],
} as const;

/** Read filters from the URL. Values the page can't offer are dropped rather than sent to the API. */
export function jobsStateFromParams(params: URLSearchParams): JobsUrlState {
  const list = (k: ChoiceFilter) => [...new Set((params.get(k) ?? "").split(",").filter((v) => FILTER_CHOICES[k].includes(v)))];
  const pick = <K extends keyof typeof ONE_OF>(k: K): string => {
    const v = params.get(k) ?? "";
    return (ONE_OF[k] as readonly string[]).includes(v) ? v : DEFAULT_JOBS_STATE[k];
  };
  const salary = params.get("min_salary") ?? "";
  const page = Number(params.get("page"));
  return {
    q: params.get("q") ?? "", location: params.get("location") ?? "",
    work_modes: list("work_modes"), employment_types: list("employment_types"), experience_levels: list("experience_levels"),
    min_salary: salary !== "" && Number(salary) >= 0 ? salary : "",
    posted: pick("posted"), min_score: pick("min_score"), eligibility: pick("eligibility"),
    state: pick("state") as JobsUrlState["state"], sort: pick("sort") as JobsUrlState["sort"],
    page: Number.isInteger(page) && page > 1 ? page : 1,
  };
}

/** Write filters into the URL, leaving other parameters (the open job, the sources dialog) alone. Defaults are omitted. */
export function jobsStateToParams(s: JobsUrlState, base: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(base.toString());
  for (const k of URL_KEYS) {
    const v = s[k];
    const text = Array.isArray(v) ? v.join(",") : String(v);
    const fallback = DEFAULT_JOBS_STATE[k];
    if (text === "" || text === (Array.isArray(fallback) ? fallback.join(",") : String(fallback))) next.delete(k);
    else next.set(k, text);
  }
  return next;
}

/** Values in a saved automation filter that JobPilot doesn't recognise (from an older version or another client). */
export function unrecognisedFilterValues(filters: Record<string, unknown>): Partial<Record<ChoiceFilter, string[]>> {
  const out: Partial<Record<ChoiceFilter, string[]>> = {};
  for (const k of Object.keys(FILTER_CHOICES) as ChoiceFilter[]) {
    const raw = filters[k];
    const values = raw == null ? [] : Array.isArray(raw) ? raw : [raw];
    const bad = values.map(String).filter((v) => !(FILTER_CHOICES[k] as readonly string[]).includes(v));
    if (bad.length) out[k] = bad;
  }
  return out;
}

/** A saved automation filter as chip values: a single string counts as one value; unrecognised values are left out. */
export function choiceValues(filters: Record<string, unknown>, k: ChoiceFilter): string[] {
  const raw = filters[k];
  const values = raw == null ? [] : Array.isArray(raw) ? raw : [raw];
  return values.map(String).filter((v) => (FILTER_CHOICES[k] as readonly string[]).includes(v));
}

type ListingFacts = Pick<JobCard, "employment_type" | "is_internship" | "work_mode" | "experience_min" | "salary_max" | "salary_currency" | "salary_period" | "posted_at">;

/** Why a job is still in filtered results: the filtered facts its listing doesn't state (shown as "Not stated"). */
export function notStatedFacts(job: ListingFacts, s: Pick<JobsUrlState, "employment_types" | "work_modes" | "experience_levels" | "min_salary" | "posted">): string[] {
  const out: string[] = [];
  if (s.employment_types.length && !job.is_internship && !job.employment_type) out.push("Employment type not stated");
  if (s.work_modes.length && !job.work_mode) out.push("Work mode not stated");
  if (s.experience_levels.length && job.experience_min == null) out.push("Experience not stated");
  if (s.min_salary !== "") {
    if (job.salary_max == null) out.push("Pay not stated");
    else if (job.salary_currency !== "INR" || !["year", "month"].includes(job.salary_period ?? "")) out.push("Pay not comparable (not stated in INR per year or month)");
  }
  if (s.posted !== "" && !job.posted_at) out.push("Posting date not stated");
  return out;
}
