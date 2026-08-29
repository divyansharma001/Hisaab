"""Grid-searching the scoring weights. Plan section 6.9.

Do not guess the weights, and do not defend them with intuition. Search them
against ground truth on the **tuning set only** - never the held-out set, or we
are fitting the records we report on.

*"We grid-searched the weights under a zero-false-approval constraint"* is a
far stronger answer than *"these felt about right."*
"""

from dataclasses import dataclass
from itertools import product

from app.dataset import Split
from app.evaluate import Metrics, evaluate, load_truth
from app.intake import load_batch
from app.memory import build_from_split
from app.pipeline import process_batch
from app.scoring import BASE_WEIGHTS

# Around the plan's starting point, coarse enough to finish in seconds.
GRID: dict[str, list[float]] = {
    "reference": [0.40, 0.45, 0.50, 0.55, 0.60],
    "amount": [0.20, 0.25, 0.30, 0.35],
    "name": [0.10, 0.15, 0.20],
    "date": [0.05, 0.10, 0.15],
}


@dataclass
class Trial:
    weights: dict[str, float]
    metrics: Metrics

    @property
    def wrong_transaction_approvals(self) -> int:
        return len(self.metrics.wrong_transaction_approvals)

    @property
    def straight_through(self) -> float:
        return self.metrics.straight_through_rate

    @property
    def accuracy(self) -> float:
        return self.metrics.outcome_accuracy


def normalised(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    return {key: value / total for key, value in weights.items()}


def search(split: Split = Split.TUNING, grid: dict[str, list[float]] | None = None) -> list[Trial]:
    grid = grid or GRID
    batch = load_batch(split)
    truth = load_truth(split)

    # The same memory a real run gets. Without it the new-counterparty rule
    # holds every record, every weighting scores identically, and the search
    # returns a flat line that looks like a result.
    memory = build_from_split()

    seen: set[tuple] = set()
    trials: list[Trial] = []

    for combo in product(*grid.values()):
        weights = normalised(dict(zip(grid.keys(), combo)))
        key = tuple(round(weights[k], 4) for k in sorted(weights))
        if key in seen:
            continue
        seen.add(key)

        result = process_batch(batch, weights, memory)
        trials.append(Trial(weights=weights, metrics=evaluate(result, truth)))

    return trials


def best(trials: list[Trial]) -> Trial:
    """Highest accuracy among combinations that never matched the wrong payment.

    The zero-wrong-approval rule is a **hard constraint, not part of the
    objective**. A setting that automates more records by matching one of them
    to the wrong payment is not a better setting, however good the average
    looks.

    Note this constrains only the mistakes weights can cause. Auto-approving a
    duplicate, or a payment two invoices both want, is a missing guardrail
    rather than a bad weight, and no weighting fixes it. Those go to zero in
    Phase 3, and the search is re-run with the full constraint then.
    """
    clean = [t for t in trials if t.wrong_transaction_approvals == 0]
    if not clean:
        raise ValueError("every weighting matched at least one wrong payment")

    top = max((t.accuracy, t.straight_through) for t in clean)
    tied = [t for t in clean if (t.accuracy, t.straight_through) == top]

    # Several settings usually tie, which is itself worth knowing: the result
    # is flat in that region. Among equals, take the one nearest the plan's
    # starting weights rather than whichever the grid happened to reach first.
    # A tie broken by luck is a weighting fitted to 45 records.
    return min(tied, key=_distance_from_base)


def _distance_from_base(trial: Trial) -> float:
    return sum(abs(trial.weights[k] - v) for k, v in BASE_WEIGHTS.items())


def sensitivity(trials: list[Trial]) -> tuple[float, float]:
    """How much the result actually moves across the grid.

    Reported alongside the chosen weights, because a result that swings wildly
    with the weights is a result to distrust.
    """
    clean = [t for t in trials if t.wrong_transaction_approvals == 0]
    scores = [t.accuracy for t in clean]
    return (min(scores), max(scores)) if scores else (0.0, 0.0)


def print_search(trials: list[Trial], top: int = 8) -> None:
    winner = best(trials)
    low, high = sensitivity(trials)
    clean = [t for t in trials if t.wrong_transaction_approvals == 0]

    print(f"\n{len(trials)} weightings tried, {len(clean)} matched no wrong payments")
    print(f"Accuracy across those: {low:.1f}% to {high:.1f}%  (spread {high - low:.1f} points)")

    print(f"\n  {'reference':>9} {'amount':>7} {'name':>6} {'date':>6} {'accuracy':>9} {'STP':>7}")
    ranked = sorted(clean, key=lambda t: (-t.accuracy, -t.straight_through))
    for t in ranked[:top]:
        w = t.weights
        print(
            f"  {w['reference']:>9.3f} {w['amount']:>7.3f} {w['name']:>6.3f} "
            f"{w['date']:>6.3f} {t.accuracy:>8.1f}% {t.straight_through:>6.1f}%"
        )

    top = max((t.accuracy, t.straight_through) for t in clean)
    tied = sum(1 for t in clean if (t.accuracy, t.straight_through) == top)
    print(f"\nChosen  ({tied} weightings tie at the top, so this is the one closest to the plan's)")
    for key, value in winner.weights.items():
        base = BASE_WEIGHTS[key]
        move = "same" if abs(value - base) < 0.005 else f"was {base}"
        print(f"  {key:<10} {value:.3f}   ({move})")
