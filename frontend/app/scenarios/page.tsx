"use client";
import { useEffect, useState } from "react";
import { Card, ErrorBox, Field, Loading, PageHeader, Stat } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { money, n, pct, signedMoney } from "@/lib/format";

const LEVERS = [
  { k: "price_mult", label: "Resale price", min: 0.7, max: 1.3, step: 0.01, def: 1, fmt: (v: number) => `${v >= 1 ? "+" : "−"}${Math.abs((v - 1) * 100).toFixed(0)}%` },
  { k: "sell_through_mult", label: "Sell-through", min: 0.6, max: 1.2, step: 0.01, def: 1, fmt: (v: number) => `${v >= 1 ? "+" : "−"}${Math.abs((v - 1) * 100).toFixed(0)}%` },
  { k: "exception_mult", label: "Exception rate", min: 0.5, max: 3, step: 0.05, def: 1, fmt: (v: number) => `×${v.toFixed(2)}` },
  { k: "process_mult", label: "Process time / device", min: 0.8, max: 1.6, step: 0.01, def: 1, fmt: (v: number) => `${v >= 1 ? "+" : "−"}${Math.abs((v - 1) * 100).toFixed(0)}%` },
  { k: "repair_mult", label: "Repair cost / exception", min: 0.5, max: 2.5, step: 0.05, def: 1, fmt: (v: number) => `×${v.toFixed(2)}` },
  { k: "volume_mult", label: "Volume", min: 0.5, max: 1.5, step: 0.01, def: 1, fmt: (v: number) => `${v >= 1 ? "+" : "−"}${Math.abs((v - 1) * 100).toFixed(0)}%` },
  { k: "uptime", label: "Robot uptime (absolute)", min: 0.4, max: 1, step: 0.01, def: 0.9, fmt: (v: number) => `${(v * 100).toFixed(0)}%` },
];

export default function Scenarios() {
  const cohorts = useApi<any[]>("/api/cohorts");
  const policy = useApi<any>("/api/policy");
  const [id, setId] = useState("");
  const [vals, setVals] = useState<Record<string, number>>(Object.fromEntries(LEVERS.map((l) => [l.k, l.def])));
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { if (!id && cohorts.data?.length) setId((cohorts.data.find((c) => c.status === "under_review") ?? cohorts.data[0]).id); }, [cohorts.data, id]);
  useEffect(() => {
    if (!id) return;
    const t = setTimeout(async () => {
      try { setErr(null); setRes(await api(`/api/cohorts/${id}/scenario`, { body: vals })); } catch (e: any) { setErr(e.message); }
    }, 250);
    return () => clearTimeout(t);
  }, [id, vals]);

  if (cohorts.loading) return <Loading />;
  if (cohorts.error) return <ErrorBox message={cohorts.error} />;
  const eligible = (cohorts.data ?? []).filter((c) => c.status !== "draft");
  const b = res?.base, s = res?.scenario;

  return (
    <>
      <PageHeader title="Scenario planner" subtitle="What happens to margin and cash if volume, resale price, uptime or exceptions change? Every scenario is run by the backend engine against the cohort's own assumptions." />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Levers" subtitle="Applied on top of the cohort's base assumptions" actions={<button className="btn" onClick={() => setVals(Object.fromEntries(LEVERS.map((l) => [l.k, l.def])))}>Reset</button>}>
          <Field label="Cohort" className="mb-4"><select className="input" value={id} onChange={(e) => setId(e.target.value)}>{eligible.map((c) => <option key={c.id} value={c.id}>{c.code} — {c.name}</option>)}</select></Field>
          <div className="space-y-4">
            {LEVERS.map((l) => (
              <div key={l.k}>
                <div className="mb-1 flex justify-between text-sm"><label htmlFor={l.k} className="font-medium">{l.label}</label><span className="num font-semibold">{l.fmt(vals[l.k])}</span></div>
                <input id={l.k} type="range" min={l.min} max={l.max} step={l.step} value={vals[l.k]} onChange={(e) => setVals({ ...vals, [l.k]: Number(e.target.value) })} className="w-full accent-[#14181f]" />
              </div>
            ))}
          </div>
        </Card>

        <div className="space-y-4 lg:col-span-2">
          {err && <ErrorBox message={err} />}
          {!res ? <Loading /> : (
            <>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <Stat label="Contribution margin" value={money(s.contribution_margin, { compact: true })} tone={s.contribution_margin < 0 ? "bad" : undefined} sub={<>Base {money(b.contribution_margin, { compact: true })} · <b className={res.delta_cm < 0 ? "text-bad" : "text-good"}>{signedMoney(res.delta_cm)}</b></>} />
                <Stat label="CM % of net recognized revenue" value={pct(s.cm_pct)} sub={`Base ${pct(b.cm_pct)} · accept line ${policy.data ? pct(policy.data.base_case_accept_cm_pct_net_revenue, 0) : "…"}`} tone={s.cm_pct == null || !policy.data ? undefined : s.cm_pct < policy.data.base_case_review_cm_pct_net_revenue ? "bad" : s.cm_pct < policy.data.base_case_accept_cm_pct_net_revenue ? "warn" : "good"} />
                <Stat label="CM / robot hour" value={money(s.cm_per_robot_hour)} sub={`Base ${money(b.cm_per_robot_hour)}`} />
                <Stat label="Cash required" value={money(s.cash_required, { compact: true })} sub={`Base ${money(b.cash_required, { compact: true })}`} />
              </div>
              <Card title="What moves margin most" subtitle="Each lever swung ±10% on its own, from the base case. Bars show change in contribution margin.">
                <Tornado rows={res.tornado} />
                <p className="mt-2 text-xs text-muted">Light = lever 10% lower, dark = lever 10% higher. Break-even resale price in this scenario: {s.break_even_resale_price ? money(s.break_even_resale_price) : "n/a"} · break-even sell-through: {s.break_even_sell_through != null ? pct(s.break_even_sell_through, 0) : "n/a"} · exception rate {pct(s.exception_rate)} · {n(s.units_sold, 0)} devices sold.</p>
              </Card>
            </>
          )}
        </div>
      </div>
    </>
  );
}

/** Diverging bars drawn directly: each lever swung -10% (light) and +10% (dark) from the base case. */
function Tornado({ rows }: { rows: any[] }) {
  const max = Math.max(...rows.flatMap((r) => [Math.abs(r.delta_low), Math.abs(r.delta_high)]), 1);
  const bar = (v: number, dark: boolean) => (
    <div className="relative h-3">
      <div
        className={`absolute top-0 h-3 ${dark ? "bg-ink" : "bg-[#b9b3a3]"} ${v >= 0 ? "left-1/2 rounded-r" : "right-1/2 rounded-l"}`}
        style={{ width: `${(Math.abs(v) / max) * 50}%` }}
      />
    </div>
  );
  return (
    <div role="img" aria-label="Tornado chart: change in contribution margin when each lever moves 10% down or up">
      <div className="mb-2 flex justify-between text-[11px] text-muted"><span>← lowers margin</span><span>raises margin →</span></div>
      <ul className="space-y-3">
        {rows.map((r) => (
          <li key={r.lever} className="grid grid-cols-[130px_1fr_110px] items-center gap-3">
            <span className="text-sm">{r.label}</span>
            <div className="relative space-y-1">
              <div className="absolute inset-y-0 left-1/2 w-px bg-ink/40" />
              {bar(r.delta_low, false)}
              {bar(r.delta_high, true)}
            </div>
            <span className="num text-right text-[11px] leading-tight text-muted">
              −10%: {signedMoney(r.delta_low)}<br />+10%: {signedMoney(r.delta_high)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
