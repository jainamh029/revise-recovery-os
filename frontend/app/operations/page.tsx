"use client";
import Link from "next/link";
import { useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, YAxis, XAxis, Tooltip } from "recharts";
import { C, axis, gridProps, tooltipStyle } from "@/components/charts";
import { Bar as Meter, Card, Chip, ErrorBox, Field, Loading, PageHeader, StatusChip, Table } from "@/components/ui";
import { cohortHref } from "@/lib/nav";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { dateShort, money, n, pct, titleCase } from "@/lib/format";

export default function Operations() {
  const robots = useApi<any[]>("/api/analytics/robot-performance");
  const cohorts = useApi<any[]>("/api/cohorts");
  const burden = useApi<any[]>("/api/analytics/model-burden");
  const open = useApi<any[]>("/api/exceptions?status=open");

  if (robots.loading || cohorts.loading) return <Loading />;
  if (robots.error || !robots.data) return <ErrorBox message={robots.error ?? "No data"} onRetry={robots.refetch} />;
  const live = (cohorts.data ?? []).filter((c) => ["scheduled", "in_intake", "in_processing", "exception_review", "quality_control"].includes(c.status));

  return (
    <>
      <PageHeader title="Operations tracker" subtitle="Are active cohorts hitting throughput, quality and cost plans — and which cells or device models are dragging on economics?" />

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        {robots.data.map((r) => {
          const off = r.status !== "available";
          const low = !off && r.avg_utilization > 0 && r.avg_utilization < 0.65;
          return (
            <Card key={r.id} className={low ? "border-warn/50" : ""}>
              <div className="flex items-center justify-between">
                <div className="text-sm font-semibold">{r.name}</div>
                {off ? <Chip>{titleCase(r.status)}</Chip> : low ? <Chip tone="warn">Uptime issue</Chip> : r.devices_started === 0 ? <Chip tone="info">Idle</Chip> : <Chip tone="good">Healthy</Chip>}
              </div>
              <div className="num mt-2 text-2xl font-semibold">{off ? "—" : pct(r.avg_utilization, 0)}</div>
              <div className="text-xs text-muted">avg productive utilization · 21d</div>
              <div className="mt-2 h-14" aria-hidden>
                {r.daily.length > 1 && (
                  <ResponsiveContainer>
                    <LineChart data={r.daily}><YAxis hide domain={[0, 1]} /><ReferenceLine y={0.65} stroke={C.soft} strokeDasharray="3 3" />
                      <Line type="monotone" dataKey="utilization" stroke={low ? C.warn : C.ink} strokeWidth={2} dot={false} /></LineChart>
                  </ResponsiveContainer>
                )}
              </div>
              <dl className="mt-2 grid grid-cols-2 gap-y-1 text-xs">
                <dt className="text-muted">Uptime</dt><dd className="num text-right">{r.uptime != null ? pct(r.uptime, 0) : "—"}</dd>
                <dt className="text-muted">First-pass</dt><dd className="num text-right">{r.first_pass_yield != null ? pct(r.first_pass_yield, 0) : "—"}</dd>
                <dt className="text-muted">Exceptions</dt><dd className="num text-right">{r.exception_rate != null ? pct(r.exception_rate, 0) : "—"}</dd>
                <dt className="text-muted">Downtime</dt><dd className="num text-right">{n(+r.downtime_hours, 0)}h</dd>
              </dl>
              {r.downtime_reasons[0] && <p className="mt-2 text-xs text-warn">Top cause: {r.downtime_reasons[0].reason}</p>}
            </Card>
          );
        })}
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2" title="Active cohorts vs plan" pad={false}>
          <Table>
            <thead className="border-b border-line"><tr><th className="th">Cohort</th><th className="th">Status</th><th className="th text-right">Received</th><th className="th text-right">Plan CM % (net rev.)</th><th className="th text-right">Forecast CM % (net rev.)</th><th className="th">Health</th></tr></thead>
            <tbody className="divide-y divide-line">
              {live.length === 0 && <tr><td className="td text-muted" colSpan={6}>No cohorts in processing.</td></tr>}
              {live.map((c) => (
                <tr key={c.id}>
                  <td className="td"><Link className="num font-semibold hover:underline" href={cohortHref(c.id)}>{c.code}</Link><div className="text-xs text-muted">{c.partner}</div></td>
                  <td className="td"><StatusChip status={c.status} /></td>
                  <td className="td num text-right">{n(c.received)} / {n(c.devices)}</td>
                  <td className="td num text-right">{pct(c.plan_cm_pct, 0)}</td>
                  <td className={`td num text-right ${c.below_floor ? "font-semibold text-bad" : ""}`}>{c.forecast_cm_pct != null ? pct(c.forecast_cm_pct, 0) : "—"}</td>
                  <td className="td">{c.below_floor ? <Chip tone="bad">Below margin floor</Chip> : <Chip tone="good">On plan</Chip>}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
        <LogForm cohorts={live} cells={robots.data} onDone={() => { robots.refetch(); cohorts.refetch(); open.refetch(); }} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Device models by rework burden" subtitle="Logged manual exceptions and cost, against profile expectations" pad={false}>
          <Table>
            <thead className="border-b border-line"><tr><th className="th">Model</th><th className="th text-right">Expected exc.</th><th className="th text-right">Logged</th><th className="th text-right">Cost</th><th className="th">Top cause</th></tr></thead>
            <tbody className="divide-y divide-line">
              {(burden.data ?? []).filter((b) => b.logged_exceptions > 0).slice(0, 8).map((b) => (
                <tr key={b.id}><td className="td">{b.model}</td><td className="td num text-right">{pct(b.expected_exception_rate, 0)}</td>
                  <td className="td text-right"><div className="num">{b.logged_exceptions}</div><Meter value={b.logged_exceptions} max={Math.max(...(burden.data ?? []).map((x) => x.logged_exceptions), 1)} tone="warn" /></td>
                  <td className="td num text-right">{money(b.logged_cost, { compact: true })}</td><td className="td text-xs">{titleCase(b.top_type)}</td></tr>
              ))}
            </tbody>
          </Table>
        </Card>
        <Card title="Open exceptions" subtitle="Needs a technician decision" pad={false}>
          <ul className="divide-y divide-line">
            {(open.data ?? []).length === 0 && <li className="p-4 text-sm text-muted">No open exceptions.</li>}
            {(open.data ?? []).slice(0, 8).map((e) => (
              <li key={e.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
                <div className="min-w-0"><div className="text-sm font-medium">{titleCase(e.exception_type)} {e.severity === "critical" && <Chip tone="bad">Critical</Chip>}</div>
                  <div className="text-xs text-muted">{dateShort(e.opened_at)} · est. {money(e.estimated_resolution_cost)}</div></div>
                <button className="btn" onClick={async () => { await api(`/api/exceptions/${e.id}`, { method: "PATCH", body: { status: "resolved", actual_resolution_cost: e.estimated_resolution_cost ?? 0 } }); open.refetch(); burden.refetch(); }}>Resolve</button>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </>
  );
}

function LogForm({ cohorts, cells, onDone }: { cohorts: any[]; cells: any[]; onDone: () => void }) {
  const today = new Date().toISOString().slice(0, 10);
  const [f, setF] = useState<any>({ cohort_id: "", robot_cell_id: "", operation_date: today, devices_received: 0, devices_started: 0, devices_completed: 0, first_pass_completions: 0, manual_exception_count: 0, quality_approved_count: 0, productive_robot_hours: 0, downtime_hours: 0, technician_hours: 0, repair_parts_cost: 0, downtime_reason: "" });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const set = (k: string, v: any) => setF((p: any) => ({ ...p, [k]: v }));
  const num = (k: string, label: string) => (
    <Field label={label} key={k}><input type="number" min={0} step="any" className="input num" value={f[k]} onChange={(e) => set(k, e.target.value === "" ? 0 : Number(e.target.value))} /></Field>
  );
  const submit = async () => {
    setMsg(null);
    try { await api("/api/operations/daily", { body: { ...f, downtime_reason: f.downtime_reason || null } }); setMsg({ ok: true, text: "Day recorded. Metrics, forecast and alerts refreshed." }); onDone(); }
    catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };
  return (
    <Card title="Log a day of operations" subtitle="Posting re-prices robot, labour and repair cost into the cohort and refreshes the forecast">
      <div className="space-y-3">
        <Field label="Cohort"><select className="input" value={f.cohort_id} onChange={(e) => set("cohort_id", e.target.value)}><option value="">Select…</option>{cohorts.map((c) => <option key={c.id} value={c.id}>{c.code}</option>)}</select></Field>
        <div className="grid grid-cols-2 gap-2">
          <Field label="Robot cell"><select className="input" value={f.robot_cell_id} onChange={(e) => set("robot_cell_id", e.target.value)}><option value="">Select…</option>{cells.filter((c) => c.status === "available").map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
          <Field label="Date"><input type="date" className="input" value={f.operation_date} onChange={(e) => set("operation_date", e.target.value)} /></Field>
          {num("devices_received", "Received")}{num("devices_started", "Started")}{num("devices_completed", "Completed")}{num("first_pass_completions", "First-pass")}
          {num("manual_exception_count", "Exceptions")}{num("quality_approved_count", "QA approved")}{num("productive_robot_hours", "Productive h")}{num("downtime_hours", "Downtime h")}
          {num("technician_hours", "Technician h")}{num("repair_parts_cost", "Repair parts $")}
        </div>
        <Field label="Downtime reason"><input className="input" value={f.downtime_reason} onChange={(e) => set("downtime_reason", e.target.value)} placeholder="optional" /></Field>
        {msg && <p role="alert" className={`rounded-lg p-2 text-xs font-medium ${msg.ok ? "bg-good-bg text-good" : "bg-bad-bg text-bad"}`}>{msg.text}</p>}
        <button className="btn btn-primary w-full" disabled={!f.cohort_id || !f.robot_cell_id} onClick={submit}>Record day</button>
      </div>
    </Card>
  );
}
