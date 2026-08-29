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
from app.generate.mix import ALIAS_SEED_SCENARIO, mix_for

# Scenarios whose difficulty comes from the counterparty name. These are the
# ones the alias seed set needs to have met before.
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

    # 3. The alias seed set: prior settlements for every customer *either*
    #    batch will meet, graded and tuning alike.
    #
    #    Covering only the graded customers looks right and is not. The tuning
    #    set is a real batch too, and with no history for its customers the
    #    new-counterparty rule holds all 45 records - so the weight grid search
    #    returned the same 13.3% for every weighting, a flat line measuring
    #    nothing. A business has history with all of its customers, not with a
    #    chosen half.
    alias_seed = _generate_prior_history(
        seed + 3, heldout.used_companies + tuning.used_companies
    )

    return {
        Split.HELDOUT: heldout.data,
        Split.TUNING: tuning.data,
        Split.ALIAS_SEED: alias_seed.data,
    }


def _generate_prior_history(seed: int, companies: list[Counterparty]) -> Ctx:
    """One already-settled invoice per graded customer.

    Uses the *same* companies as the graded set on purpose. That is what
    "we have dealt with this company before" means, and it is what makes the
    alias table and the new-counterparty rule mean anything.

    Different invoices, different amounts, different bank name forms - only
    the customers are shared, so nothing about the graded answers leaks.
    """
    unique: list[Counterparty] = []
    seen: set[str] = set()
    for company in companies:
        if company.clean not in seen:
            seen.add(company.clean)
            unique.append(company)

    ctx = Ctx(Split.ALIAS_SEED, seed, unique)
    BUILDERS[ALIAS_SEED_SCENARIO](ctx, len(unique), companies=unique)
    return ctx
