import { describe, expect, it, vi } from "vitest";
import { buildCsp } from "../proxy";
import { dismiss, subscribe, toast, type ToastItem } from "./toast";

describe("buildCsp", () => {
  const prod = buildCsp("abc123", false);
  it("is strict in production", () => {
    expect(prod).toContain("script-src 'self' 'nonce-abc123' 'strict-dynamic'");
    expect(prod).not.toContain("unsafe-eval");
    expect(prod).not.toMatch(/script-src[^;]*unsafe-inline/);
    expect(prod).not.toMatch(/(^|; )style-src [^;]*unsafe-inline/);
    for (const d of ["object-src 'none'", "frame-ancestors 'none'", "base-uri 'self'", "form-action 'self'", "upgrade-insecure-requests"]) {
      expect(prod).toContain(d);
    }
  });
  it("only relaxes what development tooling needs", () => {
    const dev = buildCsp("abc123", true);
    expect(dev).toContain("'unsafe-eval'");
    expect(dev).toContain("connect-src 'self' ws: wss:");
    expect(dev).not.toContain("upgrade-insecure-requests");
  });
});

describe("toast store", () => {
  it("publishes, caps and dismisses toasts", () => {
    const seen = vi.fn<(items: ToastItem[]) => void>();
    const unsub = subscribe(seen);
    for (let i = 0; i < 6; i++) toast.success(`n${i}`);
    toast.error("boom");
    const latest = seen.mock.calls.at(-1)![0];
    expect(latest).toHaveLength(4);
    expect(latest.at(-1)).toMatchObject({ kind: "error", message: "boom" });
    dismiss(latest.at(-1)!.id);
    expect(seen.mock.calls.at(-1)![0].some((t) => t.message === "boom")).toBe(false);
    unsub();
  });
});
