"""The only place an LLM makes a decision. Plan section 5, box 8.

Three rules hold this file together, and the whole safety argument rests on
them:

1. **The LLM never does arithmetic.** Every number it sees was computed by our
   code. It picks between options and explains in English.
2. **The LLM never has the final say on money.** It returns a recommendation.
   The hard rules in `guardrails.py` decide whether that recommendation is
   allowed to become an action.
3. **It may say "I cannot tell".** A model that never abstains is dangerous,
   and abstaining is a valid, useful answer here.

This is **single-shot structured classification**, not a ReAct loop. Everything
it needs is pre-fetched and handed to it; it picks from a closed list and
returns JSON. ReAct would let the model choose what to query, and it could pull
in a transaction blocking deliberately excluded - at which point the candidate
set is no longer the one the guardrails assumed.
"""

import hashlib
import json
from dataclasses import dataclass, field

import openai
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.intake import NormInvoice
from app.memory import Memory
from app.money import fmt
from app.scoring import Ranking, Scored

# How many candidates the model gets to see. Three is enough to choose from and
# small enough that the prompt stays cheap.
TOP_N = 3


class Adjudication(BaseModel):
    """What the model is allowed to return. Nothing else parses."""

    chosen_transaction_id: str | None = Field(
        description="The id of the payment that settles this invoice, or null if you cannot tell"
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(
        min_length=10,
        description="One or two sentences. Refer to the fields you used by name.",
    )
    evidence_fields: list[str] = Field(
        description="The specific fields that decided it, e.g. counterparty_name, amount, tds_2pct"
    )


@dataclass
class Verdict:
    """The adjudicator's answer, after our own checks on it."""

    chosen_id: str | None = None
    confidence: float = 0.0
    reasoning: str = ""
    evidence_fields: list[str] = field(default_factory=list)
    rejected: str | None = None      # why we threw the answer away
    prompt: str = ""
    raw_response: str = ""
    cached: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def usable(self) -> bool:
        return self.rejected is None and self.chosen_id is not None


# The stable prefix, byte-identical on every call. It goes in `instructions`
# rather than the per-record `input`, which is the boundary OpenAI caches on.
#
# On our workload caching never actually engages: it needs a prompt over 1024
# tokens and ours is about 670. Kept split anyway - it costs nothing, and it is
# the right shape if the prompt ever grows.
SYSTEM_RULES = """You are a reconciliation adjudicator for an Indian finance team.

You are given one unpaid invoice and a short list of candidate bank payments.
Your job is to say which payment settles the invoice, or that you cannot tell.

Rules you must follow:

1. Do no arithmetic. Every figure you are shown was already computed. Never
   calculate a new number, and never state a number that is not in the input.
2. Choose only from the candidate ids you are given. Never invent an id.
3. If no candidate is clearly right, return null. Saying "I cannot tell" is a
   correct and useful answer. Guessing is not.
4. Your answer is a recommendation. Separate hard rules decide whether it is
   allowed to take effect, so do not try to account for policy, limits or
   approval thresholds.

How to read each candidate:

- "amount status" is the finding that matters most.
    EXPLAINED means the difference between the invoice and the payment is
    fully accounted for by a known deduction. The invoice amount minus that
    deduction equals the payment to the rupee. This is a strong match, and the
    payment being smaller than the invoice is expected, not a problem.
    NOT EXPLAINED means money is missing that nothing accounts for.
- "amount working" shows the arithmetic behind that status. It has already
  been checked. Do not redo it.
- "name match" is 0 to 1. Bank narrations truncate and drop spaces, so 0.7 on
  a customer whose known bank names are listed above is a good match, not a
  weak one.
- "date match" is 0 to 1, measured from the due date. Paying late is normal.

Why a payment may be smaller or larger than the invoice:

- The gateway kept its fee plus 18% GST on that fee.
- The customer withheld tax at source, as Indian law requires, so they legally
  send less.
- A combined payment settles several invoices at once, so it is larger than
  any one of them.
- A partial payment is one instalment, so it is smaller than the invoice.

Choose the candidate whose amount status is EXPLAINED and whose name matches
the customer. If two candidates both fit, or none does, return null.

About confidence: it is your own belief that the payment you chose is the right
one, from 0 to 1. It is not a copy of any score in the input. If one candidate
has an EXPLAINED amount and a matching customer and the others do not, you are
confident - say so. If you return null, confidence is 0.

If similar cases settled before are shown, they are worked examples of how
cases of this shape were decided. Use them for the shape of the reasoning, not
as answers - the customers and amounts are different."""


def build_prompt(
    invoice: NormInvoice,
    candidates: list[Scored],
    memory: Memory,
    tags: set[str] | None = None,
) -> str:
    """Everything the model needs, and nothing it does not.

    Note every figure below is rendered from a value our code computed. The
    model is reading numbers, never producing them.
    """
    lines = [
        "INVOICE",
        f"  id            {invoice.id}",
        f"  customer      {invoice.name_clean}",
        f"  amount        {fmt(invoice.amount_paise)}",
        f"  due           {invoice.due_date}",
        "",
    ]

    known = sorted(memory.variants.get(invoice.name_clean, []))
    if known:
        lines += [
            f"KNOWN BANK NAMES FOR {invoice.name_clean}",
            *(f"  {name}" for name in known),
            "",
        ]

    lines.append(f"CANDIDATE PAYMENTS ({len(candidates)})")
    for scored in candidates:
        signals = scored.signals
        lines += [
            f"  {scored.id}",
            f"    bank text      {scored.txn.description_raw}",
            f"    amount         {fmt(scored.txn.amount_paise)}",
            f"    paid on        {scored.txn.value_date}",
            f"    amount status  {_amount_status(scored)}",
            f"    amount working {scored.amount.basis}",
            f"    name match     {_pct(signals.name)}",
            f"    date match     {_pct(signals.date)}",
            f"    reference      {_pct(signals.reference)}",
            "",
        ]

    past = memory.episodes_for(tags or set())
    if past:
        lines.append("SIMILAR CASES SETTLED BEFORE")
        for episode in past:
            lines += [f"  {episode.situation}", f"    -> {episode.resolution}", ""]

    lines.append(
        "Which candidate settles this invoice? Return null if you cannot tell."
    )
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    return "not available" if value is None else f"{value:.2f}"


def _amount_status(scored: Scored) -> str:
    """Say what the arithmetic *means*, not only what it was.

    The working alone was not enough: shown a line reading
    `Rs 2,53,950 - Rs 5,079 MDR - Rs 914.22 GST = Rs 2,47,956.78`, a small
    model still answered that no candidate matched the invoice amount. It read
    the numbers and missed the conclusion. Stating the conclusion in one word
    is what fixed it.
    """
    if scored.amount.score >= 0.95:
        return "EXPLAINED - the gap is fully accounted for"
    if scored.amount.score >= 0.85:
        return "EXPLAINED - this payment is part of a group that adds up"
    if scored.amount.score >= 0.3:
        return "NOT EXPLAINED - money is missing that nothing accounts for"
    return "NOT EXPLAINED - the amount does not fit this invoice"


def cache_key(invoice: NormInvoice, candidates: list[Scored]) -> str:
    """Stable across runs, so re-running a batch while tuning costs nothing.

    Keyed on what the model actually sees. If the invoice, the candidate ids or
    their scores change, the answer has to be asked for again.
    """
    payload = json.dumps(
        {
            "invoice": [invoice.id, invoice.amount_paise, str(invoice.due_date), invoice.name_clean],
            "candidates": [[c.id, round(c.score, 4), c.amount.basis] for c in candidates],
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class Adjudicator:
    """Wraps the one LLM call, its cache, its budget and its output checks."""

    def __init__(self, memory: Memory | None = None, budget: int | None = None):
        settings = get_settings()
        self.settings = settings
        self.memory = memory or Memory()
        self.budget = settings.llm_call_budget if budget is None else budget
        self.calls_made = 0
        self.cache: dict[str, Verdict] = {}
        self._client: openai.OpenAI | None = None

    @property
    def available(self) -> bool:
        return self.settings.has_real_llm_key

    @property
    def client(self) -> openai.OpenAI:
        if self._client is None:
            self._client = openai.OpenAI(
                api_key=self.settings.llm_api_key,
                # Empty means OpenAI direct. Any OpenAI-compatible gateway
                # works by setting this and prefixing the model name.
                base_url=self.settings.llm_base_url or None,
                # OpenRouter uses these to attribute traffic. Harmless
                # anywhere else.
                default_headers={
                    "HTTP-Referer": "https://github.com/divyansharma001/Hisaab",
                    "X-Title": "Hisaab",
                },
            )
        return self._client

    def adjudicate(
        self,
        invoice: NormInvoice,
        ranking: Ranking,
        use_cache: bool = True,
        tags: set[str] | None = None,
        temperature: float | None = None,
    ) -> Verdict:
        candidates = ranking.candidates[:TOP_N]
        if not candidates:
            return Verdict(rejected="NO_CANDIDATES")

        key = cache_key(invoice, candidates)
        if use_cache and key in self.cache:
            hit = self.cache[key]
            return Verdict(**{**hit.__dict__, "cached": True})

        if not self.available:
            return Verdict(rejected="NO_API_KEY")

        # A hard cap, so a runaway batch cannot spend without limit. Hitting it
        # sends the rest to a human rather than failing silently.
        if self.calls_made >= self.budget:
            return Verdict(rejected="BUDGET_EXHAUSTED")

        prompt = build_prompt(invoice, candidates, self.memory, tags)

        try:
            verdict = self._ask(prompt, {c.id for c in candidates}, temperature)
        except openai.APIError as exc:
            return Verdict(rejected=f"API_ERROR: {type(exc).__name__}", prompt=prompt)

        self.calls_made += 1
        self.cache[key] = verdict
        return verdict

    @retry(
        retry=retry_if_exception_type(
            (openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError)
        ),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        reraise=True,
    )
    def _ask(
        self, prompt: str, valid_ids: set[str], temperature: float | None = None
    ) -> Verdict:
        # Chat completions rather than the Responses API, because that is what
        # OpenAI-compatible gateways actually implement. `response_format`
        # carries the schema, so the reply is valid JSON in our shape or the
        # request fails - the schema check below is a belt on top of braces.
        #
        # The rules go in the system message and the record in the user
        # message. That split is what a provider caches on, though caching
        # needs a prompt over 1024 tokens and ours is around 670, so it never
        # engages here. Right shape, no effect at this size.
        response = self.client.chat.completions.parse(
            model=self.settings.llm_model,
            messages=[
                {"role": "system", "content": SYSTEM_RULES},
                {"role": "user", "content": prompt},
            ],
            response_format=Adjudication,
            max_completion_tokens=1024,
            # Left unset for real runs. The self-consistency test raises it on
            # purpose: at temperature 0 the answers are near-identical by
            # construction and the test proves nothing. Section 18, bug 9.
            **({"temperature": temperature} if temperature is not None else {}),
        )

        usage = dict(
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
            cache_read_tokens=(
                getattr(response.usage.prompt_tokens_details, "cached_tokens", 0) or 0
                if response.usage and response.usage.prompt_tokens_details
                else 0
            ),
        )
        raw = response.to_json(warnings=False)

        choice = response.choices[0] if response.choices else None
        answer = choice.message.parsed if choice else None
        if answer is None:
            return Verdict(rejected="SCHEMA_INVALID", prompt=prompt, raw_response=raw, **usage)

        # Output guardrail: it must pick an id we actually sent it. Two lines,
        # and it is the whole anti-hallucination check.
        if answer.chosen_transaction_id is not None and answer.chosen_transaction_id not in valid_ids:
            return Verdict(
                rejected=f"HALLUCINATED_ID: {answer.chosen_transaction_id}",
                prompt=prompt,
                raw_response=raw,
                **usage,
            )

        # Output guardrail: a confidence with no named field behind it is a
        # number with nothing holding it up.
        if answer.chosen_transaction_id is not None and not answer.evidence_fields:
            return Verdict(rejected="NO_EVIDENCE_NAMED", prompt=prompt, raw_response=raw, **usage)

        return Verdict(
            chosen_id=answer.chosen_transaction_id,
            confidence=answer.confidence,
            reasoning=answer.reasoning,
            evidence_fields=list(answer.evidence_fields),
            prompt=prompt,
            raw_response=raw,
            **usage,
        )
