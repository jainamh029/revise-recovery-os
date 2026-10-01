"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useMemo } from "react";
import { ArrowLeft, CheckCircle2, Download, TriangleAlert } from "lucide-react";
import { Card, Chip, Empty, ErrorBox, Loading, PageHeader, Stat, Table } from "@/components/ui";
import { FILTER_KEYS } from "@/components/DashboardFilters";
import { downloadFile } from "@/lib/download";
import { useApi } from "@/lib/hooks";
import { normalizeHref } from "@/lib/nav";
import { dateShort, money, n, pct, titleCase } from "@/lib/format";

type Col = { key: string; label: string; type: string; href_key?: string };

const fmtCell = (c: Col, v: any, row: any) => {
  if (v === null || v === undefined || v === "") return <span className="text-muted">—</span>;
  switch (c.type) {
    case "money": return <span className="num">{money(v, { cents: false })}</span>;
    case "num": return <span className="num">{n(+v, 1)}</span>;
    case "int": return <span className="num">{n(+v)}</span>;
    case "pct": return <span className="num">{pct(+v, 1)}</span>;
    case "date": return <span className="num">{String(v).length > 10 ? dateShort(String(v).slice(0, 10)) : dateShort(String(v))}</span>;
    case "chip": return <Chip>{titleCase(String(v))}</Chip>;
    case "link": return c.href_key && row[c.href_key] ? <Link className="font-semibold underline underline-offset-2" href={normalizeHref(row[c.href_key])!}>{String(v)}</Link> : <span>{String(v)}</span>;
    default: return <span>{String(v)}</span>;
  }
};
const right = (t: string) => ["money", "num", "int", "pct"].includes(t);

/** One backend-driven detail view. Every figure (rows, totals, reconciliation) comes from /api/drilldown; nothing is computed here. */
export default function DrillPage({ metric }: { metric: string }) {
  return (
    <Suspense fallback={<Loading />}>
      <Inner metric={metric} />
    </Suspense>
  );
}

function Inner({ metric }: { metric: string }) {
  const sp = useSearchParams();
  const all = useMemo(() => { const p = new URLSearchParams(sp.toString()); p.delete("metric"); return p; }, [sp]);
  const back = useMemo(() => { const p = new URLSearchParams(); FILTER_KEYS.forEach((k) => sp.get(k) && p.set(k, sp.get(k)!)); return p.toString(); }, [sp]);
  const qs = all.toString();
  const { data: d, error, loading, refetch } = useApi<any>(`/api/drilldown/${metric}${qs ? `?${qs}` : ""}`);
  const backHref = back ? `/?${back}` : "/";

  const header = (title: string) => (
    <>
      <Link href={backHref} className="btn mb-3" data-testid="back-to-dashboard"><ArrowLeft className="h-4 w-4" /> Back to filtered dashboard</Link>
      <PageHeader title={title} />
    </>
  );
  if (error) return <>{header("Drill-down")}<ErrorBox message={error} onRetry={refetch} /></>;
  if (loading || !d) return <>{header("Drill-down")}<Loading label="Loading detail" /></>;

  const r = d.reconciliation;
  const ok = r.matches === true;
  return (
    <>
      <Link href={backHref} className="btn mb-3" data-testid="back-to-dashboard"><ArrowLeft className="h-4 w-4" /> Back to filtered dashboard</Link>
      <PageHeader
        title={d.title}
        subtitle={<span className="flex flex-wrap items-center gap-2"><Chip tone={d.basis === "dated" ? "info" : "neutral"} className="!text-[12px]"><span data-testid="basis-label">{d.basis_label}</span></Chip></span>}
        actions={d.tables.map((t: any, i: number) => (
          <button key={t.id} className="btn" data-testid={i === 0 ? "export-csv" : `export-csv-${t.id}`}
            onClick={() => downloadFile(`/api/drilldown/${metric}/export.csv?${new URLSearchParams([...all.entries(), ...(i ? [["table", t.id]] : [])] as any)}`, `${metric}${i ? `-${t.id}` : ""}.csv`)}>
            <Download className="h-4 w-4" /> Export CSV{d.tables.length > 1 ? ` · ${t.id}` : ""}
          </button>
        ))}
      />

      {d.filters.applied.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-1.5 text-xs" data-testid="drill-filters">
          <span className="text-muted">Same filters as the dashboard:</span>
          {d.filters.applied.map((a: any) => <Chip key={a.key} tone="info">{a.label}: {a.value}</Chip>)}
        </div>
      )}

      <Card className={`mb-4 ${ok ? "border-good/40" : r.matches === false ? "border-bad/50" : ""}`}>
        <div className="flex items-start gap-3" data-testid="reconciliation">
          {ok ? <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-good" aria-hidden /> : <TriangleAlert className={`mt-0.5 h-5 w-5 shrink-0 ${r.matches === false ? "text-bad" : "text-muted"}`} aria-hidden />}
          <div>
            <p className="text-sm font-medium" data-testid="reconciliation-statement">{r.statement || "No dashboard card to reconcile against for this selection."}</p>
            {r.numerator !== undefined && <p className="num mt-1 text-xs text-muted" data-testid="ratio-parts">numerator {n(+r.numerator, 1)} ÷ denominator {n(+r.denominator, 1)}</p>}
            <p className="mt-1 text-xs text-muted">{r.formula}</p>
          </div>
        </div>
      </Card>

      {d.sections && (
        <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4" data-testid="sections">
          <Stat id="sec-inventory" label="1 · Inventory at cost" value={money(d.sections.inventory, { compact: true })} sub="= dashboard card" />
          <Stat id="sec-payouts" label="2 · Uncollected payouts" value={money(d.sections.payouts, { compact: true })} />
          <Stat id="sec-invoices" label="3 · Service invoices" value={money(d.sections.invoices, { compact: true })} />
          <Stat id="sec-total" label="Total current exposure" value={money(d.sections.inventory + d.sections.payouts + d.sections.invoices, { compact: true })} sub="1 + 2 + 3" />
        </div>
      )}

      {d.notes.length > 0 && (
        <ul className="mb-4 space-y-1.5 rounded-xl border border-line bg-paper/60 p-3 text-xs text-muted" data-testid="notes">
          {d.notes.map((t: string, i: number) => <li key={i} className="flex gap-2"><span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-muted" aria-hidden />{t}</li>)}
        </ul>
      )}

      {d.empty && <div className="mb-4" data-testid="drill-empty"><Empty>No records match these filters. The total is zero and nothing is hidden.</Empty></div>}

      <div className="space-y-4">
        {d.tables.map((t: any) => (
          <Card key={t.id} title={t.title} subtitle={`${t.rows.length} row${t.rows.length === 1 ? "" : "s"}`} pad={false}>
            <div data-testid={`table-${t.id}`}>
              <Table>
                <thead className="border-b border-line"><tr>{t.columns.map((c: Col) => <th key={c.key} className={`th whitespace-nowrap ${right(c.type) ? "text-right" : ""}`}>{c.label}</th>)}</tr></thead>
                <tbody className="divide-y divide-line">
                  {t.rows.map((row: any, i: number) => (
                    <tr key={i} className="hover:bg-paper/60">
                      {t.columns.map((c: Col) => <td key={c.key} className={`td ${right(c.type) ? "text-right" : ""} ${c.type === "text" && String(row[c.key] ?? "").length > 40 ? "min-w-[260px]" : ""}`}>{fmtCell(c, row[c.key], row)}</td>)}
                    </tr>
                  ))}
                  {t.rows.length === 0 && <tr><td className="td text-muted" colSpan={t.columns.length}>No rows.</td></tr>}
                </tbody>
                <tfoot className="border-t-2 border-line bg-paper/60" data-testid={`totals-${t.id}`}>
                  <tr>
                    {t.columns.map((c: Col, i: number) => {
                      const v = t.totals?.[c.key];
                      const show = v !== undefined && v !== null && typeof v !== "object";
                      return <td key={c.key} className={`td font-semibold ${right(c.type) ? "text-right" : ""}`}>{i === 0 ? <>Total{show ? " · " : ""}{show ? fmtCell(c, v, {}) : ""}</> : show ? fmtCell(c, v, {}) : ""}</td>;
                    })}
                  </tr>
                </tfoot>
              </Table>
            </div>
          </Card>
        ))}
      </div>
    </>
  );
}
