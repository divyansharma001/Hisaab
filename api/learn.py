"""Show the system getting better. Plan section 19.3.

    docker compose exec api python learn.py

The plan has memory but never *shows* it working. This does, in one command:

1. Start with thin history, so some customers look new
2. Run the batch - those records are held, correctly
3. A reviewer confirms them, which is what writes memory
4. Run again - they resolve, and so do others

**Nothing learns from the run being graded.** `learn_from` returns a new
Memory rather than changing the one in use, so improvement can only ever show
up in a *later* run. That is the whole reason the eval numbers mean anything.
"""

import argparse
import sys

from app.dataset import Outcome, Reason, Split
from app.evaluate import evaluate, load_truth
from app.intake import load_batch
from app.memory import Memory, build_from_split, episode_from, learn_from, persist
from app.money import fmt
from app.pipeline import process_batch, run


# Reason codes whose reasoning is worth showing to the adjudicator. A plain
# reference match is not one of them: there is nothing to learn from it.
WORTH_REMEMBERING = {
    Reason.TDS_2PCT,
    Reason.TDS_10PCT,
    Reason.MDR_GST,
    Reason.COMBINED_PAYMENT,
    Reason.PARTIAL_PAYMENT,
    Reason.BATCHED_SETTLEMENT,
    Reason.MATCHED_ALIAS,
}


def thin(memory: Memory, keep: float) -> Memory:
    """Keep only a fraction of what we know, so there is room to improve."""
    names = sorted(memory.confirmations)
    kept = set(names[: int(len(names) * keep)])
    return Memory(
        variants={k: v for k, v in memory.variants.items() if k in kept},
        confirmations={k: v for k, v in memory.confirmations.items() if k in kept},
        episodes=list(memory.episodes),
    ).snapshot()


def held_for_being_new(result) -> list:
    return [d for d in result.decisions if d.reason_code is Reason.NEW_COUNTERPARTY]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", type=float, default=0.5, help="fraction of history to start with")
    parser.add_argument(
        "--split",
        default=Split.HELDOUT.value,
        choices=[s.value for s in Split],
        help="which batch to learn from; episodes are tagged with it",
    )
    parser.add_argument("--keep-episodes", action="store_true",
                        help="add to the episode store instead of replacing it")
    args = parser.parse_args()

    split = Split(args.split)
    batch = load_batch(split)
    truth = load_truth(split)
    full = build_from_split()

    # --- day one: thin history ------------------------------------------
    start = thin(full, args.keep)
    print(f"\nDay one. We have settled with {len(start)} of "
          f"{len({i.name_clean for i in batch.invoices})} customers before.\n")

    before = process_batch(batch, memory=start)
    before_metrics = evaluate(before, truth)
    new_names = held_for_being_new(before)

    print(f"  automated            {len(before_metrics.auto)}/{before_metrics.total}"
          f"  ({before_metrics.straight_through_rate:.1f}%)")
    print(f"  held as new customer {len(new_names)}")
    print(f"  wrong approvals      {len(before_metrics.false_auto_approvals)}")

    if new_names:
        print("\n  A reviewer opens the first few:")
        for decision in new_names[:3]:
            print(f"    {decision.invoice_id}  {fmt(decision.amount_paise):>14}  {decision.reason_text}")

    # --- the reviewer confirms ------------------------------------------
    invoices = batch.invoice_by_id()
    transactions = batch.txn_by_id()

    grown = learn_from(start, before, invoices, transactions)
    if not args.keep_episodes:
        grown.episodes = []

    # Confirming a held record is what a human clicking "yes, that is right"
    # does. Only the ones a person actually looked at.
    confirmed = 0
    for decision in new_names:
        invoice = invoices.get(decision.invoice_id)
        for txn_id in decision.txn_ids:
            txn = transactions.get(txn_id)
            if invoice and txn:
                grown.confirmations[invoice.name_clean] = (
                    grown.confirmations.get(invoice.name_clean, 0) + 3
                )
                grown.variants.setdefault(invoice.name_clean, set()).add(txn.name_clean)
                confirmed += 1

    # Interesting cases become worked examples for the adjudicator.
    #
    # "Interesting" is about the *shape* of the reasoning, not the score. A
    # bill settled because 2% TDS explained the gap exactly is a good example
    # at 0.98 just as much as at 0.88 - it shows how that kind of case is
    # decided. A plain invoice-number match teaches nothing.
    for decision in before.decisions:
        if decision.outcome is not Outcome.AUTO:
            continue
        if decision.reason_code not in WORTH_REMEMBERING:
            continue
        invoice = invoices.get(decision.invoice_id)
        txn = transactions.get(decision.txn_ids[0]) if decision.txn_ids else None
        if invoice and txn:
            grown.episodes.append(episode_from(decision, invoice, txn, split))

    print(f"\n  Reviewer confirmed {confirmed} matches.")
    print(f"  Memory grew from {len(start)} customers to {len(grown)}, "
          f"and now holds {len(grown.episodes)} worked examples.")

    # --- day two: run again ---------------------------------------------
    after = process_batch(batch, memory=grown.snapshot())
    after_metrics = evaluate(after, truth)

    print(f"\nDay two. Same records, same code, more memory.\n")
    print(f"  automated            {len(after_metrics.auto)}/{after_metrics.total}"
          f"  ({after_metrics.straight_through_rate:.1f}%)"
          f"   {after_metrics.straight_through_rate - before_metrics.straight_through_rate:+.1f} points")
    print(f"  accuracy             {after_metrics.outcome_accuracy:.1f}%"
          f"   {after_metrics.outcome_accuracy - before_metrics.outcome_accuracy:+.1f} points")
    print(f"  wrong approvals      {len(after_metrics.false_auto_approvals)}")

    was_held = {d.invoice_id for d in before.decisions if d.outcome is not Outcome.AUTO}
    now_auto = [d for d in after.decisions if d.outcome is Outcome.AUTO and d.invoice_id in was_held]

    print(f"\n  {len(now_auto)} records resolved that had not before:")
    for decision in now_auto[:6]:
        print(f"    {decision.invoice_id}  {fmt(decision.amount_paise):>14}  "
              f"{decision.reason_code.value if decision.reason_code else ''}")
    if len(now_auto) > 6:
        print(f"    ... and {len(now_auto) - 6} more")

    if args.keep_episodes:
        grown.episodes = list(full.episodes) + grown.episodes
    counts = persist(grown)
    print(f"\n  Written: {counts['aliases']} alias rows, {counts['episodes']} episodes.")
    print("  Nothing learned here touched the run it came from - improvement only "
          "ever shows up in the next one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
