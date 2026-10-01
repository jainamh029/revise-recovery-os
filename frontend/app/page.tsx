"use client";
import Link from "next/link";
import { Suspense } from "react";
import DashboardFilters, { useFilterQuery } from "@/components/DashboardFilters";
import { ArrowRight, ShieldAlert } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, Cell } from "recharts";
import { C, axis, gridProps, tooltipStyle } from "@/components/charts";
import { Bar as Meter, Card, Chip, DecisionChip, Empty, ErrorBox, Hint, Loading, PageHeader, SeverityChip, Stat } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { dateShort, money, n, pct, signedMoney, titleCase } from "@/lib/format";

const span = (w?: { start: string | null; end: string }) => (w ? `${w.start ? dateShort(w.start) : "start"} – ${dateShort(w.end)}` : "");

export default function Executive() {
  return (
    <Suspense fallback={<Loading label="Loading dashboard" />}>
      <Dashboard />
    </Suspense>
  );
}

function Dashboard() {
  const fq = useFilterQuery();
  const qs = fq.toString();
  // Every drill-down URL = destination + its own params + ALL active dashboard filters, verbatim.
  const drill = (path: string, extra: Record<string, string> = {}) => {
    const p = new URLSearchParams(extra);
    fq.forEach((v, k) => p.set(k, v));
    return p.toString() ? `${path}?${p}` : path;
  };
  const { data: d, error, loading, refetch } = useApi<any>(`/api/dashboard/executive${qs ? `?${qs}` : ""}`);

  const header = (
    <>
      <PageHeader
        title="Executive dashboard"
        subtitle="Are we turning robot capacity into profitable throughput and cash? Plan is the approved underwriting budget; forecast re-runs that model on what has actually happened. CM % is contribution margin ÷ net recognized revenue."
      />
      <DashboardFilters applied={d?.filters.applied} count={d?.filters.count} />
    </>
  );
  if (error) return <>{header}<ErrorBox message={error} onRetry={refetch} /></>;
  if (loading || !d) return <>{header}<Loading label="Computing portfolio" /></>;

  const delta = d.forecast_cm - d.plan_cm;
  const pending = (d.cohorts as any[]).filter((c) => c.status === "under_review");
  const cmBars = (d.cohorts as any[]).filter((c) => c.forecast_cm != null).map((c) => ({
    code: c.code, Plan: c.plan_cm, Forecast: c.forecast_cm, floor: c.below_floor,
  }));
  const idle = d.cells.filter((c: any) => c.status === "available" && c.utilization < 0.05).map((c: any) => c.name);
  const weakCells = d.cells.filter((c: any) => c.status === "available" && c.utilization >= 0.05 && c.utilization < 0.65);

  return (
    <>
      {header}
      {d.empty && (
        <div className="mb-4" data-testid="empty-state">
          <Empty>No cohorts match this combination of filters (filters combine with AND). All figures below are zero. Clear or relax a filter.</Empty>
        </div>
      )}
      <p className="mb-3 text-xs text-muted" data-testid="basis-note">
        Basis: <b>Current</b> = today's state, not backdated. Dated metrics use their own source date — operations {span(d.filters.windows.operations)}, cash receipts {span(d.filters.windows.cash_collected)}, realized margin {d.filters.resolved_range.range ? span(d.filters.windows.realized_margin) : "all time"}.
      </p>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
        <Stat id="active-cohorts" href={drill("/cohorts", { status: "active" })} label="Active cohorts" value={d.active_cohorts} sub={<>{d.pending_review} awaiting decision · {d.committed_cohorts} approved, not started <Chip>Current status</Chip></>} />
        <Stat
          id="forecast-cm" href={drill("/analytics/cohort-profitability", { metric: "forecast_cm" })}
          label="Forecast contribution margin"
          value={money(d.forecast_cm, { compact: true })}
          tone={d.plan_cm > 0 && delta < -0.05 * d.plan_cm ? "warn" : undefined}
          sub={<>vs {money(d.plan_cm, { compact: true })} plan <b className={delta < 0 ? "text-bad" : "text-good"}>({signedMoney(delta)})</b> <Chip>Current</Chip></>}
        />
        <Stat
          id="realized-cm" href={drill("/analytics/cohort-profitability", { metric: "realized_cm" })}
          label="Realized contribution margin"
          value={money(d.actual_cm_to_date, { compact: true })}
          sub={<>{d.filters.realized_basis === "channel_recovery" ? "Channel recovery margin" : "Net recognized revenue − direct variable cost"} · {d.filters.resolved_range.range ? span(d.filters.windows.realized_margin) : "all time"}<Hint>{d.semantics.actual_cm_to_date.note}</Hint></>}
        />
        <Stat id="cash-tied-up" href={drill("/analytics/cash-exposure")} label="Cash tied up in inventory" value={money(d.cash_tied_up_inventory, { compact: true })} sub={<>Unsold devices at cost <Chip>Current</Chip><Hint>{d.semantics.cash_tied_up_inventory.note}</Hint></>} />
        <Stat id="cash-collected" label="Cash collected" value={money(d.cash_collected, { compact: true })} sub={<>Receipts {span(d.filters.windows.cash_collected)}<Hint>{d.semantics.cash_collected.note}</Hint></>} tone="good" />
        <Stat
          id="utilization" href={drill("/operations/robot-performance")}
          label="Robot utilization"
          value={pct(d.utilization, 0)}
          tone={d.utilization < 0.65 && d.utilization > 0 ? "warn" : undefined}
          sub={<>{idle.length ? `${idle.join(", ")} idle · ` : ""}{span(d.filters.windows.operations)}<Hint>{d.semantics.utilization.note}</Hint></>}
        />
        <Stat
          id="throughput"
          label="Throughput vs plan"
          value={`${n(d.throughput_actual)}`}
          sub={<>devices started vs {n(d.throughput_plan)} planned <b className={d.throughput_actual >= d.throughput_plan ? "text-good" : "text-bad"}>({d.throughput_plan ? signedPct(d.throughput_actual / d.throughput_plan - 1) : "—"})</b> · {span(d.filters.windows.operations)}</>}
        />
        <Stat id="exception-rate" href={drill("/operations/exceptions")} label="Exception rate" value={pct(d.exception_rate, 1)} tone={d.exception_rate > 0.2 ? "bad" : undefined} sub={<>{d.exception_detail.exceptions} of {d.exception_detail.devices_started} started · {d.filters.resolved_range.range ? span(d.filters.windows.exceptions) : "all time"}</>} />
        <Stat id="aged-inventory" href={drill("/inventory/aged")} label="Aged inventory" value={money(d.aged_inventory_value, { compact: true })} tone={d.aged_inventory_value > 0 ? "warn" : undefined} sub={<>Listed beyond target, at cost <Chip>Current</Chip></>} />
        <Stat id="overdue" href={drill("/collections/aging", { status: "overdue" })} label="Overdue collections" value={money(d.overdue_collections, { compact: true })} tone={d.overdue_collections > 0 ? "bad" : undefined} sub={<>Past-due balance{d.filters.resolved_range.range ? ` of items issued ${span(d.filters.resolved_range)}` : ""} <Chip>Current</Chip><Hint>{d.semantics.overdue_collections.note}</Hint></>} />
        <Stat id="critical-alerts" href={drill("/alerts", { severity: "critical", status: "open" })} label="Critical alerts" value={d.alert_counts.critical} tone={d.alert_counts.critical > 0 ? "bad" : undefined} sub={<>{d.alert_counts.warning} warnings · unresolved <Chip>Current</Chip></>} />
        <Stat id="decisions" label="Decisions waiting" value={d.pending_review} sub="Cohorts offered, not yet accepted" />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card
          className="lg:col-span-2"
          title="Critical action queue"
          subtitle={`${d.alert_counts.critical} critical · ${d.alert_counts.warning} warnings`}
          actions={<Link href="/alerts" className="btn">All alerts <ArrowRight className="h-3.5 w-3.5" /></Link>}
          pad={false}
        >
          <ul className="divide-y divide-line">
            {d.critical_alerts.length === 0 && <li className="p-4 text-sm text-muted">Nothing needs action.</li>}
            {d.critical_alerts.map((a: any) => (
              <li key={a.id} className="flex items-start gap-3 px-4 py-3">
                <ShieldAlert className={`mt-0.5 h-4 w-4 shrink-0 ${a.severity === "critical" ? "text-bad" : "text-warn"}`} aria-hidden />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{a.title}</span>
                    <SeverityChip severity={a.severity} />
                  </div>
                  {a.recommended_action && <p className="mt-0.5 text-xs text-muted">{a.recommended_action}</p>}
                </div>
                {a.cohort_id && <Link className="btn shrink-0" href={`/cohorts/${a.cohort_id}`}>Open {a.cohort_code}</Link>}
              </li>
            ))}
          </ul>
        </Card>

        <Card title="Decisions waiting" subtitle="Underwritten before any cash or robot time is committed" pad={false}>
          <ul className="divide-y divide-line">
            {pending.length === 0 && <li className="p-4 text-sm text-muted">No cohorts awaiting a decision.</li>}
            {pending.map((c) => (
              <li key={c.id} className="p-4">
                <div className="flex items-center justify-between gap-2">
                  <span className="num text-sm font-semibold">{c.code}</span>
                  <DecisionChip decision={c.recommendation} />
                </div>
                <p className="mt-1 text-xs text-muted">{c.name}</p>
                <div className="mt-2 flex items-center justify-between text-xs">
                  <span className="num">{pct(c.base_cm_pct, 0)} base CM · {money(c.cm_per_robot_hour_plan, {})}/robot-hr</span>
                  <Link href={`/cohorts/${c.id}`} className="font-semibold text-ink underline underline-offset-2">Review</Link>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Contribution margin: approved plan vs reforecast" subtitle="Active cohorts. Reforecast re-runs the approved model with observed exception, repair, uptime and price behaviour.">
          {cmBars.length === 0 ? <Empty>No live cohorts in scope.</Empty> : (
          <div className="h-64" role="img" aria-label="Bar chart comparing plan and forecast contribution margin per active cohort">
            <ResponsiveContainer>
              <BarChart data={cmBars} barGap={4}>
                <CartesianGrid {...gridProps} />
                <XAxis dataKey="code" {...axis} />
                <YAxis {...axis} tickFormatter={(v) => `$${v / 1000}k`} width={48} />
                <Tooltip {...tooltipStyle} formatter={(v: number) => money(v)} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="Plan" fill={C.soft} radius={[4, 4, 0, 0]} />
                <Bar dataKey="Forecast" radius={[4, 4, 0, 0]}>
                  {cmBars.map((b, i) => <Cell key={i} fill={b.floor ? C.bad : C.ink} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          )}
          <p className="mt-2 text-xs text-muted">Red = forecast margin below the cohort's approved floor.</p>
        </Card>

        <Card title="Throughput vs plan" subtitle="Devices started per day, trailing 14 days (plan from approved robot-cell schedule)">
          <div className="h-64" role="img" aria-label="Line chart of daily devices started versus plan">
            <ResponsiveContainer>
              <LineChart data={d.throughput_vs_plan.map((r: any) => ({ ...r, label: dateShort(r.date) }))}>
                <CartesianGrid {...gridProps} />
                <XAxis dataKey="label" {...axis} interval={1} />
                <YAxis {...axis} width={36} />
                <Tooltip {...tooltipStyle} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line type="linear" dataKey="plan" name="Plan" stroke={C.soft} strokeWidth={2} strokeDasharray="5 4" dot={false} />
                <Line type="linear" dataKey="actual" name="Actual" stroke={C.brand} strokeWidth={2.5} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card title="Robot cells · 14d utilization" subtitle="Productive hours ÷ scheduled, excluding planned maintenance">
          <ul className="space-y-3">
            {d.cells.map((c: any) => (
              <li key={c.id}>
                <div className="mb-1 flex items-center justify-between text-sm">
                  <span className="font-medium">{c.name}</span>
                  <span className="flex items-center gap-2">
                    {c.status !== "available" && <Chip>{titleCase(c.status)}</Chip>}
                    {c.status === "available" && c.utilization < 0.05 && <Chip tone="info">Idle</Chip>}
                    <span className="num text-sm">{c.status === "available" ? pct(c.utilization, 0) : "—"}</span>
                  </span>
                </div>
                <Meter value={c.utilization} tone={c.status !== "available" ? "neutral" : c.utilization < 0.65 ? "warn" : "good"} />
                {c.downtime_hours > 8 && <p className="mt-1 text-xs text-warn">{n(c.downtime_hours, 0)}h downtime in window</p>}
              </li>
            ))}
          </ul>
          {weakCells.length > 0 && <p className="mt-3 text-xs text-muted">{weakCells.map((c: any) => c.name).join(", ")} below the 65% warning line.</p>}
        </Card>

        <Card title="Partner ranking" subtitle="Transparent score: margin 40 · quality 20 · cash 20 · plan adherence 20" actions={<Link href="/partners" className="btn">Scorecards</Link>}>
          {d.partners.filter((p: any) => p.score != null).length === 0 && <Empty>No scored partners in scope.</Empty>}
          <ol className="space-y-3">
            {[...d.partners].filter((p: any) => p.score != null).sort((a: any, b: any) => b.score - a.score).map((p: any, i: number) => (
              <li key={p.partner_id} className="flex items-center gap-3">
                <span className="num w-5 text-xs text-muted">{i + 1}</span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{p.name}</div>
                  <div className="text-xs text-muted num">{pct(p.metrics.forecast_cm_pct, 0)} CM · {money(p.metrics.forecast_margin_per_device)}/laptop</div>
                </div>
                <Chip tone={p.recommendation === "expand" ? "good" : p.recommendation === "renegotiate" ? "warn" : "bad"}>{Math.round(p.score)}</Chip>
              </li>
            ))}
          </ol>
        </Card>

        <Card title="Cash still to come in" subtitle="Receivables by days past due">
          <div className="h-44" role="img" aria-label="Bar chart of outstanding collections by age bucket">
            <ResponsiveContainer>
              <BarChart data={Object.entries(d.collections_buckets).map(([k, v]) => ({ bucket: k === "current" ? "Not due" : k + "d", v }))}>
                <CartesianGrid {...gridProps} />
                <XAxis dataKey="bucket" {...axis} />
                <YAxis {...axis} tickFormatter={(v) => `$${v / 1000}k`} width={44} />
                <Tooltip {...tooltipStyle} formatter={(v: number) => money(v)} />
                <Bar dataKey="v" name="Outstanding" radius={[4, 4, 0, 0]}>
                  {Object.keys(d.collections_buckets).map((k, i) => <Cell key={k} fill={i === 0 ? C.soft : i === 1 ? C.warn : C.bad} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <Link href="/resale" className="mt-2 inline-block text-xs font-semibold underline underline-offset-2">Open collections →</Link>
        </Card>
      </div>
    </>
  );
}

function signedPct(v: number) {
  return `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(0)}%`;
}
