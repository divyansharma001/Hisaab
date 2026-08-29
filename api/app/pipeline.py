"""The batch pipeline. Plan section 14.6.

Read this file top to bottom and you know exactly what the system does. That
readability is the deliverable, not a side effect of skipping a framework.

**Phase 2 scope.** Deterministic only. No LLM, and none of the decision
guardrails from section 7 beyond a bare score threshold. Global assignment,
the value ceiling, the duplicate rule and the rest arrive in Phase 3, and the
straight-through rate is expected to *drop* when they do. That trade is the
whole point of the project, so the Phase 2 number is recorded as the baseline
to measure it against.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.blocking import Pass, block
from app.dataset import Outcome, Reason, Split
from app.intake import Batch, NormInvoice, load_batch
from app.scoring import TUNED_WEIGHTS, Ranking, Scored, rank

# Phase 2 has one rule. The full table lands in Phase 3.
AUTO_THRESHOLD = 0.90

FAST_PATH = "fast_path"
SCORER = "scorer"
NO_CANDIDATE = "none"


@dataclass
class Decision:
    invoice_id: str
    outcome: Outcome
    txn_ids: list[str] = field(default_factory=list)
    score: float = 0.0
    margin: float = 0.0
    margin_basis: str = "sole_candidate"
    reason_code: Reason | None = None
    reason_text: str = ""
    decided_by: str = NO_CANDIDATE
    scenario: str = ""
    amount_paise: int = 0


@dataclass
class RunResult:
    split: Split
    run_id: str
    decisions: list[Decision] = field(default_factory=list)
    rankings: dict[str, Ranking] = field(default_factory=dict)
    seconds: float = 0.0

    def by_outcome(self) -> dict[Outcome, int]:
        counts: dict[Outcome, int] = {}
        for d in self.decisions:
            counts[d.outcome] = counts.get(d.outcome, 0) + 1
        return counts


def reason_for_match(best: Scored) -> Reason:
    """Why we think this payment settles this bill.

    The reason code is what a human acts on, so section 19.7 scores us on
    getting it right - not only on reaching the right verdict.
    """
    if best.amount.pass_used is Pass.COMBINED:
        return Reason.COMBINED_PAYMENT
    if best.amount.pass_used is Pass.PARTIAL:
        return Reason.PARTIAL_PAYMENT

    if best.signals.reference == 1.0:
        return Reason.MATCHED_REFERENCE

    formula = best.amount.formula
    if formula and formula.startswith("MDR_GST"):
        return Reason.MDR_GST
    if formula == "TDS_2PCT":
        return Reason.TDS_2PCT
    if formula == "TDS_10PCT":
        return Reason.TDS_10PCT

    # An exact amount under a name that needed fuzzy work to recognise.
    if best.signals.name is not None and best.signals.name < 1.0:
        return Reason.MATCHED_ALIAS
    return Reason.MATCHED_NAME_AMOUNT


def reason_for_refusal(ranking: Ranking) -> tuple[Reason, str]:
    """Why we will not decide this one. Never 'low confidence'."""
    best = ranking.best
    if best is None:
        return Reason.NO_PAYMENT_FOUND, "No payment in this batch could belong to this invoice"

    if best.signals.date == 0.0:
        days = (best.txn.value_date - ranking.invoice.due_date).days
        wording = f"{days} days after" if days > 0 else f"{-days} days before"
        return Reason.DATE_OUT_OF_WINDOW, f"Best candidate paid {wording} the due date"

    if best.signals.amount is not None and best.signals.amount < 0.9:
        return Reason.AMOUNT_GAP_UNEXPLAINED, f"Best candidate: {best.amount.basis}"

    if ranking.margin < 0.15:
        return (
            Reason.AMBIGUOUS_CANDIDATES,
            f"Top two candidates are {ranking.margin:.2f} apart, too close to call",
        )

    weakest = min(
        ranking.best.signals.available().items(), key=lambda kv: kv[1], default=("score", 0.0)
    )
    return (
        Reason.BELOW_THRESHOLD,
        f"Scored {ranking.score:.2f} against a bar of {AUTO_THRESHOLD}; "
        f"weakest signal was {weakest[0]} at {weakest[1]:.2f}",
    )


def find_by_reference(invoice: NormInvoice, batch: Batch):
    """The fast path. Plan section 5, box 3.

    A reference match is strong evidence, not a free pass: the result still
    goes through scoring and the guardrails like everything else.
    """
    hits = [t for t in batch.transactions if invoice.invoice_no in t.refs]
    return hits[0] if len(hits) == 1 else None


def decide(ranking: Ranking, decided_by: str) -> Decision:
    """Phase 2's decision rule: one threshold, nothing else."""
    invoice = ranking.invoice
    base = dict(
        invoice_id=invoice.id,
        scenario=invoice.scenario,
        amount_paise=invoice.amount_paise,
        score=ranking.score,
        margin=ranking.margin,
        margin_basis=ranking.margin_basis,
        decided_by=decided_by,
    )

    best = ranking.best
    if best is None:
        code, text = reason_for_refusal(ranking)
        return Decision(outcome=Outcome.EXCEPTION, reason_code=code, reason_text=text, **base)

    if ranking.score >= AUTO_THRESHOLD:
        # A partial payment is only settled by all of its instalments, and the
        # scorer already worked out which ones. Filtering by individual score
        # would drop a late instalment and claim the bill was settled by the
        # first one alone.
        txn_ids = [best.id, *best.amount.members] if best.amount.pass_used is Pass.PARTIAL else [best.id]

        return Decision(
            outcome=Outcome.AUTO,
            txn_ids=txn_ids,
            reason_code=reason_for_match(best),
            reason_text=best.amount.basis,
            **base,
        )

    code, text = reason_for_refusal(ranking)
    return Decision(
        outcome=Outcome.EXCEPTION,
        txn_ids=[best.id],
        reason_code=code,
        reason_text=text,
        **base,
    )


def process_batch(batch: Batch, weights: dict[str, float] | None = None) -> RunResult:
    weights = weights or TUNED_WEIGHTS
    started = datetime.now(UTC)
    run_id = f"run-{started:%Y%m%d-%H%M%S}"
    result = RunResult(split=batch.split, run_id=run_id)

    for invoice in batch.invoices:
        candidates = block(invoice, batch)
        ranking = rank(invoice, candidates, batch, weights)
        result.rankings[invoice.id] = ranking

        referenced = find_by_reference(invoice, batch)
        decided_by = FAST_PATH if referenced else (SCORER if candidates else NO_CANDIDATE)

        result.decisions.append(decide(ranking, decided_by))

    result.seconds = (datetime.now(UTC) - started).total_seconds()
    return result


def run(split: Split, weights: dict[str, float] | None = None) -> RunResult:
    return process_batch(load_batch(split), weights)
