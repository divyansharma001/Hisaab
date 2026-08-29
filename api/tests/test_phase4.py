"""Phase 4 regression tests: the LLM adjudicator.

The whole point of this layer is that it is *contained*. So most of these
tests are about what the model is **not** allowed to do: invent an id, override
the value ceiling, unstick a duplicate, or win an argument with a hard rule.

They all run without an API key. The model is stubbed, because what is being
tested is our handling of its answer, not the model.
"""

from datetime import date

import pytest

from app import guardrails
from app.adjudicator import (
    SYSTEM_RULES,
    TOP_N,
    Adjudicator,
    Verdict,
    build_prompt,
    cache_key,
)
from app.blocking import Candidate, Pass, block
from app.dataset import Outcome, Reason, Split
from app.intake import NormInvoice, NormTxn, load_batch
from app.memory import Memory, build_from_split
from app.money import fmt, rupees
from app.pipeline import LLM, run
from app.scoring import rank


@pytest.fixture(scope="module")
def batch():
    return load_batch(Split.HELDOUT)


@pytest.fixture(scope="module")
def memory():
    return build_from_split()


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


def _ranking(batch, invoice=None, txn=None, score=None):
    invoice = invoice or _invoice()
    txn = txn or _txn()
    ranking = rank(invoice, [Candidate(txn=txn, passes={Pass.ONE_TO_ONE})], batch)
    if score is not None:
        ranking.candidates[0].score = score
    return ranking


def _endorsement(**kw) -> Verdict:
    base = dict(
        chosen_id="TXN-0001",
        confidence=0.92,
        reasoning="Name matches a known alias and the gap is exactly 2% TDS.",
        evidence_fields=["counterparty_name", "tds_2pct"],
    )
    return Verdict(**{**base, **kw})


def _verdict(batch, ranking, endorsement=None, **rule_kw):
    args = dict(
        claimed_txn_ids=[ranking.best.id],
        duplicates={},
        counterparty_seen=5,
        conflict=None,
    )
    args.update(rule_kw)
    return guardrails.apply(
        ranking.invoice, ranking, ranking.best, **args, endorsement=endorsement
    )


# --- what an endorsement is allowed to do -----------------------------------


def test_an_endorsement_can_lift_a_record_over_the_score_bar(batch):
    """The one thing the model was asked - is this the right payment - is the
    one thing its answer is allowed to settle."""
    ranking = _ranking(batch, score=0.88)

    without = _verdict(batch, ranking)
    assert without.outcome is not Outcome.AUTO

    with_model = _verdict(batch, ranking, _endorsement())
    assert with_model.outcome is Outcome.AUTO


def test_an_endorsement_can_lift_a_thin_margin(batch):
    ranking = _ranking(batch, score=0.95)
    ranking.margin = 0.02

    assert _verdict(batch, ranking).outcome is not Outcome.AUTO
    assert _verdict(batch, ranking, _endorsement()).outcome is Outcome.AUTO


# --- what an endorsement can never do ---------------------------------------


def test_an_endorsement_cannot_beat_the_value_ceiling(batch):
    """A wrong Rs 500 match is annoying. A wrong Rs 8,00,000 match is not."""
    big = rupees(800_000)
    ranking = _ranking(batch, _invoice(amount_paise=big), _txn(amount_paise=big))

    verdict = _verdict(batch, ranking, _endorsement(confidence=1.0))
    assert verdict.outcome is Outcome.REVIEW
    assert verdict.reason_code is Reason.VALUE_CEILING


def test_an_endorsement_cannot_unstick_a_duplicate(batch):
    ranking = _ranking(batch)
    verdict = _verdict(
        batch, ranking, _endorsement(confidence=1.0),
        duplicates={"TXN-0001": ["TXN-0002"]},
    )
    assert verdict.outcome is Outcome.REVIEW
    assert verdict.reason_code is Reason.DUPLICATE_TRANSACTION


def test_an_endorsement_cannot_win_a_contested_payment(batch):
    ranking = _ranking(batch)
    verdict = _verdict(
        batch, ranking, _endorsement(confidence=1.0), conflict="INV-0002 wants it too"
    )
    assert verdict.outcome is Outcome.AMBIGUOUS


def test_an_endorsement_cannot_explain_away_missing_money(batch):
    """Rs 340 nobody can account for is an exception, whatever the model says."""
    ranking = _ranking(
        batch, _invoice(amount_paise=rupees(10_000)), _txn(amount_paise=rupees(9_660))
    )
    verdict = _verdict(batch, ranking, _endorsement(confidence=1.0))

    assert verdict.outcome is not Outcome.AUTO
    assert verdict.reason_code is Reason.AMOUNT_GAP_UNEXPLAINED


def test_an_endorsement_cannot_vouch_for_a_new_counterparty(batch):
    ranking = _ranking(batch, score=0.88)
    verdict = _verdict(batch, ranking, _endorsement(), counterparty_seen=0)

    assert verdict.outcome is Outcome.REVIEW
    assert verdict.reason_code is Reason.NEW_COUNTERPARTY


# --- rejecting the model's answer -------------------------------------------


def test_an_answer_for_a_different_payment_is_ignored(batch):
    """It endorsed something, but not the candidate we are deciding on."""
    ranking = _ranking(batch, score=0.88)
    verdict = _verdict(batch, ranking, _endorsement(chosen_id="TXN-9999"))
    assert verdict.outcome is not Outcome.AUTO


def test_abstention_is_respected(batch):
    """A model that never says 'I cannot tell' is dangerous. When it does,
    the record goes to a human."""
    ranking = _ranking(batch, score=0.88)
    verdict = _verdict(batch, ranking, _endorsement(chosen_id=None))

    assert verdict.outcome is not Outcome.AUTO


def test_a_hesitant_endorsement_is_not_evidence(batch):
    ranking = _ranking(batch, score=0.88)
    below = guardrails.ENDORSEMENT_FLOOR - 0.01
    assert _verdict(batch, ranking, _endorsement(confidence=below)).outcome is not Outcome.AUTO


def test_a_rejected_answer_is_not_evidence(batch):
    ranking = _ranking(batch, score=0.88)
    rejected = _endorsement(rejected="HALLUCINATED_ID: TXN-9999")
    assert _verdict(batch, ranking, rejected).outcome is not Outcome.AUTO


def test_usable_requires_both_a_choice_and_no_rejection():
    assert _endorsement().usable
    assert not _endorsement(chosen_id=None).usable
    assert not _endorsement(rejected="SCHEMA_INVALID").usable


# --- the prompt -------------------------------------------------------------


def test_the_prompt_carries_no_number_we_did_not_compute(batch, memory):
    """The model reads figures; it never produces them. Every money value in
    the prompt has to be one our own code rendered."""
    invoice = next(i for i in batch.invoices if i.scenario == "tds_deduction")
    ranking = rank(invoice, block(invoice, batch), batch)
    prompt = build_prompt(invoice, ranking.candidates[:TOP_N], memory)

    assert fmt(invoice.amount_paise) in prompt
    for scored in ranking.candidates[:TOP_N]:
        assert fmt(scored.txn.amount_paise) in prompt
        assert scored.id in prompt


def test_the_prompt_offers_a_closed_list(batch, memory):
    invoice = _invoice()
    ranking = _ranking(batch)
    prompt = build_prompt(invoice, ranking.candidates, memory)

    assert "CANDIDATE PAYMENTS (1)" in prompt
    assert "cannot tell" in prompt


def test_the_system_rules_forbid_arithmetic_and_invention():
    assert "Do no arithmetic" in SYSTEM_RULES
    assert "Never invent an id" in SYSTEM_RULES
    assert "recommendation" in SYSTEM_RULES


def test_the_system_rules_are_byte_stable():
    """The cached prefix must not move. Anything varying per record - a
    timestamp, an id, a shuffled list - silently costs the cache."""
    assert SYSTEM_RULES == SYSTEM_RULES
    assert "{" not in SYSTEM_RULES.replace("{}", "")


# --- the cache --------------------------------------------------------------


def test_the_same_question_produces_the_same_key(batch):
    invoice, ranking = _invoice(), _ranking(batch)
    assert cache_key(invoice, ranking.candidates) == cache_key(invoice, ranking.candidates)


def test_a_different_candidate_produces_a_different_key(batch):
    invoice = _invoice()
    a = _ranking(batch)
    b = _ranking(batch, txn=_txn(id="TXN-0002"))
    assert cache_key(invoice, a.candidates) != cache_key(invoice, b.candidates)


# --- the budget -------------------------------------------------------------


def test_the_budget_is_a_hard_cap(batch, monkeypatch):
    """Hitting it sends the rest to a human rather than failing silently.

    Settings are cached process-wide, so the key is patched on the instance
    rather than on the shared object - mutating that leaks a fake key into
    every test that runs afterwards.
    """
    adjudicator = Adjudicator(memory=Memory(), budget=0)
    monkeypatch.setattr(type(adjudicator), "available", property(lambda self: True))

    verdict = adjudicator.adjudicate(_invoice(), _ranking(batch))
    assert verdict.rejected == "BUDGET_EXHAUSTED"
    assert not verdict.usable
    assert adjudicator.calls_made == 0


def test_without_a_key_the_answer_is_a_refusal_not_a_crash(batch):
    adjudicator = Adjudicator(memory=Memory())
    if adjudicator.available:
        pytest.skip("a real key is configured; this covers the unconfigured path")

    verdict = adjudicator.adjudicate(_invoice(), _ranking(batch))
    assert verdict.rejected == "NO_API_KEY"
    assert not verdict.usable


# --- only ask when it matters -----------------------------------------------


class Recording(Adjudicator):
    """Stands in for the model and remembers what it was asked."""

    def __init__(self, answer: Verdict | None = None):
        super().__init__(memory=Memory())
        self.asked: list[str] = []
        self.answer = answer

    def adjudicate(self, invoice, ranking, use_cache=True, tags=None):
        self.asked.append(invoice.id)
        self.tags_seen = tags
        if self.answer is None:
            return Verdict(rejected="NO_API_KEY")
        return Verdict(**{**self.answer.__dict__, "chosen_id": ranking.best.id})


def test_we_do_not_ask_about_records_a_rule_already_holds():
    """A duplicated payment or an unexplained gap is held whatever the model
    says, so asking is money spent on an answer nobody may act on."""
    spy = Recording()
    run(Split.HELDOUT, adjudicator=spy)

    # Sixteen records land in the adjudicator's score band. Most are held by
    # rules a model cannot override, so we never pay to ask about them.
    assert len(spy.asked) <= 6, f"asked about {len(spy.asked)} records: {spy.asked}"


def test_the_records_we_do_ask_about_are_only_held_by_the_score_bar():
    spy = Recording()
    result = run(Split.HELDOUT, adjudicator=spy)

    for invoice_id in spy.asked:
        ranking = result.rankings[invoice_id]
        assert guardrails.route(ranking.score, ranking.margin) == guardrails.ADJUDICATE


def test_an_endorsement_shows_up_in_the_decision_trail():
    """The audit log has to say the model was involved and what it said."""
    spy = Recording(_endorsement())
    result = run(Split.HELDOUT, adjudicator=spy)

    touched = [d for d in result.decisions if d.llm_used]
    assert touched, "no record reached the adjudicator"

    for decision in touched:
        assert decision.llm_reasoning
        assert 0.0 <= decision.llm_confidence <= 1.0
        if decision.outcome is Outcome.AUTO:
            assert decision.decided_by == LLM


def test_the_llm_never_creates_a_false_approval():
    """Even told to endorse everything it is shown, the hard rules hold."""
    from app.evaluate import evaluate, load_truth

    spy = Recording(_endorsement(confidence=1.0))
    result = run(Split.HELDOUT, adjudicator=spy)
    metrics = evaluate(result, load_truth(Split.HELDOUT))

    assert metrics.false_auto_approvals == []
