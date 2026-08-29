"""Writing a run's decisions to Postgres. Plan section 5, boxes 9 and 10.

Every decision lands in three places:

- **matches** and **match_allocations** - what we did with the money
- **exceptions** - what we refused to do, with a reason code and its evidence
- **audit_log** - every decision, every rule that passed or failed, append-only

The audit log is the compliance artifact, not a debug aid. In finance an
unchangeable record of why each decision was made is a legal requirement, and
it is also the thing that lets us explain a demo failure in ten seconds.

Allocations are written inside one transaction so the database's own sum
invariant gets to check our arithmetic. If the pipeline ever tries to allocate
more of a payment than arrived, the commit fails rather than the books being
quietly wrong.
"""

import json

import psycopg

from app.config import get_settings
from app.dataset import Outcome
from app.pipeline import Decision, RunResult

HELD = {Outcome.EXCEPTION, Outcome.REVIEW, Outcome.AMBIGUOUS}


def clear_run_tables(conn: psycopg.Connection) -> None:
    """A run replaces the last one. The audit log is the history, not these."""
    conn.execute("TRUNCATE match_allocations, matches, exceptions RESTART IDENTITY CASCADE")


def write_match(conn: psycopg.Connection, decision: Decision) -> None:
    if not decision.txn_ids:
        return

    for txn_id in decision.txn_ids:
        match_id = conn.execute(
            """INSERT INTO matches (transaction_id, score, margin, margin_basis,
                                    decision, decided_by)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                txn_id,
                decision.score,
                decision.margin,
                decision.margin_basis,
                decision.outcome.value,
                decision.decided_by,
            ),
        ).fetchone()[0]

        # Money only moves on an auto-approval. A held record records the
        # decision and the candidate, but allocates nothing.
        if decision.outcome is Outcome.AUTO and decision.allocated_paise > 0:
            share = _share_for(decision, txn_id)
            if share > 0:
                conn.execute(
                    """INSERT INTO match_allocations (match_id, invoice_id, allocated_paise)
                       VALUES (%s, %s, %s)""",
                    (match_id, decision.invoice_id, share),
                )


def _share_for(decision: Decision, txn_id: str) -> int:
    """How much of this payment goes to this invoice."""
    if len(decision.txn_ids) == 1:
        return decision.allocated_paise
    # A partial payment: each instalment is allocated in full, and the
    # per-payment figures were already worked out during assignment.
    return decision.allocations.get(txn_id, 0)


def write_exception(conn: psycopg.Connection, decision: Decision) -> None:
    if decision.outcome not in HELD:
        return

    conn.execute(
        """INSERT INTO exceptions (invoice_id, transaction_id, reason_code,
                                   reason_text, evidence_json)
           VALUES (%s, %s, %s, %s, %s)""",
        (
            decision.invoice_id,
            decision.txn_ids[0] if decision.txn_ids else None,
            decision.reason_code.value if decision.reason_code else "UNKNOWN",
            decision.reason_text,
            json.dumps(
                {
                    "score": decision.score,
                    "margin": decision.margin,
                    "margin_basis": decision.margin_basis,
                    "candidates": decision.txn_ids,
                    "rules_passed": decision.rules_passed,
                    "rules_failed": decision.rules_failed,
                    "outcome": decision.outcome.value,
                }
            ),
        ),
    )


def write_audit(conn: psycopg.Connection, run_id: str, decision: Decision) -> None:
    conn.execute(
        """INSERT INTO audit_log (run_id, record_id, stage, outcome, score, margin,
                                  rules_passed_json, rules_failed_json, llm_used)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            run_id,
            decision.invoice_id,
            decision.decided_by,
            decision.outcome.value,
            decision.score,
            decision.margin,
            json.dumps(decision.rules_passed),
            json.dumps(decision.rules_failed),
            False,   # no LLM until Phase 4
        ),
    )


def save(result: RunResult, database_url: str | None = None) -> dict[str, int]:
    url = database_url or get_settings().database_url
    counts = {"matches": 0, "allocations": 0, "exceptions": 0, "audit": 0}

    with psycopg.connect(url) as conn:
        clear_run_tables(conn)

        for decision in result.decisions:
            write_match(conn, decision)
            write_exception(conn, decision)
            write_audit(conn, result.run_id, decision)

        # Commit once, so the deferred sum invariant checks the whole batch.
        # If our allocations do not add up, this raises instead of writing
        # books that are quietly wrong.
        conn.commit()

        counts["matches"] = conn.execute("SELECT count(*) FROM matches").fetchone()[0]
        counts["allocations"] = conn.execute("SELECT count(*) FROM match_allocations").fetchone()[0]
        counts["exceptions"] = conn.execute("SELECT count(*) FROM exceptions").fetchone()[0]
        counts["audit"] = conn.execute(
            "SELECT count(*) FROM audit_log WHERE run_id = %s", (result.run_id,)
        ).fetchone()[0]

    return counts
