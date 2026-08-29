"""Loading a batch and cleaning it up. Plan section 5, box 1.

One rule here matters more than it looks: **the counterparty name is derived
from the raw bank narration, not read from a tidy column.** The generator does
store a clean name on each transaction, but using it would be testing a feed
we will never actually get. A real bank line is
`NEFT-YAMUNAINSTRUMENTS-UTIB834887998706-INV-0001` and nothing else.
"""

from dataclasses import dataclass, field
from datetime import date

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings
from app.dataset import Split
from app.names import clean_name, extract_refs, name_from_bank_text


@dataclass(frozen=True)
class NormInvoice:
    id: str
    invoice_no: str
    name_raw: str
    name_clean: str
    amount_paise: int
    currency: str
    invoice_date: date
    due_date: date
    scenario: str


@dataclass(frozen=True)
class NormTxn:
    id: str
    description_raw: str
    name_clean: str          # derived from the narration, not handed to us
    refs: tuple[str, ...]    # anything that could be an invoice number or UTR
    amount_paise: int
    currency: str
    value_date: date
    source: str
    utr: str | None
    scenario: str


@dataclass
class Batch:
    split: Split
    invoices: list[NormInvoice] = field(default_factory=list)
    transactions: list[NormTxn] = field(default_factory=list)

    def txn_by_id(self) -> dict[str, NormTxn]:
        return {t.id: t for t in self.transactions}

    def invoice_by_id(self) -> dict[str, NormInvoice]:
        return {i.id: i for i in self.invoices}


def normalise_invoice(row: dict) -> NormInvoice:
    return NormInvoice(
        id=row["id"],
        invoice_no=row["invoice_no"],
        name_raw=row["counterparty_name"],
        name_clean=clean_name(row["counterparty_name"]),
        amount_paise=row["amount_paise"],
        currency=row["currency"].strip(),
        invoice_date=row["invoice_date"],
        due_date=row["due_date"],
        scenario=row["scenario"],
    )


def normalise_txn(row: dict) -> NormTxn:
    description = row["description_raw"]
    return NormTxn(
        id=row["id"],
        description_raw=description,
        name_clean=name_from_bank_text(description),
        refs=tuple(extract_refs(description)),
        amount_paise=row["amount_paise"],
        currency=row["currency"].strip(),
        value_date=row["value_date"],
        source=row["source"],
        utr=row["utr"],
        scenario=row["scenario"],
    )


def load_batch(split: Split, database_url: str | None = None) -> Batch:
    url = database_url or get_settings().database_url

    with psycopg.connect(url, row_factory=dict_row) as conn:
        invoices = conn.execute(
            "SELECT * FROM invoices WHERE split = %s ORDER BY id", (split.value,)
        ).fetchall()
        transactions = conn.execute(
            "SELECT * FROM transactions WHERE split = %s ORDER BY id", (split.value,)
        ).fetchall()

    return Batch(
        split=split,
        invoices=[normalise_invoice(r) for r in invoices],
        transactions=[normalise_txn(r) for r in transactions],
    )
