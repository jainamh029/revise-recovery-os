"use client";
import clsx from "clsx";
import { AlertTriangle, ArrowUpRight, Info, Loader2 } from "lucide-react";
import Link from "next/link";
import React from "react";
import { DECISION, STATUS_TONE, titleCase, type Tone } from "@/lib/format";

const TONE: Record<Tone, string> = {
  good: "bg-good-bg text-good",
  warn: "bg-warn-bg text-warn",
  bad: "bg-bad-bg text-bad",
  info: "bg-info-bg text-info",
  neutral: "bg-paper text-muted border border-line",
};

export function Chip({ tone = "neutral", children, className }: { tone?: Tone; children: React.ReactNode; className?: string }) {
  return (
    <span className={clsx("inline-flex items-center gap-1 whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-semibold", TONE[tone], className)}>
      {children}
    </span>
  );
}

export const DecisionChip = ({ decision }: { decision?: string | null }) => {
  if (!decision) return <Chip>Not underwritten</Chip>;
  const d = DECISION[decision];
  return <Chip tone={d?.tone ?? "neutral"}>{d?.label ?? titleCase(decision)}</Chip>;
};

export const StatusChip = ({ status }: { status: string }) => <Chip tone={STATUS_TONE[status] ?? "neutral"}>{titleCase(status)}</Chip>;

export const SeverityChip = ({ severity }: { severity: string }) => (
  <Chip tone={severity === "critical" ? "bad" : severity === "warning" ? "warn" : "info"}>{titleCase(severity)}</Chip>
);

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div className="max-w-3xl">
        <h1 className="text-[26px] font-semibold leading-tight tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ title, subtitle, actions, children, className, pad = true }: {
  title?: React.ReactNode; subtitle?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; className?: string; pad?: boolean;
}) {
  return (
    <section className={clsx("card", className)}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div>
            <h2 className="text-[15px] font-semibold">{title}</h2>
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {actions}
        </header>
      )}
      <div className={pad ? "p-4" : ""}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, tone, hint, id, href }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: Tone; hint?: string; id?: string; href?: string }) {
  const body = (
    <div className={clsx("card p-4", href && "transition hover:border-ink/40 hover:shadow-md")} title={hint} data-testid={id ? `stat-${id}` : undefined}>
      <div className="eyebrow flex items-center justify-between gap-2">{label}{href && <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-muted" aria-hidden />}</div>
      <div className={clsx("num mt-1.5 text-[26px] font-semibold leading-none", tone === "bad" && "text-bad", tone === "good" && "text-good", tone === "warn" && "text-warn")}>
        {value}
      </div>
      {sub && <div className="mt-2 text-xs text-muted">{sub}</div>}
    </div>
  );
  if (!href) return body;
  return (
    <Link href={href} data-testid={id ? `link-${id}` : undefined} aria-label={`${label}: open detail`} className="block rounded-xl focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink">
      {body}
    </Link>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-10 text-sm text-muted" role="status">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> {label}…
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-bad/30 bg-bad-bg p-4 text-sm text-bad" role="alert">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <div className="flex-1">
        <div className="font-semibold">Something went wrong</div>
        <div className="mt-0.5 break-words">{message}</div>
        {message.includes("fetch") || message.includes("Failed") || message.includes("500") ? (
          <div className="mt-1 text-xs opacity-80">Is the API running? <code className="num">uvicorn app.main:app --reload</code> in <code className="num">backend/</code></div>
        ) : null}
      </div>
      {onRetry && <button className="btn" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="rounded-lg border border-dashed border-line p-6 text-center text-sm text-muted">{children}</div>;
}

export function Table({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={clsx("overflow-x-auto", className)}>
      <table className="w-full border-collapse">{children}</table>
    </div>
  );
}

export function Tabs({ tabs, value, onChange }: { tabs: { id: string; label: string }[]; value: string; onChange: (id: string) => void }) {
  return (
    <div role="tablist" className="mb-5 flex gap-1 overflow-x-auto border-b border-line">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={clsx(
            "-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium transition",
            value === t.id ? "border-ink text-ink" : "border-transparent text-muted hover:text-ink"
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Field({ label, hint, children, className }: { label: string; hint?: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={clsx("block", className)}>
      <span className="mb-1 block text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-muted">{hint}</span>}
    </label>
  );
}

export function Bar({ value, max = 1, tone = "neutral" }: { value: number; max?: number; tone?: Tone }) {
  const w = Math.max(0, Math.min(1, value / max)) * 100;
  const color = tone === "good" ? "bg-good" : tone === "warn" ? "bg-warn" : tone === "bad" ? "bg-bad" : tone === "info" ? "bg-info" : "bg-ink";
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-paper">
      <div className={clsx("h-full rounded-full", color)} style={{ width: `${w}%` }} />
    </div>
  );
}

export function Disclosure({ children }: { children: React.ReactNode }) {
  return <p className="text-xs leading-relaxed text-muted">{children}</p>;
}

/** Small "i" that states a modelling rule in plain words (hover / focus / tap). */
export function Hint({ children, label = "Modelling rule" }: { children: React.ReactNode; label?: string }) {
  return (
    <span className="group relative ml-1 inline-flex align-middle">
      <button type="button" aria-label={label} className="rounded-full text-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-ink">
        <Info className="h-3.5 w-3.5" aria-hidden />
      </button>
      <span role="tooltip" className="pointer-events-none absolute left-1/2 top-full z-30 mt-1 hidden w-64 -translate-x-1/2 rounded-lg border border-line bg-surface p-2.5 text-left text-xs font-normal normal-case leading-relaxed tracking-normal text-ink shadow-lg group-hover:block group-focus-within:block">
        {children}
      </span>
    </span>
  );
}
