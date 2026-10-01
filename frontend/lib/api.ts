export class ApiError extends Error {
  constructor(message: string, public code: string, public status: number, public details?: Record<string, unknown>) {
    super(message);
  }
}

/** All business logic lives in the FastAPI backend; this only transports requests. */
export async function api<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const res = await fetch(path, {
    method: opts.method ?? (opts.body ? "POST" : "GET"),
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    cache: "no-store",
  });
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    if (data && data.message) throw new ApiError(data.message, data.error ?? "error", res.status, data.details);
    if (data && Array.isArray(data.detail)) {
      const msg = data.detail
        .map((d: any) => {
          const field = (d.loc || []).slice(1).join(".");
          const text = String(d.msg).replace(/^Value error, /, "");
          return field ? `${field}: ${text}` : text;
        })
        .join("; ");
      throw new ApiError(msg, "validation", res.status);
    }
    throw new ApiError(typeof data === "string" && data ? data : `Request failed (${res.status})`, "error", res.status);
  }
  return data as T;
}
