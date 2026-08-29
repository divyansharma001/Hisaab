"""Counterparties, and the many ways their names arrive from a bank.

The whole name-matching problem lives here. If the variants are too tidy the
dataset is too easy, and the reported accuracy means nothing.
"""

import random
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app.names import clean_name, squash

# The comparison that actually matters is one company's clean name against
# another company's bank variant, because that is exactly the pair the scorer
# sees: an invoice name against a name pulled out of a bank narration. Variants
# are not compared with each other - they share channel words by nature, and
# nothing ever compares two of them.
CONFUSABLE_AT = 78

_FIRST = [
    "ABC", "Kaveri", "Sunrise", "Meridian", "Zenith", "Orchid", "Sagar",
    "Trident", "Vertex", "Nimbus", "Lodestar", "Anantha", "Bluepeak",
    "Chandra", "Deccan", "Everest", "Ganga", "Harsha", "Indus", "Jayanti",
    "Konark", "Lakshmi", "Maurya", "Narmada", "Oberoi", "Pinnacle", "Quantum",
    "Rajdhani", "Shreyas", "Tapti", "Uttara", "Vindhya", "Yamuna", "Zephyr",
    "Aravind", "Bharat", "Coromandel", "Dhanvi", "Eastwind", "Falcon",
    "Girija", "Hampi", "Ishaan", "Jalgaon", "Kalinga", "Malabar", "Nilgiri",
    "Palash", "Raichur", "Satpura", "Tungabhadra", "Vaigai", "Warangal",
    "Alaknanda", "Bhagirathi", "Chenab", "Dwarka", "Ellora", "Gokarna",
]

_DOMAIN = [
    "Technologies", "Foods", "Logistics", "Textiles", "Pharma", "Industries",
    "Motors", "Chemicals", "Exports", "Systems", "Solutions", "Enterprises",
    "Trading", "Agro", "Steel", "Packaging", "Infra", "Electricals",
    "Ceramics", "Polymers", "Instruments", "Bearings", "Fabrics", "Seeds",
]

_SUFFIX = ["Pvt Ltd", "Limited", "LLP", "India Pvt Ltd", ""]

# How the domain word gets shortened in a cramped bank field.
_SHORT = {
    "Technologies": "TECH",
    "Industries": "IND",
    "Enterprises": "ENT",
    "Solutions": "SOL",
    "Logistics": "LOG",
    "Packaging": "PACK",
    "Electricals": "ELEC",
    "Chemicals": "CHEM",
}


@dataclass
class Counterparty:
    canonical: str                          # what the invoice says
    clean: str                              # normalised, for comparison
    variants: list[str] = field(default_factory=list)  # what banks send

    def bank_name(self, rng: random.Random, *, messy: bool = True) -> str:
        """One of the messy forms, or the clean one when we want an easy case."""
        if not messy or not self.variants:
            return self.clean
        return rng.choice(self.variants)


def _variants(first: str, domain: str, suffix: str) -> list[str]:
    """The realistic ways this name gets mangled on the way to a bank feed."""
    full = clean_name(f"{first} {domain}")
    short_domain = _SHORT.get(domain, domain.upper()[:4])

    out = [
        squash(f"{first} {domain}"),                    # ABCTECHNOLOGIES
        squash(f"{first} {domain} {suffix}") or full,   # ABCTECHNOLOGIESPVTLTD
        f"{first.upper()} {short_domain}",              # ABC TECH
        squash(f"{first} {short_domain}"),              # ABCTECH
        f"{full} INDIA",                                # ABC TECHNOLOGIES INDIA
        f"{short_domain} {first.upper()}",              # TECH ABC, word order flipped
    ]

    seen: set[str] = set()
    return [v for v in out if v and not (v in seen or seen.add(v))]


def build_pool(rng: random.Random, size: int) -> list[Counterparty]:
    """A pool of companies that no fuzzy matcher could confuse with each other.

    Every form a company can take - its clean name and all its bank variants -
    is compared against every form already accepted. If anything is close, the
    candidate is dropped.

    Without this, two unrelated companies like NARMADA INFRA INDIA and
    YAMUNA INFRA INDIA score 81 against each other, and the answer key ends up
    claiming a single right answer where two exist.
    """
    combos = [(f, d) for f in _FIRST for d in _DOMAIN]
    rng.shuffle(combos)

    pool: list[Counterparty] = []
    cleans: list[str] = []
    variant_forms: list[str] = []

    for first, domain in combos:
        if len(pool) >= size:
            break

        suffix = rng.choice(_SUFFIX)
        canonical = " ".join(x for x in (first, domain, suffix) if x)
        clean = clean_name(canonical)
        variants = _variants(first, domain, suffix)

        collides = any(
            fuzz.token_set_ratio(clean, other) >= CONFUSABLE_AT
            for other in variant_forms
        ) or any(
            fuzz.token_set_ratio(variant, other) >= CONFUSABLE_AT
            for variant in variants
            for other in cleans
        )
        if collides:
            continue

        cleans.append(clean)
        variant_forms.extend(variants)
        pool.append(Counterparty(canonical, clean, variants))

    if len(pool) < size:
        raise ValueError(
            f"only built {len(pool)} well-separated companies, needed {size}; "
            "add more words to _FIRST or _DOMAIN"
        )
    return pool
