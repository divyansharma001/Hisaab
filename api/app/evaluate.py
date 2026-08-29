"""Comparing what the pipeline decided against the answer key. Plan section 8.

Match rate on its own is a bad metric: match everything to anything and you
hit 100%. The two failure types also cost wildly different amounts.

- **Missing a match** is annoying. A human spends thirty seconds on it.
- **Making a wrong match** is expensive. The books are wrong and nobody knows.

So they are measured separately, and the headline number is the one that must
be zero.
"""

from dataclasses import dataclass, field

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings
from app.dataset import Outcome, Split
from app.money import fmt
from app.pipeline import RunResult

# Outcomes a human never has to look at.
STRAIGHT_THROUGH = {Outcome.AUTO}

# The truth marks these as "correct behaviour is to refuse", so holding the
# record back is right. Auto-approving one is a false approval.
MUST_NOT_AUTO = {Outcome.EXCEPTION, Outcome.REVIEW, Outcome.AMBIGUOUS}


@dataclass
class Truth:
    invoice_id: str
    scenario: str
    expected_outcome: Outcome
    expected_txn_ids: list[str]
    expected_reason_code: str | None
    note: str


@dataclass
class Judgement:
    invoice_id: str
    scenario: str
    amount_paise: int
    expected: Outcome
    actual: Outcome
    expected_txns: list[str]
    actual_txns: list[str]
    expected_reason: str | None
    actual_reason: str | None

    @property
    def txns_right(self) -> bool:
        return set(self.actual_txns) == set(self.expected_txns)

    @property
    def outcome_right(self) -> bool:
        return self.actual == self.expected

    @property
    def correct(self) -> bool:
        """Did we do the right thing with this record?

        Which payments we name only counts as a claim when we auto-approved.
        On a record we held back, the transaction we list is the closest
        candidate we considered - evidence for the human, not an assertion
        that it matches. Grading that as wrong made every correctly-refused
        record look like a failure.
        """
        if not self.outcome_right:
            return False
        return self.txns_right if self.actual is Outcome.AUTO else True

    @property
    def false_auto_approval(self) -> bool:
        """The number that must be zero.

        Two ways to get one: automating a record that should have been held
        back, or automating the right record against the wrong payment.
        """
        if self.actual is not Outcome.AUTO:
            return False
        if self.expected in MUST_NOT_AUTO:
            return True
        return not self.txns_right

    @property
    def missed_exception(self) -> bool:
        """Should have been flagged for a human, and was not."""
        return self.expected in MUST_NOT_AUTO and self.actual is Outcome.AUTO

    @property
    def reason_right(self) -> bool:
        return self.actual_reason == self.expected_reason


@dataclass
class Metrics:
    total: int = 0
    judgements: list[Judgement] = field(default_factory=list)

    @property
    def auto(self) -> list[Judgement]:
        return [j for j in self.judgements if j.actual is Outcome.AUTO]

    @property
    def false_auto_approvals(self) -> list[Judgement]:
        return [j for j in self.judgements if j.false_auto_approval]

    @property
    def missed_exceptions(self) -> list[Judgement]:
        return [j for j in self.judgements if j.missed_exception]

    @property
    def straight_through_rate(self) -> float:
        return len(self.auto) / self.total * 100 if self.total else 0.0

    @property
    def auto_precision(self) -> float:
        """Of everything auto-approved, what share was actually right."""
        if not self.auto:
            return 100.0
        correct = sum(1 for j in self.auto if not j.false_auto_approval)
        return correct / len(self.auto) * 100

    @property
    def outcome_accuracy(self) -> float:
        right = sum(1 for j in self.judgements if j.correct)
        return right / self.total * 100 if self.total else 0.0

    @property
    def wrong_transaction_approvals(self) -> list[Judgement]:
        """Auto-approved the right record against the wrong payment.

        Separated from the guardrail failures because this is the kind of
        mistake the scoring weights can actually cause, so it is the kind the
        grid search can be constrained on.
        """
        return [
            j
            for j in self.judgements
            if j.actual is Outcome.AUTO
            and j.expected is Outcome.AUTO
            and not j.txns_right
        ]

    @property
    def reason_accuracy(self) -> float:
        right = sum(1 for j in self.judgements if j.reason_right)
        return right / self.total * 100 if self.total else 0.0

    @property
    def value_total(self) -> int:
        return sum(j.amount_paise for j in self.judgements)

    @property
    def value_auto(self) -> int:
        return sum(j.amount_paise for j in self.auto)

    @property
    def value_held(self) -> int:
        """Money we cannot currently account for. Plan section 19.2."""
        return sum(
            j.amount_paise for j in self.judgements if j.actual is not Outcome.AUTO
        )

    @property
    def straight_through_by_value(self) -> float:
        return self.value_auto / self.value_total * 100 if self.value_total else 0.0

    def by_scenario(self) -> dict[str, tuple[int, int, int]]:
        """scenario -> (right, total, false approvals)"""
        out: dict[str, tuple[int, int, int]] = {}
        for j in self.judgements:
            right, total, bad = out.get(j.scenario, (0, 0, 0))
            out[j.scenario] = (
                right + (1 if j.correct else 0),
                total + 1,
                bad + (1 if j.false_auto_approval else 0),
            )
        return out


def load_truth(split: Split, database_url: str | None = None) -> dict[str, Truth]:
    url = database_url or get_settings().database_url
    with psycopg.connect(url, row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT * FROM ground_truth WHERE split = %s", (split.value,)
        ).fetchall()

    return {
        r["invoice_id"]: Truth(
            invoice_id=r["invoice_id"],
            scenario=r["scenario"],
            expected_outcome=Outcome(r["expected_outcome"]),
            expected_txn_ids=list(r["expected_txn_ids"]),
            expected_reason_code=r["expected_reason_code"],
            note=r["note"],
        )
        for r in rows
    }


def evaluate(result: RunResult, truth: dict[str, Truth]) -> Metrics:
    metrics = Metrics(total=len(result.decisions))

    for decision in result.decisions:
        expected = truth[decision.invoice_id]
        metrics.judgements.append(
            Judgement(
                invoice_id=decision.invoice_id,
                scenario=decision.scenario,
                amount_paise=decision.amount_paise,
                expected=expected.expected_outcome,
                actual=decision.outcome,
                expected_txns=expected.expected_txn_ids,
                actual_txns=decision.txn_ids,
                expected_reason=expected.expected_reason_code,
                actual_reason=decision.reason_code.value if decision.reason_code else None,
            )
        )

    return metrics


# --- printing --------------------------------------------------------------


def print_report(result: RunResult, metrics: Metrics, label: str = "") -> None:
    bad = len(metrics.false_auto_approvals)

    print(f"\n{'=' * 66}")
    print(f"  {label or result.split.value.upper()}  -  {metrics.total} records in {result.seconds:.2f}s")
    print("=" * 66)

    print("\nHeadline")
    print(f"  False auto-approvals      {bad}" + ("   <- must be 0" if bad else "   OK"))
    print(f"  Auto-approval precision   {metrics.auto_precision:.1f}%")
    print(f"  Straight-through, count   {metrics.straight_through_rate:.1f}%  ({len(metrics.auto)}/{metrics.total})")
    print(f"  Straight-through, value   {metrics.straight_through_by_value:.1f}%")
    print(f"  Missed exceptions         {len(metrics.missed_exceptions)}")
    print(f"  Outcome accuracy          {metrics.outcome_accuracy:.1f}%")
    print(f"  Reason-code accuracy      {metrics.reason_accuracy:.1f}%")
    print(f"  Value held for a human    {fmt(metrics.value_held)}")

    print("\nBy scenario")
    print(f"  {'scenario':<24} {'right':>10}  {'false approvals':>16}")
    for scenario, (right, total, false) in sorted(
        metrics.by_scenario().items(), key=lambda kv: (kv[1][2] == 0, kv[1][0] / kv[1][1])
    ):
        flag = "  <- WRONG APPROVALS" if false else ("  <- weak" if right < total else "")
        print(f"  {scenario:<24} {right:>4}/{total:<5} {false:>16}{flag}")

    if metrics.false_auto_approvals:
        print("\nWrong auto-approvals")
        for j in metrics.false_auto_approvals:
            print(
                f"  {j.invoice_id}  {j.scenario:<22} expected {j.expected.value} "
                f"{j.expected_txns} but auto-approved {j.actual_txns}"
            )
