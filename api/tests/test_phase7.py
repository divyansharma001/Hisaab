"""Phase 7: the winning-margin work. Plan section 19."""

import pytest
from fastapi.testclient import TestClient

from app import qa
from app.dataset import Split
from app.main import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


# --- 19.4 settlement Q&A ----------------------------------------------------


def test_finds_the_invoice_however_it_is_written():
    for written in ("INV-0053", "inv-0053", "inv0053", "INV_53", "why inv 53 though"):
        assert qa.find_invoice_id(written) == "INV-0053", written


def test_a_question_with_no_invoice_asks_for_one():
    answer = qa.ask("why is my money missing")
    assert answer.rejected == "NO_INVOICE_IN_QUESTION"
    assert "INV-" in answer.text


def test_an_unknown_invoice_says_so_rather_than_guessing():
    answer = qa.ask("what happened to INV-9999?")
    assert answer.rejected == "UNKNOWN_INVOICE"
    assert "INV-9999" in answer.text


def test_gather_returns_nothing_for_an_invoice_that_does_not_exist():
    assert qa.gather("INV-9999") is None


def test_gather_collects_the_settlement_working():
    """The subtraction is written out in the facts, so the model can copy it
    instead of doing it."""
    facts = qa.gather("INV-0037")
    assert facts is not None
    assert any("full working" in line for line in facts.lines)
    assert facts.amounts, "no amounts collected; nothing could be checked"


# --- the guardrail that makes the answer trustworthy ------------------------


def test_an_amount_we_never_supplied_is_caught():
    """This is the whole point of the feature.

    A model that quietly does its own arithmetic produces a number the reader
    cannot check. Right or wrong, we do not show it.
    """
    allowed = {14965000, 299300, 14665700}  # paise

    clean = "You received Rs 1,46,657.00 after Rs 2,993.00 of TDS."
    assert qa.invented_amounts(clean, allowed) == []

    made_up = "You received Rs 1,46,657.00 after a fee of Rs 4,500.00."
    assert qa.invented_amounts(made_up, allowed) == ["4,500.00"]


def test_rupees_and_paise_are_both_accepted():
    """Facts hold paise, answers are written in rupees. Both forms of the same
    figure have to count as supplied, or every honest answer is rejected."""
    allowed = {976400}
    for written in ("Rs 9,764.00", "9764", "9,764"):
        assert qa.invented_amounts(written, allowed) == [], written


def test_small_numbers_are_not_treated_as_amounts():
    """Counts, day offsets and percentages are not money, and checking them
    would reject sentences like 'settled by 2 payments'."""
    assert qa.invented_amounts("settled by 2 payments, 14 days late", set()) == []


def test_a_rejected_answer_falls_back_to_the_ledger(monkeypatch):
    """When the model is thrown away the user still gets the truth, just less
    fluently written."""
    facts = qa.gather("INV-0037")
    assert facts is not None
    plain = qa.plain_answer(facts)

    assert "Invoice: INV-0037" in plain
    assert qa.invented_amounts(plain, facts.amounts) == []


# --- the endpoint -----------------------------------------------------------


def test_ask_endpoint_needs_a_question(client):
    assert client.post("/api/ask", json={"question": "   "}).status_code == 400


def test_ask_endpoint_answers_from_the_ledger(client):
    body = client.post("/api/ask", json={"question": "explain INV-0037"}).json()

    assert body["invoice_id"] == "INV-0037"
    assert body["facts"], "an answer with no facts behind it"
    assert body["answer"]

    # Whatever route the answer took - model or fallback - it may not contain
    # a rupee figure the ledger did not supply.
    facts = qa.gather("INV-0037")
    assert qa.invented_amounts(body["answer"], facts.amounts) == []


# --- 19.6 the threshold curve ----------------------------------------------


def test_the_sweep_restores_the_original_bar():
    """Leaving a test threshold bound would silently change every later
    decision in the process. That looks like a model regression for a week."""
    from app import guardrails
    from app.thresholds import sweep

    before = guardrails.AUTO_SCORE
    sweep(bars=(0.5, 0.95))
    assert guardrails.AUTO_SCORE == before


def test_the_bar_alone_never_causes_a_wrong_approval():
    """The plan predicted a 0.80 bar would produce two wrong approvals. It
    does not, and this pins that finding so it cannot quietly change back
    without someone noticing. Section 18, bug 14."""
    from app.thresholds import sweep

    for point in sweep(bars=(0.4, 0.8, 0.9)):
        assert point.wrong == 0, f"bar {point.bar} produced {point.wrong} wrong"


def test_turning_the_rules_off_does_cause_wrong_approvals():
    """The other half of the finding. If this ever passes with zero, the
    ablation is not measuring anything and the curve above means nothing."""
    from app.thresholds import sweep

    bare = sweep(bars=(0.9,), with_rules=False)[0]
    assert bare.wrong > 0, "score-only should be unsafe; the comparison is dead"
    assert bare.closed_on_their_own > 0


def test_thresholds_endpoint_shows_both_curves(client):
    body = client.get("/api/thresholds").json()

    assert body["with_rules"] and body["score_only"]
    assert body["current_bar"] == 0.90
    assert all(p["wrong"] == 0 for p in body["with_rules"])
    assert body["cost_of_the_rules"]["wrong_approvals_prevented"] > 0


# --- 19.1 the ablation table ------------------------------------------------


def test_each_layer_is_a_real_run(client):
    body = client.get("/api/ablation").json()
    layers = [r["layer"] for r in body["rows"]]

    assert layers == [
        "Scoring alone, nothing remembered",
        "Plus our checks, still nothing remembered",
        "Plus what we remember - the system as shipped",
    ]
    assert body["rows"][0]["wrong"] > 0, "scoring alone should be unsafe"
    assert body["rows"][-1]["wrong"] == 0, "the shipped config must be safe"
    for row in body["rows"]:
        assert row["explanation"], f"{row['layer']} has a number with no explanation"


# --- 19.3 the learning loop -------------------------------------------------


@pytest.fixture
def scratch_alias():
    """Remove anything a confirmation test wrote.

    These tests insert into the real aliases table, and a row left behind
    would quietly join the memory of every later run in this database.
    """
    import psycopg

    from app.config import get_settings

    yield
    with psycopg.connect(get_settings().database_url) as conn:
        conn.execute("DELETE FROM aliases WHERE canonical_name LIKE 'TEST %%'")
        conn.commit()


def test_confirming_a_graded_record_never_feeds_a_graded_run(scratch_alias):
    """The whole eval rests on this.

    A confirmation made while working the graded batch is kept, because the
    next real batch should have it. It must not come back into the run we
    score ourselves.
    """
    from app.memory import build_from_split, confirm_match

    confirm_match(
        canonical_name="TEST GRADED CO",
        bank_text="NEFT/TESTGRADEDCO/HDFC0001234",
        source_split=Split.HELDOUT,
    )
    assert "TEST GRADED CO" not in build_from_split().variants


def test_a_confirmation_from_a_safe_split_does_come_back(scratch_alias):
    """The other half. If this fails, confirming teaches nothing at all."""
    from app.memory import build_from_split, confirm_match

    confirm_match(
        canonical_name="TEST TUNING CO",
        bank_text="NEFT/TESTTUNINGCO/HDFC0001234",
        source_split=Split.TUNING,
    )
    assert "TEST TUNING CO" in build_from_split().variants


def test_the_learning_demo_actually_improves(client):
    """A demo that shows no change is not showing the loop."""
    body = client.get("/api/learning").json()

    assert body["confirmed"] > 0, "nothing was held to confirm; the demo is empty"
    assert body["closed_after"] > body["closed_before"]
    assert body["note"], "the thinned starting point must be stated on screen"


def test_confirm_reports_what_it_changed(client):
    body = client.post("/api/confirm/INV-0083").json()

    assert body["invoice_id"] == "INV-0083"
    assert "closes_now" in body and "closed_before" in body
    assert body["note"]


def test_confirming_a_record_with_no_payment_is_refused(client):
    assert client.post("/api/confirm/INV-9999").status_code == 404


# --- 19.8 the case we got wrong ---------------------------------------------


def test_mistakes_names_the_records_and_their_direction(client):
    body = client.get("/api/mistakes").json()

    for mistake in body["mistakes"]:
        assert mistake["we_said"] != mistake["answer_was"]
        assert mistake["erred_towards"] in {"holding it back", "closing it"}
        assert mistake["our_reason"]

    # Being too careful is recoverable; being too confident is not. If this
    # ever flips, the headline safety claim is no longer true.
    if body["mistakes"]:
        assert body["all_in_one_direction"] == all(
            m["erred_towards"] == "holding it back" for m in body["mistakes"]
        )
