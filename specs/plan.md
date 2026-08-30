# AI Finance Controller — Final Plan

**What we are building:** a system that takes a batch of invoices and bank
transactions, figures out which payment belongs to which bill, and is honest
about the ones it cannot figure out.

**The one-line pitch:** *Zero wrong auto-approvals, with most records handled
without a human, and every unresolved case explained.*

**The track:** Razorpay AI Buildathon, Track 04 — AI Finance Controller. The brief asks for
an agent that closes one finance-ops loop across a 50+ record batch of synthetic data,
reporting its match rate and the exceptions it could not resolve. Its stated bar is
*"throughput plus measured accuracy plus an honest exception list."* Its stated premise is
that **verification capacity, not generation speed, is the bottleneck** — which is exactly
why the guardrail layer (§7) and the eval harness (§8) are the centre of this plan, not
the LLM.

---

## Table of contents

1. [The problem in plain English](#1-the-problem-in-plain-english)
2. [Every financial term, explained](#2-every-financial-term-explained)
3. [Agent concepts, mapped to things you already know](#3-agent-concepts-mapped-to-things-you-already-know)
4. [The architecture](#4-the-architecture)
5. [What each box does](#5-what-each-box-does)
6. [Scoring — how we rank candidates](#6-scoring--how-we-rank-candidates)
7. [Guardrails — the three layers](#7-guardrails--the-three-layers)
8. [Evals — how we prove it works](#8-evals--how-we-prove-it-works)
9. [The synthetic dataset](#9-the-synthetic-dataset)
10. [Database schema](#10-database-schema)
11. [Build order, phase by phase](#11-build-order-phase-by-phase)
12. [What we are deliberately NOT building](#12-what-we-are-deliberately-not-building)
13. [Tech stack](#13-tech-stack)
14. [How we actually build the agents](#14-how-we-actually-build-the-agents)
15. [Caching, RAG, and what we deliberately skipped](#15-caching-rag-and-what-we-deliberately-skipped)
16. [The React UI](#16-the-react-ui)
17. [Demo script](#17-demo-script)
18. [Pre-build bug sweep](#18-pre-build-bug-sweep)
19. [Winning margin — seven additions](#19-winning-margin--seven-additions)
20. [Alignment with the track brief](#20-alignment-with-the-track-brief)

---

## 1. The problem in plain English

- A company sends out bills. Money arrives in its bank account.
- Somebody has to say: *this ₹9,764 that arrived on Tuesday pays off that
  ₹10,000 bill from last month.*
- This is boring, high-volume, and easy to get wrong.
- It is hard to automate for four reasons:

  1. **Names don't match.** The bill says `ABC Technologies`. The bank says
     `NEFT/ABCTECHPVTLTD/882910`.
  2. **Amounts don't match.** Fees and taxes get deducted along the way, so
     ₹10,000 owed becomes ₹9,764 received.
  3. **The shapes don't match.** One payment might cover three bills. One bill
     might be paid in two instalments.
  4. **Sometimes there genuinely is no answer.** The customer just hasn't paid.

- Our system must handle 1, 2 and 3 automatically — and must **refuse to guess**
  on 4.

---

## 2. Every financial term, explained

### 2.1 The documents

| Term | Plain English |
|---|---|
| **Invoice** | A bill you sent a customer. "You owe me ₹10,000, due on the 30th." |
| **Bank transaction** | A line on your bank statement. Money that actually landed. Has a date, an amount, and a messy text description. |
| **Payment gateway record** | If the customer paid by card or UPI, a company like Razorpay holds the money briefly, then forwards it. This is their record of it. |
| **Credit note** | A document that *reduces* what a customer owes. Used for returned goods or an agreed discount. Explains a legitimately smaller payment. |
| **Debit note** | The opposite. Increases what is owed. |

### 2.2 The core job

| Term | Plain English |
|---|---|
| **Reconciliation** | Matching "who owed us money" against "money that arrived." This is our whole project. |
| **Open invoice** | A bill with no payment matched to it yet. Still outstanding. |
| **Counterparty** | The other company in the deal. Your customer or your vendor. |
| **Aging** | How old an unpaid bill is. Usually bucketed: 0–30 days, 31–60, 61–90, 90+. |

### 2.3 Reference numbers

| Term | Plain English |
|---|---|
| **UTR** | *Unique Transaction Reference.* A 12–22 character code the Indian banking system attaches to every NEFT/RTGS/IMPS transfer. |
| **Payment reference / remittance advice** | A note the customer attaches saying what the payment is for. If they typed the invoice number here, matching is trivial. They usually don't. |

### 2.4 Why the amount received is never the amount billed

This is the single most important section. Most fake "mismatches" come from here.

| Term | Plain English | Example on a ₹10,000 bill |
|---|---|---|
| **Gross** | The full invoice amount, before anything is taken out. | ₹10,000 |
| **Platform fee / MDR** | What the payment gateway keeps for processing the card or UPI payment. Usually 1.5%–2.5%. | −₹200 |
| **GST on the fee** | Indian sales tax, 18%, charged by the gateway on *its own fee.* | −₹36 |
| **TDS** | *Tax Deducted at Source.* Indian law says for certain payments the **customer** must hold back a slice (often 2%) and pay it straight to the tax department. So they legally send you less. | −₹200 |
| **Net / settlement amount** | What actually hits your bank after everything above. | ₹9,564 |

- **Key point:** every one of those deductions is *correct and expected.*
- A naive matcher sees `₹10,000` vs `₹9,564`, calls it a mismatch, and creates a
  useless exception.
- Our system will **compute the expected deductions first**, then compare. This
  turns dozens of fake exceptions into clean matches.
- **Note on GST:** it appears in two different places. GST *on your invoice* is
  tax you charge the customer. GST *on the gateway fee* is tax the gateway
  charges you. Don't confuse them. Only the second one reduces what you receive.

### 2.5 Settlement

| Term | Plain English |
|---|---|
| **Settlement** | The gateway transferring the net money to your bank. |
| **T+2** | It arrives two working days later, not instantly. |
| **Batched settlement** | The gateway lumps 40 customer payments into one bank deposit. So one bank line ≠ one customer payment. |
| **Settlement UTR** | The reference on the *batch* deposit. Note it identifies the whole batch, not any single customer payment inside it. |

**Mirror the real structure.** This is Razorpay's own track, so our synthetic data should
follow the settlement shape their merchants actually see: MDR plus GST on MDR, a T+1/T+2
cycle, and batched settlements carrying their own UTR. Check their public settlement
documentation before writing the generator. Matching their vocabulary costs nothing and
makes the demo land with people who look at this data every day.

### 2.6 Payment patterns that break simple matching

| Term | Plain English |
|---|---|
| **Partial payment** | ₹10,000 bill, customer sends ₹6,000 now and ₹4,000 later. **One bill, two transactions.** |
| **Combined payment** | Customer owes on three bills, sends one payment of ₹30,000. **Three bills, one transaction.** |
| **Short payment** | Customer pays less and doesn't say why. A genuine exception. |
| **Overpayment / advance** | Customer pays too much, or pays before you've billed them. |
| **Duplicate transaction** | The same payment appears twice — either a data feed glitch, or the customer really did pay twice. |
| **Chargeback** | The customer disputes a card payment and the bank forcibly reverses it. Money appears, then vanishes weeks later. |
| **Refund** | Money going back out to the customer. |

### 2.7 Measurement terms

| Term | Plain English |
|---|---|
| **Tolerance** | How much amount difference we're willing to ignore. We'll use ₹1 for rounding. |
| **Straight-through processing (STP)** | The percentage of records handled with **zero human involvement.** This is what real finance teams actually care about. |
| **Exception** | A record the system refuses to decide on. Goes to a human. |
| **Audit trail** | An unchangeable log of every decision and the reason for it. In finance this is a **legal requirement**, not a nice-to-have. |
| **Write-off** | Giving up on collecting the rest. Always requires a human. We will never automate this. |

---

## 3. Agent concepts, mapped to things you already know

You've built databases and queues. Here's the translation.

| Agent world | What it actually is |
|---|---|
| **Agent** | A worker process, except one of its steps calls an LLM. |
| **Tool** | A normal function, with a JSON schema wrapped around it so the LLM can request it. |
| **Tool call** | The LLM returns `{"name": "lookup_invoice", "args": {...}}`. Your code parses that and calls your own function. |
| **Agent loop** | `while not done: ask LLM → it requests a tool → run tool → feed result back`. |
| **Orchestrator** | A router. Same as an API gateway deciding which service handles a request. |
| **Memory** | A database read whose result you paste into the prompt. Nothing magical. |
| **Guardrail** | Validation middleware, but running on the LLM's *output* instead of on user input. |
| **Eval** | A test suite, where the "unit" is a whole pipeline decision instead of a function. |

### The one mental shift

- An LLM call is a **function that gives different answers to the same input.**
- Everything else in this architecture exists to contain that single fact.

### Three rules we will not break

1. **The orchestrator is plain `if/else` code, not an LLM.** It has three fixed
   branches. An LLM there would add cost, delay and randomness for nothing.
2. **The LLM never does arithmetic.** Our code computes every number. The LLM
   only *chooses* between options and *explains* in English.
3. **The LLM never has the final say on money.** It makes a recommendation. Hard
   coded rules decide whether that recommendation is allowed to become an action.

---

## 4. The architecture

```
                        ┌──────────────────────────┐
                        │   SYNTHETIC DATA         │
                        │  invoices, bank txns,    │
                        │  gateway records         │
                        └────────────┬─────────────┘
                                     ▼
                        ┌──────────────────────────┐
                        │  1. INTAKE / NORMALISER  │   plain code
                        │  clean names, parse      │
                        │  dates, pull reference   │
                        └────────────┬─────────────┘
                                     ▼
                              ┌─────────────┐
                              │ INPUT       │  reject junk before
                              │ GUARDRAILS  │  it costs us anything
                              └──────┬──────┘
                                     ▼
                        ┌──────────────────────────┐
                        │  2. ORCHESTRATOR         │   plain if/else
                        │     (a router)           │
                        └────────────┬─────────────┘
                     ┌───────────────┴───────────────┐
     has a valid     │                               │  no reference,
     reference no.   ▼                               ▼  must work it out
            ┌────────────────┐            ┌────────────────────────┐
            │  3. FAST PATH  │            │  4. BLOCKING           │
            │  exact match   │            │  narrow to ~10         │
            │  no LLM        │            │  plausible candidates  │
            └────────┬───────┘            └───────────┬────────────┘
                     │                                ▼
                     │                    ┌────────────────────────┐
                     │                    │  5. SETTLEMENT MATHS   │
                     │                    │  gross − fee − GST     │
                     │                    │  − TDS = expected net  │
                     │                    │  runs BEFORE scoring   │
                     │                    └───────────┬────────────┘
                     │                                ▼
                     │                    ┌────────────────────────┐
                     │                    │  6. SCORER             │
                     │                    │  score every pair,     │
                     │                    │  compute margin        │
                     │                    │  (top − second)        │
                     │                    └───────────┬────────────┘
                     │                                ▼
                     │                    ┌────────────────────────┐
                     │                    │  7. GLOBAL ASSIGNMENT  │
                     │                    │  resolve conflicts     │
                     │                    │  across the WHOLE      │
                     │                    │  batch at once         │
                     │                    └───────────┬────────────┘
                     │                                │
                     │              ┌─────────────────┼─────────────────┐
                     │              │                 │                 │
                     │        score high        score middling     score low
                     │        margin wide       or margin tiny     nothing close
                     │              │                 ▼                 │
                     │              │      ┌──────────────────┐         │
                     │              │      │ 8. LLM           │         │
                     │              │      │    ADJUDICATOR   │         │
                     │              │      │ picks from top 3 │         │
                     │              │      │ returns JSON     │         │
                     │              │      └────────┬─────────┘         │
                     │              │               ▼                   │
                     │              │      ┌──────────────────┐         │
                     │              │      │ OUTPUT GUARDRAIL │         │
                     │              │      │ schema valid?    │         │
                     │              │      │ ID really exists?│         │
                     │              │      │ no invented nos.?│         │
                     │              │      └────────┬─────────┘         │
                     │              │               │                   │
                     └──────────────┴───────────────┴───────────────────┘
                                            ▼
                        ┌────────────────────────────────────┐
                        │  9. DECISION GUARDRAIL LAYER       │
                        │  hard rules. no LLM. no exceptions.│
                        │  score / margin / amount gap /     │
                        │  date window / already used? /     │
                        │  value ceiling / new counterparty  │
                        └───────┬──────────┬─────────┬───────┘
                                ▼          ▼         ▼
                        ┌──────────┐ ┌──────────┐ ┌──────────┐
                        │  AUTO    │ │  NEEDS   │ │EXCEPTION │
                        │ APPROVED │ │  REVIEW  │ │ + reason │
                        └────┬─────┘ └────┬─────┘ └────┬─────┘
                             └────────────┼────────────┘
                                          ▼
                        ┌────────────────────────────────────┐
                        │  10. AUDIT LOG  (every decision,   │
                        │      every reason, immutable)      │
                        └───────┬────────────────────┬───────┘
                                ▼                    ▼
                    ┌────────────────────┐  ┌────────────────────┐
                    │  11. DASHBOARD     │  │  12. EVAL HARNESS  │
                    │  match rate, STP,  │  │  compare against   │
                    │  exception list    │  │  the answer key    │
                    └────────────────────┘  └─────────┬──────────┘
                                                      │
                                            findings feed back
                                            into thresholds ────┐
                                                                │
                        ┌───────────────────────────────────────┘
                        ▼
            ┌────────────────────────────────┐
            │  MEMORY (plain Postgres)       │
            │  • aliases: ABC Technologies   │  read by scorer
            │    = ABCTECHPVTLTD             │  and adjudicator
            │  • episodes: past tricky cases │
            └────────────────────────────────┘
```

### Four things this fixes from the earlier draft

1. **Settlement maths moved inside the pipeline, not beside it.** The fee and TDS
   deductions must be worked out *before* we compare amounts, or every deducted
   payment becomes a false exception.
2. **Global assignment added.** Without it, whichever invoice we happen to process
   first eats the transaction, and the answer depends on sort order.
3. **The guardrail layer now has exits.** Three named end states, all logged.
4. **Evals feed back.** There is now a loop from "we decided" to "were we right."

---

## 5. What each box does

### 1. Intake / Normaliser — *plain code*
- Uppercase names, strip `PVT LTD`, `LIMITED`, `PRIVATE`, punctuation, extra spaces.
- Parse all date formats into one format.
- Convert all amounts to paise (integers). **Never use floats for money.**
- Scan the payment description for anything that looks like an invoice number or UTR.

### 2. Orchestrator — *plain code, an `if/else`*
- Has a usable reference number → **fast path**.
- No reference → **matching pipeline**.
- Failed input guardrails → **straight to exceptions**.

### 3. Fast path — *plain code, no LLM*
- Reference in the payment note exactly equals an invoice number.
- Still has to pass the decision guardrails. A reference match is strong evidence,
  not a free pass.
- Should handle roughly a quarter of the batch at near-zero cost.

### 4. Blocking — *plain code*
- For each invoice, pull only plausible transactions instead of comparing against all of them.
- **Three separate passes, not one.** A single ±25% amount window would filter out the exact
  cases we want to showcase — a combined payment is 3x the invoice, a partial is 0.6x.

```python
candidates  = block_1to1(inv, txns)        # 0.75x to 1.10x
candidates += block_partial(inv, txns)     # 0.05x to 0.95x, same counterparty
candidates += block_combined(inv, txns)    # above 1.05x, same counterparty,
                                           # capped at everything they owe
```

- **The combined pass has no fixed multiple.** A single payment covering a ₹57,600 bill and a
  ₹3,71,750 bill is 7.45x the small one, so a 5.0x cap drops the small invoice's own payment
  before scoring. The real ceiling is the sum of that customer's open invoices: nobody pays more
  than they owe. See §18, bug 12.

- **Tag every candidate with the pass that found it.** The scorer must know it is looking at
  a partial or a combination, or the amount score is meaningless.
- **Why block at all:** comparing every invoice against every transaction is slow and produces
  garbage candidates. Same instinct as indexing a table.

### 5. Settlement maths — *plain code*
- For each candidate, try each known deduction formula:
  - gross − MDR − GST-on-MDR
  - gross − TDS 2%
  - gross − TDS 10%
  - gross − MDR − GST-on-MDR − TDS
- If one formula explains the gap to within ₹1, record **which one**.
- That recorded formula becomes the explanation shown to the user later.

### 6. Scorer — *plain code*
- Produces a score from 0 to 1 for every invoice-candidate pair. Details in §6.
- **Weights are renormalised over the signals actually available** — a missing reference
  must not be treated as evidence against the match. See §6.2; this is easy to get wrong.
- Also computes **margin** = best score − second best score.
- **Margin is the most important number in the system.** A best score of 0.95 means
  nothing if the runner-up also scored 0.94 — that's a coin flip, not a match.

### 7. Global assignment — *plain code*
- Look at the whole batch at once, not one record at a time.
- If two invoices both want the same transaction with similar scores, **neither**
  gets auto-approved. Both become exceptions marked ambiguous.
- Backed by a `UNIQUE` constraint on `transaction_id` in the database, so a bug in
  our logic still can't double-count money.

### 8. LLM Adjudicator — *the only place the LLM makes a choice*
- **Fires only when:** score is between 0.70 and 0.90, or margin is under 0.15.
- **Receives:** the invoice, the top 3 candidates only, relevant aliases, and 2–3
  similar past cases from memory.
- **Returns strict JSON:**

```json
{
  "chosen_transaction_id": "TXN-1180" ,
  "confidence": 0.86,
  "reasoning": "Name matches known alias. Gap of ₹200 equals 2% TDS.",
  "evidence_fields": ["counterparty_name", "amount", "tds_2pct"]
}
```

- It may also return `"chosen_transaction_id": null` to say *I can't tell.*
  **This is a valid and valuable answer.** A model that never abstains is dangerous.

### 9. Decision guardrail layer — *hard rules, covered in §7*

### 10. Audit log
- One row per decision: record ID, outcome, score, margin, which rules passed and
  failed, whether the LLM was used, prompt, raw response, timestamp.
- Append-only. Never updated.

### 11. Dashboard
- Totals, straight-through rate, per-scenario breakdown, and the exception list
  with a reason code for each.

### 12. Eval harness
- One command. Compares every decision against the answer key. Details in §8.

### Memory — *plain Postgres tables, not a vector database*
- **`aliases`**: canonical name → known variants. This is what makes name matching
  work at all.
- **`episodes`**: past tricky cases and how they were resolved, retrieved and
  shown to the adjudicator as examples.
- **Why not a vector database:** our data is structured and numeric. `WHERE amount
  BETWEEN x AND y` is exactly the right tool. Adding a vector store here would be
  complexity for its own sake. Only add one if we finish early *and* can point to a
  specific case SQL couldn't handle.

---

## 6. Scoring — how we rank candidates

### 6.1 The structure

Every invoice-transaction pair gets a score from 0 to 1. It is a **weighted sum of four
independent signals**, each itself scored 0 to 1.

```
score = sum(signal_value * signal_weight)
```

**Why a weighted sum and not a machine learning model?**

- We have no training data. We are generating the data.
- It is **decomposable** — we can show exactly why a pair scored 0.71. The decision-trace
  screen in the UI depends on this.
- We can tune it by hand in seconds. A model would need retraining.

| Signal | Base weight |
|---|---|
| Reference match | 0.50 |
| Amount match | 0.25 |
| Name similarity | 0.15 |
| Date closeness | 0.10 |

### 6.2 The renormalisation rule (do not skip this)

A naive weighted sum has a serious bug. If a transaction carries **no reference at all**,
that signal scores 0 — so the pair caps at 0.50 however perfect everything else is.

Worked example — right alias, gap explained exactly by 2% TDS, paid on time, no reference
in the bank text:

```
reference  0.00 x 0.50 = 0.000
amount     0.95 x 0.25 = 0.238
name       1.00 x 0.15 = 0.150
date       1.00 x 0.10 = 0.100
                  total = 0.488   -> EXCEPTION
```

A perfect match scored as an exception. The cause: **a missing signal is being treated as
evidence against the match, when it is only absence of evidence.**

**Fix — renormalise the weights over the signals that are actually available:**

```
reference  (unavailable, dropped from the denominator)
amount     0.25 / 0.50 = 0.50
name       0.15 / 0.50 = 0.30
date       0.10 / 0.50 = 0.20
```

Same pair, rescored:

```
amount     0.95 x 0.50 = 0.475
name       1.00 x 0.30 = 0.300
date       1.00 x 0.20 = 0.200
                  total = 0.975   -> AUTO-APPROVED
```

- **Absence of a signal shrinks the denominator. It never counts as a zero.**
- This is why each scorer returns `None` for unavailable, distinct from `0.0` for
  present-but-contradicting.

### 6.3 Reference — weight 0.50

```python
def score_reference(invoice, txn):
    if not txn.extracted_refs:
        return None                          # unavailable, not zero
    for ref in txn.extracted_refs:
        if ref == invoice.invoice_no:
            return 1.0                       # exact
        if invoice.invoice_no in ref:
            return 0.7                       # embedded in a longer string
        if fuzz.ratio(ref, invoice.invoice_no) > 85:
            return 0.4                       # one or two characters off
    return 0.0                               # refs present, none match
```

- `None` means the bank gave us no reference.
- `0.0` means there **was** a reference and it pointed elsewhere — real evidence *against*.

### 6.4 Amount — weight 0.25

Runs **after** the settlement maths, never before.

```python
def score_amount(invoice, txn, settlement):
    gap = abs(invoice.amount_paise - txn.amount_paise)

    if gap <= 100:                           # Rs 1 tolerance
        return 1.0, "exact"

    if settlement.explains(gap):             # MDR, GST on MDR, TDS
        return 0.95, settlement.formula_used

    pct = gap / invoice.amount_paise
    if pct < 0.01:  return 0.6, "small unexplained gap"
    if pct < 0.05:  return 0.3, "unexplained gap"
    return 0.0, "amount does not match"
```

- An explained match scores **0.95, not 1.0**. A formula that happens to fit is weaker
  evidence than an exact figure.
- The formula name is stored either way. That string is what the UI displays.

### 6.5 Name — weight 0.15

```python
def score_name(invoice, txn, aliases):
    a, b = invoice.name_clean, txn.name_clean

    if aliases.same_entity(a, b):
        return 1.0
    if a == b:
        return 1.0
    return fuzz.token_set_ratio(a, b) / 100
```

- Use `token_set_ratio`, **not** plain `ratio`. It handles reordered and extra words, so
  `ABC TECH` against `TECH ABC INDIA` still scores high. That is what messy bank strings
  look like.
- **Every confirmed match writes a new alias row.** Name matching improves as the system runs.

### 6.6 Date — weight 0.10

```python
def score_date(invoice, txn):
    days = (txn.value_date - invoice.due_date).days

    if days < -7:   return 0.0    # paid a week before invoicing, suspicious
    if days <= 7:   return 1.0
    if days <= 45:  return 1.0 - (days - 7) / 38 * 0.7
    return 0.0
```

- Late is normal. Early is odd. The asymmetry is deliberate.

### 6.7 Margin — the most important number

```python
margin = scores[0] - scores[1]     # best minus runner-up
```

- Best 0.95, runner-up 0.42 → margin 0.53. **One clear winner.**
- Best 0.95, runner-up 0.94 → margin 0.01. **A coin flip wearing a high score.**

The second case is the **two-identical-transactions** scenario: one bill, and a duplicated payment
that appears twice. Score alone waves it through, margin catches it, and measured on the held-out
set those records do land at margin 0.00.

**Margin does not catch two identical invoices**, and it is worth being precise about why.
Ranking runs per invoice, over candidate *transactions*, so the runner-up is another payment - not
another bill.
Two identical invoices each see one obvious payment and each score a wide margin (0.79 on our data).
The ambiguity is on the transaction side, and the thing that catches it is **global assignment**
(§5, box 7), which refuses to auto-approve when two invoices want the same payment.

The two mechanisms are mirror images and both are needed:

| Ambiguity | Caught by |
|---|---|
| Two payments compete for one invoice | Margin |
| Two invoices compete for one payment | Global assignment |

Both numbers go to the guardrails; neither alone can approve anything.

### 6.8 Three more worked examples

**Combined payment, seen from one invoice.** No reference, amount is 3x the invoice,
name matches.

```
amount   0.00 x 0.50 = 0.000
name     1.00 x 0.30 = 0.300
date     1.00 x 0.20 = 0.200
                total = 0.500   -> ambiguous band -> LLM adjudicator
```

**Careful — 0.500 is below the 0.70 adjudicator floor, so this falls to EXCEPTION, not to
the LLM.** That is only correct behaviour if the combined-payment blocking pass (§5, box 4)
has tagged the candidate, letting `score_amount` recognise *"this equals the sum of three
open invoices for this counterparty"* and score it around 0.8 instead of 0.0. Without that
tag, every combined payment becomes an exception and the LLM never sees the one case it is
best placed to resolve.

**Unexplained short payment.** Rs 340 missing, no formula fits.

```
amount   0.30 x 0.50 = 0.150
name     0.90 x 0.30 = 0.270
date     1.00 x 0.20 = 0.200
                total = 0.620   -> EXCEPTION
```

Correct. Below 0.70, so no LLM call, straight to a human. The reason code writes itself:
`AMOUNT_GAP_UNEXPLAINED: Rs 340`.

**Clean reference match.**

```
reference 1.00 x 0.50 = 0.500
amount    1.00 x 0.25 = 0.250
name      0.95 x 0.15 = 0.143
date      1.00 x 0.10 = 0.100
                 total = 0.993   -> AUTO-APPROVED
```

### 6.9 Where the weights come from

Do not guess them, and do not defend them with intuition. **Tune them against ground truth:**

1. Grid search plausible combinations — reference 0.4 to 0.6, amount 0.2 to 0.35, and so on.
2. Run the **tuning set** for each combination — never the held-out set (§9).
3. **Reject any combination producing even one false auto-approval.** A hard constraint,
   not part of the objective function.
4. Among survivors, pick the highest straight-through rate.
5. Report both the chosen weights **and** how sensitive the results were to them.

*"We grid-searched the weights under a zero-false-approval constraint"* is a far stronger
answer than *"these felt about right."*

---

## 7. Guardrails — the three layers

Most projects only build the third layer. All three matter.

### Layer 1 — Input guardrails (before anything else)

Cheap, deterministic, run first.

- Amount must be a positive number.
- Currency must be present and must be INR.
- Date must parse, and must not be in the future.
- Invoice must not already be fully settled.
- Every record must have an ID.
- **Anything that fails → `EXCEPTION: MALFORMED_INPUT`.** Never send bad data
  downstream, and never send it to an LLM.

### Layer 2 — Decision guardrails (hard rules on every proposed match)

**These are `if` statements, not prompts.** A match must pass **all** of them to
be auto-approved.

| Rule | Threshold | Why it exists |
|---|---|---|
| Match score | ≥ 0.90 | Basic quality bar |
| Margin | ≥ 0.15 | Stops coin-flips between near-identical candidates |
| Amount gap after settlement maths | ≤ ₹1 | Unexplained money is never OK |
| Date: payment vs **due date** | −7 to +45 days | Catches wildly wrong pairings. Must use the same anchor as `score_date()` — see §18, bug 5 |
| Transaction already matched? | Must be no | Stops double-counting revenue |
| Currency | Must be identical | No silent cross-currency matches |
| **Value ceiling** | Over ₹5,00,000 → always human | A wrong ₹500 match is annoying. A wrong ₹5,00,000 match is how fraud gets through. |
| **New counterparty** | Fewer than 3 prior confirmed matches → never auto-approve | New names are the highest-risk category |
| **Duplicate check** | Same amount + same counterparty within 48h → flag both | Catches double payments and feed glitches |

- **The score does not get a veto.** Score 0.99 but an unexplained gap of ₹340?
  **Exception.** No argument.

**Routing precedence — write this exactly once, in one function.** Two rules would otherwise
both claim a record scoring 0.95 with a margin of 0.03. Ambiguity beats confidence:

```python
def route(score, margin):
    if margin < 0.15:   return ADJUDICATE   # ambiguity wins, whatever the score
    if score >= 0.90:   return AUTO
    if score >= 0.70:   return ADJUDICATE
    return EXCEPTION
```

- **Sole-candidate rule:** when a candidate set has exactly one member (every fast-path match),
  set `margin = 1.0` explicitly and record `margin_basis = "sole_candidate"`. Never leave it
  null — a null comparison here silently passes or silently fails every fast-path record.

### Layer 3 — LLM output guardrails

Treat the model as an untrusted client submitting a form.

1. **Schema validation.** Must be valid JSON in the expected shape. Fails → retry
   once → then exception.
2. **The chosen ID must exist in the list we sent it.** If it invents `TXN-9931`,
   reject instantly. This is our anti-hallucination check and it's about two lines
   of code.
3. **No numbers from the LLM.** Every money figure in its explanation must trace
   back to a value our code computed.
4. **Confidence must come with a reason** that names at least one concrete field.
5. **Budget cap.** A hard limit on LLM calls per batch. Hit the cap → remaining
   records go to review, not silent failure.
6. **Log the exact prompt and raw response** for every single call. When something
   goes wrong during the demo, this is how we explain it in ten seconds.

---

## 8. Evals — how we prove it works

This is where most projects have nothing. It's our biggest chance to stand out.

### 8.1 The foundation: generate the answer key alongside the data

Because the data is synthetic, **we know the truth for free.** The generator writes
both files from the same function:

```json
{
  "INV-023": {"match": "TXN-1180", "reason": "TDS 2% deducted"},
  "INV-041": {"match": null,       "reason": "customer never paid"},
  "INV-056": {"match": "AMBIGUOUS","reason": "two identical invoices"}
}
```

Everything below depends on this file existing.

### 8.2 Why match rate alone is a bad metric

- **Match rate is trivially gameable.** Match everything to anything and you hit 100%.
- Two failure types with wildly different costs:
  - **Missing a match** — annoying. A human spends 30 seconds on it.
  - **Making a wrong match** — expensive. The books are wrong and nobody knows.
- So we measure them **separately.**

### 8.3 The metrics that actually matter

| Metric | Meaning | Target |
|---|---|---|
| **Auto-approval precision** | Of everything auto-approved, what % was actually right | **100% — non-negotiable** |
| **False auto-approvals** | Count of wrong auto-approvals | **0** |
| **Straight-through rate** | % handled with no human at all | 65–80% |
| **Exception precision** | Of things sent to humans, what % genuinely needed a human | > 70% |
| **Missed exceptions** | Should have been flagged, weren't | 0 |
| **Cost per 100 records** | LLM spend | Track it |
| **P95 latency per record** | Speed | Track it |

- **The headline claim we want:** *"Zero false auto-approvals across 85 held-out records,
  78.8% straight-through by count and 91.2% by value, at Rs 167 per thousand records."*
- Count, value, safety and cost in one sentence. Each of those is a phrase from the brief.
- That is a far stronger statement than *"84% match rate."*

### 8.4 Per-scenario breakdown

Aggregate numbers hide everything. This table is the most useful screen in the
whole project — it tells us exactly where to spend our remaining hours.

```
Exact reference match       18/18  100%   ok
Alias name match             7/8    88%   ok
TDS deduction                6/6   100%   ok
Gateway fee deduction        5/5   100%   ok
Partial payment              3/5    60%   <- weak
Combined payment             2/4    50%   <- weak
Duplicate detection          4/4   100%   ok
Ambiguous (correctly held)   4/4   100%   ok
Genuine no-match             5/5   100%   ok
```

### 8.5 Testing the LLM part separately

The adjudicator needs its own tests, because it's the non-deterministic piece.

- **Accuracy.** Run only the adjudicator on the ~15 ambiguous cases. Compare to truth.
- **Self-consistency.** Run the **same input 3 times at `temperature=0.7`**, not at 0.
  At temperature 0 the answers are near-identical by construction and the test proves nothing.
  Raising it asks the real question: is the model stable because the *prompt* is good, or only
  because we pinned the temperature? **Report this number.** Almost nobody measures it.
- **Bypass the result cache for this test** (see §15.4), or it passes trivially every time.
- **Refusal quality.** Feed it a case where the right answer is *"I can't tell."*
  Does it correctly abstain, or invent confidence?

### 8.6 A frozen regression set

- Pick 20 tricky cases. Freeze them in a file.
- Run after every change. If a previously-passing case breaks, the last edit did it.
- This is just unit testing. You already do this — the only difference is the "unit"
  is a whole pipeline decision.

### 8.7 One command

- `python eval.py` prints every table above.
- **If running evals takes more than one command, we'll stop doing it by hour six.**

---

## 9. The synthetic dataset

Don't generate 85 random records. Design the mix so **every branch gets exercised.**

| Scenario | Count | What it tests |
|---|---|---|
| Exact reference in payment note | 18 | Fast path |
| Clean name + exact amount | 10 | Basic scoring |
| Company alias variations | 8 | Alias memory |
| TDS deducted (2% / 10%) | 6 | Settlement maths |
| Gateway fee + GST deducted | 5 | Settlement maths |
| Partial payment | 5 | One bill, many payments |
| Combined payment | 4 | Many bills, one payment |
| Batched settlement (one deposit, 6 payments) | 6 | Splitting a batch back into its parts |
| Duplicate transaction | 4 | Duplicate guardrail |
| Unexplained short payment | 4 | A true exception |
| No payment at all | 5 | A true exception |
| Two identical invoices, one payment | 4 | Margin rule / ambiguity |
| Payment far outside date window | 3 | Date guardrail |
| Above value ceiling, perfect match | 3 | Value ceiling guardrail |
| **Graded set total** | **85** | |

- Seed the random number generator with a fixed value so runs are reproducible.
- Emit `records.json` and `ground_truth.json` from the same function.

**The ceiling this mix implies.**
Of the 85 graded records, 62 expect `AUTO`, 12 expect `EXCEPTION`, 7 expect `REVIEW` and 4 expect `AMBIGUOUS`.
Straight-through processing counts only the `AUTO` records, so **72.9% is the highest STP any correct
system can reach on this data.**
Anything above that is a false approval, not an improvement.
The illustrative 78.8% in §19.1 is a table-format example, not a target.

### Three sets, not one

Generating a single batch and using it for everything is training on your test set. Generate
**160 records** and split them:

| Set | Size | Purpose |
|---|---|---|
| **Alias seed set** | 30 | Populates the alias table *before* the graded run. Never scored. |
| **Tuning set** | 45 | Grid-searching the weights (§6.9) and threshold tuning. |
| **Held-out set** | 85 | The only numbers we report. Touched once, at the end. |

- **Why the alias seed set exists:** confirmed matches write new alias rows. If those aliases
  then help match later records *in the same graded run*, the reported accuracy is inflated by
  information the system would not have had. Freeze the alias table before the graded run.
- *"Weights tuned on a held-out split"* is a line that lands with technical judges, and it
  costs one extra call to the generator.

---

## 10. Database schema

```sql
invoices(
  id, invoice_no, counterparty_name, counterparty_name_clean,
  amount_paise, currency, invoice_date, due_date, status
)

transactions(
  id, txn_ref, description_raw, counterparty_name_clean,
  amount_paise, currency, value_date, source, utr
)

settlements(
  id, txn_id, gross_paise, fee_paise, gst_on_fee_paise,
  tds_paise, net_paise, formula_used
)

matches(
  id, transaction_id, score, margin, decision, decided_by, created_at
)

match_allocations(
  id, match_id, invoice_id, allocated_paise
)
-- The real invariant, enforced in the assignment pass or by a trigger:
--   SUM(allocated_paise) per transaction <= transactions.amount_paise

exceptions(
  id, invoice_id, reason_code, reason_text, evidence_json
)

aliases(
  id, canonical_name, variant_name, confirmed_count
)

episodes(
  id, situation_text, resolution_text, tags
)

audit_log(
  id, record_id, stage, outcome, rules_passed_json,
  rules_failed_json, llm_used, prompt, raw_response, ts
)
```

- **Money is always stored as integer paise.** Never floats.
- **Why allocations instead of a simple `transaction_id UNIQUE`.** A plain unique constraint
  stops double-counting, but it also makes combined payments impossible — one transaction
  legitimately settles three invoices, and the second insert would throw. Allocations model
  both shapes correctly:
  - **Partial payment** = several allocations pointing at the same invoice
  - **Combined payment** = several allocations under one transaction
- The protection we actually want is the **sum invariant**: allocations against a transaction
  can never exceed what the transaction was worth. That is the real safety net.

---

## 11. Build order, phase by phase

**Build strictly in this order.** Each phase ends with something demoable. If we
run out of time, we stop where we are and still have a working system.

### Phase 0 — The box everything runs in
0. Write `docker-compose.yml` with `db`, `api` and `web`, plus the two Dockerfiles and `.env.example` (§13.1).
   Confirm `docker compose up` gives a reachable Postgres and a FastAPI health endpoint.
   **Do this first.** Every command below assumes it works.

### Phase 1 — Data and truth
1. Write the generator producing the table in §9, plus the answer key.
2. Load everything into Postgres using the schema in §10.
3. Add the sum invariant on `match_allocations` as a database trigger (§10).
   **Not** a `UNIQUE` constraint on `matches.transaction_id` — that is bug 2 in §18, and it makes
   combined payments impossible.
4. **Do not touch an LLM yet.**

### Phase 2 — Deterministic core
5. Intake / normaliser.
6. Fast path — exact reference match.
7. Blocking.
8. Scorer with the four weighted signals — **including the renormalisation rule in §6.2.**
9. Settlement maths, running **inside** the scorer.
10. Grid-search the weights against ground truth under a zero-false-approval constraint (§6.9).
11. **Run the eval now.** Expect 55–65% with **zero LLM involved.** That's the
    baseline, and it is a genuinely respectable result.

### Phase 3 — Guardrails
12. Global assignment pass.
13. The full guardrail table from §7.
14. Three terminal states, all writing to the audit log.
15. **Run the eval again.** Straight-through rate will **drop**. False approvals go
    to **zero**. That trade is the entire point — say so out loud in the demo.

### Phase 4 — LLM, narrowly
16. Adjudicator, firing only in the 0.70–0.90 score band or when margin < 0.15.
17. Output validation: schema check + the chosen ID must exist in what we sent.
18. Settlement explainer: turns already-computed numbers into an English sentence.
    **Explanation only. It computes nothing.**
19. **Run the eval again.** Measure exactly what the LLM bought us. If it's under
    5 points, **say so honestly** — that's a more interesting finding than a fake
    big number, and it shows we measured rather than assumed.

### Phase 5 — Memory
20. `aliases` table, populated from confirmed matches.
21. `episodes` table, retrieved into the adjudicator prompt.
22. Vector store **only** if time remains *and* we can point to a case SQL missed.

### Phase 6 — Show it
23. Eval harness printing every table from §8. **Command line only. This must work before
    any frontend work starts.**
24. FastAPI layer — the six endpoints in §16.4.
25. React UI — exception list first, then decision trace, then run summary, then cash
    position. Details in §16.
26. Static JSON fallback so the demo survives a dead backend.
27. **Every exception must show a reason code and its evidence.**
    - Good: `AMOUNT_GAP_UNEXPLAINED: ₹340 remaining after TDS and fee applied`
    - Useless: `low confidence`

### Phase 7 — Winning margin (only once Phases 1–6 work)
28. Ablation table (§19.1) — 30 min, mostly bookkeeping.
29. Money-weighted metrics (§19.2) — one SQL query.
30. Throughput and cost numbers (§19.5) — 20 min.
31. Reason-code accuracy (§19.7) — 30 min.
32. Live learning loop (§19.3) — 1 hr.
33. Threshold curve slider (§19.6) — 1 hr.
34. Settlement Q&A (§19.4) — 1.5 hr. **Cut this first if time is short.**

---

## 12. What we are deliberately NOT building

The main risk on this project is overbuilding. Concretely:

| Cut | Why |
|---|---|
| **Cash forecasting agent** | A predictive agent doesn't share the reconciliation pipeline and adds nothing to the accuracy story. **But see the note below** — the track subtitle does say "and the cash position." |
| **Tax verification as a separate agent** | TDS and GST checking already lives inside the settlement maths. Making it a separate agent adds a box to the diagram and zero capability. |
| **LLM orchestrator** | Three fixed branches. `if/else` is correct. |
| **Vector database** | Only if Phase 5 finishes early and we can justify it with a real case. |
| **Auto write-offs** | Never automate giving up on money. |

**Keeping:** reconciliation, settlement explanation, guardrails, memory, evals.

### The cash position — one panel, not an agent

The track subtitle is *"run the books and the cash position."* We should answer that, but
it does not need a forecasting agent. Once reconciliation has run, the cash position falls
out of data we already have:

- **Confirmed in** — total of all auto-approved matches.
- **Still owed** — total of open invoices, split by aging bucket (0–30, 31–60, 61–90, 90+).
- **In flight** — settlements sent by the gateway but not yet landed in the bank.
- **Uncertain** — total value sitting in the exception queue. *This is the interesting
  number:* it is money we cannot currently account for, and no spreadsheet shows it.

Four SQL queries and one dashboard panel. No new agent, no prediction, no extra risk.
It closes the second half of the track title for roughly an hour of work.

- Four working things beat nine boxes on a slide.
- **Build the eval harness before anything clever** — it's what proves the other
  four actually work.

---

## 13. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python | Best fuzzy-matching and LLM libraries |
| Database | PostgreSQL | Structured financial data. You already know it. |
| Fuzzy matching | `rapidfuzz` | Fast, simple, no ML needed |
| Data generation | `faker` + fixed seed | Reproducible |
| LLM | `gpt-4o-mini` through OpenRouter | Reliable structured JSON output, cheap enough that one call per batch rounds to nothing, and one gateway key instead of an account per provider |
| Schema validation | `pydantic` | Enforces the LLM output contract |
| Dashboard | **React + Vite + TypeScript** | Cleaner demo surface than Streamlit. See §16. |
| UI components | Tailwind + shadcn/ui | Professional tables and badges with no CSS files |
| Charts | Recharts | Aging buckets only |
| API layer | FastAPI | Six endpoints, reusing the pipeline's own pydantic models |
| Orchestration | **Plain Python functions** | No framework. See §14 for the full reasoning. |
| Retries | `tenacity` | A decorator, not a framework |
| Observability | Our own `audit_log` table | Same data LangSmith gives you, in SQL, no external service |
| Packaging | **Docker + Docker Compose** | One command brings up the whole system. See §13.1. |

### 13.1 Everything runs in Docker

**Rule: nothing runs on the host machine.**
Postgres, the Python pipeline, the FastAPI layer and the React app all run in containers.

**Why this matters more than usual here:**

- **The demo cannot break on someone else's laptop.**
  A hackathon demo that needs a local Postgres install and the right Python version is a demo that fails at the worst moment.
- **Reproducibility is part of the pitch.**
  We claim fixed seeds and repeatable numbers.
  That claim is weak if the environment itself is not pinned.
- **Judges can run it.**
  `git clone` then `docker compose up` is a much stronger handoff than a page of setup steps.

**The services:**

| Service | What it is | Port |
|---|---|---|
| `db` | Postgres 16, with a named volume so data survives restarts | 5432 |
| `api` | FastAPI plus the whole pipeline, the six endpoints from §16.4 | 8000 |
| `web` | React and Vite, dev server in development, static build in the demo image | 5173 |

**Rules we follow:**

- **One `docker-compose.yml` at the repo root.**
  No second compose file unless a real need shows up.
- **The API waits for the database.**
  Use a healthcheck on `db` and `depends_on: condition: service_healthy`.
  Without this the API starts first, fails to connect, and looks like a bug in our code.
- **Secrets come from a `.env` file** that is never committed.
  Ship a `.env.example` with every key present and no real values.
- **Multi-stage builds for the Python image.**
  Build dependencies in one stage, copy only what runs into the final stage.
- **Source is bind-mounted in development** so we do not rebuild the image on every edit.
- **The batch run and the eval are compose commands**, not host commands:

```bash
docker compose up -d                          # bring everything up
docker compose exec api python run_batch.py   # run the batch
docker compose exec api python eval.py        # print every eval table
```

- **The `db` volume must be resettable in one command**, because we reseed the fixed dataset often:

```bash
docker compose down -v && docker compose up -d
```

- **Do not dockerise the LLM calls behind a proxy container.**
  The API container calls the Anthropic API directly.
  An extra hop adds a failure mode and buys nothing.

---

## 14. How we actually build the agents

### 14.1 What the framework options are

| Tool | What it really is | Analogy |
|---|---|---|
| **LangChain** | A library of wrappers: prompt templates, LLM clients, output parsers, chains piping them together. | An ORM. Convenient until you need to see the SQL it generated. |
| **LangGraph** | A state machine framework. Nodes are steps, edges are transitions; it runs the graph and can pause, resume and checkpoint. | A workflow engine like Temporal or Airflow, sized for LLM steps. |
| **LangSmith** | Hosted observability and evals. Traces every LLM call, inspects prompts, runs test datasets. | Datadog, but for LLM calls. |

These are not toys. LangGraph in particular is good software.

### 14.2 We are not building ReAct agents

**ReAct** (Reason + Act) is a loop: the model thinks, picks a tool, sees the result, thinks
again, repeats until it decides it is done. It suits open-ended tasks where you cannot know
in advance what information will be needed.

Our adjudicator is **single-shot structured classification.** One call. Everything it needs
is pre-fetched and handed to it. It picks from a closed list and returns JSON.

Why not ReAct here:

- ReAct means the model **chooses what to query.** It could pull in a transaction our blocking
  stage deliberately excluded, and then the candidate set is no longer what the guardrails
  assumed.
- Unbounded turns mean unbounded cost and latency. Across 85 records that is real money.
- Every extra turn is another opportunity to drift. A bounded blast radius is the whole argument.
- Auditing a six-turn trace is far harder than auditing one input and one output.

**The general rule:** ReAct when the search space is unknown. Single-shot when the options can
be pre-computed. Ours can — that is exactly what blocking and scoring are for.

### 14.3 The decision rule

**Does the agent decide its own path at runtime?**

- **Yes** → e.g. a research agent looping *"search, read, decide if I need more, search again"*
  for an unknown number of turns with dynamic tool selection. **Use LangGraph.** Writing your
  own state machine with checkpointing is real work.
- **No** → our pipeline. Fixed stages, one branch point, no loops, no tool selection,
  one LLM call per record.

Ours is a **DAG that never changes shape.** Expressing it in LangGraph's node/edge syntax
produces the same DAG plus a dependency. The framework's headline feature — dynamic routing
— is the exact thing we deliberately don't want.

### 14.4 Three concrete costs during a hackathon

1. **Debugging goes through someone else's abstraction.** When the prompt is wrong at hour 20,
   you want `print(prompt)`. In a framework you're working out which of four layers built it.
2. **Breaking changes.** These libraries move fast. Answers from eight months ago often
   no longer apply.
3. **It undercuts our own story.** The pitch is *"we control exactly what the AI is allowed
   to do."* That's hard to say convincingly when the control flow lives in a library.

**On LangSmith specifically** — the closest call, because observability is a genuine need.
But our `audit_log` already stores every prompt, every raw response and every rule outcome.
Same data, in Postgres, queryable with SQL we already know, no external account. It also
doubles as the compliance artifact, which LangSmith is not.

### 14.5 What an agent actually looks like here

```python
def adjudicate(invoice, candidates, aliases, episodes):
    """The only place an LLM makes a decision."""

    prompt = build_prompt(invoice, candidates, aliases, episodes)

    raw = call_claude(
        system=SYSTEM_RULES,      # cached prefix, see 15.4
        user=prompt,
        temperature=0,
    )

    # Guardrail 1: must be valid JSON in our schema
    try:
        result = AdjudicationResult.model_validate_json(raw)
    except ValidationError:
        return reject("SCHEMA_INVALID", raw)

    # Guardrail 2: must pick an ID we actually sent it
    valid_ids = {c.id for c in candidates}
    if result.chosen_id and result.chosen_id not in valid_ids:
        return reject("HALLUCINATED_ID", raw)

    audit.log(invoice.id, prompt, raw, result)
    return result
```

- That is an agent: assemble context, call a model, validate the output, log everything.
- **No framework anywhere in it.**

### 14.6 The pipeline that runs it

```python
def process_batch(invoices, transactions):
    proposals = []

    for inv in invoices:
        if not passes_input_guardrails(inv):
            record(inv, EXCEPTION, "MALFORMED_INPUT")
            continue

        if ref := find_exact_reference(inv, transactions):
            proposals.append(Proposal(inv, ref, score=1.0, by="fast_path"))
            continue

        candidates = block(inv, transactions)
        candidates = apply_settlement_math(inv, candidates)
        scored = score_all(inv, candidates)

        if is_ambiguous(scored):
            result = adjudicate(inv, scored[:3], aliases, episodes)
            proposals.append(Proposal.from_llm(inv, result))
        else:
            proposals.append(Proposal(inv, scored[0], by="scorer"))

    # Whole-batch conflict resolution, then the hard rules
    assignments = resolve_conflicts(proposals)
    for a in assignments:
        decision = apply_guardrails(a)      # AUTO / REVIEW / EXCEPTION
        audit.log_decision(a, decision)
```

- Read it top to bottom and you know exactly what the system does.
- **That readability is the deliverable**, not a side effect of skipping the framework.

### 14.7 What frameworks give you, and how we get it instead

| Need | Framework way | Our way | Cost |
|---|---|---|---|
| Structured output | LangChain output parsers | `pydantic` + `model_validate_json` | 5 lines |
| Retries | Built in | `tenacity` decorator | 3 lines |
| Tracing | LangSmith | `audit_log` table | already in the schema |
| Prompt templates | `PromptTemplate` | Python f-strings | 0 lines |

About 30 lines of our own code, all of it inspectable.

### 14.8 The honest caveat

If we were building a research agent that loops 40 turns with dynamic tool selection,
**LangGraph would be the right answer.** Our pipeline just isn't that shape. If the judges
ask why no framework, that's the answer: *the framework's value is dynamic control flow, and
fixed control flow is our entire safety argument.*

---

## 15. Caching, RAG, and what we deliberately skipped

Keep this section — "why didn't you use X" is a question judges ask.

### 15.1 We are already doing RAG

- **RAG** means *fetch relevant context, then call the LLM.* That's all.
- It does **not** have to mean a vector database. That's just one retrieval method.
- Our adjudicator already receives the invoice, the top 3 candidates, matching aliases, and
  similar past cases. **That is retrieval-augmented generation.** We retrieve with SQL.

### 15.2 Why SQL beats vector search here

Our retrieval questions all have exact right answers:

- *Transactions within ±25% of ₹10,000?* → a numeric range
- *Transactions within 45 days?* → a date range
- *Is `ABCTECHPVTLTD` a known alias?* → an exact string lookup

- A B-tree index answers these in a millisecond, **exactly correct, every time.**
- Embeddings would turn them into approximate similarity. On money, "approximately the
  right amount" is a bug. ₹10,000 and ₹1,00,000 sit close together in embedding space and
  are catastrophically different in reality.
- **Rule of thumb:** vector search for fuzzy meaning in prose. Exact indexes for numbers,
  dates and IDs. Ours is the second kind.

### 15.3 The one place a vector store would earn its keep

- The `episodes` table. Content is prose, and the query — *"find past cases that feel like
  this one"* — has no exact key.
- **But start with SQL tags** (`tag = 'combined_payment'`). Ship that, see whether tags miss
  real cases, and only then upgrade to embeddings. This is why Phase 5 marks it optional.

### 15.4 Caching — four kinds, all worth adding

| Cache | What it does | When |
|---|---|---|
| **Prompt caching** | The provider can cache a stable prompt prefix, and ours is byte-identical on every call. **Measured: it never engages here** — caching needs a prompt over 1024 tokens and ours is around 670. Right shape, no effect at this size. | Phase 4 |
| **Adjudicator result cache** | Key on a hash of (invoice fields + candidate IDs + scores). Same input → stored answer, no API call. Essential because we re-run the batch many times while tuning thresholds. | Phase 4 |
| **Normalisation cache** | `functools.lru_cache` on `clean_name()`. One line. | Phase 2 |
| **Alias / episode cache** | Both tables are small and rarely change. Load into a dict once per batch instead of 85 DB round trips. | Phase 5 |

- **Critical exception:** the self-consistency test in §8.5 **must bypass the result cache.**
  It exists to check whether the model gives the same answer three times. Serving it from
  cache would make it always pass and always be meaningless.

### 15.5 Deliberately skipped

| Thing | Why not |
|---|---|
| **Fine-tuning** | Needs thousands of labelled examples we don't have, and bakes decisions into weights you can't audit. Finance needs "why". A fine-tuned model can't tell you. |
| **Agent frameworks** (LangChain, CrewAI) | They hide control flow, and control flow *is* the product here. Debugging someone else's abstraction at hour 20 is a bad place to be. |
| **Multi-agent chat** | Non-determinism multiplied by non-determinism. Our stages are pipeline steps with one LLM call each. That's deliberate. |
| **Graph database** | A Postgres foreign key does what we need. |
| **Streaming responses** | We need complete, validated JSON before acting. Nothing to stream to. |

### 15.6 The principle behind all of it

- The brief's premise is that **verification capacity is the bottleneck.**
- Every technology in §15.5 is a *generation* or *retrieval* technology. None of them makes
  verification better.
- Prompt caching earns its place because it is pure cost reduction with zero added risk.
- A vector database for structured financial data would add a dependency, a failure mode,
  and approximate answers — to solve a problem `WHERE amount BETWEEN` already solves exactly.

---

## 16. The React UI

### 16.1 The rule that protects the project

**The pipeline and the eval harness must run end to end from the command line before
anyone opens the frontend.** The UI *reads* results. It must never be the thing that
produces them. If the React app is broken at hour 20, `python run_batch.py` and
`python eval.py` still prove the system works.

### 16.2 What React costs us versus Streamlit

Streamlit was the original pick because it's Python calling Python. React adds:

- A real API layer (FastAPI, six endpoints)
- CORS configuration — small, but it will eat 20 minutes at the worst moment
- Two processes running during the demo instead of one
- A build step

Realistically **2–4 hours that isn't reconciliation logic.** Worth it if Phases 1–5 are
done and someone on the team is comfortable in React. Not worth it otherwise.

### 16.3 Stack

| Piece | Choice | Why |
|---|---|---|
| Build | Vite + React + TypeScript | Instant dev server, no config |
| Styling | Tailwind | No time for CSS files |
| Components | shadcn/ui | Copy the code in, own it, no dependency weight |
| Data fetching | TanStack Query | Skip it entirely if reading static JSON |
| Charts | Recharts | Aging buckets only |

- **No Redux, no Zustand, no router beyond simple tabs.** Four screens needs no state library.

### 16.4 API surface

```
GET  /api/runs/latest      -> summary metrics
GET  /api/exceptions       -> the exception list
GET  /api/records/{id}     -> full decision trace for one record
GET  /api/cash-position    -> the four numbers
GET  /api/eval             -> per-scenario breakdown
POST /api/runs             -> trigger a batch run
```

- FastAPI, reusing the pydantic models the pipeline already defines. The response models
  are largely the same objects.

### 16.5 Screens, in build order

**1. Exception list — build first.** The demo opens here. Table of record ID, amount,
counterparty, reason code, confidence. Reason codes as coloured badges. Row click opens
screen 2.

**2. Decision trace — the screen that wins.** Nobody else will have this, and it is the
direct answer to *"verification capacity is the bottleneck."* For one record, show the
whole chain:

- Invoice and candidate transactions side by side
- **Score breakdown** — reference 0.0, amount 0.95, name 0.88, date 1.0 → total 0.71,
  as a small bar per component
- **Settlement math** — `₹10,000 − ₹200 MDR − ₹36 GST = ₹9,764 ✓ explains the gap`
- **Every guardrail with a tick or cross** — score ✓, margin ✗ (0.04, needs 0.15),
  amount ✓, date ✓
- **The LLM's raw reasoning** if it was involved, clearly labelled as a *recommendation*,
  not a decision
- **The final outcome** and which rule produced it

A judge reading that screen understands the entire thesis without us explaining it.

**3. Run summary.** Headline numbers, per-scenario breakdown table, straight-through rate.

**4. Cash position.** The four numbers from §12 plus an aging bar chart. Fastest screen
to build.

**5. Ablation and threshold panel (Phase 7).** The table from §19.1 plus the threshold slider
from §19.6. Both read precomputed JSON — no live re-running. This is where the money-weighted
metrics and throughput numbers live too.

### 16.6 Two things that save the demo

- **Ship a static JSON fallback.** After a successful run, write `results.json` into the
  frontend's `public/` folder. Add a flag so the UI reads that file instead of the API.
  If the backend dies five minutes before presenting, flip the flag and demo from static
  data. Fifteen minutes of work.
- **Seed the demo data once and freeze it.** Same fixed random seed, same 85 records,
  every run. Never demo on freshly generated data.

### 16.7 Do not build

Auth, live-updating anything, a dark mode toggle, animations, mobile responsive layouts,
pagination for 85 rows, or a settings page.

---

## 17. Demo script

Five minutes, in this order:

The brief warns that *"one cherry-picked match proves nothing."* So we do the opposite of
the usual demo — **we lead with what we couldn't solve.**

1. **The problem.** Show one invoice for ₹10,000 and one deposit for ₹9,564.
   *"A human has to work out whether these are the same event. We have 85 of these."*
2. **Run the batch live.** 85 records processed in front of them.
3. **Open the exception list first.** Every unresolved case, each with a reason code and
   its evidence. *"Here's what we couldn't do, and exactly why."*
4. **Then the headline.** Straight-through rate, and **zero false auto-approvals.**
5. **Show the per-scenario table.** *"Here's where we're strong and where we're weak."*
   Honesty reads as competence.
6. **Show a guardrail firing.** The ₹5,00,000 case with a perfect score that we still sent
   to a human. *"The model was confident. The rules overruled it. That's deliberate."*
7. **Show the cash position panel.** Especially the value sitting in exceptions —
   money the business currently cannot account for.
8. **Show the ablation table.** *"Deterministic alone got 58.8%. The LLM took it to 71.8%.
   Memory took it to 78.8% and made it cheaper. Here's the measurement."*
9. **Run the learning loop live.** Confirm one exception, re-run, watch three records resolve
   that didn't before.
10. **Name one case we got wrong** and what we'd change (§19.8).
11. **Close on the eval loop.** *"We can change a threshold and re-measure in one command.
    That's the difference between a demo and a system."*

### The line to land

> *"We didn't build an AI that decides where money goes. We built one that
> investigates and recommends, and a set of rules that decides what it's actually
> allowed to do."*

---

## 18. Pre-build bug sweep

Thirteen bugs found reviewing this plan before implementation, plus one found by building it.
All are fixed above; this is the record of what they were, because the same mistakes are easy to
reintroduce while coding.

### Critical — would have broken scenarios in our own dataset

**1. Blocking excluded the showcase cases.**
A single ±25% amount window filters out combined payments (3x the invoice) and partials
(0.6x). Nine records could never find their match — the right candidate was removed one
step before scoring. *Fixed: three blocking passes, each tagging its candidates (§5, box 4).*

**2. `transaction_id UNIQUE` made combined payments impossible.**
The constraint stops double-counting, but one transaction legitimately settles three
invoices, so the second insert throws. The constraint and the scenario directly contradicted
each other. *Fixed: `match_allocations` table with a sum invariant (§10).*

**3. Fast path had no margin, but the guardrail required one.**
Fast-path proposals skip the scorer, so `margin` was null while the guardrail compared it
against 0.15. Depending on how the comparison is written, every fast-path record silently
passes or silently fails. *Fixed: sole-candidate rule, `margin = 1.0` explicitly (§7).*

### Logic

**4. Two rules claimed the same record.**
Score 0.95 with margin 0.03 satisfied both "auto-approve above 0.90" and "adjudicate below
0.15 margin". *Fixed: one `route()` function, margin checked first (§7).*

**5. Inconsistent date anchor.**
`score_date()` measured from `due_date`; the guardrail table said `invoice_date`. Roughly 30
days apart, so records passed one check and failed the other arbitrarily. *Fixed: `due_date`
everywhere.*

**6. A wrong worked example.**
The combined-payment example scored 0.500 and was described as routing to the LLM, but 0.500
is below the 0.70 adjudicator floor — it falls to EXCEPTION. *Fixed: the example now states
this, and depends on the blocking tag from bug 1 (§6.8).*

### Eval integrity

**7. Aliases learned from the batch being graded.**
Confirmed matches write alias rows, which then helped match later records in the same run —
inflating accuracy with information the system would not have had. *Fixed: a 30-record alias
seed set, frozen before the graded run (§9).*

**8. Tuning and reporting on the same records.**
Training on the test set. The grid search would fit those specific 85 records and the
reported number would be optimistic. *Fixed: 160 records split 30/45/85 (§9).*

**9. Self-consistency tested at temperature 0.**
At temperature 0 the answers are near-identical by construction, so the test proved nothing.
*Fixed: run the consistency check at 0.7, and bypass the result cache (§8.5).*

**10. The build order still asked for the constraint that bug 2 removed.**
Phase 1 step 3 said *"add the `UNIQUE` constraint on `matches.transaction_id`"* while §10 and bug 2
above had already replaced it with `match_allocations` and a sum invariant.
Building Phase 1 to the letter would have reintroduced bug 2 on day one.
*Fixed: Phase 1 step 3 now asks for the sum invariant trigger (§11).*

**11. The three sets did not add up.**
§9 said *"generate 130 records and split them"* into sets of 30, 45 and 85.
Those sum to 160.
130 is what you get by counting only the tuning and held-out sets and forgetting the alias seed set,
which is the one the whole split exists to create.
*Fixed: 160 everywhere (§9, §20).*

**12. The combined-payment window had an arbitrary ceiling.**
§5 capped the combined blocking pass at 5.0x the invoice.
Combined payments are routinely lopsided: one payment settling a ₹57,600 bill and a ₹3,71,750 bill
is 7.45x the smaller one, which fell outside the window, so that invoice never saw its own payment.
Measured on the held-out set, blocking recall was 87 of 89.
*Fixed: the ceiling is now the sum of that counterparty's open invoices, which is a real limit rather
than a guessed multiple. Recall is 89 of 89 with no increase in candidates per invoice (§5).*

**13. Margin was credited with catching a case it cannot see.**
§6.7 named the two-identical-invoices scenario as the thing margin exists for.
Ranking is per invoice over candidate transactions, so margin only ever compares payments;
two identical invoices each score a wide margin, measured at 0.79 on the held-out set.
Reading §6.7 alone, global assignment looks redundant and could be cut - and cutting it is how a
double-counted payment gets auto-approved.
*Fixed: §6.7 now names the duplicate-transaction case, which margin does catch at 0.00, and states
that the invoice-side mirror is global assignment's job.*

### The pattern worth noticing

Six of the nine were **contradictions between two parts of the plan** that were each fine on
their own — a constraint that fought a scenario, two rules claiming one record, two different
date anchors. That is the normal failure mode for a system like this, and it is why the eval
harness comes before the clever parts. Contradictions surface as unexplained accuracy dips,
and without per-scenario numbers you cannot tell a contradiction from a hard case.

---

**14. The score threshold was credited with safety it does not provide.**
Section 19.6 predicted the curve would show two wrong approvals at a 0.80 bar and zero at 0.90,
making 0.90 "where false approvals reach zero".
Measured on the held-out set, there is not one wrong approval at any bar between 0.30 and 0.98.
The score bar is not the safety mechanism at all.

Running the same sweep with the nine hard rules switched off shows what is: score-only auto-approves
82.4% at a 0.90 bar and gets **11 of them wrong**, rising to 18 wrong as the bar drops.
The rules cost about thirteen points of automation and take wrong approvals from eleven to zero.

The slider was going to be a flat line labelled as a trade-off.
It now draws both curves, and the gap between them is the actual finding.


## 19. Winning margin — seven additions

Everything in this section is cheap relative to its impact, and every item ties back to a
phrase in the track brief. Build them **after** Phases 1-6 are working, in this order.

### 19.1 The ablation table (~30 min)

We already run the eval at the end of Phases 2, 3 and 4. Keep those numbers in one table:

```
Configuration                          STP     False approvals   Cost
Deterministic only                    58.8%          0           Rs 0
+ guardrails                          52.9%          0           Rs 0
+ LLM adjudicator                     71.8%          0        Rs 14.20
+ memory (aliases, episodes)          78.8%          0        Rs 11.40
```

- Answers the question judges actually have — *did the AI do anything, or is it decoration?* —
  with a measurement rather than an assertion.
- Note memory both raises accuracy **and lowers cost**, because fewer records reach the LLM.
  That is a real finding, and worth saying out loud.
- **Pure bookkeeping on runs we are doing anyway.**

### 19.2 Money-weighted metrics (~1 SQL query)

Counting records treats a Rs 500 invoice the same as a Rs 5,00,000 one. Finance people do not.

```
Match rate by count:   78.8%
Match rate by value:   91.2%
Value in exceptions:   Rs 4,21,300   <- money we cannot currently account for
```

- That last line is what a real controller cares about, and no other team will show it.

### 19.3 The learning loop, demonstrated live (~1 hr)

The plan has memory but never *shows* it working. Make it a 30-second demo moment:

1. Open an exception: `ABC TECHNOLOGIES` vs `ABCTECH IND PVT`, no alias, score 0.68
2. Reviewer clicks **Confirm match** in the UI
3. That writes an alias row
4. Re-run — the same pattern now auto-resolves, **and two other records resolve too**

The system visibly improves while they watch. One button, one insert, one re-run.

### 19.4 Settlement Q&A (~1.5 hr) — closes a listed direction

The brief names "Settlement Q&A agent" explicitly, and we already hold every number. A query
box answering from the audit log:

> *"Why did we receive Rs 9,764 for INV-023?"*
>
> Gross Rs 10,000 − MDR Rs 200 − GST on MDR Rs 36 = Rs 9,764. Matched to TXN-1180, settled
> T+2 on 14 Aug, auto-approved at score 0.97.

- Fixed query patterns, LLM writes the sentence, **every number comes from the database.**
- Same discipline as the settlement explainer: the model narrates, it never calculates.

### 19.5 Throughput numbers (~20 min)

"Throughput" is literally in the bar. Report it:

```
85 records in 11.3s        (7.5 records/sec)
LLM calls: 19 of 85        (22%)
Cost: Rs 14.20 per 85      (Rs 167 per 1,000 records)
Manual equivalent: ~4 hours at 3 min/record
```

### 19.6 The threshold curve (~1 hr)

Pre-run the batch at five thresholds, store the results, put a slider in the UI:

```
Threshold 0.80  ->  STP 84.7%  |  2 false approvals  X
Threshold 0.90  ->  STP 78.8%  |  0 false approvals  OK
Threshold 0.95  ->  STP 61.2%  |  0 false approvals
```

- Makes the safety/throughput trade-off physical instead of theoretical.
- Also proves 0.90 was not chosen by feel — it is where false approvals reach zero.

### 19.7 Reason-code accuracy (~30 min)

Everyone measures *did we reach the right verdict.* Almost nobody measures *did we give the
right reason.*

```
Verdict accuracy:      94.1%
Reason-code accuracy:  88.2%
```

- Reason codes are what a human acts on. A wrong reason wastes their time even when the
  verdict was right.
- Ground truth already stores the reason, so this is a comparison we can already make.

### 19.8 The free one: show a case we got wrong

One slide near the end. A record the system misjudged, why it happened, and what we would
change. Judges watch polished demos all day. A team that names its own failure mode reads as
markedly more credible than one claiming everything worked.

---

## 20. Alignment with the track brief

Keep this table. It is the answer to "how does this meet the brief?"

| The brief says | Where we answer it |
|---|---|
| "closes one finance-ops loop" | Invoice → decision → allocation → audit log |
| "50+ record batch of synthetic data" | 85 graded, 160 generated (§9) |
| "reporting its match rate" | By count **and by value** (§19.2) |
| "exceptions it could not resolve" | Reason code plus evidence for every one (§11) |
| "verification capacity, not generation speed, is the bottleneck" | The guardrail layer (§7) and eval harness (§8) are the centre of the design |
| "throughput" | Records/sec and cost per 1,000 (§19.5) |
| "measured accuracy" | Held-out set, zero-false-approval constraint (§9, §6.9) |
| "an honest exception list" | The demo opens on it (§17) |
| "one cherry-picked match proves nothing" | Per-scenario breakdown (§8.4) and the ablation table (§19.1) |
| "run the books **and the cash position**" | Cash position panel (§12) |
| "Multi-source reconciliation" | The core system |
| "Settlement Q&A agent" | §19.4 |
| "Forward cash forecaster" | Deliberately scoped down to a position readout, not a forecast (§12) |
| "Tax-line matcher" | TDS and GST handled inside the settlement maths (§6.4) |
