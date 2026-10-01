export const n = (v: number | null | undefined, d = 0) =>
  v == null || Number.isNaN(v) ? "—" : v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

export const money = (v: number | null | undefined, opts: { compact?: boolean; cents?: boolean } = {}) => {
  if (v == null || Number.isNaN(v)) return "—";
  const neg = v < 0;
  const a = Math.abs(v);
  let s: string;
  if (opts.compact && a >= 1_000_000) s = `$${(a / 1_000_000).toFixed(2)}M`;
  else if (opts.compact && a >= 10_000) s = `$${(a / 1000).toFixed(1)}k`;
  else s = `$${a.toLocaleString("en-US", { minimumFractionDigits: opts.cents ? 2 : 0, maximumFractionDigits: opts.cents ? 2 : 0 })}`;
  return neg ? `−${s}` : s;
};

export const pct = (v: number | null | undefined, d = 1) => (v == null || Number.isNaN(v) ? "—" : `${(v * 100).toFixed(d)}%`);

export const signedMoney = (v: number | null | undefined, compact = true) =>
  v == null ? "—" : `${v >= 0 ? "+" : "−"}${money(Math.abs(v), { compact })}`;

export const signedPct = (v: number | null | undefined, d = 0) =>
  v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(d)}%`;

export const dateShort = (iso?: string | null) => {
  if (!iso) return "—";
  const d = new Date(iso + (iso.length === 10 ? "T00:00:00" : ""));
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
};

export const titleCase = (s?: string | null) => (s ? s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : "—");

export const DECISION: Record<string, { label: string; tone: Tone }> = {
  accept: { label: "Accept", tone: "good" },
  accept_with_conditions: { label: "Accept with conditions", tone: "warn" },
  review: { label: "Review required", tone: "info" },
  decline: { label: "Decline", tone: "bad" },
};

export type Tone = "good" | "warn" | "bad" | "info" | "neutral";

export const STATUS_TONE: Record<string, Tone> = {
  draft: "neutral", under_review: "info", approved: "good", scheduled: "good", in_intake: "info", in_processing: "info",
  exception_review: "warn", quality_control: "info", listed_for_sale: "info", partially_sold: "info", closed: "neutral",
  declined: "bad", cancelled: "neutral",
};
