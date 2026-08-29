"""Generate the synthetic dataset and its answer key.

    docker compose exec api python generate_data.py

Same seed in, same 160 records out. The answer key is written by the same
function that writes the data, so it costs nothing and cannot drift.
"""

import argparse
import json
import sys
from pathlib import Path

from app.config import get_settings
from app.dataset import Dataset, Split
from app.generate.run import generate_all
from app.generate.validate import validate, validate_across_splits
from app.money import fmt

OUT_DIR = Path(__file__).parent / "data" / "generated"

ORDER = [Split.HELDOUT, Split.TUNING, Split.ALIAS_SEED]


def write_files(data: Dataset, out_dir: Path) -> None:
    records = {
        "split": data.split.value,
        "seed": data.seed,
        "invoices": [i.model_dump(mode="json") for i in data.invoices],
        "transactions": [t.model_dump(mode="json") for t in data.transactions],
        "settlements": [s.model_dump(mode="json") for s in data.settlements],
    }
    (out_dir / f"{data.split.value}_records.json").write_text(
        json.dumps(records, indent=2)
    )

    # Keyed by invoice id, the shape plan section 8.1 specifies.
    truth = {
        t.invoice_id: {
            "scenario": t.scenario,
            "expected_outcome": t.expected_outcome.value,
            "expected_txn_ids": t.expected_txn_ids,
            "expected_reason_code": (
                t.expected_reason_code.value if t.expected_reason_code else None
            ),
            "note": t.note,
        }
        for t in data.truth
    }
    (out_dir / f"{data.split.value}_ground_truth.json").write_text(
        json.dumps(truth, indent=2)
    )


def print_summary(datasets: dict[Split, Dataset]) -> None:
    print("\nGenerated")
    print(f"  {'split':<12} {'invoices':>9} {'txns':>6} {'settlements':>12} {'value':>16}")
    for split in ORDER:
        d = datasets[split]
        value = sum(i.amount_paise for i in d.invoices)
        print(
            f"  {split.value:<12} {len(d.invoices):>9} {len(d.transactions):>6} "
            f"{len(d.settlements):>12} {fmt(value):>16}"
        )

    heldout = datasets[Split.HELDOUT]
    print("\nHeld-out set, by scenario")
    counts = heldout.scenario_counts()
    outcomes: dict[str, dict[str, int]] = {}
    for t in heldout.truth:
        outcomes.setdefault(t.scenario, {})
        key = t.expected_outcome.value
        outcomes[t.scenario][key] = outcomes[t.scenario].get(key, 0) + 1

    for scenario in sorted(counts, key=lambda s: -counts[s]):
        shape = ", ".join(f"{v} {k}" for k, v in sorted(outcomes[scenario].items()))
        print(f"  {scenario:<24} {counts[scenario]:>3}   expects {shape}")

    total = sum(len(datasets[s].invoices) for s in ORDER)
    print(f"\n  {total} records across three splits.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=get_settings().random_seed)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    print(f"Generating with seed {args.seed}")
    datasets = generate_all(args.seed)

    failed = False
    for split in ORDER:
        report = validate(datasets[split])
        if report.ok:
            print(f"  {split.value:<12} answer key checks out")
        else:
            failed = True
            print(f"  {split.value:<12} {len(report.errors)} PROBLEMS")
            for err in report.errors:
                print(f"      {err}")

    cross = validate_across_splits(datasets)
    if not cross.ok:
        failed = True
        for err in cross.errors:
            print(f"      {err}")

    if failed:
        print("\nRefusing to write a dataset whose answer key is wrong.")
        return 1

    for split in ORDER:
        write_files(datasets[split], args.out)

    print_summary(datasets)
    print(f"\nWritten to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
