"use client";
import Link from "next/link";
import { Plus } from "lucide-react";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import DrillPage from "@/components/DrillPage";
import { Card, DecisionChip, ErrorBox, Hint, Loading, PageHeader, StatusChip, Tabs, Table } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { dateShort, money, n, pct } from "@/lib/format";
import { cohortHref } from "@/lib/nav";

const GROUPS: Record<string, (s: string) => boolean> = {
  all: () => true,
  decision: (s) => s === "under_review" || s === "draft",
  committed: (s) => s === "approved" || s === "scheduled",
  active: (s) => ["in_intake", "in_processing", "exception_review", "quality_control", "listed_for_sale", "partially_sold"].includes(s),
  done: (s) => ["closed", "declined", "cancelled"].includes(s),
};

function Cohorts() {
  const { data, error, loading, refetch } = useApi<any[]>("/api/cohorts");
  const [tab, setTab] = useState("all");
  if (loading) return <Loading />;
  if (error || !data) return <ErrorBox message={error ?? "No data"} onRetry={refetch} />;
  const rows = data.filter((c) => GROUPS[tab](c.status)).sort((a, b) => (b.priority_score ?? 0) - (a.priority_score ?? 0));
  const count = (k: string) => data.filter((c) => GROUPS[k](c.status)).length;

  return (
    <>
      <PageHeader
        title="Cohort underwriting"
        subtitle="Should we accept this batch before committing capital and robot hours? Every cohort is underwritten in base, downside and upside cases against policy."
        actions={<Link href="/cohorts/new" className="btn btn-primary"><Plus className="h-4 w-4" /> Underwrite a cohort</Link>}
      />
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "all", label: `All (${count("all")})` },
          { id: "decision", label: `Awaiting decision (${count("decision")})` },
          { id: "committed", label: `Approved (${count("committed")})` },
          { id: "active", label: `In flight (${count("active")})` },
          { id: "done", label: `Declined / closed (${count("done")})` },
        ]}
      />
      <Card pad={false}>
        <Table>
          <thead className="border-b border-line">
            <tr>
              <th className="th">Cohort</th><th className="th">Status</th><th className="th">System recommendation</th>
              <th className="th text-right">Devices</th><th className="th text-right">Plan CM<Hint>Contribution margin and CM% of net recognized revenue (gross sale proceeds − fees − refunds − discounts). All policy thresholds use this basis.</Hint></th><th className="th text-right">Forecast CM</th>
              <th className="th text-right">CM / robot-hr</th><th className="th text-right">Cash required</th><th className="th text-right">Priority</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((c) => (
              <tr key={c.id} className="hover:bg-paper/60">
                <td className="td">
                  <Link href={cohortHref(c.id)} className="font-semibold hover:underline"><span className="num">{c.code}</span></Link>
                  <div className="text-xs text-muted">{c.partner} · intake {dateShort(c.intake_date)}</div>
                </td>
                <td className="td"><StatusChip status={c.status} /></td>
                <td className="td"><DecisionChip decision={c.recommendation} /></td>
                <td className="td num text-right">{n(c.devices)}</td>
                <td className="td num text-right">{pct(c.base_cm_pct, 0)}<div className="text-xs text-muted">{money(c.base_cm, { compact: true })}</div></td>
                <td className={`td num text-right ${c.below_floor ? "font-semibold text-bad" : ""}`}>
                  {c.forecast_cm_pct != null ? <>{pct(c.forecast_cm_pct, 0)}<div className="text-xs">{money(c.forecast_cm, { compact: true })}</div></> : <span className="text-muted">—</span>}
                </td>
                <td className="td num text-right">{money(c.cm_per_robot_hour_plan)}</td>
                <td className="td num text-right">{money(c.cash_required, { compact: true })}</td>
                <td className="td num text-right">{c.priority_score != null ? n(c.priority_score, 0) : "—"}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td className="td text-muted" colSpan={9}>No cohorts in this view.</td></tr>}
          </tbody>
        </Table>
      </Card>
      <p className="mt-3 text-xs text-muted">Priority score ranks cohorts competing for scarce robot hours — dominated by contribution margin per robot hour, then margin %, exception risk and cash efficiency (weights are configurable in Policy).</p>
    </>
  );
}

/** /cohorts?status=active&<dashboard filters> is the "Active cohorts" drill-down; otherwise the normal pipeline list. */
function Switch() {
  const sp = useSearchParams();
  return sp.get("status") === "active" ? <DrillPage metric="active_cohorts" /> : <Cohorts />;
}

export default function CohortsPage() {
  return <Suspense fallback={<Loading />}><Switch /></Suspense>;
}
