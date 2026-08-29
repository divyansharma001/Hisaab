# Hisaab

**An AI finance controller that matches invoices to bank payments, and is honest about the ones it cannot solve.**

> Zero wrong auto-approvals, most records handled without a human, and every unresolved case explained.

Built for the Razorpay AI Buildathon, Track 04 — AI Finance Controller.

---

## The problem

A company sends out bills.
Money arrives in its bank account.
Somebody has to say: *this ₹9,764 that arrived on Tuesday pays off that ₹10,000 bill from last month.*

That is hard to automate for four reasons:

| Problem | Example |
|---|---|
| Names don't match | Bill says `ABC Technologies`, bank says `NEFT/ABCTECHPVTLTD/882910` |
| Amounts don't match | ₹10,000 billed, ₹9,564 received |
| Shapes don't match | One payment covers three bills, or one bill is paid in two parts |
| Sometimes there is no answer | The customer simply never paid |

Hisaab solves the first three on its own, and **refuses to guess** on the fourth.

---

## Why the amount is always smaller

Every cut on the way in is legal and expected:

| Deduction | What it is | On a ₹10,000 bill |
|---|---|---|
| MDR / platform fee | What the payment gateway keeps | −₹200 |
| GST on the fee | 18% tax on the gateway's own fee | −₹36 |
| TDS | Tax the customer must hold back by law | −₹200 |
| **Net received** | | **₹9,564** |

A naive matcher sees ₹10,000 against ₹9,564 and creates a useless exception.
Hisaab computes the expected deductions first, then compares.
That turns dozens of fake exceptions into clean matches, and the formula it used becomes the explanation shown to the user.

---

## How it works

```
invoices + bank transactions
        │
        ▼
  clean and normalise      names, dates, money as integer paise
        │
        ▼
  input guardrails         reject junk before it costs anything
        │
        ▼
  router (plain if/else)
    ├── has a reference number ──▶ fast path, exact match, no AI
    └── no reference
             │
             ▼
        blocking           narrow to ~10 plausible candidates
             ▼
        settlement maths   work out the expected deductions
             ▼
        scoring            reference, amount, name, date → 0 to 1
             ▼
        margin             best score minus runner-up
             ▼
        global assignment  resolve conflicts across the whole batch
             ▼
        LLM adjudicator    only for the middle ground
             ▼
        hard rules         plain code, no AI, no exceptions
             ▼
   AUTO APPROVED  |  NEEDS REVIEW  |  EXCEPTION + reason
             ▼
        audit log          every decision, append-only
```

---

## Three rules we do not break

1. **The router is plain code, not an LLM.** Three fixed branches. An LLM there would add cost, delay and randomness for nothing.
2. **The LLM never does arithmetic.** Our code computes every number. The LLM only chooses between options and explains in English.
3. **The LLM never has the final say on money.** It recommends. Hard-coded rules decide whether that recommendation is allowed to become an action.

The LLM touches roughly 20% of records. Everything else is ordinary code.

---

## Scoring

Every invoice-payment pair gets a score from 0 to 1, built from four independent signals:

| Signal | Base weight |
|---|---|
| Reference number | 0.50 |
| Amount | 0.25 |
| Name | 0.15 |
| Date | 0.10 |

**Missing signals are renormalised, never zeroed.**
A bank line with no reference would otherwise cap at 0.50 however perfect everything else is.
Absence of evidence is not evidence against.

**Margin = best score − runner-up.**
A score of 0.95 means nothing if the runner-up scored 0.94.
That is a coin flip, not a match, and margin is what catches it.

---

## Guardrails

Three layers, all of them plain `if` statements.

**Before anything runs** — amount positive, currency present, date parses, every record has an ID.

**On every proposed match** — a match must pass all of these to be auto-approved:

| Rule | Threshold |
|---|---|
| Match score | ≥ 0.90 |
| Margin | ≥ 0.15 |
| Amount gap after settlement maths | ≤ ₹1 |
| Payment date vs due date | −7 to +45 days |
| Transaction already matched | must be no |
| Value ceiling | over ₹5,00,000 always goes to a human |
| New counterparty | fewer than 3 prior confirmed matches never auto-approves |

**On the LLM's output** — valid JSON, and the chosen ID must exist in the list we sent it.

The score does not get a veto.
Score 0.99 with an unexplained gap of ₹340 is still an exception.

## Results

On the 85 held-out records, measured against the answer key:

| | |
|---|---|
| Wrong auto-approvals | **0** |
| Auto-approval precision | **100%** |
| Outcome accuracy | **100%** |
| Straight-through | **72.9%** (62 of 85) |
| Missed exceptions | 0 |
| Time | 2.1s for 85 records |
| Cost | **₹0.02 per run**, ₹0.24 per 1,000 records |

72.9% is not a number we tuned towards — it is the ceiling. 62 of the 85
records are the ones that *should* be automated; the other 23 genuinely need a
human. Anything above 62 would be a wrong approval, not an improvement.

## What each layer actually bought

Every row is a real run on the same records.

| Configuration | Straight-through | Accuracy | Wrong approvals |
|---|---|---|---|
| Scoring only | 82.4% | 83.5% | **11** |
| + guardrails | 0.0% | 12.9% | 0 |
| + memory | 69.4% | 96.5% | 0 |
| + LLM adjudicator | **72.9%** | **100%** | 0 |

Three things worth reading off that table.

**Guardrails without memory reach 0%.** Every customer looks new, so nothing
can be automated. Guardrails without memory are useless and memory without
guardrails is pointless — neither number means anything alone.

**Memory is the biggest single contribution**, worth 69 points. It is also the
easiest place to produce a fake number, so a batch can never learn from itself:
`learn_from` returns a *new* memory rather than changing the one in use, and
worked examples drawn from the graded records are filtered out of a graded run.
Improvement only ever shows up in a later run.

**The LLM is worth 3.5 points** for three calls and six paise. The hard rules
run first, and the model is asked only when its answer could change the
outcome — sixteen records land in its score band, but most are held by rules it
cannot override, so we never pay to ask about them.

## Watching it learn

```bash
docker compose exec api python learn.py
```

Starts with history for half the customers, runs the batch, has a reviewer
confirm what was held, and runs again:

```
Day one   automated 29/85 (34.1%)   40 held as new customer   0 wrong
Day two   automated 61/85 (71.8%)   +37.6 points              0 wrong
          32 records resolved that had not before
```

---

## How we prove it works

The data is synthetic, so the answer key is generated alongside it.

| Metric | Target |
|---|---|
| Wrong auto-approvals | **0 — non-negotiable** |
| Straight-through rate | 65–72.9% (see below for why 72.9 is the ceiling) |
| Missed exceptions | 0 |
| Cost per 1,000 records | tracked |
| P95 latency per record | tracked |

Match rate alone is a bad metric — match everything to anything and you hit 100%.
So we measure missed matches and wrong matches separately, and break both down per scenario.

160 records are generated and split three ways: 30 to seed the alias table, 45 to tune the weights, and **85 held out** as the only numbers we report.

---

## Running it

Everything runs in Docker. Nothing runs on the host.

```bash
cp .env.example .env          # add your OpenRouter (or OpenAI) key
docker compose up -d          # db, api, web
```

| Service | What | Port |
|---|---|---|
| `db` | Postgres 16 | 5432 |
| `api` | FastAPI + the pipeline | 8000 |
| `web` | React + Vite | 5173 |

```bash
docker compose exec api python generate_data.py  # build the data and answer key
docker compose exec api python load_data.py      # load it into Postgres

docker compose exec api python run_batch.py      # run a batch
docker compose exec api python eval.py           # every table from the plan
docker compose exec api python eval.py --llm     # also test the model on its own
docker compose exec api python ablate.py         # what each layer bought
docker compose exec api python learn.py          # watch it get better
docker compose exec api python tune_weights.py   # grid-search the weights

docker compose exec api python freeze.py         # check nothing drifted
docker compose exec api python snapshot.py       # write the UI's offline fallback
docker compose exec api python -m pytest tests/  # 170 tests

docker compose down -v && docker compose up -d   # wipe and start over
```

Then open **http://localhost:5173**.

## The screens

Four, and the order matters.

**Exceptions** opens first, because leading with what we could not do is the
point. Every row names a reason code and the evidence behind it.

**Decision trace** is the one that explains the whole thesis. Click any row:
every signal with the weight it actually carried, the settlement arithmetic,
every rule ticked or crossed, and the model's reasoning labelled a
recommendation rather than a decision.

**This run** is the headline plus the per-scenario table that stops it hiding
anything.

**Cash position** is the four numbers, including the value sitting in the
exception queue — money the business cannot currently account for.

If the API is down the UI reads `web/public/results.json` on its own. Nothing
to flip. Regenerate it with `snapshot.py`.

## The data

160 records from one fixed seed, split three ways.

| Set | Size | Purpose |
|---|---|---|
| Alias seed | 30 | Fills the known-names table before the graded run. Never scored. |
| Tuning | 45 | Grid-searching the weights. |
| Held out | 85 | The only numbers we report. |

The answer key is written by the same function that writes the data, so it
cannot drift. Fourteen scenarios are represented, from exact reference matches
to bills nobody ever paid.

Of the 85 graded records, 62 should be auto-approved, 12 are real exceptions,
7 need a human, and 4 are genuinely ambiguous. **So 72.9% is the highest
straight-through rate any correct system can reach on this data.** Anything
above that is a false approval, not an improvement.

Generation is checked before anything is written: the answer key must reference
records that exist, the settlement maths must balance, an invoice marked unpaid
must have nothing in the batch that fits it, and no two invoices may be able to
claim the same payment unless we built them that way.

---

## Stack

Python, PostgreSQL, `rapidfuzz`, `faker`, `pydantic`, `gpt-4o-mini` through OpenRouter, FastAPI, React + Vite + TypeScript, Tailwind, shadcn/ui.

The adjudicator talks to anything OpenAI-compatible. Three settings pick the
provider — key, base URL, model name — so OpenRouter and OpenAI direct are the
same code path.

**No agent framework.**
The pipeline is plain Python functions.
A framework's value is dynamic control flow, and fixed control flow is our entire safety argument.

---

## Documentation

The full plan lives in [`specs/plan.md`](specs/plan.md) — architecture, scoring maths, guardrails, the eval harness, the dataset design, the build order, and a record of nine bugs found and fixed before a line of code was written.

---

> We didn't build an AI that decides where money goes.
> We built one that investigates and recommends, and a set of rules that decides what it's actually allowed to do.
