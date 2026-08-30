import type { Outcome } from "./types";

/**
 * Every internal word, translated once.
 *
 * The API speaks the engineering vocabulary the pipeline was built in:
 * AMOUNT_GAP_UNEXPLAINED, straight-through, adjudicator, margin, exception.
 * A person reconciling payments has never used any of those words.
 *
 * This file is the only place the translation happens, so a term can never be
 * plain on one screen and raw on another. If a new code appears and is not
 * listed here, `plain()` shows a readable fallback rather than a constant.
 */

type Tone = "good" | "warn" | "bad" | "info" | "neutral";

export const OUTCOME: Record<Outcome, { label: string; tone: Tone }> = {
  AUTO: { label: "Settled", tone: "good" },
  REVIEW: { label: "Needs sign-off", tone: "warn" },
  EXCEPTION: { label: "Not matched", tone: "bad" },
  AMBIGUOUS: { label: "Unclear", tone: "info" },
};

/** What the user should do about each state, in one short line. */
export const OUTCOME_MEANING: Record<Outcome, string> = {
  AUTO: "Matched and closed. Nothing for you to do.",
  REVIEW: "We know which payment this is. You just have to agree.",
  EXCEPTION: "We could not account for the money. Someone has to look.",
  AMBIGUOUS: "More than one invoice wants the same payment.",
};

/** Reason codes, as a sentence a person could say out loud. */
const REASONS: Record<string, string> = {
  MATCHED_REFERENCE: "The invoice number was on the payment",
  MATCHED_NAME_AMOUNT: "The name and the amount both matched",
  MATCHED_ALIAS: "A short name we have seen this customer use before",
  TDS_2PCT: "TDS deducted at 2%",
  TDS_10PCT: "TDS deducted at 10%",
  MDR_GST: "Gateway fee and GST taken out",
  PARTIAL_PAYMENT: "Paid in instalments",
  COMBINED_PAYMENT: "One transfer covering several invoices",
  BATCHED_SETTLEMENT: "Part of a batch settlement",
  DUPLICATE_TRANSACTION: "The same payment appears twice",
  AMOUNT_GAP_UNEXPLAINED: "The amount does not add up",
  NO_PAYMENT_FOUND: "No payment found for this invoice",
  AMBIGUOUS_CANDIDATES: "Two invoices want the same payment",
  BELOW_THRESHOLD: "Not enough to go on",
  MALFORMED_INPUT: "The record itself is broken",
  NEW_COUNTERPARTY: "We have barely dealt with this customer",
  DATE_OUT_OF_WINDOW: "Paid well outside the expected window",
  VALUE_CEILING: "Large amount, so a person signs it off",
};

/** The nine checks, named the way a reviewer would name them. */
const CHECKS: Record<string, string> = {
  score: "Strong enough match",
  margin: "One clear winner",
  "amount explained": "Every rupee accounted for",
  "date window": "Paid around the due date",
  currency: "Same currency",
  "value ceiling": "Within the sign-off limit",
  "known counterparty": "Customer we know",
  "not a duplicate": "Not a repeated payment",
  unclaimed: "No other invoice wants it",
};

/** How the payment lined up against the invoice. */
const SHAPES: Record<string, string> = {
  one_to_one: "One payment",
  partial: "Part payment",
  combined: "Covers several invoices",
};

/** The four signals we score a payment on. */
export const SIGNAL: Record<string, { label: string; hint: string }> = {
  reference: { label: "Invoice number", hint: "Was our invoice number in the payment text?" },
  amount: { label: "Amount", hint: "Does the money add up once deductions are applied?" },
  name: { label: "Name", hint: "How close is the bank name to the customer name?" },
  date: { label: "Date", hint: "How near the due date did it arrive?" },
};

function humanise(code: string): string {
  const words = code.toLowerCase().replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function reasonText(code: string | null): string {
  if (!code) return "";
  return REASONS[code] ?? humanise(code);
}

export function checkText(name: string): string {
  return CHECKS[name] ?? humanise(name);
}

export function shapeText(pass: string): string {
  return SHAPES[pass] ?? humanise(pass);
}

/** Scenario names from the eval, for the evidence screen only. */
const ACRONYMS: Record<string, string> = { tds: "TDS", mdr: "MDR", gst: "GST" };

export function scenarioText(scenario: string): string {
  const words = scenario.split("_").map((w) => ACRONYMS[w] ?? w);
  const first = words[0];
  words[0] = first.charAt(0).toUpperCase() + first.slice(1);
  return words.join(" ");
}

/**
 * A 0-1 score, said in words.
 *
 * The number is still shown next to it for anyone who wants it, but "Very
 * strong" is what tells a reviewer whether to slow down.
 */
export function strength(score: number): string {
  if (score >= 0.95) return "Very strong";
  if (score >= 0.85) return "Strong";
  if (score >= 0.7) return "Reasonable";
  if (score >= 0.5) return "Weak";
  return "Very weak";
}

/** Capitalise a fragment that is being used as a standalone sentence. */
export function asSentence(text: string): string {
  if (!text) return text;
  const capped = text.charAt(0).toUpperCase() + text.slice(1);
  return /[.!?]$/.test(capped) ? capped : `${capped}.`;
}
