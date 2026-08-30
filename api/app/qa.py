"""Settlement Q&A. Plan section 19.4.

    "Why did we receive Rs 9,764 for INV-023?"

    Gross Rs 10,000 - MDR Rs 200 - GST on MDR Rs 36 = Rs 9,764. Matched to
    TXN-1180, settled T+2 on 14 Aug, auto-approved.

The brief names this direction, and we already hold every number it needs.

**The model narrates. It never calculates.** Every figure is pulled from the
database first, handed to the model as facts, and the answer is then checked
back against those facts: any rupee amount that is not one we supplied means
the model did arithmetic, and the answer is thrown away rather than shown.

That check is the whole point. An explanation of money that quietly invents a
number is worse than no explanation, because it is the kind of wrong a reader
cannot catch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import openai
import psycopg
from psycopg.rows import dict_row

from app.config import get_settings
from app.dataset import Split
from app.money import fmt

# An invoice id anywhere in the question, however the user cased it.
INVOICE_RE = re.compile(r"\b(inv[-_ ]?\d{1,5})\b", re.IGNORECASE)

# Money-shaped numbers only: written after "Rs", or grouped with commas, or
# carrying paise. Every amount in the facts is produced by fmt(), so it always
# takes one of those forms, and so does any restatement of it.
#
# A plain run of digits is deliberately not money here. Dates, UTRs and
# account numbers are all bare digits, and treating them as amounts rejected
# every honest answer that mentioned a settlement date. The trade is that a
# bare "4500" written as money would slip through; the facts never write money
# that way, so it is a narrow gap and much better than the alternative.
AMOUNT_RE = re.compile(
    r"""(?:Rs\.?\s*)(\d[\d,]*(?:\.\d+)?)   # Rs 1,46,657.00
      | (\d{1,3}(?:,\d{2,3})+(?:\.\d+)?)    # 1,46,657 grouped
      | (\d+\.\d{2})\b                      # 146657.00
    """,
    re.VERBOSE,
)

# Below this a figure is a count, a day offset or a percentage, not money.
AMOUNT_FLOOR = 100

SYSTEM = """You explain payment reconciliation to a finance person in India.

You will be given a question and a block of FACTS taken from the ledger.

Rules, in order of importance:
1. Every number in your answer must appear in the FACTS, copied exactly as
   written there. Never add, subtract, or recompute anything. If the working
   is not already in the FACTS, do not produce it.
2. If the FACTS do not answer the question, say so plainly and say what is
   missing. Do not guess.
3. Two to four sentences. Plain English. No jargon, no bullet points, no
   preamble like "Based on the facts".
4. Write to the reader as "you": it is their money."""


@dataclass
class Facts:
    """Everything the ledger knows about one invoice, already formatted."""

    invoice_id: str
    lines: list[str] = field(default_factory=list)
    amounts: set[int] = field(default_factory=set)

    def add(self, label: str, text: str, paise: int | None = None) -> None:
        self.lines.append(f"{label}: {text}")
        if paise is not None:
            self.amounts.add(paise)

    def as_block(self) -> str:
        return "\n".join(self.lines)


@dataclass
class Answer:
    text: str
    invoice_id: str | None = None
    facts: list[str] = field(default_factory=list)
    used_model: bool = False
    rejected: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


def find_invoice_id(question: str) -> str | None:
    """Pull an invoice id out of the question, tolerating inv23, INV_0023."""
    match = INVOICE_RE.search(question)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return f"INV-{int(digits):04d}"


def gather(invoice_id: str, database_url: str | None = None) -> Facts | None:
    """Every number this answer is allowed to contain.

    One pass over the ledger. If a figure is not collected here, the model has
    no way to produce it, which is exactly the intent.
    """
    url = database_url or get_settings().database_url
    facts = Facts(invoice_id=invoice_id)

    with psycopg.connect(url, row_factory=dict_row) as conn:
        invoice = conn.execute(
            """SELECT id, invoice_no, counterparty_name, counterparty_name_clean,
                      amount_paise, invoice_date, due_date, currency, scenario
               FROM invoices WHERE id = %s""",
            (invoice_id,),
        ).fetchone()
        if invoice is None:
            return None

        facts.add("Invoice", invoice["id"])
        facts.add("Customer", invoice["counterparty_name_clean"])
        facts.add("Name on the invoice", invoice["counterparty_name"])
        facts.add("Invoice amount", fmt(int(invoice["amount_paise"])),
                  int(invoice["amount_paise"]))
        facts.add("Raised on", str(invoice["invoice_date"]))
        facts.add("Due on", str(invoice["due_date"]))

        allocations = conn.execute(
            """SELECT ma.allocated_paise, m.decision, m.decided_by, m.score,
                      t.id AS txn_id, t.amount_paise AS txn_paise,
                      t.value_date, t.description_raw, t.source, t.utr
               FROM match_allocations ma
               JOIN matches m ON m.id = ma.match_id
               JOIN transactions t ON t.id = m.transaction_id
               WHERE ma.invoice_id = %s
               ORDER BY t.value_date""",
            (invoice_id,),
        ).fetchall()

        if not allocations:
            facts.add("Settled", "no - nothing has been matched to this invoice yet")
            exception = conn.execute(
                "SELECT reason_code, reason_text FROM exceptions WHERE invoice_id = %s",
                (invoice_id,),
            ).fetchone()
            if exception:
                facts.add("Why it is open", exception["reason_text"])
            return facts

        facts.add("Settled", f"yes, by {len(allocations)} payment(s)")

        for row in allocations:
            txn = row["txn_id"]
            facts.add(f"Payment {txn} amount", fmt(int(row["txn_paise"])),
                      int(row["txn_paise"]))
            facts.add(f"Payment {txn} put against this invoice",
                      fmt(int(row["allocated_paise"])), int(row["allocated_paise"]))
            facts.add(f"Payment {txn} reached the bank on", str(row["value_date"]))
            facts.add(f"Payment {txn} bank text", row["description_raw"])
            facts.add(f"Payment {txn} came through", row["source"])
            facts.add(f"Payment {txn} decided by", row["decided_by"])

            settlement = conn.execute(
                """SELECT gross_paise, fee_paise, gst_on_fee_paise, tds_paise,
                          net_paise, formula_used, settled_on, batch_utr
                   FROM settlements WHERE txn_id = %s""",
                (txn,),
            ).fetchone()
            if settlement is None:
                continue

            # The working, written out. The model copies this; it never
            # reproduces the subtraction itself.
            gross = int(settlement["gross_paise"])
            fee = int(settlement["fee_paise"])
            gst = int(settlement["gst_on_fee_paise"])
            tds = int(settlement["tds_paise"])
            net = int(settlement["net_paise"])

            facts.add(f"Payment {txn} gross before deductions", fmt(gross), gross)
            if fee:
                facts.add(f"Payment {txn} gateway fee (MDR) taken off", fmt(fee), fee)
            if gst:
                facts.add(f"Payment {txn} GST on the gateway fee taken off", fmt(gst), gst)
            if tds:
                facts.add(f"Payment {txn} TDS withheld by the customer", fmt(tds), tds)
            facts.add(f"Payment {txn} net that actually landed", fmt(net), net)

            parts = [fmt(gross)]
            if fee:
                parts.append(f"minus gateway fee {fmt(fee)}")
            if gst:
                parts.append(f"minus GST on that fee {fmt(gst)}")
            if tds:
                parts.append(f"minus TDS {fmt(tds)}")
            facts.add(
                f"Payment {txn} full working",
                f"{' '.join(parts)} = {fmt(net)}",
            )

            if settlement["settled_on"]:
                facts.add(f"Payment {txn} settled on", str(settlement["settled_on"]))
            if settlement["batch_utr"]:
                facts.add(
                    f"Payment {txn} arrived in a batch with UTR",
                    settlement["batch_utr"],
                )

    return facts


def invented_amounts(answer: str, allowed: set[int]) -> list[str]:
    """Rupee figures in the answer that we never supplied.

    Facts hold paise; answers are written in rupees. A figure counts as
    supplied if it matches either form, so an answer saying "Rs 9,764.00" and
    a fact holding 976400 agree.
    """
    permitted: set[str] = set()
    for paise in allowed:
        permitted.add(f"{paise}")
        permitted.add(f"{paise / 100:.2f}")
        permitted.add(f"{paise // 100}")

    invented = []
    for groups in AMOUNT_RE.findall(answer):
        raw = next((g for g in groups if g), "")
        if not raw:
            continue
        plain = raw.replace(",", "")
        try:
            value = float(plain)
        except ValueError:
            continue
        if value < AMOUNT_FLOOR:
            continue
        # A whole-rupee figure may be written with or without decimals.
        forms = {plain, plain.rstrip("0").rstrip("."), f"{value:.2f}", f"{int(value)}"}
        if not forms & permitted:
            invented.append(raw)
    return invented


def plain_answer(facts: Facts) -> str:
    """What we say when there is no model, or its answer was thrown away.

    Less fluent than the model, and exactly as true.
    """
    return "\n".join(facts.lines)


def ask(question: str, database_url: str | None = None) -> Answer:
    """Answer a question about one invoice, from the ledger only."""
    invoice_id = find_invoice_id(question)
    if invoice_id is None:
        return Answer(
            text=(
                "Tell me which invoice you mean and I will explain it - "
                "for example, \"why did we receive this much for INV-0053?\"."
            ),
            rejected="NO_INVOICE_IN_QUESTION",
        )

    facts = gather(invoice_id, database_url)
    if facts is None:
        return Answer(
            text=f"There is no invoice {invoice_id} in the ledger.",
            invoice_id=invoice_id,
            rejected="UNKNOWN_INVOICE",
        )

    settings = get_settings()
    if not settings.has_real_llm_key:
        return Answer(
            text=plain_answer(facts),
            invoice_id=invoice_id,
            facts=facts.lines,
            rejected="NO_API_KEY",
        )

    client = openai.OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url or None,
        default_headers={
            "HTTP-Referer": "https://github.com/divyansharma001/Hisaab",
            "X-Title": "Hisaab",
        },
    )

    prompt = f"QUESTION\n{question}\n\nFACTS\n{facts.as_block()}"

    try:
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_completion_tokens=400,
        )
    except openai.APIError as exc:
        return Answer(
            text=plain_answer(facts),
            invoice_id=invoice_id,
            facts=facts.lines,
            rejected=f"API_ERROR: {type(exc).__name__}",
        )

    text = (response.choices[0].message.content or "").strip()
    usage = response.usage

    made_up = invented_amounts(text, facts.amounts)
    if made_up:
        # It did arithmetic. The numbers may even be right, but we cannot tell
        # from here, and a wrong one is undetectable by the reader.
        return Answer(
            text=plain_answer(facts),
            invoice_id=invoice_id,
            facts=facts.lines,
            rejected=f"INVENTED_AMOUNTS: {', '.join(made_up)}",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )

    return Answer(
        text=text,
        invoice_id=invoice_id,
        facts=facts.lines,
        used_model=True,
        input_tokens=usage.prompt_tokens if usage else 0,
        output_tokens=usage.completion_tokens if usage else 0,
    )
