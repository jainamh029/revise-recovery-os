"use client";
import { Filter, X } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { Chip, Field } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { titleCase } from "@/lib/format";

export const FILTER_KEYS = ["range", "start_date", "end_date", "partner_id", "cohort_id", "robot_cell_id", "cohort_status", "contract_type", "ownership_model", "resale_channel"] as const;

const RANGES = [
  { v: "", l: "Default windows" },
  { v: "all_time", l: "All time" },
  { v: "last_7_days", l: "Last 7 days" },
  { v: "last_30_days", l: "Last 30 days" },
  { v: "current_month", l: "Current month" },
  { v: "custom", l: "Custom range" },
];
const STATUSES = ["draft", "under_review", "approved", "scheduled", "in_intake", "in_processing", "exception_review", "quality_control", "listed_for_sale", "partially_sold", "closed", "declined", "cancelled"];
const CONTRACTS = ["supply_purchase", "processing_service", "revenue_share", "hybrid"];
const OWNERSHIP = ["partner_owned", "operator_owned", "shared", "unknown"];

/** Filters live ONLY in the URL query string: shareable, refresh-safe, and read by the backend (never filtered client-side). */
export function useFilterQuery() {
  const sp = useSearchParams();
  return useMemo(() => {
    const p = new URLSearchParams();
    FILTER_KEYS.forEach((k) => { const v = sp.get(k); if (v) p.set(k, v); });
    return p;
  }, [sp]);
}

export default function DashboardFilters({ applied, count }: { applied?: { key: string; label: string; value: string }[]; count?: number }) {
  const router = useRouter();
  const path = usePathname();
  const q = useFilterQuery();
  const partners = useApi<any[]>("/api/partners");
  const cohorts = useApi<any[]>("/api/cohorts");
  const cells = useApi<any[]>("/api/robot-cells");
  const listings = useApi<any[]>("/api/resale-listings");
  const [dateErr, setDateErr] = useState<string | null>(null);
  // router.replace is async: keep the latest intended query synchronously so rapid successive changes compose instead of clobbering.
  const pending = useRef<string>(q.toString());
  const sent = useRef<string[]>([]);  // URLs this component pushed; when one echoes back it is NOT an external navigation
  useEffect(() => {
    const cur = q.toString();
    const i = sent.current.indexOf(cur);
    if (i >= 0) { sent.current = sent.current.slice(i + 1); return; }
    pending.current = cur;  // back/forward or a pasted URL: adopt it
    sent.current = [];
  }, [q]);

  const channels = useMemo(() => [...new Set((listings.data ?? []).map((l) => l.listing_channel))].sort(), [listings.data]);
  const get = (k: string) => q.get(k) ?? "";
  const range = get("range") || (get("start_date") || get("end_date") ? "custom" : "");

  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(pending.current);
    Object.entries(patch).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)));
    if (next.get("range") !== "custom") { next.delete("start_date"); next.delete("end_date"); }
    const s = next.get("start_date"), e = next.get("end_date");
    if (s && e && e < s) { setDateErr("End date cannot be before the start date."); return; }
    setDateErr(null);
    pending.current = next.toString();
    sent.current.push(pending.current);
    router.replace(next.toString() ? `${path}?${next}` : path, { scroll: false });
  };
  const sel = (label: string, k: string, opts: { v: string; l: string }[], testid: string) => (
    <Field label={label}>
      <select className="input" value={get(k)} onChange={(e) => set({ [k]: e.target.value })} data-testid={testid} aria-label={label}>
        <option value="">Any</option>
        {opts.map((o) => <option key={o.v} value={o.v}>{o.l}</option>)}
      </select>
    </Field>
  );
  const n = count ?? FILTER_KEYS.filter((k) => k !== "start_date" && k !== "end_date" && get(k)).length;

  return (
    <section className="card mb-4 p-4" aria-label="Dashboard filters">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-semibold"><Filter className="h-4 w-4" aria-hidden /> Filters
          <span data-testid="filter-count" className={`num rounded-md px-1.5 py-0.5 text-xs ${n ? "bg-ink text-white" : "bg-paper text-muted"}`}>{n} active</span>
        </div>
        <button className="btn" onClick={() => { setDateErr(null); pending.current = ""; sent.current.push(""); router.replace(path, { scroll: false }); }} disabled={!q.toString()} data-testid="clear-filters"><X className="h-3.5 w-3.5" /> Clear filters</button>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Field label="Date range">
          <select className="input" value={range} onChange={(e) => set({ range: e.target.value, start_date: "", end_date: "" })} data-testid="filter-range" aria-label="Date range">
            {RANGES.map((r) => <option key={r.v} value={r.v}>{r.l}</option>)}
          </select>
        </Field>
        {range === "custom" && (<>
          <Field label="Start date"><input type="date" className="input" value={get("start_date")} onChange={(e) => set({ range: "custom", start_date: e.target.value })} data-testid="filter-start" /></Field>
          <Field label="End date"><input type="date" className="input" value={get("end_date")} onChange={(e) => set({ range: "custom", end_date: e.target.value })} data-testid="filter-end" /></Field>
        </>)}
        {sel("Partner", "partner_id", (partners.data ?? []).map((p) => ({ v: p.id, l: p.name })), "filter-partner")}
        {sel("Cohort", "cohort_id", (cohorts.data ?? []).map((c) => ({ v: c.id, l: c.code })), "filter-cohort")}
        {sel("Robot cell", "robot_cell_id", (cells.data ?? []).map((c) => ({ v: c.id, l: c.cell_name })), "filter-cell")}
        {sel("Cohort status (current)", "cohort_status", STATUSES.map((s) => ({ v: s, l: titleCase(s) })), "filter-status")}
        {sel("Contract type", "contract_type", CONTRACTS.map((s) => ({ v: s, l: titleCase(s) })), "filter-contract")}
        {sel("Ownership model", "ownership_model", OWNERSHIP.map((s) => ({ v: s, l: titleCase(s) })), "filter-ownership")}
        {sel("Resale channel", "resale_channel", channels.map((c) => ({ v: c, l: c })), "filter-channel")}
      </div>
      {dateErr && <p role="alert" data-testid="date-error" className="mt-3 rounded-lg bg-bad-bg p-2 text-xs font-medium text-bad">{dateErr}</p>}
      {applied && applied.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs" data-testid="applied-summary">
          <span className="text-muted">Showing:</span>
          {applied.map((a) => <Chip key={a.key} tone="info">{a.label}: {a.value}</Chip>)}
          <span className="text-muted">· filters combine with AND</span>
        </div>
      )}
    </section>
  );
}
