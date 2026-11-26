import { defineConfig, devices } from "@playwright/test";

// E2E runs against a real backend (FastAPI) and the Next.js app. Start both with `npm run dev:all`
// from the repo root, or let Playwright start them (set PW_START_SERVERS=1).
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
  ],
  webServer: process.env.PW_START_SERVERS
    ? [
        { command: "cd ../backend && uv run uvicorn app.main:app --port 8000", url: "http://127.0.0.1:8000/api/v1/health", reuseExistingServer: true },
        { command: "npm run dev", url: "http://localhost:3000/login", reuseExistingServer: true },
      ]
    : undefined,
});
