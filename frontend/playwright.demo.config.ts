import { defineConfig } from "@playwright/test";

// Tests the STATIC GitHub Pages build (backend running in the browser). Needs frontend/out (scripts/build_pages.sh) and internet
// (the browser downloads the Pyodide runtime from jsDelivr). DEMO_URL points the suite at an already-deployed site instead.
const PORT = Number(process.env.DEMO_PORT ?? 4173);
const URL = process.env.DEMO_URL ?? `http://localhost:${PORT}/revise-recovery-os/`;

export default defineConfig({
  testDir: "./e2e-demo",
  timeout: 180_000,
  workers: 1,
  retries: 0,
  use: { baseURL: URL.endsWith("/") ? URL : URL + "/", trace: "retain-on-failure", screenshot: "only-on-failure" },
  reporter: [["list"]],
  webServer: process.env.DEMO_URL ? undefined : { command: `bash ../scripts/serve_demo.sh ${PORT}`, url: URL, timeout: 30_000, reuseExistingServer: false },
});
