"use client";
import { CheckCircle2, CircleAlert, Info, XCircle } from "lucide-react";
import { Bar, Card, Chip, DecisionChip, Hint, Table } from "@/components/ui";
import { money, n, pct } from "@/lib/format";

export function ChecksList({ checks }: { checks: any[] }) {
  const icon = (s: string) =>
    s === "pass" ? <CheckCircle2 className="h-4 w-4 text-good" /> : s === "fail" ? <XCircle className="h-4 w-4 text-bad" /> : s === "warn" ? <CircleAlert className="h-4 w-4 text-warn" /> : <Info className="h-4 w-4 text-muted" />;
  return (
    <ul className="divide-y divide-line">
      {checks.map((c) => (
        <li key={c.key} className="flex items-start gap-3 py-2.5">
          <span className="mt-0.5" aria-label={c.status}>{icon(c.status)}</span>
          <div className="min-w-0">
            <div className="text-sm font-medium">{c.label}</div>
            <div className="text-xs text-muted">{c.detail}</div>
          </div>
        </li>
      ))}
    </ul>
  );
}

export const GROSS_TIP =
  "This is an informational recovery-efficiency metric. It is not used for acceptance, prioritization, partner scoring, margin-floor alerts, or operating policy. All policy decisions use contribution margin as a percentage of net recognized revenue.";

const ROWS: { key: string; label: string; fmt: (v: any) => string; strong?: boolean; hint?: string; sub?: boolean }[] = [
  { key: "gross_sale_proceeds", label: "Gross sale proceeds", fmt: (v) => money(v), hint: "Sale prices of devices sold, before fees, refunds or discounts. Kept as a separate field from every deduction — never blended into a single 'net price'." },
  { key: "marketplace_fees", label: "− Marketplace fees", fmt: (v) => money(v), sub: true, hint: "Deducted from revenue exactly once (not also counted as a cost)." },
  { key: "refunds", label: "− Refunds / returns", fmt: (v) => money(v), sub: true, hint: "Revenue reversed for returned devices. Return shipping is a separate direct cost." },
  { key: "discounts", label: "− Discounts", fmt: (v) => money(v), sub: true },
  { key: "net_recognized_revenue", label: "Net recognized revenue", fmt: (v) => money(v), strong: true, hint: "Gross sale proceeds − marketplace fees − refunds − discounts (+ invoiced service fees). This is the denominator of the policy CM%." },
  { key: "merchant_shipping_cost", label: "Merchant-paid shipping (direct cost)", fmt: (v) => money(v), hint: "Outbound shipping plus return labels. A direct variable cost — never a revenue deduction." },
  { key: "direct_cost", label: "Direct variable cost", fmt: (v) => money(v), hint: "Acquisition (or partner share) + inbound logistics + committed robot cost + manual labour + repair parts + merchant-paid shipping." },
  { key: "contribution_margin", label: "Contribution margin", fmt: (v) => money(v), strong: true, hint: "CM = net recognized revenue − direct variable cost." },
  { key: "cm_pct", label: "CM % of net recognized revenue", fmt: (v) => pct(v), strong: true, hint: "The authoritative policy metric: CM ÷ net recognized revenue. Acceptance, downside risk, margin-floor alerts, partner scoring and prioritization all use it. Shown as n/a when net revenue is zero or negative." },
  { key: "cm_pct_gross_sale_proceeds", label: "CM % of Gross Sale Proceeds", fmt: (v) => pct(v), hint: GROSS_TIP },
  { key: "margin_per_incoming_device", label: "Margin per incoming device", fmt: (v) => money(v) },
  { key: "margin_per_sale", label: "Margin per successful sale", fmt: (v) => money(v) },
  { key: "cm_per_robot_hour", label: "CM per productive robot hour", fmt: (v) => money(v) },
  { key: "committed_robot_cost", label: "Committed robot cost", fmt: (v) => money(v), hint: "Scheduled cohort hours × hourly cost. This is the economic commitment: capacity is paid for even when downtime or slow processing prevents output. It is what the cohort margin uses." },
  { key: "productive_robot_cost", label: "Productive robot cost", fmt: (v) => money(v), hint: "Only the hours that produced output × hourly cost. The gap to committed cost is idle-capacity leakage." },
  { key: "idle_capacity_leakage", label: "Idle-capacity leakage", fmt: (v) => money(v), hint: "Committed − productive robot cost. Low uptime or utilisation shows up here first." },
  { key: "cash_required", label: "Cash required before recovery", fmt: (v) => money(v), hint: "Acquisition + inbound + robot + labour + repair, all paid before the first sale receipt. Fees, discounts and shipping are netted from payouts, so they are not upfront cash." },
  { key: "break_even_resale_price", label: "Break-even resale price", fmt: (v) => (v == null ? "n/a" : money(v)) },
  { key: "break_even_sell_through", label: "Break-even sell-through", fmt: (v) => (v == null ? "n/a" : pct(v, 0)) },
  { key: "exception_rate", label: "Expected exception rate", fmt: (v) => pct(v) },
  { key: "first_pass_yield", label: "Expected first-pass yield", fmt: (v) => pct(v), hint: "First-pass yield counts only devices that finish without manual intervention. A repaired device is recovered only if it then passes repair and quality control (85% by default) — and it never counts toward first-pass yield." },
  { key: "scheduled_robot_hours", label: "Scheduled robot hours", fmt: (v) => n(v, 0) },
  { key: "collection_days", label: "Days to cash (sale + payout)", fmt: (v) => `${v}` },
];

export function ScenarioTable({ scenarios }: { scenarios: any }) {
  return (
    <Table>
      <thead className="border-b border-line">
        <tr><th className="th">Metric</th><th className="th text-right">Downside</th><th className="th text-right">Base</th><th className="th text-right">Upside</th></tr>
      </thead>
      <tbody className="divide-y divide-line">
        {ROWS.map((r) => (
          <tr key={r.key} className={r.strong ? "bg-paper/60" : ""}>
            <td className={`td ${r.strong ? "font-semibold" : ""} ${r.sub ? "pl-6 text-muted" : ""}`}>{r.label}{r.hint && <Hint>{r.hint}</Hint>}</td>
            {(["downside", "base", "upside"] as const).map((s) => {
              const v = scenarios[s][r.key];
              const neg = typeof v === "number" && v < 0 && (r.key === "contribution_margin" || r.key === "cm_pct" || r.key === "cm_pct_gross_sale_proceeds");
              return <td key={s} className={`td num text-right ${r.strong ? "font-semibold" : ""} ${neg ? "text-bad" : ""}`}>{r.fmt(v)}</td>;
            })}
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

export function MoneyFlow({ base }: { base: any }) {
  const deductions = [
    { label: "Marketplace fees", v: base.marketplace_fees },
    { label: "Refunds / returns", v: base.refunds },
    { label: "Discounts", v: base.discounts },
  ].filter((i) => i.v > 0);
  const costs = [
    { label: "Acquisition + partner share", v: base.acquisition_cost + (base.partner_revenue_share || 0) },
    { label: "Committed robot cost", v: base.robot_cost },
    { label: "Manual exception labour", v: base.manual_labor_cost },
    { label: "Repair parts", v: base.repair_cost },
    { label: "Inbound logistics", v: base.inbound_logistics_cost },
    { label: "Merchant-paid shipping", v: base.merchant_shipping_cost },
  ].filter((i) => i.v > 0);
  const gross = Math.max(base.gross_sale_proceeds, base.net_recognized_revenue, 1);
  const net = base.net_recognized_revenue;
  return (
    <ul className="space-y-2.5">
      {base.gross_sale_proceeds > 0 && (
        <li>
          <div className="mb-1 flex justify-between text-sm"><span>Gross sale proceeds</span><span className="num">{money(base.gross_sale_proceeds)}</span></div>
          <Bar value={base.gross_sale_proceeds} max={gross} tone="neutral" />
        </li>
      )}
      {deductions.map((i) => (
        <li key={i.label}>
          <div className="mb-1 flex justify-between text-sm text-muted"><span>− {i.label}</span><span className="num">{money(i.v)}</span></div>
          <Bar value={i.v} max={gross} tone="warn" />
        </li>
      ))}
      <li className="border-t border-line pt-2">
        <div className="mb-1 flex justify-between text-sm"><span className="font-medium">Net recognized revenue</span><span className="num font-semibold">{money(net)}</span></div>
        <Bar value={Math.max(net, 0)} max={gross} tone="good" />
      </li>
      {costs.map((i) => (
        <li key={i.label}>
          <div className="mb-1 flex justify-between text-sm"><span>{i.label}</span><span className="num">{money(i.v)} <span className="text-muted">· {net > 0 ? pct(i.v / net, 0) : "n/a"} of net</span></span></div>
          <Bar value={i.v} max={gross} />
        </li>
      ))}
      <li className="border-t border-line pt-2">
        <div className="mb-1 flex justify-between text-sm"><span className="font-semibold">Contribution margin</span><span className={`num font-semibold ${base.contribution_margin < 0 ? "text-bad" : ""}`}>{money(base.contribution_margin)} · {pct(base.cm_pct)}</span></div>
        <Bar value={Math.max(base.contribution_margin, 0)} max={gross} tone={base.contribution_margin < 0 ? "bad" : "good"} />
      </li>
    </ul>
  );
}

export function RecommendationCard({ uw }: { uw: any }) {
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-3">
        <div className="eyebrow">System recommendation</div>
        <div className="scale-110 origin-left"><DecisionChip decision={uw.decision} /></div>
        <Chip>Priority score {n(uw.priority_score, 0)}</Chip>
      </div>
      <ul className="mt-3 space-y-1.5 text-sm">
        {uw.reasons.map((r: string, i: number) => <li key={i} className="flex gap-2"><span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-ink" aria-hidden />{r}</li>)}
      </ul>
      {uw.decision === "accept" ? null : uw.conditions.length > 0 && uw.conditions.join() !== uw.reasons.join() && (
        <div className="mt-4 rounded-lg bg-warn-bg p-3 text-sm text-warn">
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide">Conditions to attach</div>
          <ul className="space-y-1">{uw.conditions.map((c: string, i: number) => <li key={i}>{c}</li>)}</ul>
        </div>
      )}
    </Card>
  );
}

export function LinesTable({ lines }: { lines: any[] }) {
  return (
    <Table>
      <thead className="border-b border-line">
        <tr>
          <th className="th">Model</th><th className="th">Grade</th><th className="th text-right">Units</th><th className="th text-right">Min/device</th>
          <th className="th text-right">Exception</th><th className="th text-right">FPY</th><th className="th text-right">Repair</th>
          <th className="th text-right">Resale</th><th className="th text-right">Sell-through</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-line">
        {lines.map((l, i) => (
          <tr key={i}>
            <td className="td">{l.model_name}</td><td className="td">{l.condition_grade}</td>
            <td className="td num text-right">{n(l.units)}</td><td className="td num text-right">{n(+l.process_minutes, 0)}</td>
            <td className="td num text-right">{pct(+l.exception_rate, 0)}</td><td className="td num text-right">{pct(+l.first_pass_yield, 0)}</td>
            <td className="td num text-right">{money(+l.repair_cost)}</td><td className="td num text-right">{money(+l.resale_price)}</td>
            <td className="td num text-right">{pct(+l.sell_through, 0)}</td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

