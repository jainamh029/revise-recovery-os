/**
 * Static-demo mode (GitHub Pages). The REAL FastAPI backend runs inside the browser (Pyodide / WebAssembly, in a Web Worker)
 * against a pre-seeded synthetic SQLite database. `api()` sends requests to that worker instead of the network.
 * Nothing here is used in normal (server) mode.
 */
export const DEMO = process.env.NEXT_PUBLIC_DEMO === "1";
export const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

export type DemoStatus = { stage: string; ready: boolean; error?: string; manifest?: { as_of: string; built_at: string; pyodide: string } };
type Pending = { resolve: (v: { status: number; text: string }) => void; reject: (e: Error) => void };

let worker: Worker | null = null;
let seq = 0;
const pending = new Map<number, Pending>();
const listeners = new Set<(s: DemoStatus) => void>();
let status: DemoStatus = { stage: "Starting the in-browser engine…", ready: false };
let readyPromise: Promise<void> | null = null;

const publish = (s: Partial<DemoStatus>) => { status = { ...status, ...s }; listeners.forEach((l) => l(status)); };
export const demoStatus = () => status;
export const onDemoStatus = (fn: (s: DemoStatus) => void) => { listeners.add(fn); fn(status); return () => { listeners.delete(fn); }; };

function boot(): Promise<void> {
  if (readyPromise) return readyPromise;
  readyPromise = new Promise<void>((resolve, reject) => {
    worker = new Worker(`${BASE_PATH}/demo/worker.js`, { type: "module" });
    worker.onmessage = (ev) => {
      const m = ev.data;
      if (m.type === "progress") publish({ stage: m.stage });
      else if (m.type === "ready") { publish({ stage: "Ready", ready: true, manifest: m.manifest }); resolve(); }
      else if (m.type === "fatal") { publish({ stage: "Failed to start", error: m.error }); reject(new Error(m.error)); }
      else if (m.type === "response") { const p = pending.get(m.id); if (p) { pending.delete(m.id); p.resolve({ status: m.status, text: m.body }); } }
    };
    worker.onerror = (e) => { publish({ stage: "Failed to start", error: e.message }); reject(new Error(e.message)); };
    worker.postMessage({ type: "init", base: `${BASE_PATH}/demo/` });
  });
  return readyPromise;
}

/** Send one API request to the in-browser backend. Same shape as fetch for the pieces `api()` needs. */
export async function demoRequest(method: string, path: string, body?: unknown): Promise<{ status: number; text: string }> {
  await boot();
  const id = ++seq;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    worker!.postMessage({ type: "request", id, method, url: path, body: body === undefined ? null : JSON.stringify(body) });
  });
}

/** Restore the pristine synthetic dataset (discard everything done in this session). */
export async function demoReset(): Promise<void> {
  await boot();
  const id = ++seq;
  await new Promise<void>((resolve) => { pending.set(id, { resolve: () => resolve(), reject: () => resolve() }); worker!.postMessage({ type: "reset", id }); });
}

export function startDemo() { if (DEMO && typeof window !== "undefined") void boot().catch(() => {}); }
