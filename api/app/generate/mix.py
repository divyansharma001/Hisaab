"""How many records of each scenario, per split.

Do not generate 85 random records. Design the mix so every branch of the
pipeline gets exercised. Plan section 9.
"""

from app.dataset import Split

# The graded set, exactly as the plan specifies. Counts are invoices.
HELDOUT_MIX: dict[str, int] = {
    "exact_reference": 18,        # fast path
    "clean_name_amount": 10,      # basic scoring
    "alias_variation": 8,         # alias memory
    "tds_deduction": 6,           # settlement maths
    "gateway_fee": 5,             # settlement maths
    "partial_payment": 5,         # one bill, many payments
    "combined_payment": 4,        # many bills, one payment
    "batched_settlement": 6,      # splitting a batch back into its parts
    "duplicate_transaction": 4,   # duplicate guardrail
    "short_payment": 4,           # a true exception
    "no_payment": 5,              # a true exception
    "identical_invoices": 4,      # margin rule
    "date_out_of_window": 3,      # date guardrail
    "value_ceiling": 3,           # value ceiling guardrail
}

SPLIT_SIZES: dict[Split, int] = {
    Split.TUNING: 45,
    Split.HELDOUT: 85,
}

# The alias seed set has no scenario mix. Its size is not a number we choose -
# it is one prior settlement per customer the graded batch will meet, so the
# new-counterparty guardrail has real history to check against.
#
# The plan fixed it at 30. The graded set has 81 distinct customers, so 30
# records left 64 of 85 invoices looking like first-time counterparties and
# the guardrail correctly refused to automate them. Straight-through came out
# at 22.4%, which measures our synthetic history, not our matcher.
ALIAS_SEED_SCENARIO = "prior_settlement"

# Some scenarios need whole groups to make sense: a pair of identical invoices,
# a batch of gateway payments under one deposit. Scaling must respect that.
GROUP_SIZES: dict[str, int] = {
    "identical_invoices": 2,
    "batched_settlement": 3,
    "combined_payment": 2,
}


def mix_for(split: Split) -> dict[str, int]:
    """Scale the graded mix to a split's size, keeping the shape.

    The held-out set uses the plan's table unchanged. The other two are scaled
    down, then nudged until the counts add up exactly.
    """
    if split is Split.ALIAS_SEED:
        raise ValueError(
            "the alias seed set has no scenario mix; its size comes from the "
            "graded set's counterparties"
        )

    target = SPLIT_SIZES[split]
    if split is Split.HELDOUT:
        return dict(HELDOUT_MIX)

    total = sum(HELDOUT_MIX.values())
    scaled: dict[str, int] = {}

    for name, count in HELDOUT_MIX.items():
        group = GROUP_SIZES.get(name, 1)
        want = count * target / total
        # Round to a whole number of groups, but never drop a scenario entirely.
        groups = max(1, round(want / group))
        scaled[name] = groups * group

    _reconcile(scaled, target)
    return scaled


def _reconcile(mix: dict[str, int], target: int) -> None:
    """Add or remove records until the mix totals exactly `target`.

    Adjustments land on the biggest ungrouped scenarios first, so the rare
    ones keep the counts they were given.
    """
    adjustable = sorted(
        (n for n in mix if n not in GROUP_SIZES),
        key=lambda n: HELDOUT_MIX[n],
        reverse=True,
    )

    while sum(mix.values()) != target:
        diff = target - sum(mix.values())
        step = 1 if diff > 0 else -1

        for name in adjustable:
            if step < 0 and mix[name] <= 1:
                continue
            mix[name] += step
            break
        else:
            # Every ungrouped scenario is already at its floor.
            raise ValueError(f"cannot reconcile mix to {target}: {mix}")
