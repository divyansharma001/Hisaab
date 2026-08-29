"""Checks the generated data against its own answer key.

Everything in plan section 8 rests on the answer key being right. A wrong
answer key is worse than no answer key, because it produces confident numbers
that are quietly false.

The check that matters most is accidental ambiguity: two invoices that could
both plausibly claim one transaction, in a scenario where we did not intend
any ambiguity. That would mark the pipeline wrong for behaving correctly.
"""

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app import settlement
from app.dataset import Dataset, Outcome, Split
from app.generate.builders import ANCHOR
from app.generate.mix import mix_for
from app.money import TOLERANCE_PAISE, fmt

# Scenarios where more than one invoice claiming a transaction is the point.
AMBIGUITY_EXPECTED = {"identical_invoices", "combined_payment", "batched_settlement"}

NAME_MATCH_FLOOR = 80


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def validate(data: Dataset) -> Report:
    r = Report()
    _check_counts(data, r)
    _check_references(data, r)
    _check_dates(data, r)
    _check_settlements(data, r)
    _check_amount_relationships(data, r)
    _check_accidental_ambiguity(data, r)
    _check_no_payment_is_really_unpaid(data, r)
    _check_short_payment_is_really_unexplained(data, r)
    return r


def _check_counts(data: Dataset, r: Report) -> None:
    if data.split is Split.ALIAS_SEED:
        _check_prior_history(data, r)
        return

    want = mix_for(data.split)
    got = data.scenario_counts()

    for scenario, count in want.items():
        if got.get(scenario, 0) != count:
            r.error(
                f"{scenario}: mix wants {count} invoices, generator produced "
                f"{got.get(scenario, 0)}"
            )
    for scenario in got:
        if scenario not in want:
            r.error(f"{scenario}: produced but not in the mix")

    covered = {t.invoice_id for t in data.truth}
    for inv in data.invoices:
        if inv.id not in covered:
            r.error(f"{inv.id} ({inv.scenario}): no answer key row")


def _check_prior_history(data: Dataset, r: Report) -> None:
    """The seed set is one settled invoice per customer, nothing else."""
    for inv in data.invoices:
        if inv.scenario != "prior_settlement":
            r.error(f"{inv.id}: the seed set holds prior settlements only, got {inv.scenario}")

    from app.generate.builders import PRIOR_SETTLEMENTS_PER_CUSTOMER

    counts: dict[str, int] = {}
    for inv in data.invoices:
        counts[inv.counterparty_name_clean] = counts.get(inv.counterparty_name_clean, 0) + 1

    for name, n in counts.items():
        if n != PRIOR_SETTLEMENTS_PER_CUSTOMER:
            r.error(
                f"{name}: {n} prior settlements, but the guardrail asks for "
                f"{PRIOR_SETTLEMENTS_PER_CUSTOMER}"
            )

    covered = {t.invoice_id for t in data.truth}
    for inv in data.invoices:
        if inv.id not in covered:
            r.error(f"{inv.id}: no answer key row")


def _check_references(data: Dataset, r: Report) -> None:
    invoice_ids = {i.id for i in data.invoices}
    txn_ids = {t.id for t in data.transactions}

    if len(invoice_ids) != len(data.invoices):
        r.error("duplicate invoice ids")
    if len(txn_ids) != len(data.transactions):
        r.error("duplicate transaction ids")

    seen: set[str] = set()
    for t in data.truth:
        if t.invoice_id not in invoice_ids:
            r.error(f"truth row points at unknown invoice {t.invoice_id}")
        if t.invoice_id in seen:
            r.error(f"two truth rows for invoice {t.invoice_id}")
        seen.add(t.invoice_id)

        for txn_id in t.expected_txn_ids:
            if txn_id not in txn_ids:
                r.error(f"{t.invoice_id}: expects unknown transaction {txn_id}")

        if t.expected_outcome is Outcome.EXCEPTION and t.scenario == "no_payment":
            if t.expected_txn_ids:
                r.error(f"{t.invoice_id}: unpaid but names transactions")

    for s in data.settlements:
        if s.txn_id not in txn_ids:
            r.error(f"settlement {s.id} points at unknown transaction {s.txn_id}")


def _check_dates(data: Dataset, r: Report) -> None:
    for inv in data.invoices:
        if inv.due_date < inv.invoice_date:
            r.error(f"{inv.id}: due date is before the invoice date")
        if inv.invoice_date > ANCHOR:
            r.error(f"{inv.id}: invoice dated in the future")

    for txn in data.transactions:
        if txn.value_date > ANCHOR:
            r.error(f"{txn.id}: dated {txn.value_date}, after the anchor {ANCHOR}")


def _check_settlements(data: Dataset, r: Report) -> None:
    by_id = {i.id: i for i in data.invoices}
    truth_by_txn: dict[str, list[str]] = {}
    for t in data.truth:
        for txn_id in t.expected_txn_ids:
            truth_by_txn.setdefault(txn_id, []).append(t.invoice_id)

    for s in data.settlements:
        computed = s.gross_paise - s.fee_paise - s.gst_on_fee_paise - s.tds_paise
        if computed != s.net_paise:
            r.error(
                f"settlement {s.id}: {fmt(s.gross_paise)} minus deductions is "
                f"{fmt(computed)}, but net says {fmt(s.net_paise)}"
            )

        # The gross must be the invoice it settles, or the maths is fiction.
        owners = truth_by_txn.get(s.txn_id, [])
        if len(owners) == 1 and owners[0] in by_id:
            inv = by_id[owners[0]]
            if inv.amount_paise != s.gross_paise:
                r.error(
                    f"settlement {s.id}: gross {fmt(s.gross_paise)} does not equal "
                    f"invoice {inv.id} at {fmt(inv.amount_paise)}"
                )


def _check_amount_relationships(data: Dataset, r: Report) -> None:
    """Each scenario promises a specific relationship between bill and payment."""
    invoices = {i.id: i for i in data.invoices}
    txns = {t.id: t for t in data.transactions}

    for t in data.truth:
        # Unresolvable ids are already reported by _check_references. Skipping
        # them here keeps this check reporting rather than crashing.
        inv = invoices.get(t.invoice_id)
        if inv is None:
            continue
        paid = [txns[i] for i in t.expected_txn_ids if i in txns]
        if len(paid) != len(t.expected_txn_ids) or not paid:
            continue

        total = sum(x.amount_paise for x in paid)

        if t.scenario in {"exact_reference", "clean_name_amount", "alias_variation",
                          "date_out_of_window", "value_ceiling"}:
            if abs(total - inv.amount_paise) > TOLERANCE_PAISE:
                r.error(f"{inv.id} ({t.scenario}): expected an exact amount match")

        elif t.scenario in {"tds_deduction", "gateway_fee", "batched_settlement"}:
            if settlement.explain(inv.amount_paise, paid[0].amount_paise) is None:
                r.error(
                    f"{inv.id} ({t.scenario}): no formula turns "
                    f"{fmt(inv.amount_paise)} into {fmt(paid[0].amount_paise)}"
                )

        elif t.scenario == "partial_payment":
            if abs(total - inv.amount_paise) > TOLERANCE_PAISE:
                r.error(f"{inv.id}: instalments total {fmt(total)}, bill is {fmt(inv.amount_paise)}")
            if len(paid) < 2:
                r.error(f"{inv.id}: a partial payment needs at least two transactions")

        elif t.scenario == "combined_payment":
            if paid[0].amount_paise <= inv.amount_paise:
                r.error(f"{inv.id}: a combined payment must exceed any one invoice")

        elif t.scenario == "duplicate_transaction":
            if len(paid) != 2 or paid[0].amount_paise != paid[1].amount_paise:
                r.error(f"{inv.id}: a duplicate needs two transactions of equal value")

        elif t.scenario == "short_payment":
            if paid[0].amount_paise >= inv.amount_paise:
                r.error(f"{inv.id}: a short payment must be less than the bill")


def _check_accidental_ambiguity(data: Dataset, r: Report) -> None:
    """No transaction may be plausibly claimable by an invoice that does not own it.

    'Plausible' here is deliberately generous: a close name and an amount the
    settlement maths could explain. If a competitor clears that bar in a
    scenario meant to be unambiguous, the answer key is claiming a single
    right answer where two exist.
    """
    txns = {t.id: t for t in data.transactions}
    owners: dict[str, set[str]] = {}
    for t in data.truth:
        for txn_id in t.expected_txn_ids:
            owners.setdefault(txn_id, set()).add(t.invoice_id)

    truth_by_invoice = {t.invoice_id: t for t in data.truth}

    for txn_id, owner_ids in owners.items():
        txn = txns.get(txn_id)
        if txn is None:
            continue
        scenarios = {
            truth_by_invoice[i].scenario for i in owner_ids if i in truth_by_invoice
        }
        if scenarios & AMBIGUITY_EXPECTED:
            continue

        for inv in data.invoices:
            if inv.id in owner_ids:
                continue
            # Invoices with no truth row are reported by _check_references.
            owner_truth = truth_by_invoice.get(inv.id)
            if owner_truth is None or owner_truth.scenario in AMBIGUITY_EXPECTED:
                continue
            if _could_claim(inv, txn):
                r.error(
                    f"{txn_id} is owned by {sorted(owner_ids)} but {inv.id} "
                    f"({inv.counterparty_name_clean}, {fmt(inv.amount_paise)}) could "
                    f"also claim it; accidental ambiguity"
                )


def _could_claim(inv, txn) -> bool:
    if not txn.counterparty_name_clean:
        return False
    if fuzz.token_set_ratio(inv.counterparty_name_clean, txn.counterparty_name_clean) < NAME_MATCH_FLOOR:
        return False
    return settlement.explain(inv.amount_paise, txn.amount_paise) is not None


def _check_no_payment_is_really_unpaid(data: Dataset, r: Report) -> None:
    """An invoice marked unpaid must have nothing in the batch that fits it."""
    unpaid = [t.invoice_id for t in data.truth if t.scenario == "no_payment"]
    by_id = {i.id: i for i in data.invoices}

    for invoice_id in unpaid:
        inv = by_id.get(invoice_id)
        if inv is None:
            continue
        for txn in data.transactions:
            if _could_claim(inv, txn):
                r.error(
                    f"{invoice_id} is marked unpaid but {txn.id} fits it "
                    f"({txn.counterparty_name_clean}, {fmt(txn.amount_paise)})"
                )


def _check_short_payment_is_really_unexplained(data: Dataset, r: Report) -> None:
    """The AMOUNT_GAP_UNEXPLAINED reason code must be true, not assumed."""
    invoices = {i.id: i for i in data.invoices}
    txns = {t.id: t for t in data.transactions}

    for t in data.truth:
        if t.scenario != "short_payment":
            continue
        inv = invoices.get(t.invoice_id)
        txn = txns.get(t.expected_txn_ids[0]) if t.expected_txn_ids else None
        if inv is None or txn is None:
            continue
        found = settlement.explain(inv.amount_paise, txn.amount_paise)
        if found is not None:
            r.error(
                f"{inv.id}: marked unexplained, but {found.formula} explains "
                f"{fmt(inv.amount_paise)} to {fmt(txn.amount_paise)}"
            )


def validate_across_splits(datasets: dict[Split, Dataset]) -> Report:
    """Ids must be unique across every split, since they share one database."""
    r = Report()
    seen_inv: dict[str, Split] = {}
    seen_txn: dict[str, Split] = {}

    for split, data in datasets.items():
        for inv in data.invoices:
            if inv.id in seen_inv:
                r.error(f"invoice id {inv.id} used in both {seen_inv[inv.id]} and {split}")
            seen_inv[inv.id] = split
        for txn in data.transactions:
            if txn.id in seen_txn:
                r.error(f"transaction id {txn.id} used in both {seen_txn[txn.id]} and {split}")
            seen_txn[txn.id] = split
    return r
