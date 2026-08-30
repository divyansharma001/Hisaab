---
name: Hisaab
description: A reconciliation desk built on Razorpay's Blade foundations
colors:
  brand: "hsl(218 89% 51%)"
  brandStrong: "hsl(218 87% 43%)"
  positive: "hsl(153 100% 30%)"
  negative: "hsl(4 85% 44%)"
  notice: "hsl(25 100% 44%)"
  information: "hsl(200 100% 41%)"
  surface: "hsl(0 0% 100%)"
  surfaceSunken: "hsl(210 12% 97%)"
  border: "hsl(204 8% 88%)"
  textPrimary: "hsl(200 11% 11%)"
  textMuted: "hsl(204 9% 42%)"
---

# Design System: Hisaab

## Overview

**Creative North Star: "The passbook, not the dashboard"**

Hisaab is a desk, not a report.
The person using it opens it to work through a pile, one record at a time, and closes it when the pile is empty.
So the interface behaves like a well-printed bank passbook: quiet paper, ruled lines, numbers that line up down the column, and colour used only where it changes what you do next.

The world is inherited, not invented.
The user pinned **Razorpay's Blade** design system, so every token here traces to a real Blade value: the azure brand ramp, emerald and crimson and cider for meaning, the blueGray neutral ramp for everything else, and Inter as the single family.
Nothing is borrowed from Blade's shape and then recoloured to taste.

The density is deliberately high.
This is Operate mode: a finance person scanning twenty-three rows wants all twenty-three visible, not eight in generous cards.
Where a marketing surface would breathe, this one rules a line and moves on.

**Key characteristics:**
- One family, Inter, at four weights. No display face.
- Colour is semantic only. A green thing means settled; a green thing never means decoration.
- Every row is one line tall unless its content genuinely wraps.
- Numbers are tabular everywhere, right-aligned in columns, and never wrap mid-value.
- Status is always carried by a word, never by colour alone.

## Colors

Blade's ramps, used at their intended semantic roles. Restrained: the brand azure appears on primary actions and the current tab, nowhere else.

### Primary
- **Razorpay Azure** (hsl(218 89% 51%), Blade `azure.500`): the primary button, the active tab, focus rings, and the selected row marker. Nothing decorative.
- **Azure Strong** (hsl(218 87% 43%), Blade `azure.600`): hover and pressed states for the above.
- **Azure Wash** (hsl(217 100% 98%), Blade `azure.50`): the selected-row tint and the assistant panel's ground.

### Secondary
- **Emerald** (hsl(153 100% 30%), Blade `emerald.500`): money confirmed, matches that stand, "settled" states.
- **Crimson** (hsl(4 85% 44%), Blade `crimson.600`): a payment that does not add up, and only that. Never used for "needs attention".
- **Cider** (hsl(25 100% 44%), Blade `cider.600`): needs a person's eye. The most common non-green state, so it must not read as an alarm.
- **Sapphire** (hsl(200 100% 41%), Blade `sapphire.600`): informational, and the marker on anything the assistant looked at.

### Neutral
Blade `blueGrayLight`, which is very slightly cool. That coolness is the whole reason the page does not look like default Tailwind.
- **Paper** (hsl(0 0% 100%), `0`): cards, table bodies, the header.
- **Desk** (hsl(210 12% 97%), near `50`): the page behind the cards, and table header rows.
- **Rule** (hsl(204 8% 88%), `200`): every border and divider. One value, everywhere.
- **Ink** (hsl(200 11% 11%), `1200`): primary text and every number that matters.
- **Pencil** (hsl(204 9% 42%), `700`): labels, captions, secondary text.

### Named Rules

**The Semantic Colour Rule.** A colour appears only where it changes what the user does next. Settled is green because you can stop looking at it. Cider means open this one. If a colour is doing decoration, delete it.

**The Word Rule.** Every state carries its word. Colour is the second signal, never the only one. A user who cannot distinguish emerald from cider must still be able to work the queue.

## Typography

**Display Font:** TASA Orbiter (Blade `fontFamily.heading`), self-hosted.
**Body Font:** Inter (Blade `fontFamily.text`), with `system-ui, -apple-system, Segoe UI, Arial` behind it.
**Label/Mono Font:** ui-monospace / Menlo, for identifiers and bank narration only.

**Character:** Blade's own pairing. TASA Orbiter is a warmer, more geometric face with real personality in its letterforms; Inter is the neutral workhorse under it. The contrast is what stops the product reading as a default Tailwind page.

The first build skipped the display face on the belief it was not packaged. It is - `@fontsource-variable/tasa-orbiter` - and the pages looked anonymous without it.

**Where the display face is allowed:** the product name and the one page title per screen. Nothing else. Card headings, labels, buttons and every number stay in Inter, because Inter's tabular figures are why it is here and this interface is mostly numbers in columns.

### Hierarchy
- **Page title** (600, 20px, 1.25): the screen's name. One per screen.
- **Section title** (600, 14px, 1.4): a card's heading.
- **Body** (400, 14px, 1.55): sentences, explanations, reasons.
- **Data** (500, 14px, tabular): every amount, count, and rate.
- **Label** (500, 11px, 0.04em, uppercase): column headers and stat captions only.
- **Code** (400, 12px, mono): invoice and payment identifiers, raw bank narration.

### Named Rules

**The Tabular Rule.** Anything that is a number uses `font-variant-numeric: tabular-nums` and never wraps mid-value. A rupee amount that breaks across two lines is a defect, not a layout.

**The No-Shouting Rule.** Uppercase is for 11px labels only. A reason shown to a user is a sentence, never a SCREAMING_SNAKE_CASE constant.

## Layout

A single centred column, max 1200px, 24px gutters. No sidebar: there are four destinations, and a top tab bar is the familiar affordance for that.

- The header is sticky. Every screen is taller than the viewport and losing the tabs mid-table is a real failure.
- Tables sit inside a card, scroll horizontally within their own container, and never make the page scroll sideways.
- Spacing rhythm is a 4px base: 4, 8, 12, 16, 20, 24, 32, 48. Rows are 12px vertical padding, 20px horizontal.
- More space above a heading than below it, so a heading belongs to what follows it.
- Two-column splits are 3:2, not 1:1, so the dense side gets the room it needs.

**Tables become lists below `md`.** Measured at 500px, the queue table pushed
"what is wrong" 338px off-screen - the one column a reviewer opens the list to
read. Below the breakpoint every table renders as stacked rows carrying the
same fields. A table whose point is out of view is worse than no table.

**The tab bar scrolls sideways rather than wrapping.** Four labels wrapping to
two lines each turned a 36px bar into a 100px block.

## Elevation & Depth

Almost flat. This is paper on a desk, not floating glass.

- **Card:** 1px `Rule` border plus Blade `elevation.onLight.lowRaised`, `0px 2px 4px 0px hsla(200, 10%, 18%, 0.06)`. The border does the work; the shadow only stops the card dissolving into the desk. Both live as `--shadow-card` and `--radius-card` so no component re-guesses them.
- **Sticky header:** the same border on its bottom edge, and a solid background. Never translucent - content ghosting through a header is the failure it was fixed for.
- **No raised buttons, no inner shadows, no glass.** A zero-offset colour halo is decoration and is banned.

## The mark

Two lines entering horizontally, turning through a rounded elbow, and leaving
as one. An invoice and a payment becoming a single settled thing.

It is deliberately not a letter. The first version was a white **R** on an
azure square, which is Razorpay's mark, not ours - wrong on a product called
Hisaab, and wrong in a room where Razorpay is judging.

Two earlier attempts read as an arrow. Angled strokes converging on a point
are an arrowhead, however they are balanced; the inputs have to arrive
horizontally for the shape to read as a merge.

## Shapes

- **Radius:** 12px on cards - Blade `border.radius.medium` - and 6px on buttons, inputs, pills and chips. Full round on progress tracks.
- Consistent across every screen. A button in one place is the same shape as a button in another.

## Components

- **Stat band.** The four headline numbers live in **one** card with ruled columns, not four floating tiles. Four boxes of one number each is the hero-metric template, and three screens were stacking it; ruled columns read the way a ledger does. Banded only where all four fit on a line - below that it stacks and the rules turn horizontal, because `divide-x` on a two-row grid draws a border down the left of the third cell.
- **Stat.** A caption, a number, and one line of plain explanation. The explanation is mandatory - a number with no sentence under it says nothing to someone who did not build this.
- **Card + CardHead.** Every panel. Title, optional one-line note, then content. Never nested.
- **Status pill.** A word plus its semantic tint, 11px, medium. Four values only: Settled, Needs sign-off, Not matched, Unclear. The warn tint uses cider 800 rather than 700, because 700 measured 4.22:1 on its own ground and 11px text needs 4.5.
- **Reason chip.** A plain-English phrase, neutral ground. Never the raw constant.
- **Table.** Sticky-free header on `Desk`, 1px rules between rows, hover tint, whole row clickable with a visible pointer.
- **Bar.** A 6px track for proportions. Semantic fill. Used for aging and outcome splits only.
- **Empty state.** Teaches the screen: what would appear here and why it is empty. Never "No data".
- **Skeleton.** Grey blocks in the real layout while a batch runs, never a centred spinner.

Every interactive element ships default, hover, focus-visible, active, and disabled. Focus-visible is a 2px azure ring at 2px offset.

**The one exception is a table row.** Chrome paints an outline on a
`display: table-row` as a sliver along its bottom edge, so the shared ring is
effectively invisible there. Focused rows take an azure background tint
instead - a stronger cue than the hover state it has to out-rank.

## Do's and Don'ts

**Do**
- Write the reason as a sentence a person could say out loud.
- Keep the queue first. The screen opens on what needs a person, not on a success rate.
- Show the arithmetic when explaining an amount: gross, what was deducted, what landed.
- Let long tables be long. Density is a feature here.

**Don't**
- Never surface internal vocabulary: adjudicator, straight-through, exception, margin, guardrail, held for a human, reason code, split, heldout. Each has a plain replacement.
- Never show a raw enum. `AMOUNT_GAP_UNEXPLAINED` is a bug in the copy, not a label.
- Never let an amount wrap or a monospace ID break at its hyphen.
- Never use monospace to make something feel technical. It is for identifiers and bank text only.
- Never put accuracy or answer-key language on the daily screens. A real user has no answer key; that lives in its own area.
- No emoji or unicode glyphs standing in for icons. Icons are authored SVG at one stroke weight.
