"""Turning messy company names into something comparable.

The bill says `ABC Technologies Pvt Ltd`. The bank says `NEFT/ABCTECHPVTLTD/882910`.
Everything here exists to close that gap.
"""

import re
from functools import lru_cache

# Legal suffixes carry no identifying information, so they only add noise.
LEGAL_TOKENS = {
    "PVT",
    "PRIVATE",
    "LTD",
    "LIMITED",
    "LLP",
    "LLC",
    "INC",
    "INCORPORATED",
    "CORP",
    "CORPORATION",
    "PLC",
    "GMBH",
}

# Bank text is littered with these. They are transport, not identity.
CHANNEL_TOKENS = {
    "NEFT",
    "RTGS",
    "IMPS",
    "UPI",
    "ACH",
    "MMT",
    "CMS",
    "INF",
    "TRF",
    "CR",
    "DR",
    "PAYMENT",
    "PMT",
    "SETTLEMENT",
    "RAZORPAY",
    "RZPY",
    "UTR",
    "REF",
    "REFNO",
    "TXN",
    "INV",
    "BY",
    "FROM",
    "TO",
    "FAV",
}

# The bank half of a UPI id, e.g. the OKAXIS in abctech@okaxis.
UPI_HANDLES = {
    "OKAXIS",
    "OKHDFCBANK",
    "OKICICI",
    "OKSBI",
    "YBL",
    "IBL",
    "AXL",
    "PAYTM",
    "APL",
    "UPI",
}

_PUNCT = re.compile(r"[^A-Z0-9]+")
_LONG_DIGITS = re.compile(r"\b\d{4,}\b")


@lru_cache(maxsize=8192)
def clean_name(raw: str) -> str:
    """Uppercase, drop punctuation and legal suffixes, collapse spaces.

    Cached because the scorer calls it on the same names constantly.
    Plan section 15.4.
    """
    if not raw:
        return ""

    text = _PUNCT.sub(" ", raw.upper())
    tokens = [t for t in text.split() if t and t not in LEGAL_TOKENS]
    return " ".join(tokens)


@lru_cache(maxsize=8192)
def squash(raw: str) -> str:
    """Everything joined up, the way a bank field with no spaces looks."""
    return clean_name(raw).replace(" ", "")


def extract_refs(description: str) -> list[str]:
    """Anything in the text that could be an invoice number or a UTR.

    Deliberately generous. A wrong candidate costs one failed comparison;
    a missed one costs the whole fast path.
    """
    text = description.upper()
    refs: list[str] = []

    # INV-0231, INV/0231, INV 0231
    refs += [
        f"INV-{m}" for m in re.findall(r"INV[\s/_-]?(\d{3,6})", text)
    ]
    # Bare alphanumeric codes long enough to be a reference, not a date.
    refs += re.findall(r"\b[A-Z]{2,6}\d{6,22}\b", text)
    # Long digit runs, which is what a UTR looks like once the bank mangles it.
    refs += re.findall(r"\b\d{12,22}\b", text)

    seen: set[str] = set()
    out: list[str] = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


@lru_cache(maxsize=8192)
def name_from_bank_text(description: str) -> str:
    """Best guess at the counterparty inside a bank description line.

    `NEFT/ABCTECHPVTLTD/882910` gives `ABCTECHPVTLTD`. We strip the references,
    the channel words and anything containing a digit. Whatever survives is
    almost always the name.

    Limitation: a real company with a digit in its name (3M, 24x7 Logistics)
    would be dropped. None exist in our dataset.
    """
    text = description.upper()

    # Pull the references out first, so a UTR cannot end up inside the name.
    for ref in extract_refs(text):
        text = text.replace(ref, " ")

    text = _LONG_DIGITS.sub(" ", text)
    text = _PUNCT.sub(" ", text)

    candidates = [
        t
        for t in text.split()
        if len(t) >= 3
        and t not in CHANNEL_TOKENS
        and t not in UPI_HANDLES
        # Any leftover token with a digit in it is a code, not a name.
        and not any(ch.isdigit() for ch in t)
    ]
    if not candidates:
        return ""

    # Adjacent leftover words usually belong to the same name.
    return clean_name(" ".join(candidates))
