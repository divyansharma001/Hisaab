"""Let someone try the matcher on their own figures.

Type in a few invoices and a few bank lines, press match, and see what the
system decides and why. It is the difference between reading that we handle
TDS and watching it explain a gap of Rs 2,993 on a number you chose.

**It is scored on nothing.** Sandbox rows have no answer key, so this returns
decisions and reasons and never an accuracy figure. They live in their own
split, are never tuned on, and cannot teach memory - the same rule the graded
set follows, for the same reason.

Memory still applies *to* a sandbox run: without it, every customer typed in
would look new and every record would be held, which would tell the visitor
nothing about the matcher.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings
from app.dataset import Outcome, Split
from app.intake import load_batch
from app.memory import build_from_split
from app.money import fmt, parse_amount
from app.pipeline import process_batch

# Enough for a real statement's worth of rows, small enough that nobody can
# use this as free compute or fill the table.
MAX_ROWS = 200

# A pasted file bigger than this is not somebody trying the demo.
MAX_UPLOAD_CHARS = 200_000

NAME_OK = re.compile(r"^[\w .,&()/-]{2,80}$", re.UNICODE)


class BadEntry(ValueError):
    """Something the person typed, said back to them in words."""


@dataclass
class Added:
    id: str
    kind: str
    summary: str


def _next_id(conn, table: str, prefix: str) -> str:
    row = conn.execute(
        f"SELECT id FROM {table} WHERE split = %s ORDER BY id DESC LIMIT 1",
        (Split.SANDBOX.value,),
    ).fetchone()
    n = int(row["id"].rsplit("-", 1)[1]) + 1 if row else 1
    return f"{prefix}-{n:04d}"


def _a_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        raise BadEntry(f"{field} needs to look like 2026-08-31") from None


def add_invoice(
    customer: str,
    amount: str,
    invoice_date: str | None = None,
    due_date: str | None = None,
    database_url: str | None = None,
) -> Added:
    """One invoice. Dates default to something sensible so the quickest way
    through the form is three fields, not five."""
    customer = (customer or "").strip()
    if not NAME_OK.match(customer):
        raise BadEntry("give the customer a name, 2 to 80 ordinary characters")

    amount_paise = parse_amount(amount)

    # Default to an invoice raised a month ago and due today, so a payment
    # dated today lands on the due date. Defaulting the other way - raised
    # today, due in a month - put every quick attempt 30 days early and
    # failed it for being outside the date window, which is the matcher
    # working correctly on dates nobody meant to enter.
    today = date.today()
    raised = _a_date(invoice_date, "the invoice date") if invoice_date else today - timedelta(days=30)
    due = _a_date(due_date, "the due date") if due_date else max(raised + timedelta(days=30), today)
    if due < raised:
        raise BadEntry("the due date cannot be before the invoice date")

    url = database_url or get_settings().database_url
    with psycopg.connect(url, row_factory=dict_row) as conn:
        _check_room(conn)
        new_id = _next_id(conn, "invoices", "INV-SB")
        conn.execute(
            """INSERT INTO invoices (id, split, invoice_no, counterparty_name,
                                     counterparty_name_clean, amount_paise, currency,
                                     invoice_date, due_date, status, scenario)
               VALUES (%s, %s, %s, %s, %s, %s, 'INR', %s, %s, 'open', 'sandbox')""",
            (new_id, Split.SANDBOX.value, new_id, customer, customer.upper(),
             amount_paise, raised, due),
        )
        conn.commit()

    return Added(new_id, "invoice", f"{customer} · {fmt(amount_paise)} · due {due}")


def add_payment(
    bank_text: str,
    amount: str,
    value_date: str | None = None,
    database_url: str | None = None,
) -> Added:
    """One bank line, as it would appear on a statement."""
    bank_text = (bank_text or "").strip()
    if len(bank_text) < 3 or len(bank_text) > 140:
        raise BadEntry("paste the bank narration, 3 to 140 characters")

    amount_paise = parse_amount(amount)
    landed = _a_date(value_date, "the payment date") if value_date else date.today()

    url = database_url or get_settings().database_url
    with psycopg.connect(url, row_factory=dict_row) as conn:
        _check_room(conn)
        new_id = _next_id(conn, "transactions", "TXN-SB")
        conn.execute(
            """INSERT INTO transactions (id, split, txn_ref, description_raw,
                                         amount_paise, currency, value_date,
                                         source, scenario)
               VALUES (%s, %s, %s, %s, %s, 'INR', %s, 'bank', 'sandbox')""",
            (new_id, Split.SANDBOX.value, new_id, bank_text, amount_paise, landed),
        )
        conn.commit()

    return Added(new_id, "payment", f"{bank_text[:48]} · {fmt(amount_paise)} · {landed}")


def _check_room(conn) -> None:
    used = (
        conn.execute("SELECT count(*) AS n FROM invoices WHERE split = %s",
                     (Split.SANDBOX.value,)).fetchone()["n"]
        + conn.execute("SELECT count(*) AS n FROM transactions WHERE split = %s",
                       (Split.SANDBOX.value,)).fetchone()["n"]
    )
    if used >= MAX_ROWS:
        raise BadEntry(
            f"that is {MAX_ROWS} rows, which is plenty to try it out. "
            f"Clear them and start again."
        )


def contents(database_url: str | None = None) -> dict:
    """What is currently in the scratch set."""
    url = database_url or get_settings().database_url
    with psycopg.connect(url, row_factory=dict_row) as conn:
        invoices = conn.execute(
            """SELECT id, counterparty_name, amount_paise, invoice_date, due_date
               FROM invoices WHERE split = %s ORDER BY id""",
            (Split.SANDBOX.value,),
        ).fetchall()
        payments = conn.execute(
            """SELECT id, description_raw, amount_paise, value_date
               FROM transactions WHERE split = %s ORDER BY id""",
            (Split.SANDBOX.value,),
        ).fetchall()

    return {
        "invoices": [
            {
                "id": r["id"],
                "customer": r["counterparty_name"],
                "amount": {"paise": int(r["amount_paise"]), "display": fmt(int(r["amount_paise"]))},
                "invoice_date": str(r["invoice_date"]),
                "due_date": str(r["due_date"]),
            }
            for r in invoices
        ],
        "payments": [
            {
                "id": r["id"],
                "bank_text": r["description_raw"],
                "amount": {"paise": int(r["amount_paise"]), "display": fmt(int(r["amount_paise"]))},
                "value_date": str(r["value_date"]),
            }
            for r in payments
        ],
        "room_left": MAX_ROWS - len(invoices) - len(payments),
    }


def clear(database_url: str | None = None) -> dict:
    """Throw the scratch set away. Touches nothing else - the delete is
    filtered on the sandbox split, and the graded rows are a different
    split in the same tables."""
    url = database_url or get_settings().database_url
    with psycopg.connect(url) as conn:
        invoices = conn.execute(
            "DELETE FROM invoices WHERE split = %s", (Split.SANDBOX.value,)
        ).rowcount
        payments = conn.execute(
            "DELETE FROM transactions WHERE split = %s", (Split.SANDBOX.value,)
        ).rowcount
        conn.commit()
    return {"invoices_removed": invoices, "payments_removed": payments}


def match(database_url: str | None = None) -> dict:
    """Run the same pipeline the real batch runs, on the scratch set.

    No adjudicator: it costs money per call and a visitor can press this as
    often as they like. The deterministic core is what the demo is about
    anyway, and the screen says so rather than quietly implying the model was
    involved.
    """
    batch = load_batch(Split.SANDBOX, database_url)
    if not batch.invoices:
        return {"ran": False, "why": "add an invoice first"}
    if not batch.transactions:
        return {"ran": False, "why": "add a payment for it to match against"}

    # Treat the customers they typed as ones they have dealt with before.
    # Without this every sandbox row is held purely for being a new
    # counterparty, which is the rule working correctly and tells the visitor
    # nothing about matching. Only the "have we met them" count is set - no
    # name alias, because handing the matcher the answer to the name signal
    # would make the score meaningless.
    memory = build_from_split()
    for invoice in batch.invoices:
        memory.confirmations[invoice.name_clean] = max(
            memory.confirmations.get(invoice.name_clean, 0), 3
        )

    result = process_batch(batch, memory=memory.snapshot())
    invoices = batch.invoice_by_id()
    txns = batch.txn_by_id()

    rows = []
    for decision in result.decisions:
        invoice = invoices.get(decision.invoice_id)
        ranking = result.rankings.get(decision.invoice_id)
        best = ranking.best if ranking else None
        rows.append(
            {
                "invoice_id": decision.invoice_id,
                "customer": invoice.name_clean if invoice else "",
                "amount": {
                    "paise": decision.amount_paise,
                    "display": fmt(decision.amount_paise),
                },
                "outcome": decision.outcome.value,
                "reason_code": decision.reason_code.value if decision.reason_code else None,
                "reason_text": decision.reason_text,
                "matched": [
                    {
                        "id": t,
                        "bank_text": txns[t].description_raw if t in txns else "",
                    }
                    for t in decision.txn_ids
                ],
                "amount_working": best.amount.basis if best else "",
                "score": round(decision.score, 3),
            }
        )

    settled = sum(1 for d in result.decisions if d.outcome is Outcome.AUTO)
    return {
        "ran": True,
        "records": len(rows),
        "settled": settled,
        "held": len(rows) - settled,
        "seconds": round(result.seconds, 2),
        "results": rows,
        "note": (
            "Run without the assistant, so this costs nothing and gives the "
            "same answer every time. There is no right answer to score it "
            "against - these are your figures, not our test set."
        ),
        "assumption": (
            "We assume you have dealt with these customers before. Otherwise "
            "every row would be held simply for being a new name, which is the "
            "rule doing its job but tells you nothing about the matching."
        ),
    }


# --- bulk upload ------------------------------------------------------------
#
# Real CSVs do not agree on anything. An accounting export calls it
# "Party Name", a bank calls the narration "Particulars", and half of them
# split money across Debit and Credit columns. Rather than demand one shape,
# we accept the names people actually have and say clearly which column we
# read as what.

INVOICE_COLUMNS = {
    "customer": ("customer", "customername", "name", "party", "partyname",
                 "counterparty", "client", "clientname", "billedto", "vendor"),
    "amount": ("amount", "invoiceamount", "total", "totalamount", "value",
               "amountinr", "grandtotal", "netamount"),
    "invoice_date": ("invoicedate", "date", "billdate", "raised", "raisedon",
                     "issuedate", "documentdate"),
    "due_date": ("duedate", "due", "paymentdue", "dueon", "maturitydate"),
}

PAYMENT_COLUMNS = {
    "bank_text": ("narration", "description", "particulars", "details",
                  "remarks", "banktext", "transactiondetails", "transactionremarks",
                  "reference", "text"),
    "amount": ("amount", "credit", "creditamount", "deposit", "depositamt",
               "amountinr", "creditinr", "receivedamount"),
    "value_date": ("valuedate", "date", "txndate", "transactiondate",
                   "posteddate", "postingdate", "bookingdate"),
}

# Formats a spreadsheet or a bank is likely to hand us, most specific first.
DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y",
    "%d %b %Y", "%d-%b-%Y", "%d %B %Y", "%m/%d/%Y",
)


@dataclass
class Upload:
    added: int = 0
    skipped: int = 0
    ignored: int = 0
    problems: list[dict] = field(default_factory=list)
    columns_used: dict[str, str] = field(default_factory=dict)

    def note(self, line: int, problem: str) -> None:
        self.skipped += 1
        # Ten is enough for someone to see the pattern; a thousand identical
        # complaints is just noise.
        if len(self.problems) < 10:
            self.problems.append({"line": line, "problem": problem})


def _key(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (header or "").lower())


def _pick_columns(headers: list[str], wanted: dict) -> dict[str, str]:
    """Map our field names onto whatever this file calls them."""
    seen = {_key(h): h for h in headers if h}
    chosen: dict[str, str] = {}
    for ours, aliases in wanted.items():
        for alias in aliases:
            if alias in seen:
                chosen[ours] = seen[alias]
                break
    return chosen


def _flexible_date(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    for fmt_ in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt_).date()
        except ValueError:
            continue
    raise BadEntry(f"{text!r} is not a date we recognise")


def upload(text: str, kind: str, database_url: str | None = None) -> Upload:
    """Take a pasted or uploaded CSV of invoices or payments.

    A single bad row does not lose the file. Every row is tried, the good ones
    are kept, and the rest come back with their line number and what was wrong
    with them - which is the only version of this anyone can actually use to
    fix their data.
    """
    if kind not in ("invoices", "payments"):
        raise BadEntry("say whether these are invoices or payments")
    if not (text or "").strip():
        raise BadEntry("paste some rows, or choose a file")
    if len(text) > MAX_UPLOAD_CHARS:
        raise BadEntry("that file is too large for a scratch pad")

    # Sniff the delimiter: exports are comma, semicolon or tab depending on
    # locale, and guessing wrong turns every row into one unreadable column.
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = reader.fieldnames or []
    wanted = INVOICE_COLUMNS if kind == "invoices" else PAYMENT_COLUMNS
    columns = _pick_columns(headers, wanted)

    required = ("customer", "amount") if kind == "invoices" else ("bank_text", "amount")
    missing = [r for r in required if r not in columns]
    if missing:
        readable = {"bank_text": "narration", "customer": "customer", "amount": "amount"}
        raise BadEntry(
            "could not find a "
            + " or a ".join(readable[m] for m in missing)
            + f" column. The file has: {', '.join(h for h in headers if h) or 'no headers'}"
        )

    report = Upload(columns_used=columns)
    url = database_url or get_settings().database_url

    for offset, row in enumerate(reader):
        line = offset + 2  # header is line 1

        # A wholly blank line is a spreadsheet artefact. Test it first, or it
        # gets counted as money going out and the tally lies.
        if not any((v or "").strip() for v in row.values()):
            continue

        # A statement row with nothing in the credit column is money going
        # out. Counting those as broken rows would make an ordinary bank
        # export look like it was full of errors.
        if kind == "payments" and not (row.get(columns["amount"]) or "").strip():
            report.ignored += 1
            continue

        try:
            if kind == "invoices":
                add_invoice(
                    customer=row.get(columns["customer"], ""),
                    amount=row.get(columns["amount"], ""),
                    invoice_date=_iso(row, columns.get("invoice_date")),
                    due_date=_iso(row, columns.get("due_date")),
                    database_url=url,
                )
            else:
                add_payment(
                    bank_text=row.get(columns["bank_text"], ""),
                    amount=row.get(columns["amount"], ""),
                    value_date=_iso(row, columns.get("value_date")),
                    database_url=url,
                )
            report.added += 1
        except ValueError as exc:
            report.note(line, str(exc))
            # Hitting the row cap is not a problem with their data, and
            # reporting it once per remaining row would bury everything else.
            if "rows, which is plenty" in str(exc):
                break

    return report


def _iso(row: dict, column: str | None) -> str | None:
    """One cell as an ISO date string, or None to let the default apply."""
    if not column:
        return None
    parsed = _flexible_date(row.get(column, ""))
    return parsed.isoformat() if parsed else None
