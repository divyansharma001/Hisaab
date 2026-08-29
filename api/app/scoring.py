"""Scoring an invoice against a candidate payment. Plan section 6.

Four independent signals, weighted and summed. Two things here are easy to get
wrong and both matter more than the weights themselves:

**Renormalisation.** A missing signal shrinks the denominator; it never counts
as a zero. A bank line with no reference would otherwise cap at 0.50 however
perfect everything else is, and a correct match would be filed as an exception.
Absence of evidence is not evidence against.

**Margin.** The gap between the best candidate and the runner-up. A score of
0.95 means nothing if the runner-up scored 0.94 - that is a coin flip wearing a
high score, and it is the only thing that catches two identical invoices.
"""

from dataclasses import dataclass, field
from datetime import date
from itertools import combinations

from rapidfuzz import fuzz

from app import settlement
from app.blocking import Candidate, Pass, counterparty_invoices, counterparty_txns
from app.intake import Batch, NormInvoice, NormTxn
from app.money import TOLERANCE_PAISE, fmt
from app.names import looks_like_invoice_ref, name_similarity

# The plan's starting point, and the reference the grid search is measured from.
BASE_WEIGHTS: dict[str, float] = {
    "reference": 0.50,
    "amount": 0.25,
    "name": 0.15,
    "date": 0.10,
}

# What the grid search chose, on the tuning set only, under a no-wrong-payment
# constraint: the plan's own numbers, unchanged. 179 weightings were tried and
# 114 tied at 100% accuracy, so the result is flat across most of the grid.
#
# The finding worth saying out loud: the weights are not what makes or breaks
# this system. Over a hundred settings reach the same answer. The guardrails
# and the memory behind them are what move the number.
TUNED_WEIGHTS: dict[str, float] = dict(BASE_WEIGHTS)

# Subset search for combined and partial payments. Counterparties have a
# handful of open invoices, so this stays tiny; the cap only stops a
# pathological group from blowing up.
MAX_SUBSET_ITEMS = 8
MAX_SUBSET_SIZE = 4

SOLE_CANDIDATE = "sole_candidate"
RUNNER_UP = "runner_up"


@dataclass
class Signals:
    """Each signal is 0 to 1, or None when we simply do not have it.

    None and 0.0 are different claims. None means the bank told us nothing.
    0.0 means the bank told us something and it points elsewhere.
    """

    reference: float | None = None
    amount: float | None = None
    name: float | None = None
    date: float | None = None

    def available(self) -> dict[str, float]:
        return {
            key: value
            for key, value in (
                ("reference", self.reference),
                ("amount", self.amount),
                ("name", self.name),
                ("date", self.date),
            )
            if value is not None
        }


@dataclass
class AmountVerdict:
    """What the amount signal decided, and why."""

    score: float
    basis: str                 # the sentence the UI shows
    pass_used: Pass
    formula: str | None = None  # MDR_GST, TDS_2PCT, ... when one explained it
    # The rest of the group, for the shapes that only match as a set: the
    # sibling instalments of a partial, or the other invoices a combined
    # payment settles.
    members: tuple[str, ...] = ()


@dataclass
class Scored:
    txn: NormTxn
    passes: set[Pass]
    signals: Signals
    score: float
    weights_used: dict[str, float]
    amount: AmountVerdict
    # The due date the date signal was measured from. Stored rather than
    # recomputed, because the guardrail has to check the same window the
    # scorer used - measuring from two different anchors is bug 5 in
    # section 18, and it makes records pass one check and fail the other.
    date_anchor: date = date.min

    @property
    def id(self) -> str:
        return self.txn.id


@dataclass
class Ranking:
    invoice: NormInvoice
    candidates: list[Scored] = field(default_factory=list)
    margin: float = 0.0
    margin_basis: str = SOLE_CANDIDATE

    @property
    def best(self) -> Scored | None:
        return self.candidates[0] if self.candidates else None

    @property
    def score(self) -> float:
        return self.candidates[0].score if self.candidates else 0.0


# --- individual signals ----------------------------------------------------


def score_reference(invoice: NormInvoice, txn: NormTxn) -> float | None:
    """Plan section 6.3.

    None means the bank told us nothing about which invoice this pays. That is
    not the same as "no references at all": nearly every bank line carries a
    UTR, which is the bank's own id for the transfer and says nothing about the
    bill. Scoring a UTR as a non-matching reference caps a perfect match at
    0.49, which is the exact failure the renormalisation rule exists to stop,
    arriving by a different door.
    """
    if not txn.refs:
        return None

    best = 0.0
    for ref in txn.refs:
        if ref == invoice.invoice_no:
            return 1.0
        if invoice.invoice_no in ref:
            best = max(best, 0.7)
        elif fuzz.ratio(ref, invoice.invoice_no) > 85:
            best = max(best, 0.4)

    if best > 0.0:
        return best

    # Nothing matched. Only call that evidence against the match if the bank
    # actually sent something invoice-shaped; a bare UTR is silence.
    invoice_shaped = any(looks_like_invoice_ref(ref) for ref in txn.refs)
    return 0.0 if invoice_shaped else None


def score_name(invoice: NormInvoice, txn: NormTxn, aliases=None) -> float | None:
    """Plan section 6.5. None when the narration carries no name at all,
    which is exactly what a batched settlement deposit looks like."""
    if not txn.name_clean:
        return None
    if aliases is not None and aliases.same_entity(invoice.name_clean, txn.name_clean):
        return 1.0
    return name_similarity(txn.name_clean, invoice.name_clean)


def due_anchor(invoice: NormInvoice, amount: AmountVerdict, batch: Batch) -> date:
    """The date this payment was actually due to arrive.

    Normally the invoice's own due date. For a combined payment it is the
    latest due date in the group: a customer settling three bills at once pays
    around when the last one falls due, so the older invoices are not late in
    any meaningful sense. Measuring them against their own due dates makes a
    correct match look 55 days overdue.
    """
    if amount.pass_used is not Pass.COMBINED or not amount.members:
        return invoice.due_date

    group = {invoice.id, *amount.members}
    return max(i.due_date for i in batch.invoices if i.id in group)


def score_date(invoice: NormInvoice, txn: NormTxn, anchor: date | None = None) -> float:
    """Plan section 6.6. Late is normal, early is odd. The asymmetry is deliberate."""
    days = (txn.value_date - (anchor or invoice.due_date)).days

    if days < -7:
        return 0.0
    if days <= 7:
        return 1.0
    if days <= 45:
        return 1.0 - (days - 7) / 38 * 0.7
    return 0.0


def _find_subset(
    target: int, parts: list[tuple[str, int]], must_include: int
) -> tuple[str, ...] | None:
    """Which of `parts`, alongside `must_include`, add up to `target`?

    Returns the ids that complete the group, or None if nothing does. The ids
    matter as much as the answer: a partial payment is only settled by all of
    its instalments, so the decision needs to name every one of them.
    """
    remainder = target - must_include
    if abs(remainder) <= TOLERANCE_PAISE:
        return ()
    if remainder < 0:
        return None

    usable = sorted(
        ((i, a) for i, a in parts if a <= remainder + TOLERANCE_PAISE),
        key=lambda p: -p[1],
    )[:MAX_SUBSET_ITEMS]

    for size in range(1, min(MAX_SUBSET_SIZE, len(usable)) + 1):
        for combo in combinations(usable, size):
            if abs(sum(a for _, a in combo) - remainder) <= TOLERANCE_PAISE:
                return tuple(i for i, _ in combo)
    return None


def score_amount(
    invoice: NormInvoice, candidate: Candidate, batch: Batch
) -> AmountVerdict:
    """Plan section 6.4, extended so a partial or combined payment is not
    scored as if it were a failed one-to-one match.

    Runs **after** the settlement maths, never before. Returns the score, the
    string the UI shows, and which pass earned it.
    """
    txn = candidate.txn
    verdicts = [AmountVerdict(0.0, "amount does not match", Pass.ONE_TO_ONE)]

    if Pass.ONE_TO_ONE in candidate.passes:
        verdicts.append(_score_one_to_one(invoice, txn))
    if Pass.COMBINED in candidate.passes:
        verdicts.append(_score_combined(invoice, txn, batch))
    if Pass.PARTIAL in candidate.passes:
        verdicts.append(_score_partial(invoice, txn, batch))

    return max(verdicts, key=lambda v: v.score)


def _score_one_to_one(invoice: NormInvoice, txn: NormTxn) -> AmountVerdict:
    gap = abs(invoice.amount_paise - txn.amount_paise)
    one = Pass.ONE_TO_ONE

    if gap <= TOLERANCE_PAISE:
        return AmountVerdict(1.0, "exact", one, settlement.NO_DEDUCTION)

    explained = settlement.explain(invoice.amount_paise, txn.amount_paise)
    if explained is not None:
        # A formula that happens to fit is weaker evidence than an exact figure.
        return AmountVerdict(0.95, explained.describe(), one, explained.formula)

    pct = gap / invoice.amount_paise
    if pct < 0.01:
        return AmountVerdict(0.6, f"unexplained gap of {fmt(gap)}", one)
    if pct < 0.05:
        return AmountVerdict(0.3, f"unexplained gap of {fmt(gap)}", one)
    return AmountVerdict(0.0, "amount does not match", one)


def _score_combined(
    invoice: NormInvoice, txn: NormTxn, batch: Batch
) -> AmountVerdict:
    """Does this payment equal this invoice plus other bills for the same customer?

    Without this, a combined payment scores 0.0 on amount and falls to an
    exception - and the plan's own worked example (section 6.8) depends on it
    scoring around 0.8 instead.
    """
    siblings = [(o.id, o.amount_paise) for o in counterparty_invoices(invoice, batch)]
    if siblings:
        others = _find_subset(txn.amount_paise, siblings, invoice.amount_paise)
        if others is not None:
            return AmountVerdict(
                0.85,
                f"settles this invoice together with {len(others)} more "
                f"for this customer",
                Pass.COMBINED,
                members=others,
            )
    return AmountVerdict(0.0, "amount does not match", Pass.COMBINED)


def _score_partial(
    invoice: NormInvoice, txn: NormTxn, batch: Batch
) -> AmountVerdict:
    """Does this payment plus other payments from the same customer equal the bill?"""
    others = [
        (o.id, o.amount_paise)
        for o in counterparty_txns(invoice, batch)
        if o.id != txn.id
    ]
    if others:
        rest = _find_subset(invoice.amount_paise, others, txn.amount_paise)
        if rest is not None:
            return AmountVerdict(
                0.85,
                f"one of {len(rest) + 1} instalments that together settle this invoice",
                Pass.PARTIAL,
                members=rest,
            )
    return AmountVerdict(0.0, "amount does not match", Pass.PARTIAL)


# --- putting it together ---------------------------------------------------


def score_pair(
    invoice: NormInvoice,
    candidate: Candidate,
    batch: Batch,
    weights: dict[str, float] | None = None,
    aliases=None,
) -> Scored:
    weights = weights or TUNED_WEIGHTS

    amount = score_amount(invoice, candidate, batch)
    anchor = due_anchor(invoice, amount, batch)
    signals = Signals(
        reference=score_reference(invoice, candidate.txn),
        amount=amount.score,
        name=score_name(invoice, candidate.txn, aliases),
        date=score_date(invoice, candidate.txn, anchor),
    )

    available = signals.available()

    # The renormalisation rule. Plan section 6.2, and do not skip it: dropping
    # an unavailable signal from the denominator is the difference between a
    # perfect match scoring 0.488 and scoring 0.975.
    total_weight = sum(weights[key] for key in available)
    if total_weight == 0:
        score, used = 0.0, {}
    else:
        used = {key: weights[key] / total_weight for key in available}
        score = sum(value * used[key] for key, value in available.items())

    return Scored(
        txn=candidate.txn,
        passes=candidate.passes,
        signals=signals,
        score=round(score, 4),
        weights_used=used,
        amount=amount,
        date_anchor=anchor,
    )


def rank(
    invoice: NormInvoice,
    candidates: list[Candidate],
    batch: Batch,
    weights: dict[str, float] | None = None,
    aliases=None,
) -> Ranking:
    scored = sorted(
        (score_pair(invoice, c, batch, weights, aliases) for c in candidates),
        key=lambda s: s.score,
        reverse=True,
    )
    ranking = Ranking(invoice=invoice, candidates=scored)
    if not scored:
        return ranking

    best = scored[0]

    # Margin only means something between candidates we would have to *choose*
    # between. The instalments of a partial payment are not alternatives to
    # each other - we are taking both - so comparing them produces a tiny
    # margin and holds back a match that was never ambiguous.
    group = {best.id, *best.amount.members}
    rival = next((s for s in scored[1:] if s.id not in group), None)

    if rival is not None:
        ranking.margin = round(best.score - rival.score, 4)
        ranking.margin_basis = RUNNER_UP
    else:
        # The sole-candidate rule. Plan section 7, and bug 3 in section 18:
        # a null margin here silently passes or silently fails every
        # fast-path record, depending on how the comparison is written.
        ranking.margin = 1.0
        ranking.margin_basis = SOLE_CANDIDATE

    return ranking
