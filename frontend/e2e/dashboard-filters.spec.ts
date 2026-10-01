import { expect, test, type Page } from "@playwright/test";

/** DASH-07 browser coverage. Filters live only in the URL; the backend does all filtering. */
test.beforeEach(async ({ request }) => {
  expect((await request.post("/api/admin/reseed")).ok()).toBeTruthy();
});

const stat = (page: Page, id: string) => page.getByTestId(`stat-${id}`);
const val = async (page: Page, id: string) => (await stat(page, id).locator(".num").first().innerText()).trim();
const ready = async (page: Page) => {
  await expect(page.getByText("Computing portfolio")).toHaveCount(0, { timeout: 15_000 });
  await expect(stat(page, "active-cohorts")).toBeVisible();
};
const SNAP = ["active-cohorts", "forecast-cm", "realized-cm", "cash-tied-up", "cash-collected", "utilization", "throughput", "exception-rate", "aged-inventory", "overdue", "decisions"];
const snapshot = async (page: Page) => Object.fromEntries(await Promise.all(SNAP.map(async (k) => [k, await val(page, k)])));

test("selecting a filter writes it to the URL, re-queries the backend, and survives a refresh", async ({ page }) => {
  await page.goto("/");
  await ready(page);
  expect(await val(page, "active-cohorts")).toBe("3");
  const q = page.waitForResponse((r) => r.url().includes("/api/dashboard/executive?") && r.url().includes("partner_id="));   // backend receives the filter
  await page.getByTestId("filter-partner").selectOption({ label: "Meridian ITAD Services" });
  expect((await q).status()).toBe(200);
  await expect(page).toHaveURL(/partner_id=/);
  await ready(page);
  expect(await val(page, "active-cohorts")).toBe("1");                                     // ITAD-002 only
  await expect(page.getByTestId("filter-count")).toHaveText("1 active");
  await expect(page.getByTestId("applied-summary")).toContainText("Partner: Meridian ITAD Services");
  const before = await snapshot(page);
  await page.reload();                                                                      // refresh
  await ready(page);
  await expect(page).toHaveURL(/partner_id=/);
  await expect(page.getByTestId("filter-partner")).toHaveValue(/.+/);
  expect(await snapshot(page)).toEqual(before);
  await expect(page.getByTestId("filter-count")).toHaveText("1 active");
});

test("a multi-filter combination intersects, persists across refresh, and is shareable as a URL", async ({ page }) => {
  await page.goto("/");
  await ready(page);
  await page.getByTestId("filter-partner").selectOption({ label: "Meridian ITAD Services" });
  await page.getByTestId("filter-cell").selectOption({ label: "Cell D" });
  await page.getByTestId("filter-range").selectOption("last_30_days");
  await page.getByTestId("filter-status").selectOption("in_processing");
  await ready(page);
  await expect(page.getByTestId("filter-count")).toHaveText("4 active");
  expect(await val(page, "active-cohorts")).toBe("1");
  const url = page.url();
  expect(url).toMatch(/partner_id=.*robot_cell_id=|robot_cell_id=.*partner_id=/);
  const snap = await snapshot(page);
  const fresh = await page.context().newPage();                                              // "share the link": a brand-new tab
  await fresh.goto(url);
  await ready(fresh);
  expect(await snapshot(fresh)).toEqual(snap);
  await expect(fresh.getByTestId("filter-count")).toHaveText("4 active");
  await fresh.close();
});

test("Clear filters restores the exact global dashboard", async ({ page }) => {
  await page.goto("/");
  await ready(page);
  const global = await snapshot(page);
  await page.getByTestId("filter-cohort").selectOption({ label: "EDU-001" });
  await ready(page);
  expect(await snapshot(page)).not.toEqual(global);
  await page.getByTestId("clear-filters").click();
  await expect(page).toHaveURL(/\/$/);
  await ready(page);
  expect(await snapshot(page)).toEqual(global);
  await expect(page.getByTestId("filter-count")).toHaveText("0 active");
});

test("a no-match combination shows an explained zero state: no NaN, no broken layout", async ({ page }) => {
  await page.goto("/");
  await ready(page);
  await page.getByTestId("filter-partner").selectOption({ label: "Meridian ITAD Services" });
  await page.getByTestId("filter-cohort").selectOption({ label: "EDU-001" });                // disjoint: intersection is empty
  await expect(page.getByTestId("empty-state")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("empty-state")).toContainText("No cohorts match");
  expect(await val(page, "active-cohorts")).toBe("0");
  expect(await val(page, "realized-cm")).toBe("$0");
  expect(await val(page, "overdue")).toBe("$0");
  const text = await page.locator("main").innerText();
  expect(text).not.toMatch(/NaN|undefined|Infinity|null/);
  await expect(page.getByText("Critical action queue")).toBeVisible();
  await expect(page.getByText("Nothing needs action.")).toBeVisible();
});

test("invalid date ranges are blocked with understandable feedback (UI and direct URL)", async ({ page }) => {
  await page.goto("/");
  await ready(page);
  await page.getByTestId("filter-range").selectOption("custom");
  await page.getByTestId("filter-start").fill("2026-09-25");
  await page.getByTestId("filter-end").fill("2026-09-01");
  await expect(page.getByTestId("date-error")).toContainText("End date cannot be before the start date");
  await expect(page).not.toHaveURL(/end_date=2026-09-01/);                                   // the bad range never reaches the backend
  await page.goto("/?start_date=2026-09-25&end_date=2026-09-01");                            // hand-edited URL: server-side validation
  await expect(page.getByText("end_date cannot be before start_date")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("clear-filters")).toBeEnabled();                              // user can recover
  await page.goto("/?partner_id=not-a-real-id");
  await expect(page.getByText(/Unknown partner_id/)).toBeVisible({ timeout: 15_000 });
});

test("dated and current-state metrics are labelled as such", async ({ page }) => {
  await page.goto("/?range=last_7_days");
  await ready(page);
  await expect(page.getByTestId("basis-note")).toContainText("Current");
  await expect(stat(page, "cash-tied-up")).toContainText("Current");
  await expect(stat(page, "aged-inventory")).toContainText("Current");
  await expect(stat(page, "cash-collected")).toContainText("Receipts");
});
