"""The cash position. Plan section 12.

The track is *"run the books and the cash position"*. The second half does not
need a forecasting agent - once reconciliation has run, the position falls out
of data we already hold. Four numbers, four queries, no prediction and no new
risk:

- **Confirmed in** - what we auto-approved and can stand behind
- **Still owed** - open invoices, split by how old they are
- **In flight** - the gateway has sent it, the bank has not shown it yet
- **Uncertain** - the value sitting in the exception queue

That last one is the interesting one. It is money the business cannot
currently account for, and no spreadsheet shows it.
"""

from dataclasses import dataclass, field

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings
from app.dataset import Split

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
    in_flight_paise: int = 0
    uncertain_paise: int = 0
    aging: list[Bucket] = field(default_factory=list)
    as_of: str = ""

    def to_dict(self) -> dict:
        return {
            "confirmed_in_paise": self.confirmed_in_paise,
            "still_owed_paise": self.still_owed_paise,
            "in_flight_paise": self.in_flight_paise,
            "uncertain_paise": self.uncertain_paise,
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

        # Uncertain: the value a human still has to look at.
        position.uncertain_paise = int(conn.execute(
            """SELECT COALESCE(SUM(i.amount_paise), 0) AS total
               FROM exceptions e
               JOIN invoices i ON i.id = e.invoice_id
               WHERE i.split = %s""",
            (split.value,),
        ).fetchone()["total"])

        # Still owed: invoices with nothing allocated against them.
        position.still_owed_paise = int(conn.execute(
            """SELECT COALESCE(SUM(i.amount_paise), 0) AS total
               FROM invoices i
               WHERE i.split = %s
                 AND NOT EXISTS (
                   SELECT 1 FROM match_allocations ma
                   JOIN matches m ON m.id = ma.match_id
                   WHERE ma.invoice_id = i.id AND m.decision = 'AUTO'
                 )""",
            (split.value,),
        ).fetchone()["total"])

        # In flight: the gateway has settled it, the bank line has not landed.
        position.in_flight_paise = int(conn.execute(
            """SELECT COALESCE(SUM(s.net_paise), 0) AS total
               FROM settlements s
               JOIN transactions t ON t.id = s.txn_id
               WHERE t.split = %s AND t.source = 'gateway'
                 AND s.settled_on IS NOT NULL AND s.settled_on > %s""",
            (split.value, today),
        ).fetchone()["total"])

        # Aging, on what is still owed.
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
