"""Testing the adjudicator on its own. Plan section 8.5.

The LLM is the non-deterministic piece, so it gets its own tests rather than
being judged only through the pipeline's aggregate numbers.

Three questions, and the second is the one almost nobody measures:

1. **Accuracy** - on the cases it actually sees, does it pick the right payment?
2. **Self-consistency** - asked the same thing three times at temperature 0.7,
   does it give the same answer? At temperature 0 the replies are near-identical
   by construction and the test proves nothing. Raising it asks the real
   question: is the model stable because the *prompt* is good, or only because
   we pinned the temperature? Section 18, bug 9.
3. **Refusal quality** - handed a case with no right answer, does it say so, or
   invent confidence? A model that never abstains is dangerous.

The consistency run must bypass the result cache, or it is served the same
stored answer three times and passes trivially. Section 15.4.
"""

from dataclasses import dataclass, field

from app import guardrails
from app.adjudicator import Adjudicator
from app.blocking import block
from app.dataset import Outcome, Split
from app.evaluate import Truth
from app.intake import Batch, NormInvoice
from app.memory import Memory, case_tags
from app.pipeline import Decision, RunResult
from app.scoring import rank

# The temperature the consistency check runs at. Not zero, on purpose.
CONSISTENCY_TEMPERATURE = 0.7
CONSISTENCY_RUNS = 3


@dataclass
class Case:
    invoice: NormInvoice
    ranking: object
    tags: set[str]
    expected_txns: list[str]
    expected_outcome: Outcome
    scenario: str


@dataclass
class LLMReport:
    asked: int = 0
    correct: int = 0
    abstained: int = 0
    wrong: int = 0
    rejected: list[str] = field(default_factory=list)

    consistency_cases: int = 0
    consistency_stable: int = 0
    confidence_spread: list[float] = field(default_factory=list)

    refusal_cases: int = 0
    refusal_correct: int = 0

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.asked * 100 if self.asked else 0.0

    @property
    def consistency(self) -> float:
        if not self.consistency_cases:
            return 0.0
        return self.consistency_stable / self.consistency_cases * 100

    @property
    def refusal_rate(self) -> float:
        if not self.refusal_cases:
            return 0.0
        return self.refusal_correct / self.refusal_cases * 100


def cases_the_adjudicator_sees(
    batch: Batch, result: RunResult, truth: dict[str, Truth], memory: Memory
) -> list[Case]:
    """Exactly the records the live pipeline would ask about.

    Not "everything ambiguous" - the pipeline only asks when the score or
    margin bar is the sole thing holding a record, so testing a wider set
    would be measuring a system we do not run.
    """
    by_id = {d.invoice_id: d for d in result.decisions}
    cases: list[Case] = []

    for invoice in batch.invoices:
        ranking = result.rankings.get(invoice.id)
        decision = by_id.get(invoice.id)
        if ranking is None or ranking.best is None or decision is None:
            continue
        if guardrails.route(ranking.score, ranking.margin) != guardrails.ADJUDICATE:
            continue
        if set(decision.rules_failed) - guardrails.ENDORSABLE_RULES:
            continue

        expected = truth[invoice.id]
        cases.append(
            Case(
                invoice=invoice,
                ranking=ranking,
                tags=case_tags(decision),
                expected_txns=expected.expected_txn_ids,
                expected_outcome=expected.expected_outcome,
                scenario=expected.scenario,
            )
        )
    return cases


def refusal_cases(
    batch: Batch, result: RunResult, truth: dict[str, Truth], limit: int = 4
) -> list[Case]:
    """Records where the right answer is 'I cannot tell'.

    Invoices nobody paid, and pairs of identical invoices fighting over one
    payment. There is no correct choice, so choosing is the failure.
    """
    by_id = {d.invoice_id: d for d in result.decisions}
    wanted = {"no_payment", "identical_invoices"}
    cases: list[Case] = []

    for invoice in batch.invoices:
        expected = truth[invoice.id]
        if expected.scenario not in wanted or len(cases) >= limit:
            continue
        ranking = result.rankings.get(invoice.id)
        decision = by_id.get(invoice.id)
        if ranking is None or ranking.best is None or decision is None:
            continue
        cases.append(
            Case(
                invoice=invoice,
                ranking=ranking,
                tags=case_tags(decision),
                expected_txns=[],
                expected_outcome=expected.expected_outcome,
                scenario=expected.scenario,
            )
        )
    return cases


def run_llm_eval(
    batch: Batch,
    result: RunResult,
    truth: dict[str, Truth],
    memory: Memory,
    adjudicator: Adjudicator | None = None,
) -> LLMReport:
    adjudicator = adjudicator or Adjudicator(memory=memory, budget=200)
    report = LLMReport()

    if not adjudicator.available:
        return report

    # --- 1. accuracy on the cases it actually sees -----------------------
    live = cases_the_adjudicator_sees(batch, result, truth, memory)
    for case in live:
        verdict = adjudicator.adjudicate(case.invoice, case.ranking, tags=case.tags)
        report.asked += 1
        report.input_tokens += verdict.input_tokens
        report.output_tokens += verdict.output_tokens

        if verdict.rejected:
            report.rejected.append(verdict.rejected)
        elif verdict.chosen_id is None:
            report.abstained += 1
        elif verdict.chosen_id in case.expected_txns:
            report.correct += 1
        else:
            report.wrong += 1

    # --- 2. self-consistency, cache bypassed, temperature raised ---------
    for case in live:
        answers, confidences = set(), []
        for _ in range(CONSISTENCY_RUNS):
            verdict = adjudicator.adjudicate(
                case.invoice,
                case.ranking,
                use_cache=False,
                tags=case.tags,
                temperature=CONSISTENCY_TEMPERATURE,
            )
            answers.add(verdict.chosen_id)
            confidences.append(verdict.confidence)
            report.input_tokens += verdict.input_tokens
            report.output_tokens += verdict.output_tokens

        report.consistency_cases += 1
        if len(answers) == 1:
            report.consistency_stable += 1
        if confidences:
            report.confidence_spread.append(max(confidences) - min(confidences))

    # --- 3. refusal quality ---------------------------------------------
    for case in refusal_cases(batch, result, truth):
        verdict = adjudicator.adjudicate(case.invoice, case.ranking, tags=case.tags)
        report.refusal_cases += 1
        report.input_tokens += verdict.input_tokens
        report.output_tokens += verdict.output_tokens
        if verdict.chosen_id is None or verdict.confidence < guardrails.ENDORSEMENT_FLOOR:
            report.refusal_correct += 1

    return report


def print_llm_report(report: LLMReport) -> None:
    if not report.asked and not report.refusal_cases:
        print("\nAdjudicator tests skipped - no API key configured.")
        return

    print("\nThe adjudicator, tested on its own")
    print(f"  Cases it was asked about   {report.asked}")
    print(f"  Picked the right payment   {report.correct}/{report.asked}"
          f"  ({report.accuracy:.0f}%)")
    print(f"  Abstained                  {report.abstained}")
    print(f"  Picked the wrong payment   {report.wrong}")
    if report.rejected:
        print(f"  Answers we threw away      {len(report.rejected)}  {set(report.rejected)}")

    spread = max(report.confidence_spread, default=0.0)
    print(f"\n  Same question {CONSISTENCY_RUNS}x at temperature {CONSISTENCY_TEMPERATURE}"
          f"   (cache bypassed)")
    print(f"  Same answer every time     {report.consistency_stable}/{report.consistency_cases}"
          f"  ({report.consistency:.0f}%)")
    print(f"  Widest confidence swing    {spread:.2f}")

    print(f"\n  Cases with no right answer {report.refusal_cases}")
    print(f"  Correctly refused to pick  {report.refusal_correct}/{report.refusal_cases}"
          f"  ({report.refusal_rate:.0f}%)")
