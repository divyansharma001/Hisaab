import { api } from "../api";
import { Bar, Card, CardHead, Label, Loading, Problem, Stat } from "../components/ui";
import { useApi } from "../useApi";
import type {
  Ablation,
  Learning as LearningData,
  Mistakes as MistakesData,
  Thresholds,
} from "../types";
import { OUTCOME, scenarioText } from "../words";

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
  const layers = useApi(() => api.ablation());
  const wrong = useApi(() => api.mistakes());
  const learning = useApi(() => api.learning());

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

      <Layers data={layers.data} />

      <Learning data={learning.data} />

      <Mistakes data={wrong.data} />

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
        note="The same batch at five strictness settings, with our checks on and then off. Both columns keep everything we remember, so the only thing changing is the checks."
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
                <Label>Same, checks off</Label>
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

/**
 * Did each layer earn its place? Plan section 19.1.
 *
 * Every row is a real run over the same records, so this answers the question
 * a judge actually has - did the clever parts do anything - with a
 * measurement rather than a claim.
 */
function Layers({ data }: { data: Ablation | null }) {
  if (!data) return null;

  return (
    <Card>
      <CardHead
        title="What each part of it is worth"
        note={`The same ${data.records} invoices, run again with one layer at a time switched on.`}
      />
      <div className="divide-y divide-ink-100">
        {data.rows.map((row) => (
          <div key={row.layer} className="px-5 py-3.5">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <span className="text-sm font-medium text-ink-1200">{row.layer}</span>
              <div className="flex items-baseline gap-5 text-sm">
                <span className="tnum text-ink-700">
                  {row.closed}% <span className="text-ink-600">closed</span>
                </span>
                <span
                  className={`tnum font-medium ${row.wrong ? "text-bad-600" : "text-good-600"}`}
                >
                  {row.wrong} wrong
                </span>
              </div>
            </div>
            <p className="mt-1 max-w-3xl text-xs leading-relaxed text-ink-700">
              {row.explanation}
            </p>
          </div>
        ))}
      </div>
      <p className="border-t border-ink-200 px-5 py-3 text-xs leading-relaxed text-ink-700">
        The assistant is left out of this table on purpose. Running it on every page load would
        cost money and its answer moves a little between runs, so the table would disagree with
        itself while you read it. Its contribution is on the batch screen instead, measured on the
        run that just happened.
      </p>
    </Card>
  );
}

/**
 * The records we got wrong, named. Plan section 19.8.
 *
 * The direction of the mistake is the part that matters: being too careful
 * costs a person a few minutes, being too confident costs money.
 */
function Mistakes({ data }: { data: MistakesData | null }) {
  if (!data) return null;

  if (data.count === 0) {
    return (
      <Card>
        <CardHead title="What we got wrong" />
        <p className="px-5 py-4 text-sm leading-relaxed text-ink-700">
          Nothing in this run. That is not always true - the assistant is not perfectly repeatable,
          and on some runs one invoice is held that should have closed. It has never gone the other
          way.
        </p>
      </Card>
    );
  }

  return (
    <Card>
      <CardHead
        title={`What we got wrong: ${data.count} of them`}
        note={
          data.all_in_one_direction
            ? "Every one is a case we were too careful about, not one we closed wrongly."
            : "Some of these were closed when they should not have been."
        }
      />
      <div className="divide-y divide-ink-100">
        {data.mistakes.map((m) => (
          <div key={m.invoice_id} className="px-5 py-3.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2.5">
                <span className="whitespace-nowrap font-mono text-xs font-medium">
                  {m.invoice_id}
                </span>
                <span className="text-sm text-ink-1200">{m.customer}</span>
                <span className="rounded bg-ink-50 px-1.5 py-0.5 text-[10px] text-ink-700 ring-1 ring-inset ring-ink-200">
                  {scenarioText(m.scenario)}
                </span>
              </div>
              <span className="tnum whitespace-nowrap text-sm font-medium">
                {m.amount.display}
              </span>
            </div>
            <p className="mt-1.5 max-w-3xl text-xs leading-relaxed text-ink-700">
              We said <span className="font-medium text-ink-1200">{OUTCOME[m.we_said].label}</span>;
              it should have been{" "}
              <span className="font-medium text-ink-1200">{OUTCOME[m.answer_was].label}</span>. Our
              reason was &ldquo;{m.our_reason}&rdquo; We erred towards {m.erred_towards}
              {m.asked_the_assistant ? ", and we did ask the assistant about this one." : "."}
            </p>
          </div>
        ))}
      </div>
    </Card>
  );
}

/**
 * The learning loop, measured. Plan section 19.3.
 *
 * Starts from half our history and says so. With the history we actually
 * ship, nothing is ever held for being a new customer, so there would be
 * nothing to confirm and nothing to show. Section 18, bug 15.
 */
function Learning({ data }: { data: LearningData | null }) {
  if (!data) return null;

  const gain = data.closed_after - data.closed_before;

  return (
    <Card>
      <CardHead
        title="It gets better as you use it"
        note={data.note}
      />
      <div className="grid gap-4 px-5 py-4 sm:grid-cols-3">
        <div>
          <Label>Before</Label>
          <p className="tnum mt-1.5 text-2xl font-semibold">
            {data.closed_before}
            <span className="text-base font-normal text-ink-700">/{data.records}</span>
          </p>
          <p className="mt-1 text-xs text-ink-700">
            closed on their own, knowing {data.customers_known_at_the_start} of{" "}
            {data.customers_in_the_batch} customers
          </p>
        </div>
        <div>
          <Label>A reviewer confirms</Label>
          <p className="tnum mt-1.5 text-2xl font-semibold text-brand-600">{data.confirmed}</p>
          <p className="mt-1 text-xs text-ink-700">
            names they were shown, one click each
          </p>
        </div>
        <div>
          <Label>After</Label>
          <p className="tnum mt-1.5 text-2xl font-semibold text-good-600">
            {data.closed_after}
            <span className="text-base font-normal text-ink-700">/{data.records}</span>
          </p>
          <p className="mt-1 text-xs text-ink-700">
            closed on their own, {gain > 0 ? `${gain} more than before` : "no change"}
          </p>
        </div>
      </div>
      <p className="border-t border-ink-200 px-5 py-3 text-xs leading-relaxed text-ink-700">
        {data.knock_on_note}
      </p>
    </Card>
  );
}
