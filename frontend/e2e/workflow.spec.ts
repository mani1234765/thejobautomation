import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// One account walks the whole product: onboarding → discovery (real Ashby board) → application →
// approval → assisted submission. Requires the API and web app to be running (see playwright.config.ts).
test.describe.configure({ mode: "serial" });

const email = `e2e-${Date.now()}@example.com`;
const password = "correct-horse-42";
let page: Page;
const cspViolations: string[] = [];

test.beforeAll(async ({ browser }) => {
  const context = await browser.newContext({ baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000", viewport: { width: 1440, height: 900 } });
  // Record every Content-Security-Policy violation across the whole journey.
  await context.exposeBinding("__cspViolation", (_src, v: string) => { cspViolations.push(v); });
  await context.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (e) =>
      (window as unknown as { __cspViolation: (v: string) => void }).__cspViolation(`${e.violatedDirective} ${e.blockedURI}`));
  });
  page = await context.newPage();
});

async function axe(p: Page) {
  const result = await new AxeBuilder({ page: p }).withTags(["wcag2a", "wcag2aa"]).disableRules(["color-contrast"]).analyze();
  expect(result.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
}

test("unauthenticated users are redirected to sign in", async () => {
  await page.goto("/overview");
  await expect(page).toHaveURL(/\/login\?next=%2Foverview/);
  await axe(page);
});

test("register and complete onboarding from a resume", async () => {
  await page.goto("/register");
  await page.fill("#full_name", "Asha Verma");
  await page.fill("#email", email);
  await page.fill("#password", password);
  await page.click("button[type=submit]");
  await expect(page).toHaveURL(/\/onboarding/);

  await page.setInputFiles("#resume-file", "e2e/fixtures/sample-resume.docx");
  await expect(page.getByText("Resume imported")).toBeVisible();
  await expect(page.getByText(/Found 3 roles/)).toBeVisible();
  await page.getByRole("button", { name: /Continue/ }).click();
  await expect(page.locator("#full_name")).toHaveValue("Asha Verma");
  await expect(page.locator("#phone")).toHaveValue(/98765/);
  await page.getByRole("button", { name: /Continue/ }).click();

  // Target titles are required before continuing.
  await expect(page.getByRole("button", { name: /Continue/ })).toBeDisabled();
  await page.fill("#titles", "Software Engineer");
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "3–5 yrs" }).click();
  await page.getByRole("button", { name: /Continue/ }).click();
  await page.getByRole("button", { name: "Hybrid" }).click();
  await page.getByRole("button", { name: "Full-time" }).click();
  await page.getByRole("button", { name: /Continue/ }).click();
  await expect(page.getByText("Review what we found")).toBeVisible();
  await page.getByRole("button", { name: /Confirm & finish/ }).click();
  await expect(page).toHaveURL(/\/overview/);
  await expect(page.getByRole("heading", { name: /Good (morning|afternoon|evening), Asha/ })).toBeVisible();
  await axe(page);
});

test("review and correct the extracted resume; corrections persist and survive re-extraction", async () => {
  await page.goto("/resume");
  await expect(page.getByRole("tab", { name: "Review & edit" })).toHaveAttribute("aria-selected", "true");
  const experience = page.getByTestId("review-experience");
  await expect(experience).toHaveCount(3);

  // Edit: the change shows as saved only after the server confirms it.
  await page.getByRole("button", { name: "Edit Associate Developer · Nimbus Labs" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel(/Job title/).fill("Associate Software Developer");
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Correction saved")).toBeVisible();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("Associate Software Developer · Nimbus Labs")).toBeVisible();

  // Move it to another section and back (e.g. education misfiled as experience).
  await page.getByRole("button", { name: "More actions for Associate Software Developer · Nimbus Labs" }).click();
  await page.getByRole("menuitem", { name: /Move to/ }).click();
  await page.getByRole("dialog").getByLabel("Section").selectOption("education");
  await axe(page);
  await page.getByRole("dialog").getByRole("button", { name: "Move" }).click();
  await expect(page.getByText("Moved to Education")).toBeVisible();
  await expect(experience).toHaveCount(2);
  await expect(page.getByTestId("review-education")).toHaveCount(2);

  // Corrections persist across a reload.
  await page.reload();
  await expect(page.getByTestId("review-education")).toHaveCount(2);
  await expect(experience).toHaveCount(2);

  // Re-extraction previews first and keeps corrections.
  await page.getByRole("button", { name: /Re-extract/ }).click();
  await expect(page.getByText("Your corrections are protected")).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "Apply" }).click();
  await expect(page.getByText(/Re-extracted\. Your corrections were kept/)).toBeVisible();
  await expect(experience).toHaveCount(2);
  await expect(page.getByTestId("review-education")).toHaveCount(2);

  // Move it back so the rest of the journey sees the original experience.
  const moved = page.getByTestId("review-education").filter({ hasText: "Nimbus" });
  await moved.getByRole("button", { name: /More actions for/ }).click();
  await page.getByRole("menuitem", { name: /Move to/ }).click();
  await page.getByRole("dialog").getByLabel("Section").selectOption("experience:professional");
  await page.getByRole("dialog").getByRole("button", { name: "Move" }).click();
  await expect(experience).toHaveCount(3);
  await axe(page);
});

test("follow a real job board and see explained matches", async () => {
  await page.goto("/jobs?sources=1");
  await page.getByPlaceholder("stripe").fill("https://jobs.ashbyhq.com/ramp");
  await page.getByRole("button", { name: "Follow" }).click();
  await expect(page.getByText(/open roles · synced/)).toBeVisible({ timeout: 60_000 });
  await page.keyboard.press("Escape");
  const first = page.getByRole("button", { name: / at Ramp$/ }).first();
  await expect(first).toBeVisible();
  await first.click();
  await expect(page.getByText("Why this score")).toBeVisible();
  await expect(page.getByText("Job description")).toBeVisible();
  await axe(page);
});

test("prepare, approve and record a submission", async () => {
  await page.getByRole("button", { name: /Prepare application/ }).click();
  await expect(page).toHaveURL(/\/applications\/[a-f0-9]+$/);
  await page.getByRole("button", { name: /Prepare application/ }).click();
  await expect(page.getByText("Answers", { exact: true })).toBeVisible();

  // Fill every answer JobPilot couldn't know — it never guesses.
  const needsYou = () => page.getByText("Needs you", { exact: true }).filter({ visible: true });
  const pending = await needsYou().count();
  expect(pending).toBeGreaterThan(0);
  for (let i = 0; i < pending; i++) {
    const row = page.getByRole("listitem").filter({ has: needsYou() }).first();
    await row.getByRole("button", { name: /Edit answer/ }).click();
    await row.getByRole("textbox").fill("Provided by me");
    await row.getByRole("button", { name: "Save" }).click();
    await expect(needsYou()).toHaveCount(pending - i - 1);
  }
  await expect(page.getByText("All reviewed")).toBeVisible();
  await page.getByRole("button", { name: /Request approval/ }).click();
  await expect(page.getByText("Your approval is needed")).toBeVisible();
  await axe(page);
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText("Submit on the employer's site")).toBeVisible();

  const popup = page.waitForEvent("popup");
  await page.getByRole("button", { name: /Open form & start/ }).click();
  await (await popup).close();
  await page.getByRole("button", { name: "I submitted it" }).click();
  await page.fill("#ref", "E2E-CONF-1");
  await page.getByRole("button", { name: "Confirm submitted" }).click();
  await expect(page.getByText("Submitted", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/Submitted .* · E2E-CONF-1/)).toBeVisible();

  await page.goto("/overview");
  const submitted = page.locator("a", { hasText: "Submitted" }).first();
  await expect(submitted).toContainText("1");
});

test("command palette navigates by keyboard", async () => {
  await page.goto("/overview");
  await page.keyboard.press("Meta+k");
  await page.getByPlaceholder("Search pages and actions…").fill("analytics");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/analytics/);
  await expect(page.getByRole("heading", { name: "Analytics" })).toBeVisible();
  await axe(page);
});

for (const path of ["/resume", "/applications", "/assistant", "/interviews", "/communications", "/automation", "/integrations", "/notifications", "/settings"]) {
  test(`renders ${path} without accessibility violations`, async () => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(path);
    await expect(page.locator("main h1")).toBeVisible();
    await axe(page);
    expect(errors).toEqual([]);
  });
}

test("responses carry a strict, per-request nonce CSP", async ({ request }) => {
  const a = (await request.get("/login")).headers()["content-security-policy"];
  const b = (await request.get("/login")).headers()["content-security-policy"];
  expect(a).toContain("default-src 'self'");
  expect(a).toContain("'strict-dynamic'");
  expect(a).toContain("object-src 'none'");
  expect(a).toContain("frame-ancestors 'none'");
  expect(a).not.toMatch(/script-src[^;]*'unsafe-inline'/);
  const nonce = (h: string) => /'nonce-([^']+)'/.exec(h)?.[1];
  expect(nonce(a)).toBeTruthy();
  expect(nonce(a)).not.toEqual(nonce(b));
  expect((await request.get("/login")).headers()["strict-transport-security"]).toContain("max-age=31536000");
});

test("no CSP violations occurred during the journey", async () => {
  // In development React needs eval for debugging, which the dev policy allows; production must be clean.
  expect(cspViolations).toEqual([]);
});
