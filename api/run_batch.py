"""Run one reconciliation batch and print what it decided.

    docker compose exec api python run_batch.py
    docker compose exec api python run_batch.py --split tuning

Use eval.py to score the result against the answer key.
"""

import argparse
import sys

from app.dataset import Split
from app.money import fmt
from app.pipeline import run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--split", default=Split.HELDOUT.value, choices=[s.value for s in Split]
    )
    parser.add_argument("--show", type=int, default=12, help="rows to print")
    args = parser.parse_args()

    result = run(Split(args.split))

    print(f"{len(result.decisions)} records in {result.seconds:.2f}s")
    for outcome, count in sorted(result.by_outcome().items()):
        print(f"  {outcome.value:<10} {count}")

    held = [d for d in result.decisions if d.outcome.value != "AUTO"]
    print(f"\nNot auto-approved ({len(held)}), showing {min(args.show, len(held))}:")
    print(f"  {'invoice':<11} {'amount':>14}  {'reason':<24} why")
    for d in held[: args.show]:
        code = d.reason_code.value if d.reason_code else "-"
        print(f"  {d.invoice_id:<11} {fmt(d.amount_paise):>14}  {code:<24} {d.reason_text}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
