import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import type { AppStatus, EmploymentType, JobCard, PolicyChange } from "./types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const diff = (new Date(iso).getTime() - now) / 1000;
  const abs = Math.abs(diff);
  if (abs < 45) return "just now";
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["minute", 60], ["hour", 3600], ["day", 86400], ["week", 604800], ["month", 2629800], ["year", 31557600],
  ];
  let chosen: [Intl.RelativeTimeFormatUnit, number] = units[0];
  for (const u of units) if (abs >= u[1]) chosen = u;
  return rtf.format(Math.round(diff / chosen[1]), chosen[0]);
}

export function formatDate(iso: string | null | undefined, opts: Intl.DateTimeFormatOptions = { day: "numeric", month: "short", year: "numeric" }) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-IN", opts);
}

export function formatDateTime(iso: string | null | undefined) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

function compactMoney(v: number, currency: string | null): string {
  if (currency === "INR") {
    if (v >= 100_000) return `₹${(v / 100_000).toFixed(v % 100_000 === 0 ? 0 : 1)}L`;
    return v >= 1000 ? `₹${Math.round(v / 1000)}k` : `₹${v}`;
  }
  // No currency stated: show the bare figure, never a guessed symbol.
  const symbol = currency == null ? "" : ({ USD: "$", EUR: "€", GBP: "£", $: "$" } as Record<string, string>)[currency] ?? `${currency} `;
  return v >= 1000 ? `${symbol}${Math.round(v / 1000)}k` : `${symbol}${v}`;
}

/** Salary as disclosed, or null. Never invents a figure, a currency or a pay period. */
export function formatSalary(job: Pick<JobCard, "salary_min" | "salary_max" | "salary_currency" | "salary_period">): string | null {
  const { salary_min: lo, salary_max: hi, salary_currency: cur, salary_period: per } = job;
  if (lo == null && hi == null) return null;
  const range = lo != null && hi != null && lo !== hi ? `${compactMoney(lo, cur)}–${compactMoney(hi, cur)}` : compactMoney((lo ?? hi)!, cur);
  const suffix = per === "month" ? "/mo" : per === "hour" ? "/hr" : per === "year" ? (cur === "INR" ? " CTC" : "/yr") : "";
  const notes = [cur == null && "Currency not stated", cur === "$" && "Currency unclear ($)", per == null && "Period not stated"].filter(Boolean);
  return range + suffix + (notes.length ? ` · ${notes.join(" · ")}` : "");
}

/** Where a salary figure came from: the employer's own pay field, or wording in the description. */
export function salarySource(job: Pick<JobCard, "salary_min" | "salary_max" | "salary_disclosure">): string | null {
  if (job.salary_min == null && job.salary_max == null) return null;
  return job.salary_disclosure === "verified" ? "Employer-provided salary" : "Salary mentioned in the description";
}

export function formatExperience(min: number | null, max: number | null): string | null {
  if (min == null) return null;
  if (min === 0 && (max ?? 0) <= 1) return "Entry level";
  return max != null ? `${min}–${max} yrs` : `${min}+ yrs`;
}

export const STATUS_META: Record<AppStatus, { label: string; tone: Tone }> = {
  discovered: { label: "Discovered", tone: "neutral" },
  shortlisted: { label: "Shortlisted", tone: "neutral" },
  preparing: { label: "Preparing", tone: "info" },
  awaiting_approval: { label: "Awaiting approval", tone: "warning" },
  approved: { label: "Approved", tone: "accent" },
  submitted: { label: "Submitted", tone: "info" },
  assessment: { label: "Assessment", tone: "info" },
  interview: { label: "Interview", tone: "accent" },
  offer: { label: "Offer", tone: "positive" },
  rejected: { label: "Rejected", tone: "danger" },
  withdrawn: { label: "Withdrawn", tone: "neutral" },
  failed: { label: "Submission failed", tone: "danger" },
};

export const PIPELINE: AppStatus[] = [
  "discovered", "shortlisted", "preparing", "awaiting_approval", "approved", "submitted", "assessment", "interview", "offer",
];

export type Tone = "neutral" | "accent" | "positive" | "warning" | "danger" | "info";

export function scoreTone(score: number | null | undefined): Tone {
  if (score == null) return "neutral";
  if (score >= 75) return "positive";
  if (score >= 55) return "accent";
  if (score >= 40) return "warning";
  return "neutral";
}

export const WORK_MODE_LABEL: Record<string, string> = { remote: "Remote", hybrid: "Hybrid", onsite: "On-site" };
export const EMPLOYMENT_LABEL: Record<string, string> = {
  full_time: "Full-time", part_time: "Part-time", contract: "Contract", internship: "Internship",
  freelance: "Freelance", temporary: "Temporary", apprenticeship: "Apprenticeship",
};
/** Every employment type a user can prefer, in display order. */
export const EMPLOYMENT_OPTIONS = (Object.keys(EMPLOYMENT_LABEL) as EmploymentType[]).map((value) => ({
  value, label: EMPLOYMENT_LABEL[value],
}));
/** Settings-page names for each eligibility setting, used to explain why a decision is outdated. */
export const POLICY_SETTING: Record<string, string> = {
  min_ctc_lpa: "Minimum annual CTC", allow_undisclosed_salary: "Allow undisclosed salary",
  min_stipend_monthly_inr: "Minimum monthly stipend",
  allow_undisclosed_stipend: "Allow undisclosed stipend (internships and apprenticeships)",
  apprentice_min_stipend: "Minimum stipend", apprentice_stipend_currency: "Stipend currency",
  apprentice_stipend_period: "Stipend period", apprentice_advertised_stipend: "Stipends stated only in the description",
  apprentice_unverified_outcome: "Unverified stipend that meets the minimum",
};
export const POLICY_GROUP_LABEL: Record<PolicyChange["group"], string> = {
  salary: "Full-time salary policy", stipend: "Internship stipend policy", apprenticeship: "Apprenticeship stipend policy",
};
const POLICY_CHOICE: Record<string, string> = {
  month: "per month", year: "per year", compare: "compare with minimum", review: "needs review", pass: "pass (marked unverified)",
};

/** A stored or current eligibility setting, as the user would recognise it from Settings. */
export function formatPolicyValue(key: string, v: PolicyChange["before"]): string {
  if (typeof v === "boolean") return v ? "on" : "off";
  if (v == null) return key === "apprentice_stipend_currency" ? "not set" : key.startsWith("allow_") ? "not recorded" : "no minimum";
  if (key === "min_ctc_lpa") return `₹${v} LPA`;
  if (key === "min_stipend_monthly_inr") return `₹${Number(v).toLocaleString("en-IN")}/month`;
  if (key === "apprentice_advertised_stipend" && v === "review") return "always review";
  if (typeof v === "number") return v.toLocaleString("en-IN");
  return POLICY_CHOICE[v] ?? v;
}

/** Changed settings grouped by Settings card, in the order the cards appear. */
export function groupPolicyChanges(changes: PolicyChange[]): { group: PolicyChange["group"]; label: string; changes: PolicyChange[] }[] {
  return (Object.keys(POLICY_GROUP_LABEL) as PolicyChange["group"][])
    .map((group) => ({ group, label: POLICY_GROUP_LABEL[group], changes: changes.filter((c) => c.group === group) }))
    .filter((g) => g.changes.length > 0);
}

export const EXPERIENCE_LEVELS = [
  { value: "fresher", label: "Fresher" },
  { value: "internship", label: "Internship" },
  { value: "0-1", label: "0–1 yrs" },
  { value: "1-2", label: "1–2 yrs" },
  { value: "2-3", label: "2–3 yrs" },
  { value: "3-5", label: "3–5 yrs" },
  { value: "5+", label: "5+ yrs" },
] as const;

export function pluralize(n: number, word: string, plural = `${word}s`) {
  return `${n.toLocaleString("en-IN")} ${n === 1 ? word : plural}`;
}

export function percent(v: number | null | undefined): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

export function initials(name: string | null | undefined): string {
  if (!name) return "?";
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]!.toUpperCase()).join("");
}
