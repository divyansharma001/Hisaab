"""Run the pipeline and score it against the answer key.

    docker compose exec api python eval.py
    docker compose exec api python eval.py --split tuning

One command, or we stop running it by hour six. Plan section 8.7.
"""

import argparse
import sys

from app.dataset import Split
from app.evaluate import evaluate, load_truth, print_report
from app.eval_llm import print_llm_report, run_llm_eval
from app.intake import load_batch
from app.memory import build_from_split
from app.pipeline import run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--split", default=Split.HELDOUT.value, choices=[s.value for s in Split]
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="also test the adjudicator on its own (costs a few rupees)",
    )
    args = parser.parse_args()
    split = Split(args.split)

    memory = build_from_split()
    result = run(split)
    metrics = evaluate(result, load_truth(split))
    print_report(result, metrics)

    if args.llm:
        report = run_llm_eval(load_batch(split), result, load_truth(split), memory)
        print_llm_report(report)

    # A non-zero exit on a wrong approval, so this can gate a commit later.
    return 1 if metrics.false_auto_approvals else 0


if __name__ == "__main__":
    sys.exit(main())
