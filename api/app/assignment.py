"""Resolving the whole batch at once. Plan section 5, box 7.

Deciding one invoice at a time has a quiet bug: whichever invoice happens to be
processed first eats the transaction, and the answer depends on sort order.
Two identical bills and one payment would auto-approve whichever came back
from the database first.

**The test for a conflict is the sum invariant, not a name or a score.** Money
allocated out of a transaction can never exceed what the transaction was
worth. That single rule separates the two cases that look alike from a
distance:

- A **combined payment** has several invoices claiming one transaction, and
  their claims add up to exactly what arrived. No conflict.
- **Two identical invoices** each claim the whole transaction, so the claims
  add up to twice what arrived. Conflict, and neither may be automated.

It is the same invariant the database enforces with a trigger, so a bug here
still cannot double-count money.
"""

from dataclasses import dataclass, field

from app.blocking import Pass
from app.intake import NormInvoice
from app.guardrails import AUTO_SCORE
from app.money import TOLERANCE_PAISE, fmt
from app.scoring import Ranking, Scored


@dataclass
class Claim:
    """One invoice asking for money out of one or more transactions."""

    invoice: NormInvoice
    ranking: Ranking
    best: Scored
    allocations: dict[str, int]     # txn id -> paise claimed from that payment

    @property
    def txn_ids(self) -> list[str]:
        return list(self.allocations)

    @property
    def allocated_paise(self) -> int:
        return sum(self.allocations.values())

    @property
    def score(self) -> float:
        return self.ranking.score


@dataclass
class Assignment:
    claim: Claim
    conflict: str | None = None      # None means this claim stands

    @property
    def invoice_id(self) -> str:
        return self.claim.invoice.id


@dataclass
class Conflict:
    txn_id: str
    available_paise: int
    claimed_paise: int
    invoice_ids: list[str] = field(default_factory=list)


def claim_amounts(invoice: NormInvoice, ranking: Ranking, txn_ids: list[str]) -> dict[str, int]:
    """How much this invoice wants out of each payment it named.

    One number for the whole claim is wrong for a partial payment: the claim
    names two instalments, and charging the total against both makes the
    smaller one look over-subscribed by its own claimant.
    """
    best = ranking.best
    assert best is not None

    if best.amount.pass_used is Pass.COMBINED:
        # One payment across several bills: this invoice takes its own value.
        return {best.id: invoice.amount_paise}

    if best.amount.pass_used is Pass.PARTIAL:
        # Every instalment goes to this bill, each charged to its own payment.
        by_id = {s.id: s.txn.amount_paise for s in ranking.candidates}
        return {t: by_id[t] for t in txn_ids if t in by_id}

    return {best.id: min(invoice.amount_paise, best.txn.amount_paise)}


def resolve(claims: list[Claim]) -> tuple[list[Assignment], list[Conflict]]:
    """Mark every claim that cannot stand alongside the others.

    Where a transaction is over-subscribed, the claims are ranked by score. A
    clear winner keeps it; near-equal claimants all lose it, because picking
    between them would be a coin flip dressed as a decision.
    """
    capacity: dict[str, int] = {}
    for claim in claims:
        for scored in claim.ranking.candidates:
            capacity[scored.id] = scored.txn.amount_paise

    # Only claims strong enough to be automated can take a payment off another
    # invoice. A record already heading for a human contests nothing: letting
    # a 0.31 candidate poison a 0.99 match would turn a clean answer into an
    # ambiguity that exists only inside our own scoring.
    contending = [c for c in claims if c.score >= AUTO_SCORE]

    by_txn: dict[str, list[Claim]] = {}
    for claim in contending:
        for txn_id in claim.txn_ids:
            by_txn.setdefault(txn_id, []).append(claim)

    blocked: dict[str, str] = {}
    conflicts: list[Conflict] = []

    for txn_id, contenders in by_txn.items():
        available = capacity.get(txn_id, 0)
        claimed = sum(c.allocations.get(txn_id, 0) for c in contenders)

        if claimed <= available + TOLERANCE_PAISE:
            continue

        conflicts.append(
            Conflict(
                txn_id=txn_id,
                available_paise=available,
                claimed_paise=claimed,
                invoice_ids=[c.invoice.id for c in contenders],
            )
        )

        ranked = sorted(contenders, key=lambda c: c.score, reverse=True)
        others = [c.invoice.id for c in ranked]

        for claim in ranked:
            rivals = [i for i in others if i != claim.invoice.id]
            if rivals:
                blocked[claim.invoice.id] = (
                    f"{txn_id} is worth {fmt(available)} but {len(contenders)} invoices "
                    f"claim {fmt(claimed)} of it; also wanted by {', '.join(rivals)}"
                )
            else:
                # One claim on its own asking for more than arrived. Not an
                # ambiguity - just money that is not there.
                blocked[claim.invoice.id] = (
                    f"{txn_id} is worth {fmt(available)} but this invoice claims "
                    f"{fmt(claimed)} of it"
                )

    return [Assignment(claim=c, conflict=blocked.get(c.invoice.id)) for c in claims], conflicts
