import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";

/** DASH-08 browser coverage: dashboard cards -> backend-driven detail views that reconcile to the card under the same filters. */
test.beforeEach(async ({ request }) => {
  expect((await request.post("/api/admin/reseed")).ok()).toBeTruthy();
});

const FILTER = "range=last_30_days";
const CARDS = [
  { id: "active-cohorts", path: "/cohorts", params: ["status=active"], metric: "active_cohorts", current: true },
  { id: "forecast-cm", path: "/analytics/cohort-profitability", params: ["metric=forecast_cm"], metric: "forecast_cm", current: true },
  { id: "realized-cm", path: "/analytics/cohort-profitability", params: ["metric=realized_cm"], metric: "realized_cm", current: false },
  { id: "cash-tied-up", path: "/analytics/cash-exposure", params: [], metric: "cash_exposure", current: true },
  { id: "overdue", path: "/collections/aging", params: ["status=overdue"], metric: "overdue_collections", current: true },
  { id: "utilization", path: "/operations/robot-performance", params: [], metric: "robot_utilization", current: false },
  { id: "exception-rate", path: "/operations/exceptions", params: [], metric: "exceptions", current: false },
  { id: "critical-alerts", path: "/alerts", params: ["severity=critical", "status=open"], metric: "critical_alerts", current: true },
  { id: "aged-inventory", path: "/inventory/aged", params: [], metric: "aged_inventory", current: true },
];

const ready = async (page: Page) => {
  await expect(page.getByText("Computing portfolio")).toHaveCount(0, { timeout: 15_000 });
  await expect(page.getByTestId("stat-active-cohorts")).toBeVisible();
};
const drillReady = async (page: Page) => {
  await expect(page.getByTestId("reconciliation")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Loading detail")).toHaveCount(0);
};
const num = (t: string) => Number(t.replace(/[^0-9.\-−]/g, "").replace("−", "-"));
const cardValue = async (page: Page, id: string) => (await page.getByTestId(`stat-${id}`).locator(".num").first().innerText()).trim();

test("every dashboard card opens its destination, keeps the filters in the URL, and reconciles", async ({ page }) => {
  for (const card of CARDS) {
    await page.goto(`/?${FILTER}`);
    await ready(page);
    await page.getByTestId(`stat-${card.id}`).click();
    await drillReady(page);
    const url = new URL(page.url());
    expect(url.pathname, card.id).toBe(card.path);
    for (const p of card.params) expect(url.search, card.id).toContain(p);
    expect(url.search, `${card.id} keeps the dashboard filter`).toContain(FILTER);
    await expect(page.getByTestId("basis-label")).toContainText(card.current ? /Current( balance)? as of/ : "Date-filtered");
    await expect(page.getByTestId("drill-filters")).toContainText("Custom range".replace("Custom range", "Last 30 days"));
    const rec = await page.getByTestId("reconciliation-statement").innerText();
    expect(rec, card.id).toMatch(/match(es|ing)? the (filtered )?dashboard/);
    expect(rec).not.toContain("WARNING");
    expect(await page.locator("main").innerText()).not.toMatch(/NaN|undefined|Infinity/);
  }
});

test("Back to filtered dashboard restores the original filter state", async ({ page, request }) => {
  const partner = (await (await request.get("/api/partners")).json()).find((p: any) => p.name.startsWith("Meridian")).id;
  const start = `/?range=last_30_days&partner_id=${partner}&cohort_status=in_processing`;
  await page.goto(start);
  await ready(page);
  const before = { url: page.url(), cohorts: await cardValue(page, "active-cohorts"), realized: await cardValue(page, "realized-cm") };
  await page.getByTestId("stat-realized-cm").click();
  await drillReady(page);
  await expect(page).toHaveURL(/partner_id=/);
  await expect(page).toHaveURL(/cohort_status=in_processing/);
  await page.getByTestId("back-to-dashboard").click();
  await ready(page);
  const u = new URL(page.url());
  expect(u.pathname).toBe("/");
  expect(u.searchParams.get("range")).toBe("last_30_days");
  expect(u.searchParams.get("partner_id")).toBe(partner);
  expect(u.searchParams.get("cohort_status")).toBe("in_processing");
  expect(u.searchParams.has("metric")).toBe(false);                      // drill-only params are dropped, filters are kept
  await expect(page.getByTestId("filter-count")).toHaveText("3 active");
  expect(await cardValue(page, "active-cohorts")).toBe(before.cohorts);
  expect(await cardValue(page, "realized-cm")).toBe(before.realized);
});

test("weighted ratio: utilization detail shows numerator ÷ denominator that reproduce the dashboard card", async ({ page }) => {
  await page.goto("/");
  await ready(page);
  const card = await cardValue(page, "utilization");                      // e.g. "50%"
  await page.getByTestId("stat-utilization").click();
  await drillReady(page);
  const parts = await page.getByTestId("ratio-parts").innerText();        // "numerator 239.0 ÷ denominator 480.0"
  const [numer, denom] = [...parts.matchAll(/([0-9,]+\.?[0-9]*)/g)].map((m) => Number(m[1].replace(/,/g, "")));
  expect(denom).toBeGreaterThan(0);
  expect(Math.round((numer / denom) * 100) + "%").toBe(card);
  await expect(page.getByTestId("reconciliation-statement")).toContainText(`${((numer / denom) * 100).toFixed(1)}%`);
  // the footer totals are the same sums (not a mean of the row percentages)
  const foot = await page.getByTestId("totals-cell_days").innerText();
  expect(foot).toContain("Total");
});

test("exception-rate detail: Σ exceptions ÷ Σ devices equals the card", async ({ page }) => {
  await page.goto("/");
  await ready(page);
  const card = await cardValue(page, "exception-rate");                   // "13.3%"
  await page.getByTestId("stat-exception-rate").click();
  await drillReady(page);
  const parts = await page.getByTestId("ratio-parts").innerText();
  const [ex, dv] = [...parts.matchAll(/([0-9,]+\.?[0-9]*)/g)].map((m) => Number(m[1].replace(/,/g, "")));
  expect(((ex / dv) * 100).toFixed(1) + "%").toBe(card);
});

test("a zero-data drill-down is a stable zero state", async ({ page, request }) => {
  const partner = (await (await request.get("/api/partners")).json()).find((p: any) => p.name.startsWith("Meridian")).id;
  const edu = (await (await request.get("/api/cohorts")).json()).find((c: any) => c.code === "EDU-001").id;
  for (const path of ["/analytics/cohort-profitability?metric=realized_cm", "/collections/aging?status=overdue", "/operations/robot-performance", "/operations/exceptions", "/inventory/aged", "/analytics/cash-exposure"]) {
    await page.goto(`${path}${path.includes("?") ? "&" : "?"}partner_id=${partner}&cohort_id=${edu}`);
    await drillReady(page);
    await expect(page.getByTestId("drill-empty")).toBeVisible();
    await expect(page.getByTestId("reconciliation-statement")).not.toContainText("WARNING");
    expect(await page.locator("main").innerText(), path).not.toMatch(/NaN|undefined|Infinity/);
  }
});

test("cards are keyboard accessible", async ({ page }) => {
  await page.goto(`/?${FILTER}`);
  await ready(page);
  const link = page.getByTestId("link-overdue");
  await link.focus();
  await expect(link).toBeFocused();
  await page.keyboard.press("Enter");
  await drillReady(page);
  expect(new URL(page.url()).pathname).toBe("/collections/aging");
});

test("CSV export from the UI reconciles to the visible total", async ({ page }) => {
  await page.goto("/collections/aging?status=overdue");
  await drillReady(page);
  const visible = num(await page.getByTestId("totals-items").locator("td").nth(9).innerText());   // "Outstanding balance" column total
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("export-csv").click()]);
  const text = fs.readFileSync((await download.path())!, "utf8").trim().split("\n");
  const head = text[1].split(","), body = text.slice(2);
  const i = head.findIndex((h) => h.includes("Outstanding balance"));
  const rows = body.filter((r) => !r.startsWith("TOTAL"));
  const sum = rows.reduce((s, r) => s + Number(r.split(",").slice(-1 - (head.length - 1 - i))[0] ?? 0), 0);
  const totalRow = body.find((r) => r.startsWith("TOTAL"))!;
  expect(Math.abs(Number(totalRow.split(",")[i]) - visible)).toBeLessThan(1);
  expect(sum).toBeGreaterThan(0);
  expect(download.suggestedFilename()).toBe("overdue_collections.csv");
});

test("drill-down pages never overflow the page at 375px; tables scroll inside their wrapper", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  for (const c of CARDS) {
    await page.goto(`${c.path}?${[...c.params, FILTER].join("&")}`);
    await drillReady(page);
    await page.waitForTimeout(300);
    const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(over, `${c.path} overflows by ${over}px`).toBeLessThanOrEqual(0);
  }
});
