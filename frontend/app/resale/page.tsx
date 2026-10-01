"use client";
import Link from "next/link";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { C, axis, gridProps, tooltipStyle } from "@/components/charts";
import { Card, Chip, Empty, ErrorBox, Field, Loading, PageHeader, Stat, Table } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { dateShort, money, n } from "@/lib/format";

export default function Resale() {
  const aging = useApi<any>("/api/collections/aging");
  const inv = useApi<any>("/api/analytics/inventory-aging");
  const listings = useApi<any[]>("/api/resale-listings");
  const cohorts = useApi<any[]>("/api/cohorts");
  const dash = useApi<any>("/api/dashboard/executive");
  const [payAmt, setPayAmt] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<{ id: string; ok: boolean; text: string } | null>(null);

  if (aging.loading || inv.loading) return <Loading />;
  if (aging.error || !aging.data) return <ErrorBox message={aging.error ?? "No data"} onRetry={aging.refetch} />;
  const a = aging.data;
  const codes = Object.fromEntries((cohorts.data ?? []).map((c) => [c.id, c.code]));
  const refresh = () => { aging.refetch(); inv.refetch(); listings.refetch(); dash.refetch(); };

  const receive = async (it: any) => {
    const amount = Number(payAmt[it.id] ?? it.remaining);
    try {
      await api("/api/cash-receipts", { body: { [it.kind === "invoice" ? "invoice_id" : "sales_transaction_id"]: it.id, receipt_date: new Date().toISOString().slice(0, 10), amount } });
      setMsg({ id: it.id, ok: true, text: "Receipt recorded." }); refresh();
    } catch (e: any) { setMsg({ id: it.id, ok: false, text: e.message }); }
  };

  return (
    <>
      <PageHeader title="Resale & collections" subtitle="Are devices selling and cash coming back as underwritten? Resale revenue is not cash until a payout is recorded; invoice revenue is not cash until a receipt is." />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Cash tied up in inventory" value={money(dash.data?.cash_tied_up_inventory, { compact: true })} sub="Unsold devices at cost" />
        <Stat label="Aged inventory" value={money(inv.data?.aged_value, { compact: true })} tone={inv.data?.aged_value > 0 ? "warn" : undefined} sub="Listed beyond 45-day target" />
        <Stat label="Receivables outstanding" value={money(a.total_outstanding, { compact: true })} sub="Invoices + resale payouts not yet received" />
        <Stat label="Overdue" value={money(a.total_overdue, { compact: true })} tone={a.total_overdue > 0 ? "bad" : "good"} sub="Past due date" />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2" title="Collections" subtitle="Everything still owed to us, oldest first" pad={false}>
          {a.items.length === 0 ? <div className="p-4"><Empty>Nothing outstanding.</Empty></div> : (
            <Table>
              <thead className="border-b border-line"><tr><th className="th">Item</th><th className="th">Cohort</th><th className="th">Due</th><th className="th text-right">Remaining</th><th className="th">Record receipt</th></tr></thead>
              <tbody className="divide-y divide-line">
                {a.items.slice(0, 14).map((it: any) => (
                  <tr key={it.id}>
                    <td className="td"><div className="font-medium">{it.ref}</div><div className="text-xs text-muted">{it.kind === "invoice" ? `Invoice · ${it.partner}` : "Resale payout"}</div></td>
                    <td className="td num">{codes[it.cohort_id] ?? "—"}</td>
                    <td className="td">{dateShort(it.due_date)} {it.days_overdue > 0 ? <Chip tone={it.days_overdue > 30 ? "bad" : "warn"}>{it.days_overdue}d late</Chip> : <Chip>Not due</Chip>}</td>
                    <td className="td num text-right">{money(it.remaining, { cents: true })}</td>
                    <td className="td">
                      <div className="flex items-center gap-1.5">
                        <input aria-label="Receipt amount" type="number" className="input num w-24" placeholder={String(Math.round(it.remaining))} value={payAmt[it.id] ?? ""} onChange={(e) => setPayAmt({ ...payAmt, [it.id]: e.target.value })} />
                        <button className="btn" onClick={() => receive(it)}>Receive</button>
                      </div>
                      {msg && msg.id === it.id && <p role="alert" className={`mt-1 text-xs ${msg.ok ? "text-good" : "text-bad"}`}>{msg.text}</p>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
        <div className="space-y-4">
          <Card title="Receivables by age">
            <div className="h-40" role="img" aria-label="Receivables by days past due">
              <ResponsiveContainer><BarChart data={Object.entries(a.buckets).map(([k, v]) => ({ k: k === "current" ? "Not due" : k + "d", v }))}>
                <CartesianGrid {...gridProps} /><XAxis dataKey="k" {...axis} /><YAxis {...axis} width={44} tickFormatter={(v) => `$${v / 1000}k`} /><Tooltip {...tooltipStyle} formatter={(v: number) => money(v)} />
                <Bar dataKey="v" radius={[4, 4, 0, 0]}>{Object.keys(a.buckets).map((k, i) => <Cell key={k} fill={i === 0 ? C.soft : i === 1 ? C.warn : C.bad} />)}</Bar></BarChart></ResponsiveContainer>
            </div>
          </Card>
          <Card title="Unsold inventory by listing age" subtitle="Valued at cohort unit cost">
            <div className="h-40" role="img" aria-label="Unsold inventory value by listing age">
              <ResponsiveContainer><BarChart data={Object.entries(inv.data.buckets).map(([k, v]) => ({ k: k + "d", v }))}>
                <CartesianGrid {...gridProps} /><XAxis dataKey="k" {...axis} /><YAxis {...axis} width={44} tickFormatter={(v) => `$${v / 1000}k`} /><Tooltip {...tooltipStyle} formatter={(v: number) => money(v)} />
                <Bar dataKey="v" radius={[4, 4, 0, 0]}>{Object.keys(inv.data.buckets).map((k, i) => <Cell key={k} fill={i < 3 ? C.ink : C.warn} />)}</Bar></BarChart></ResponsiveContainer>
            </div>
          </Card>
        </div>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2" title="Listings" pad={false}>
          <Table>
            <thead className="border-b border-line"><tr><th className="th">Cohort</th><th className="th">Channel</th><th className="th text-right">Listed</th><th className="th text-right">Sold</th><th className="th text-right">Unsold</th><th className="th text-right">Age</th></tr></thead>
            <tbody className="divide-y divide-line">
              {(listings.data ?? []).map((l) => (
                <tr key={l.id}><td className="td"><Link className="num font-semibold hover:underline" href={`/cohorts/${l.cohort_id}`}>{codes[l.cohort_id]}</Link></td><td className="td">{l.listing_channel}</td>
                  <td className="td num text-right">{n(l.device_count)}</td><td className="td num text-right">{n(l.units_sold)}</td><td className="td num text-right">{n(l.units_unsold)}</td>
                  <td className="td num text-right">{l.age_days != null ? <span className={l.age_days > 45 && l.units_unsold > 0 ? "font-semibold text-warn" : ""}>{l.age_days}d</span> : "—"}</td></tr>
              ))}
            </tbody>
          </Table>
        </Card>
        <SaleForm cohorts={cohorts.data ?? []} listings={listings.data ?? []} onDone={refresh} />
      </div>
    </>
  );
}

function SaleForm({ cohorts, listings, onDone }: { cohorts: any[]; listings: any[]; onDone: () => void }) {
  const [f, setF] = useState<any>({ cohort_id: "", resale_listing_id: "", sale_date: new Date().toISOString().slice(0, 10), units_sold: 10, gross_sale_amount: 0, marketplace_fee: 0, shipping_cost: 0 });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const set = (k: string, v: any) => setF((p: any) => ({ ...p, [k]: v }));
  const sellable = cohorts.filter((c) => ["listed_for_sale", "partially_sold"].includes(c.status));
  const net = (+f.gross_sale_amount || 0) - (+f.marketplace_fee || 0) - (+f.shipping_cost || 0);
  const submit = async () => {
    setMsg(null);
    try { await api("/api/sales-transactions", { body: { ...f, resale_listing_id: f.resale_listing_id || null } }); setMsg({ ok: true, text: "Sale recorded; net recovery and margin updated." }); onDone(); }
    catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };
  return (
    <Card title="Record a sale" subtitle="Net proceeds can never exceed gross">
      <div className="space-y-2">
        <Field label="Cohort"><select className="input" value={f.cohort_id} onChange={(e) => set("cohort_id", e.target.value)}><option value="">Select…</option>{sellable.map((c) => <option key={c.id} value={c.id}>{c.code}</option>)}</select></Field>
        <Field label="Listing"><select className="input" value={f.resale_listing_id} onChange={(e) => set("resale_listing_id", e.target.value)}><option value="">Unlinked</option>{listings.filter((l) => l.cohort_id === f.cohort_id).map((l) => <option key={l.id} value={l.id}>{l.listing_channel} · {dateShort(l.listing_date)}</option>)}</select></Field>
        <div className="grid grid-cols-2 gap-2">
          <Field label="Date"><input type="date" className="input" value={f.sale_date} onChange={(e) => set("sale_date", e.target.value)} /></Field>
          <Field label="Units"><input type="number" className="input num" value={f.units_sold} onChange={(e) => set("units_sold", Number(e.target.value))} /></Field>
          <Field label="Gross $"><input type="number" className="input num" value={f.gross_sale_amount} onChange={(e) => set("gross_sale_amount", Number(e.target.value))} /></Field>
          <Field label="Marketplace fee $"><input type="number" className="input num" value={f.marketplace_fee} onChange={(e) => set("marketplace_fee", Number(e.target.value))} /></Field>
          <Field label="Shipping $"><input type="number" className="input num" value={f.shipping_cost} onChange={(e) => set("shipping_cost", Number(e.target.value))} /></Field>
          <Field label="Net (preview)"><div className="num py-1.5 text-sm font-semibold">{money(net, { cents: true })}</div></Field>
        </div>
        {msg && <p role="alert" className={`rounded-lg p-2 text-xs font-medium ${msg.ok ? "bg-good-bg text-good" : "bg-bad-bg text-bad"}`}>{msg.text}</p>}
        <button className="btn btn-primary w-full" disabled={!f.cohort_id || !f.gross_sale_amount} onClick={submit}>Record sale</button>
      </div>
    </Card>
  );
}
