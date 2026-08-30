import { api } from "../api";
import { Bar, Card, CardHead, Label, Loading, Problem, Stat } from "../components/ui";
import { useApi } from "../useApi";
import type { Thresholds } from "../types";
import { scenarioText } from "../words";

/**
 * How well it works - the only screen that talks about being right or wrong.
 *
 * Accuracy here is measured against a known answer for every record, which is
 * something a real batch never has. Keeping that language in one place stops
 * the daily screens claiming a certainty they cannot have.
 */
export function Evidence() {
  const run = useApi(() => api.summary());
  const breakdown = useApi(() => api.breakdown());
  const curve = useApi(() => api.thresholds());

  if (run.error) return <Problem message={run.error} />;
  if (!run.data || !breakdown.data) return <Loading what="the test results" />;

  const r = run.data;
  const clean = r.false_auto_approvals === 0;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">How well it works</h1>
        <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-700">
          These {r.records} invoices were built with a known right answer, then held back while the
          system was tuned. Nothing here was used to teach it. This is the only place we compare
          what it decided against what was true.
        </p>
      </div>

      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Closed the wrong thing"
          value={String(r.false_auto_approvals)}
          note={
            clean
              ? "The number that has to be zero. It has stayed at zero on every run."
              : "This must be zero. Something is wrong."
          }
          tone={clean ? "good" : "bad"}
        />
        <Stat
          label="Got the answer right"
          value={`${r.outcome_accuracy}%`}
          note={`Named the right reason ${r.reason_accuracy}% of the time`}
        />
        <Stat
          label="Closed without a person"
          value={`${r.straight_through_count}%`}
          note={`${r.straight_through_value}% counting by rupees rather than by invoice`}
        />
        <Stat
          label="Missed problems"
          value={String(r.missed_exceptions)}
          note="Invoices we closed that should have been held back"
          tone={r.missed_exceptions === 0 ? "good" : "bad"}
        />
      </dl>

      <Card>
        <CardHead
          title="Broken down by the kind of problem"
          note="One overall number hides everything, so every kind of case is listed separately."
        />
        <div className="divide-y divide-ink-100">
          {breakdown.data.scenarios.map((s) => (
            <div key={s.scenario} className="flex items-center gap-4 px-5 py-2.5">
              <span className="w-52 shrink-0 text-sm text-ink-1200">
                {scenarioText(s.scenario)}
              </span>
              <div className="flex-1">
                <Bar
                  value={s.rate}
                  tone={s.false_approvals ? "bad" : s.rate === 100 ? "good" : "warn"}
                />
              </div>
              <span className="tnum w-14 shrink-0 text-right font-mono text-xs text-ink-700">
                {s.right}/{s.total}
              </span>
              {s.false_approvals > 0 && (
                <span className="whitespace-nowrap text-xs font-medium text-bad-600">
                  {s.false_approvals} wrong
                </span>
              )}
            </div>
          ))}
        </div>
      </Card>

      <SafetyCurve data={curve.data} />

      <Card>
        <CardHead title="Two things worth saying plainly" />
        <div className="grid gap-5 px-5 py-4 sm:grid-cols-2">
          <div>
            <h3 className="text-sm font-semibold text-ink-1200">The numbers move a little</h3>
            <p className="mt-1.5 text-sm leading-relaxed text-ink-700">
              The assistant is not perfectly repeatable. Across cold runs, roughly one in four it
              declines an invoice it usually accepts, and the share closed on its own drops by about
              one invoice. It has never gone the other way: nothing has ever been wrongly closed.
            </p>
          </div>
          <div>
            <h3 className="text-sm font-semibold text-ink-1200">It fails towards holding back</h3>
            <p className="mt-1.5 text-sm leading-relaxed text-ink-700">
              Holding an invoice costs a person a few minutes. Closing the wrong one costs money and
              trust. Every check is built so that when we are unsure, the invoice waits for you.
            </p>
          </div>
        </div>
      </Card>
    </div>
  );
}

/**
 * What the score bar actually buys. Plan section 19.6.
 *
 * We expected this to show the bar keeping us safe. It does not: with the
 * checks in place, no bar we tried produced a single wrong match. Turning the
 * checks off is what breaks it, so the panel draws both and lets the gap make
 * the point. Section 18, bug 14.
 */
function SafetyCurve({ data }: { data: Thresholds | null }) {
  if (!data) {
    return (
      <Card>
        <CardHead
          title="What actually keeps this safe"
          note="Running the batch again at five different strictness settings…"
        />
        <div className="space-y-3 px-5 py-5" aria-busy="true">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-4 animate-pulse rounded bg-ink-100" />
          ))}
        </div>
      </Card>
    );
  }

  const bare = new Map(data.score_only.map((p) => [p.bar, p]));
  const worst = Math.max(...data.score_only.map((p) => p.wrong), 1);

  return (
    <Card>
      <CardHead
        title="What actually keeps this safe"
        note="The same batch at different strictness settings, with our checks on and then off."
      />

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-ink-200 bg-ink-50 text-left">
              <th className="whitespace-nowrap px-5 py-2.5">
                <Label>Strictness</Label>
              </th>
              <th className="whitespace-nowrap px-5 py-2.5 text-right">
                <Label>Closed on their own</Label>
              </th>
              <th className="whitespace-nowrap px-5 py-2.5 text-right">
                <Label>Wrong</Label>
              </th>
              <th className="px-5 py-2.5">
                <Label>Without our checks</Label>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {data.with_rules.map((p) => {
              const off = bare.get(p.bar);
              return (
                <tr key={p.bar} className={p.is_current ? "bg-brand-50/60" : undefined}>
                  <td className="tnum whitespace-nowrap px-5 py-2.5 font-medium">
                    {p.bar.toFixed(2)}
                    {p.is_current && (
                      <span className="ml-2 rounded bg-brand-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.04em] text-brand-700">
                        in use
                      </span>
                    )}
                  </td>
                  <td className="tnum whitespace-nowrap px-5 py-2.5 text-right">{p.closed}%</td>
                  <td className="tnum whitespace-nowrap px-5 py-2.5 text-right font-medium text-good-600">
                    {p.wrong}
                  </td>
                  <td className="px-5 py-2.5">
                    {off && (
                      <div className="flex items-center gap-3">
                        <div className="w-28 shrink-0">
                          <Bar value={(off.wrong / worst) * 100} tone="bad" />
                        </div>
                        <span className="tnum whitespace-nowrap text-xs text-ink-700">
                          {off.closed}% closed, <span className="font-medium text-bad-600">{off.wrong} wrong</span>
                        </span>
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="border-t border-ink-200 px-5 py-3.5 text-sm leading-relaxed text-ink-700">
        We expected the strictness setting to be the thing protecting us. It is not - with our
        checks on, no setting we tried got a single match wrong. Turning the checks off is what
        breaks it: at the setting we use, that costs{" "}
        <span className="font-medium text-ink-1200">
          {data.cost_of_the_rules.wrong_approvals_prevented} wrong matches
        </span>
        , and the checks give up about{" "}
        <span className="font-medium text-ink-1200">
          {data.cost_of_the_rules.automation_given_up}%
        </span>{" "}
        of automation to prevent them. That is the trade, measured rather than assumed.
      </p>
    </Card>
  );
}
