"""Phase 5 regression tests: memory, and the freeze that keeps it honest.

Memory is the layer that most easily produces a fake number. If a batch learns
from itself, or reads back examples drawn from the records being graded, the
reported accuracy includes information the system never had. Most of this file
is about making that impossible rather than merely avoiding it.

The last section guards a bug that hid for a whole phase: the weight search
ran without memory, so every weighting scored identically and the search
returned a flat line that looked like a result.
"""

import psycopg
import pytest

from app.config import get_settings
from app.dataset import Outcome, Reason, Split
from app.evaluate import evaluate, load_truth
from app.intake import load_batch
from app.memory import (
    EPISODES_IN_PROMPT,
    Episode,
    Memory,
    build_from_split,
    case_tags,
    episode_from,
    learn_from,
    load_episodes,
    persist,
)
from app.pipeline import Decision, process_batch
from app.tune import search, sensitivity


@pytest.fixture(scope="module")
def memory():
    return build_from_split()


@pytest.fixture(scope="module")
def batch():
    return load_batch(Split.HELDOUT)


# --- the freeze -------------------------------------------------------------


def test_memory_refuses_to_be_seeded_from_the_graded_set():
    """Section 18, bug 7. The function will not do it at all."""
    with pytest.raises(ValueError):
        build_from_split(Split.HELDOUT)


def test_what_a_run_receives_is_frozen(memory):
    assert memory.frozen


def test_learning_returns_a_new_memory_rather_than_changing_this_one(memory, batch):
    """A batch that learned mid-flight would grade itself on what it just
    worked out. Returning a new object makes that impossible."""
    before_customers = len(memory)
    before_variants = {k: set(v) for k, v in memory.variants.items()}

    result = process_batch(batch, memory=memory)
    grown = learn_from(memory, result, batch.invoice_by_id(), batch.txn_by_id())

    assert len(memory) == before_customers, "the run's memory was mutated"
    assert memory.variants == before_variants
    assert grown is not memory


def test_learning_actually_learns(memory, batch):
    thin = Memory(
        variants={},
        confirmations={k: v for k, v in list(memory.confirmations.items())[:10]},
    )
    result = process_batch(batch, memory=thin.snapshot())
    grown = learn_from(thin, result, batch.invoice_by_id(), batch.txn_by_id())

    assert len(grown) >= len(thin)


# --- episodes ---------------------------------------------------------------


def test_a_graded_run_cannot_see_episodes_drawn_from_its_own_records():
    """The same contamination as an alias learned mid-run, one step removed.

    A case written from a graded record and shown back during grading would
    hand the model the answer and call the result accuracy.
    """
    url = get_settings().database_url
    with psycopg.connect(url) as conn:
        conn.execute(
            """INSERT INTO episodes (situation_text, resolution_text, tags, source_split)
               VALUES (%s, %s, %s, %s)""",
            ("a graded case", "should never be read back", ["TDS_2PCT"], "heldout"),
        )
        conn.commit()
        try:
            visible = load_episodes(conn, exclude=Split.HELDOUT)
            assert all(e.source_split is not Split.HELDOUT for e in visible)
            assert not any(e.situation == "a graded case" for e in visible)
        finally:
            conn.execute("DELETE FROM episodes WHERE situation_text = 'a graded case'")
            conn.commit()


def test_episodes_are_retrieved_by_shared_tags():
    store = Memory(
        episodes=[
            Episode("owed 100, got 98", "TDS_2PCT: tax withheld", ("TDS_2PCT", "below_bar")),
            Episode("owed 50, got 49", "MDR_GST: gateway fee", ("MDR_GST",)),
            Episode("owed 70, got 70", "MATCHED_REFERENCE", ("MATCHED_REFERENCE",)),
        ]
    )
    hits = store.episodes_for({"TDS_2PCT"})
    assert len(hits) == 1
    assert "tax withheld" in hits[0].resolution


def test_more_shared_tags_ranks_higher():
    store = Memory(
        episodes=[
            Episode("weak", "one tag", ("TDS_2PCT",)),
            Episode("strong", "two tags", ("TDS_2PCT", "below_bar")),
        ]
    )
    assert store.episodes_for({"TDS_2PCT", "below_bar"})[0].situation == "strong"


def test_no_tags_retrieves_nothing():
    store = Memory(episodes=[Episode("a", "b", ("TDS_2PCT",))])
    assert store.episodes_for(set()) == []


def test_retrieval_is_capped():
    store = Memory(episodes=[Episode(f"case {i}", "r", ("TDS_2PCT",)) for i in range(20)])
    assert len(store.episodes_for({"TDS_2PCT"})) == EPISODES_IN_PROMPT


def test_case_tags_use_nothing_from_the_answer_key():
    """The same tags have to be computable for a live record, so they can only
    come from what the pipeline itself worked out."""
    decision = Decision(
        invoice_id="INV-0001",
        outcome=Outcome.EXCEPTION,
        score=0.82,
        margin=0.04,
        reason_code=Reason.TDS_2PCT,
        rules_failed=["score", "amount explained"],
    )
    tags = case_tags(decision)
    assert "TDS_2PCT" in tags
    assert "thin_margin" in tags
    assert "below_bar" in tags
    assert "failed_amount_explained" in tags


def test_an_episode_records_where_it_came_from(batch):
    invoice = batch.invoices[0]
    txn = batch.transactions[0]
    decision = Decision(
        invoice_id=invoice.id,
        outcome=Outcome.AUTO,
        reason_code=Reason.MDR_GST,
        reason_text="gateway fee explained the gap",
    )
    episode = episode_from(decision, invoice, txn, Split.TUNING)

    assert episode.source_split is Split.TUNING
    assert invoice.name_clean in episode.situation
    assert "MDR_GST" in episode.resolution


# --- what memory is worth ---------------------------------------------------


def test_without_memory_nothing_can_be_automated(batch):
    """Every counterparty looks new, so the guardrail holds all of them.
    Guardrails without memory are useless - that is the ablation's middle row."""
    result = process_batch(batch, memory=Memory())
    assert [d for d in result.decisions if d.outcome is Outcome.AUTO] == []


def test_with_memory_most_records_are_automated(batch, memory):
    """Deterministic only - no adjudicator. The remaining few records sit just
    under the score bar and are what the LLM is for."""
    result = process_batch(batch, memory=memory)
    metrics = evaluate(result, load_truth(Split.HELDOUT))

    assert metrics.straight_through_rate > 65.0
    assert metrics.false_auto_approvals == []


def test_episodes_change_nothing_on_this_dataset(batch, memory):
    """Measured, and worth saying plainly.

    Only one record per batch reaches the adjudicator, and it resolves
    correctly without examples. There is no headroom for episodes to fill, so
    they are worth zero points here. Plan section 15.3 says to add a vector
    store only once we can point to a case SQL missed - we cannot even point
    to a case *episodes* helped.
    """
    with_examples = evaluate(
        process_batch(batch, memory=memory), load_truth(Split.HELDOUT)
    )
    without = Memory(
        variants=memory.variants, confirmations=memory.confirmations, episodes=[]
    ).snapshot()
    bare = evaluate(process_batch(batch, memory=without), load_truth(Split.HELDOUT))

    assert with_examples.straight_through_rate == bare.straight_through_rate
    assert with_examples.outcome_accuracy == bare.outcome_accuracy


# --- prior history has to cover every batch ---------------------------------


def test_prior_history_covers_the_tuning_customers_too(memory):
    """Covering only the graded customers looks right and is not.

    The tuning set is a real batch. With no history for its customers the
    new-counterparty rule holds all 45 records, and the weight search returns
    the same number for every weighting.
    """
    tuning = load_batch(Split.TUNING)
    for invoice in tuning.invoices:
        assert memory.seen(invoice.name_clean) >= 3, invoice.name_clean


def test_the_tuning_set_can_actually_be_automated(memory):
    tuning = load_batch(Split.TUNING)
    result = process_batch(tuning, memory=memory)
    automated = [d for d in result.decisions if d.outcome is Outcome.AUTO]
    assert len(automated) > 20, "the tuning set cannot measure anything if it is all held"


def test_the_weight_search_is_not_a_flat_line():
    """The regression guard for a bug that hid for a whole phase.

    `search` ran `process_batch` without memory, so every weighting was held by
    the new-counterparty rule and scored identically. 179 trials, 13.3%
    accuracy, 0.0 spread - a result-shaped output measuring nothing.
    """
    coarse = {
        "reference": [0.30, 0.50],
        "amount": [0.25],
        "name": [0.15],
        "date": [0.10],
    }
    trials = search(Split.TUNING, coarse)
    best = max(t.accuracy for t in trials)

    assert best > 50.0, f"the search tops out at {best}%, so it is measuring nothing"
    assert sensitivity(trials)[1] > 50.0


# --- the frozen regression set ----------------------------------------------


def test_no_decision_drifted_from_the_frozen_baseline():
    """Plan section 8.6. Ordinary unit testing, where the unit is a whole
    pipeline decision.

    This asks "did anything change", not "were we right" - the eval asks the
    second. A refactor that improves a number should still surface here, so it
    gets looked at rather than absorbed silently.
    """
    from app import regression

    frozen = regression.load()
    assert frozen, "no baseline; run freeze.py --write"

    drifts = regression.compare(frozen, regression.current())
    assert not drifts, "\n".join(str(d) for d in drifts)


def test_the_baseline_covers_every_scenario(batch):
    from app import regression

    frozen = regression.load()
    covered = {row["scenario"] for row in frozen.values()}
    assert covered == {i.scenario for i in batch.invoices}


def test_the_baseline_check_actually_detects_a_change():
    """A regression file that cannot fail is not a regression file."""
    from app import regression

    frozen = regression.load()
    key = sorted(frozen)[0]
    tampered = {**frozen, key: {**frozen[key], "outcome": "TAMPERED"}}

    assert regression.compare(tampered, regression.current())
