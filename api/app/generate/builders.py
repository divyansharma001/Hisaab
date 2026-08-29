"""One builder per scenario. Each writes the records and the answer key together.

Two rules hold this file together:

1. **Every builder writes its truth rows.** The answer key is never derived
   afterwards, because a derivation can disagree with what was generated.
2. **No accidental ambiguity.** Each scenario group gets its own counterparty,
   and amounts are kept apart, so the only ambiguous cases are the ones we
   built on purpose. Ambiguity that leaks in by chance makes the answer key
   wrong, and a wrong answer key is worse than no answer key.
"""

import random
from datetime import date, timedelta

from app import settlement
from app.dataset import (
    Dataset,
    Invoice,
    Outcome,
    Reason,
    Settlement,
    Split,
    Transaction,
    TruthEntry,
)
from app.generate.companies import Counterparty
from app.money import TOLERANCE_PAISE, rupees
from app.names import clean_name

# Nothing may be dated after this. Fixed, not "today", so the dataset does not
# change shape depending on when it is generated.
ANCHOR = date(2026, 8, 29)

EARLIEST_INVOICE = date(2026, 4, 1)
LATEST_INVOICE = date(2026, 7, 10)
PAYMENT_TERM_DAYS = 30

BANK_CODES = ["HDFC", "ICIC", "SBIN", "UTIB", "KKBK", "YESB", "IDFB"]

ID_PREFIX: dict[Split, str] = {
    Split.HELDOUT: "",
    Split.TUNING: "T",
    Split.ALIAS_SEED: "A",
}

# How far apart two amounts must stay before nothing can confuse them.
#
# Sized against the matching tolerance, not by feel: a match needs the gap
# explained to within Rs 1, so Rs 40 of separation is a 40x margin. Anything
# larger just exhausts the range, because each amount also blocks the six
# values the deduction formulas can turn it into.
MIN_AMOUNT_GAP_PAISE = rupees(40)

VALUE_CEILING_PAISE = rupees(500_000)

# How deep the prior history goes. Matches the new-counterparty guardrail,
# which wants three confirmed settlements before it will automate a customer.
PRIOR_SETTLEMENTS_PER_CUSTOMER = 3


class Ctx:
    """Shared state for one split's generation run."""

    def __init__(self, split: Split, seed: int, pool: list[Counterparty]):
        self.split = split
        self.seed = seed
        self.rng = random.Random(seed)
        self.pool = list(pool)
        self.data = Dataset(split=split, seed=seed)

        self._pool_at = 0
        self._inv_n = 0
        self._txn_n = 0
        self._stl_n = 0
        # Not just the amounts used, but everything the deduction formulas can
        # turn them into. See Ctx.amount.
        self._taken: list[int] = []
        self.used_companies: list[Counterparty] = []

    # -- identifiers ------------------------------------------------------

    def invoice_no(self) -> str:
        self._inv_n += 1
        return f"INV-{ID_PREFIX[self.split]}{self._inv_n:04d}"

    def txn_id(self) -> str:
        self._txn_n += 1
        return f"TXN-{ID_PREFIX[self.split]}{self._txn_n:04d}"

    def settlement_id(self) -> str:
        self._stl_n += 1
        return f"STL-{ID_PREFIX[self.split]}{self._stl_n:04d}"

    def utr(self) -> str:
        code = self.rng.choice(BANK_CODES)
        return f"{code}{self.rng.randrange(10**11, 10**12)}"

    # -- draws ------------------------------------------------------------

    def company(self) -> Counterparty:
        """A counterparty not yet used in this split."""
        if self._pool_at >= len(self.pool):
            raise ValueError("company pool exhausted; build a bigger pool")
        c = self.pool[self._pool_at]
        self._pool_at += 1
        self.used_companies.append(c)
        return c

    def amount(self, low: int = 5_000, high: int = 400_000) -> int:
        """A rupee amount that no deduction can collide with an existing one.

        Separating the billed amounts is not enough. A bill of Rs 2,68,300 less
        2% TDS is Rs 2,62,934, which can land within a rupee of what a
        completely different customer actually paid. Then two invoices can both
        explain one transaction, and the answer key is wrong.

        So a candidate is rejected unless *every* value the formulas can turn
        it into is clear of every value already taken.
        """
        for _ in range(2000):
            rupee_value = self.rng.randrange(low // 50, high // 50) * 50
            paise = rupees(rupee_value)
            forms = self._reachable(paise)
            if all(
                abs(form - taken) > MIN_AMOUNT_GAP_PAISE
                for form in forms
                for taken in self._taken
            ):
                self._taken.extend(forms)
                return paise
        raise ValueError("could not find a well-separated amount")

    def reserve_amount(self, paise: int) -> int:
        """Register a derived amount, so later draws steer clear of it.

        No separation check here: these are computed from an invoice amount
        that has already been checked, and the link between them is the whole
        point of the scenario.
        """
        self._taken.extend(self._reachable(paise))
        return paise

    @staticmethod
    def _reachable(paise: int) -> list[int]:
        """Everything the settlement formulas can turn this amount into."""
        return [paise, *(d.net_paise for d in settlement.all_formulas(paise))]

    def invoice_dates(
        self, earliest: date = EARLIEST_INVOICE, latest: date = LATEST_INVOICE
    ) -> tuple[date, date]:
        span = (latest - earliest).days
        issued = earliest + timedelta(days=self.rng.randrange(span))
        return issued, issued + timedelta(days=PAYMENT_TERM_DAYS)

    # -- writers ----------------------------------------------------------

    def add_invoice(
        self,
        company: Counterparty,
        amount_paise: int,
        scenario: str,
        *,
        dates: tuple[date, date] | None = None,
    ) -> Invoice:
        issued, due = dates or self.invoice_dates()
        no = self.invoice_no()
        inv = Invoice(
            id=no,
            split=self.split,
            invoice_no=no,
            counterparty_name=company.canonical,
            counterparty_name_clean=company.clean,
            amount_paise=amount_paise,
            invoice_date=issued,
            due_date=due,
            scenario=scenario,
        )
        self.data.invoices.append(inv)
        return inv

    def add_txn(
        self,
        description: str,
        amount_paise: int,
        value_date: date,
        scenario: str,
        *,
        source: str = "bank",
        utr: str | None = None,
        name_clean: str | None = None,
        txn_id: str | None = None,
    ) -> Transaction:
        if value_date > ANCHOR:
            raise ValueError(f"{value_date} is in the future; the input guardrail rejects it")

        txn = Transaction(
            id=txn_id or self.txn_id(),
            split=self.split,
            txn_ref=utr or "",
            description_raw=description,
            counterparty_name_clean=name_clean,
            amount_paise=amount_paise,
            value_date=value_date,
            source=source,
            utr=utr,
            scenario=scenario,
        )
        self.data.transactions.append(txn)
        return txn

    def add_settlement(
        self,
        txn: Transaction,
        deduction: settlement.Deduction,
        *,
        settled_on: date | None = None,
        batch_utr: str | None = None,
    ) -> Settlement:
        s = Settlement(
            id=self.settlement_id(),
            txn_id=txn.id,
            gross_paise=deduction.gross_paise,
            fee_paise=deduction.fee_paise,
            gst_on_fee_paise=deduction.gst_on_fee_paise,
            tds_paise=deduction.tds_paise,
            net_paise=deduction.net_paise,
            formula_used=deduction.formula,
            settled_on=settled_on or txn.value_date,
            batch_utr=batch_utr,
        )
        self.data.settlements.append(s)
        return s

    def add_truth(
        self,
        invoice: Invoice,
        outcome: Outcome,
        note: str,
        *,
        txn_ids: list[str] | None = None,
        reason: Reason | None = None,
    ) -> None:
        self.data.truth.append(
            TruthEntry(
                invoice_id=invoice.id,
                split=self.split,
                scenario=invoice.scenario,
                expected_outcome=outcome,
                expected_txn_ids=txn_ids or [],
                expected_reason_code=reason,
                note=note,
            )
        )

    # -- helpers ----------------------------------------------------------

    def paid_on(self, due: date, low: int = -3, high: int = 12) -> date:
        """A normal payment date: a little early, usually a little late."""
        day = due + timedelta(days=self.rng.randrange(low, high + 1))
        return min(day, ANCHOR)

    def description(
        self, company: Counterparty, utr: str, *, reference: str | None = None
    ) -> str:
        """A bank narration line, in one of the shapes Indian feeds produce."""
        name = company.bank_name(self.rng)
        digits = self.rng.randrange(10**11, 10**12)

        if reference:
            template = self.rng.choice(
                [
                    f"NEFT/{name}/{utr} {reference}",
                    f"RTGS/{name}/{reference}/{utr}",
                    f"NEFT-{name}-{utr}-{reference}",
                    f"IMPS/{digits}/{name}/{reference}",
                ]
            )
        else:
            template = self.rng.choice(
                [
                    f"NEFT/{name}/{utr}",
                    f"NEFT-{name}-{utr}",
                    f"RTGS/{name}/{utr}",
                    f"IMPS/{digits}/{name}",
                    f"MMT/IMPS/{digits}/{name}/HDFC",
                    f"ACH C- {name}",
                ]
            )
        return template


# ---------------------------------------------------------------------------
# The builders. One per scenario in the plan's section 9 table.
# ---------------------------------------------------------------------------


def exact_reference(ctx: Ctx, count: int) -> None:
    """The invoice number is written in the payment note. The fast path."""
    for _ in range(count):
        company = ctx.company()
        inv = ctx.add_invoice(company, ctx.amount(), "exact_reference")
        utr = ctx.utr()
        txn = ctx.add_txn(
            ctx.description(company, utr, reference=inv.invoice_no),
            inv.amount_paise,
            ctx.paid_on(inv.due_date),
            "exact_reference",
            utr=utr,
            name_clean=company.clean,
        )
        ctx.add_truth(
            inv,
            Outcome.AUTO,
            f"Invoice number {inv.invoice_no} appears in the payment note",
            txn_ids=[txn.id],
            reason=Reason.MATCHED_REFERENCE,
        )


def clean_name_amount(ctx: Ctx, count: int) -> None:
    """No reference, but the name is clean and the amount is exact."""
    for _ in range(count):
        company = ctx.company()
        inv = ctx.add_invoice(company, ctx.amount(), "clean_name_amount")
        utr = ctx.utr()
        txn = ctx.add_txn(
            f"NEFT/{company.clean}/{utr}",
            inv.amount_paise,
            ctx.paid_on(inv.due_date),
            "clean_name_amount",
            utr=utr,
            name_clean=company.clean,
        )
        ctx.add_truth(
            inv,
            Outcome.AUTO,
            "Name and amount both match exactly, no reference needed",
            txn_ids=[txn.id],
            reason=Reason.MATCHED_NAME_AMOUNT,
        )


def alias_variation(ctx: Ctx, count: int) -> None:
    """The bank sends a mangled version of the name. Exact amount."""
    for _ in range(count):
        company = ctx.company()
        inv = ctx.add_invoice(company, ctx.amount(), "alias_variation")
        utr = ctx.utr()
        variant = company.bank_name(ctx.rng)
        txn = ctx.add_txn(
            f"NEFT/{variant}/{utr}",
            inv.amount_paise,
            ctx.paid_on(inv.due_date),
            "alias_variation",
            utr=utr,
            name_clean=clean_name(variant),
        )
        ctx.add_truth(
            inv,
            Outcome.AUTO,
            f"Bank name {variant!r} is a variant of {company.clean!r}",
            txn_ids=[txn.id],
            reason=Reason.MATCHED_ALIAS,
        )


def tds_deduction(ctx: Ctx, count: int) -> None:
    """The customer legally held back tax, so less money arrived."""
    for i in range(count):
        company = ctx.company()
        inv = ctx.add_invoice(company, ctx.amount(), "tds_deduction")

        rate_name = "TDS_2PCT" if i % 3 != 2 else "TDS_10PCT"
        deduction = settlement.tds(inv.amount_paise, rate_name)
        ctx.reserve_amount(deduction.net_paise)

        utr = ctx.utr()
        txn = ctx.add_txn(
            ctx.description(company, utr),
            deduction.net_paise,
            ctx.paid_on(inv.due_date),
            "tds_deduction",
            utr=utr,
            name_clean=clean_name(company.bank_name(ctx.rng)),
        )
        ctx.add_settlement(txn, deduction)
        ctx.add_truth(
            inv,
            Outcome.AUTO,
            deduction.describe(),
            txn_ids=[txn.id],
            reason=Reason.TDS_2PCT if rate_name == "TDS_2PCT" else Reason.TDS_10PCT,
        )


def gateway_fee(ctx: Ctx, count: int) -> None:
    """Paid by card or UPI, so the gateway kept its fee and the GST on it."""
    for _ in range(count):
        company = ctx.company()
        inv = ctx.add_invoice(company, ctx.amount(), "gateway_fee")

        deduction = settlement.gateway(inv.amount_paise)
        ctx.reserve_amount(deduction.net_paise)

        utr = ctx.utr()
        settled = ctx.paid_on(inv.due_date)
        txn = ctx.add_txn(
            f"RAZORPAY SETTLEMENT {company.bank_name(ctx.rng)} {utr}",
            deduction.net_paise,
            settled,
            "gateway_fee",
            source="gateway",
            utr=utr,
            name_clean=clean_name(company.bank_name(ctx.rng)),
        )
        ctx.add_settlement(txn, deduction, settled_on=settled)
        ctx.add_truth(
            inv,
            Outcome.AUTO,
            deduction.describe(),
            txn_ids=[txn.id],
            reason=Reason.MDR_GST,
        )


def partial_payment(ctx: Ctx, count: int) -> None:
    """One bill, two instalments. Neither transaction matches on its own."""
    for _ in range(count):
        company = ctx.company()
        inv = ctx.add_invoice(company, ctx.amount(20_000, 400_000), "partial_payment")

        first_share = ctx.rng.choice([40, 50, 60, 65])
        first = ctx.reserve_amount(inv.amount_paise * first_share // 100)
        second = ctx.reserve_amount(inv.amount_paise - first)

        due = inv.due_date
        t1 = ctx.add_txn(
            ctx.description(company, (u1 := ctx.utr())),
            first,
            ctx.paid_on(due, -2, 4),
            "partial_payment",
            utr=u1,
            name_clean=clean_name(company.bank_name(ctx.rng)),
        )
        t2 = ctx.add_txn(
            ctx.description(company, (u2 := ctx.utr())),
            second,
            ctx.paid_on(due, 10, 25),
            "partial_payment",
            utr=u2,
            name_clean=clean_name(company.bank_name(ctx.rng)),
        )
        ctx.add_truth(
            inv,
            Outcome.AUTO,
            f"Paid in two instalments, {first_share}% then {100 - first_share}%",
            txn_ids=[t1.id, t2.id],
            reason=Reason.PARTIAL_PAYMENT,
        )


def combined_payment(ctx: Ctx, count: int, group: int = 2) -> None:
    """Several bills, one payment. The transaction is a multiple of each bill."""
    remaining = count
    while remaining > 0:
        size = min(group, remaining)
        remaining -= size

        company = ctx.company()
        invoices = [
            ctx.add_invoice(company, ctx.amount(), "combined_payment")
            for _ in range(size)
        ]
        total = ctx.reserve_amount(sum(i.amount_paise for i in invoices))
        latest_due = max(i.due_date for i in invoices)

        utr = ctx.utr()
        txn = ctx.add_txn(
            ctx.description(company, utr),
            total,
            ctx.paid_on(latest_due),
            "combined_payment",
            utr=utr,
            name_clean=clean_name(company.bank_name(ctx.rng)),
        )
        for inv in invoices:
            ctx.add_truth(
                inv,
                Outcome.AUTO,
                f"One payment settles {size} invoices for {company.clean}",
                txn_ids=[txn.id],
                reason=Reason.COMBINED_PAYMENT,
            )


def batched_settlement(ctx: Ctx, count: int, group: int = 3) -> None:
    """The gateway lumps several customer payments into one bank deposit.

    Each invoice has its own gateway record. The bank sees only the batch
    total under a single UTR, which identifies the batch and not any customer.
    """
    remaining = count
    while remaining > 0:
        size = min(group, remaining)
        remaining -= size

        batch_utr = ctx.utr()

        # A gateway batch is the payments made on one day, so the invoices in
        # it were due around the same time. Giving them unrelated due dates
        # made some of them look 47 days late for no reason other than how the
        # generator picked them, which is a fact about our code and not about
        # reconciliation.
        _, batch_due = ctx.invoice_dates(date(2026, 5, 1), date(2026, 7, 1))
        settled_on = min(ANCHOR, batch_due + timedelta(days=ctx.rng.randrange(0, 6)))

        gateway_txns: list[Transaction] = []
        invoices: list[Invoice] = []
        net_total = 0

        for _ in range(size):
            company = ctx.company()
            due = batch_due + timedelta(days=ctx.rng.randrange(-6, 4))
            inv = ctx.add_invoice(
                company,
                ctx.amount(),
                "batched_settlement",
                dates=(due - timedelta(days=PAYMENT_TERM_DAYS), due),
            )
            invoices.append(inv)

            deduction = settlement.gateway(inv.amount_paise)
            ctx.reserve_amount(deduction.net_paise)
            net_total += deduction.net_paise

            txn = ctx.add_txn(
                f"RZPY PMT {company.bank_name(ctx.rng)} BATCH {batch_utr}",
                deduction.net_paise,
                settled_on,
                "batched_settlement",
                source="gateway",
                utr=batch_utr,
                name_clean=clean_name(company.bank_name(ctx.rng)),
            )
            ctx.add_settlement(txn, deduction, settled_on=settled_on, batch_utr=batch_utr)
            gateway_txns.append(txn)

        # The one line the bank actually shows. It carries no customer name.
        ctx.reserve_amount(net_total)
        ctx.add_txn(
            f"RAZORPAY SETTLEMENT UTR {batch_utr}",
            net_total,
            min(ANCHOR, settled_on + timedelta(days=2)),   # T+2
            "batched_settlement",
            source="bank",
            utr=batch_utr,
            name_clean=None,
        )

        for inv, txn in zip(invoices, gateway_txns):
            ctx.add_truth(
                inv,
                Outcome.AUTO,
                f"Gateway payment inside batch {batch_utr}, settled T+2",
                txn_ids=[txn.id],
                reason=Reason.BATCHED_SETTLEMENT,
            )


def duplicate_transaction(ctx: Ctx, count: int) -> None:
    """The same payment appears twice, within the 48 hour duplicate window."""
    for _ in range(count):
        company = ctx.company()
        inv = ctx.add_invoice(company, ctx.amount(), "duplicate_transaction")

        paid = ctx.paid_on(inv.due_date)
        utr = ctx.utr()
        description = ctx.description(company, utr)

        t1 = ctx.add_txn(
            description,
            inv.amount_paise,
            paid,
            "duplicate_transaction",
            utr=utr,
            name_clean=clean_name(company.bank_name(ctx.rng)),
        )
        t2 = ctx.add_txn(
            description,
            inv.amount_paise,
            min(paid + timedelta(days=1), ANCHOR),
            "duplicate_transaction",
            utr=utr,
            name_clean=t1.counterparty_name_clean,
        )
        ctx.add_truth(
            inv,
            Outcome.REVIEW,
            "Same amount and counterparty twice within 48 hours; both flagged",
            txn_ids=[t1.id, t2.id],
            reason=Reason.DUPLICATE_TRANSACTION,
        )


def short_payment(ctx: Ctx, count: int) -> None:
    """Less money arrived and no known formula explains the gap. A real exception."""
    for _ in range(count):
        company = ctx.company()
        inv = ctx.add_invoice(company, ctx.amount(20_000, 400_000), "short_payment")

        shortfall = _unexplained_shortfall(ctx, inv.amount_paise)
        received = ctx.reserve_amount(inv.amount_paise - shortfall)

        utr = ctx.utr()
        txn = ctx.add_txn(
            ctx.description(company, utr),
            received,
            ctx.paid_on(inv.due_date),
            "short_payment",
            utr=utr,
            name_clean=clean_name(company.bank_name(ctx.rng)),
        )
        ctx.add_truth(
            inv,
            Outcome.EXCEPTION,
            f"{shortfall // 100} rupees short and no deduction formula explains it",
            txn_ids=[txn.id],
            reason=Reason.AMOUNT_GAP_UNEXPLAINED,
        )


def _unexplained_shortfall(ctx: Ctx, gross_paise: int) -> int:
    """A gap that no known deduction can account for.

    Checked against the real formulas rather than assumed, so the answer key
    cannot claim 'unexplained' about a gap the pipeline can in fact explain.
    """
    for _ in range(500):
        shortfall = rupees(ctx.rng.randrange(3, 90) * 10)
        if shortfall >= gross_paise:
            continue
        if settlement.explain(gross_paise, gross_paise - shortfall) is None:
            return shortfall
    raise ValueError("could not build an unexplained shortfall")


def no_payment(ctx: Ctx, count: int) -> None:
    """The customer simply has not paid. There is no right answer to find."""
    for _ in range(count):
        company = ctx.company()
        inv = ctx.add_invoice(company, ctx.amount(), "no_payment")
        ctx.add_truth(
            inv,
            Outcome.EXCEPTION,
            "No payment received for this invoice",
            reason=Reason.NO_PAYMENT_FOUND,
        )


def identical_invoices(ctx: Ctx, count: int, group: int = 2) -> None:
    """Two identical bills, one payment. A high score hiding a coin flip.

    This is the scenario margin exists for. Both invoices score almost the
    same against the one transaction, so neither may be auto-approved.
    """
    remaining = count
    while remaining > 0:
        size = min(group, remaining)
        remaining -= size

        company = ctx.company()
        amount = ctx.amount()
        issued, due = ctx.invoice_dates()

        invoices = [
            ctx.add_invoice(company, amount, "identical_invoices", dates=(issued, due))
            for _ in range(size)
        ]

        utr = ctx.utr()
        txn = ctx.add_txn(
            ctx.description(company, utr),
            amount,
            ctx.paid_on(due),
            "identical_invoices",
            utr=utr,
            name_clean=clean_name(company.bank_name(ctx.rng)),
        )
        for inv in invoices:
            ctx.add_truth(
                inv,
                Outcome.AMBIGUOUS,
                f"{size} invoices for {company.clean} at the same amount and date, one payment",
                txn_ids=[txn.id],
                reason=Reason.AMBIGUOUS_CANDIDATES,
            )


def date_out_of_window(ctx: Ctx, count: int) -> None:
    """Name and amount match perfectly, but the timing makes no sense."""
    for i in range(count):
        company = ctx.company()

        if i % 2 == 0:
            # Very late: an April invoice paid nearly three months past due.
            issued, due = ctx.invoice_dates(date(2026, 4, 1), date(2026, 4, 20))
            paid = due + timedelta(days=ctx.rng.randrange(70, 95))
            note = f"Paid {(paid - due).days} days after the due date"
        else:
            # Paid well before the invoice even existed. Suspicious, not late.
            issued, due = ctx.invoice_dates(date(2026, 6, 1), date(2026, 7, 10))
            paid = issued - timedelta(days=ctx.rng.randrange(20, 45))
            note = f"Paid {(issued - paid).days} days before the invoice was raised"

        inv = ctx.add_invoice(
            company, ctx.amount(), "date_out_of_window", dates=(issued, due)
        )
        utr = ctx.utr()
        txn = ctx.add_txn(
            ctx.description(company, utr),
            inv.amount_paise,
            paid,
            "date_out_of_window",
            utr=utr,
            name_clean=clean_name(company.bank_name(ctx.rng)),
        )
        ctx.add_truth(
            inv,
            Outcome.EXCEPTION,
            note,
            txn_ids=[txn.id],
            reason=Reason.DATE_OUT_OF_WINDOW,
        )


def value_ceiling(ctx: Ctx, count: int) -> None:
    """A perfect match that is still too large to approve without a human.

    A wrong 500 rupee match is annoying. A wrong 5 lakh match is how fraud
    gets through. The score is irrelevant here, which is the point.
    """
    for _ in range(count):
        company = ctx.company()
        inv = ctx.add_invoice(
            company, ctx.amount(550_000, 2_000_000), "value_ceiling"
        )
        utr = ctx.utr()
        txn = ctx.add_txn(
            ctx.description(company, utr, reference=inv.invoice_no),
            inv.amount_paise,
            ctx.paid_on(inv.due_date),
            "value_ceiling",
            utr=utr,
            name_clean=company.clean,
        )
        ctx.add_truth(
            inv,
            Outcome.REVIEW,
            f"Perfect match but above the {VALUE_CEILING_PAISE // 100:,} rupee ceiling",
            txn_ids=[txn.id],
            reason=Reason.VALUE_CEILING,
        )


def prior_settlement(ctx: Ctx, count: int, companies: list[Counterparty] | None = None) -> None:
    """Invoices this business already settled, before the graded batch.

    This is what the alias seed set is *for*: it stands in for the ledger a
    real finance team already has. It teaches the alias table the name forms
    a customer's bank uses, and it is what the new-counterparty guardrail
    checks against.

    Three records per customer the graded batch will meet, because the
    guardrail asks for three prior settlements before it will automate anyone.
    Anything less and it blocks most of the batch - not because the matcher is
    wrong, but because our synthetic history is too shallow to answer the
    question the rule is asking.
    """
    pool = companies or [ctx.company() for _ in range(count)]

    for company in pool[:count]:
        for _ in range(PRIOR_SETTLEMENTS_PER_CUSTOMER):
            inv = ctx.add_invoice(
                company,
                ctx.amount(5_000, 900_000),
                "prior_settlement",
                dates=ctx.invoice_dates(date(2025, 6, 1), date(2026, 3, 1)),
            )
            variant = company.bank_name(ctx.rng)
            utr = ctx.utr()
            txn = ctx.add_txn(
                f"NEFT/{variant}/{utr}",
                inv.amount_paise,
                ctx.paid_on(inv.due_date),
                "prior_settlement",
                utr=utr,
                name_clean=clean_name(variant),
            )
            ctx.add_truth(
                inv,
                Outcome.AUTO,
                f"Previously settled with {company.clean}, paid as {variant!r}",
                txn_ids=[txn.id],
                reason=Reason.MATCHED_ALIAS,
            )


BUILDERS = {
    "prior_settlement": prior_settlement,
    "exact_reference": exact_reference,
    "clean_name_amount": clean_name_amount,
    "alias_variation": alias_variation,
    "tds_deduction": tds_deduction,
    "gateway_fee": gateway_fee,
    "partial_payment": partial_payment,
    "combined_payment": combined_payment,
    "batched_settlement": batched_settlement,
    "duplicate_transaction": duplicate_transaction,
    "short_payment": short_payment,
    "no_payment": no_payment,
    "identical_invoices": identical_invoices,
    "date_out_of_window": date_out_of_window,
    "value_ceiling": value_ceiling,
}

__all__ = ["BUILDERS", "Ctx", "ANCHOR", "VALUE_CEILING_PAISE", "TOLERANCE_PAISE"]
