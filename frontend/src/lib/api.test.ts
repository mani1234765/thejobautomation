import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";

afterEach(() => vi.restoreAllMocks());

describe("api client", () => {
  it("sends the CSRF token on mutations and not on reads", async () => {
    document.cookie = "jp_csrf=abc123";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
    await api.get("/profile");
    await api.post("/profile/verify");
    const [getUrl, getInit] = fetchMock.mock.calls[0];
    const [, postInit] = fetchMock.mock.calls[1];
    expect(getUrl).toBe("/api/v1/profile");
    expect((getInit!.headers as Record<string, string>)["x-jobpilot-csrf"]).toBeUndefined();
    expect((postInit!.headers as Record<string, string>)["x-jobpilot-csrf"]).toBe("abc123");
  });

  it("drops empty query params and joins arrays", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
    await api.get("/jobs", { q: "", work_modes: ["remote", "hybrid"], skills: [], page: 2 });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/jobs?work_modes=remote%2Chybrid&page=2");
  });

  it("normalises API errors", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: { message: "Daily limit reached", status: 409 } }), { status: 409 }),
    );
    await expect(api.post("/applications/x/submission")).rejects.toMatchObject({ message: "Daily limit reached", status: 409 });
  });

  it("reports network failures clearly", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    const err = (await api.get("/health").catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
  });
});
