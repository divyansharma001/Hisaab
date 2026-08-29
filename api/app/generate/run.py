"""Generating all three splits, and keeping them honest about each other.

The order matters. The held-out set is built first and claims its
counterparties. The alias seed set is then built from **those same
counterparties**, because its only job is to teach the alias table names it
will meet again during the graded run.

If the seed set used different companies it would teach nothing, and if the
graded run learned its own aliases the reported accuracy would be inflated by
information the system never actually had. That is bug 7 in plan section 18.
"""

import random

from app.dataset import Dataset, Split
from app.generate.builders import BUILDERS, Ctx
from app.generate.companies import Counterparty, build_pool
from app.generate.mix import mix_for

# Scenarios whose difficulty comes from the counterparty name. These are the
# ones the alias seed set needs to have met before.
NAME_DRIVEN = {"alias_variation", "tds_deduction", "gateway_fee", "clean_name_amount"}

POOL_SIZE = 170


def _generate_split(
    split: Split, seed: int, pool: list[Counterparty]
) -> Ctx:
    ctx = Ctx(split, seed, pool)
    for scenario, count in mix_for(split).items():
        BUILDERS[scenario](ctx, count)
    return ctx


def generate_all(seed: int) -> dict[Split, Dataset]:
    """Build every split from one seed. Same seed in, same 130 records out."""
    rng = random.Random(seed)
    pool = build_pool(rng, POOL_SIZE)

    at = 0

    # 1. The graded set. Gets first pick of the pool.
    heldout = _generate_split(Split.HELDOUT, seed + 1, pool[at:])
    at += len(heldout.used_companies)

    # 2. The tuning set. Fresh companies, so tuning cannot memorise the
    #    graded ones.
    tuning = _generate_split(Split.TUNING, seed + 2, pool[at:])
    at += len(tuning.used_companies)

    # 3. The alias seed set. Reuses the graded set's name-driven companies,
    #    which is exactly what "we have seen this customer before" means.
    seen = _name_driven_companies(heldout)
    topped_up = seen + pool[at:]
    alias_seed = _generate_split(Split.ALIAS_SEED, seed + 3, topped_up)

    return {
        Split.HELDOUT: heldout.data,
        Split.TUNING: tuning.data,
        Split.ALIAS_SEED: alias_seed.data,
    }


def _name_driven_companies(ctx: Ctx) -> list[Counterparty]:
    """The graded set's counterparties whose scenarios turn on the name."""
    wanted = {
        inv.counterparty_name_clean
        for inv in ctx.data.invoices
        if inv.scenario in NAME_DRIVEN
    }
    return [c for c in ctx.used_companies if c.clean in wanted]
