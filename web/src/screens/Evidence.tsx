import { api } from "../api";
import { Bar, Card, CardHead, Loading, Problem, Stat } from "../components/ui";
import { useApi } from "../useApi";
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
