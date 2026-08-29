"""A frozen set of tricky decisions. Plan section 8.6.

Twenty records, one per scenario plus the hardest of the rest, with the exact
decision the pipeline made frozen into a file. Run after every change: if a
case that used to pass stops passing, the last edit did it.

This is ordinary unit testing. The only difference is that the unit is a whole
pipeline decision rather than a function.

It is deliberately **not** the same thing as the eval. The eval asks "were we
right"; this asks "did anything change". A refactor that improves a number
should still show up here, so it gets looked at rather than absorbed.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from app.dataset import Split
from app.memory import build_from_split
from app.intake import load_batch
from app.pipeline import RunResult, process_batch

FROZEN = Path(__file__).parent.parent / "tests" / "frozen_decisions.json"

# One from every scenario, so no branch of the pipeline can change unnoticed.
PER_SCENARIO = 2


@dataclass
class Drift:
    invoice_id: str
    field: str
    was: str
    now: str

    def __str__(self) -> str:
        return f"{self.invoice_id}  {self.field}: {self.was} -> {self.now}"


def snapshot(result: RunResult, invoices_by_scenario: dict[str, list[str]]) -> dict:
    """The decisions worth freezing, keyed by invoice."""
    wanted: set[str] = set()
    for ids in invoices_by_scenario.values():
        wanted.update(sorted(ids)[:PER_SCENARIO])

    frozen = {}
    for decision in result.decisions:
        if decision.invoice_id not in wanted:
            continue
        frozen[decision.invoice_id] = {
            "scenario": decision.scenario,
            "outcome": decision.outcome.value,
            "reason_code": decision.reason_code.value if decision.reason_code else None,
            "txn_ids": sorted(decision.txn_ids),
            # Scores move with any weight change, so they are recorded to two
            # places - enough to catch a real shift, loose enough not to fail
            # on floating point noise.
            "score": round(decision.score, 2),
            "margin": round(decision.margin, 2),
        }
    return frozen


def current(split: Split = Split.HELDOUT) -> dict:
    """Freeze without the LLM. The adjudicator is non-deterministic by nature,
    and this file exists to catch changes we did not intend."""
    batch = load_batch(split)
    result = process_batch(batch, memory=build_from_split())

    by_scenario: dict[str, list[str]] = {}
    for invoice in batch.invoices:
        by_scenario.setdefault(invoice.scenario, []).append(invoice.id)

    return snapshot(result, by_scenario)


def load() -> dict:
    if not FROZEN.exists():
        return {}
    return json.loads(FROZEN.read_text())


def save(frozen: dict) -> int:
    FROZEN.parent.mkdir(parents=True, exist_ok=True)
    FROZEN.write_text(json.dumps(frozen, indent=2, sort_keys=True) + "\n")
    return len(frozen)


def compare(frozen: dict, now: dict) -> list[Drift]:
    drifts: list[Drift] = []

    for invoice_id, before in sorted(frozen.items()):
        after = now.get(invoice_id)
        if after is None:
            drifts.append(Drift(invoice_id, "record", "present", "missing"))
            continue
        for field in ("outcome", "reason_code", "txn_ids", "score", "margin"):
            if before.get(field) != after.get(field):
                drifts.append(
                    Drift(invoice_id, field, str(before.get(field)), str(after.get(field)))
                )

    for invoice_id in sorted(set(now) - set(frozen)):
        drifts.append(Drift(invoice_id, "record", "missing", "present"))

    return drifts
