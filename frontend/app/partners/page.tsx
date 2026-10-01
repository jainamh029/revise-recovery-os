"use client";
import Link from "next/link";
import { Bar as Meter, Card, Chip, ErrorBox, Loading, PageHeader, StatusChip } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { money, n, pct, titleCase } from "@/lib/format";
import { cohortHref } from "@/lib/nav";

const REC: Record<string, { label: string; tone: "good" | "warn" | "bad" | "neutral" }> = {
  expand: { label: "Expand", tone: "good" }, renegotiate: { label: "Renegotiate", tone: "warn" }, reduce_or_stop: { label: "Reduce or stop", tone: "bad" },
  no_history: { label: "No history", tone: "neutral" }, decline_pattern: { label: "Declined to date", tone: "bad" },
};
const COMP: Record<string, string> = { margin: "Realised / forecast margin", quality: "Quality (exceptions)", cash: "Cash conversion", plan_adherence: "Plan adherence" };

export default function Partners() {
  const { data, error, loading, refetch } = useApi<any[]>("/api/analytics/partner-scorecards");
  if (loading) return <Loading />;
  if (error || !data) return <ErrorBox message={error ?? "No data"} onRetry={refetch} />;
  const sorted = [...data].sort((a, b) => (b.score ?? -1) - (a.score ?? -1));

  return (
    <>
      <PageHeader title="Partner scorecards" subtitle="Which suppliers should we expand, renegotiate or stop? Scores are transparent: every component and weight is shown, and all inputs come from realised or reforecast cohort economics." />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {sorted.map((p) => {
          const r = REC[p.recommendation] ?? REC.no_history;
          return (
            <Card key={p.partner_id}>
              <div className="flex items-start justify-between gap-3">
                <div><h2 className="text-[15px] font-semibold">{p.name}</h2><p className="text-xs text-muted">{titleCase(p.partner_type)}</p></div>
                <div className="text-right"><div className="num text-3xl font-semibold leading-none">{p.score != null ? Math.round(p.score) : "—"}</div><div className="mt-1"><Chip tone={r.tone}>{r.label}</Chip></div></div>
              </div>
              {p.score == null ? <p className="mt-4 text-sm text-muted">{p.note ?? "No operating history."}</p> : (
                <>
                  <ul className="mt-4 space-y-2.5">
                    {Object.entries(p.components).map(([k, v]: any) => (
                      <li key={k}>
                        <div className="mb-1 flex justify-between text-xs"><span>{COMP[k]} <span className="text-muted">· weight {Math.round(p.weights[k] * 100)}</span></span><span className="num font-medium">{Math.round(v)}</span></div>
                        <Meter value={v} max={100} tone={v >= 75 ? "good" : v >= 50 ? "warn" : "bad"} />
                      </li>
                    ))}
                  </ul>
                  <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-1.5 border-t border-line pt-3 text-xs">
                    <dt className="text-muted">Forecast margin / laptop</dt><dd className="num text-right font-medium">{money(p.metrics.forecast_margin_per_device)}</dd>
                    <dt className="text-muted">Realised to date / laptop</dt><dd className="num text-right">{money(p.metrics.realized_margin_per_device)}</dd>
                    <dt className="text-muted">Forecast CM % (net revenue)</dt><dd className="num text-right">{pct(p.metrics.forecast_cm_pct, 1)}</dd>
                    <dt className="text-muted">Exception rate</dt><dd className="num text-right">{pct(p.metrics.exception_rate, 1)}</dd>
                    <dt className="text-muted">First-pass yield</dt><dd className="num text-right">{pct(p.metrics.first_pass_yield, 1)}</dd>
                    <dt className="text-muted">Avg collection lag</dt><dd className="num text-right">{p.metrics.avg_collection_lag_days != null ? `${n(p.metrics.avg_collection_lag_days, 0)}d` : "—"}</dd>
                    <dt className="text-muted">Devices received</dt><dd className="num text-right">{n(p.metrics.devices_received)}</dd>
                  </dl>
                </>
              )}
              <div className="mt-4 flex flex-wrap gap-2 border-t border-line pt-3">
                {p.cohorts.map((c: any) => <Link key={c.id} href={cohortHref(c.id)} className="inline-flex items-center gap-1.5 rounded-md border border-line px-2 py-1 text-xs hover:bg-paper"><span className="num font-semibold">{c.code}</span><StatusChip status={c.status} /></Link>)}
                {p.cohorts.length === 0 && <span className="text-xs text-muted">No cohorts yet</span>}
              </div>
            </Card>
          );
        })}
      </div>
    </>
  );
}
