import type {
  AdjudicatedList,
  AskAnswer,
  CashPosition,
  EvalBreakdown,
  ExceptionList,
  RunSummary,
  Thresholds,
  Trace,
} from "./types";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

/**
 * Read from the API, and fall back to a snapshot on disk if it is not there.
 *
 * Fifteen minutes of work that saves the demo. After a good run the backend
 * writes `public/results.json`; if the API dies five minutes before
 * presenting, every screen still has real numbers to show.
 */
type Snapshot = Record<string, unknown> | null;

/**
 * Cache the promise, not a "loaded" flag.
 *
 * A flag set before the fetch resolves is a race: every screen fires two or
 * more requests at once, they all miss the API together, and the second one
 * to ask sees loaded=true with the value still null - so the fallback throws
 * on exactly the screens it exists to save.
 *
 * Caching the promise makes concurrent callers await the same fetch.
 */
let snapshotPromise: Promise<Snapshot> | null = null;

function loadSnapshot(): Promise<Snapshot> {
  snapshotPromise ??= (async () => {
    try {
      const res = await fetch("/results.json");
      return res.ok ? ((await res.json()) as Snapshot) : null;
    } catch {
      return null;
    }
  })();
  return snapshotPromise;
}

async function get<T>(path: string, snapshotKey: string): Promise<T> {
  try {
    const res = await fetch(`${API_URL}${path}`);
    if (!res.ok) throw new Error(`${res.status}`);
    return (await res.json()) as T;
  } catch (err) {
    const fallback = await loadSnapshot();
    if (fallback && snapshotKey in fallback) return fallback[snapshotKey] as T;
    throw err;
  }
}

export const api = {
  summary: () => get<RunSummary>("/api/runs/latest", "summary"),
  exceptions: () => get<ExceptionList>("/api/exceptions", "exceptions"),
  breakdown: () => get<EvalBreakdown>("/api/eval", "eval"),
  adjudicated: () => get<AdjudicatedList>("/api/adjudicated", "adjudicated"),
  cash: () => get<CashPosition>("/api/cash-position", "cash"),
  thresholds: () => get<Thresholds>("/api/thresholds", "thresholds"),
  trace: (id: string) => get<Trace>(`/api/records/${id}`, `trace_${id}`),
  // Asking is a live question, so there is no snapshot to fall back on.
  ask: async (question: string): Promise<AskAnswer> => {
    const res = await fetch(`${API_URL}/api/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (!res.ok) throw new Error(`${res.status}`);
    return res.json();
  },
  run: async () => {
    const res = await fetch(`${API_URL}/api/runs`, { method: "POST" });
    if (!res.ok) throw new Error(`${res.status}`);
    return res.json();
  },
};
