"""The cash position. Plan section 12.

The track is *"run the books and the cash position"*. The second half does not
need a forecasting agent - once reconciliation has run, the position falls out
of data we already hold. Four numbers, four queries, no prediction and no new
risk:

- **Confirmed in** - what we auto-approved and can stand behind
- **Still owed** - no payment turned up that covers the invoice
- **Uncertain** - a payment probably exists, but we would not sign it off
- **Withheld** - MDR, GST on MDR and TDS taken out before the money landed

The split between *still owed* and *uncertain* is the point. Both are open
invoices, but they need different people: one is chased with the customer,
the other is cleared by a reviewer. Lumping them together, as an aging report
does, hides which is which.

**They are derived from the reason code we already produced, never from the
answer key.** A record counts as unpaid because the pipeline could not explain
the amount, not because we looked up what it really was.

*In flight* - gateway captured, bank has not shown it - is deliberately not
here. This dataset has no capture date separate from the settlement date, so
every honest version of that query returns zero. A card that can only ever
read zero is worse than no card.
"""

from dataclasses import dataclass, field

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings
from app.dataset import Split

# Reason codes that mean the money has not arrived, as opposed to arrived but
# unconfirmed. AMOUNT_GAP_UNEXPLAINED covers both "nobody paid" and "they paid
# short"; in both the business is still owed the difference.
UNPAID_REASONS = ("AMOUNT_GAP_UNEXPLAINED", "NO_PAYMENT_FOUND")

# The buckets a finance team actually uses.
AGING_BUCKETS = [
    ("0-30 days", 0, 30),
    ("31-60 days", 31, 60),
    ("61-90 days", 61, 90),
    ("90+ days", 91, 100_000),
]


@dataclass
class Bucket:
    label: str
    count: int
    value_paise: int


@dataclass
class CashPosition:
    confirmed_in_paise: int = 0
    still_owed_paise: int = 0
    withheld_paise: int = 0
    uncertain_paise: int = 0
    mdr_paise: int = 0
    gst_paise: int = 0
    tds_paise: int = 0
    aging: list[Bucket] = field(default_factory=list)
    as_of: str = ""

    def to_dict(self) -> dict:
        return {
            "confirmed_in_paise": self.confirmed_in_paise,
            "still_owed_paise": self.still_owed_paise,
            "withheld_paise": self.withheld_paise,
            "uncertain_paise": self.uncertain_paise,
            "mdr_paise": self.mdr_paise,
            "gst_paise": self.gst_paise,
            "tds_paise": self.tds_paise,
            "aging": [
                {"label": b.label, "count": b.count, "value_paise": b.value_paise}
                for b in self.aging
            ],
            "as_of": self.as_of,
        }


def cash_position(
    split: Split = Split.HELDOUT, database_url: str | None = None
) -> CashPosition:
    url = database_url or get_settings().database_url
    position = CashPosition()

    # Every total below is cast with int(). Postgres returns SUM() over BIGINT
    # as numeric, which arrives as a Decimal, and money is integer paise
    # everywhere in this codebase. Casting at the boundary keeps a different
    # numeric type out of code that assumes int.

    with psycopg.connect(url, row_factory=dict_row) as conn:
        # "Today" is the latest date in the feed, not the wall clock, so the
        # aging buckets do not drift as time passes.
        row = conn.execute(
            "SELECT max(value_date) AS today FROM transactions WHERE split = %s",
            (split.value,),
        ).fetchone()
        today = row["today"]
        position.as_of = str(today) if today else ""

        # Confirmed in: money we auto-approved and can stand behind.
        position.confirmed_in_paise = int(conn.execute(
            """SELECT COALESCE(SUM(ma.allocated_paise), 0) AS total
               FROM match_allocations ma
               JOIN matches m  ON m.id = ma.match_id
               JOIN invoices i ON i.id = ma.invoice_id
               WHERE m.decision = 'AUTO' AND i.split = %s""",
            (split.value,),
        ).fetchone()["total"])

        # Still owed: no payment turned up that covers the invoice. Either
        # nobody paid, or they paid short and the shortfall is unexplained.
        # An open invoice with no exception row at all lands here too, so
        # nothing can go missing between the two buckets.
        position.still_owed_paise = int(conn.execute(
            """SELECT COALESCE(SUM(i.amount_paise), 0) AS total
               FROM invoices i
               WHERE i.split = %s
                 AND NOT EXISTS (
                   SELECT 1 FROM match_allocations ma
                   JOIN matches m ON m.id = ma.match_id
                   WHERE ma.invoice_id = i.id AND m.decision = 'AUTO'
                 )
                 AND NOT EXISTS (
                   SELECT 1 FROM exceptions e
                   WHERE e.invoice_id = i.id
                     AND e.reason_code <> ALL(%s)
                 )""",
            (split.value, list(UNPAID_REASONS)),
        ).fetchone()["total"])

        # Uncertain: a payment is sitting there, but a rule stopped us signing
        # it off - too large, duplicated, ambiguous, or outside the date window.
        position.uncertain_paise = int(conn.execute(
            """SELECT COALESCE(SUM(i.amount_paise), 0) AS total
               FROM invoices i
               WHERE i.split = %s
                 AND EXISTS (
                   SELECT 1 FROM exceptions e
                   WHERE e.invoice_id = i.id
                     AND e.reason_code <> ALL(%s)
                 )""",
            (split.value, list(UNPAID_REASONS)),
        ).fetchone()["total"])

        # Withheld: taken out before the money reached the bank. TDS is
        # reclaimable, MDR and its GST are a cost - a controller needs both.
        row = conn.execute(
            """SELECT COALESCE(SUM(s.fee_paise), 0) AS mdr,
                      COALESCE(SUM(s.gst_on_fee_paise), 0) AS gst,
                      COALESCE(SUM(s.tds_paise), 0) AS tds
               FROM settlements s
               JOIN transactions t ON t.id = s.txn_id
               WHERE t.split = %s""",
            (split.value,),
        ).fetchone()
        position.mdr_paise = int(row["mdr"])
        position.gst_paise = int(row["gst"])
        position.tds_paise = int(row["tds"])
        position.withheld_paise = (
            position.mdr_paise + position.gst_paise + position.tds_paise
        )

        # Aging, over every invoice we did not settle - both buckets above,
        # because a reviewer queue that is 90 days old is its own problem.
        for label, low, high in AGING_BUCKETS:
            row = conn.execute(
                """SELECT count(*) AS n, COALESCE(SUM(i.amount_paise), 0) AS total
                   FROM invoices i
                   WHERE i.split = %s
                     AND (%s - i.due_date) BETWEEN %s AND %s
                     AND NOT EXISTS (
                       SELECT 1 FROM match_allocations ma
                       JOIN matches m ON m.id = ma.match_id
                       WHERE ma.invoice_id = i.id AND m.decision = 'AUTO'
                     )""",
                (split.value, today, low, high),
            ).fetchone()
            position.aging.append(Bucket(label, int(row["n"]), int(row["total"])))

    return position
