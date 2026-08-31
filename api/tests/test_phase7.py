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


# --- the scratch set --------------------------------------------------------


def test_bank_plumbing_is_not_read_as_an_invoice_number():
    """An IFSC code is the branch, not the bill.

    Getting this wrong is expensive in a specific way: an unreadable reference
    is dropped and its weight shared out, but one we read and fail to match
    scores zero on the heaviest signal. Found by typing an ordinary narration
    into the scratch set - our generated data writes codes long enough that
    the digit test caught them, so it never showed up.
    """
    from app.names import looks_like_invoice_ref

    for ifsc in ("HDFC0001234", "ICIC0000123", "SBIN0000456", "kkbk0000321"):
        assert not looks_like_invoice_ref(ifsc), ifsc

    for utr in ("955640968266", "KKBK290665407983"):
        assert not looks_like_invoice_ref(utr), utr

    for invoice in ("INV-0083", "INV/2026/044", "12345"):
        assert looks_like_invoice_ref(invoice), invoice


def test_a_typed_amount_never_goes_through_a_float():
    """int(float("1234.35") * 100) is 123434. A reconciliation tool that
    loses a paise on entry has no business grading anyone's books."""
    from app.money import parse_amount

    assert parse_amount("1234.35") == 123435
    assert parse_amount("1,20,500.75") == 12050075
    assert parse_amount("Rs 9,764.00") == 976400
    assert parse_amount("0.01") == 1


def test_a_typed_amount_says_what_is_wrong_with_it():
    from app.money import parse_amount

    for bad in ("", "abc", "-5", "0", "1.234"):
        with pytest.raises(ValueError) as caught:
            parse_amount(bad)
        assert str(caught.value), f"{bad!r} rejected with no explanation"


def test_the_scratch_set_is_a_split_of_its_own(client):
    """It must not be scored, tuned on, or able to teach memory."""
    from app.memory import build_from_split

    assert Split.SANDBOX.is_graded is False
    with pytest.raises(ValueError):
        build_from_split(Split.SANDBOX)


def test_adding_and_clearing_leaves_the_graded_rows_alone(client):
    before = client.get("/api/runs/latest").json()["records"]

    client.post("/api/sandbox/invoices", json={"customer": "Test Co", "amount": "1000"})
    client.post(
        "/api/sandbox/payments",
        json={"bank_text": "NEFT/TESTCO/HDFC0001234", "amount": "980"},
    )
    listed = client.get("/api/sandbox").json()
    assert len(listed["invoices"]) >= 1 and len(listed["payments"]) >= 1

    matched = client.post("/api/sandbox/match").json()
    assert matched["ran"] is True
    assert "accuracy" not in matched, "a scratch run has no answer key to score"

    client.delete("/api/sandbox")
    assert client.get("/api/sandbox").json()["invoices"] == []
    assert client.get("/api/runs/latest").json()["records"] == before


def test_a_bad_entry_is_refused_with_a_readable_reason(client):
    body = client.post("/api/sandbox/invoices", json={"customer": "X", "amount": "1000"})
    assert body.status_code == 400
    assert "name" in body.json()["detail"].lower()

    body = client.post("/api/sandbox/invoices", json={"customer": "Real Co", "amount": "lots"})
    assert body.status_code == 400
    assert "amount" in body.json()["detail"].lower()


# --- bulk upload ------------------------------------------------------------


def test_reads_whatever_the_columns_are_called(client):
    """An accounting export says "Party Name", a bank says "Particulars".
    Demanding one shape means nobody can use their real file."""
    client.delete("/api/sandbox")
    body = client.post("/api/sandbox/upload", json={
        "kind": "invoices",
        "csv": 'Party Name,Invoice Amount,Due Date\n'
               'Sundaram Textiles,"2,40,000",31/08/2026\n'
               'Kaveri Foods,"98,500.50",31-08-2026\n',
    }).json()

    assert body["added"] == 2
    assert body["columns_used"]["customer"] == "Party Name"
    assert body["columns_used"]["amount"] == "Invoice Amount"
    # Indian grouping and two date formats, parsed to the paise.
    assert body["invoices"][1]["amount"]["paise"] == 9850050
    client.delete("/api/sandbox")


def test_one_bad_row_does_not_lose_the_file(client):
    """Rejecting the whole upload because line 5 is empty is the version of
    this nobody can use to fix their data."""
    client.delete("/api/sandbox")
    body = client.post("/api/sandbox/upload", json={
        "kind": "invoices",
        "csv": "customer,amount\nGood Co,1000\n,500\nAlso Good,2000\nBad Co,not a number\n",
    }).json()

    assert body["added"] == 2
    assert body["skipped"] == 2
    assert {p["line"] for p in body["problems"]} == {3, 5}
    assert all(p["problem"] for p in body["problems"]), "a rejection with no reason"
    client.delete("/api/sandbox")


def test_money_going_out_is_ignored_not_called_broken(client):
    """A bank statement is mostly debits. Counting them as errors makes an
    ordinary export look like it is full of them."""
    client.delete("/api/sandbox")
    body = client.post("/api/sandbox/upload", json={
        "kind": "payments",
        "csv": "Value Date,Particulars,Debit,Credit\n"
               '31/08/2026,NEFT/ACME/HDFC0001234,,"1,000.00"\n'
               '30/08/2026,ATM WDL SELF,"20,000.00",\n'
               ",,,\n",
    }).json()

    assert body["added"] == 1
    assert body["ignored"] == 1, "the debit should be ignored, not counted broken"
    assert body["skipped"] == 0, "a blank line is not an error"
    client.delete("/api/sandbox")


def test_a_file_we_cannot_read_says_which_columns_it_found(client):
    body = client.post("/api/sandbox/upload", json={
        "kind": "invoices", "csv": "foo,bar\n1,2\n",
    })
    assert body.status_code == 400
    detail = body.json()["detail"]
    assert "customer" in detail and "foo" in detail


def test_upload_refuses_an_absurd_file(client):
    body = client.post("/api/sandbox/upload", json={
        "kind": "invoices", "csv": "customer,amount\n" + "A Co,100\n" * 40000,
    })
    assert body.status_code == 400
    assert "large" in body.json()["detail"]
