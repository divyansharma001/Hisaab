"""The batch pipeline. Plan section 14.6.

Read this file top to bottom and you know exactly what the system does. That
readability is the deliverable, not a side effect of skipping a framework.

The shape, in order:

1. Input guardrails, before anything costs us
2. Block, then score, then rank - per invoice
3. **Global assignment across the whole batch**, so the answer does not depend
   on which invoice came out of the database first
4. The hard rules, which decide what is allowed to become an action
5. Three terminal states, all logged

**Phase 3 scope.** No LLM yet. Records that would go to the adjudicator are
held for a human instead, which is the honest placeholder: they are exactly
the cases we cannot settle on our own.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app import guardrails
from app.assignment import Claim, Conflict, claim_amounts, resolve
from app.blocking import Pass, block
from app.dataset import Outcome, Reason, Split
from app.guardrails import find_duplicates
from app.intake import Batch, load_batch
from app.memory import Memory, build_from_split
from app.scoring import TUNED_WEIGHTS, Ranking, rank

FAST_PATH = "fast_path"
SCORER = "scorer"
NO_CANDIDATE = "none"
INPUT_GUARDRAIL = "input_guardrail"


@dataclass
class Decision:
    invoice_id: str
    outcome: Outcome
    txn_ids: list[str] = field(default_factory=list)
    allocated_paise: int = 0
    allocations: dict[str, int] = field(default_factory=dict)
    score: float = 0.0
    margin: float = 0.0
    margin_basis: str = "sole_candidate"
    reason_code: Reason | None = None
    reason_text: str = ""
    decided_by: str = NO_CANDIDATE
    scenario: str = ""
    amount_paise: int = 0
    rules_passed: list[str] = field(default_factory=list)
    rules_failed: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    split: Split
    run_id: str
    decisions: list[Decision] = field(default_factory=list)
    rankings: dict[str, Ranking] = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)
    seconds: float = 0.0
    memory_size: int = 0

    def by_outcome(self) -> dict[Outcome, int]:
        counts: dict[Outcome, int] = {}
        for d in self.decisions:
            counts[d.outcome] = counts.get(d.outcome, 0) + 1
        return counts


def settled_txn_ids(ranking: Ranking) -> list[str]:
    """Which payments this invoice is claiming.

    A partial payment is only settled by all of its instalments. Filtering
    them by individual score drops a late one and claims the bill was cleared
    by the first payment alone.
    """
    best = ranking.best
    if best is None:
        return []
    if best.amount.pass_used is Pass.PARTIAL:
        return [best.id, *best.amount.members]
    return [best.id]


def process_batch(
    batch: Batch,
    weights: dict[str, float] | None = None,
    memory: Memory | None = None,
) -> RunResult:
    started = datetime.now(UTC)
    weights = weights or TUNED_WEIGHTS
    memory = memory if memory is not None else Memory()

    # "Today" is the latest date in the feed, not the wall clock, so a record
    # never starts failing the future-date check because time passed.
    today = max(t.value_date for t in batch.transactions) if batch.transactions else started.date()

    result = RunResult(
        split=batch.split,
        run_id=f"run-{started:%Y%m%d-%H%M%S}",
        memory_size=len(memory),
    )

    duplicates = find_duplicates(batch)
    claims: list[Claim] = []

    # --- 1 and 2: input guardrails, then score what survives ---------------

    for invoice in batch.invoices:
        problems = guardrails.check_invoice(invoice, today)
        if problems:
            result.decisions.append(
                Decision(
                    invoice_id=invoice.id,
                    outcome=Outcome.EXCEPTION,
                    reason_code=Reason.MALFORMED_INPUT,
                    reason_text="; ".join(problems),
                    decided_by=INPUT_GUARDRAIL,
                    scenario=invoice.scenario,
                    amount_paise=invoice.amount_paise,
                    rules_failed=["input"],
                )
            )
            continue

        # Bad transactions never reach the scorer, let alone an LLM.
        usable = [c for c in block(invoice, batch) if not guardrails.check_txn(c.txn, today)]
        ranking = rank(invoice, usable, batch, weights, memory or None)
        result.rankings[invoice.id] = ranking

        if ranking.best is None:
            result.decisions.append(
                Decision(
                    invoice_id=invoice.id,
                    outcome=Outcome.EXCEPTION,
                    reason_code=Reason.NO_PAYMENT_FOUND,
                    reason_text="No payment in this batch could belong to this invoice",
                    decided_by=NO_CANDIDATE,
                    scenario=invoice.scenario,
                    amount_paise=invoice.amount_paise,
                )
            )
            continue

        txn_ids = settled_txn_ids(ranking)
        claims.append(
            Claim(
                invoice=invoice,
                ranking=ranking,
                best=ranking.best,
                allocations=claim_amounts(invoice, ranking, txn_ids),
            )
        )

    # --- 3: resolve the whole batch at once --------------------------------

    assignments, conflicts = resolve(claims)
    result.conflicts = conflicts

    # --- 4 and 5: the hard rules, then a terminal state --------------------

    for assignment in assignments:
        claim = assignment.claim
        invoice, ranking, best = claim.invoice, claim.ranking, claim.best

        verdict = guardrails.apply(
            invoice,
            ranking,
            best,
            claimed_txn_ids=claim.txn_ids,
            duplicates=duplicates,
            counterparty_seen=memory.seen(invoice.name_clean),
            conflict=assignment.conflict,
        )

        result.decisions.append(
            Decision(
                invoice_id=invoice.id,
                outcome=verdict.outcome,
                txn_ids=claim.txn_ids,
                allocated_paise=claim.allocated_paise if verdict.outcome is Outcome.AUTO else 0,
                allocations=dict(claim.allocations) if verdict.outcome is Outcome.AUTO else {},
                score=ranking.score,
                margin=ranking.margin,
                margin_basis=ranking.margin_basis,
                reason_code=verdict.reason_code,
                reason_text=verdict.reason_text,
                decided_by=FAST_PATH if best.signals.reference == 1.0 else SCORER,
                scenario=invoice.scenario,
                amount_paise=invoice.amount_paise,
                rules_passed=[r.name for r in verdict.passed],
                rules_failed=[r.name for r in verdict.failed],
            )
        )

    result.decisions.sort(key=lambda d: d.invoice_id)
    result.seconds = (datetime.now(UTC) - started).total_seconds()
    return result


def run(
    split: Split,
    weights: dict[str, float] | None = None,
    use_memory: bool = True,
) -> RunResult:
    memory = build_from_split() if use_memory else Memory()
    return process_batch(load_batch(split), weights, memory)
