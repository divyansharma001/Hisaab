"""Phase 2 regression tests: the deterministic core.

The baseline test at the bottom is the important one. Phase 3 is expected to
*lower* the straight-through rate while taking false approvals to zero, and
that trade is the whole argument of the project. It can only be shown if the
number it started from is pinned down.
"""

from datetime import date, timedelta

import pytest

from app.blocking import Pass, block
from app.dataset import Outcome, Split
from app.evaluate import Judgement, evaluate, load_truth
from app.intake import NormInvoice, NormTxn, load_batch
from app.money import rupees
from app.names import looks_like_invoice_ref
from app.pipeline import run
from app.scoring import BASE_WEIGHTS, Signals, rank, score_date, score_pair, score_reference
from app.blocking import Candidate


@pytest.fixture(scope="module")
def batch():
    return load_batch(Split.HELDOUT)


@pytest.fixture(scope="module")
def truth():
    return load_truth(Split.HELDOUT)


@pytest.fixture(scope="module")
def result():
    return run(Split.HELDOUT)


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


# --- a UTR is not a reference ----------------------------------------------


def test_a_utr_is_not_invoice_shaped():
    assert not looks_like_invoice_ref("KKBK290665407983")
    assert not looks_like_invoice_ref("912345678901")


def test_an_invoice_number_is_invoice_shaped():
    assert looks_like_invoice_ref("INV-0231")
    assert looks_like_invoice_ref("INV/0231")


def test_a_bare_utr_scores_none_not_zero():
    """A UTR is on every payment and says nothing about which bill it settles.

    Scoring it as a non-matching reference caps a perfect match at 0.49 - the
    exact failure renormalisation exists to prevent, arriving by another door.
    """
    assert score_reference(_invoice(), _txn()) is None


def test_a_different_invoice_number_scores_zero():
    """That is real evidence against, not absence of evidence."""
    txn = _txn(refs=("INV-0777",), description_raw="NEFT/ABC/INV-0777")
    assert score_reference(_invoice(), txn) == 0.0


def test_the_right_invoice_number_scores_one():
    txn = _txn(refs=("INV-0001",))
    assert score_reference(_invoice(), txn) == 1.0


# --- renormalisation --------------------------------------------------------


def test_a_missing_signal_shrinks_the_denominator(batch):
    """The plan's own worked example, section 6.2.

    Right name, gap explained exactly by 2% TDS, paid on time, no reference.
    Treating the missing reference as a zero scores it 0.488 and files a
    perfect match as an exception. Renormalising scores it near 0.975.
    """
    invoice = _invoice()
    txn = _txn(amount_paise=rupees(9_800), refs=(), description_raw="NEFT/ABCTECHNOLOGIES")
    scored = score_pair(invoice, Candidate(txn=txn, passes={Pass.ONE_TO_ONE}), batch, BASE_WEIGHTS)

    assert scored.signals.reference is None
    assert scored.score > 0.90, "a missing reference must not sink a perfect match"

    naive = sum(
        (value or 0.0) * BASE_WEIGHTS[key]
        for key, value in (
            ("reference", scored.signals.reference),
            ("amount", scored.signals.amount),
            ("name", scored.signals.name),
            ("date", scored.signals.date),
        )
    )
    assert naive < 0.55, "without renormalisation this pair would be an exception"


def test_available_weights_sum_to_one(batch):
    invoice = _invoice()
    txn = _txn(refs=(), description_raw="NEFT/ABCTECHNOLOGIES")
    scored = score_pair(invoice, Candidate(txn=txn, passes={Pass.ONE_TO_ONE}), batch)
    assert scored.signals.reference is None
    assert sum(scored.weights_used.values()) == pytest.approx(1.0)


# --- date asymmetry ---------------------------------------------------------


def test_late_is_normal_and_early_is_odd():
    invoice = _invoice()
    on_time = _txn(value_date=invoice.due_date + timedelta(days=5))
    late = _txn(value_date=invoice.due_date + timedelta(days=40))
    too_early = _txn(value_date=invoice.due_date - timedelta(days=30))

    assert score_date(invoice, on_time) == 1.0
    assert 0.0 < score_date(invoice, late) < 1.0
    assert score_date(invoice, too_early) == 0.0


# --- blocking ---------------------------------------------------------------


def test_blocking_never_drops_a_true_payment(batch, truth):
    """If the right payment is filtered out here, nothing downstream can recover it."""
    missed = []
    for invoice in batch.invoices:
        found = {c.id for c in block(invoice, batch)}
        for expected in truth[invoice.id].expected_txn_ids:
            if expected not in found:
                missed.append((invoice.id, invoice.scenario, expected))
    assert not missed, f"blocking dropped {len(missed)} true payments: {missed[:5]}"


def test_blocking_stays_narrow(batch):
    """Blocking exists to cut work. If it returns everything it has failed."""
    sizes = [len(block(i, batch)) for i in batch.invoices]
    assert max(sizes) < len(batch.transactions) / 2


def test_a_lopsided_combined_payment_is_still_a_candidate(batch, truth):
    """One payment settling a small bill and a large one is 7.45x the small
    one, so the plan's 5.0x ceiling dropped it. Section 18, bug 12."""
    combined = [i for i in batch.invoices if i.scenario == "combined_payment"]
    for invoice in combined:
        found = {c.id for c in block(invoice, batch)}
        assert set(truth[invoice.id].expected_txn_ids) <= found


# --- margin -----------------------------------------------------------------


def test_a_sole_candidate_gets_an_explicit_margin(batch):
    """Never null. A null comparison here silently passes or silently fails
    every fast-path record. Section 18, bug 3."""
    invoice = _invoice()
    ranking = rank(invoice, [Candidate(txn=_txn(), passes={Pass.ONE_TO_ONE})], batch)
    assert ranking.margin == 1.0
    assert ranking.margin_basis == "sole_candidate"


def test_duplicate_payments_collapse_the_margin(result):
    """Two identical payments for one bill is what margin actually catches."""
    for invoice_id, ranking in result.rankings.items():
        if ranking.invoice.scenario == "duplicate_transaction":
            assert ranking.margin < 0.05, f"{invoice_id} margin {ranking.margin}"


# --- group shapes -----------------------------------------------------------


def test_a_partial_payment_names_every_instalment(result, truth):
    """A late instalment scores below the bar on its own. Filtering by
    individual score would claim the bill was settled by the first one."""
    for decision in result.decisions:
        if decision.scenario == "partial_payment" and decision.outcome is Outcome.AUTO:
            expected = set(truth[decision.invoice_id].expected_txn_ids)
            assert set(decision.txn_ids) == expected
            assert len(decision.txn_ids) >= 2


def test_a_combined_payment_is_not_scored_as_a_failed_one_to_one(result):
    """Without the combined pass the amount signal is 0.0 and every combined
    payment becomes an exception the LLM never sees. Plan section 6.8."""
    for ranking in result.rankings.values():
        if ranking.invoice.scenario == "combined_payment":
            assert ranking.best is not None
            assert ranking.best.amount.score >= 0.85


# --- the eval harness itself ------------------------------------------------


def _judgement(**kw) -> Judgement:
    base = dict(
        invoice_id="INV-0001",
        scenario="test",
        amount_paise=rupees(1000),
        expected=Outcome.AUTO,
        actual=Outcome.AUTO,
        expected_txns=["TXN-0001"],
        actual_txns=["TXN-0001"],
        expected_reason="MATCHED_REFERENCE",
        actual_reason="MATCHED_REFERENCE",
    )
    return Judgement(**{**base, **kw})


def test_automating_a_record_that_needed_a_human_is_a_false_approval():
    assert _judgement(expected=Outcome.REVIEW).false_auto_approval
    assert _judgement(expected=Outcome.AMBIGUOUS).false_auto_approval
    assert _judgement(expected=Outcome.EXCEPTION).false_auto_approval


def test_automating_the_wrong_payment_is_a_false_approval():
    assert _judgement(actual_txns=["TXN-9999"]).false_auto_approval


def test_holding_a_record_back_is_never_a_false_approval():
    assert not _judgement(actual=Outcome.EXCEPTION, expected=Outcome.EXCEPTION).false_auto_approval


def test_an_exceptions_candidate_list_is_evidence_not_a_claim():
    """We record the closest candidate on a refusal so a human can see it.
    Grading that as a wrong match made every correct refusal look like a
    failure."""
    j = _judgement(
        expected=Outcome.EXCEPTION,
        actual=Outcome.EXCEPTION,
        expected_txns=[],
        actual_txns=["TXN-0001"],
    )
    assert j.correct
    assert not j.false_auto_approval


# --- what Phase 2 established -----------------------------------------------
#
# The pipeline now carries Phase 3's guardrails, so the straight-through rate
# it produces is no longer Phase 2's number. What still belongs here is the
# thing Phase 2 was responsible for: the scorer must never match the wrong
# payment. Every remaining refusal is a rule's decision, not a scoring error.


def test_the_scorer_never_matches_the_wrong_payment(result, truth):
    metrics = evaluate(result, truth)
    assert metrics.wrong_transaction_approvals == []


def test_no_scenario_is_completely_broken(result, truth):
    metrics = evaluate(result, truth)
    for scenario, (right, total, _) in metrics.by_scenario().items():
        assert right / total >= 0.75, f"{scenario} only {right}/{total}"
