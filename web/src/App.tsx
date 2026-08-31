import { useState } from "react";
import { api } from "./api";
import { Button } from "./components/ui";
import { Cash } from "./screens/Cash";
import { Evidence } from "./screens/Evidence";
import { Queue } from "./screens/Queue";
import { Record } from "./screens/Record";
import { Today } from "./screens/Today";
import { TryIt } from "./screens/TryIt";

/**
 * Four destinations and a top tab bar. No router, no state library.
 *
 * The queue is first because the work is first. A person opens this to clear
 * a pile, not to read a success rate.
 *
 * "How well it works" is deliberately its own tab. Accuracy is measured
 * against an answer key, and a real finance user has no answer key - so that
 * language stays off every screen they use daily.
 */
const TABS = [
  { id: "queue", label: "To review" },
  { id: "today", label: "This batch" },
  { id: "cash", label: "Cash" },
  { id: "evidence", label: "How well it works" },
  { id: "try", label: "Try your own" },
] as const;

type Tab = (typeof TABS)[number]["id"];

export default function App() {
  const [tab, setTab] = useState<Tab>("queue");
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
    <div className="min-h-screen bg-ink-50 text-ink-1200">
      {/* Sticky and solid. Every screen below is taller than the viewport, and
          losing the tabs halfway down a table is a real failure. */}
      <header className="sticky top-0 z-20 border-b border-ink-200 bg-ink-0">
        <div className="mx-auto flex max-w-[1200px] flex-wrap items-center justify-between gap-3 px-6 py-3">
          <div className="flex items-center gap-2.5">
            <Mark />
            <div>
              <h1 className="display text-[17px] leading-tight">Hisaab</h1>
              <p className="text-xs leading-tight text-ink-700">
                Matches your invoices to the money that came in
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <nav className="flex max-w-full overflow-x-auto rounded-lg bg-ink-50 p-0.5 ring-1 ring-inset ring-ink-200 [scrollbar-width:none] max-md:[mask-image:linear-gradient(to_right,#000_calc(100%-24px),transparent)] [&::-webkit-scrollbar]:hidden">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  aria-current={tab === t.id ? "page" : undefined}
                  onClick={() => {
                    setTab(t.id);
                    setOpenRecord(null);
                  }}
                  className={`shrink-0 whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium transition-colors duration-150 ${
                    tab === t.id
                      ? "bg-ink-0 text-ink-1200 shadow-[0_1px_2px_hsl(200_11%_11%_/_0.06)]"
                      : "text-ink-700 hover:text-ink-1200"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </nav>

            <span className="shrink-0">
              <Button onClick={rerun} disabled={running}>
                {running ? "Running…" : "Run again"}
              </Button>
            </span>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1200px] px-6 py-6">
        <div key={nonce}>
          {openRecord ? (
            <Record id={openRecord} onBack={() => setOpenRecord(null)} />
          ) : tab === "queue" ? (
            <Queue onOpen={setOpenRecord} />
          ) : tab === "today" ? (
            <Today />
          ) : tab === "cash" ? (
            <Cash />
          ) : tab === "evidence" ? (
            <Evidence />
          ) : (
            <TryIt />
          )}
        </div>
      </main>
    </div>
  );
}

/**
 * Two lines meeting.
 *
 * An invoice coming down and a payment coming up, converging on one point and
 * leaving as a single line. That convergence is the whole product.
 *
 * Deliberately not a letter: the previous mark was a white R on an azure
 * square, which is Razorpay's, not ours.
 */
function Mark() {
  return (
    <svg viewBox="0 0 28 28" className="size-7 shrink-0" aria-hidden="true">
      <rect width="28" height="28" rx="7" className="fill-brand-500" />
      <path
        d="M7 9h2.5a5 5 0 0 1 5 5H21M7 19h2.5a5 5 0 0 0 5-5"
        fill="none"
        stroke="white"
        strokeWidth="2.1"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
