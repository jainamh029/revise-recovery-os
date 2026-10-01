import { DEMO, demoRequest } from "./demo";

/** Download a backend-generated file (CSV / Markdown). Works against the real API and against the in-browser backend. */
export async function downloadFile(apiPath: string, filename: string): Promise<void> {
  let text: string;
  if (DEMO) {
    const r = await demoRequest("GET", apiPath);
    if (r.status >= 400) throw new Error(`Export failed (${r.status})`);
    text = r.text;
  } else {
    const r = await fetch(apiPath, { cache: "no-store" });
    if (!r.ok) throw new Error(`Export failed (${r.status})`);
    text = await r.text();
  }
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Fetch plain text from the backend (used for the memo preview). */
export async function fetchText(apiPath: string): Promise<string> {
  if (DEMO) return (await demoRequest("GET", apiPath)).text;
  return (await fetch(apiPath, { cache: "no-store" })).text();
}
