"use client";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { Plus, Trash2, Wand2 } from "lucide-react";
import { ChecksList, RecommendationCard, ScenarioTable } from "@/components/cohort/Underwriting";
import { Card, ErrorBox, Field, Loading, PageHeader } from "@/components/ui";
import { cohortHref } from "@/lib/nav";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { titleCase } from "@/lib/format";

type Line = { key: number; profileId: string; model_name: string; units: number; grade: string; minutes: number; exc: number; fpy: number; repair: number; price: number; st: number };
const blank = (key: number): Line => ({ key, profileId: "", model_name: "", units: 100, grade: "B", minutes: 32, exc: 10, fpy: 88, repair: 60, price: 250, st: 90 });

export default function NewCohort() {
  const router = useRouter();
  const partners = useApi<any[]>("/api/partners");
  const contracts = useApi<any[]>("/api/contracts");
  const models = useApi<any[]>("/api/device-models");

  const [partnerId, setPartnerId] = useState("");
  const [contractId, setContractId] = useState("");
  const [name, setName] = useState("");
  const [intake, setIntake] = useState("");
  const [target, setTarget] = useState("");
  const [inbound, setInbound] = useState(3.5);
  const [ship, setShip] = useState(13);
  const [fee, setFee] = useState(12);
  const [ret, setRet] = useState(4);
  const [payout, setPayout] = useState(7);
  const [acq, setAcq] = useState<number | "">("");
  const [lines, setLines] = useState<Line[]>([blank(1)]);
  const [preview, setPreview] = useState<any>(null);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const seq = useRef(2);

  const partnerContracts = useMemo(() => (contracts.data ?? []).filter((c) => c.partner_id === partnerId), [contracts.data, partnerId]);
  const suppliers = (partners.data ?? []).filter((p) => !["resale_channel", "processing_customer"].includes(p.partner_type));

  // Contract terms prefill (user can still override).
  useEffect(() => {
    const c = partnerContracts.find((x) => x.id === contractId);
    if (c) { setAcq(c.acquisition_cost_per_unit ?? 0); }
  }, [contractId, partnerContracts]);

  const setLine = (key: number, patch: Partial<Line>) => setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const pickModel = (key: number, id: string) => {
    const m = (models.data ?? []).find((x) => x.id === id);
    if (!m) return setLine(key, { profileId: "" });
    setLine(key, {
      profileId: id, model_name: `${m.manufacturer} ${m.model_name}`, minutes: +m.expected_process_minutes, exc: +(m.expected_exception_rate_pct * 100).toFixed(1),
      fpy: +(m.expected_first_pass_yield_pct * 100).toFixed(1), repair: +m.expected_repair_cost, price: +m.expected_resale_price, st: +(m.expected_sell_through_pct * 100).toFixed(1),
    });
  };

  const body = useMemo(() => ({
    partner_id: partnerId, contract_id: contractId || null, intake_date: intake || null, target_completion_date: target || null,
    assumptions: {
      lines: lines.filter((l) => l.units > 0 && l.model_name).map((l) => ({
        model_name: l.model_name, device_model_profile_id: l.profileId || null, units: l.units, condition_grade: l.grade, process_minutes: l.minutes,
        exception_rate: l.exc / 100, first_pass_yield: l.fpy / 100, repair_cost: l.repair, resale_price: l.price, sell_through: l.st / 100,
      })),
      ...(acq !== "" ? { acquisition_cost_per_unit: acq } : {}),
      inbound_logistics_per_unit: inbound, outbound_shipping_per_unit: ship, marketplace_fee_rate: fee / 100, return_rate: ret / 100, payout_days: payout,
    },
  }), [partnerId, contractId, intake, target, lines, acq, inbound, ship, fee, ret, payout]);

  // Debounced live underwriting -- the numbers come from the backend engine, never computed here.
  useEffect(() => {
    if (!partnerId || body.assumptions.lines.length === 0) { setPreview(null); return; }
    const t = setTimeout(async () => {
      try { setPreviewErr(null); setPreview(await api("/api/underwrite/preview", { body })); }
      catch (e: any) { setPreviewErr(e.message); }
    }, 450);
    return () => clearTimeout(t);
  }, [body, partnerId]);

  const units = lines.reduce((s, l) => s + (l.units || 0), 0);

  const loadExample = () => {
    const edu = suppliers.find((p) => p.partner_type === "university");
    if (!edu || !models.data) return;
    setPartnerId(edu.id);
    const ct = (contracts.data ?? []).find((c) => c.partner_id === edu.id);
    if (ct) setContractId(ct.id);
    setName("University retired-laptop offer — 400 devices");
    const d = (iso: number) => new Date(Date.now() + iso * 864e5).toISOString().slice(0, 10);
    setIntake(d(6)); setTarget(d(24));
    const find = (t: string) => models.data!.find((m) => `${m.manufacturer} ${m.model_name}`.includes(t))?.id;
    const mk = (t: string, u: number, grade = "B"): Line => { const l = blank(seq.current++); const m = models.data!.find((x) => x.id === find(t))!; return { ...l, units: u, grade, profileId: m.id, model_name: `${m.manufacturer} ${m.model_name}`, minutes: +m.expected_process_minutes, exc: +(m.expected_exception_rate_pct * 100).toFixed(1), fpy: +(m.expected_first_pass_yield_pct * 100).toFixed(1), repair: +m.expected_repair_cost, price: +m.expected_resale_price, st: +(m.expected_sell_through_pct * 100).toFixed(1) }; };
    setLines([mk("T480", 120), mk("Latitude 5490", 120), mk("EliteBook 840", 90), mk("Surface Laptop 2", 40), mk("MacBook Air", 30)]);
  };

  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      const c = await api("/api/cohorts", { body: { ...body, cohort_name: name || "Untitled cohort" } });
      await api(`/api/cohorts/${c.id}/underwrite`, { method: "POST", body: {} });
      router.push(cohortHref(c.id));
    } catch (e: any) { setErr(e.message); setBusy(false); }
  };

  if (partners.loading || models.loading) return <Loading />;
  if (partners.error) return <ErrorBox message={partners.error} />;
  const num = (v: string) => (v === "" ? 0 : Number(v));
  const numCell = (l: Line, k: keyof Line, w: number) => (
    <td key={k} className="px-1 py-2">
      <input aria-label={k} type="number" style={{ width: w }} className="input num" value={l[k] as number} onChange={(e) => setLine(l.key, { [k]: num(e.target.value) } as Partial<Line>)} />
    </td>
  );

  return (
    <>
      <PageHeader title="Underwrite a cohort" subtitle="Model the batch before any cash or robot time is committed. The panel on the right is computed live by the backend engine."
        actions={<button className="btn" onClick={loadExample}><Wand2 className="h-4 w-4" /> Load example offer</button>} />
      <div className="grid gap-4 xl:grid-cols-5">
        <div className="space-y-4 xl:col-span-3">
          <Card title="1 · Partner & terms">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Supply partner">
                <select className="input" value={partnerId} onChange={(e) => { setPartnerId(e.target.value); setContractId(""); }}>
                  <option value="">Select…</option>
                  {suppliers.map((p) => <option key={p.id} value={p.id}>{p.name} — {titleCase(p.partner_type)}</option>)}
                </select>
              </Field>
              <Field label="Contract">
                <select className="input" value={contractId} onChange={(e) => setContractId(e.target.value)} disabled={!partnerId}>
                  <option value="">No contract (manual terms)</option>
                  {partnerContracts.map((c) => <option key={c.id} value={c.id}>{c.contract_name} — {titleCase(c.contract_type)}</option>)}
                </select>
              </Field>
              <Field label="Cohort name" className="sm:col-span-2"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Fall refresh — 400 laptops" /></Field>
              <Field label="Expected intake"><input type="date" className="input" value={intake} onChange={(e) => setIntake(e.target.value)} /></Field>
              <Field label="Target completion (SLA)"><input type="date" className="input" value={target} onChange={(e) => setTarget(e.target.value)} /></Field>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-3">
              <Field label="Acquisition $/unit"><input type="number" className="input num" value={acq} onChange={(e) => setAcq(e.target.value === "" ? "" : Number(e.target.value))} /></Field>
              <Field label="Inbound logistics $/unit"><input type="number" className="input num" value={inbound} onChange={(e) => setInbound(num(e.target.value))} /></Field>
              <Field label="Outbound shipping $/unit"><input type="number" className="input num" value={ship} onChange={(e) => setShip(num(e.target.value))} /></Field>
              <Field label="Marketplace fee %"><input type="number" className="input num" value={fee} onChange={(e) => setFee(num(e.target.value))} /></Field>
              <Field label="Return rate %"><input type="number" className="input num" value={ret} onChange={(e) => setRet(num(e.target.value))} /></Field>
              <Field label="Payout / payment lag (days)"><input type="number" className="input num" value={payout} onChange={(e) => setPayout(num(e.target.value))} /></Field>
            </div>
          </Card>

          <Card title={`2 · Device model mix · ${units.toLocaleString()} devices`} subtitle="Picking a model loads its profile defaults; every number stays editable. Percentages are entered as %."
            actions={<button className="btn" onClick={() => setLines((l) => [...l, blank(seq.current++)])}><Plus className="h-4 w-4" /> Add model</button>} pad={false}>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px]">
                <thead className="border-b border-line"><tr>
                  <th className="th">Model</th><th className="th">Units</th><th className="th">Grade</th><th className="th">Min/dev</th><th className="th">Exc %</th>
                  <th className="th">FPY %</th><th className="th">Repair $</th><th className="th">Resale $</th><th className="th">Sell-thru %</th><th className="th" />
                </tr></thead>
                <tbody className="divide-y divide-line">
                  {lines.map((l) => (
                    <tr key={l.key}>
                      <td className="px-2 py-2"><select aria-label="Device model" className="input min-w-[190px]" value={l.profileId} onChange={(e) => pickModel(l.key, e.target.value)}>
                        <option value="">Select model…</option>{(models.data ?? []).map((m) => <option key={m.id} value={m.id}>{m.manufacturer} {m.model_name}</option>)}
                      </select></td>
                      {numCell(l, "units", 70)}
                      <td className="px-1 py-2"><select aria-label="Condition grade" className="input w-[58px]" value={l.grade} onChange={(e) => setLine(l.key, { grade: e.target.value })}>{["A", "B", "C", "D"].map((g) => <option key={g}>{g}</option>)}</select></td>
                      {numCell(l, "minutes", 64)}
                      {numCell(l, "exc", 60)}
                      {numCell(l, "fpy", 60)}
                      {numCell(l, "repair", 70)}
                      {numCell(l, "price", 70)}
                      {numCell(l, "st", 66)}
                      <td className="px-1 py-2"><button aria-label="Remove model" className="btn px-2" onClick={() => setLines((ls) => ls.filter((x) => x.key !== l.key))} disabled={lines.length === 1}><Trash2 className="h-4 w-4" /></button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          {err && <ErrorBox message={err} />}
          <div className="flex justify-end gap-2">
            <button className="btn btn-primary" disabled={busy || !partnerId || !preview || !name} onClick={submit}>{busy ? "Saving…" : "Save & submit for review"}</button>
          </div>
        </div>

        <div className="space-y-4 xl:col-span-2 xl:sticky xl:top-14 xl:self-start">
          {!preview && !previewErr && <Card><p className="text-sm text-muted">Choose a partner and at least one device model to see the recommendation.</p></Card>}
          {previewErr && <ErrorBox message={previewErr} />}
          {preview && (<>
            <RecommendationCard uw={{ ...preview, priority_score: preview.priority_score ?? 0 }} />
            <Card title="Economics" pad={false}><ScenarioTable scenarios={preview.scenarios} /></Card>
            <Card title="Policy checks"><ChecksList checks={preview.checks} /></Card>
          </>)}
        </div>
      </div>
    </>
  );
}
