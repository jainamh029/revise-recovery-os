import { expect, test, type Page } from "@playwright/test";

/** The static demo: the real backend runs in the browser. Each test boots a fresh engine (fresh in-memory data). */
const stat = (page: Page, id: string) => page.getByTestId(`stat-${id}`);
const val = async (page: Page, id: string) => (await stat(page, id).locator(".num").first().innerText()).trim();
async function open(page: Page, path = "") {
  await page.goto(path);
  await expect(page.getByText("Illustrative demo · synthetic data")).toBeVisible();
  await expect(page.getByTestId("demo-loading")).toHaveCount(0, { timeout: 150_000 });   // engine boot (downloads runtime on first load)
}

test("boots the in-browser backend and shows the synthetic dashboard", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await open(page);
  await expect(page.getByText("Executive dashboard")).toBeVisible({ timeout: 60_000 });
  await expect(stat(page, "active-cohorts")).toBeVisible({ timeout: 60_000 });
  expect(await val(page, "active-cohorts")).toBe("3");
  await expect(page.getByTestId("demo-bar")).toContainText("Runs entirely in your browser");
  await expect(page.getByText("Illustrative demo · synthetic data")).toBeVisible();
  expect(await page.locator("main").innerText()).not.toMatch(/NaN|undefined|Infinity/);
  expect(errors).toEqual([]);
});

test("filters, drill-down reconciliation, CSV export and back-link work client-side", async ({ page }) => {
  await open(page);
  await expect(stat(page, "active-cohorts")).toBeVisible({ timeout: 60_000 });
  await page.getByTestId("filter-partner").selectOption({ label: "Meridian ITAD Services" });
  await expect(page).toHaveURL(/partner_id=/);
  await expect(page.getByTestId("filter-count")).toHaveText("1 active");
  await expect.poll(() => val(page, "active-cohorts"), { timeout: 30_000 }).toBe("1");
  await stat(page, "realized-cm").click();
  await expect(page.getByTestId("reconciliation-statement")).toContainText("matching the filtered dashboard total", { timeout: 60_000 });
  await expect(page).toHaveURL(/partner_id=/);
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("export-csv").click()]);
  expect(download.suggestedFilename()).toBe("realized_cm.csv");
  await page.getByTestId("back-to-dashboard").click();
  await expect(page.getByTestId("filter-count")).toHaveText("1 active");
  await expect.poll(() => val(page, "active-cohorts"), { timeout: 30_000 }).toBe("1");
});

test("approve EDU-005, hit the backend's over-capacity rejection, then split across cells", async ({ page }) => {
  await open(page, "cohorts/");
  await page.getByRole("link", { name: "EDU-005" }).click();
  await expect(page.getByText("System recommendation")).toBeVisible({ timeout: 60_000 });
  await page.getByPlaceholder("Why accept, modify or decline?").fill("Demo approval.");
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(page.getByText(/immutable/i)).toBeVisible({ timeout: 30_000 });

  // client-side navigation: a full page load would (by design) reset the in-memory demo data
  await page.getByRole("link", { name: "Capacity", exact: true }).click();
  await page.getByLabel("Cohort").selectOption({ label: "EDU-005" });
  await page.getByLabel("Robot cell").selectOption({ label: "Cell A — Available" });
  await page.getByRole("button", { name: "Assign", exact: true }).click();
  await expect(page.locator('[role="alert"]:not(#__next-route-announcer__)')).toContainText(/Overbooked: Cell A has [\d.]+h free .* but [\d.]+h were requested/, { timeout: 30_000 });
  await page.getByLabel("Robot cell").selectOption({ label: "Cell B — Available" });
  await page.getByLabel("Hours to place").fill("150");
  await page.getByRole("button", { name: "Assign", exact: true }).click();
  await expect(page.locator('[role="alert"]:not(#__next-route-announcer__)')).toContainText("Capacity assigned", { timeout: 30_000 });
});

test("a cohort deep link works after a refresh, and Reset demo restores the pristine data", async ({ page }) => {
  await open(page, "cohorts/");
  await page.getByRole("link", { name: "EDU-005" }).click();
  await expect(page.getByText("System recommendation")).toBeVisible({ timeout: 60_000 });
  await expect(page).toHaveURL(/\/cohort\/\?id=/);
  await page.getByPlaceholder("Why accept, modify or decline?").fill("x");
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(page.getByText(/immutable/i)).toBeVisible({ timeout: 30_000 });
  await page.reload();                                                                  // a full reload restarts the engine: data is pristine again
  await expect(page.getByTestId("demo-loading")).toHaveCount(0, { timeout: 150_000 });
  await expect(page.getByText("System recommendation")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/immutable/i)).toHaveCount(0);
  await page.getByTestId("demo-reset").click();                                         // explicit reset
  await expect(page.getByTestId("demo-loading")).toHaveCount(0, { timeout: 150_000 });
  await expect(page.getByText("Executive dashboard")).toBeVisible({ timeout: 60_000 });
});
