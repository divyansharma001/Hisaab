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
    # Bank names appear as routing information in narration lines, not as the
    # counterparty. MMT/IMPS/312812345678/ABC TECH/HDFC is a payment to ABC
    # TECH, not to HDFC.
    "HDFC",
    "ICICI",
    "SBI",
    "AXIS",
    "KOTAK",
    "YESBANK",
    "IDFC",
    "INDUSIND",
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


# An IFSC code: four letters, a zero, then six more characters. HDFC0001234.
# It identifies the branch, appears on a large share of real narrations, and
# says nothing at all about which invoice is being paid.
IFSC = re.compile(r"^[A-Za-z]{4}0[A-Za-z0-9]{6}$")


def looks_like_invoice_ref(ref: str) -> bool:
    """Could this reference be an invoice number, rather than bank plumbing?

    A UTR, an IFSC code and an invoice number are all "references", but only
    one of them says anything about which bill is being paid.
    `KKBK290665407983` is the bank's id for the transfer and `HDFC0001234` is
    the branch; both are present on payments that have no invoice number at
    all.

    Getting this wrong is expensive in a specific way. A reference we cannot
    read is dropped and its weight is shared out; a reference we read and
    fail to match scores zero on the heaviest signal there is. So bank
    plumbing mistaken for an invoice number does not merely fail to help, it
    actively pushes a good match below the bar.

    Found by someone typing an ordinary narration into the scratch set:
    `NEFT/BRIGHTMETALS/HDFC0001234` matched on name and amount, TDS explained
    to the rupee, and still scored 0.39 because the IFSC was treated as an
    invoice number that did not match. Our own generated data hid it - the
    codes it writes are long enough to be caught by the digit test below.
    """
    if IFSC.match(ref):
        return False
    if any(sep in ref for sep in "-/_"):
        return True
    digits = "".join(ch for ch in ref if ch.isdigit())
    return len(digits) <= 8


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


def name_similarity(a: str, b: str) -> float:
    """How alike two company names are, 0 to 1.

    A plain token comparison is not enough here. Bank fields drop spaces, so
    `YAMUNA INSTRUMENTS` arrives as `YAMUNAINSTRUMENTS` - one token against
    two, which `token_set_ratio` scores 63 even though they are the same
    company. Comparing the squashed forms as well fixes that.

    Truncation is the other common shape: `HAMPIPOLY` for `HAMPI POLYMERS`.
    A prefix comparison catches it, but only once the shorter name is long
    enough that a prefix means something.
    """
    from rapidfuzz import fuzz

    if not a or not b:
        return 0.0

    a_clean, b_clean = clean_name(a), clean_name(b)
    if a_clean == b_clean:
        return 1.0

    a_squashed, b_squashed = squash(a), squash(b)
    if a_squashed == b_squashed:
        return 1.0

    scores = [
        fuzz.token_set_ratio(a_clean, b_clean),
        fuzz.ratio(a_squashed, b_squashed),
    ]

    # One name being a truncation of the other, e.g. HAMPIPOLY / HAMPIPOLYMERS.
    shorter, longer = sorted((a_squashed, b_squashed), key=len)
    if len(shorter) >= 6 and longer.startswith(shorter):
        scores.append(95.0)

    return max(scores) / 100
