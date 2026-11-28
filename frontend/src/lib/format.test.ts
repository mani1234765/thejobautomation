import { describe, expect, it } from "vitest";
import {
  EMPLOYMENT_OPTIONS, formatExperience, formatPolicyValue, formatSalary, groupPolicyChanges, percent, relativeTime, salarySource,
  scoreTone,
} from "./format";
import { wordDiff } from "./diff";

describe("formatSalary", () => {
  it("never invents a figure when pay is undisclosed", () => {
    expect(formatSalary({ salary_min: null, salary_max: null, salary_currency: null, salary_period: null })).toBeNull();
  });
  it("formats INR lakhs as CTC", () => {
    expect(formatSalary({ salary_min: 1_200_000, salary_max: 1_800_000, salary_currency: "INR", salary_period: "year" })).toBe("₹12L–₹18L CTC");
  });
  it("formats foreign currency per period", () => {
    expect(formatSalary({ salary_min: 150_000, salary_max: 190_000, salary_currency: "USD", salary_period: "year" })).toBe("$150k–$190k/yr");
    expect(formatSalary({ salary_min: 20_000, salary_max: 20_000, salary_currency: "INR", salary_period: "month" })).toBe("₹20k/mo");
  });
  it("never assumes a currency or a pay period", () => {
    expect(formatSalary({ salary_min: 900_000, salary_max: 1_200_000, salary_currency: null, salary_period: "year" }))
      .toBe("900k–1200k/yr · Currency not stated");
    expect(formatSalary({ salary_min: 150_000, salary_max: 190_000, salary_currency: "$", salary_period: null }))
      .toBe("$150k–$190k · Currency unclear ($) · Period not stated");
    expect(formatSalary({ salary_min: 2_500_000, salary_max: 3_500_000, salary_currency: "INR", salary_period: null }))
      .toBe("₹25L–₹35L · Period not stated");
  });
  it("labels where the salary came from", () => {
    expect(salarySource({ salary_min: 1, salary_max: 2, salary_disclosure: "verified" })).toBe("Employer-provided salary");
    expect(salarySource({ salary_min: 1, salary_max: 2, salary_disclosure: "advertised" })).toBe("Salary mentioned in the description");
    expect(salarySource({ salary_min: null, salary_max: null, salary_disclosure: "undisclosed" })).toBeNull();
  });
});

describe("formatting helpers", () => {
  it("formats experience ranges", () => {
    expect(formatExperience(null, null)).toBeNull();
    expect(formatExperience(0, 1)).toBe("Entry level");
    expect(formatExperience(3, 5)).toBe("3–5 yrs");
    expect(formatExperience(5, null)).toBe("5+ yrs");
    expect(formatExperience(0, null)).toBe("Entry level"); // "Freshers welcome": minimum 0, no maximum
  });
  it("offers all seven employment types, each labelled", () => {
    expect(EMPLOYMENT_OPTIONS.map((o) => o.value)).toEqual([
      "full_time", "part_time", "contract", "internship", "freelance", "temporary", "apprenticeship",
    ]);
    expect(EMPLOYMENT_OPTIONS.every((o) => o.label.length > 0)).toBe(true);
  });
  it("shows a dash rather than 0% when a rate has no denominator", () => {
    expect(percent(null)).toBe("—");
    expect(percent(0.25)).toBe("25%");
  });
  it("describes relative times", () => {
    const now = Date.parse("2026-11-26T12:00:00Z");
    expect(relativeTime("2026-11-26T11:59:50Z", now)).toBe("just now");
    expect(relativeTime("2026-11-26T09:00:00Z", now)).toBe("3 hours ago");
    expect(relativeTime(null, now)).toBe("—");
  });
  it("maps scores to tones", () => {
    expect(scoreTone(null)).toBe("neutral");
    expect(scoreTone(90)).toBe("positive");
    expect(scoreTone(60)).toBe("accent");
  });
});

describe("policy changes", () => {
  it("formats settings as they read on the Settings page", () => {
    expect(formatPolicyValue("min_ctc_lpa", 12)).toBe("₹12 LPA");
    expect(formatPolicyValue("allow_undisclosed_salary", false)).toBe("off");
    expect(formatPolicyValue("apprentice_min_stipend", null)).toBe("no minimum");
    expect(formatPolicyValue("apprentice_min_stipend", 15000)).toBe("15,000");
    expect(formatPolicyValue("apprentice_stipend_currency", null)).toBe("not set");
    expect(formatPolicyValue("apprentice_stipend_period", "year")).toBe("per year");
    expect(formatPolicyValue("apprentice_advertised_stipend", "review")).toBe("always review");
    expect(formatPolicyValue("apprentice_unverified_outcome", "review")).toBe("needs review");
  });
  it("groups changes by policy, salary first", () => {
    const groups = groupPolicyChanges([
      { key: "apprentice_min_stipend", group: "apprenticeship", before: null, after: 15000 },
      { key: "min_ctc_lpa", group: "salary", before: 10, after: 12 },
    ]);
    expect(groups.map((g) => g.label)).toEqual(["Full-time salary policy", "Apprenticeship stipend policy"]);
    expect(groupPolicyChanges([])).toEqual([]);
  });
});

describe("wordDiff", () => {
  it("marks removed and added words", () => {
    const parts = wordDiff("Responsible for CI/CD pipelines", "Automated CI/CD pipelines");
    expect(parts.filter((p) => p.type === "removed").map((p) => p.text).join("")).toContain("Responsible");
    expect(parts.filter((p) => p.type === "added").map((p) => p.text).join("")).toContain("Automated");
    expect(parts.filter((p) => p.type !== "removed").map((p) => p.text).join("")).toBe("Automated CI/CD pipelines");
  });
});
