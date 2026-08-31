"""Money is integer paise everywhere. Floats are never allowed near it.

100 paise = 1 rupee. A rupee amount of 10,000 is 1,000,000 paise.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

PAISE_PER_RUPEE = 100

# Plan section 2.7. Anything under this is rounding, not a real gap.
TOLERANCE_PAISE = 100


def rupees(amount: int | str) -> int:
    """Whole rupees to paise."""
    return int(amount) * PAISE_PER_RUPEE


def parse_amount(text: str) -> int:
    """A typed rupee amount to integer paise.

    Everything downstream assumes integer paise, so this is the one place a
    human's "1,20,500.75" becomes 12050075. It goes through Decimal, never
    float: `int(float("1234.35") * 100)` is 123434, and a reconciliation tool
    that loses a paise on entry has no business telling anyone their books are
    wrong.

    Raises ValueError with something a person can act on.
    """
    cleaned = (text or "").strip().replace(",", "").replace("\u20b9", "")
    for prefix in ("Rs.", "Rs", "INR", "rs"):
        if cleaned.lower().startswith(prefix.lower()):
            cleaned = cleaned[len(prefix):].strip()
            break

    if not cleaned:
        raise ValueError("enter an amount")

    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"{text!r} is not an amount") from None

    if amount <= 0:
        raise ValueError("the amount has to be more than zero")
    if amount.as_tuple().exponent < -2:
        raise ValueError("amounts go to paise, so at most two decimal places")
    if amount > Decimal("1e11"):
        raise ValueError("that amount is implausibly large")

    return int(amount.scaleb(2).to_integral_value(rounding=ROUND_HALF_UP))


def pct_of(amount_paise: int, rate: Decimal) -> int:
    """A percentage of an amount, rounded half up to whole paise.

    Decimal, not float, so 2% of 1,000,000 is never 19999.999999998.
    """
    return int(
        (Decimal(amount_paise) * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def fmt(amount_paise: int) -> str:
    """Paise to a readable rupee string, in the Indian digit grouping.

    Strict about the type on purpose. Postgres returns SUM() over BIGINT as a
    Decimal, and letting one through here fails deep inside a format string
    with "invalid format string" - which says nothing about where the wrong
    type came from.
    """
    if not isinstance(amount_paise, int):
        raise TypeError(
            f"money must be integer paise, got {type(amount_paise).__name__} "
            f"({amount_paise!r}). Cast it where it enters the code."
        )

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


# gpt-4o-mini list price, in paise per token. Kept here so the ablation table
# and the API quote the same rate rather than drifting apart.
RUPEES_PER_USD = 88
INPUT_PAISE_PER_TOKEN = 0.15 / 1_000_000 * RUPEES_PER_USD * 100
OUTPUT_PAISE_PER_TOKEN = 0.60 / 1_000_000 * RUPEES_PER_USD * 100


def llm_cost_paise(input_tokens: int, output_tokens: int) -> int:
    """What a run cost, rounded up to the paise. Rounding up so a reported
    cost is never lower than the real one."""
    exact = input_tokens * INPUT_PAISE_PER_TOKEN + output_tokens * OUTPUT_PAISE_PER_TOKEN
    return int(-(-exact // 1))
