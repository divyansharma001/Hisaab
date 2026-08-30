-- Hisaab schema. Plan section 10.
-- Money is always integer paise. Never floats.

DROP TABLE IF EXISTS audit_log        CASCADE;
DROP TABLE IF EXISTS episodes         CASCADE;
DROP TABLE IF EXISTS aliases          CASCADE;
DROP TABLE IF EXISTS exceptions       CASCADE;
DROP TABLE IF EXISTS match_allocations CASCADE;
DROP TABLE IF EXISTS matches          CASCADE;
DROP TABLE IF EXISTS ground_truth     CASCADE;
DROP TABLE IF EXISTS settlements      CASCADE;
DROP TABLE IF EXISTS transactions     CASCADE;
DROP TABLE IF EXISTS invoices         CASCADE;

DROP TYPE IF EXISTS split_name    CASCADE;
DROP TYPE IF EXISTS decision_kind CASCADE;

-- Three sets, never mixed. Plan section 9.
CREATE TYPE split_name AS ENUM ('alias_seed', 'tuning', 'heldout');

-- The three terminal states from the guardrail layer, plus the truth-only
-- value AMBIGUOUS which says "correct behaviour here is to refuse".
CREATE TYPE decision_kind AS ENUM ('AUTO', 'REVIEW', 'EXCEPTION', 'AMBIGUOUS');


CREATE TABLE invoices (
  id                      TEXT PRIMARY KEY,
  split                   split_name  NOT NULL,
  invoice_no              TEXT        NOT NULL UNIQUE,
  counterparty_name       TEXT        NOT NULL,
  counterparty_name_clean TEXT        NOT NULL,
  amount_paise            BIGINT      NOT NULL CHECK (amount_paise > 0),
  currency                CHAR(3)     NOT NULL DEFAULT 'INR',
  invoice_date            DATE        NOT NULL,
  due_date                DATE        NOT NULL,
  status                  TEXT        NOT NULL DEFAULT 'open',
  scenario                TEXT        NOT NULL
);

CREATE INDEX invoices_split_idx  ON invoices (split);
CREATE INDEX invoices_name_idx   ON invoices (counterparty_name_clean);
CREATE INDEX invoices_amount_idx ON invoices (amount_paise);
CREATE INDEX invoices_due_idx    ON invoices (due_date);


CREATE TABLE transactions (
  id                      TEXT PRIMARY KEY,
  split                   split_name  NOT NULL,
  txn_ref                 TEXT        NOT NULL,
  description_raw         TEXT        NOT NULL,
  counterparty_name_clean TEXT,
  amount_paise            BIGINT      NOT NULL CHECK (amount_paise > 0),
  currency                CHAR(3)     NOT NULL DEFAULT 'INR',
  value_date              DATE        NOT NULL,
  source                  TEXT        NOT NULL,   -- bank | gateway
  utr                     TEXT,
  scenario                TEXT        NOT NULL
);

CREATE INDEX transactions_split_idx  ON transactions (split);
CREATE INDEX transactions_name_idx   ON transactions (counterparty_name_clean);
CREATE INDEX transactions_amount_idx ON transactions (amount_paise);
CREATE INDEX transactions_date_idx   ON transactions (value_date);
CREATE INDEX transactions_utr_idx    ON transactions (utr);


-- What the gateway kept and what the tax office took, per transaction.
CREATE TABLE settlements (
  id                TEXT   PRIMARY KEY,
  txn_id            TEXT   NOT NULL REFERENCES transactions (id) ON DELETE CASCADE,
  gross_paise       BIGINT NOT NULL,
  fee_paise         BIGINT NOT NULL DEFAULT 0,
  gst_on_fee_paise  BIGINT NOT NULL DEFAULT 0,
  tds_paise         BIGINT NOT NULL DEFAULT 0,
  net_paise         BIGINT NOT NULL,
  formula_used      TEXT   NOT NULL,
  settled_on        DATE,
  batch_utr         TEXT,
  CHECK (net_paise = gross_paise - fee_paise - gst_on_fee_paise - tds_paise)
);

CREATE INDEX settlements_txn_idx   ON settlements (txn_id);
CREATE INDEX settlements_batch_idx ON settlements (batch_utr);


-- The answer key, written by the same function that writes the data.
-- Everything in section 8 depends on this table existing.
CREATE TABLE ground_truth (
  invoice_id           TEXT          PRIMARY KEY REFERENCES invoices (id) ON DELETE CASCADE,
  split                split_name    NOT NULL,
  scenario             TEXT          NOT NULL,
  expected_outcome     decision_kind NOT NULL,
  expected_txn_ids     TEXT[]        NOT NULL DEFAULT '{}',
  expected_reason_code TEXT,
  note                 TEXT          NOT NULL
);

CREATE INDEX ground_truth_split_idx    ON ground_truth (split);
CREATE INDEX ground_truth_scenario_idx ON ground_truth (scenario);


CREATE TABLE matches (
  id             BIGSERIAL     PRIMARY KEY,
  transaction_id TEXT          NOT NULL REFERENCES transactions (id) ON DELETE CASCADE,
  score          NUMERIC(5, 4),
  margin         NUMERIC(5, 4),
  margin_basis   TEXT,          -- 'runner_up' or 'sole_candidate'. Never null.
  decision       decision_kind NOT NULL,
  decided_by     TEXT          NOT NULL,   -- fast_path | scorer | llm
  created_at     TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX matches_txn_idx ON matches (transaction_id);

-- Deliberately NOT a UNIQUE constraint on transaction_id. One transaction can
-- legitimately settle three invoices, and a unique constraint would make
-- combined payments impossible. See plan section 18, bug 2.
CREATE TABLE match_allocations (
  id              BIGSERIAL PRIMARY KEY,
  match_id        BIGINT    NOT NULL REFERENCES matches  (id) ON DELETE CASCADE,
  invoice_id      TEXT      NOT NULL REFERENCES invoices (id) ON DELETE CASCADE,
  allocated_paise BIGINT    NOT NULL CHECK (allocated_paise > 0)
);

CREATE INDEX match_allocations_match_idx   ON match_allocations (match_id);
CREATE INDEX match_allocations_invoice_idx ON match_allocations (invoice_id);


-- The real safety net: money allocated out of a transaction can never exceed
-- what the transaction was worth. A bug in our matching logic still cannot
-- double-count revenue.
CREATE OR REPLACE FUNCTION check_allocation_sum() RETURNS TRIGGER AS $$
DECLARE
  txn        TEXT;
  allocated  BIGINT;
  available  BIGINT;
BEGIN
  SELECT m.transaction_id INTO txn FROM matches m WHERE m.id = NEW.match_id;

  SELECT COALESCE(SUM(ma.allocated_paise), 0) INTO allocated
  FROM match_allocations ma
  JOIN matches m ON m.id = ma.match_id
  WHERE m.transaction_id = txn;

  SELECT t.amount_paise INTO available FROM transactions t WHERE t.id = txn;

  IF allocated > available THEN
    RAISE EXCEPTION
      'Allocations for transaction % total % paise but the transaction is only worth % paise',
      txn, allocated, available;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER match_allocations_sum_invariant
  AFTER INSERT OR UPDATE ON match_allocations
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION check_allocation_sum();


CREATE TABLE exceptions (
  id            BIGSERIAL PRIMARY KEY,
  invoice_id    TEXT      REFERENCES invoices     (id) ON DELETE CASCADE,
  transaction_id TEXT     REFERENCES transactions (id) ON DELETE CASCADE,
  reason_code   TEXT      NOT NULL,
  reason_text   TEXT      NOT NULL,
  evidence_json JSONB     NOT NULL DEFAULT '{}',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (invoice_id IS NOT NULL OR transaction_id IS NOT NULL)
);

CREATE INDEX exceptions_invoice_idx ON exceptions (invoice_id);
CREATE INDEX exceptions_reason_idx  ON exceptions (reason_code);


-- Memory. Plain tables, not a vector store. Plan section 15.2.
CREATE TABLE aliases (
  id              BIGSERIAL PRIMARY KEY,
  canonical_name  TEXT       NOT NULL,
  variant_name    TEXT       NOT NULL,
  confirmed_count INT        NOT NULL DEFAULT 1,
  -- Which batch this confirmation came from. Episodes already carry this so a
  -- graded run cannot read back examples drawn from the records it is being
  -- graded on; aliases need the same guard for the same reason.
  source_split    split_name NOT NULL DEFAULT 'alias_seed',
  UNIQUE (canonical_name, variant_name)
);

CREATE INDEX aliases_variant_idx   ON aliases (variant_name);
CREATE INDEX aliases_canonical_idx ON aliases (canonical_name);


-- Worked examples shown to the adjudicator.
--
-- source_split is not bookkeeping. An episode written from a graded record and
-- then shown back during grading is the same contamination as an alias learned
-- mid-run: the reported accuracy would include information the system never
-- had. Episodes are filtered by origin, and the graded split is never a source
-- a graded run can read. Section 18, bug 7.
CREATE TABLE episodes (
  id              BIGSERIAL  PRIMARY KEY,
  situation_text  TEXT       NOT NULL,
  resolution_text TEXT       NOT NULL,
  tags            TEXT[]     NOT NULL DEFAULT '{}',
  source_split    split_name NOT NULL
);

CREATE INDEX episodes_tags_idx   ON episodes USING GIN (tags);
CREATE INDEX episodes_source_idx ON episodes (source_split);


-- Append-only. This is the compliance artifact, not just a debug log.
CREATE TABLE audit_log (
  id                BIGSERIAL   PRIMARY KEY,
  run_id            TEXT        NOT NULL,
  record_id         TEXT        NOT NULL,
  stage             TEXT        NOT NULL,
  outcome           TEXT        NOT NULL,
  score             NUMERIC(5, 4),
  margin            NUMERIC(5, 4),
  rules_passed_json JSONB       NOT NULL DEFAULT '[]',
  rules_failed_json JSONB       NOT NULL DEFAULT '[]',
  llm_used          BOOLEAN     NOT NULL DEFAULT FALSE,
  prompt            TEXT,
  raw_response      TEXT,
  ts                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX audit_log_run_idx    ON audit_log (run_id);
CREATE INDEX audit_log_record_idx ON audit_log (record_id);
