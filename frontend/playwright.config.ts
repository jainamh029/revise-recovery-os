import { defineConfig } from "@playwright/test";

// The suite runs against a PRODUCTION build (own build dir, port 3100) talking to the seeded API on :8010.
// API must be running with ALLOW_RESEED=true so each run can restore the pristine demo state.
const PORT = Number(process.env.E2E_PORT ?? 3100);

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  workers: 1, // tests share one database and reseed it
  retries: 0,
  use: { baseURL: process.env.WEB_URL ?? `http://localhost:${PORT}`, trace: "retain-on-failure", screenshot: "only-on-failure" },
  reporter: [["list"], ["json", { outputFile: "test-results/e2e.json" }]],
  webServer: process.env.WEB_URL
    ? undefined
    : {
        command: `NEXT_DIST_DIR=.next-e2e npx next build && NEXT_DIST_DIR=.next-e2e npx next start -p ${PORT}`,
        url: `http://localhost:${PORT}`,
        timeout: 240_000,
        reuseExistingServer: false,
        env: { API_URL: process.env.API_URL ?? "http://127.0.0.1:8010" },
      },
});
