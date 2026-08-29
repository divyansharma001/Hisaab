import { useEffect, useState } from "react";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

type Health = {
  status: string;
  database: string;
  llm_key_configured: boolean;
  version: string;
};

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/health`)
      .then((r) => r.json())
      .then(setHealth)
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <main className="min-h-screen bg-neutral-50 text-neutral-900">
      <div className="mx-auto max-w-2xl px-6 py-20">
        <h1 className="text-3xl font-semibold tracking-tight">Hisaab</h1>
        <p className="mt-2 text-neutral-600">
          An AI finance controller that matches invoices to bank payments.
        </p>

        <div className="mt-10 rounded-lg border border-neutral-200 bg-white p-5">
          <h2 className="text-sm font-medium text-neutral-500 uppercase tracking-wide">
            Phase 0 &middot; stack check
          </h2>

          {error && <p className="mt-4 text-red-600">API unreachable: {error}</p>}

          {!error && !health && <p className="mt-4 text-neutral-500">Checking...</p>}

          {health && (
            <dl className="mt-4 space-y-2 text-sm">
              <Row label="Web" value="up" ok />
              <Row label="API" value={health.status} ok={health.status === "ok"} />
              <Row label="Database" value={health.database} ok={health.database === "up"} />
              <Row
                label="LLM key"
                value={health.llm_key_configured ? "configured" : "missing"}
                ok={health.llm_key_configured}
              />
            </dl>
          )}
        </div>
      </div>
    </main>
  );
}

function Row({ label, value, ok }: { label: string; value: string; ok: boolean }) {
  return (
    <div className="flex items-center justify-between border-b border-neutral-100 pb-2 last:border-0">
      <dt className="text-neutral-600">{label}</dt>
      <dd
        className={`rounded px-2 py-0.5 text-xs font-medium ${
          ok ? "bg-green-100 text-green-800" : "bg-amber-100 text-amber-800"
        }`}
      >
        {value}
      </dd>
    </div>
  );
}
