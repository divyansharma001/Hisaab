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

    for key in ("confirmed_in", "still_owed", "in_flight", "uncertain"):
        assert body[key]["paise"] >= 0
        assert body[key]["display"].startswith("Rs ")

    assert [b["label"] for b in body["aging"]] == [
        "0-30 days", "31-60 days", "61-90 days", "90+ days"
    ]


def test_the_aging_buckets_add_up_to_what_is_still_owed(client):
    body = client.get("/api/cash-position").json()
    bucketed = sum(b["value"]["paise"] for b in body["aging"])
    assert bucketed == body["still_owed"]["paise"]


def test_money_never_leaves_the_database_as_a_decimal():
    """Postgres returns SUM() over BIGINT as numeric, which arrives as a
    Decimal. Money is integer paise everywhere, and letting one through fails
    deep inside a format string saying nothing about where it came from."""
    position = cash_position(Split.HELDOUT)

    for value in (
        position.confirmed_in_paise,
        position.still_owed_paise,
        position.in_flight_paise,
        position.uncertain_paise,
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
