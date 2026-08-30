"""Phase 6 regression tests: the API and the cash position.

The rule this layer lives under: **it reads results, it never produces them.**
If the API is broken at hour twenty, the command line still proves the system
works. So these tests check the shapes the UI depends on, not the matching.
"""

import pytest
from fastapi.testclient import TestClient

from app.dataset import Split
from app.cash import cash_position
from app.main import app
from app.money import fmt


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# --- the six endpoints ------------------------------------------------------


def test_health_reports_the_provider(client):
    body = client.get("/api/health").json()
    assert body["database"] == "up"
    assert "llm_provider" in body and "llm_model" in body


def test_latest_run_carries_the_headline(client):
    body = client.get("/api/runs/latest").json()

    assert body["records"] == 85
    assert body["false_auto_approvals"] == 0
    assert body["auto_precision"] == 100.0
    assert 0 < body["straight_through_count"] <= 100
    assert body["value_held"]["display"].startswith("Rs ")


def test_exceptions_all_name_a_reason(client):
    """Good: AMOUNT_GAP_UNEXPLAINED with the figure. Useless: low confidence."""
    body = client.get("/api/exceptions").json()

    assert body["count"] > 0
    for row in body["exceptions"]:
        assert row["reason_code"], row
        assert len(row["reason_text"]) > 10, row
        assert row["outcome"] != "AUTO"


def test_exceptions_lead_with_the_biggest_money(client):
    amounts = [r["amount"]["paise"] for r in client.get("/api/exceptions").json()["exceptions"]]
    assert amounts == sorted(amounts, reverse=True)


def test_a_record_carries_its_whole_decision_trail(client):
    body = client.get("/api/records/INV-0001").json()

    assert body["invoice"]["id"] == "INV-0001"
    assert body["decision"]["outcome"]
    assert body["rules"], "the trace needs every rule, ticked or crossed"
    assert body["candidates"], "and the payments it chose between"
    assert body["adjudicator"]["note"] == "a recommendation, not a decision"


def test_every_rule_is_shown_either_way(client):
    body = client.get("/api/records/INV-0001").json()
    names = {r["name"] for r in body["rules"]}
    assert {"score", "margin", "value ceiling", "not a duplicate"} <= names


def test_an_unknown_record_is_a_404(client):
    assert client.get("/api/records/INV-NOPE").status_code == 404


def test_the_eval_breakdown_is_per_scenario(client):
    body = client.get("/api/eval").json()
    scenarios = {s["scenario"] for s in body["scenarios"]}

    assert len(scenarios) == 14
    assert all(s["false_approvals"] == 0 for s in body["scenarios"])


def test_a_run_can_be_triggered(client):
    body = client.post("/api/runs", params={"split": "heldout", "use_llm": False}).json()
    assert body["records"] == 85
    assert body["false_auto_approvals"] == 0


def test_an_unknown_split_is_rejected(client):
    assert client.post("/api/runs", params={"split": "nonsense"}).status_code == 400


# --- the cash position ------------------------------------------------------


def test_cash_position_is_four_numbers_and_an_aging_split(client):
    body = client.get("/api/cash-position").json()

    for key in ("confirmed_in", "still_owed", "uncertain", "withheld"):
        assert body[key]["paise"] >= 0
        assert body[key]["display"].startswith("Rs ")

    assert [b["label"] for b in body["aging"]] == [
        "0-30 days", "31-60 days", "61-90 days", "90+ days"
    ]


def test_still_owed_and_uncertain_are_different_questions(client):
    """They were the same query once, so the panel showed one number twice
    under two labels. Both must be real, and they must not be the same set."""
    body = client.get("/api/cash-position").json()

    assert body["still_owed"]["paise"] > 0
    assert body["uncertain"]["paise"] > 0
    assert body["still_owed"]["paise"] != body["uncertain"]["paise"]


def test_the_aging_buckets_add_up_to_every_open_invoice(client):
    """Aging covers both open buckets. If a record could fall between them the
    totals would silently stop matching, so this is the check that catches it."""
    body = client.get("/api/cash-position").json()
    bucketed = sum(b["value"]["paise"] for b in body["aging"])
    assert bucketed == body["still_owed"]["paise"] + body["uncertain"]["paise"]


def test_withheld_is_its_own_parts(client):
    body = client.get("/api/cash-position").json()
    parts = body["withheld_split"]
    assert (
        parts["mdr"]["paise"] + parts["gst"]["paise"] + parts["tds"]["paise"]
        == body["withheld"]["paise"]
    )


def test_money_never_leaves_the_database_as_a_decimal():
    """Postgres returns SUM() over BIGINT as numeric, which arrives as a
    Decimal. Money is integer paise everywhere, and letting one through fails
    deep inside a format string saying nothing about where it came from."""
    position = cash_position(Split.HELDOUT)

    for value in (
        position.confirmed_in_paise,
        position.still_owed_paise,
        position.uncertain_paise,
        position.withheld_paise,
        position.mdr_paise,
        position.gst_paise,
        position.tds_paise,
    ):
        assert isinstance(value, int), type(value)
    for bucket in position.aging:
        assert isinstance(bucket.value_paise, int)
        assert isinstance(bucket.count, int)


def test_fmt_refuses_anything_that_is_not_integer_paise():
    from decimal import Decimal

    with pytest.raises(TypeError, match="integer paise"):
        fmt(Decimal("100"))
    with pytest.raises(TypeError):
        fmt(1.5)


# --- the static fallback ----------------------------------------------------


def test_the_snapshot_holds_everything_the_ui_reads():
    """The UI falls back to this file when the API is unreachable, so it has
    to answer every call the UI makes. A missing key would show up as a blank
    screen five minutes before presenting."""
    import json
    from pathlib import Path

    path = Path(__file__).parent.parent.parent / "web" / "public" / "results.json"
    if not path.exists():
        pytest.skip("no snapshot yet; run snapshot.py")

    payload = json.loads(path.read_text())

    # Every key api.ts asks for.
    for key in ("summary", "exceptions", "eval", "cash"):
        assert key in payload, key

    assert payload["summary"]["records"] == 85
    assert payload["cash"]["aging"]
    assert payload["exceptions"]["exceptions"]

    # Traces for the records the demo actually opens.
    traces = [k for k in payload if k.startswith("trace_")]
    assert traces, "no decision traces in the snapshot"

    held = {f"trace_{r['invoice_id']}" for r in payload["exceptions"]["exceptions"]}
    assert held <= set(traces), "an exception row has no trace to open"


# --- the records the model touched ------------------------------------------


def test_adjudicated_lists_only_records_the_model_saw(client):
    """The UI had no way to reach a record the adjudicator touched, because
    every one it touched was auto-approved and the exception list was the only
    route into the trace screen. The screen that proves the model earns its
    place was unreachable from the app."""
    body = client.get("/api/adjudicated").json()

    assert body["count"] == len(body["records"])
    assert body["of_total"] >= body["count"]
    for row in body["records"]:
        assert row["invoice_id"].startswith("INV-")
        assert 0.0 <= row["confidence"] <= 1.0


def test_every_adjudicated_record_opens_in_the_trace_screen(client):
    """The whole point of the panel is that these are clickable. A row that
    404s on the way to the trace is worse than no row."""
    for row in client.get("/api/adjudicated").json()["records"]:
        trace = client.get(f"/api/records/{row['invoice_id']}")
        assert trace.status_code == 200, row["invoice_id"]
        assert trace.json()["adjudicator"]["used"] is True


def test_days_from_due_reads_as_english():
    """A reviewer should not have to work out what a minus sign means."""
    from app.guardrails import days_from_due

    assert days_from_due(0) == "paid on the due date"
    assert days_from_due(5) == "5 days after the due date"
    assert days_from_due(-56) == "56 days before the due date"
    assert "-" not in days_from_due(-56)


# --- the offline snapshot has to match the live shape ----------------------


def test_snapshot_has_every_field_the_live_api_returns(client):
    """The snapshot is a second copy of the API contract, and it drifts.

    Adding a field to an endpoint and forgetting to rebuild the file leaves
    the UI crashing the moment it falls back - which is exactly when it is
    least affordable. Comparing keys here turns that into a failed test
    instead of a blank screen.
    """
    import json
    from pathlib import Path

    snapshot_file = Path("/web/public/results.json")
    if not snapshot_file.exists():
        pytest.skip("no snapshot built; run snapshot.py")

    saved = json.loads(snapshot_file.read_text())

    for key, path in (
        ("summary", "/api/runs/latest"),
        ("exceptions", "/api/exceptions"),
        ("eval", "/api/eval"),
        ("cash", "/api/cash-position"),
        ("adjudicated", "/api/adjudicated"),
        ("thresholds", "/api/thresholds"),
    ):
        assert key in saved, f"snapshot is missing {key}"
        live = client.get(path).json()
        missing = set(live) - set(saved[key])
        assert not missing, f"snapshot {key} is missing {sorted(missing)}; rebuild it"
