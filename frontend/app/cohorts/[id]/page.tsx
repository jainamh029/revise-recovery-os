"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Download } from "lucide-react";
import { Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { C, axis, gridProps, tooltipStyle } from "@/components/charts";
import { ChecksList, LinesTable, MoneyFlow, RecommendationCard, ScenarioTable } from "@/components/cohort/Underwriting";
import { Card, Chip, DecisionChip, Empty, ErrorBox, Field, Hint, Loading, PageHeader, StatusChip, Stat, Table, Tabs } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { dateShort, money, n, pct, signedMoney, titleCase } from "@/lib/format";

const fmtVar = (unit: string, v: number | null) => {
  if (v == null) return "—";
  if (unit === "$") return money(v);
  if (unit === "%") return pct(v, 1);
  if (unit === "days") return `${n(v, 1)}d`;
  return n(v, unit === "hours" ? 0 : 0);
};
const VAR_TONE: Record<string, "good" | "warn" | "bad" | "neutral"> = { on_track: "good", watch: "warn", off_track: "bad", "n/a": "neutral" };

export default function CohortDetail() {
  const { id } = useParams<{ id: string }>();
  const cohort = useApi<any>(`/api/cohorts/${id}`);
  const uw = useApi<any>(`/api/cohorts/${id}/underwriting`);
  const perf = useApi<any>(`/api/cohorts/${id}/performance`);
  const [tab, setTab] = useState<string | null>(null);

  if (cohort.loading || uw.loading) return <Loading />;
  if (cohort.error || !cohort.data) return <ErrorBox message={cohort.error ?? "Not found"} onRetry={cohort.refetch} />;
  const c = cohort.data;
  const live = ["in_intake", "in_processing", "exception_review", "quality_control", "listed_for_sale", "partially_sold", "closed"].includes(c.status);
  const active = tab ?? (live ? "perf" : "uw");
  const refetchAll = () => { cohort.refetch(); uw.refetch(); perf.refetch(); };

  return (
    <>
      <div className="mb-2 text-xs"><Link href="/cohorts" className="text-muted hover:text-ink">← Underwriting</Link></div>
      <PageHeader
        title={`${c.cohort_code} · ${c.cohort_name}`}
        subtitle={<span className="flex flex-wrap items-center gap-2"><StatusChip status={c.status} /> {c.partner.name} · {titleCase(c.contract?.contract_type)} · {n(c.expected_device_count)} devices · intake {dateShort(c.intake_date)} → target {dateShort(c.target_completion_date)}</span>}
      />
      <Tabs
        value={active}
        onChange={setTab}
        tabs={[
          { id: "uw", label: "Underwriting" },
          ...(live ? [{ id: "perf", label: "Plan vs actual" }] : []),
          { id: "cap", label: "Capacity" },
          ...(live ? [{ id: "fin", label: "Financials" }] : []),
          { id: "log", label: "Decision log & memo" },
        ]}
      />
      {active === "uw" && <UnderwritingTab c={c} uw={uw.data} onChange={refetchAll} />}
      {active === "perf" && live && <PerfTab id={id} perf={perf} onChange={refetchAll} />}
      {active === "cap" && <CapacityTab c={c} onChange={refetchAll} />}
      {active === "fin" && <FinancialsTab id={id} perf={perf.data} />}
      {active === "log" && <LogTab id={id} />}
    </>
  );
}

/* ------------------------------------------------------------------------------------ underwriting */
function UnderwritingTab({ c, uw, onChange }: { c: any; uw: any; onChange: () => void }) {
  const [why, setWhy] = useState("");
  const [override, setOverride] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const approvedVersion = c.versions.find((v: any) => v.is_approved);
  const pending = c.status === "under_review" || c.status === "draft";

  const act = async (kind: "approve" | "decline") => {
    setBusy(true); setErr(null);
    try {
      if (c.status === "draft") await api(`/api/cohorts/${c.id}/underwrite`, { method: "POST", body: undefined as any }).catch(() => {});
      await api(`/api/cohorts/${c.id}/${kind}`, { body: { approver: "You (demo approver)", rationale: why, override } });
      setWhy(""); onChange();
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };

  // Approved cohorts show the frozen plan; pending ones show the live model.
  const scen = approvedVersion
    ? Object.fromEntries(["downside", "base", "upside"].map((s) => [s, approvedVersion.scenarios[s].economics]))
    : uw.scenarios;
  const decision = approvedVersion?.recommendation?.decision ?? uw.decision;

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-2">
        {approvedVersion ? (
          <Card>
            <div className="flex flex-wrap items-center gap-3">
              <div className="eyebrow">Approved plan · v{approvedVersion.version_number} · immutable</div>
              <DecisionChip decision={decision} />
            </div>
            <p className="mt-2 text-sm">System recommended <b>{titleCase(decision)}</b>{approvedVersion.recommendation?.override ? " — approved by override" : ""}; approved by <b>{approvedVersion.approved_by}</b> on {dateShort(approvedVersion.approved_at)}.</p>
            <blockquote className="mt-2 border-l-2 border-line pl-3 text-sm text-muted">{approvedVersion.decision_rationale}</blockquote>
          </Card>
        ) : <RecommendationCard uw={uw} />}

        <Card title="Base, downside and upside economics" subtitle={approvedVersion ? "Frozen at approval. Actuals never change these numbers." : "Computed by the deterministic backend engine from the assumptions below."} pad={false}>
          <ScenarioTable scenarios={scen} />
        </Card>

        <Card title="Model mix & assumptions"><LinesTable lines={c.assumptions.lines} /></Card>
      </div>

      <div className="space-y-4">
        {pending && (
          <Card title="Decision" subtitle="Approval freezes the budget and logs approver, time, version and rationale.">
            <Field label="Decision rationale (required)">
              <textarea className="input min-h-[84px]" value={why} onChange={(e) => setWhy(e.target.value)} placeholder="Why accept, modify or decline?" />
            </Field>
            {uw.decision === "decline" && (
              <label className="mt-2 flex items-start gap-2 text-xs text-bad">
                <input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} className="mt-0.5" />
                Policy says decline. Override and approve anyway (logged).
              </label>
            )}
            {err && <p role="alert" className="mt-2 text-xs font-medium text-bad">{err}</p>}
            <div className="mt-3 flex gap-2">
              <button className="btn btn-primary flex-1" disabled={busy || !why.trim() || (uw.decision === "decline" && !override)} onClick={() => act("approve")}>Approve</button>
              <button className="btn btn-danger flex-1" disabled={busy || !why.trim()} onClick={() => act("decline")}>Decline</button>
            </div>
          </Card>
        )}
        <Card title="Where the base-case money goes"><MoneyFlow base={scen.base} /></Card>
        <Card title={approvedVersion ? "Policy checks (live, today)" : "Policy checks"} subtitle="Capacity, review-slots and cash checks use today's state."><ChecksList checks={uw.checks} /></Card>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------------------ plan vs actual */
function PerfTab({ id, perf, onChange }: { id: string; perf: any; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  if (perf.loading) return <Loading />;
  if (perf.error || !perf.data) return <ErrorBox message={perf.error ?? "No data"} onRetry={perf.refetch} />;
  const { variance: v, series, exceptions_by_type, cash_conversion, alerts } = perf.data;
  const fc = v.reforecast;
  const planExc = fc?.drivers.plan_exception_rate;
  const chart = series.map((r: any) => ({ d: dateShort(r.date), daily: r.exception_rate, cum: r.cum_exception_rate, started: r.started, exceptions: r.exceptions }));
  const late = v.forecast_completion && v.target_completion && v.forecast_completion > v.target_completion;

  const makeReforecast = async () => { setBusy(true); try { await api(`/api/cohorts/${id}/reforecast`, { method: "POST", body: {} }); onChange(); } finally { setBusy(false); } };

  return (
    <div className="space-y-4">
      {fc && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Approved plan CM" value={money(fc.plan_cm, { compact: true })} sub={`${pct(fc.plan_cm_pct, 1)} of net recognized revenue`} />
          <Stat label="Reforecast CM" value={money(fc.economics.contribution_margin, { compact: true })} tone={fc.below_floor ? "bad" : "good"}
            sub={<>{pct(fc.economics.cm_pct, 1)} · floor {pct(fc.margin_floor_pct, 0)} · <b>{signedMoney(fc.delta_cm)}</b> vs plan</>} />
          <Stat label="Forecast completion" value={dateShort(v.forecast_completion)} tone={late ? "warn" : undefined}
            sub={`Target ${dateShort(v.target_completion)} · ${n(v.recent_daily_throughput, 0)}/day recent · ${n(v.remaining_units)} to go`} />
          <Stat label="Cash conversion" value={cash_conversion.days != null ? `${cash_conversion.days}d` : "—"}
            sub={cash_conversion.recovery_date ? `Recovered ${dateShort(cash_conversion.recovery_date)}` : cash_conversion.forecast_recovery_date ? `Forecast ${dateShort(cash_conversion.forecast_recovery_date)}` : "Not yet recovering outflows"} />
        </div>
      )}

      {fc && (
        <Card title={<>Why the forecast moved<Hint>The approved model is re-run with observed exception rate (actuals to date, remaining devices at the recent 3-day run-rate), repair cost, process time, uptime, realised price and returns, blended by a credibility weight. A heuristic, not a statistical forecast.</Hint></>} subtitle={`Model re-run with observed behaviour (credibility ${pct(fc.credibility, 0)} — rises as more of the cohort is through the cells)`}
          actions={<button className="btn" disabled={busy} onClick={makeReforecast}>Save as reforecast version</button>}>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
            {[
              ["Exception rate", `${pct(fc.drivers.observed_exception_rate, 0)}`, `plan ${pct(fc.drivers.plan_exception_rate, 0)}`, fc.drivers.exception_mult > 1.15],
              ["Repair cost / exception", `×${fc.drivers.repair_mult.toFixed(2)}`, "vs plan", fc.drivers.repair_mult > 1.1],
              ["Process time / device", `×${fc.drivers.process_mult.toFixed(2)}`, "vs plan", fc.drivers.process_mult > 1.1],
              ["Resale price", `×${fc.drivers.price_mult.toFixed(2)}`, "vs plan", fc.drivers.price_mult < 0.95],
              ["Robot uptime", pct(fc.drivers.uptime, 0), "blended", fc.drivers.uptime < 0.85],
              ["Return rate", pct(fc.drivers.return_rate, 1), "blended", false],
            ].map(([l, val, sub, bad]: any) => (
              <div key={l} className={`rounded-lg border p-3 ${bad ? "border-bad/30 bg-bad-bg" : "border-line bg-paper/50"}`}>
                <div className="eyebrow">{l}</div><div className={`num mt-1 text-lg font-semibold ${bad ? "text-bad" : ""}`}>{val}</div><div className="text-xs text-muted">{sub}</div>
              </div>
            ))}
          </div>
          {alerts.length > 0 && (
            <ul className="mt-3 space-y-1 text-sm">
              {alerts.map((a: any) => <li key={a.id} className="flex items-center gap-2"><Chip tone={a.severity === "critical" ? "bad" : "warn"}>{titleCase(a.severity)}</Chip>{a.title}</li>)}
            </ul>
          )}
        </Card>
      )}

      <RobotCostCard a={perf.data.actuals} plan={v.reforecast} />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Plan vs actual variance" subtitle="Budget is progress-adjusted (cost lines scale with devices started)" pad={false}>
          <Table>
            <thead className="border-b border-line"><tr><th className="th">Dimension</th><th className="th text-right">Plan</th><th className="th text-right">Actual</th><th className="th text-right">Var</th><th className="th" /></tr></thead>
            <tbody className="divide-y divide-line">
              {v.rows.map((r: any) => (
                <tr key={r.key}>
                  <td className="td">{r.label}</td>
                  <td className="td num text-right text-muted">{fmtVar(r.unit, r.plan)}</td>
                  <td className="td num text-right font-medium">{fmtVar(r.unit, r.actual)}</td>
                  <td className="td num text-right">{r.variance_pct == null ? "—" : `${r.variance_pct >= 0 ? "+" : "−"}${Math.abs(r.variance_pct * 100).toFixed(0)}%`}</td>
                  <td className="td"><Chip tone={VAR_TONE[r.status]}>{r.status === "n/a" ? "n/a" : titleCase(r.status)}</Chip></td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>

        <div className="space-y-4">
          <Card title="Exception rate" subtitle="Daily and cumulative, against the approved plan">
            <div className="h-56" role="img" aria-label="Line chart of daily and cumulative exception rate against plan">
              <ResponsiveContainer>
                <ComposedChart data={chart}>
                  <CartesianGrid {...gridProps} />
                  <XAxis dataKey="d" {...axis} />
                  <YAxis {...axis} tickFormatter={(x) => `${Math.round(x * 100)}%`} width={40} domain={[0, 0.4]} ticks={[0, 0.1, 0.2, 0.3, 0.4]} />
                  <Tooltip {...tooltipStyle} formatter={(x: number) => pct(x)} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  {planExc != null && <ReferenceLine y={planExc} stroke={C.muted} strokeDasharray="5 4" label={{ value: "plan", fontSize: 11, fill: C.muted, position: "insideTopLeft" }} />}
                  <Line dataKey="daily" name="Daily" stroke={C.brand} strokeWidth={2} dot={{ r: 2 }} />
                  <Line dataKey="cum" name="Cumulative" stroke={C.ink} strokeWidth={2} dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </Card>
          <Card title="Exceptions by type" subtitle="Where manual intervention goes">
            {exceptions_by_type.length === 0 ? <Empty>No logged exceptions.</Empty> : (
              <div className="h-44" role="img" aria-label="Bar chart of exceptions by type">
                <ResponsiveContainer>
                  <BarChart data={exceptions_by_type.map((e: any) => ({ t: titleCase(e.type), count: e.count }))} layout="vertical" margin={{ left: 24 }}>
                    <CartesianGrid {...gridProps} horizontal={false} vertical />
                    <XAxis type="number" {...axis} />
                    <YAxis type="category" dataKey="t" {...axis} width={130} />
                    <Tooltip {...tooltipStyle} />
                    <Bar dataKey="count" fill={C.ink} radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------------------ capacity */
function CapacityTab({ c, onChange }: { c: any; onChange: () => void }) {
  const cells = useApi<any[]>("/api/robot-cells");
  const [cell, setCell] = useState("");
  const [start, setStart] = useState(c.intake_date ?? "");
  const [end, setEnd] = useState(c.target_completion_date ?? "");
  const [hours, setHours] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const canAssign = ["approved", "scheduled"].includes(c.status);

  const assign = async () => {
    setErr(null); setOk(false);
    try { await api(`/api/cohorts/${c.id}/assignments`, { body: { robot_cell_id: cell, start_date: start, end_date: end, ...(hours ? { planned_hours: Number(hours) } : {}) } }); setOk(true); setHours(""); onChange(); }
    catch (e: any) { setErr(e.message); }
  };
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card className="lg:col-span-2" title="Assignments" pad={false}>
        {c.assignments.length === 0 ? <div className="p-4"><Empty>No robot time assigned yet.</Empty></div> : (
          <Table>
            <thead className="border-b border-line"><tr><th className="th">Cell</th><th className="th">Window</th><th className="th text-right">Planned hours</th><th className="th text-right">Planned units</th><th className="th">Status</th></tr></thead>
            <tbody className="divide-y divide-line">
              {c.assignments.map((a: any) => (
                <tr key={a.id}><td className="td font-medium">{a.cell_name}</td><td className="td">{dateShort(a.start_date)} → {dateShort(a.end_date)}</td>
                  <td className="td num text-right">{n(+a.planned_hours, 1)}</td><td className="td num text-right">{n(a.planned_units)}</td><td className="td"><Chip>{titleCase(a.status)}</Chip></td></tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      <Card title="Assign robot capacity" subtitle={canAssign ? "The backend blocks overbooking, blocked cells and SLA breaches." : "Only approved cohorts can be scheduled."}>
        <div className="space-y-3">
          <Field label="Robot cell">
            <select className="input" value={cell} onChange={(e) => setCell(e.target.value)} disabled={!canAssign}>
              <option value="">Select…</option>
              {(cells.data ?? []).map((r) => <option key={r.id} value={r.id}>{r.cell_name} — {titleCase(r.status)}</option>)}
            </select>
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Start"><input type="date" className="input" value={start} onChange={(e) => setStart(e.target.value)} disabled={!canAssign} /></Field>
            <Field label="End"><input type="date" className="input" value={end} onChange={(e) => setEnd(e.target.value)} disabled={!canAssign} /></Field>
          </div>
          <Field label="Hours to place" hint="Blank = all remaining hours. Enter fewer to split the cohort across cells.">
            <input type="number" min={0} step="any" className="input num" value={hours} onChange={(e) => setHours(e.target.value)} placeholder="all remaining" disabled={!canAssign} />
          </Field>
          {err && <p role="alert" className="rounded-lg bg-bad-bg p-2 text-xs font-medium text-bad">{err}</p>}
          {ok && <p className="rounded-lg bg-good-bg p-2 text-xs font-medium text-good">Capacity assigned.</p>}
          <button className="btn btn-primary w-full" disabled={!canAssign || !cell || !start || !end} onClick={assign}>Assign</button>
        </div>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------------------------ financials */
function FinancialsTab({ id, perf }: { id: string; perf: any }) {
  const sales = useApi<any[]>(`/api/sales-transactions?cohort_id=${id}`);
  const close = useApi<any>(`/api/cohorts/${id}/closeout-check`);
  if (!perf) return <Loading />;
  const a = perf.actuals;
  const cats = Object.entries(a.costs_by_category as Record<string, number>).sort((x, y) => y[1] - x[1]);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Net recognized revenue" value={money(a.net_recognized_revenue, { compact: true })} sub={`${n(a.sales.units_sold)} sold · ${n(a.sales.units_returned)} returned`} />
        <Stat label="Costs to date" value={money(a.cost_total, { compact: true })} sub={`${money(a.unit_cost)} per QA-approved device`} />
        <Stat label="Contribution margin" value={money(a.contribution_margin, { compact: true })} tone={a.contribution_margin < 0 ? "bad" : undefined} sub={a.cm_pct != null ? pct(a.cm_pct) + " of net recognized revenue" : "Pre-revenue (CM % n/a)"} />
        <Stat label="Cash exposure" value={money(a.cash_exposure, { compact: true })} sub={`${money(a.cash_out, { compact: true })} out · ${money(a.cash_in, { compact: true })} in`} tone={a.cash_exposure > 25000 ? "warn" : undefined} />
        <Stat label="Inventory at cost" value={money(a.inventory_value, { compact: true })} sub={`${n(a.inventory_units)} devices unsold`} />
      </div>
      <CloseoutCard id={id} check={close} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Costs by category" pad={false}>
          <Table><tbody className="divide-y divide-line">
            {cats.map(([k, v]) => <tr key={k}><td className="td">{titleCase(k)}</td><td className="td num text-right">{money(v)}</td></tr>)}
            <tr><td className="td font-semibold">Sales-side (fees, shipping, returns)</td><td className="td num text-right">{money(a.sales.marketplace_fees + a.sales.shipping + a.sales.return_cost)}</td></tr>
          </tbody></Table>
        </Card>
        <Card title="Sales" pad={false}>
          <Table>
            <thead className="border-b border-line"><tr><th className="th">Date</th><th className="th text-right">Units</th><th className="th text-right">Gross</th><th className="th text-right">Net</th><th className="th">Cash</th></tr></thead>
            <tbody className="divide-y divide-line">
              {(sales.data ?? []).slice(0, 12).map((s) => (
                <tr key={s.id}><td className="td">{dateShort(s.sale_date)}</td><td className="td num text-right">{s.units_sold}</td><td className="td num text-right">{money(s.gross_sale_amount)}</td>
                  <td className="td num text-right">{money(s.net_sale_amount)}</td><td className="td"><Chip tone={s.payment_status === "paid" ? "good" : "warn"}>{titleCase(s.payment_status)}</Chip></td></tr>
              ))}
              {(sales.data ?? []).length === 0 && <tr><td className="td text-muted" colSpan={5}>No sales recorded.</td></tr>}
            </tbody>
          </Table>
        </Card>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------------------ log & memo */
function LogTab({ id }: { id: string }) {
  const audit = useApi<any[]>(`/api/cohorts/${id}/audit`);
  const [memo, setMemo] = useState<string | null>(null);
  const loadMemo = async () => setMemo(await fetch(`/api/cohorts/${id}/memo`).then((r) => r.text()));
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Audit trail" pad={false}>
        <ul className="divide-y divide-line">
          {(audit.data ?? []).map((a) => (
            <li key={a.id} className="px-4 py-2.5 text-sm">
              <div className="flex items-center justify-between"><span className="font-medium">{titleCase(a.action)}</span><span className="num text-xs text-muted">{new Date(a.created_at).toLocaleString()}</span></div>
              <div className="text-xs text-muted">{a.user_id}{a.new_value?.rationale ? ` — “${a.new_value.rationale}”` : a.new_value?.status ? ` → ${a.new_value.status}` : ""}</div>
            </li>
          ))}
          {audit.data?.length === 0 && <li className="p-4 text-sm text-muted">No entries.</li>}
        </ul>
      </Card>
      <Card title="Underwriting memo" subtitle="Generated by the backend; Markdown"
        actions={<div className="flex gap-2"><button className="btn" onClick={loadMemo}>Preview</button><a className="btn" href={`/api/cohorts/${id}/memo`} download={`memo-${id.slice(0, 8)}.md`}><Download className="h-3.5 w-3.5" /> Download</a></div>}>
        {memo ? <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap rounded-lg bg-paper p-3 text-xs leading-relaxed">{memo}</pre> : <Empty>Preview or download the IC-style memo for this cohort.</Empty>}
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------------------------ robot cost lenses */
function RobotCostCard({ a, plan }: { a: any; plan: any }) {
  const scheduled = a.hours.productive + a.hours.downtime;
  const leak = a.idle_capacity_leakage;
  return (
    <Card title={<>Robot cost: committed vs productive<Hint>Committed cost = scheduled cell hours × hourly cost — what the cohort margin is built on, because capacity is paid for even when it is down. Productive cost rate = robot cost incurred ÷ productive hours; when it climbs above the hourly rate, uptime or utilisation is leaking money.</Hint></>}
      subtitle="Two lenses: the cohort's economic commitment, and what the hours actually produced">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Robot cost incurred" value={money(a.robot_cost_incurred, { compact: true })} sub={`${n(scheduled, 0)} scheduled h (productive + downtime)`} />
        <Stat label="Productive cost rate" value={a.productive_robot_cost_rate != null ? `${money(a.productive_robot_cost_rate, { cents: true })}/h` : "—"} sub="Incurred cost ÷ productive hours" tone={a.productive_robot_cost_rate > 48 ? "warn" : undefined} />
        <Stat label="Idle-capacity leakage" value={money(leak, { compact: true })} tone={leak > 1000 ? "warn" : undefined} sub={`${n(a.hours.downtime, 0)}h downtime paid for but not productive`} />
        <Stat label="Blended uptime" value={plan ? pct(plan.drivers.uptime, 0) : "—"} sub="Blended observed uptime in the reforecast" />
      </div>
    </Card>
  );
}

function CloseoutCard({ id, check }: { id: string; check: any }) {
  const [err, setErr] = useState<string | null>(null);
  if (check.loading || !check.data) return null;
  const c = check.data;
  const closed = c.status === "closed";
  const go = async () => {
    setErr(null);
    try { await api(`/api/cohorts/${id}/status`, { body: { status: "closed" } }); check.refetch(); } catch (e: any) { setErr(e.message); }
  };
  return (
    <Card title={<>Closeout readiness<Hint>A cohort closes only when every device is sold, recycled or destroyed; all payouts and invoices are collected; and no critical alert is open. Closed cohorts are frozen.</Hint></>}
      actions={!closed && <button className="btn btn-primary" disabled={!c.ready} onClick={go}>Close cohort</button>}>
      {closed ? <Chip tone="neutral">Closed — frozen</Chip> : c.blockers.length === 0 ? <Chip tone="good">Ready to close</Chip> : (
        <ul className="space-y-1.5 text-sm">{c.blockers.map((b: any) => <li key={b.code} className="flex gap-2"><span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-bad" aria-hidden />{b.message}</li>)}</ul>
      )}
      {err && <p role="alert" className="mt-2 text-xs font-medium text-bad">{err}</p>}
    </Card>
  );
}
