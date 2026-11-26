import { describe, expect, it } from "vitest";
import { formatExperience, formatSalary, percent, relativeTime, scoreTone } from "./format";
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
});

describe("formatting helpers", () => {
  it("formats experience ranges", () => {
    expect(formatExperience(null, null)).toBeNull();
    expect(formatExperience(0, 1)).toBe("Entry level");
    expect(formatExperience(3, 5)).toBe("3–5 yrs");
    expect(formatExperience(5, null)).toBe("5+ yrs");
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

describe("wordDiff", () => {
  it("marks removed and added words", () => {
    const parts = wordDiff("Responsible for CI/CD pipelines", "Automated CI/CD pipelines");
    expect(parts.filter((p) => p.type === "removed").map((p) => p.text).join("")).toContain("Responsible");
    expect(parts.filter((p) => p.type === "added").map((p) => p.text).join("")).toContain("Automated");
    expect(parts.filter((p) => p.type !== "removed").map((p) => p.text).join("")).toBe("Automated CI/CD pipelines");
  });
});
