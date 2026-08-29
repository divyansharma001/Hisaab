import { api } from "../api";
import { Card, CardHead, Loading, Problem, Stat } from "../components/ui";
import { useApi } from "../useApi";

/** The headline, and the per-scenario table that stops it hiding anything. */
export function Summary() {
  const run = useApi(() => api.summary());
  const breakdown = useApi(() => api.breakdown());

  if (run.error) return <Problem message={run.error} />;
  if (!run.data || !breakdown.data) return <Loading what="the run" />;

  const r = run.data;
  const clean = r.false_auto_approvals === 0;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-slate-900">This run</h1>
        <p className="mt-1 text-sm text-slate-600">
          {r.records} records in {r.seconds}s ({r.records_per_second}/sec) ·{" "}
          {r.llm_calls} model {r.llm_calls === 1 ? "call" : "calls"} · {r.split}
        </p>
      </div>

      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Wrong auto-approvals"
          value={String(r.false_auto_approvals)}
          note={clean ? "the number that has to be zero" : "must be zero"}
          tone={clean ? "good" : "warn"}
        />
        <Stat
          label="Straight through"
          value={`${r.straight_through_count}%`}
          note={`${r.straight_through_value}% by value`}
        />
        <Stat
          label="Outcome accuracy"
          value={`${r.outcome_accuracy}%`}
          note={`reason codes ${r.reason_accuracy}%`}
        />
        <Stat
          label="Held for a human"
          value={r.value_held.display}
          note="money we cannot yet account for"
        />
      </dl>

      <div className="grid gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHead
            title="By scenario"
            note="aggregate numbers hide everything, so every case type is listed"
          />
          <div className="divide-y divide-slate-50">
            {breakdown.data.scenarios.map((s) => (
              <div key={s.scenario} className="flex items-center gap-4 px-5 py-2.5">
                <span className="w-48 shrink-0 text-sm text-slate-700">
                  {s.scenario.replace(/_/g, " ")}
                </span>
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                  <div
                    className={`h-full rounded-full ${
                      s.false_approvals ? "bg-rose-500" : s.rate === 100 ? "bg-emerald-500" : "bg-amber-500"
                    }`}
                    style={{ width: `${s.rate}%` }}
                  />
                </div>
                <span className="w-14 shrink-0 text-right font-mono text-xs tabular-nums text-slate-600">
                  {s.right}/{s.total}
                </span>
                {s.false_approvals > 0 && (
                  <span className="text-xs font-medium text-rose-600">
                    {s.false_approvals} wrong
                  </span>
                )}
              </div>
            ))}
          </div>
        </Card>

        <Card className="lg:col-span-2">
          <CardHead title="Where every record went" />
          <div className="space-y-3 px-5 py-4">
            {Object.entries(r.outcomes)
              .sort((a, b) => b[1] - a[1])
              .map(([outcome, count]) => (
                <div key={outcome}>
                  <div className="flex items-baseline justify-between text-sm">
                    <span className="text-slate-700">{outcome}</span>
                    <span className="font-medium tabular-nums text-slate-900">{count}</span>
                  </div>
                  <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
                    <div
                      className="h-full rounded-full bg-slate-700"
                      style={{ width: `${(count / r.records) * 100}%` }}
                    />
                  </div>
                </div>
              ))}
            <p className="pt-2 text-xs leading-relaxed text-slate-500">
              Settled {r.value_settled.display} without a human touching it, at{" "}
              {r.auto_precision}% precision.
            </p>
          </div>
        </Card>
      </div>
    </div>
  );
}
