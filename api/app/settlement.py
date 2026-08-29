"""Why the money that arrives is never the money that was billed.

Plan section 2.4. Every deduction here is correct and expected. A matcher that
does not model them turns dozens of clean matches into useless exceptions.

This module is shared by the data generator and the pipeline on purpose. Two
copies of these formulas would drift, and a drift here looks like a matching
bug rather than an arithmetic one.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.money import TOLERANCE_PAISE, pct_of

# What the gateway keeps for processing a card or UPI payment.
MDR_RATE = Decimal("0.02")

# Indian sales tax, charged by the gateway on its own fee. Not the GST on
# your invoice, which is a different thing entirely.
GST_ON_FEE_RATE = Decimal("0.18")

# Tax the customer must legally hold back and pay to the tax department.
TDS_RATES = {
    "TDS_2PCT": Decimal("0.02"),
    "TDS_10PCT": Decimal("0.10"),
}

NO_DEDUCTION = "NONE"


@dataclass(frozen=True)
class Deduction:
    """One way the gross could have shrunk on its way to the bank."""

    formula: str
    gross_paise: int
    fee_paise: int
    gst_on_fee_paise: int
    tds_paise: int

    @property
    def total_deducted_paise(self) -> int:
        return self.fee_paise + self.gst_on_fee_paise + self.tds_paise

    @property
    def net_paise(self) -> int:
        return self.gross_paise - self.total_deducted_paise

    def describe(self) -> str:
        """The sentence the UI shows. Every number here was computed by us."""
        from app.money import fmt

        parts = [fmt(self.gross_paise)]
        if self.fee_paise:
            parts.append(f"- {fmt(self.fee_paise)} MDR")
        if self.gst_on_fee_paise:
            parts.append(f"- {fmt(self.gst_on_fee_paise)} GST on MDR")
        if self.tds_paise:
            parts.append(f"- {fmt(self.tds_paise)} TDS")
        return f"{' '.join(parts)} = {fmt(self.net_paise)}"


def _gateway_fee(gross_paise: int) -> tuple[int, int]:
    fee = pct_of(gross_paise, MDR_RATE)
    return fee, pct_of(fee, GST_ON_FEE_RATE)


def none(gross_paise: int) -> Deduction:
    return Deduction(NO_DEDUCTION, gross_paise, 0, 0, 0)


def gateway(gross_paise: int) -> Deduction:
    """gross - MDR - GST on MDR"""
    fee, gst = _gateway_fee(gross_paise)
    return Deduction("MDR_GST", gross_paise, fee, gst, 0)


def tds(gross_paise: int, rate_name: str) -> Deduction:
    """gross - TDS"""
    amount = pct_of(gross_paise, TDS_RATES[rate_name])
    return Deduction(rate_name, gross_paise, 0, 0, amount)


def gateway_and_tds(gross_paise: int, rate_name: str = "TDS_2PCT") -> Deduction:
    """gross - MDR - GST on MDR - TDS"""
    fee, gst = _gateway_fee(gross_paise)
    amount = pct_of(gross_paise, TDS_RATES[rate_name])
    return Deduction(f"MDR_GST_{rate_name}", gross_paise, fee, gst, amount)


def all_formulas(gross_paise: int) -> list[Deduction]:
    """Every deduction shape we know about, cheapest first.

    Plan section 5, box 5. The pipeline tries each one and keeps the first
    that explains the gap to within the tolerance.
    """
    return [
        none(gross_paise),
        gateway(gross_paise),
        tds(gross_paise, "TDS_2PCT"),
        tds(gross_paise, "TDS_10PCT"),
        gateway_and_tds(gross_paise, "TDS_2PCT"),
        gateway_and_tds(gross_paise, "TDS_10PCT"),
    ]


def explain(gross_paise: int, received_paise: int) -> Deduction | None:
    """Which known deduction turns gross into received? None if nothing fits.

    A None here is the whole point of the AMOUNT_GAP_UNEXPLAINED reason code:
    money is missing and we cannot say why, so a human has to look.
    """
    for candidate in all_formulas(gross_paise):
        if abs(candidate.net_paise - received_paise) <= TOLERANCE_PAISE:
            return candidate
    return None
