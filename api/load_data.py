"""Apply the schema and load the generated dataset into Postgres.

    docker compose exec api python load_data.py

Destructive by design: the schema drops and recreates every table, because we
reseed the same fixed dataset constantly while tuning.
"""

import argparse
import json
import sys
from pathlib import Path

import psycopg

from app.config import get_settings
from app.dataset import Split

SCHEMA = Path(__file__).parent / "app" / "schema.sql"
DATA_DIR = Path(__file__).parent / "data" / "generated"

ORDER = [Split.HELDOUT, Split.TUNING, Split.ALIAS_SEED]


def apply_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA.read_text())
    print("  schema applied")


def load_split(conn: psycopg.Connection, split: Split, data_dir: Path) -> dict[str, int]:
    records = json.loads((data_dir / f"{split.value}_records.json").read_text())
    truth = json.loads((data_dir / f"{split.value}_ground_truth.json").read_text())

    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO invoices (id, split, invoice_no, counterparty_name,
                 counterparty_name_clean, amount_paise, currency, invoice_date,
                 due_date, status, scenario)
               VALUES (%(id)s, %(split)s, %(invoice_no)s, %(counterparty_name)s,
                 %(counterparty_name_clean)s, %(amount_paise)s, %(currency)s,
                 %(invoice_date)s, %(due_date)s, %(status)s, %(scenario)s)""",
            records["invoices"],
        )
        cur.executemany(
            """INSERT INTO transactions (id, split, txn_ref, description_raw,
                 counterparty_name_clean, amount_paise, currency, value_date,
                 source, utr, scenario)
               VALUES (%(id)s, %(split)s, %(txn_ref)s, %(description_raw)s,
                 %(counterparty_name_clean)s, %(amount_paise)s, %(currency)s,
                 %(value_date)s, %(source)s, %(utr)s, %(scenario)s)""",
            records["transactions"],
        )
        cur.executemany(
            """INSERT INTO settlements (id, txn_id, gross_paise, fee_paise,
                 gst_on_fee_paise, tds_paise, net_paise, formula_used,
                 settled_on, batch_utr)
               VALUES (%(id)s, %(txn_id)s, %(gross_paise)s, %(fee_paise)s,
                 %(gst_on_fee_paise)s, %(tds_paise)s, %(net_paise)s,
                 %(formula_used)s, %(settled_on)s, %(batch_utr)s)""",
            records["settlements"],
        )
        cur.executemany(
            """INSERT INTO ground_truth (invoice_id, split, scenario,
                 expected_outcome, expected_txn_ids, expected_reason_code, note)
               VALUES (%(invoice_id)s, %(split)s, %(scenario)s,
                 %(expected_outcome)s, %(expected_txn_ids)s,
                 %(expected_reason_code)s, %(note)s)""",
            [
                {"invoice_id": invoice_id, "split": split.value, **row}
                for invoice_id, row in truth.items()
            ],
        )

    return {
        "invoices": len(records["invoices"]),
        "transactions": len(records["transactions"]),
        "settlements": len(records["settlements"]),
        "truth": len(truth),
    }


def verify(conn: psycopg.Connection) -> None:
    """Read the counts back out, rather than trusting the inserts."""
    print("\nIn the database")
    print(f"  {'split':<12} {'invoices':>9} {'txns':>6} {'settlements':>12} {'truth':>7}")

    for split in ORDER:
        row = conn.execute(
            """SELECT
                 (SELECT count(*) FROM invoices     WHERE split = %(s)s),
                 (SELECT count(*) FROM transactions WHERE split = %(s)s),
                 (SELECT count(*) FROM settlements st
                    JOIN transactions t ON t.id = st.txn_id WHERE t.split = %(s)s),
                 (SELECT count(*) FROM ground_truth WHERE split = %(s)s)""",
            {"s": split.value},
        ).fetchone()
        print(f"  {split.value:<12} {row[0]:>9} {row[1]:>6} {row[2]:>12} {row[3]:>7}")

    orphans = conn.execute(
        """SELECT count(*) FROM invoices i
           LEFT JOIN ground_truth g ON g.invoice_id = i.id
           WHERE g.invoice_id IS NULL"""
    ).fetchone()[0]
    if orphans:
        raise SystemExit(f"{orphans} invoices have no answer key row")

    print("\n  Every invoice has an answer key row.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args()

    if not (args.data / "heldout_records.json").exists():
        print(f"No data in {args.data}. Run generate_data.py first.")
        return 1

    with psycopg.connect(get_settings().database_url) as conn:
        print("Loading into Postgres")
        apply_schema(conn)
        for split in ORDER:
            counts = load_split(conn, split, args.data)
            print(f"  {split.value:<12} loaded {counts}")
        conn.commit()
        verify(conn)

    return 0


if __name__ == "__main__":
    sys.exit(main())
