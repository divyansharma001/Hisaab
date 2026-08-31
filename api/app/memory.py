"""What the system already knows. Plan section 5, memory, and Phase 5.

Plain Postgres tables, not a vector database. Our retrieval questions have
exact right answers - *is this a known alias?* is a string lookup, and a
B-tree answers it correctly every time.

Two kinds of memory:

- **Aliases**: the bank name forms a customer's payments arrive under. This is
  what makes name matching work at all, and what the new-counterparty guardrail
  checks against.
- **Episodes**: past cases and how they were settled, retrieved by tag and
  shown to the adjudicator as worked examples.

**The freeze is the important part.** Confirmed matches teach the alias table,
so a batch that learned from itself would report accuracy inflated by
information it never actually had. Memory is snapshotted before a graded run
and cannot change during it - `learn_from` returns a *new* Memory rather than
mutating the one the run is using. That is bug 7 in section 18, made
impossible rather than merely avoided.
"""

import json
from dataclasses import dataclass, field, replace

import psycopg

from app.config import get_settings
from app.dataset import Split
from app.names import name_from_bank_text, name_similarity

# Two names are the same company at or above this.
SAME_ENTITY = 0.90

# How many worked examples the adjudicator gets. More is not better: they are
# there to show the shape of a decision, not to be searched through.
EPISODES_IN_PROMPT = 3


@dataclass(frozen=True)
class Episode:
    """One past case, and how it was settled."""

    situation: str
    resolution: str
    tags: tuple[str, ...]
    source_split: Split = Split.ALIAS_SEED


@dataclass
class Memory:
    """Counterparties we have settled with, the names they arrive under, and
    the tricky cases we have already worked through."""

    variants: dict[str, set[str]] = field(default_factory=dict)   # canonical -> bank forms
    confirmations: dict[str, int] = field(default_factory=dict)   # canonical -> times settled
    episodes: list[Episode] = field(default_factory=list)
    frozen: bool = False

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

    def episodes_for(self, tags: set[str], limit: int = EPISODES_IN_PROMPT) -> list[Episode]:
        """Past cases that share a shape with this one.

        Tag overlap, not embedding similarity. The plan's own rule of thumb:
        vector search for fuzzy meaning in prose, exact indexes for the rest.
        A tag like `MDR_GST` either applies or it does not.
        """
        if not tags:
            return []
        scored = [
            (len(tags & set(e.tags)), e) for e in self.episodes if tags & set(e.tags)
        ]
        scored.sort(key=lambda pair: -pair[0])
        return [episode for _, episode in scored[:limit]]

    def snapshot(self) -> "Memory":
        """A frozen copy, safe to hand to a graded run."""
        return Memory(
            variants={k: set(v) for k, v in self.variants.items()},
            confirmations=dict(self.confirmations),
            episodes=list(self.episodes),
            frozen=True,
        )

    def __len__(self) -> int:
        return len(self.confirmations)


def build_from_split(split: Split = Split.ALIAS_SEED, database_url: str | None = None) -> Memory:
    """Read past confirmed matches out of the seed set.

    These stand for settlements a human already signed off, so taking them
    from the answer key is not cheating - it is what "prior confirmed match"
    means. What would be cheating is reading the graded set's answers, and
    this function will not load that split.
    """
    if split in (Split.HELDOUT, Split.SANDBOX):
        raise ValueError(f"memory must never be seeded from {split.value}")

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

        # Never read back examples drawn from the records we are about to grade.
        memory.episodes = load_episodes(conn, exclude=Split.HELDOUT)

        # Aliases a reviewer confirmed by hand, under the same rule. A name
        # confirmed while working the graded batch is real knowledge, but
        # feeding it back into the run we score would flatter the number, so
        # the graded split is excluded here exactly as it is for episodes.
        confirmed = conn.execute(
            """SELECT canonical_name, variant_name, confirmed_count
               FROM aliases WHERE source_split <> ALL(%s)""",
            ([Split.HELDOUT.value, Split.SANDBOX.value],),
        ).fetchall()

    for canonical, description in rows:
        _record(memory, canonical, description)

    for canonical, variant, count in confirmed:
        memory.variants.setdefault(canonical, set()).add(variant)
        memory.confirmations[canonical] = memory.confirmations.get(canonical, 0) + count

    return memory.snapshot()


def _record(memory: Memory, canonical: str, description: str) -> None:
    memory.confirmations[canonical] = memory.confirmations.get(canonical, 0) + 1
    bank_form = name_from_bank_text(description)
    if bank_form:
        memory.variants.setdefault(canonical, set()).add(bank_form)


# --- learning ---------------------------------------------------------------


def case_tags(decision) -> set[str]:
    """The shape of a case, for retrieving past ones like it.

    Deliberately observable at decision time. Nothing here comes from the
    answer key, so the same tags can be computed for a live record.
    """
    tags = set()
    if decision.reason_code is not None:
        tags.add(decision.reason_code.value)
    if decision.margin < 0.15:
        tags.add("thin_margin")
    if decision.score < 0.90:
        tags.add("below_bar")
    for rule in decision.rules_failed:
        tags.add(f"failed_{rule.replace(' ', '_')}")
    return tags


def learn_from(memory: Memory, result, invoices: dict, transactions: dict) -> Memory:
    """Fold a finished run's confirmed matches into a **new** Memory.

    Returns a new object rather than mutating the one the run used, so a batch
    can never learn from itself mid-flight. To use what was learned, run again
    with the returned memory.
    """
    grown = Memory(
        variants={k: set(v) for k, v in memory.variants.items()},
        confirmations=dict(memory.confirmations),
        episodes=list(memory.episodes),
    )

    for decision in result.decisions:
        if decision.outcome.value != "AUTO":
            continue
        invoice = invoices.get(decision.invoice_id)
        if invoice is None:
            continue
        for txn_id in decision.txn_ids:
            txn = transactions.get(txn_id)
            if txn is not None:
                _record(grown, invoice.name_clean, txn.description_raw)

    return grown


def episode_from(decision, invoice, txn, source_split: Split) -> Episode:
    """Turn one settled case into a worked example.

    Written as a short situation and what was done about it, because that is
    what is useful to read back - not a row of numbers.
    """
    from app.money import fmt

    situation = (
        f"{invoice.name_clean} owed {fmt(invoice.amount_paise)} and "
        f"{fmt(txn.amount_paise)} arrived as {txn.description_raw!r}"
    )
    resolution = f"{decision.reason_code.value if decision.reason_code else 'MATCHED'}: {decision.reason_text}"
    return Episode(
        situation=situation,
        resolution=resolution,
        tags=tuple(sorted(case_tags(decision))),
        source_split=source_split,
    )


# --- persistence ------------------------------------------------------------


def load_episodes(conn: psycopg.Connection, exclude: Split | None = None) -> list[Episode]:
    """Worked examples, minus anything written from the split being graded.

    A case drawn from the graded records and shown back while grading them is
    the same contamination as an alias learned mid-run.
    """
    if exclude is None:
        rows = conn.execute(
            "SELECT situation_text, resolution_text, tags, source_split FROM episodes ORDER BY id"
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT situation_text, resolution_text, tags, source_split
               FROM episodes WHERE source_split <> %s ORDER BY id""",
            (exclude.value,),
        ).fetchall()
    return [Episode(s, r, tuple(t or ()), Split(sp)) for s, r, t, sp in rows]


def persist(memory: Memory, database_url: str | None = None) -> dict[str, int]:
    """Write memory back, so the UI and the next run can read it."""
    url = database_url or get_settings().database_url

    with psycopg.connect(url) as conn:
        conn.execute("TRUNCATE aliases RESTART IDENTITY")
        conn.execute("TRUNCATE episodes RESTART IDENTITY")

        for canonical, forms in memory.variants.items():
            for variant in forms:
                conn.execute(
                    """INSERT INTO aliases
                           (canonical_name, variant_name, confirmed_count, source_split)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (canonical_name, variant_name)
                       DO UPDATE SET confirmed_count = aliases.confirmed_count + 1""",
                    (canonical, variant, memory.confirmations.get(canonical, 1),
                     Split.ALIAS_SEED.value),
                )

        for episode in memory.episodes:
            conn.execute(
                """INSERT INTO episodes (situation_text, resolution_text, tags, source_split)
                   VALUES (%s, %s, %s, %s)""",
                (
                    episode.situation,
                    episode.resolution,
                    list(episode.tags),
                    episode.source_split.value,
                ),
            )

        conn.commit()
        counts = {
            "aliases": conn.execute("SELECT count(*) FROM aliases").fetchone()[0],
            "episodes": conn.execute("SELECT count(*) FROM episodes").fetchone()[0],
        }

    return counts


def confirm_match(
    canonical_name: str,
    bank_text: str,
    source_split: Split,
    episode: Episode | None = None,
    database_url: str | None = None,
) -> dict:
    """Record one reviewer saying "yes, that is the right payment".

    Adds a single row rather than rewriting the store, because `persist`
    truncates and this runs while somebody is working the queue.

    The row carries the batch it came from. A confirmation made on the graded
    set is kept - it is real knowledge and the next real batch should have it -
    but `build_from_split` will not read it back into a graded run. Learning
    from the records you are being marked on is how an eval quietly stops
    meaning anything.
    """
    url = database_url or get_settings().database_url
    variant = name_from_bank_text(bank_text) or ""
    if not variant:
        return {"alias_written": False, "reason": "no usable name in the bank text"}

    with psycopg.connect(url) as conn:
        conn.execute(
            """INSERT INTO aliases
                   (canonical_name, variant_name, confirmed_count, source_split)
               VALUES (%s, %s, 1, %s)
               ON CONFLICT (canonical_name, variant_name)
               DO UPDATE SET confirmed_count = aliases.confirmed_count + 1""",
            (canonical_name, variant, source_split.value),
        )
        if episode is not None:
            conn.execute(
                """INSERT INTO episodes (situation_text, resolution_text, tags, source_split)
                   VALUES (%s, %s, %s, %s)""",
                (episode.situation, episode.resolution, list(episode.tags),
                 episode.source_split.value),
            )
        conn.commit()

    return {
        "alias_written": True,
        "canonical": canonical_name,
        "variant": variant,
        "counts_towards_graded_runs": source_split is not Split.HELDOUT,
    }
