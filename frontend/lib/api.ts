import { DEMO, demoRequest } from "./demo";

export class ApiError extends Error {
  constructor(message: string, public code: string, public status: number, public details?: Record<string, unknown>) {
    super(message);
  }
}

/** All business logic lives in the FastAPI backend; this only transports requests. */
export async function api<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  let status: number, text: string;
  const method = opts.method ?? (opts.body ? "POST" : "GET");
  if (DEMO) {  // static demo: the backend runs in a Web Worker inside this browser tab
    const r = await demoRequest(method, path, opts.body);
    status = r.status; text = r.text;
  } else {
    const res = await fetch(path, {
      method,
      headers: opts.body ? { "Content-Type": "application/json" } : undefined,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      cache: "no-store",
    });
    status = res.status; text = await res.text();
  }
  const ok = status >= 200 && status < 300;
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!ok) {
    if (data && data.message) throw new ApiError(data.message, data.error ?? "error", status, data.details);
    if (data && Array.isArray(data.detail)) {
      const msg = data.detail
        .map((d: any) => {
          const field = (d.loc || []).slice(1).join(".");
          const text = String(d.msg).replace(/^Value error, /, "");
          return field ? `${field}: ${text}` : text;
        })
        .join("; ");
      throw new ApiError(msg, "validation", status);
    }
    throw new ApiError(typeof data === "string" && data ? data : `Request failed (${status})`, "error", status);
  }
  return data as T;
}
