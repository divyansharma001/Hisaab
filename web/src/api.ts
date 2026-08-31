import type {
  Ablation,
  AdjudicatedList,
  AskAnswer,
  ConfirmResult,
  Learning,
  Mistakes,
  SandboxContents,
  SandboxResult,
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

/**
 * A live call with no snapshot behind it.
 *
 * The API answers a bad entry with a sentence written for the person who
 * typed it, so that sentence is thrown as the error rather than a status
 * code.
 */
async function json<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await res.json().catch(() => null);
  if (!res.ok) throw new Error(payload?.detail ?? `Something went wrong (${res.status})`);
  return payload as T;
}

export const api = {
  summary: () => get<RunSummary>("/api/runs/latest", "summary"),
  exceptions: () => get<ExceptionList>("/api/exceptions", "exceptions"),
  breakdown: () => get<EvalBreakdown>("/api/eval", "eval"),
  adjudicated: () => get<AdjudicatedList>("/api/adjudicated", "adjudicated"),
  cash: () => get<CashPosition>("/api/cash-position", "cash"),
  thresholds: () => get<Thresholds>("/api/thresholds", "thresholds"),
  ablation: () => get<Ablation>("/api/ablation", "ablation"),
  mistakes: () => get<Mistakes>("/api/mistakes", "mistakes"),
  learning: () => get<Learning>("/api/learning", "learning"),
  trace: (id: string) => get<Trace>(`/api/records/${id}`, `trace_${id}`),
  // The scratch set is live state a visitor is editing, so none of it falls
  // back to a snapshot.
  sandbox: {
    read: async (): Promise<SandboxContents> => json("GET", "/api/sandbox"),
    addInvoice: (body: Record<string, string>) =>
      json<SandboxContents>("POST", "/api/sandbox/invoices", body),
    addPayment: (body: Record<string, string>) =>
      json<SandboxContents>("POST", "/api/sandbox/payments", body),
    match: () => json<SandboxResult>("POST", "/api/sandbox/match"),
    clear: () => json<SandboxContents>("DELETE", "/api/sandbox"),
  },

  // Confirming changes state, so it never falls back to a snapshot.
  confirm: async (invoiceId: string): Promise<ConfirmResult> => {
    const res = await fetch(`${API_URL}/api/confirm/${invoiceId}`, { method: "POST" });
    if (!res.ok) throw new Error(`${res.status}`);
    return res.json();
  },
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
