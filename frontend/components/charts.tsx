"use client";
import React from "react";

/** Chart palette for the dark "mission control" theme. Keep keys stable: other pages import C. */
export const C = { ink: "#e6edf5", brand: "#ff6a3d", muted: "#8a99ab", good: "#34d399", warn: "#fbbf24", bad: "#f87171", info: "#60a5fa", grid: "#1b2735", soft: "#4a5a6d", accent: "#22d3ee" };

export const axis = { stroke: C.muted, fontSize: 11, tickLine: false as const, axisLine: { stroke: C.grid } };
export const gridProps = { stroke: C.grid, strokeDasharray: "2 4", vertical: false };
export const tooltipStyle = {
  contentStyle: { borderRadius: 8, border: `1px solid ${C.accent}55`, background: "rgba(8,13,19,.95)", color: C.ink, boxShadow: "0 0 24px -6px rgba(34,211,238,.35)", fontSize: 12, padding: "8px 10px" },
  labelStyle: { fontWeight: 600, marginBottom: 2, color: C.accent },
  itemStyle: { color: C.ink },
  cursor: { fill: "rgba(34,211,238,0.06)", stroke: "rgba(34,211,238,0.35)", strokeDasharray: "3 3" },
};

/** Shared gradient + hatch defs. Call as {chartDefs("id")} directly inside a chart (recharts drops custom wrapper components). `uid` keeps ids unique per chart. */
export function chartDefs(uid: string) {
  const g = (id: string, color: string, top = 0.95, bottom = 0.25) => (
    <linearGradient id={`${uid}-${id}`} x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stopColor={color} stopOpacity={top} />
      <stop offset="100%" stopColor={color} stopOpacity={bottom} />
    </linearGradient>
  );
  return (
    <defs>
      {g("accent", C.accent)}
      {g("bad", C.bad)}
      {g("warn", C.warn)}
      {g("soft", C.soft, 0.55, 0.12)}
      {g("area", C.accent, 0.45, 0)}
      <pattern id={`${uid}-hatch`} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
        <rect width="6" height="6" fill="#4a5a6d" fillOpacity="0.18" />
        <line x1="0" y1="0" x2="0" y2="6" stroke="#8a99ab" strokeOpacity="0.55" strokeWidth="2" />
      </pattern>
      <filter id={`${uid}-glow`} x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur stdDeviation="3" result="b" />
        <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
      </filter>
    </defs>
  );
}

/** Pulsing "sensor" dot used on the newest point of a line. */
export function PulseDot({ cx, cy, color = C.accent }: { cx?: number; cy?: number; color?: string }) {
  if (cx == null || cy == null) return null;
  return (
    <g>
      <circle cx={cx} cy={cy} r={9} fill={color} opacity={0.18}><animate attributeName="r" values="5;11;5" dur="2.2s" repeatCount="indefinite" /></circle>
      <circle cx={cx} cy={cy} r={4} fill={color} stroke="#070b10" strokeWidth={1.5} />
    </g>
  );
}

/** 270° radial gauge (SVG, no chart lib): ticked dial with glowing arc. value is 0..1. */
export function Gauge({ value, label, sub, tone = "accent", size = 168 }: { value: number; label: string; sub?: React.ReactNode; tone?: "accent" | "warn" | "bad" | "good"; size?: number }) {
  const col = { accent: C.accent, warn: C.warn, bad: C.bad, good: C.good }[tone];
  const r = 64, cx = 80, cy = 80, a0 = 135, sweep = 270;
  const pt = (deg: number, rad = r) => [cx + rad * Math.cos((deg * Math.PI) / 180), cy + rad * Math.sin((deg * Math.PI) / 180)];
  const arc = (frac: number) => {
    const [x0, y0] = pt(a0), [x1, y1] = pt(a0 + sweep * frac);
    return `M${x0} ${y0} A${r} ${r} 0 ${sweep * frac > 180 ? 1 : 0} 1 ${x1} ${y1}`;
  };
  const v = Math.max(0, Math.min(1, value));
  const ticks = Array.from({ length: 28 }, (_, i) => a0 + (sweep * i) / 27);
  const warnAt = a0 + sweep * 0.65;
  return (
    <div className="flex flex-col items-center" role="img" aria-label={`${label}: ${Math.round(v * 100)} percent`}>
      <svg viewBox="0 0 160 160" width={size} height={size}>
        <defs>
          <filter id="gauge-glow" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="3.5" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
        </defs>
        {ticks.map((d, i) => { const [x1, y1] = pt(d, r + 9), [x2, y2] = pt(d, r + (i % 3 === 0 ? 15 : 12)); return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke={C.soft} strokeWidth={i % 3 === 0 ? 1.6 : 1} />; })}
        <path d={arc(1)} fill="none" stroke={C.grid} strokeWidth={10} strokeLinecap="round" />
        {v > 0.005 && <path d={arc(v)} fill="none" stroke={col} strokeWidth={10} strokeLinecap="round" filter="url(#gauge-glow)" />}
        {(() => { const [x1, y1] = pt(warnAt, r - 9), [x2, y2] = pt(warnAt, r + 9); return <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={C.warn} strokeWidth={2} strokeDasharray="2 2" />; })()}
        <text x={cx} y={cy + 6} textAnchor="middle" fontSize={30} fontWeight={600} fill={C.ink} style={{ fontFamily: "var(--font-mono), monospace" }}>{Math.round(v * 100)}%</text>
        <text x={cx} y={cy + 24} textAnchor="middle" fontSize={8.5} letterSpacing={1.4} fill={C.muted} style={{ fontFamily: "var(--font-mono), monospace" }}>{label.toUpperCase()}</text>
      </svg>
      {sub && <div className="-mt-3 text-xs text-muted">{sub}</div>}
    </div>
  );
}
