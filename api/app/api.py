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
from app.memory import Memory, build_from_split, confirm_match, episode_from
from app.money import fmt, llm_cost_paise
from app.pipeline import RunResult, process_batch, run
from app.qa import ask
from app import sandbox
from app.thresholds import BARS, both_curves
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


# What each layer of the system actually bought. Plan section 19.1.
#
# The LLM row is deliberately excluded from this endpoint. Running it would
# spend money on every page load, and its result moves between runs, so the
# table would disagree with itself while a judge was reading it. The three
# deterministic rows are repeatable and free; the model's contribution is
# already reported on the batch screen, measured on the run that just ran.
ABLATION = [
    (
        "Scoring alone, nothing remembered",
        dict(use_guardrails=False, use_memory=False, use_llm=False),
        "Match on the four signals and close anything that scores highly enough.",
    ),
    (
        "Plus our checks, still nothing remembered",
        dict(use_guardrails=True, use_memory=False, use_llm=False),
        "Wrong matches stop, but so does everything else: with no history every "
        "customer looks new, and a new customer is always held. The checks need "
        "memory to be usable, which is the point of the next row.",
    ),
    (
        "Plus what we remember - the system as shipped",
        dict(use_guardrails=True, use_memory=True, use_llm=False),
        "Knowing who we have dealt with before is what makes the checks "
        "affordable. This is the system without the assistant.",
    ),
]


# --- the scratch set, for trying it on your own figures --------------------
#
# Everything here is filtered on the sandbox split. It cannot read, change or
# be scored against the graded rows, which sit in the same tables under a
# different split.


@router.get("/sandbox")
def sandbox_contents() -> dict:
    return sandbox.contents()


@router.post("/sandbox/invoices")
def sandbox_add_invoice(body: dict) -> dict:
    b = body or {}
    try:
        added = sandbox.add_invoice(
            customer=b.get("customer", ""),
            amount=b.get("amount", ""),
            invoice_date=b.get("invoice_date") or None,
            due_date=b.get("due_date") or None,
        )
    except ValueError as exc:
        # The message is written for the person who typed it, so it goes
        # straight through rather than becoming "invalid input".
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"id": added.id, "summary": added.summary, **sandbox.contents()}


@router.post("/sandbox/payments")
def sandbox_add_payment(body: dict) -> dict:
    b = body or {}
    try:
        added = sandbox.add_payment(
            bank_text=b.get("bank_text", ""),
            amount=b.get("amount", ""),
            value_date=b.get("value_date") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"id": added.id, "summary": added.summary, **sandbox.contents()}


@router.post("/sandbox/match")
def sandbox_match() -> dict:
    return sandbox.match()


@router.delete("/sandbox")
def sandbox_clear() -> dict:
    return {**sandbox.clear(), **sandbox.contents()}


@router.get("/ablation")
def ablation() -> dict:
    """Did each layer earn its place? Plan section 19.1.

    Every row is a real run over the same records, one layer at a time, so
    the answer is a measurement rather than an assertion.
    """
    truth = load_truth(Split.HELDOUT)

    rows = []
    previous = None
    for label, flags, explanation in ABLATION:
        result = run(Split.HELDOUT, **flags)
        metrics = evaluate(result, truth)
        rows.append(
            {
                "layer": label,
                "explanation": explanation,
                "closed": round(metrics.straight_through_rate, 1),
                "accuracy": round(metrics.outcome_accuracy, 1),
                "wrong": len(metrics.false_auto_approvals),
                "closed_change": round(
                    metrics.straight_through_rate - previous.straight_through_rate, 1
                )
                if previous
                else None,
                "wrong_change": len(metrics.false_auto_approvals)
                - len(previous.false_auto_approvals)
                if previous
                else None,
            }
        )
        previous = metrics

    return {"rows": rows, "records": len(truth)}


@router.get("/mistakes")
def mistakes() -> dict:
    """The records we got wrong, named. Plan section 19.8.

    Judges watch polished demos all day. A team that can point at its own
    failure and say why reads as markedly more credible than one claiming
    everything worked - and we have to be able to find them anyway.
    """
    result, metrics = CACHE.ensure()
    invoices = load_batch(result.split).invoice_by_id()
    by_id = {d.invoice_id: d for d in result.decisions}

    wrong = []
    for judgement in metrics.judgements:
        if judgement.correct:
            continue
        decision = by_id.get(judgement.invoice_id)
        invoice = invoices.get(judgement.invoice_id)
        if decision is None or invoice is None:
            continue
        wrong.append(
            {
                "invoice_id": judgement.invoice_id,
                "customer": invoice.name_clean,
                "amount": _money(invoice.amount_paise),
                "scenario": invoice.scenario,
                "we_said": decision.outcome.value,
                "answer_was": judgement.expected.value,
                "our_reason": decision.reason_text,
                "asked_the_assistant": decision.llm_used,
                # Which direction the mistake went. Being too careful costs a
                # person a few minutes; being too confident costs money.
                "erred_towards": "holding it back"
                if judgement.expected.value == "AUTO"
                else "closing it",
            }
        )

    wrong.sort(key=lambda r: -r["amount"]["paise"])
    return {
        "count": len(wrong),
        "mistakes": wrong,
        "all_in_one_direction": all(
            m["erred_towards"] == "holding it back" for m in wrong
        )
        if wrong
        else True,
    }


@router.get("/learning")
def learning() -> dict:
    """The learning loop, shown rather than asserted. Plan section 19.3.

    There is a catch the plan did not anticipate: on this dataset the loop
    cannot be demonstrated with the memory we ship. The alias seed covers
    every customer three times over, so **not one record is held for being a
    new customer** and confirming a name unblocks nothing. The feature works;
    the data gives it nothing to do.

    So this starts from half the history, which is what a real business looks
    like partway through its first year, and shows the same batch before and
    after a reviewer confirms the names. The thinning is stated, not hidden -
    a demo that quietly rigs its own starting point is worth nothing.
    """
    result, _ = CACHE.ensure()
    batch = load_batch(result.split)
    invoices, transactions = batch.invoice_by_id(), batch.txn_by_id()
    full = build_from_split()

    # Half the customers, so the rest look new.
    names = sorted(full.confirmations)
    keep = set(names[: len(names) // 2])
    thin = Memory(
        variants={k: v for k, v in full.variants.items() if k in keep},
        confirmations={k: v for k, v in full.confirmations.items() if k in keep},
        episodes=list(full.episodes),
    ).snapshot()

    before = process_batch(batch, memory=thin)
    held_as_new = [
        d for d in before.decisions if "known counterparty" in d.rules_failed
    ]

    # The reviewer confirms exactly the ones they were shown.
    grown = Memory(
        variants={k: set(v) for k, v in thin.variants.items()},
        confirmations=dict(thin.confirmations),
        episodes=list(thin.episodes),
    )
    for decision in held_as_new:
        invoice = invoices.get(decision.invoice_id)
        for txn_id in decision.txn_ids:
            txn = transactions.get(txn_id)
            if invoice and txn and txn.name_clean:
                grown.variants.setdefault(invoice.name_clean, set()).add(txn.name_clean)
                grown.confirmations[invoice.name_clean] = (
                    grown.confirmations.get(invoice.name_clean, 0) + 3
                )

    after = process_batch(batch, memory=grown.snapshot())

    closed_before = {d.invoice_id for d in before.decisions if d.outcome is Outcome.AUTO}
    closed_after = {d.invoice_id for d in after.decisions if d.outcome is Outcome.AUTO}
    confirmed_ids = {d.invoice_id for d in held_as_new}
    newly = sorted(closed_after - closed_before)

    return {
        "customers_known_at_the_start": len(keep),
        "customers_in_the_batch": len({i.name_clean for i in batch.invoices}),
        "held_as_new": [
            {
                "invoice_id": d.invoice_id,
                "customer": invoices[d.invoice_id].name_clean
                if d.invoice_id in invoices
                else "",
                "amount": _money(d.amount_paise),
            }
            for d in held_as_new[:5]
        ],
        "confirmed": len(confirmed_ids),
        "closed_before": len(closed_before),
        "closed_after": len(closed_after),
        "newly_closing": newly,
        "knock_on": sorted(set(newly) - confirmed_ids),
        "records": len(batch.invoices),
        "note": (
            "Started from half our history on purpose. With the full history we "
            "ship, nothing is held for being a new customer, so there would be "
            "nothing here to confirm."
        ),
        "knock_on_note": (
            "Confirming a name releases every held invoice from that customer. "
            "In this batch each of them has only one, so the count of newly "
            "closing records tracks the confirmations rather than exceeding them."
        ),
    }


@router.post("/confirm/{invoice_id}")
def confirm(invoice_id: str) -> dict:
    """A reviewer says "yes, that is the right payment". Plan section 19.3.

    The plan wants this shown, not asserted: click confirm, and the same name
    pattern resolves by itself next time. So this does two things - writes the
    confirmation, then re-runs the batch with it and reports what actually
    changed. A measured preview, not a promise.

    On the graded batch the confirmation is stored but deliberately kept out
    of the scored run, because learning from the records you are marked on is
    how an eval stops meaning anything. The preview says so.
    """
    result, _ = CACHE.ensure()
    batch = load_batch(result.split)
    invoice = batch.invoice_by_id().get(invoice_id)
    decision = next((d for d in result.decisions if d.invoice_id == invoice_id), None)

    if invoice is None or decision is None:
        raise HTTPException(status_code=404, detail=f"no record {invoice_id}")
    if not decision.txn_ids:
        raise HTTPException(
            status_code=400, detail="there is no payment here to confirm"
        )

    transactions = batch.txn_by_id()
    txn = transactions.get(decision.txn_ids[0])
    if txn is None:
        raise HTTPException(status_code=400, detail="that payment is not in this batch")

    written = confirm_match(
        canonical_name=invoice.name_clean,
        bank_text=txn.description_raw,
        source_split=result.split,
        episode=episode_from(decision, invoice, txn, result.split),
    )

    # What it would change on a fresh batch, measured rather than claimed.
    before = build_from_split()
    after = build_from_split()
    after.variants.setdefault(invoice.name_clean, set()).add(written.get("variant", ""))
    after.confirmations[invoice.name_clean] = (
        after.confirmations.get(invoice.name_clean, 0) + 3
    )

    was = process_batch(batch, memory=before)
    now = process_batch(batch, memory=after.snapshot())

    settled_before = {d.invoice_id for d in was.decisions if d.outcome is Outcome.AUTO}
    settled_now = {d.invoice_id for d in now.decisions if d.outcome is Outcome.AUTO}
    newly = sorted(settled_now - settled_before)

    return {
        "invoice_id": invoice_id,
        "customer": invoice.name_clean,
        **written,
        "closes_now": len(settled_now),
        "closed_before": len(settled_before),
        "newly_closing": newly,
        "also_affected": [i for i in newly if i != invoice_id],
        "note": (
            "Stored, and it will count on the next real batch. It is kept out of "
            "the scored run above, because learning from the records we grade "
            "ourselves would make those numbers meaningless."
            if result.split is Split.HELDOUT
            else "Stored, and counted from the next run."
        ),
    }


@router.get("/thresholds")
def thresholds() -> dict:
    """Where the bar could sit, and what it actually buys. Plan section 19.6.

    Two curves. The top one is the system as shipped; the bottom is the same
    sweep with the nine rules switched off. The plan expected the bar to be
    the safety mechanism - it is not, and the gap between these lines is why.
    Section 18, bug 14.

    Run without the assistant, so the curve is repeatable and free.
    """
    curves = both_curves()

    def rows(points):
        return [
            {
                "bar": p.bar,
                "closed": p.closed_on_their_own,
                "wrong": p.wrong,
                "is_current": p.is_current,
            }
            for p in points
        ]

    guarded = rows(curves["with_rules"])
    bare = rows(curves["score_only"])
    current = next((r for r in guarded if r["is_current"]), None)
    same_bar = next((r for r in bare if r["is_current"]), None)

    return {
        "with_rules": guarded,
        "score_only": bare,
        "current_bar": current["bar"] if current else None,
        "cost_of_the_rules": {
            "automation_given_up": round(
                (same_bar["closed"] - current["closed"]), 1
            )
            if current and same_bar
            else None,
            "wrong_approvals_prevented": same_bar["wrong"] if same_bar else None,
        },
        "finding": (
            "The score bar is not what keeps this safe. With the rules on, no bar "
            "between 0.30 and 0.98 produced a wrong approval. With only the score "
            "bar, the same batch gets eleven wrong."
        ),
    }


@router.post("/ask")
def ask_question(body: dict) -> dict:
    """Settlement Q&A. Plan section 19.4.

    Answers "why did we receive this much for INV-0053?" from the ledger.
    Every number is fetched before the model sees the question, and the answer
    is checked back against those numbers - an amount we did not supply means
    the model did arithmetic, and we show the ledger instead of its answer.
    """
    question = (body or {}).get("question", "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="ask a question")

    answer = ask(question)
    return {
        "question": question,
        "answer": answer.text,
        "invoice_id": answer.invoice_id,
        "facts": answer.facts,
        "used_model": answer.used_model,
        "rejected": answer.rejected,
        "cost": _money(llm_cost_paise(answer.input_tokens, answer.output_tokens)),
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
