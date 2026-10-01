import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

/**
 * Browser acceptance suite. Maps to the acceptance plan IDs in docs/ACCEPTANCE_TESTS.md.
 * Each test starts from the pristine seeded dataset (POST /api/admin/reseed).
 */
const ROUTES = ["/", "/cohorts", "/cohorts/new", "/capacity", "/operations", "/devices", "/resale", "/partners", "/alerts", "/scenarios", "/policy"];

test.beforeEach(async ({ request }) => {
  const r = await request.post("/api/admin/reseed");
  expect(r.ok(), "reseed endpoint requires the API to run with ALLOW_RESEED=true").toBeTruthy();
});

const cohortId = async (request: APIRequestContext, code: string) =>
  (await (await request.get("/api/cohorts")).json()).find((c: any) => c.code === code).id as string;

/** The app's own inline alerts (Next.js adds a hidden route-announcer that also has role=alert). */
const alertBox = (page: Page) => page.locator('[role="alert"]:not(#__next-route-announcer__)');

/** Collects console errors and failed requests so a test can assert the page was clean. */
function watch(page: Page) {
  const problems: string[] = [];
  page.on("console", (m) => m.type() === "error" && problems.push(`console: ${m.text()}`));
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("response", (r) => r.url().includes("/api/") && r.status() >= 400 && problems.push(`http ${r.status()}: ${r.url()}`));
  return problems;
}

test("ENV-02/04/07: every route loads with the disclaimer, no console errors and no failed API calls", async ({ page, request }) => {
  const problems = watch(page);
  const itad = await cohortId(request, "ITAD-002");
  for (const route of [...ROUTES, `/cohorts/${itad}`]) {
    const res = await page.goto(route);
    expect(res?.status(), route).toBe(200);
    await expect(page.getByText(/Illustrative demo · synthetic data/)).toBeVisible();
    await expect(page.getByText("Loading…")).toHaveCount(0, { timeout: 15_000 });
    await expect(page.getByText("Something went wrong")).toHaveCount(0);
  }
  expect(problems).toEqual([]);
});

test("ENV-03: reseed restores the documented baseline", async ({ request }) => {
  const id = await cohortId(request, "EDU-005");
  await request.post(`/api/cohorts/${id}/approve`, { data: { approver: "t", rationale: "mutate" } });
  expect((await (await request.get(`/api/cohorts/${id}`)).json()).status).toBe("approved");
  await request.post("/api/admin/reseed");
  const after = await (await request.get(`/api/cohorts/${await cohortId(request, "EDU-005")}`)).json();
  expect(after.status).toBe("under_review");
});

test("ENV-05: no horizontal overflow at 375px on any route", async ({ page, request }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const itad = await cohortId(request, "ITAD-002");
  const edu5 = await cohortId(request, "EDU-005");
  for (const route of [...ROUTES, `/cohorts/${itad}`, `/cohorts/${edu5}`]) {
    await page.goto(route);
    await expect(page.getByText("Loading…")).toHaveCount(0, { timeout: 15_000 });
    await page.waitForTimeout(400);
    const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(over, `${route} overflows by ${over}px`).toBeLessThanOrEqual(0);
  }
});

test("UAT: approve EDU-005 → plan freezes → overbooking rejected with numbers → split across cells reconciles to zero", async ({ page, request }) => {
  const id = await cohortId(request, "EDU-005");
  await page.goto(`/cohorts/${id}`);
  await expect(page.getByText("System recommendation")).toBeVisible();
  await expect(page.getByText("Accept", { exact: true }).first()).toBeVisible();
  await page.getByPlaceholder("Why accept, modify or decline?").fill("Repeat partner, strong downside, Cell B idle.");
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(page.getByText(/immutable/i)).toBeVisible();
  // APP-06: the frozen budget cannot be edited; a dated change is refused
  const patch = await request.patch(`/api/cohorts/${id}`, { data: { intake_date: "2031-01-01" } });
  expect(patch.status()).toBe(409);

  await page.goto("/capacity");
  await page.getByLabel("Cohort").selectOption({ label: "EDU-005" });
  const cells = page.getByLabel("Robot cell");
  await cells.selectOption({ label: "Cell A — Available" });
  await page.getByRole("button", { name: "Assign", exact: true }).click();
  await expect(alertBox(page)).toContainText(/Overbooked: Cell A has [\d.]+h free .* but [\d.]+h were requested/);   // CAP-02

  await cells.selectOption({ label: "Cell B — Available" });
  await page.getByLabel("Hours to place").fill("150");
  await page.getByRole("button", { name: "Assign", exact: true }).click();
  await expect(alertBox(page)).toContainText("Capacity assigned");
  await cells.selectOption({ label: "Cell C — Available" });
  await page.getByRole("button", { name: "Assign", exact: true }).click();                                                      // default = remaining
  await expect(alertBox(page)).toContainText("Capacity assigned");

  const detail = await (await request.get(`/api/cohorts/${id}`)).json();
  const need = (await (await request.get(`/api/cohorts/${id}/underwriting`)).json()).scenarios.base.scheduled_robot_hours;
  const hours = detail.assignments.reduce((s: number, a: any) => s + Number(a.planned_hours), 0);
  const units = detail.assignments.reduce((s: number, a: any) => s + a.planned_units, 0);
  expect(Math.abs(hours - need)).toBeLessThan(0.011);                                                                           // CAP-03
  expect(units).toBe(400);                                                                                                       // CAP-10
  expect(detail.status).toBe("scheduled");
  const again = await request.post(`/api/cohorts/${id}/assignments`, { data: { robot_cell_id: detail.assignments[0].robot_cell_id, start_date: "2031-01-01", end_date: "2031-01-02" } });
  expect((await again.json()).error).toBe("fully_assigned");                                                                     // remaining = 0
});

test("ITAD-002 deterioration: extra exceptions and cost lower the forecast and leave exactly one critical margin alert", async ({ page, request }) => {
  const id = await cohortId(request, "ITAD-002");
  const before = (await (await request.get(`/api/cohorts/${id}/variance`)).json()).reforecast;
  expect(before.economics.cm_pct).toBeLessThan(0.15);                                                                            // OPS-07: below the 15% live floor (net-revenue basis)
  expect(before.economics.cm_pct).toBeGreaterThan(0.04);
  const cellB = (await (await request.get("/api/robot-cells")).json()).find((c: any) => c.cell_name === "Cell B").id;
  const today = (await (await request.get("/api/drilldown/active_cohorts?verify=false")).json()).as_of;   // the API's "today" (local), not the UTC clock
  for (let i = 0; i < 2; i++) {                                                                                                   // OPS-09: submitted twice
    const r = await request.post("/api/operations/daily", { data: { cohort_id: id, robot_cell_id: cellB, operation_date: today, devices_started: 40, devices_completed: 30,
      first_pass_completions: 12, manual_exception_count: 22, quality_approved_count: 30, productive_robot_hours: 9, repair_parts_cost: 4200, technician_hours: 14 } });
    expect(r.status()).toBe(201);
  }
  const after = (await (await request.get(`/api/cohorts/${id}/variance`)).json()).reforecast;
  expect(after.economics.contribution_margin).toBeLessThan(before.economics.contribution_margin);
  const alerts = (await (await request.get("/api/alerts")).json()).filter((a: any) => a.cohort_id === id && ["CRITICAL_MARGIN_INTERVENTION", "STOP_LOSS_MAKING"].includes(a.alert_type));
  expect(alerts).toHaveLength(1);
  expect(alerts[0].severity).toBe("critical");
  await page.goto(`/cohorts/${id}`);
  await expect(page.getByText("Reforecast CM")).toBeVisible();
  await expect(page.getByText("Why the forecast moved")).toBeVisible();
  await page.goto("/alerts");
  await expect(page.getByText(/ITAD-002: forecast margin/)).toHaveCount(1);
});

test("ENT-003: margin stays positive while the overdue-collection alert stays active", async ({ page, request }) => {
  const id = await cohortId(request, "ENT-003");
  const a = await (await request.get(`/api/cohorts/${id}/actuals`)).json();
  expect(a.contribution_margin).toBeGreaterThan(0);
  expect(a.cash_exposure).toBeGreaterThan(0);
  await page.goto(`/cohorts/${id}`);
  await page.getByRole("tab", { name: "Financials" }).click();
  await expect(page.getByText("Cash exposure")).toBeVisible();
  await expect(page.getByText("Closeout readiness")).toBeVisible();
  await expect(page.getByText(/not yet collected/)).toBeVisible();
  await page.goto("/alerts");
  await expect(page.getByText(/ENT-003: \$[\d,]+ of resale payouts overdue/)).toBeVisible();
  await expect(page.getByText(/Invoice INV-NBF-0417 overdue/)).toBeVisible();
});

test("COC: a device cannot be listed before sanitization and QC; after both it lists", async ({ page, request }) => {
  const id = await cohortId(request, "ITAD-002");
  const listing = await (await request.post("/api/resale-listings", { data: { cohort_id: id, listing_channel: "E2E channel", listing_date: new Date().toISOString().slice(0, 10), listing_price: 200, device_count: 5 } })).json();
  const serial = `E2E-${Date.now()}`;
  const dev = await (await request.post("/api/devices", { data: { cohort_id: id, serial_number: serial, manufacturer: "Dell", model: "Latitude 5490" } })).json();

  // backend refuses: never sanitized / never QC'd
  const blocked = await request.post(`/api/devices/${dev.id}/list`, { data: { resale_listing_id: listing.id } });
  expect(blocked.status()).toBe(409);
  expect((await blocked.json()).error).toBe("listing_gate");

  await page.goto("/devices");
  await page.getByPlaceholder("search…").fill(serial);
  await page.getByRole("cell", { name: serial }).click();
  await expect(page.getByRole("button", { name: "List device" })).toHaveCount(0);   // UI offers no listing action at this stage
  await page.getByRole("button", { name: "Triage" }).click();
  await page.getByRole("button", { name: "Queue for sanitization" }).click();
  await page.getByRole("button", { name: "Record pass" }).click();                     // certificate is required
  await expect(alertBox(page)).toContainText(/certificate/i);
  await page.getByPlaceholder("e.g. CERT-2026-000123").fill("CERT-E2E-1");
  await page.getByRole("button", { name: "Record pass" }).click();
  await page.getByRole("button", { name: "Robot processing" }).click();
  await page.getByRole("button", { name: "Send to QC" }).click();
  await page.getByRole("button", { name: "QC pass" }).click();
  await page.getByRole("button", { name: "Designate for resale" }).click();
  await expect(page.getByText(/Disposition: RESALE/i)).toBeVisible();
  await page.getByLabel("List on").selectOption({ value: listing.id });
  await page.getByRole("button", { name: "List device" }).click();
  await expect(page.locator("span", { hasText: /^listed$/i }).first()).toBeVisible();
  const final = await (await request.get(`/api/devices/${dev.id}`)).json();
  expect(final.current_status).toBe("LISTED");
  expect(final.custody_events.map((e: any) => e.event_type)).toEqual(expect.arrayContaining(["received", "sanitization", "quality_control", "listing"]));
});

test("COC-05: a failed wipe quarantines the device, raises one critical alert and blocks resale", async ({ request }) => {
  const id = await cohortId(request, "ITAD-002");
  const listing = (await (await request.get(`/api/resale-listings?cohort_id=${id}`)).json())[0];
  const dev = await (await request.post("/api/devices", { data: { cohort_id: id, serial_number: `BAD-${Date.now()}` } })).json();
  for (const to of ["TRIAGED", "AWAITING_SANITIZATION"]) await request.post(`/api/devices/${dev.id}/advance`, { data: { to } });
  const out = await (await request.post(`/api/devices/${dev.id}/sanitization`, { data: { passed: false } })).json();
  expect(out.current_status).toBe("QUARANTINED");
  const before = (await (await request.get("/api/alerts")).json()).filter((a: any) => a.alert_type === "SANITIZATION_FAILURE");
  expect(before.length).toBe(2);      // seeded quarantined device + this one; one alert per device
  for (const [path, data] of [["list", { resale_listing_id: listing.id }], ["advance", { to: "IN_ROBOT_PROCESSING" }], ["sell", { sales_transaction_id: "x" }]] as const) {
    expect((await request.post(`/api/devices/${dev.id}/${path}`, { data })).status()).toBeGreaterThanOrEqual(409);
  }
});

test("Mobile smoke: primary routes work at 375px", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  await expect(page.getByText("Executive dashboard")).toBeVisible();
  await page.getByLabel("Open menu").click();
  await page.getByRole("link", { name: "Underwriting" }).click();
  await expect(page.getByText("Cohort underwriting")).toBeVisible();
  await page.getByLabel("Open menu").click();
  await page.getByRole("link", { name: "Capacity" }).click();
  await expect(page.getByText("Capacity planner")).toBeVisible();
  await page.getByLabel("Open menu").click();
  await page.getByRole("link", { name: /Alerts/ }).click();
  await expect(page.getByText("Action queue")).toBeVisible();
});
