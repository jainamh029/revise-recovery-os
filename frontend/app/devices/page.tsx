"use client";
import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, Download, ShieldAlert } from "lucide-react";
import { Card, Chip, Empty, ErrorBox, Field, Hint, Loading, PageHeader, Stat, Table } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { titleCase, type Tone } from "@/lib/format";

const TONE: Record<string, Tone> = {
  QUARANTINED: "bad", SANITIZATION_FAILED: "bad", LISTED: "info", SOLD: "good", RETURNED: "warn", READY_FOR_LISTING: "info",
  RECYCLED: "neutral", DESTROYED: "neutral", MANUAL_REVIEW: "warn", UNDER_REPAIR: "warn",
};
const REASONS = ["BEYOND_ECONOMIC_REPAIR", "CONDITION_TOO_POOR", "DATA_SANITIZATION_FAILED", "SECURITY_POLICY", "PARTNER_REQUEST", "END_OF_LIFE", "OTHER"];

export default function Devices() {
  const cohorts = useApi<any[]>("/api/cohorts");
  const [cid, setCid] = useState("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<string>("");

  useEffect(() => {
    if (!cid && cohorts.data) setCid((cohorts.data.find((c) => c.code === "ITAD-002") ?? cohorts.data.find((c) => c.received > 0) ?? cohorts.data[0])?.id ?? "");
  }, [cohorts.data, cid]);

  const qs = useMemo(() => `/api/devices?cohort_id=${cid}&limit=100${status ? `&status=${status}` : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}`, [cid, status, q]);
  const list = useApi<any[]>(cid ? qs : null);
  const rec = useApi<any>(cid ? `/api/cohorts/${cid}/device-reconciliation` : null);
  const refresh = () => { list.refetch(); rec.refetch(); };

  if (cohorts.loading) return <Loading />;
  if (cohorts.error) return <ErrorBox message={cohorts.error} onRetry={cohorts.refetch} />;
  const r = rec.data;

  return (
    <>
      <PageHeader
        title="Device ledger"
        subtitle="Serial-level chain of custody. A laptop cannot be listed until it is sanitized, passed quality control and designated for resale; a failed wipe quarantines it. Every gate is enforced by the backend."
      />
      <div className="mb-4 grid gap-3 md:grid-cols-4">
        <Field label="Cohort">
          <select className="input" value={cid} onChange={(e) => { setCid(e.target.value); setSel(""); }}>
            {(cohorts.data ?? []).filter((c) => !["draft", "under_review", "declined"].includes(c.status)).map((c) => <option key={c.id} value={c.id}>{c.code} — {c.name}</option>)}
          </select>
        </Field>
        <Field label="Status">
          <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option>
            {r && Object.keys(r.by_status).map((s) => <option key={s} value={s}>{titleCase(s)} ({r.by_status[s]})</option>)}
          </select>
        </Field>
        <Field label="Serial / asset tag"><input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="search…" /></Field>
        <div className="flex items-end"><a className="btn w-full" href={`/api/export/devices.csv?cohort_id=${cid}`} download><Download className="h-4 w-4" /> Export ledger CSV</a></div>
      </div>

      {r && (
        <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
          <Stat label="Registered devices" value={r.registered} sub={`${r.received_per_operations} received per operations`} />
          <Stat label="Count reconciliation" value={r.reconciles && r.matches_intake ? <span className="inline-flex items-center gap-1.5 text-good"><CheckCircle2 className="h-5 w-5" /> Balanced</span> : <span className="text-bad">Off by {Math.abs(r.unregistered)}</span>}
            sub="sold + recycled + destroyed + active = registered = intake" />
          <Stat label="Active inventory" value={r.active_inventory} sub="Not yet sold, recycled or destroyed" />
          <Stat label="Sold · recycled · destroyed" value={`${r.sold} · ${r.recycled} · ${r.destroyed}`} sub="Terminal outcomes" />
          <Stat label="Quarantined" value={r.quarantined} tone={r.quarantined ? "bad" : undefined} sub="Blocked from processing, resale, shipment" />
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-3" title="Devices" subtitle={list.data ? `Showing ${list.data.length}${list.data.length === 100 ? " (first 100)" : ""}` : undefined} pad={false}>
          {list.loading ? <div className="p-4"><Loading /></div> : list.error ? <div className="p-4"><ErrorBox message={list.error} /></div> : (
            <Table>
              <thead className="border-b border-line"><tr><th className="th">Serial</th><th className="th">Model</th><th className="th">Status</th><th className="th">Sanitization<Hint>SANITIZED requires a method and a certificate / result ID. Listing is impossible without it.</Hint></th><th className="th">QC</th></tr></thead>
              <tbody className="divide-y divide-line">
                {(list.data ?? []).map((d) => (
                  <tr key={d.id} onClick={() => setSel(d.id)} className={`cursor-pointer hover:bg-paper/60 ${sel === d.id ? "bg-paper" : ""}`}>
                    <td className="td num font-medium">{d.serial_number}</td>
                    <td className="td text-xs">{d.manufacturer} {d.model}</td>
                    <td className="td"><Chip tone={TONE[d.current_status] ?? "neutral"}>{titleCase(d.current_status)}</Chip></td>
                    <td className="td"><Chip tone={d.sanitization_status === "SANITIZED" ? "good" : d.sanitization_status === "FAILED" ? "bad" : "neutral"}>{titleCase(d.sanitization_status)}</Chip></td>
                    <td className="td"><Chip tone={d.quality_control_status === "PASSED" ? "good" : d.quality_control_status === "FAILED" ? "bad" : "neutral"}>{titleCase(d.quality_control_status)}</Chip></td>
                  </tr>
                ))}
                {(list.data ?? []).length === 0 && <tr><td className="td text-muted" colSpan={5}>No devices match.</td></tr>}
              </tbody>
            </Table>
          )}
        </Card>

        <div className="space-y-4 lg:col-span-2">
          <Register cohortId={cid} onDone={refresh} />
          {sel ? <Detail id={sel} cohortId={cid} onChange={refresh} /> : <Card><Empty>Select a device to see its custody trail and the actions it is allowed to take.</Empty></Card>}
        </div>
      </div>
    </>
  );
}

function Register({ cohortId, onDone }: { cohortId: string; onDone: () => void }) {
  const [serial, setSerial] = useState("");
  const [model, setModel] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const go = async () => {
    setMsg(null);
    try { await api("/api/devices", { body: { cohort_id: cohortId, serial_number: serial, model: model || null } }); setSerial(""); setMsg({ ok: true, text: "Received into custody." }); onDone(); }
    catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };
  return (
    <Card title="Receive a device" subtitle="Serial numbers are unique across the whole ledger">
      <div className="flex gap-2">
        <input className="input num" placeholder="Serial number" value={serial} onChange={(e) => setSerial(e.target.value)} aria-label="Serial number" />
        <input className="input" placeholder="Model" value={model} onChange={(e) => setModel(e.target.value)} aria-label="Model" />
        <button className="btn btn-primary" disabled={!serial || !cohortId} onClick={go}>Receive</button>
      </div>
      {msg && <p role="alert" className={`mt-2 text-xs font-medium ${msg.ok ? "text-good" : "text-bad"}`}>{msg.text}</p>}
    </Card>
  );
}

function Detail({ id, cohortId, onChange }: { id: string; cohortId: string; onChange: () => void }) {
  const dev = useApi<any>(`/api/devices/${id}`);
  const listings = useApi<any[]>(`/api/resale-listings?cohort_id=${cohortId}`);
  const sales = useApi<any[]>(`/api/sales-transactions?cohort_id=${cohortId}`);
  const [err, setErr] = useState<string | null>(null);
  const [cert, setCert] = useState("");
  const [reason, setReason] = useState(REASONS[0]);
  const [operator, setOperator] = useState("");
  const [listing, setListing] = useState("");
  const [sale, setSale] = useState("");
  useEffect(() => { setErr(null); }, [id]);

  if (dev.loading) return <Card><Loading /></Card>;
  if (dev.error || !dev.data) return <ErrorBox message={dev.error ?? "Not found"} />;
  const d = dev.data;
  const s = d.current_status;

  const run = async (path: string, body: unknown = {}) => {
    setErr(null);
    try { await api(`/api/devices/${id}/${path}`, { body }); await dev.refetch(); onChange(); }
    catch (e: any) { setErr(e.message); }
  };
  const advance = (to: string, label: string) => <button key={to} className="btn" onClick={() => run("advance", { to })}>{label}</button>;

  return (
    <Card title={<span className="num">{d.serial_number}</span>} subtitle={`${d.manufacturer ?? ""} ${d.model ?? ""} · ${d.current_location} · custodian ${d.current_custodian}`}
      actions={<a className="btn" href={`/api/devices/${id}/export?format=csv`} download><Download className="h-3.5 w-3.5" /> Audit</a>}>
      {(s === "QUARANTINED" || s === "SANITIZATION_FAILED") && (
        <div className="mb-3 flex items-start gap-2 rounded-lg bg-bad-bg p-2.5 text-xs text-bad"><ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" /> Quarantined: blocked from robot processing, resale and shipment until re-sanitized with a passing certificate, or destroyed.</div>
      )}
      <div className="mb-3 flex flex-wrap gap-1.5">
        <Chip tone={TONE[s] ?? "neutral"}>{titleCase(s)}</Chip>
        <Chip>Sanitization: {titleCase(d.sanitization_status)}</Chip><Chip>QC: {titleCase(d.quality_control_status)}</Chip><Chip>Disposition: {titleCase(d.final_disposition)}</Chip>
      </div>
      {d.sanitization_certificate_id && <p className="mb-3 text-xs text-muted">Certificate <span className="num text-ink">{d.sanitization_certificate_id}</span> · {d.sanitization_method}</p>}

      <div className="eyebrow mb-1.5">Next steps<Hint label="Gates">Buttons only offer steps that make sense for the current status, but the server enforces every rule regardless — try an illegal step and you will see its message.</Hint></div>
      <div className="flex flex-wrap gap-2">
        {s === "RECEIVED" && advance("TRIAGED", "Triage")}
        {s === "TRIAGED" && advance("AWAITING_SANITIZATION", "Queue for sanitization")}
        {s === "QUARANTINED" && advance("AWAITING_SANITIZATION", "Release to re-sanitize")}
        {(s === "SANITIZED") && advance("IN_ROBOT_PROCESSING", "Robot processing")}
        {(s === "IN_ROBOT_PROCESSING" || s === "SANITIZED" || s === "UNDER_REPAIR" || s === "MANUAL_REVIEW" || s === "RETURNED") && advance("QUALITY_CONTROL", "Send to QC")}
        {s === "MANUAL_REVIEW" && advance("UNDER_REPAIR", "Send to repair")}
        {s === "READY_FOR_LISTING" && d.final_disposition !== "RESALE" && <button className="btn" onClick={() => run("disposition", { disposition: "RESALE" })}>Designate for resale</button>}
        {s === "LISTED" && <button className="btn" onClick={() => run("delist")}>Delist</button>}
        {s === "SOLD" && <button className="btn" onClick={() => run("return")}>Mark returned</button>}
      </div>

      {s === "AWAITING_SANITIZATION" && (
        <div className="mt-3 space-y-2 rounded-lg border border-line p-3">
          <Field label="Certificate / result ID (required to pass)"><input className="input num" value={cert} onChange={(e) => setCert(e.target.value)} placeholder="e.g. CERT-2026-000123" /></Field>
          <div className="flex gap-2">
            <button className="btn btn-primary flex-1" onClick={() => run("sanitization", { passed: true, certificate_id: cert || null })}>Record pass</button>
            <button className="btn btn-danger flex-1" onClick={() => run("sanitization", { passed: false })}>Record failure</button>
          </div>
        </div>
      )}
      {s === "QUALITY_CONTROL" && (
        <div className="mt-3 flex gap-2 rounded-lg border border-line p-3">
          <button className="btn btn-primary flex-1" onClick={() => run("qc", { passed: true })}>QC pass</button>
          <button className="btn btn-danger flex-1" onClick={() => run("qc", { passed: false })}>QC fail</button>
        </div>
      )}
      {(s === "READY_FOR_LISTING" || s === "QUALITY_CONTROL") && (
        <div className="mt-3 space-y-2 rounded-lg border border-line p-3">
          <Field label="List on"><select className="input" value={listing} onChange={(e) => setListing(e.target.value)}><option value="">Select a listing…</option>{(listings.data ?? []).map((l) => <option key={l.id} value={l.id}>{l.listing_channel} · {l.units_unsold} unsold</option>)}</select></Field>
          <button className="btn w-full" disabled={!listing} onClick={() => run("list", { resale_listing_id: listing })}>List device</button>
        </div>
      )}
      {s === "LISTED" && (
        <div className="mt-3 space-y-2 rounded-lg border border-line p-3">
          <Field label="Attach to sale"><select className="input" value={sale} onChange={(e) => setSale(e.target.value)}><option value="">Select a sale…</option>{(sales.data ?? []).map((x) => <option key={x.id} value={x.id}>{x.sale_date} · {x.units_sold} units · ${x.gross_sale_amount}</option>)}</select></Field>
          <button className="btn w-full" disabled={!sale} onClick={() => run("sell", { sales_transaction_id: sale })}>Mark sold</button>
        </div>
      )}
      {!["SOLD", "LISTED", "RECYCLED", "DESTROYED"].includes(s) && (
        <div className="mt-3 space-y-2 rounded-lg border border-line p-3">
          <div className="eyebrow">Recycle or destroy<Hint>Requires a reason code and an operator reference; the system stamps the time.</Hint></div>
          <div className="grid grid-cols-2 gap-2">
            <select aria-label="Reason code" className="input" value={reason} onChange={(e) => setReason(e.target.value)}>{REASONS.map((r) => <option key={r} value={r}>{titleCase(r)}</option>)}</select>
            <input aria-label="Operator" className="input" placeholder="Operator ref" value={operator} onChange={(e) => setOperator(e.target.value)} />
          </div>
          <div className="flex gap-2">
            <button className="btn flex-1" onClick={() => run("disposition", { disposition: "RECYCLE", reason_code: reason, operator_ref: operator })}>Recycle</button>
            <button className="btn flex-1" onClick={() => run("disposition", { disposition: "DESTROY", reason_code: reason, operator_ref: operator })}>Destroy</button>
          </div>
        </div>
      )}
      {err && <p role="alert" className="mt-3 rounded-lg bg-bad-bg p-2 text-xs font-medium text-bad">{err}</p>}

      <div className="eyebrow mb-1.5 mt-4">Chain of custody (append-only)</div>
      <ol className="max-h-56 space-y-1.5 overflow-auto border-l-2 border-line pl-3 text-xs">
        {[...d.custody_events].reverse().map((e: any) => (
          <li key={e.id}><span className="font-semibold">{titleCase(e.event_type)}</span>{e.to_status ? ` → ${titleCase(e.to_status)}` : ""}<div className="text-muted">{new Date(e.occurred_at).toLocaleString()} · {e.location} · {e.custodian}</div></li>
        ))}
      </ol>
    </Card>
  );
}
