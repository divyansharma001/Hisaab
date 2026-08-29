import type { ReactNode } from "react";
import type { Outcome } from "../types";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-slate-200 bg-white shadow-sm ${className}`}>
      {children}
    </div>
  );
}

export function CardHead({ title, note }: { title: string; note?: string }) {
  return (
    <div className="border-b border-slate-100 px-5 py-3.5">
      <h2 className="text-sm font-semibold tracking-tight text-slate-900">{title}</h2>
      {note && <p className="mt-0.5 text-xs text-slate-500">{note}</p>}
    </div>
  );
}

const OUTCOME_STYLE: Record<Outcome, string> = {
  AUTO: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  REVIEW: "bg-amber-50 text-amber-800 ring-amber-600/20",
  EXCEPTION: "bg-rose-50 text-rose-700 ring-rose-600/20",
  AMBIGUOUS: "bg-violet-50 text-violet-700 ring-violet-600/20",
};

export function OutcomeBadge({ outcome }: { outcome: Outcome }) {
  return (
    <span
      className={`inline-flex rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${OUTCOME_STYLE[outcome]}`}
    >
      {outcome}
    </span>
  );
}

export function ReasonBadge({ code }: { code: string | null }) {
  if (!code) return <span className="text-slate-400">—</span>;
  return (
    <span className="inline-flex rounded-md bg-slate-100 px-2 py-0.5 font-mono text-[11px] font-medium text-slate-700 ring-1 ring-inset ring-slate-200">
      {code}
    </span>
  );
}

export function Stat({
  label,
  value,
  note,
  tone = "default",
}: {
  label: string;
  value: string;
  note?: string;
  tone?: "default" | "good" | "warn";
}) {
  const valueTone =
    tone === "good" ? "text-emerald-600" : tone === "warn" ? "text-amber-600" : "text-slate-900";
  return (
    <Card className="px-5 py-4">
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className={`mt-1.5 text-2xl font-semibold tabular-nums tracking-tight ${valueTone}`}>
        {value}
      </dd>
      {note && <p className="mt-1 text-xs text-slate-500">{note}</p>}
    </Card>
  );
}

export function Tick({ ok }: { ok: boolean }) {
  return ok ? (
    <span className="text-emerald-600" aria-label="passed">✓</span>
  ) : (
    <span className="text-rose-600" aria-label="failed">✗</span>
  );
}

export function Loading({ what }: { what: string }) {
  return <p className="py-16 text-center text-sm text-slate-500">Loading {what}…</p>;
}

export function Problem({ message }: { message: string }) {
  return (
    <Card className="px-5 py-8 text-center">
      <p className="text-sm font-medium text-rose-700">Could not reach the API</p>
      <p className="mt-1 font-mono text-xs text-slate-500">{message}</p>
      <p className="mt-3 text-xs text-slate-500">
        Run <code className="rounded bg-slate-100 px-1 py-0.5">docker compose up -d</code>, or drop a
        snapshot at <code className="rounded bg-slate-100 px-1 py-0.5">web/public/results.json</code>.
      </p>
    </Card>
  );
}
