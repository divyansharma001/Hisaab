"""Write the last run to a file the UI can read without a backend.

    docker compose exec api python snapshot.py

Plan section 16.6. If the API dies five minutes before presenting, every
screen still has real numbers, because the frontend falls back to
`web/public/results.json` on its own. Nothing to flip, no flag to remember.
"""

import argparse
import json
import sys
from pathlib import Path

from app.api import CACHE
from app.cash import cash_position
from app.dataset import Outcome, Split

OUT = Path(__file__).parent.parent / "web" / "public" / "results.json"

# Traces are the heaviest part, so only the held records are snapshotted -
# those are the ones the demo actually opens.
MAX_TRACES = 40


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--split", default=Split.HELDOUT.value, choices=[s.value for s in Split])
    args = parser.parse_args()

    from app.api import adjudicated, cash, eval_breakdown, exceptions, latest_run, record

    CACHE.refresh(Split(args.split))
    print(f"Ran {CACHE.split.value}, snapshotting")

    payload: dict = {
        "summary": latest_run(),
        "exceptions": exceptions(),
        "eval": eval_breakdown(),
        "cash": cash(),
        "adjudicated": adjudicated(),
    }

    # Every row the UI can click has to have a trace in here, or the offline
    # demo dead-ends on the click. That means the held records *and* the ones
    # the model was asked about, which are auto-approved and so are not in the
    # held list at all.
    held = [d for d in CACHE.result.decisions if d.outcome is not Outcome.AUTO]
    wanted = held[:MAX_TRACES] + [
        d for d in CACHE.result.decisions if d.llm_used and d not in held
    ]
    for decision in wanted:
        payload[f"trace_{decision.invoice_id}"] = record(decision.invoice_id)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2))

    size = args.out.stat().st_size / 1024
    print(f"  {len(payload)} entries, {len(wanted)} traces, {size:.0f} KB")
    print(f"  {args.out}")
    print("\n  The UI reads this on its own if the API is unreachable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
