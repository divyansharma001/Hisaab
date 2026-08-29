"""Money is integer paise everywhere. Floats are never allowed near it.

100 paise = 1 rupee. A rupee amount of 10,000 is 1,000,000 paise.
"""

from decimal import ROUND_HALF_UP, Decimal

PAISE_PER_RUPEE = 100

# Plan section 2.7. Anything under this is rounding, not a real gap.
TOLERANCE_PAISE = 100


def rupees(amount: int | str) -> int:
    """Whole rupees to paise."""
    return int(amount) * PAISE_PER_RUPEE


def pct_of(amount_paise: int, rate: Decimal) -> int:
    """A percentage of an amount, rounded half up to whole paise.

    Decimal, not float, so 2% of 1,000,000 is never 19999.999999998.
    """
    return int(
        (Decimal(amount_paise) * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def fmt(amount_paise: int) -> str:
    """Paise to a readable rupee string, in the Indian digit grouping."""
    sign = "-" if amount_paise < 0 else ""
    whole, frac = divmod(abs(amount_paise), PAISE_PER_RUPEE)

    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups) + "," + tail

    return f"{sign}Rs {digits}.{frac:02d}"
