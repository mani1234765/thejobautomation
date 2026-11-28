import { describe, expect, it } from "vitest";
import { EMPLOYMENT_OPTIONS } from "./format";
import {
  choiceValues, DEFAULT_JOBS_STATE, FILTER_CHOICES, jobsStateFromParams, jobsStateToParams, notStatedFacts, unrecognisedFilterValues,
} from "./job-filters";

const SEVEN = ["full_time", "part_time", "contract", "internship", "freelance", "temporary", "apprenticeship"];

describe("filter choices", () => {
  it("offer the same seven employment types everywhere", () => {
    expect(FILTER_CHOICES.employment_types).toEqual(SEVEN);
    expect(EMPLOYMENT_OPTIONS.map((o) => o.value)).toEqual(SEVEN);
    expect(FILTER_CHOICES.work_modes).toEqual(["remote", "hybrid", "onsite"]);
    expect(FILTER_CHOICES.experience_levels).toContain("5+");
  });
});

describe("Discover Jobs URL state", () => {
  it("round-trips every filter through the URL", () => {
    const s = {
      ...DEFAULT_JOBS_STATE, q: "backend", location: "Pune", employment_types: ["apprenticeship", "freelance"], work_modes: ["remote"],
      experience_levels: ["3-5"], min_salary: "8", posted: "7", min_score: "70", eligibility: "review", state: "saved" as const,
      sort: "recent" as const, page: 3,
    };
    const params = jobsStateToParams(s, new URLSearchParams());
    expect(params.get("employment_types")).toBe("apprenticeship,freelance");
    expect(jobsStateFromParams(params)).toEqual(s);
  });
  it("omits defaults and keeps unrelated parameters", () => {
    const params = jobsStateToParams({ ...DEFAULT_JOBS_STATE, employment_types: ["contract"] }, new URLSearchParams("job=abc&sources=1"));
    expect(params.toString()).toBe("job=abc&sources=1&employment_types=contract");
    expect(jobsStateToParams(DEFAULT_JOBS_STATE, new URLSearchParams("employment_types=contract&page=2")).toString()).toBe("");
  });
  it("drops values the page can't offer instead of sending them to the API", () => {
    const s = jobsStateFromParams(new URLSearchParams("employment_types=full_time,Full-time,bogus,full_time&posted=3&sort=x&page=-1&min_salary=-5"));
    expect(s.employment_types).toEqual(["full_time"]);
    expect([s.posted, s.sort, s.page, s.min_salary]).toEqual(["", "match", 1, ""]);
  });
  it("an empty selection means no filter", () => {
    expect(jobsStateFromParams(new URLSearchParams("employment_types=")).employment_types).toEqual([]);
  });
});

describe("Not stated labels", () => {
  const unknown = {
    employment_type: null, is_internship: false, work_mode: null, experience_min: null,
    salary_max: null, salary_currency: null, salary_period: null, posted_at: null,
  };
  const off = { employment_types: [], work_modes: [], experience_levels: [], min_salary: "", posted: "" };
  it("explain only the filters that are active", () => {
    expect(notStatedFacts(unknown, off)).toEqual([]);
    expect(notStatedFacts(unknown, { ...off, employment_types: ["contract"], work_modes: ["remote"] })).toEqual([
      "Employment type not stated", "Work mode not stated",
    ]);
    expect(notStatedFacts(unknown, { ...off, experience_levels: ["3-5"], min_salary: "8", posted: "7" })).toEqual([
      "Experience not stated", "Pay not stated", "Posting date not stated",
    ]);
  });
  it("never label an internship-flagged or typed listing as unknown", () => {
    expect(notStatedFacts({ ...unknown, is_internship: true }, { ...off, employment_types: ["full_time"] })).toEqual([]);
    expect(notStatedFacts({ ...unknown, employment_type: "temporary" }, { ...off, employment_types: ["full_time"] })).toEqual([]);
  });
  it("say when pay can't be compared without guessing", () => {
    const usd = { ...unknown, salary_max: 90_000, salary_currency: "USD", salary_period: "year" as const };
    expect(notStatedFacts(usd, { ...off, min_salary: "8" })).toEqual(["Pay not comparable (not stated in INR per year or month)"]);
    expect(notStatedFacts({ ...usd, salary_currency: "INR", salary_period: "month" as const }, { ...off, min_salary: "8" })).toEqual([]);
  });
});

describe("saved automation filters", () => {
  it("read a single string as one value and leave unrecognised values out", () => {
    expect(choiceValues({ employment_types: "apprenticeship" }, "employment_types")).toEqual(["apprenticeship"]);
    expect(choiceValues({ employment_types: ["contract", "Contract"] }, "employment_types")).toEqual(["contract"]);
    expect(choiceValues({}, "experience_levels")).toEqual([]);
  });
  it("report unrecognised values so the user can see them before saving", () => {
    expect(unrecognisedFilterValues({ employment_types: ["full_time", "bogus"], work_modes: "office", min_score: 70 })).toEqual({
      employment_types: ["bogus"], work_modes: ["office"],
    });
    expect(unrecognisedFilterValues({ employment_types: SEVEN })).toEqual({});
  });
});
