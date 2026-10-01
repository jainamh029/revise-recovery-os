"use client";
import { Loader2, RotateCcw, TriangleAlert } from "lucide-react";
import React, { useEffect, useState } from "react";
import { BASE_PATH, DEMO, demoReset, onDemoStatus, startDemo, type DemoStatus } from "@/lib/demo";

function useDemoStatus(): DemoStatus {
  const [s, setS] = useState<DemoStatus>({ stage: "Starting the in-browser engine…", ready: false });
  useEffect(() => { startDemo(); return onDemoStatus(setS); }, []);
  return s;
}

/** Static demo only: shows a loading screen until the in-browser backend is ready. Renders children untouched in normal mode. */
export function DemoGate({ children }: { children: React.ReactNode }) {
  const s = useDemoStatus();
  if (!DEMO || s.ready) return <>{children}</>;
  return (
    <div className="mx-auto mt-16 max-w-xl rounded-2xl border border-line bg-surface p-8 text-center shadow-card" role="status" data-testid="demo-loading">
      {s.error ? <TriangleAlert className="mx-auto h-8 w-8 text-bad" aria-hidden /> : <Loader2 className="mx-auto h-8 w-8 animate-spin text-ink" aria-hidden />}
      <h1 className="mt-4 text-lg font-semibold">{s.error ? "The demo engine could not start" : "Starting the demo engine"}</h1>
      <p className="mt-2 text-sm text-muted" data-testid="demo-stage">{s.error ?? s.stage}</p>
      {!s.error && (
        <p className="mt-4 text-xs leading-relaxed text-muted">
          This is the <b>real backend</b> (Python, FastAPI, SQLAlchemy) running inside your browser via WebAssembly, on synthetic data. The first load downloads about
          15–20&nbsp;MB and takes a few seconds; later visits are cached. Nothing is sent to any server.
        </p>
      )}
      {s.error && <button className="btn btn-primary mt-4" onClick={() => window.location.reload()}>Reload</button>}
    </div>
  );
}

/** Static demo only: explains what this is and offers a reset. */
export function DemoBar() {
  const s = useDemoStatus();
  const [busy, setBusy] = useState(false);
  if (!DEMO) return null;
  return (
    <span className="ml-auto flex items-center gap-2 text-xs" data-testid="demo-bar">
      <span className="hidden sm:inline">Runs entirely in your browser{s.manifest ? ` · data as of ${s.manifest.as_of}` : ""} · changes reset on reload</span>
      <button
        className="inline-flex items-center gap-1 rounded-md border border-warn/40 bg-surface px-2 py-0.5 font-semibold hover:bg-paper disabled:opacity-50"
        disabled={!s.ready || busy}
        data-testid="demo-reset"
        onClick={async () => { setBusy(true); await demoReset(); window.location.assign(`${BASE_PATH}/`); }}
      >
        <RotateCcw className="h-3 w-3" aria-hidden /> Reset demo
      </button>
    </span>
  );
}
