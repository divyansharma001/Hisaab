"""The threshold curve. Plan section 19.6.

The plan predicted the bar was the safety mechanism - drop it to 0.80 and two
wrong approvals appear. **Measured, that is not what happens.** With the nine
rules in place there is not one wrong approval at any bar from 0.30 to 0.98.
Section 18, bug 14.

So the curve is drawn twice, and the gap between the lines is the answer:

    bar    with the rules      score bar alone
    0.90   69.4%   0 wrong     82.4%  11 wrong
    0.80   72.9%   0 wrong     90.6%  15 wrong
    0.70   72.9%   0 wrong     94.1%  18 wrong

Reading only the top line, the bar looks free and 0.90 looks arbitrary. The
second line is what the bar is hiding: the score never protected anything. The
rules did, and they cost about thirteen points of automation to do it.

Measured **without the assistant**. The model is not perfectly repeatable, so
including it would mean the curve moved between viewings and the comparison
between bars would be measuring noise as well as the bar. This is the
deterministic core alone, which is the part the bar actually governs.
"""

from __future__ import annotations

from dataclasses import dataclass

from app import guardrails
from app.dataset import Split
from app.evaluate import evaluate, load_truth
from app.intake import load_batch
from app.memory import build_from_split
from app.pipeline import process_batch

# Where the interesting behaviour is. Below 0.80 the wrong approvals pile up;
# above 0.95 almost nothing clears on its own.
BARS = (0.80, 0.85, 0.90, 0.95, 0.98)


@dataclass
class Point:
    bar: float
    closed_on_their_own: float
    wrong: int
    left_for_a_person: int
    is_current: bool
    with_rules: bool = True


def sweep(
    bars: tuple[float, ...] = BARS,
    split: Split = Split.HELDOUT,
    with_rules: bool = True,
) -> list[Point]:
    """Run the batch once per bar and record what each one costs.

    `AUTO_SCORE` is a module constant read at call time, so the only way to
    sweep it is to rebind it around each run. The original is restored in a
    `finally`: leaving a test threshold behind would silently change every
    later decision in the process, which is the kind of bug that looks like a
    model regression for a week.
    """
    batch = load_batch(split)
    truth = load_truth(split)
    memory = build_from_split()
    original = guardrails.AUTO_SCORE

    points: list[Point] = []
    try:
        for bar in bars:
            guardrails.AUTO_SCORE = bar
            result = process_batch(batch, memory=memory, use_guardrails=with_rules)
            metrics = evaluate(result, truth)
            points.append(
                Point(
                    bar=bar,
                    closed_on_their_own=round(metrics.straight_through_rate, 1),
                    wrong=len(metrics.false_auto_approvals),
                    left_for_a_person=metrics.total
                    - round(metrics.straight_through_rate * metrics.total / 100),
                    is_current=abs(bar - original) < 1e-9,
                    with_rules=with_rules,
                )
            )
    finally:
        guardrails.AUTO_SCORE = original

    return points


def both_curves(
    bars: tuple[float, ...] = BARS, split: Split = Split.HELDOUT
) -> dict[str, list[Point]]:
    """The curve with the rules, and the same sweep without them."""
    return {
        "with_rules": sweep(bars, split, with_rules=True),
        "score_only": sweep(bars, split, with_rules=False),
    }


def print_curves(curves: dict[str, list[Point]]) -> None:
    guarded = {p.bar: p for p in curves["with_rules"]}
    bare = {p.bar: p for p in curves["score_only"]}

    print("\nWhat the bar actually does")
    print(f"  {'bar':>5}  {'with the rules':>22}  {'score bar alone':>22}")
    for bar in sorted(guarded):
        g, b = guarded[bar], bare[bar]
        mark = "  <- we use this" if g.is_current else ""
        print(
            f"  {bar:>5.2f}  {g.closed_on_their_own:>14.1f}% {g.wrong:>3} wrong"
            f"  {b.closed_on_their_own:>14.1f}% {b.wrong:>3} wrong{mark}"
        )
    print("\n  The bar is not the safety mechanism. The rules are.")
