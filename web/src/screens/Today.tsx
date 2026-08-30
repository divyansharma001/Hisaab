import { api } from "../api";
import {
  Bar,
  Card,
  CardHead,
  Loading,
  Problem,
  Stat,
  StatBand,
} from "../components/ui";
import { useApi } from "../useApi";
import { OUTCOME, OUTCOME_MEANING } from "../words";
import type { Outcome } from "../types";

/**
 * What this batch did, for the person who ran it.
 *
 * No accuracy, no answer key, no eval vocabulary - that lives in its own tab.
 * This screen answers "what happened just now and what does it leave me".
 */
export function Today() {
  const { data, error } = useApi(() => api.summary());

  if (error) return <Problem message={error} />;
  if (!data) return <Loading what="this batch" />;

  const r = data;
  const settled = r.outcomes.AUTO ?? 0;
  const waiting = r.records - settled;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="display text-2xl">This batch</h1>
        <p className="mt-1.5 text-sm text-ink-700">
          {r.records} invoices checked in {r.seconds} seconds.
        </p>
      </div>

      <StatBand>
        <Stat
          label="Closed on their own"
          value={String(settled)}
          note={`${r.straight_through_count}% of the batch, matched and done`}
          tone="good"
        />
        <Stat
          label="Left for you"
          value={String(waiting)}
          note="Each one names what is wrong"
          tone="warn"
        />
        <Stat
          label="Money accounted for"
          value={r.value_settled.display}
          note="Matched to an invoice and closed"
        />
        <Stat
          label="Money still open"
          value={r.value_held.display}
          note="Sitting in the list waiting for you"
          tone="warn"
        />
      </StatBand>

      <div className="grid gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHead title="Where the invoices ended up" />
          <div className="space-y-4 px-5 py-4">
            {(Object.entries(r.outcomes) as [Outcome, number][])
              .sort((a, b) => b[1] - a[1])
              .map(([outcome, count]) => (
                <div key={outcome}>
                  <div className="flex items-baseline justify-between gap-3 text-sm">
                    <span className="font-medium text-ink-1200">{OUTCOME[outcome].label}</span>
                    <span className="tnum font-medium">{count}</span>
                  </div>
                  <div className="mt-1.5">
                    <Bar value={(count / r.records) * 100} tone={OUTCOME[outcome].tone} />
                  </div>
                  <p className="mt-1 text-xs text-ink-700">{OUTCOME_MEANING[outcome]}</p>
                </div>
              ))}
          </div>
        </Card>

        <Card className="lg:col-span-2">
          <CardHead
            title="What this run cost"
            note="We only pay for the invoices that were genuinely unclear."
          />
          <dl className="divide-y divide-ink-100 text-sm">
            {[
              ["Speed", `${r.records_per_second} invoices a second`],
              ["Asked the assistant", `${r.llm_calls} of ${r.records}`],
              ["Cost of this run", r.cost.display],
              ["Cost per 1,000 invoices", r.cost_per_1000.display],
            ].map(([label, value]) => (
              <div key={label} className="flex items-baseline justify-between gap-3 px-5 py-2.5">
                <dt className="text-ink-700">{label}</dt>
                <dd className="tnum font-medium text-ink-1200">{value}</dd>
              </div>
            ))}
          </dl>
          <p className="border-t border-ink-100 px-5 py-3 text-xs leading-relaxed text-ink-700">
            By hand, {r.records} invoices at three minutes each is about{" "}
            {Math.round((r.records * 3) / 60)} hours.
          </p>
        </Card>
      </div>
    </div>
  );
}
