import { api } from "../api";
import { Card, CardHead, Loading, OutcomeBadge, Problem, ReasonBadge, Tick } from "../components/ui";
import { useApi } from "../useApi";
import type { Signals } from "../types";

const SIGNALS: (keyof Signals)[] = ["reference", "amount", "name", "date"];

/**
 * The screen that wins. Plan section 16.5.
 *
 * The whole chain for one record: every signal with the weight it carried,
 * the settlement arithmetic, every rule ticked or crossed, and the model's
 * reasoning clearly marked as a recommendation. A judge reading this
 * understands the thesis without us explaining it.
 */
export function Trace({ id, onBack }: { id: string; onBack: () => void }) {
  const { data, error } = useApi(() => api.trace(id), [id]);

  if (error) return <Problem message={error} />;
  if (!data) return <Loading what={id} />;

  const { invoice, decision, rules, candidates, adjudicator, verdict } = data;
  const failed = rules.filter((r) => !r.passed);

  return (
    <div className="space-y-4">
      <button
        onClick={onBack}
        className="text-sm font-medium text-slate-600 transition-colors hover:text-slate-900"
      >
        ← Back to exceptions
      </button>

      {/* What was decided, and by which rule */}
      <Card className="px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2.5">
              <h1 className="font-mono text-lg font-semibold text-slate-900">{invoice.id}</h1>
              <OutcomeBadge outcome={decision.outcome} />
              {verdict.correct === true && (
                <span className="text-xs font-medium text-emerald-600">matches the answer key</span>
              )}
              {verdict.correct === false && (
                <span className="text-xs font-medium text-rose-600">
                  answer key says {verdict.expected}
                </span>
              )}
            </div>
            <p className="mt-1 text-sm text-slate-600">
              {invoice.counterparty} · due {invoice.due_date}
            </p>
          </div>
          <div className="text-right">
            <p className="text-2xl font-semibold tabular-nums tracking-tight text-slate-900">
              {invoice.amount.display}
            </p>
            <p className="mt-0.5 text-xs text-slate-500">
              score {decision.score.toFixed(2)} · margin {decision.margin.toFixed(2)} (
              {decision.margin_basis.replace("_", " ")})
            </p>
          </div>
        </div>

        <div className="mt-4 flex items-start gap-2.5 rounded-lg bg-slate-50 px-4 py-3">
          <ReasonBadge code={decision.reason_code} />
          <p className="text-sm leading-relaxed text-slate-700">{decision.reason_text}</p>
        </div>
      </Card>

      {/* Every rule, ticked or crossed */}
      <Card>
        <CardHead
          title="The rules"
          note={
            failed.length
              ? `${failed.length} failed, and the first one decided the outcome`
              : "all passed, so this was automated"
          }
        />
        <div className="grid gap-x-8 gap-y-2 px-5 py-4 sm:grid-cols-2 lg:grid-cols-3">
          {rules.map((rule) => (
            <div key={rule.name} className="flex items-center gap-2 text-sm">
              <Tick ok={rule.passed} />
              <span className={rule.passed ? "text-slate-600" : "font-medium text-slate-900"}>
                {rule.name}
              </span>
            </div>
          ))}
        </div>
      </Card>

      {/* The payments it chose between */}
      <Card>
        <CardHead
          title="Candidate payments"
          note="scored on four signals, with the weights each one actually carried"
        />
        <div className="divide-y divide-slate-50">
          {candidates.map((c) => (
            <div key={c.txn_id} className={c.chosen ? "bg-emerald-50/40 px-5 py-4" : "px-5 py-4"}>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs font-medium text-slate-900">{c.txn_id}</span>
                  {c.chosen && (
                    <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-700">
                      chosen
                    </span>
                  )}
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">
                    {c.matched_via.replace(/_/g, " ")}
                  </span>
                </div>
                <span className="font-medium tabular-nums text-slate-900">{c.amount.display}</span>
              </div>

              <p className="mt-1 truncate font-mono text-[11px] text-slate-500">{c.description}</p>

              <p className="mt-2 rounded bg-white px-3 py-2 text-xs text-slate-700 ring-1 ring-slate-100">
                {c.amount_status}
              </p>

              <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1.5 sm:grid-cols-4">
                {SIGNALS.map((key) => {
                  const value = c.signals[key];
                  const weight = c.weights_used[key];
                  return (
                    <div key={key}>
                      <div className="flex items-baseline justify-between text-[11px]">
                        <span className="text-slate-500">{key}</span>
                        <span className="font-medium tabular-nums text-slate-700">
                          {value === null ? "n/a" : value.toFixed(2)}
                        </span>
                      </div>
                      <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-slate-100">
                        <div
                          className={`h-full rounded-full ${value === null ? "bg-slate-200" : "bg-slate-700"}`}
                          style={{ width: `${(value ?? 0) * 100}%` }}
                        />
                      </div>
                      <p className="mt-0.5 text-[10px] text-slate-400">
                        {weight === undefined ? "dropped" : `weight ${weight.toFixed(2)}`}
                      </p>
                    </div>
                  );
                })}
              </div>

              <p className="mt-2 text-right text-xs font-medium tabular-nums text-slate-600">
                total {c.score.toFixed(3)}
              </p>
            </div>
          ))}
        </div>
      </Card>

      {/* The model, clearly labelled */}
      {adjudicator.used && (
        <Card>
          <CardHead title="What the model recommended" note={adjudicator.note} />
          <div className="px-5 py-4">
            {adjudicator.rejected ? (
              <p className="text-sm text-rose-700">
                Its answer was thrown away:{" "}
                <span className="font-mono text-xs">{adjudicator.rejected}</span>
              </p>
            ) : (
              <>
                <p className="text-sm leading-relaxed text-slate-700">{adjudicator.reasoning}</p>
                <p className="mt-2 text-xs text-slate-500">
                  confidence {adjudicator.confidence.toFixed(2)} · the hard rules above still
                  decided the outcome
                </p>
              </>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
