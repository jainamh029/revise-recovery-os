"use client";
export const C = { ink: "#14181f", brand: "#e2542b", muted: "#98a2b3", good: "#19704a", warn: "#a8660b", bad: "#b7352a", info: "#2b56a0", grid: "#e4e0d6", soft: "#c9c4b6" };

export const axis = { stroke: C.muted, fontSize: 11, tickLine: false as const, axisLine: { stroke: C.grid } };
export const gridProps = { stroke: C.grid, strokeDasharray: "3 3", vertical: false };
export const tooltipStyle = {
  contentStyle: { borderRadius: 10, border: `1px solid ${C.grid}`, boxShadow: "0 4px 16px rgba(20,24,31,.08)", fontSize: 12, padding: "8px 10px" },
  labelStyle: { fontWeight: 600, marginBottom: 2 },
  cursor: { fill: "rgba(20,24,31,0.04)" },
};
