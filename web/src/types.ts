export type Money = { paise: number; display: string };

export type Outcome = "AUTO" | "REVIEW" | "EXCEPTION" | "AMBIGUOUS";

export type RunSummary = {
  run_id: string;
  split: string;
  ran_at: string;
  records: number;
  seconds: number;
  records_per_second: number;
  false_auto_approvals: number;
  auto_precision: number;
  straight_through_count: number;
  straight_through_value: number;
  outcome_accuracy: number;
  reason_accuracy: number;
  missed_exceptions: number;
  llm_calls: number;
  input_tokens: number;
  output_tokens: number;
  cost: Money;
  cost_per_1000: Money;
  value_settled: Money;
  value_held: Money;
  outcomes: Record<string, number>;
};

export type ExceptionRow = {
  invoice_id: string;
  counterparty: string;
  amount: Money;
  outcome: Outcome;
  reason_code: string | null;
  reason_text: string;
  score: number;
  margin: number;
  candidates: string[];
  llm_used: boolean;
  correct: boolean | null;
};

export type ExceptionList = { count: number; total: Money; exceptions: ExceptionRow[] };

export type Signals = {
  reference: number | null;
  amount: number | null;
  name: number | null;
  date: number | null;
};

export type Candidate = {
  txn_id: string;
  description: string;
  amount: Money;
  value_date: string;
  score: number;
  chosen: boolean;
  signals: Signals;
  weights_used: Record<string, number>;
  amount_status: string;
  amount_formula: string | null;
  matched_via: string;
};

export type Trace = {
  invoice: {
    id: string;
    counterparty: string;
    counterparty_clean: string;
    amount: Money;
    invoice_date: string;
    due_date: string;
    scenario: string;
  };
  decision: {
    outcome: Outcome;
    reason_code: string | null;
    reason_text: string;
    score: number;
    margin: number;
    margin_basis: string;
    decided_by: string;
    settled_by: string[];
    allocated: Money;
  };
  rules: { name: string; passed: boolean }[];
  candidates: Candidate[];
  adjudicator: {
    used: boolean;
    confidence: number;
    reasoning: string;
    rejected: string | null;
    note: string;
  };
  verdict: { expected: Outcome | null; correct: boolean | null };
};

export type ScenarioRow = {
  scenario: string;
  right: number;
  total: number;
  false_approvals: number;
  rate: number;
};

export type EvalBreakdown = {
  scenarios: ScenarioRow[];
  reason_accuracy: number;
  outcome_accuracy: number;
};

export type CashPosition = {
  as_of: string;
  confirmed_in: Money;
  still_owed: Money;
  uncertain: Money;
  withheld: Money;
  withheld_split: { mdr: Money; gst: Money; tds: Money };
  still_owed_note: string;
  uncertain_note: string;
  withheld_note: string;
  open_total: Money;
  aging: { label: string; count: number; value: Money }[];
};

export type AdjudicatedRow = {
  invoice_id: string;
  counterparty: string;
  amount: Money;
  outcome: Outcome;
  score: number;
  margin: number;
  confidence: number;
  reasoning: string;
  rejected: string | null;
  correct: boolean | null;
};

export type AdjudicatedList = {
  count: number;
  of_total: number;
  records: AdjudicatedRow[];
  note: string;
};

export type AskAnswer = {
  question: string;
  answer: string;
  invoice_id: string | null;
  facts: string[];
  used_model: boolean;
  rejected: string | null;
  cost: Money;
};

export type ThresholdPoint = {
  bar: number;
  closed: number;
  wrong: number;
  is_current: boolean;
};

export type Thresholds = {
  with_rules: ThresholdPoint[];
  score_only: ThresholdPoint[];
  current_bar: number | null;
  cost_of_the_rules: {
    automation_given_up: number | null;
    wrong_approvals_prevented: number | null;
  };
  finding: string;
};

export type AblationRow = {
  layer: string;
  explanation: string;
  closed: number;
  accuracy: number;
  wrong: number;
  closed_change: number | null;
  wrong_change: number | null;
};

export type Ablation = { rows: AblationRow[]; records: number };

export type Mistake = {
  invoice_id: string;
  customer: string;
  amount: Money;
  scenario: string;
  we_said: Outcome;
  answer_was: Outcome;
  our_reason: string;
  asked_the_assistant: boolean;
  erred_towards: string;
};

export type Mistakes = {
  count: number;
  mistakes: Mistake[];
  all_in_one_direction: boolean;
};

export type Learning = {
  customers_known_at_the_start: number;
  customers_in_the_batch: number;
  held_as_new: { invoice_id: string; customer: string; amount: Money }[];
  confirmed: number;
  closed_before: number;
  closed_after: number;
  newly_closing: string[];
  knock_on: string[];
  records: number;
  note: string;
  knock_on_note: string;
};

export type ConfirmResult = {
  invoice_id: string;
  customer: string;
  alias_written: boolean;
  variant?: string;
  counts_towards_graded_runs?: boolean;
  closes_now: number;
  closed_before: number;
  newly_closing: string[];
  also_affected: string[];
  note: string;
};

export type SandboxInvoice = {
  id: string;
  customer: string;
  amount: Money;
  invoice_date: string;
  due_date: string;
};

export type SandboxPayment = {
  id: string;
  bank_text: string;
  amount: Money;
  value_date: string;
};

export type SandboxContents = {
  invoices: SandboxInvoice[];
  payments: SandboxPayment[];
  room_left: number;
};

export type SandboxResult = {
  ran: boolean;
  why?: string;
  records?: number;
  settled?: number;
  held?: number;
  seconds?: number;
  note?: string;
  assumption?: string;
  results?: {
    invoice_id: string;
    customer: string;
    amount: Money;
    outcome: Outcome;
    reason_code: string | null;
    reason_text: string;
    matched: { id: string; bank_text: string }[];
    amount_working: string;
    score: number;
  }[];
};
