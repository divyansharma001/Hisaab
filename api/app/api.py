"""The six endpoints the UI reads. Plan section 16.4.

**This layer reads results. It never produces them.** The pipeline and the eval
harness run from the command line, and if this API is broken at hour twenty,
`python run_batch.py` and `python eval.py` still prove the system works. That
rule is what stops a frontend problem becoming a project problem.

The one exception is POST /api/runs, which triggers a batch. Even that just
calls the same function the command line does.
"""

from dataclasses import dataclass, field
from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.cash import cash_position
from app.dataset import Outcome, Split
from app.evaluate import Metrics, evaluate, load_truth
from app.intake import load_batch
from app.memory import build_from_split
from app.money import fmt, llm_cost_paise
from app.pipeline import RunResult, run
from app.persist import save

router = APIRouter(prefix="/api")


@dataclass
class RunCache:
    """The last run, held in memory so the UI is not re-running the pipeline
    on every page load. A batch takes seconds; a page load should not."""

    result: RunResult | None = None
    metrics: Metrics | None = None
    split: Split = Split.HELDOUT
    ran_at: str = ""

    def ensure(self) -> tuple[RunResult, Metrics]:
        if self.result is None or self.metrics is None:
            self.refresh(self.split)
        return self.result, self.metrics   # type: ignore[return-value]

    def refresh(self, split: Split, use_llm: bool = True) -> tuple[RunResult, Metrics]:
        result = run(split, use_llm=use_llm)
        metrics = evaluate(result, load_truth(split))
        save(result)

        self.result, self.metrics = result, metrics
        self.split = split
        self.ran_at = datetime.now().isoformat(timespec="seconds")
        return result, metrics


CACHE = RunCache()


def _money(paise: int) -> dict:
    """Both forms, so the UI never does currency formatting itself and never
    does arithmetic on a display string."""
    return {"paise": paise, "display": fmt(paise)}


@router.get("/runs/latest")
def latest_run() -> dict:
    """The headline numbers. Plan section 8.3."""
    result, metrics = CACHE.ensure()

    return {
        "run_id": result.run_id,
        "split": result.split.value,
        "ran_at": CACHE.ran_at,
        "records": metrics.total,
        "seconds": round(result.seconds, 2),
        "records_per_second": round(metrics.total / result.seconds, 1) if result.seconds else 0,
        "false_auto_approvals": len(metrics.false_auto_approvals),
        "auto_precision": round(metrics.auto_precision, 1),
        "straight_through_count": round(metrics.straight_through_rate, 1),
        "straight_through_value": round(metrics.straight_through_by_value, 1),
        "outcome_accuracy": round(metrics.outcome_accuracy, 1),
        "reason_accuracy": round(metrics.reason_accuracy, 1),
        "missed_exceptions": len(metrics.missed_exceptions),
        "llm_calls": result.llm_calls,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cost": _money(llm_cost_paise(result.input_tokens, result.output_tokens)),
        "cost_per_1000": _money(
            llm_cost_paise(result.input_tokens, result.output_tokens)
            * 1000
            // max(metrics.total, 1)
        ),
        "value_settled": _money(metrics.value_auto),
        "value_held": _money(metrics.value_held),
        "outcomes": {o.value: n for o, n in result.by_outcome().items()},
    }


@router.get("/exceptions")
def exceptions() -> dict:
    """Everything we would not decide, and why. The demo opens here."""
    result, metrics = CACHE.ensure()
    truth = {j.invoice_id: j for j in metrics.judgements}
    invoices = load_batch(result.split).invoice_by_id()

    rows = []
    for decision in result.decisions:
        if decision.outcome is Outcome.AUTO:
            continue
        invoice = invoices.get(decision.invoice_id)
        rows.append(
            {
                "invoice_id": decision.invoice_id,
                "counterparty": invoice.name_clean if invoice else "",
                "amount": _money(decision.amount_paise),
                "outcome": decision.outcome.value,
                "reason_code": decision.reason_code.value if decision.reason_code else None,
                "reason_text": decision.reason_text,
                "score": round(decision.score, 3),
                "margin": round(decision.margin, 3),
                "candidates": decision.txn_ids,
                "llm_used": decision.llm_used,
                "correct": truth[decision.invoice_id].correct
                if decision.invoice_id in truth
                else None,
            }
        )

    rows.sort(key=lambda r: -r["amount"]["paise"])
    return {"count": len(rows), "total": _money(metrics.value_held), "exceptions": rows}


@router.get("/adjudicated")
def adjudicated() -> dict:
    """The records the model was actually asked about.

    Without this the UI has a hole in the middle of its own argument. The
    exception list only shows what we held, but every record the adjudicator
    touched this run was one it helped clear - so the single screen that
    proves the model earns its place was unreachable from the demo.

    These are listed separately rather than mixed into the exception table,
    because they are not exceptions. They are the opposite.
    """
    result, metrics = CACHE.ensure()
    truth = {j.invoice_id: j for j in metrics.judgements}
    invoices = load_batch(result.split).invoice_by_id()

    rows = []
    for decision in result.decisions:
        if not decision.llm_used:
            continue
        invoice = invoices.get(decision.invoice_id)
        rows.append(
            {
                "invoice_id": decision.invoice_id,
                "counterparty": invoice.name_clean if invoice else "",
                "amount": _money(decision.amount_paise),
                "outcome": decision.outcome.value,
                "score": round(decision.score, 3),
                "margin": round(decision.margin, 3),
                "confidence": decision.llm_confidence,
                "reasoning": decision.llm_reasoning,
                "rejected": decision.llm_rejected,
                "correct": truth[decision.invoice_id].correct
                if decision.invoice_id in truth
                else None,
            }
        )

    rows.sort(key=lambda r: -r["amount"]["paise"])
    return {
        "count": len(rows),
        "of_total": metrics.total,
        "records": rows,
        "note": "a recommendation on each; the hard rules still decided",
    }


@router.get("/records/{invoice_id}")
def record(invoice_id: str) -> dict:
    """The whole chain for one record. Plan section 16.5, screen 2.

    Every signal, every rule with a tick or a cross, the settlement maths, and
    the model's reasoning clearly labelled as a recommendation rather than a
    decision. A judge reading this screen understands the thesis without us
    explaining it.
    """
    result, metrics = CACHE.ensure()
    batch = load_batch(result.split)
    invoice = batch.invoice_by_id().get(invoice_id)
    decision = next((d for d in result.decisions if d.invoice_id == invoice_id), None)

    if invoice is None or decision is None:
        raise HTTPException(status_code=404, detail=f"no record {invoice_id}")

    ranking = result.rankings.get(invoice_id)
    judgement = next((j for j in metrics.judgements if j.invoice_id == invoice_id), None)

    candidates = []
    for scored in (ranking.candidates[:5] if ranking else []):
        candidates.append(
            {
                "txn_id": scored.id,
                "description": scored.txn.description_raw,
                "amount": _money(scored.txn.amount_paise),
                "value_date": str(scored.txn.value_date),
                "score": round(scored.score, 3),
                "chosen": scored.id in decision.txn_ids,
                "signals": {
                    "reference": scored.signals.reference,
                    "amount": scored.signals.amount,
                    "name": scored.signals.name,
                    "date": scored.signals.date,
                },
                "weights_used": {k: round(v, 3) for k, v in scored.weights_used.items()},
                "amount_status": scored.amount.basis,
                "amount_formula": scored.amount.formula,
                "matched_via": scored.amount.pass_used.value,
            }
        )

    return {
        "invoice": {
            "id": invoice.id,
            "counterparty": invoice.name_raw,
            "counterparty_clean": invoice.name_clean,
            "amount": _money(invoice.amount_paise),
            "invoice_date": str(invoice.invoice_date),
            "due_date": str(invoice.due_date),
            "scenario": invoice.scenario,
        },
        "decision": {
            "outcome": decision.outcome.value,
            "reason_code": decision.reason_code.value if decision.reason_code else None,
            "reason_text": decision.reason_text,
            "score": round(decision.score, 3),
            "margin": round(decision.margin, 3),
            "margin_basis": decision.margin_basis,
            "decided_by": decision.decided_by,
            "settled_by": decision.txn_ids,
            "allocated": _money(decision.allocated_paise),
        },
        "rules": [
            {"name": name, "passed": True} for name in decision.rules_passed
        ] + [
            {"name": name, "passed": False} for name in decision.rules_failed
        ],
        "candidates": candidates,
        "adjudicator": {
            "used": decision.llm_used,
            "confidence": decision.llm_confidence,
            "reasoning": decision.llm_reasoning,
            "rejected": decision.llm_rejected,
            "note": "a recommendation, not a decision",
        },
        "verdict": {
            "expected": judgement.expected.value if judgement else None,
            "correct": judgement.correct if judgement else None,
        },
    }


@router.get("/cash-position")
def cash() -> dict:
    """Four numbers. Plan section 12."""
    result, _ = CACHE.ensure()
    position = cash_position(result.split)

    return {
        "as_of": position.as_of,
        "confirmed_in": _money(position.confirmed_in_paise),
        "still_owed": _money(position.still_owed_paise),
        "uncertain": _money(position.uncertain_paise),
        "withheld": _money(position.withheld_paise),
        "withheld_split": {
            "mdr": _money(position.mdr_paise),
            "gst": _money(position.gst_paise),
            "tds": _money(position.tds_paise),
        },
        "still_owed_note": "no payment turned up that covers these",
        "uncertain_note": "a payment exists, we would not sign it off",
        "withheld_note": "taken out before the money reached the bank",
        # The two open buckets together. Sent formatted so the UI never has a
        # second money formatter that can drift from this one.
        "open_total": _money(position.still_owed_paise + position.uncertain_paise),
        "aging": [
            {"label": b.label, "count": b.count, "value": _money(b.value_paise)}
            for b in position.aging
        ],
    }


@router.get("/eval")
def eval_breakdown() -> dict:
    """Per scenario, because aggregate numbers hide everything."""
    _, metrics = CACHE.ensure()

    scenarios = [
        {
            "scenario": scenario,
            "right": right,
            "total": total,
            "false_approvals": false,
            "rate": round(right / total * 100, 1) if total else 0.0,
        }
        for scenario, (right, total, false) in sorted(metrics.by_scenario().items())
    ]
    scenarios.sort(key=lambda s: (s["false_approvals"] == 0, s["rate"]))

    return {
        "scenarios": scenarios,
        "reason_accuracy": round(metrics.reason_accuracy, 1),
        "outcome_accuracy": round(metrics.outcome_accuracy, 1),
    }


@router.post("/runs")
def trigger_run(split: str = Split.HELDOUT.value, use_llm: bool = True) -> dict:
    """Run a batch. Calls exactly what the command line calls."""
    try:
        chosen = Split(split)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"unknown split {split!r}")

    result, metrics = CACHE.refresh(chosen, use_llm=use_llm)
    return {
        "run_id": result.run_id,
        "records": metrics.total,
        "seconds": round(result.seconds, 2),
        "straight_through": round(metrics.straight_through_rate, 1),
        "false_auto_approvals": len(metrics.false_auto_approvals),
    }
