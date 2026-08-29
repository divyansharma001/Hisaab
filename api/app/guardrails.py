"""The three guardrail layers. Plan section 7.

Most projects only build the third layer, the one that checks what a model
said. All three matter, and the second one is where the money is actually
protected.

**These are `if` statements, not prompts.** A proposed match must pass every
rule to be auto-approved, and the score does not get a veto: 0.99 with an
unexplained gap of Rs 340 is still an exception, no argument.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.blocking import Pass
from app.dataset import Outcome, Reason
from app.intake import Batch, NormInvoice, NormTxn
from app.money import TOLERANCE_PAISE, fmt, rupees
from app.names import name_similarity
from app.scoring import Ranking, Scored

# --- thresholds, all in one place so the tuning curve can move them ---------

AUTO_SCORE = 0.90
MARGIN_FLOOR = 0.15
ADJUDICATE_FLOOR = 0.70

# A wrong Rs 500 match is annoying. A wrong Rs 5,00,000 match is how fraud
# gets through.
VALUE_CEILING_PAISE = rupees(500_000)

# Payment timing, measured from the due date. Must use the same anchor as
# score_date, or a record passes one check and fails the other arbitrarily.
DATE_WINDOW_DAYS = (-7, 45)

# Same amount, same counterparty, this close together: flag both.
DUPLICATE_WINDOW = timedelta(hours=48)

# New names are the highest-risk category, so a counterparty must have been
# settled with this many times before their payments can be automated.
NEW_COUNTERPARTY_MIN = 3

AUTO = "AUTO"
ADJUDICATE = "ADJUDICATE"
EXCEPTION = "EXCEPTION"

# The only two rules an adjudicator's answer can satisfy. Everything else is
# policy rather than judgement, so a model's opinion cannot move it.
ENDORSABLE_RULES = {"score", "margin"}


@dataclass
class Rule:
    name: str
    passed: bool
    detail: str


@dataclass
class Verdict:
    outcome: Outcome
    reason_code: Reason
    reason_text: str
    rules: list[Rule] = field(default_factory=list)

    @property
    def passed(self) -> list[Rule]:
        return [r for r in self.rules if r.passed]

    @property
    def failed(self) -> list[Rule]:
        return [r for r in self.rules if not r.passed]

    @property
    def an_endorsement_could_change_this(self) -> bool:
        """Would asking the adjudicator make any difference to the outcome?

        Only if the *sole* thing standing in the way is the score or margin
        bar. A duplicated payment, an unexplained gap or a match above the
        value ceiling is held whatever a model thinks, so asking is money
        spent on an answer that cannot be acted on.
        """
        failed = {r.name for r in self.failed}
        return bool(failed) and failed <= ENDORSABLE_RULES


# --- Layer 1: input guardrails ---------------------------------------------


def check_invoice(invoice: NormInvoice, today: date) -> list[str]:
    """Cheap, deterministic, run before anything else costs us."""
    problems = []
    if not invoice.id:
        problems.append("no id")
    if invoice.amount_paise <= 0:
        problems.append(f"amount is {fmt(invoice.amount_paise)}")
    if invoice.currency != "INR":
        problems.append(f"currency is {invoice.currency!r}")
    if invoice.invoice_date > today:
        problems.append(f"dated {invoice.invoice_date}, in the future")
    if invoice.due_date < invoice.invoice_date:
        problems.append("due before it was raised")
    return problems


def check_txn(txn: NormTxn, today: date) -> list[str]:
    problems = []
    if not txn.id:
        problems.append("no id")
    if txn.amount_paise <= 0:
        problems.append(f"amount is {fmt(txn.amount_paise)}")
    if txn.currency != "INR":
        problems.append(f"currency is {txn.currency!r}")
    if txn.value_date > today:
        problems.append(f"dated {txn.value_date}, in the future")
    return problems


# --- routing ---------------------------------------------------------------


def route(score: float, margin: float) -> str:
    """Written exactly once, so two rules can never claim the same record.

    Margin is checked first on purpose. A record scoring 0.95 with a margin of
    0.03 satisfies both "auto-approve above 0.90" and "adjudicate below 0.15
    margin", and ambiguity has to win that argument.
    """
    if margin < MARGIN_FLOOR:
        return ADJUDICATE
    if score >= AUTO_SCORE:
        return AUTO
    if score >= ADJUDICATE_FLOOR:
        return ADJUDICATE
    return EXCEPTION


# --- Layer 2: decision guardrails ------------------------------------------


def find_duplicates(batch: Batch) -> dict[str, list[str]]:
    """Transactions that look like the same payment arriving twice.

    Either a data feed glitch or the customer really did pay twice. Both need
    a human, and both need *both* transactions flagged rather than one quietly
    matched and the other left floating.
    """
    duplicates: dict[str, list[str]] = {}

    for i, a in enumerate(batch.transactions):
        for b in batch.transactions[i + 1 :]:
            if a.amount_paise != b.amount_paise:
                continue
            if abs(a.value_date - b.value_date) > DUPLICATE_WINDOW:
                continue
            if name_similarity(a.name_clean, b.name_clean) < 0.9:
                continue
            duplicates.setdefault(a.id, []).append(b.id)
            duplicates.setdefault(b.id, []).append(a.id)

    return duplicates


# An adjudicator recommendation below this is not evidence of anything.
ENDORSEMENT_FLOOR = 0.80


def apply(
    invoice: NormInvoice,
    ranking: Ranking,
    best: Scored,
    *,
    claimed_txn_ids: list[str],
    duplicates: dict[str, list[str]],
    counterparty_seen: int,
    conflict: str | None = None,
    endorsement=None,
) -> Verdict:
    """Every hard rule, on one proposed match. All must pass to automate it.

    An adjudicator recommendation can satisfy the **score and margin bars, and
    nothing else**. Those two are the question the model was asked - is this
    the right payment - so a confident answer from it is evidence about
    exactly that.

    It can never satisfy the value ceiling, the duplicate rule, the conflict
    rule, the amount check, the date window or the new-counterparty rule. Those
    are policy, not judgement, and this is what "the LLM never has the final say
    on money" means in code rather than in a sentence.
    """
    rules: list[Rule] = []

    def rule(name: str, ok: bool, detail: str) -> None:
        rules.append(Rule(name, ok, detail))

    endorsed = (
        endorsement is not None
        and getattr(endorsement, "usable", False)
        and endorsement.chosen_id == best.id
        and endorsement.confidence >= ENDORSEMENT_FLOOR
    )

    if endorsed:
        note = f"adjudicator backed {best.id} at {endorsement.confidence:.2f}"
        rule("score", True, f"{ranking.score:.2f} below {AUTO_SCORE}, but {note}")
        rule("margin", True, f"{ranking.margin:.2f} below {MARGIN_FLOOR}, but {note}")
    else:
        rule("score", ranking.score >= AUTO_SCORE, f"{ranking.score:.2f} (needs {AUTO_SCORE})")
        rule(
            "margin",
            ranking.margin >= MARGIN_FLOOR,
            f"{ranking.margin:.2f} (needs {MARGIN_FLOOR}, basis {ranking.margin_basis})",
        )

    # The amount must be explained to the rupee. Unexplained money is never OK,
    # whatever the score says.
    gap_ok = best.amount.score >= 0.85
    rule("amount explained", gap_ok, best.amount.basis)

    # Read the anchor the scorer used rather than recomputing it. Two anchors
    # 30 days apart is how records pass one check and fail the other for no
    # reason anyone can see. Section 18, bug 5.
    days = (best.txn.value_date - best.date_anchor).days
    low, high = DATE_WINDOW_DAYS
    rule("date window", low <= days <= high, f"{days:+d} days from the due date")

    rule(
        "currency",
        best.txn.currency == invoice.currency,
        f"{invoice.currency} against {best.txn.currency}",
    )

    rule(
        "value ceiling",
        invoice.amount_paise <= VALUE_CEILING_PAISE,
        f"{fmt(invoice.amount_paise)} (ceiling {fmt(VALUE_CEILING_PAISE)})",
    )

    seen_enough = counterparty_seen >= NEW_COUNTERPARTY_MIN
    rule(
        "known counterparty",
        seen_enough,
        f"settled with {invoice.name_clean} {counterparty_seen} time(s) before",
    )

    dupes = [d for t in claimed_txn_ids for d in duplicates.get(t, [])]
    rule(
        "not a duplicate",
        not dupes,
        f"also appears as {', '.join(sorted(set(dupes)))}" if dupes else "no twin in this batch",
    )

    rule(
        "unclaimed",
        conflict is None,
        conflict or "no other invoice wants this payment",
    )

    verdict_outcome, code, text = _conclude(invoice, ranking, best, rules, conflict, dupes)
    return Verdict(outcome=verdict_outcome, reason_code=code, reason_text=text, rules=rules)


def _conclude(
    invoice: NormInvoice,
    ranking: Ranking,
    best: Scored,
    rules: list[Rule],
    conflict: str | None,
    dupes: list[str],
) -> tuple[Outcome, Reason, str]:
    """Which rule actually decided this, and how to say it to a human.

    Order matters: the first failure listed here is the one the exception list
    shows, so the most actionable cause has to come first.
    """
    failed = {r.name: r for r in rules if not r.passed}

    if not failed:
        return Outcome.AUTO, _match_reason(best), best.amount.basis

    # Two invoices want the same payment. Neither may be automated.
    if "unclaimed" in failed:
        return Outcome.AMBIGUOUS, Reason.AMBIGUOUS_CANDIDATES, failed["unclaimed"].detail

    # The same payment appears twice. A human decides which is real.
    if "not a duplicate" in failed:
        return (
            Outcome.REVIEW,
            Reason.DUPLICATE_TRANSACTION,
            f"{best.id} {failed['not a duplicate'].detail}; both held",
        )

    # Big enough that a wrong match is a different kind of problem.
    if "value ceiling" in failed:
        return (
            Outcome.REVIEW,
            Reason.VALUE_CEILING,
            f"{fmt(invoice.amount_paise)} is above the {fmt(VALUE_CEILING_PAISE)} ceiling, "
            f"so a human signs it off even at score {ranking.score:.2f}",
        )

    if "known counterparty" in failed:
        return (
            Outcome.REVIEW,
            Reason.NEW_COUNTERPARTY,
            f"First time settling with {invoice.name_clean}",
        )

    if "date window" in failed:
        return Outcome.EXCEPTION, Reason.DATE_OUT_OF_WINDOW, failed["date window"].detail

    if "amount explained" in failed:
        return (
            Outcome.EXCEPTION,
            Reason.AMOUNT_GAP_UNEXPLAINED,
            f"Best candidate: {best.amount.basis}",
        )

    if "margin" in failed:
        return (
            Outcome.AMBIGUOUS,
            Reason.AMBIGUOUS_CANDIDATES,
            f"Top two candidates are {ranking.margin:.2f} apart, too close to call",
        )

    weakest = min(best.signals.available().items(), key=lambda kv: kv[1], default=("score", 0.0))
    return (
        Outcome.EXCEPTION,
        Reason.BELOW_THRESHOLD,
        f"Scored {ranking.score:.2f} against a bar of {AUTO_SCORE}; "
        f"weakest signal was {weakest[0]} at {weakest[1]:.2f}",
    )


def _match_reason(best: Scored) -> Reason:
    if best.amount.pass_used is Pass.COMBINED:
        return Reason.COMBINED_PAYMENT
    if best.amount.pass_used is Pass.PARTIAL:
        return Reason.PARTIAL_PAYMENT
    if best.signals.reference == 1.0:
        return Reason.MATCHED_REFERENCE

    formula = best.amount.formula
    if formula and formula.startswith("MDR_GST"):
        return Reason.MDR_GST
    if formula in ("TDS_2PCT", "TDS_10PCT"):
        return Reason(formula)

    if best.signals.name is not None and best.signals.name < 1.0:
        return Reason.MATCHED_ALIAS
    return Reason.MATCHED_NAME_AMOUNT


def score_only(invoice: NormInvoice, ranking: Ranking, best: Scored) -> Verdict:
    """The score bar and nothing else. Ablation only, never the live path.

    This is what the deterministic core did before the guardrail layer: no
    value ceiling, no duplicate rule, no conflict check, no new-counterparty
    rule. Kept so the ablation table can show what those rules actually cost
    and bought, rather than asserting it.
    """
    passed = ranking.score >= AUTO_SCORE
    rules = [Rule("score", passed, f"{ranking.score:.2f} (needs {AUTO_SCORE})")]

    if passed:
        return Verdict(Outcome.AUTO, _match_reason(best), best.amount.basis, rules)

    weakest = min(best.signals.available().items(), key=lambda kv: kv[1], default=("score", 0.0))
    return Verdict(
        Outcome.EXCEPTION,
        Reason.BELOW_THRESHOLD,
        f"Scored {ranking.score:.2f} against a bar of {AUTO_SCORE}; "
        f"weakest signal was {weakest[0]} at {weakest[1]:.2f}",
        rules,
    )
