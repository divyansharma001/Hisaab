"""Narrowing each invoice down to the payments worth scoring. Plan section 5, box 4.

**Three passes, not one.** A single amount window around the invoice value
would throw away the exact cases the project exists to show: a combined payment
is three times the invoice, a partial is a little over half of it. One window
cannot hold all three shapes, and the right candidate would be gone one step
before scoring. That was bug 1 in plan section 18.

Every candidate is tagged with the pass that found it. Without the tag the
scorer has no way to know whether an amount three times too big is a
combined payment or simply wrong.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.intake import Batch, NormInvoice, NormTxn
from app.names import name_similarity

# Same counterparty, near enough. Used to gate the partial and combined passes,
# where the amount alone says nothing useful.
SAME_PARTY_FLOOR = 0.75


class Pass(StrEnum):
    ONE_TO_ONE = "one_to_one"   # 0.75x to 1.10x, deductions live in here
    PARTIAL = "partial"         # smaller than the bill, an instalment
    COMBINED = "combined"       # bigger than the bill, covering several of them


ONE_TO_ONE_WINDOW = (0.75, 1.10)

# A partial is anything meaningfully under the bill. The floor only keeps
# obvious noise out.
PARTIAL_WINDOW = (0.05, 0.95)

# A combined payment starts just above the bill. Its ceiling is not a fixed
# multiple - it is everything this customer owes.
#
# The plan said 1.10x to 5.0x. That silently drops the lopsided case: one
# payment covering a Rs 57,600 bill and a Rs 3,71,750 bill is 7.45x the small
# one, so the small invoice never sees its own payment. A customer cannot pay
# more than they owe, and that is a real ceiling rather than a guessed one.
COMBINED_FLOOR = 1.05

# The partial and combined passes need a name match; the one-to-one pass does
# not, because a clean amount match is evidence on its own.
NEEDS_SAME_PARTY = {Pass.PARTIAL, Pass.COMBINED}


@dataclass
class Candidate:
    txn: NormTxn
    passes: set[Pass]

    @property
    def id(self) -> str:
        return self.txn.id


def _in_window(invoice: NormInvoice, txn: NormTxn, window: tuple[float, float]) -> bool:
    low, high = window
    return (
        invoice.amount_paise * low <= txn.amount_paise <= invoice.amount_paise * high
    )


def block(invoice: NormInvoice, batch: Batch) -> list[Candidate]:
    """Every transaction any pass considers plausible for this invoice."""
    found: dict[str, Candidate] = {}

    # The most one payment from this customer could ever settle.
    owed_in_total = invoice.amount_paise + sum(
        other.amount_paise for other in counterparty_invoices(invoice, batch)
    )

    for txn in batch.transactions:
        if txn.currency != invoice.currency:
            continue

        same_party = name_similarity(txn.name_clean, invoice.name_clean) >= SAME_PARTY_FLOOR

        hits: set[Pass] = set()

        if _in_window(invoice, txn, ONE_TO_ONE_WINDOW):
            hits.add(Pass.ONE_TO_ONE)

        if same_party:
            if _in_window(invoice, txn, PARTIAL_WINDOW):
                hits.add(Pass.PARTIAL)
            if (
                txn.amount_paise > invoice.amount_paise * COMBINED_FLOOR
                and txn.amount_paise <= owed_in_total
            ):
                hits.add(Pass.COMBINED)

        if not hits:
            continue

        if txn.id in found:
            found[txn.id].passes |= hits
        else:
            found[txn.id] = Candidate(txn=txn, passes=hits)

    # A transaction naming this invoice is always worth scoring, whatever its
    # amount. Otherwise a referenced payment with an odd value is dropped
    # before the fast path ever sees it.
    for txn in batch.transactions:
        if txn.id in found:
            continue
        if invoice.invoice_no in txn.refs:
            found[txn.id] = Candidate(txn=txn, passes={Pass.ONE_TO_ONE})

    return list(found.values())


def counterparty_invoices(
    invoice: NormInvoice, batch: Batch, floor: float = SAME_PARTY_FLOOR
) -> list[NormInvoice]:
    """Other open invoices for the same customer. Needed to spot a combined payment."""
    return [
        other
        for other in batch.invoices
        if other.id != invoice.id
        and name_similarity(other.name_clean, invoice.name_clean) >= floor
    ]


def counterparty_txns(
    invoice: NormInvoice, batch: Batch, floor: float = SAME_PARTY_FLOOR
) -> list[NormTxn]:
    """Payments that look like they came from this customer. Needed for partials."""
    return [
        txn
        for txn in batch.transactions
        if name_similarity(txn.name_clean, invoice.name_clean) >= floor
    ]
