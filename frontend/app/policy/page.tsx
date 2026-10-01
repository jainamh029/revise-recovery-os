"use client";
import { useEffect, useState } from "react";
import { Card, ErrorBox, Field, Loading, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";

type F = { k: string; label: string; hint: string; kind: "pct" | "num" | "money" };
const GROUPS: { title: string; fields: F[] }[] = [
  { title: "Acceptance thresholds", fields: [
    { k: "base_case_accept_cm_pct_net_revenue", label: "Base-case accept line — CM % of net recognized revenue", hint: "At or above (with the downside floor met) → Accept", kind: "pct" },
    { k: "base_case_review_cm_pct_net_revenue", label: "Base-case decline line — CM % of net recognized revenue", hint: "Below this (or negative CM dollars) → Decline; between the lines → Review", kind: "pct" },
    { k: "downside_case_min_cm_pct_net_revenue", label: "Downside-case minimum CM % of net recognized revenue", hint: "Below this → Review required", kind: "pct" },
    { k: "live_margin_floor_cm_pct_net_revenue", label: "Live margin floor — forecast CM % of net recognized revenue", hint: "Forecast below this → critical margin-intervention alert", kind: "pct" },
    { k: "critical_negative_margin_threshold", label: "Loss-making threshold — forecast CM dollars ($)", hint: "Forecast CM dollars below this → stop / loss-making alert", kind: "money" },
    { k: "max_exception_rate", label: "Maximum expected exception rate", hint: "", kind: "pct" },
    { k: "min_first_pass_yield", label: "Minimum first-pass yield", hint: "", kind: "pct" },
    { k: "min_sell_through", label: "Minimum sell-through", hint: "", kind: "pct" },
    { k: "min_quality_index", label: "Minimum quality index (0–1)", hint: "Condition-weighted A=1.0 B=.75 C=.45 D=.15", kind: "num" },
    { k: "working_capital_limit", label: "Working-capital limit ($)", hint: "Committed + new cohort cash exposure", kind: "money" },
    { k: "review_capacity_per_day", label: "Manual-review slots per day", hint: "Technician exception capacity", kind: "num" },
  ]},
  { title: "Alert thresholds", fields: [
    { k: "utilization_warning", label: "Utilization warning below", hint: "5 consecutive operating days", kind: "pct" },
    { k: "throughput_miss_warning", label: "Throughput miss warning", hint: "Below plan by more than this", kind: "pct" },
    { k: "cost_overrun_warning", label: "Cost overrun warning", hint: "Above approved cost budget", kind: "pct" },
    { k: "target_days_to_sale", label: "Target days to sale", hint: "Inventory older than this is 'aged'", kind: "num" },
    { k: "target_collection_days", label: "Target collection days", hint: "", kind: "num" },
  ]},
  { title: "Cost rates (defaults for new cohorts)", fields: [
    { k: "robot_cost_per_hour", label: "Robot-cell cost per scheduled hour ($)", hint: "", kind: "money" },
    { k: "labor_rate_per_hour", label: "Technician rate per hour ($)", hint: "", kind: "money" },
    { k: "manual_minutes_per_exception", label: "Manual minutes per exception", hint: "", kind: "num" },
  ]},
  { title: "Priority-score weights", fields: [
    { k: "w_cm_per_hour", label: "CM per robot hour", hint: "", kind: "num" }, { k: "w_cm_pct", label: "CM %", hint: "", kind: "num" },
    { k: "w_low_exception", label: "Low exception rate", hint: "", kind: "num" }, { k: "w_cash_efficiency", label: "Cash efficiency", hint: "", kind: "num" },
  ]},
];

export default function Policy() {
  const { data, error, loading, refetch } = useApi<any>("/api/policy");
  const [form, setForm] = useState<Record<string, number>>({});
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const kinds: Record<string, F["kind"]> = Object.fromEntries(GROUPS.flatMap((g) => g.fields).map((f) => [f.k, f.kind]));

  useEffect(() => {
    if (data) setForm(Object.fromEntries(Object.entries(kinds).map(([k, kind]) => [k, kind === "pct" ? +(data[k] * 100).toFixed(2) : data[k]])));
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) return <Loading />;
  if (error || !data) return <ErrorBox message={error ?? "No data"} onRetry={refetch} />;

  const save = async () => {
    setMsg(null);
    try {
      const patch = Object.fromEntries(Object.entries(form).map(([k, v]) => [k, kinds[k] === "pct" ? v / 100 : v]));
      await api("/api/policy", { method: "PATCH", body: patch });
      setMsg({ ok: true, text: "Policy saved. Recommendations and alerts use the new thresholds immediately; approved budgets are unchanged." }); refetch();
    } catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };

  return (
    <>
      <PageHeader title="Underwriting policy" subtitle="Configurable thresholds. Illustrative synthetic assumptions, not Revise's real thresholds. Every CM % is contribution margin ÷ net recognized revenue." />
      <div className="grid gap-4 md:grid-cols-2">
        {GROUPS.map((g) => (
          <Card key={g.title} title={g.title}>
            <div className="grid gap-3 sm:grid-cols-2">
              {g.fields.map((f) => (
                <Field key={f.k} label={f.label} hint={f.hint || undefined}>
                  <div className="relative">
                    <input type="number" step="any" className="input num pr-7" value={form[f.k] ?? ""} onChange={(e) => setForm({ ...form, [f.k]: Number(e.target.value) })} />
                    {f.kind === "pct" && <span className="pointer-events-none absolute right-3 top-1.5 text-sm text-muted">%</span>}
                  </div>
                </Field>
              ))}
            </div>
          </Card>
        ))}
      </div>
      <div className="mt-4 flex items-center gap-3">
        <button className="btn btn-primary" onClick={save}>Save policy</button>
        {msg && <p role="alert" className={`text-sm font-medium ${msg.ok ? "text-good" : "text-bad"}`}>{msg.text}</p>}
      </div>
    </>
  );
}
