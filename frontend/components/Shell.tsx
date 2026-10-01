"use client";
import clsx from "clsx";
import {
  Bell, CalendarRange, ScanBarcode, Coins, FileCheck2, Gauge, LayoutDashboard, Menu, SlidersHorizontal, Sparkles, Users, X, Activity, Bot,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import React, { useEffect, useState } from "react";
import { DemoBar, DemoGate } from "@/components/DemoGate";
import { api } from "@/lib/api";

const NAV = [
  { href: "/", label: "Executive", icon: LayoutDashboard },
  { href: "/cohorts", label: "Underwriting", icon: FileCheck2 },
  { href: "/capacity", label: "Capacity", icon: CalendarRange },
  { href: "/operations", label: "Operations", icon: Activity },
  { href: "/devices", label: "Device ledger", icon: ScanBarcode },
  { href: "/resale", label: "Resale & cash", icon: Coins },
  { href: "/partners", label: "Partners", icon: Users },
  { href: "/alerts", label: "Alerts", icon: Bell },
  { href: "/scenarios", label: "Scenarios", icon: SlidersHorizontal },
  { href: "/policy", label: "Policy", icon: Gauge },
];

export const DISCLAIMER =
  "Illustrative demo data only. Built from public operating context and synthetic assumptions; not representative of Revise Robotics' internal data, customers, economics, or processes.";

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const [alerts, setAlerts] = useState<{ critical: number; total: number } | null>(null);

  useEffect(() => {
    api<any[]>("/api/alerts")
      .then((a) => setAlerts({ critical: a.filter((x) => x.severity === "critical").length, total: a.length }))
      .catch(() => setAlerts(null));
  }, [path]);

  const nav = (
    <nav className="flex flex-1 flex-col gap-0.5 px-3" aria-label="Primary">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            onClick={() => setOpen(false)}
            aria-current={active ? "page" : undefined}
            className={clsx(
              "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition",
              active ? "bg-accent/10 text-white shadow-[inset_2px_0_0_rgb(var(--c-accent))]" : "text-white/60 hover:bg-white/5 hover:text-white"
            )}
          >
            <Icon className="h-4 w-4" aria-hidden />
            <span className="flex-1">{label}</span>
            {href === "/alerts" && alerts && alerts.total > 0 && (
              <span className={clsx("num rounded-md px-1.5 py-0.5 text-[11px] font-semibold", alerts.critical ? "bg-brand text-white" : "bg-white/15 text-white")}>
                {alerts.total}
              </span>
            )}
          </Link>
        );
      })}
    </nav>
  );

  return (
    <div className="min-h-screen lg:flex">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-line bg-side/95 py-5 lg:flex">
        <Brand />
        {nav}
        <div className="eyebrow mx-5 mt-4 flex items-center gap-2 text-good"><span className="led led-pulse" aria-hidden />Engine online</div>
        <p className="mx-5 mt-2 text-[11px] leading-relaxed text-white/40">
          Deterministic backend engine. The UI only displays results — no finance logic runs in the browser.
        </p>
      </aside>

      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 flex w-64 flex-col bg-side py-5">
            <button className="absolute right-3 top-3 text-white/70" onClick={() => setOpen(false)} aria-label="Close menu"><X className="h-5 w-5" /></button>
            <Brand />
            {nav}
          </aside>
        </div>
      )}

      <div className="min-w-0 flex-1">
        <div className="sticky top-0 z-30 flex items-center gap-3 border-b border-warn/30 bg-warn-bg/95 px-4 py-1.5 text-xs text-warn backdrop-blur" title={DISCLAIMER}>
          <button className="lg:hidden" onClick={() => setOpen(true)} aria-label="Open menu"><Menu className="h-5 w-5" /></button>
          <Sparkles className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span className="font-semibold">Illustrative demo · synthetic data</span>
          <span className="hidden truncate opacity-80 md:inline">Not representative of Revise Robotics' internal data, customers, economics, or processes.</span>
          <DemoBar />
        </div>
        <main className="mx-auto max-w-[1400px] px-4 py-6 sm:px-6 lg:px-8"><DemoGate>{children}</DemoGate></main>
        <footer className="mx-auto max-w-[1400px] px-4 pb-8 sm:px-6 lg:px-8">
          <p className="border-t border-line pt-4 text-xs leading-relaxed text-muted">{DISCLAIMER} Not investment advice; not audited financial reporting. No robot control or live telemetry.</p>
        </footer>
      </div>
    </div>
  );
}

function Brand() {
  return (
    <div className="mb-6 px-5">
      <div className="flex items-center gap-2.5">
        <div className="grid h-9 w-9 place-items-center rounded-lg bg-gradient-to-br from-brand to-brand/60 text-white shadow-[0_0_18px_-2px_rgba(255,106,61,.65)]"><Bot className="h-5 w-5" aria-hidden /></div>
        <div>
          <div className="text-[15px] font-semibold leading-none text-white">Recovery OS</div>
          <div className="mt-1 text-[11px] leading-none text-white/50">Operating finance for refurbishment</div>
        </div>
      </div>
    </div>
  );
}
