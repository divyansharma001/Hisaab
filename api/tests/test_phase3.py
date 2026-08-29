"""Phase 3 regression tests: global assignment, the guardrails, the audit log.

The headline test is `test_zero_false_auto_approvals`. Everything else in this
project exists to keep that number at zero, and it is the one claim we make
that cannot be softened.

The second most important is `test_the_guardrails_bought_safety_not_accuracy`,
which checks the trade in both directions: false approvals went to zero *and*
the straight-through rate went down. A change that quietly raised both would
mean a guardrail stopped firing.
"""

from datetime import date, timedelta

import psycopg
import pytest

from app import guardrails
from app.assignment import Claim, claim_amounts, resolve
from app.blocking import Candidate, Pass, block
from app.config import get_settings
from app.dataset import Outcome, Reason, Split
from app.evaluate import evaluate, load_truth
from app.guardrails import find_duplicates
from app.intake import NormInvoice, NormTxn, load_batch
from app.memory import Memory, build_from_split
from app.money import rupees
from app.persist import save
from app.pipeline import run
from app.scoring import rank

# What the deterministic core managed before the guardrail layer existed.
# Kept as numbers, not prose, so the trade can be asserted rather than claimed.
PHASE_2_STRAIGHT_THROUGH = 83.5
PHASE_2_FALSE_APPROVALS = 11

# 62 of the 85 graded records should be automated. Anything above that is a
# false approval, not an improvement.
STRAIGHT_THROUGH_CEILING = 62


@pytest.fixture(scope="module")
def batch():
    return load_batch(Split.HELDOUT)


@pytest.fixture(scope="module")
def truth():
    return load_truth(Split.HELDOUT)


@pytest.fixture(scope="module")
def result():
    return run(Split.HELDOUT)


@pytest.fixture(scope="module")
def metrics(result, truth):
    return evaluate(result, truth)


def _invoice(**kw) -> NormInvoice:
    base = dict(
        id="INV-0001",
        invoice_no="INV-0001",
        name_raw="ABC Technologies Pvt Ltd",
        name_clean="ABC TECHNOLOGIES",
        amount_paise=rupees(10_000),
        currency="INR",
        invoice_date=date(2026, 6, 1),
        due_date=date(2026, 7, 1),
        scenario="test",
    )
    return NormInvoice(**{**base, **kw})


def _txn(**kw) -> NormTxn:
    base = dict(
        id="TXN-0001",
        description_raw="NEFT/ABCTECHNOLOGIES/HDFC123456789012",
        name_clean="ABCTECHNOLOGIES",
        refs=("HDFC123456789012",),
        amount_paise=rupees(10_000),
        currency="INR",
        value_date=date(2026, 7, 2),
        source="bank",
        utr="HDFC123456789012",
        scenario="test",
    )
    return NormTxn(**{**base, **kw})


# --- the headline -----------------------------------------------------------


def test_zero_false_auto_approvals(metrics):
    """The one number that must be zero. Nothing else in this file matters
    if this fails."""
    assert metrics.false_auto_approvals == [], [
        f"{j.invoice_id} ({j.scenario}): expected {j.expected.value}"
        for j in metrics.false_auto_approvals
    ]


def test_nothing_that_needed_a_human_slipped_through(metrics):
    assert metrics.missed_exceptions == []


def test_auto_approval_precision_is_total(metrics):
    assert metrics.auto_precision == 100.0


def test_the_guardrails_bought_safety_not_accuracy(metrics):
    """The trade, asserted in both directions.

    False approvals had to reach zero, and the straight-through rate had to
    fall. If both improved, a guardrail has stopped firing somewhere.
    """
    assert len(metrics.false_auto_approvals) < PHASE_2_FALSE_APPROVALS
    assert metrics.straight_through_rate < PHASE_2_STRAIGHT_THROUGH


def test_we_never_automate_more_than_the_data_allows(metrics):
    assert len(metrics.auto) <= STRAIGHT_THROUGH_CEILING


def test_accuracy_did_not_collapse(metrics):
    assert metrics.outcome_accuracy > 95.0


# --- routing precedence -----------------------------------------------------


def test_ambiguity_beats_confidence():
    """A record scoring 0.95 with a margin of 0.03 satisfies two rules at
    once. Ambiguity has to win that argument. Section 18, bug 4."""
    assert guardrails.route(0.95, 0.03) == guardrails.ADJUDICATE
    assert guardrails.route(0.95, 0.50) == guardrails.AUTO
    assert guardrails.route(0.75, 0.50) == guardrails.ADJUDICATE
    assert guardrails.route(0.40, 0.50) == guardrails.EXCEPTION


# --- Layer 1: input guardrails ----------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount_paise", -100),
        ("currency", "USD"),
        ("invoice_date", date(2099, 1, 1)),
        ("id", ""),
    ],
)
def test_bad_input_never_reaches_the_scorer(field, value):
    today = date(2026, 8, 29)
    assert guardrails.check_invoice(_invoice(**{field: value}), today)


def test_clean_input_passes():
    assert guardrails.check_invoice(_invoice(), date(2026, 8, 29)) == []


def test_a_future_dated_payment_is_rejected():
    today = date(2026, 8, 29)
    assert guardrails.check_txn(_txn(value_date=date(2027, 1, 1)), today)


# --- Layer 2: the hard rules ------------------------------------------------


def _verdict(invoice, txn, batch, memory_seen=5, conflict=None, duplicates=None):
    ranking = rank(invoice, [Candidate(txn=txn, passes={Pass.ONE_TO_ONE})], batch)
    return guardrails.apply(
        invoice,
        ranking,
        ranking.best,
        claimed_txn_ids=[txn.id],
        duplicates=duplicates or {},
        counterparty_seen=memory_seen,
        conflict=conflict,
    )


def test_a_perfect_match_above_the_ceiling_still_goes_to_a_human(batch):
    """The model was confident. The rules overruled it. That is deliberate."""
    big = rupees(800_000)
    verdict = _verdict(_invoice(amount_paise=big), _txn(amount_paise=big), batch)

    assert verdict.outcome is Outcome.REVIEW
    assert verdict.reason_code is Reason.VALUE_CEILING


def test_a_first_time_counterparty_is_never_automated(batch):
    verdict = _verdict(_invoice(), _txn(), batch, memory_seen=0)
    assert verdict.outcome is Outcome.REVIEW
    assert verdict.reason_code is Reason.NEW_COUNTERPARTY


def test_a_known_counterparty_passes_that_rule(batch):
    verdict = _verdict(_invoice(), _txn(), batch, memory_seen=3)
    assert verdict.outcome is Outcome.AUTO


def test_a_duplicated_payment_holds_both(batch):
    verdict = _verdict(
        _invoice(), _txn(), batch, duplicates={"TXN-0001": ["TXN-0002"]}
    )
    assert verdict.outcome is Outcome.REVIEW
    assert verdict.reason_code is Reason.DUPLICATE_TRANSACTION
    assert "TXN-0002" in verdict.reason_text


def test_a_contested_payment_is_never_automated(batch):
    verdict = _verdict(_invoice(), _txn(), batch, conflict="INV-0002 wants it too")
    assert verdict.outcome is Outcome.AMBIGUOUS


def test_the_score_does_not_get_a_veto(batch):
    """0.99 with an unexplained gap of Rs 340 is still an exception."""
    invoice = _invoice(amount_paise=rupees(10_000))
    txn = _txn(amount_paise=rupees(9_660))     # no formula explains this
    verdict = _verdict(invoice, txn, batch)

    assert verdict.outcome is not Outcome.AUTO
    assert verdict.reason_code is Reason.AMOUNT_GAP_UNEXPLAINED


def test_every_rule_is_recorded_either_way(batch):
    """The decision trace screen and the audit log both need this."""
    verdict = _verdict(_invoice(), _txn(), batch)
    names = {r.name for r in verdict.rules}
    assert {"score", "margin", "amount explained", "date window", "currency",
            "value ceiling", "known counterparty", "not a duplicate",
            "unclaimed"} <= names


# --- the date anchor --------------------------------------------------------


def test_the_scorer_and_the_guardrail_measure_from_the_same_date(result):
    """Two anchors 30 days apart make records pass one check and fail the
    other for no reason anyone can see. Section 18, bug 5."""
    for ranking in result.rankings.values():
        best = ranking.best
        if best is None:
            continue
        assert best.date_anchor >= ranking.invoice.due_date


def test_a_combined_payment_is_not_late_against_its_group(result, batch):
    """A customer settling three bills at once pays around when the last one
    falls due. Measuring the older ones against their own due dates makes a
    correct match look 55 days overdue."""
    by_id = {i.id: i for i in batch.invoices}
    checked = 0

    for ranking in result.rankings.values():
        best = ranking.best
        if best is None or best.amount.pass_used is not Pass.COMBINED:
            continue
        if not best.amount.members:
            continue

        group = [ranking.invoice, *(by_id[m] for m in best.amount.members if m in by_id)]
        assert best.date_anchor == max(i.due_date for i in group)
        checked += 1

    assert checked, "no combined payment reached the anchor rule"


# --- global assignment ------------------------------------------------------


def test_duplicate_detection_finds_the_twins(batch):
    duplicates = find_duplicates(batch)
    scenario_txns = {
        t.id for t in batch.transactions if t.scenario == "duplicate_transaction"
    }
    assert scenario_txns <= set(duplicates)


def test_two_invoices_cannot_split_one_payment_they_both_want(batch, result, truth):
    """The identical-invoice case. Global assignment's whole reason to exist."""
    for decision in result.decisions:
        if decision.scenario == "identical_invoices":
            assert decision.outcome is not Outcome.AUTO
            assert decision.reason_code is Reason.AMBIGUOUS_CANDIDATES


def test_a_combined_payment_is_not_treated_as_a_conflict(result):
    """Several invoices claiming one payment is fine when their claims add up
    to what actually arrived. That is the difference between a combined
    payment and two bills fighting over one deposit."""
    for decision in result.decisions:
        if decision.scenario == "combined_payment":
            assert decision.reason_code is not Reason.AMBIGUOUS_CANDIDATES


def test_a_partial_charges_each_instalment_to_its_own_payment(batch):
    """One number for the whole claim made the smaller instalment look
    over-subscribed by its own claimant."""
    invoice = next(i for i in batch.invoices if i.scenario == "partial_payment")
    ranking = rank(invoice, block(invoice, batch), batch)
    best = ranking.best
    txn_ids = [best.id, *best.amount.members]

    allocations = claim_amounts(invoice, ranking, txn_ids)
    by_id = {s.id: s.txn.amount_paise for s in ranking.candidates}

    assert len(allocations) == len(txn_ids)
    for txn_id, allocated in allocations.items():
        assert allocated <= by_id[txn_id], "claimed more than that payment was worth"


def test_a_hopeless_claim_cannot_poison_a_good_match(batch):
    """An invoice heading for a human contests nothing.

    Before this, a 0.31 candidate could contest a 0.99 match and turn a clean
    answer into an ambiguity that existed only inside our own scoring.
    """
    txn = _txn(amount_paise=rupees(10_000))

    # Two invoices, each wanting the whole payment. Together they ask for
    # twice what arrived, so on their own they would collide.
    strong = _invoice(id="INV-STRONG", amount_paise=rupees(10_000))
    weak = _invoice(id="INV-WEAK", amount_paise=rupees(10_000))

    def claim_for(invoice, score):
        ranking = rank(invoice, [Candidate(txn=txn, passes={Pass.ONE_TO_ONE})], batch)
        ranking.candidates[0].score = score
        return Claim(
            invoice=invoice,
            ranking=ranking,
            best=ranking.candidates[0],
            allocations={txn.id: invoice.amount_paise},
        )

    # Both viable: they contest each other and neither may be automated.
    contested, _ = resolve([claim_for(strong, 0.99), claim_for(weak, 0.97)])
    assert all(a.conflict for a in contested)

    # The rival is hopeless: it contests nothing and the good match stands.
    assignments, _ = resolve([claim_for(strong, 0.99), claim_for(weak, 0.31)])
    by_invoice = {a.invoice_id: a for a in assignments}
    assert by_invoice["INV-STRONG"].conflict is None


# --- memory -----------------------------------------------------------------


def test_memory_never_reads_the_graded_set():
    """Seeding from the batch being graded inflates accuracy with information
    the system never had. Section 18, bug 7."""
    with pytest.raises(ValueError):
        build_from_split(Split.HELDOUT)


def test_memory_covers_every_graded_customer(batch):
    memory = build_from_split()
    for invoice in batch.invoices:
        assert memory.seen(invoice.name_clean) >= guardrails.NEW_COUNTERPARTY_MIN


def test_without_memory_nothing_is_automated():
    """Proves the new-counterparty rule is actually load-bearing."""
    result = run(Split.HELDOUT, use_memory=False)
    automated = [d for d in result.decisions if d.outcome is Outcome.AUTO]
    assert automated == []


# --- persistence ------------------------------------------------------------


def test_the_run_is_written_and_the_database_agrees(result):
    """The sum invariant is a trigger, so this commit is an independent check
    on our allocation arithmetic. If we ever allocate more of a payment than
    arrived, save() raises instead of writing books that are quietly wrong."""
    counts = save(result)

    assert counts["audit"] == len(result.decisions)
    assert counts["exceptions"] == sum(
        1 for d in result.decisions if d.outcome is not Outcome.AUTO
    )

    with psycopg.connect(get_settings().database_url) as conn:
        over_allocated = conn.execute(
            """SELECT count(*) FROM (
                 SELECT t.id FROM match_allocations ma
                 JOIN matches m      ON m.id = ma.match_id
                 JOIN transactions t ON t.id = m.transaction_id
                 GROUP BY t.id, t.amount_paise
                 HAVING SUM(ma.allocated_paise) > t.amount_paise
               ) x"""
        ).fetchone()[0]
    assert over_allocated == 0


def test_held_records_allocate_no_money(result):
    for decision in result.decisions:
        if decision.outcome is not Outcome.AUTO:
            assert decision.allocated_paise == 0
            assert decision.allocations == {}


def test_every_refusal_names_a_reason(result):
    """Good: AMOUNT_GAP_UNEXPLAINED with the figure. Useless: low confidence."""
    for decision in result.decisions:
        if decision.outcome is Outcome.AUTO:
            continue
        assert decision.reason_code is not None
        assert len(decision.reason_text) > 10, decision
