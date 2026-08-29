"""The shapes the generator emits and the loader reads.

The same models are reused by the API layer later, so the JSON on disk, the
rows in Postgres and the JSON on the wire all agree by construction.
"""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class Split(StrEnum):
    """Three sets, never mixed. Plan section 9."""

    ALIAS_SEED = "alias_seed"   # 30. Fills the alias table. Never scored.
    TUNING = "tuning"           # 45. Grid search and threshold tuning.
    HELDOUT = "heldout"         # 85. The only numbers we report.


class Outcome(StrEnum):
    """What should happen to a record."""

    AUTO = "AUTO"
    REVIEW = "REVIEW"
    EXCEPTION = "EXCEPTION"
    AMBIGUOUS = "AMBIGUOUS"     # Truth only: correct behaviour is to refuse.


class Reason(StrEnum):
    """Reason codes. Section 19.7 scores us on getting these right, not just
    the verdict, because the reason is what a human actually acts on."""

    MATCHED_REFERENCE = "MATCHED_REFERENCE"
    MATCHED_NAME_AMOUNT = "MATCHED_NAME_AMOUNT"
    MATCHED_ALIAS = "MATCHED_ALIAS"
    TDS_2PCT = "TDS_2PCT"
    TDS_10PCT = "TDS_10PCT"
    MDR_GST = "MDR_GST"
    PARTIAL_PAYMENT = "PARTIAL_PAYMENT"
    COMBINED_PAYMENT = "COMBINED_PAYMENT"
    BATCHED_SETTLEMENT = "BATCHED_SETTLEMENT"
    DUPLICATE_TRANSACTION = "DUPLICATE_TRANSACTION"
    AMOUNT_GAP_UNEXPLAINED = "AMOUNT_GAP_UNEXPLAINED"
    NO_PAYMENT_FOUND = "NO_PAYMENT_FOUND"
    AMBIGUOUS_CANDIDATES = "AMBIGUOUS_CANDIDATES"
    DATE_OUT_OF_WINDOW = "DATE_OUT_OF_WINDOW"
    VALUE_CEILING = "VALUE_CEILING"


class Invoice(BaseModel):
    id: str
    split: Split
    invoice_no: str
    counterparty_name: str
    counterparty_name_clean: str
    amount_paise: int = Field(gt=0)
    currency: str = "INR"
    invoice_date: date
    due_date: date
    status: str = "open"
    scenario: str


class Transaction(BaseModel):
    id: str
    split: Split
    txn_ref: str
    description_raw: str
    counterparty_name_clean: str | None = None
    amount_paise: int = Field(gt=0)
    currency: str = "INR"
    value_date: date
    source: str          # bank | gateway
    utr: str | None = None
    scenario: str


class Settlement(BaseModel):
    id: str
    txn_id: str
    gross_paise: int
    fee_paise: int = 0
    gst_on_fee_paise: int = 0
    tds_paise: int = 0
    net_paise: int
    formula_used: str
    settled_on: date | None = None
    batch_utr: str | None = None


class TruthEntry(BaseModel):
    """One row of the answer key. Written by the same function that writes
    the data, so it costs nothing and cannot drift."""

    invoice_id: str
    split: Split
    scenario: str
    expected_outcome: Outcome
    expected_txn_ids: list[str] = Field(default_factory=list)
    expected_reason_code: Reason | None = None
    note: str


class Dataset(BaseModel):
    split: Split
    seed: int
    invoices: list[Invoice] = Field(default_factory=list)
    transactions: list[Transaction] = Field(default_factory=list)
    settlements: list[Settlement] = Field(default_factory=list)
    truth: list[TruthEntry] = Field(default_factory=list)

    def extend(self, other: "Dataset") -> None:
        self.invoices.extend(other.invoices)
        self.transactions.extend(other.transactions)
        self.settlements.extend(other.settlements)
        self.truth.extend(other.truth)

    def scenario_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for inv in self.invoices:
            counts[inv.scenario] = counts.get(inv.scenario, 0) + 1
        return counts
