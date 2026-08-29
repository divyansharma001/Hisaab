import { useState } from "react";
import { api } from "./api";
import { Cash } from "./screens/Cash";
import { Exceptions } from "./screens/Exceptions";
import { Summary } from "./screens/Summary";
import { Trace } from "./screens/Trace";

/**
 * Four screens and a tab bar. No router, no state library.
 *
 * The exception list is first on purpose: the demo opens on what we could not
 * do, because one cherry-picked match proves nothing.
 */
const TABS = [
  { id: "exceptions", label: "Exceptions" },
  { id: "summary", label: "This run" },
  { id: "cash", label: "Cash position" },
] as const;

type Tab = (typeof TABS)[number]["id"];

export default function App() {
  const [tab, setTab] = useState<Tab>("exceptions");
  const [openRecord, setOpenRecord] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [nonce, setNonce] = useState(0);

  async function rerun() {
    setRunning(true);
    try {
      await api.run();
      setOpenRecord(null);
      setNonce((n) => n + 1);
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-6 py-4">
          <div>
            <h1 className="text-base font-semibold tracking-tight">Hisaab</h1>
            <p className="text-xs text-slate-500">
              Matches invoices to bank payments, and is honest about the rest
            </p>
          </div>

          <div className="flex items-center gap-2">
            <nav className="flex rounded-lg bg-slate-100 p-0.5">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  onClick={() => {
                    setTab(t.id);
                    setOpenRecord(null);
                  }}
                  className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                    tab === t.id
                      ? "bg-white text-slate-900 shadow-sm"
                      : "text-slate-600 hover:text-slate-900"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </nav>

            <button
              onClick={rerun}
              disabled={running}
              className="rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-slate-700 disabled:opacity-50"
            >
              {running ? "Running…" : "Run batch"}
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-6 py-6">
        <div key={nonce}>
          {openRecord ? (
            <Trace id={openRecord} onBack={() => setOpenRecord(null)} />
          ) : tab === "exceptions" ? (
            <Exceptions onOpen={setOpenRecord} />
          ) : tab === "summary" ? (
            <Summary />
          ) : (
            <Cash />
          )}
        </div>
      </main>
    </div>
  );
}
