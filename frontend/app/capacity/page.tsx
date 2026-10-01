"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { Card, Chip, DecisionChip, Empty, ErrorBox, Field, Loading, PageHeader, Table } from "@/components/ui";
import { cohortHref } from "@/lib/nav";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { dateShort, money, n, pct, titleCase } from "@/lib/format";

const COLORS = ["#14181f", "#e2542b", "#2b56a0", "#19704a", "#a8660b", "#6b4fa0"];

export default function Capacity() {
  const cal = useApi<any>("/api/capacity/calendar");
  const queue = useApi<any[]>("/api/capacity/queue");
  const [sel, setSel] = useState<string>("");
  const [cell, setCell] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [hours, setHours] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const codes = useMemo(() => {
    const s = new Set<string>();
    cal.data?.cells.forEach((c: any) => Object.values(c.allocation).forEach((a: any) => Object.keys(a).forEach((k) => s.add(k))));
    return [...s];
  }, [cal.data]);

  if (cal.loading || queue.loading) return <Loading />;
  if (cal.error || !cal.data) return <ErrorBox message={cal.error ?? "No data"} onRetry={cal.refetch} />;
  const { cells, today } = cal.data;
  const dates: string[] = cells[0]?.days.map((d: any) => d.date) ?? [];
  const schedulable = (queue.data ?? []).filter((q) => ["approved", "scheduled"].includes(q.status));
  const picked = (queue.data ?? []).find((q) => q.id === sel);

  const assign = async () => {
    setMsg(null);
    try {
      await api(`/api/cohorts/${sel}/assignments`, { body: { robot_cell_id: cell, start_date: start, end_date: end, ...(hours ? { planned_hours: Number(hours) } : {}) } });
      setMsg({ ok: true, text: "Capacity assigned." }); setHours(""); cal.refetch(); queue.refetch();
    } catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };

  return (
    <>
      <PageHeader title="Capacity planner" subtitle="Which robot cells have room, and is a profitable cohort actually processable before its SLA? Overbooking, blocked cells and SLA breaches are rejected by the backend." />

      <Card title="Robot-cell calendar" subtitle="Each block is one working day. Fill = assigned ÷ available hours; colour = cohort. Hatched = maintenance / no capacity." className="mb-4">
        <div className="overflow-x-auto">
          <div className="min-w-[760px]">
            {cells.map((c: any) => (
              <div key={c.id} className="mb-2 flex items-center gap-3">
                <div className="w-28 shrink-0">
                  <div className="text-sm font-medium">{c.cell_name}</div>
                  <div className="text-xs text-muted">{c.status === "available" ? `${n(c.free_hours, 0)}h free ahead` : titleCase(c.status)}</div>
                </div>
                <div className="flex flex-1 gap-[3px]">
                  {c.days.map((d: any) => {
                    const alloc = c.allocation[d.date] ?? {};
                    const entries = Object.entries(alloc) as [string, number][];
                    const avail = +d.available;
                    const blocked = avail <= 0;
                    return (
                      <div key={d.date} title={`${c.cell_name} · ${dateShort(d.date)}\n${n(+d.assigned, 1)}h assigned of ${n(avail, 1)}h available${d.maintenance > 0 ? `\n${d.maintenance}h maintenance` : ""}${entries.map(([k, h]) => `\n${k}: ${n(h, 1)}h`).join("")}`}
                        className={`relative flex h-9 flex-1 flex-col-reverse overflow-hidden rounded-[3px] border ${d.date === today ? "border-brand" : "border-line"} ${blocked ? "bg-[repeating-linear-gradient(45deg,#ebe7dd,#ebe7dd_3px,#f5f3ee_3px,#f5f3ee_6px)]" : "bg-surface"}`}>
                        {entries.map(([k, h]) => (
                          <div key={k} style={{ height: `${Math.min((h / Math.max(avail, 1)) * 100, 100)}%`, background: COLORS[codes.indexOf(k) % COLORS.length] }} />
                        ))}
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
            <div className="mt-1 flex items-center gap-3 pl-[7.75rem] text-[10px] text-muted">
              <span>{dateShort(dates[0])}</span><span className="flex-1 border-t border-dashed border-line" /><span className="text-brand">today {dateShort(today)}</span><span className="flex-1 border-t border-dashed border-line" /><span>{dateShort(dates[dates.length - 1])}</span>
            </div>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-3 text-xs">
          {codes.map((k, i) => <span key={k} className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: COLORS[i % COLORS.length] }} />{k}</span>)}
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2" title="Priority queue" subtitle="Cohorts competing for robot hours, ranked by CM per robot hour, margin, exception risk and cash efficiency" pad={false}>
          <Table>
            <thead className="border-b border-line"><tr><th className="th">Rank</th><th className="th">Cohort</th><th className="th">Decision</th><th className="th text-right">CM / robot-hr</th><th className="th text-right">Hours needed</th><th className="th text-right">Assigned</th><th className="th text-right">Score</th></tr></thead>
            <tbody className="divide-y divide-line">
              {(queue.data ?? []).map((q, i) => {
                const short = q.scheduled_hours_needed - q.assigned_hours;
                return (
                  <tr key={q.id} className={sel === q.id ? "bg-paper" : ""}>
                    <td className="td num text-muted">{i + 1}</td>
                    <td className="td"><Link href={cohortHref(q.id)} className="num font-semibold hover:underline">{q.code}</Link><div className="text-xs text-muted">SLA {dateShort(q.target_completion_date)} · {titleCase(q.status)}</div></td>
                    <td className="td">{q.status === "under_review" ? <Chip tone="info">Awaiting decision</Chip> : <Chip tone="good">Approved</Chip>}
                      {q.sla_feasible === false && <div className="mt-1"><Chip tone="bad">Not enough free hours before SLA</Chip></div>}
                      {q.review_required && <div className="mt-1"><Chip tone="warn">Review-slot risk</Chip></div>}</td>
                    <td className="td num text-right">{money(q.cm_per_robot_hour)}</td>
                    <td className="td num text-right">{n(q.scheduled_hours_needed, 0)}h</td>
                    <td className={`td num text-right ${short > 1 && q.status !== "under_review" ? "text-warn" : ""}`}>{n(q.assigned_hours, 0)}h</td>
                    <td className="td num text-right font-semibold">{n(q.priority_score, 0)}</td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        </Card>

        <Card title="Assign robot time" subtitle="Only approved cohorts can be scheduled">
          {schedulable.length === 0 ? <Empty>No approved cohort is waiting for capacity. Approve a cohort from Underwriting first.</Empty> : (
            <div className="space-y-3">
              <Field label="Cohort"><select className="input" value={sel} onChange={(e) => { setSel(e.target.value); const q = schedulable.find((x) => x.id === e.target.value); setStart(q?.intake_date ?? ""); setEnd(q?.target_completion_date ?? ""); setMsg(null); }}>
                <option value="">Select…</option>{schedulable.map((q) => <option key={q.id} value={q.id}>{q.code}</option>)}</select></Field>
              <Field label="Robot cell"><select className="input" value={cell} onChange={(e) => setCell(e.target.value)}>
                <option value="">Select…</option>{cells.map((c: any) => <option key={c.id} value={c.id}>{c.cell_name} — {titleCase(c.status)}</option>)}</select></Field>
              <div className="grid grid-cols-2 gap-2">
                <Field label="Start"><input type="date" className="input" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
                <Field label="End"><input type="date" className="input" value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
              </div>
              <Field label="Hours to place" hint={picked ? `Needs ${n(picked.scheduled_hours_needed, 0)}h in total · ${n(Math.max(picked.scheduled_hours_needed - picked.assigned_hours, 0), 0)}h still unplaced. Leave blank to place all of it; enter fewer to split across cells.` : undefined}>
                <input type="number" min={0} step="any" className="input num" value={hours} onChange={(e) => setHours(e.target.value)} placeholder="all remaining" />
              </Field>
              {msg && <p role="alert" className={`rounded-lg p-2 text-xs font-medium ${msg.ok ? "bg-good-bg text-good" : "bg-bad-bg text-bad"}`}>{msg.text}</p>}
              <button className="btn btn-primary w-full" disabled={!sel || !cell || !start || !end} onClick={assign}>Assign</button>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
