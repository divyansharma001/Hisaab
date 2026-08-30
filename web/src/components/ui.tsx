import type { ReactNode } from "react";
import type { Outcome } from "../types";
import { OUTCOME } from "../words";

/**
 * The shared vocabulary. Blade tokens, one shape language, every state.
 *
 * A control that looks one way here and another way on the next screen is a
 * bug, so every screen builds from these and nothing rolls its own.
 */

type Tone = "good" | "warn" | "bad" | "info" | "neutral" | "brand";

const TINT: Record<Tone, string> = {
  good: "bg-good-50 text-good-700 ring-good-500/20",
  warn: "bg-warn-50 text-warn-800 ring-warn-600/20",
  bad: "bg-bad-50 text-bad-700 ring-bad-600/20",
  info: "bg-info-50 text-info-700 ring-info-600/20",
  brand: "bg-brand-50 text-brand-700 ring-brand-500/20",
  neutral: "bg-ink-50 text-ink-700 ring-ink-200",
};

const FILL: Record<Tone, string> = {
  good: "bg-good-500",
  warn: "bg-warn-500",
  bad: "bg-bad-500",
  info: "bg-info-500",
  brand: "bg-brand-500",
  neutral: "bg-ink-400",
};

const TEXT: Record<Tone, string> = {
  good: "text-good-600",
  warn: "text-warn-700",
  bad: "text-bad-600",
  info: "text-info-700",
  brand: "text-brand-600",
  neutral: "text-ink-1200",
};

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-[8px] border border-ink-200 bg-ink-0 shadow-[0_1px_2px_hsl(200_11%_11%_/_0.04)] ${className}`}
    >
      {children}
    </div>
  );
}

export function CardHead({ title, note }: { title: string; note?: string }) {
  return (
    <div className="border-b border-ink-200 px-5 py-3.5">
      <h2 className="text-sm font-semibold text-ink-1200">{title}</h2>
      {note && <p className="mt-1 text-xs leading-relaxed text-ink-700">{note}</p>}
    </div>
  );
}

/** The label above a number. Uppercase lives here and nowhere else. */
export function Label({ children }: { children: ReactNode }) {
  return (
    <span className="text-[11px] font-medium uppercase tracking-[0.04em] text-ink-700">
      {children}
    </span>
  );
}

/**
 * A number with its caption and, always, a sentence saying what it means.
 * A bare big number is the hero-metric template; the sentence is what makes
 * it useful to someone who did not build this.
 */
export function Stat({
  label,
  value,
  note,
  tone = "neutral",
}: {
  label: string;
  value: string;
  note: string;
  tone?: Tone;
}) {
  return (
    <Card className="px-5 py-4">
      <dt>
        <Label>{label}</Label>
      </dt>
      <dd className={`tnum mt-2 text-2xl font-semibold tracking-tight ${TEXT[tone]}`}>{value}</dd>
      <p className="mt-1.5 text-xs leading-relaxed text-ink-700">{note}</p>
    </Card>
  );
}

export function StatusPill({ outcome }: { outcome: Outcome }) {
  const { label, tone } = OUTCOME[outcome];
  return (
    <span
      className={`inline-flex whitespace-nowrap rounded-md px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${TINT[tone]}`}
    >
      {label}
    </span>
  );
}

/** The reason, as a phrase. Never the raw constant. */
export function ReasonChip({ children }: { children: ReactNode }) {
  if (!children) return <span className="text-ink-400">—</span>;
  return (
    <span className="inline-flex w-fit shrink-0 self-start whitespace-nowrap rounded-md bg-ink-50 px-2 py-0.5 text-[11px] font-medium text-ink-800 ring-1 ring-inset ring-ink-200">
      {children}
    </span>
  );
}

export function Bar({ value, tone = "brand" }: { value: number; tone?: Tone }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink-100">
      <div
        className={`h-full rounded-full transition-[width] duration-200 ${FILL[tone]}`}
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  );
}

export function Tick({ ok }: { ok: boolean }) {
  return ok ? (
    <svg viewBox="0 0 16 16" aria-label="passed" role="img" className="size-4 shrink-0 text-good-600">
      <path
        d="M3.5 8.5l3 3 6-7"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  ) : (
    <svg viewBox="0 0 16 16" aria-label="failed" role="img" className="size-4 shrink-0 text-bad-600">
      <path
        d="M4.5 4.5l7 7M11.5 4.5l-7 7"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function Button({
  children,
  onClick,
  variant = "primary",
  disabled,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "quiet";
  disabled?: boolean;
}) {
  const base =
    "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50";
  const look =
    variant === "primary"
      ? "bg-brand-500 text-white hover:bg-brand-600 active:bg-brand-700 disabled:hover:bg-brand-500"
      : "text-ink-700 hover:bg-ink-50 hover:text-ink-1200 active:bg-ink-100";
  return (
    <button type="button" onClick={onClick} disabled={disabled} className={`${base} ${look}`}>
      {children}
    </button>
  );
}

/**
 * Skeleton rows in the real layout, not a spinner.
 *
 * The first load runs a whole batch and takes several seconds, so this is on
 * screen long enough to matter.
 */
export function Loading({ what }: { what: string }) {
  return (
    <div className="space-y-4" aria-busy="true" aria-live="polite">
      <div>
        <div className="h-6 w-56 animate-pulse rounded bg-ink-200" />
        <p className="mt-2 text-sm text-ink-700">Working through {what}…</p>
      </div>
      <Card>
        <div className="divide-y divide-ink-100">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="flex items-center gap-4 px-5 py-3.5">
              <div className="h-3 w-20 animate-pulse rounded bg-ink-100" />
              <div className="h-3 w-40 animate-pulse rounded bg-ink-100" />
              <div className="ml-auto h-3 w-28 animate-pulse rounded bg-ink-100" />
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

/**
 * A panel that has not arrived yet.
 *
 * Several of these run the batch again and land a couple of seconds after the
 * rest of the page. Rendering nothing until then makes the page grow under
 * the reader; a placeholder of the right height does not.
 */
export function PanelSkeleton({ title, note, rows = 4 }: { title: string; note?: string; rows?: number }) {
  return (
    <Card>
      <CardHead title={title} note={note} />
      <div className="space-y-3 px-5 py-5" aria-busy="true">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="h-4 animate-pulse rounded bg-ink-100" />
        ))}
      </div>
    </Card>
  );
}

/** An empty state that teaches the screen rather than saying "no data". */
export function Empty({ title, body }: { title: string; body: string }) {
  return (
    <Card className="px-6 py-12 text-center">
      <p className="text-sm font-semibold text-ink-1200">{title}</p>
      <p className="mx-auto mt-1.5 max-w-md text-sm leading-relaxed text-ink-700">{body}</p>
    </Card>
  );
}

export function Problem({ message }: { message: string }) {
  return (
    <Card className="px-6 py-10 text-center">
      <p className="text-sm font-semibold text-bad-700">We could not load this</p>
      <p className="mt-1.5 text-sm text-ink-700">
        The service is not responding, and there is no saved copy to fall back on.
      </p>
      <p className="mt-3 font-mono text-xs text-ink-500">{message}</p>
      <p className="mt-4 text-xs text-ink-700">
        Start it with{" "}
        <code className="rounded bg-ink-50 px-1.5 py-0.5 font-mono ring-1 ring-inset ring-ink-200">
          docker compose up -d
        </code>
      </p>
    </Card>
  );
}
