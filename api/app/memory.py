"""What the system already knows about a counterparty. Plan section 5, memory.

Plain Postgres tables, not a vector database. Our retrieval questions have
exact right answers - *is this a known alias?* is a string lookup, and a
B-tree answers it correctly every time.

**Seeded from the alias set, never from the graded run.** Confirmed matches
write alias rows, and if the graded batch learned its own aliases the reported
accuracy would be inflated by information the system never actually had. That
is bug 7 in section 18. The seed set exists to be frozen before grading, and
it deliberately covers the same customers the graded set will meet - which is
what "we have dealt with this company before" means in real life.
"""

from dataclasses import dataclass, field

import psycopg

from app.config import get_settings
from app.dataset import Split
from app.names import name_from_bank_text, name_similarity

# Two names are the same company at or above this.
SAME_ENTITY = 0.90


@dataclass
class Memory:
    """Counterparties we have settled with, and the names they arrive under."""

    variants: dict[str, set[str]] = field(default_factory=dict)   # canonical -> bank forms
    confirmations: dict[str, int] = field(default_factory=dict)   # canonical -> times settled

    def seen(self, name_clean: str) -> int:
        """How many times we have confirmed a payment from this counterparty."""
        if name_clean in self.confirmations:
            return self.confirmations[name_clean]
        for canonical, count in self.confirmations.items():
            if name_similarity(canonical, name_clean) >= SAME_ENTITY:
                return count
        return 0

    def same_entity(self, invoice_name: str, bank_name: str) -> bool:
        """Have we seen this exact bank form for this customer before?"""
        for canonical, forms in self.variants.items():
            if name_similarity(canonical, invoice_name) < SAME_ENTITY:
                continue
            if bank_name in forms:
                return True
        return False

    def __len__(self) -> int:
        return len(self.confirmations)


def build_from_split(split: Split = Split.ALIAS_SEED, database_url: str | None = None) -> Memory:
    """Read past confirmed matches out of the seed set.

    These stand for settlements a human already signed off, so taking them
    from the answer key is not cheating - it is what "prior confirmed match"
    means. What would be cheating is reading the graded set's answers, and
    this function will not load that split.
    """
    if split is Split.HELDOUT:
        raise ValueError("memory must never be seeded from the graded set")

    url = database_url or get_settings().database_url
    memory = Memory()

    with psycopg.connect(url) as conn:
        rows = conn.execute(
            """
            SELECT i.counterparty_name_clean, t.description_raw
            FROM ground_truth g
            JOIN invoices i     ON i.id = g.invoice_id
            JOIN transactions t ON t.id = ANY(g.expected_txn_ids)
            WHERE g.split = %s AND g.expected_outcome = 'AUTO'
            """,
            (split.value,),
        ).fetchall()

    for canonical, description in rows:
        memory.confirmations[canonical] = memory.confirmations.get(canonical, 0) + 1
        bank_form = name_from_bank_text(description)
        if bank_form:
            memory.variants.setdefault(canonical, set()).add(bank_form)

    return memory


def persist(memory: Memory, database_url: str | None = None) -> int:
    """Write the aliases table, so the UI and later phases can read it."""
    url = database_url or get_settings().database_url
    written = 0

    with psycopg.connect(url) as conn:
        conn.execute("TRUNCATE aliases RESTART IDENTITY")
        for canonical, forms in memory.variants.items():
            for variant in forms:
                conn.execute(
                    """INSERT INTO aliases (canonical_name, variant_name, confirmed_count)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (canonical_name, variant_name)
                       DO UPDATE SET confirmed_count = aliases.confirmed_count + 1""",
                    (canonical, variant, memory.confirmations.get(canonical, 1)),
                )
                written += 1
        conn.commit()

    return written
