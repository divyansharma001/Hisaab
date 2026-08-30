import { useState } from "react";
import { api } from "../api";
import {
  Bar,
  Button,
  Card,
  CardHead,
  Loading,
  Problem,
  ReasonChip,
  StatusPill,
  Tick,
} from "../components/ui";
import { useApi } from "../useApi";
import type { AskAnswer, Signals } from "../types";
import {
  asSentence,
  checkText,
  OUTCOME_MEANING,
  reasonText,
  shapeText,
  SIGNAL,
  strength,
} from "../words";

const SIGNALS: (keyof Signals)[] = ["reference", "amount", "name", "date"];

/**
 * One invoice, explained end to end.
 *
 * The order is the order a person asks the questions in: what did you decide,
 * why, which payments did you look at, and did you ask the assistant.
 */
export function Record({ id, onBack }: { id: string; onBack: () => void }) {
  const { data, error } = useApi(() => api.trace(id), [id]);

  if (error) return <Problem message={error} />;
  if (!data) return <Loading what={id} />;

  const { invoice, decision, rules, candidates, adjudicator } = data;
  const failed = rules.filter((r) => !r.passed);
  // Failures first. They decided the outcome, so they should not have to be
  // picked out of a wall of ticks.
  const ordered = [...failed, ...rules.filter((r) => r.passed)];

  return (
    <div className="space-y-4">
      <button
        type="button"
        onClick={onBack}
        className="inline-flex items-center gap-1.5 text-sm font-medium text-ink-700 transition-colors duration-150 hover:text-ink-1200"
      >
        <svg viewBox="0 0 16 16" className="size-4" aria-hidden="true">
          <path
            d="M10 3.5L5.5 8l4.5 4.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.75"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        Back to the list
      </button>

      <Card className="px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2.5">
              <h1 className="font-mono text-lg font-semibold">{invoice.id}</h1>
              <StatusPill outcome={decision.outcome} />
            </div>
            <p className="mt-1.5 text-sm text-ink-700">
              {invoice.counterparty} · due {invoice.due_date}
            </p>
          </div>
          <div className="text-right">
            <p className="tnum whitespace-nowrap text-2xl font-semibold tracking-tight">
              {invoice.amount.display}
            </p>
            <p className="mt-1 text-xs text-ink-700">
              Match strength: {strength(decision.score)}
            </p>
          </div>
        </div>

        <div className="mt-4 rounded-md bg-ink-50 px-4 py-3 ring-1 ring-inset ring-ink-200">
          <div className="flex flex-wrap items-center gap-2">
            <ReasonChip>{reasonText(decision.reason_code)}</ReasonChip>
            <span className="text-xs text-ink-700">{OUTCOME_MEANING[decision.outcome]}</span>
          </div>
          <p className="mt-2 text-sm leading-relaxed text-ink-1200">{decision.reason_text}</p>
        </div>
      </Card>

      <Card>
        <CardHead
          title="What we checked"
          note={
            failed.length
              ? `${failed.length} of these did not pass, and the first one decided it`
              : "All of these passed, which is why it closed on its own"
          }
        />
        <div className="grid gap-x-8 gap-y-2.5 px-5 py-4 sm:grid-cols-2 lg:grid-cols-3">
          {ordered.map((rule) => (
            <div key={rule.name} className="flex items-center gap-2 text-sm">
              <Tick ok={rule.passed} />
              <span className={rule.passed ? "text-ink-700" : "font-medium text-ink-1200"}>
                {checkText(rule.name)}
              </span>
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <CardHead
          title="Payments we compared"
          note="Each one is scored on four things. A thing we cannot judge is left out rather than counted as zero."
        />
        <div className="divide-y divide-ink-100">
          {candidates.map((c) => (
            <div key={c.txn_id} className={c.chosen ? "bg-good-50/50 px-5 py-4" : "px-5 py-4"}>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="whitespace-nowrap font-mono text-xs font-medium">
                    {c.txn_id}
                  </span>
                  {c.chosen && (
                    <span className="rounded bg-good-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.04em] text-good-700">
                      This one
                    </span>
                  )}
                  <span className="rounded bg-ink-50 px-1.5 py-0.5 text-[10px] text-ink-700 ring-1 ring-inset ring-ink-200">
                    {shapeText(c.matched_via)}
                  </span>
                </div>
                <span className="tnum whitespace-nowrap text-sm font-medium">
                  {c.amount.display}
                </span>
              </div>

              <p className="mt-1.5 truncate font-mono text-[11px] text-ink-600">{c.description}</p>

              <p className="mt-2.5 rounded bg-ink-0 px-3 py-2 text-xs leading-relaxed text-ink-1200 ring-1 ring-inset ring-ink-200">
                {asSentence(c.amount_status)}
              </p>

              <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
                {SIGNALS.map((key) => {
                  const value = c.signals[key];
                  const weight = c.weights_used[key];
                  const dropped = weight === undefined;
                  return (
                    <div key={key} title={SIGNAL[key].hint}>
                      <div className="flex items-baseline justify-between gap-2 text-[11px]">
                        <span className="text-ink-700">{SIGNAL[key].label}</span>
                        <span className="tnum font-medium text-ink-1200">
                          {value === null ? "—" : value.toFixed(2)}
                        </span>
                      </div>
                      <div className="mt-1.5">
                        <Bar
                          value={(value ?? 0) * 100}
                          tone={dropped ? "neutral" : c.chosen ? "good" : "brand"}
                        />
                      </div>
                      <p className="mt-1 text-[10px] text-ink-600">
                        {dropped ? "nothing to go on" : `counts for ${Math.round(weight * 100)}%`}
                      </p>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </Card>

      {/* Shown even when the assistant was never asked. "We did not need to
          spend anything here" is the point, not an absence - hiding the panel
          made most records look like the assistant does not exist. */}
      <Card>
        <CardHead
          title={adjudicator.used ? "What the assistant said" : "We did not need the assistant"}
          note={
            adjudicator.used
              ? "An opinion, not the decision. The checks above still had the final say."
              : "Arithmetic settled this one on its own."
          }
        />
        <div className="px-5 py-4">
          {!adjudicator.used ? (
            <p className="max-w-3xl text-sm leading-relaxed text-ink-700">
              We only ask for a second opinion when the payment is genuinely unclear and nothing
              else is wrong. Here {failed.length ? "a check failed outright" : "the answer was already clear"}
              , so no opinion could have changed the outcome.
            </p>
          ) : adjudicator.rejected ? (
            <p className="text-sm leading-relaxed text-bad-700">
              We threw its answer away: {adjudicator.rejected.replace(/_/g, " ")}.
            </p>
          ) : (
            <>
              <p className="max-w-3xl text-sm leading-relaxed text-ink-1200">
                {adjudicator.reasoning}
              </p>
              <p className="mt-2.5 text-xs text-ink-700">
                How sure it was: {(adjudicator.confidence * 100).toFixed(0)}%
              </p>
            </>
          )}
        </div>
      </Card>

      <Ask invoiceId={invoice.id} />
    </div>
  );
}

/**
 * Ask a question about this invoice. Plan section 19.4.
 *
 * The numbers are fetched from the ledger before the model sees the question,
 * and the answer is checked back against them. A rupee figure we did not
 * supply means it did arithmetic, and we show the ledger instead.
 */
function Ask({ invoiceId }: { invoiceId: string }) {
  const [question, setQuestion] = useState(`Why did we receive this much for ${invoiceId}?`);
  const [answer, setAnswer] = useState<AskAnswer | null>(null);
  const [asking, setAsking] = useState(false);
  const [failed, setFailed] = useState(false);
  const [showFacts, setShowFacts] = useState(false);

  async function send() {
    if (!question.trim() || asking) return;
    setAsking(true);
    setFailed(false);
    try {
      setAnswer(await api.ask(question));
    } catch {
      setFailed(true);
    } finally {
      setAsking(false);
    }
  }

  return (
    <Card>
      <CardHead
        title="Ask about this invoice"
        note="Answered from the ledger. Every figure is one we hold, not one it worked out."
      />
      <div className="px-5 py-4">
        <div className="flex flex-wrap gap-2">
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder={`Why did we receive this much for ${invoiceId}?`}
            className="min-w-0 flex-1 rounded-md border border-ink-200 bg-ink-0 px-3 py-1.5 text-sm text-ink-1200 placeholder:text-ink-500 focus:border-brand-500"
          />
          <Button onClick={send} disabled={asking || !question.trim()}>
            {asking ? "Asking…" : "Ask"}
          </Button>
        </div>

        {failed && (
          <p className="mt-3 text-sm text-bad-700">
            Could not reach the service. Try again once it is back.
          </p>
        )}

        {answer && (
          <div className="mt-4">
            <p className="max-w-3xl text-sm leading-relaxed text-ink-1200">{answer.answer}</p>

            <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-ink-700">
              {answer.rejected?.startsWith("INVENTED_AMOUNTS") ? (
                <span className="rounded-md bg-warn-50 px-2 py-0.5 font-medium text-warn-700 ring-1 ring-inset ring-warn-600/20">
                  It used a figure we did not give it, so we are showing the ledger instead
                </span>
              ) : answer.used_model ? (
                <span>Written by the assistant from {answer.facts.length} facts we hold</span>
              ) : (
                <span>Straight from the ledger</span>
              )}
              {answer.used_model && <span>· {answer.cost.display}</span>}
              {answer.facts.length > 0 && (
                <button
                  type="button"
                  onClick={() => setShowFacts((v) => !v)}
                  className="font-medium text-brand-600 hover:text-brand-700"
                >
                  {showFacts ? "Hide what it was given" : "Show what it was given"}
                </button>
              )}
            </div>

            {showFacts && (
              <dl className="mt-3 divide-y divide-ink-100 rounded-md bg-ink-50 px-4 ring-1 ring-inset ring-ink-200">
                {answer.facts.map((fact) => {
                  const [label, ...rest] = fact.split(": ");
                  return (
                    <div key={fact} className="flex flex-wrap justify-between gap-3 py-2 text-xs">
                      <dt className="text-ink-700">{label}</dt>
                      <dd className="tnum font-medium text-ink-1200">{rest.join(": ")}</dd>
                    </div>
                  );
                })}
              </dl>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}
