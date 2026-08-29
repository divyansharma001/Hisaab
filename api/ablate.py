"""What each layer actually bought us. Plan section 19.1.

    docker compose exec api python ablate.py

Answers the question judges really have - *did the AI do anything, or is it
decoration?* - with a measurement rather than an assertion. Every row is a real
run against the same held-out records.
"""

import sys

from app.adjudicator import Adjudicator
from app.dataset import Split
from app.evaluate import evaluate, load_truth
from app.memory import build_from_split
from app.money import fmt
from app.pipeline import run

# Anthropic list price for claude-opus-5, in paise per token.
# Input $5 / MTok, output $25 / MTok, at roughly Rs 88 to the dollar.
RUPEES_PER_USD = 88
INPUT_PAISE_PER_TOKEN = 5 / 1_000_000 * RUPEES_PER_USD * 100
OUTPUT_PAISE_PER_TOKEN = 25 / 1_000_000 * RUPEES_PER_USD * 100

CONFIGURATIONS = [
    ("Scoring only, no guardrails", dict(use_guardrails=False, use_memory=False, use_llm=False)),
    ("+ guardrails", dict(use_guardrails=True, use_memory=False, use_llm=False)),
    ("+ memory", dict(use_guardrails=True, use_memory=True, use_llm=False)),
    ("+ LLM adjudicator", dict(use_guardrails=True, use_memory=True, use_llm=True)),
]


def cost_paise(result) -> int:
    return round(
        result.input_tokens * INPUT_PAISE_PER_TOKEN
        + result.output_tokens * OUTPUT_PAISE_PER_TOKEN
    )


def main() -> int:
    split = Split.HELDOUT
    truth = load_truth(split)

    print(f"\nAblation on the {split.value} set, {len(truth)} records")
    print("Every row is a real run. Same records, same seed, one layer at a time.\n")

    header = f"  {'configuration':<30} {'STP':>7} {'accuracy':>9} {'false approvals':>16} {'LLM calls':>10} {'cost':>10}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    rows = []
    for label, flags in CONFIGURATIONS:
        result = run(split, **flags)
        metrics = evaluate(result, truth)
        rows.append((label, metrics, result))

        flag = "  <- UNSAFE" if metrics.false_auto_approvals else ""
        print(
            f"  {label:<30} {metrics.straight_through_rate:>6.1f}% "
            f"{metrics.outcome_accuracy:>8.1f}% {len(metrics.false_auto_approvals):>16} "
            f"{result.llm_calls:>10} {fmt(cost_paise(result)):>10}{flag}"
        )

    print("\nWhat each layer did")
    for i in range(1, len(rows)):
        label, metrics, _ = rows[i]
        _, before, _ = rows[i - 1]
        print(
            f"  {label:<30} "
            f"straight-through {metrics.straight_through_rate - before.straight_through_rate:+5.1f} points, "
            f"accuracy {metrics.outcome_accuracy - before.outcome_accuracy:+5.1f} points, "
            f"wrong approvals {len(metrics.false_auto_approvals) - len(before.false_auto_approvals):+d}"
        )

    _, final_metrics, final_result = rows[-1]
    adjudicator = Adjudicator(memory=build_from_split())
    if not adjudicator.available:
        print(
            "\n  Note: no Anthropic API key is set, so the adjudicator row ran with the\n"
            "  model unavailable. Every record it would have asked about fell back to a\n"
            "  human, which is the safe failure. Set ANTHROPIC_API_KEY to measure it."
        )

    per_thousand = cost_paise(final_result) / max(len(truth), 1) * 1000
    print(
        f"\n  {len(final_result.decisions)} records in {final_result.seconds:.2f}s "
        f"({len(final_result.decisions) / max(final_result.seconds, 0.001):.0f}/sec), "
        f"{final_result.llm_calls} LLM calls, {fmt(round(per_thousand))} per 1,000 records"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
