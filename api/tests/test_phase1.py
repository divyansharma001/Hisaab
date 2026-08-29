"""Phase 1 regression tests.

Two things are being protected here. First, the money and settlement maths,
because every later number depends on them. Second, the *validator itself* -
a check that never fires is worse than no check, so each one is shown failing
on a deliberately broken dataset.
"""

import copy
from datetime import date
from decimal import Decimal

import pytest

from app import settlement
from app.dataset import Split
from app.generate.run import generate_all
from app.generate.validate import validate, validate_across_splits
from app.money import fmt, pct_of, rupees
from app.names import clean_name, extract_refs, name_from_bank_text, squash

SEED = 20260829


@pytest.fixture(scope="module")
def datasets():
    return generate_all(SEED)


@pytest.fixture(scope="module")
def heldout(datasets):
    return datasets[Split.HELDOUT]


# --- money -----------------------------------------------------------------


def test_rupees_to_paise():
    assert rupees(10_000) == 1_000_000


def test_percentage_uses_decimal_not_float():
    # 2% of Rs 10,000 must be exactly Rs 200, not 199.99999999998.
    assert pct_of(rupees(10_000), Decimal("0.02")) == rupees(200)


def test_rounding_is_half_up():
    assert pct_of(101, Decimal("0.5")) == 51


def test_indian_digit_grouping():
    assert fmt(rupees(500_000)) == "Rs 5,00,000.00"
    assert fmt(956_400) == "Rs 9,564.00"


# --- names -----------------------------------------------------------------


def test_legal_suffixes_are_stripped():
    assert clean_name("ABC Technologies Pvt Ltd") == "ABC TECHNOLOGIES"
    assert clean_name("ABC Technologies Limited") == "ABC TECHNOLOGIES"


def test_squash_matches_a_spaceless_bank_field():
    assert squash("ABC Technologies Pvt Ltd") == "ABCTECHNOLOGIES"


@pytest.mark.parametrize(
    "description,expected",
    [
        ("NEFT/ABCTECHPVTLTD/882910", "ABCTECHPVTLTD"),
        ("UPI/ABCTECH@OKAXIS/PAYMENT INV-0231", "ABCTECH"),
        ("RTGS/ABC TECHNOLOGIES/UTIBR52023081200123", "ABC TECHNOLOGIES"),
        ("IMPS/912345678901/KAVERI FOODS", "KAVERI FOODS"),
    ],
)
def test_name_survives_the_bank_narration(description, expected):
    assert name_from_bank_text(description) == expected


def test_a_utr_never_leaks_into_the_name():
    name = name_from_bank_text("RTGS/ABC TECHNOLOGIES/UTIBR52023081200123")
    assert "UTIBR" not in name


def test_invoice_number_is_extracted():
    assert "INV-0231" in extract_refs("NEFT/ABCTECH/HDFC123/INV-0231")


# --- settlement ------------------------------------------------------------


def test_the_plans_worked_example():
    # Rs 10,000 minus MDR and the GST on it is Rs 9,764. Plan section 2.4.
    d = settlement.gateway(rupees(10_000))
    assert d.fee_paise == rupees(200)
    assert d.gst_on_fee_paise == rupees(36)
    assert d.net_paise == rupees(9_764)


def test_every_formula_balances():
    for d in settlement.all_formulas(rupees(37_450)):
        assert d.net_paise == d.gross_paise - d.total_deducted_paise


def test_explain_finds_the_right_formula():
    gross = rupees(10_000)
    assert settlement.explain(gross, rupees(9_764)).formula == "MDR_GST"
    assert settlement.explain(gross, rupees(9_800)).formula == "TDS_2PCT"
    assert settlement.explain(gross, rupees(9_000)).formula == "TDS_10PCT"


def test_explain_gives_up_when_nothing_fits():
    assert settlement.explain(rupees(10_000), rupees(9_660)) is None


# --- the generated dataset -------------------------------------------------


def test_split_sizes(datasets):
    """The seed set is not a fixed number.

    Its size is three prior settlements per customer the graded batch will
    meet, because the new-counterparty guardrail asks for three before it will
    automate anyone. Fixing it at 30 left 64 of 85 graded invoices looking like
    first-time counterparties, and straight-through came out at 22.4% - a
    number about our synthetic history, not our matcher.
    """
    from app.generate.builders import PRIOR_SETTLEMENTS_PER_CUSTOMER

    heldout = datasets[Split.HELDOUT]
    assert len(heldout.invoices) == 85
    assert len(datasets[Split.TUNING].invoices) == 45

    # History has to cover every batch that gets run, not only the graded one.
    # Covering the graded customers alone leaves the tuning set entirely held,
    # and the weight search then measures nothing.
    customers = {i.counterparty_name_clean for i in heldout.invoices} | {
        i.counterparty_name_clean for i in datasets[Split.TUNING].invoices
    }
    seed = datasets[Split.ALIAS_SEED]
    assert len(seed.invoices) == len(customers) * PRIOR_SETTLEMENTS_PER_CUSTOMER


def test_every_graded_customer_has_prior_history(datasets):
    """Otherwise the new-counterparty guardrail blocks them for a reason that
    is about our data rather than about the payment."""
    from app.generate.builders import PRIOR_SETTLEMENTS_PER_CUSTOMER

    seeded: dict[str, int] = {}
    for inv in datasets[Split.ALIAS_SEED].invoices:
        seeded[inv.counterparty_name_clean] = seeded.get(inv.counterparty_name_clean, 0) + 1

    for split in (Split.HELDOUT, Split.TUNING):
        for inv in datasets[split].invoices:
            assert seeded.get(inv.counterparty_name_clean, 0) >= PRIOR_SETTLEMENTS_PER_CUSTOMER


def test_same_seed_gives_the_same_data():
    a = generate_all(SEED)[Split.HELDOUT]
    b = generate_all(SEED)[Split.HELDOUT]
    assert a.model_dump_json() == b.model_dump_json()


def test_a_different_seed_gives_different_data():
    a = generate_all(SEED)[Split.HELDOUT]
    b = generate_all(SEED + 7)[Split.HELDOUT]
    assert a.model_dump_json() != b.model_dump_json()


def test_every_split_validates(datasets):
    for split, data in datasets.items():
        report = validate(data)
        assert report.ok, f"{split}: {report.errors}"


def test_ids_are_unique_across_splits(datasets):
    assert validate_across_splits(datasets).ok


def test_money_is_always_an_integer(heldout):
    for inv in heldout.invoices:
        assert isinstance(inv.amount_paise, int)
    for txn in heldout.transactions:
        assert isinstance(txn.amount_paise, int)


def test_the_alias_seed_set_shares_customers_with_the_graded_set(datasets):
    """Otherwise seeding the alias table teaches nothing the graded run meets."""
    seeded = {i.counterparty_name_clean for i in datasets[Split.ALIAS_SEED].invoices}
    graded = {i.counterparty_name_clean for i in datasets[Split.HELDOUT].invoices}
    assert seeded & graded, "the seed set met none of the graded customers"


def test_the_tuning_set_shares_no_customers_with_the_graded_set(datasets):
    """Tuning on the graded set's customers would be fitting the test set."""
    tuning = {i.counterparty_name_clean for i in datasets[Split.TUNING].invoices}
    graded = {i.counterparty_name_clean for i in datasets[Split.HELDOUT].invoices}
    assert not (tuning & graded)


def test_straight_through_ceiling(heldout):
    """62 of 85 records can legitimately be automated. Anything more is a
    false approval, not an improvement. Plan section 9."""
    auto = sum(1 for t in heldout.truth if t.expected_outcome == "AUTO")
    assert auto == 62


# --- the validator itself --------------------------------------------------
#
# Each test breaks the dataset in one specific way and asserts the validator
# notices. A validator that cannot fail is not a validator.


def _broken(heldout, mutate):
    d = copy.deepcopy(heldout)
    mutate(d)
    return validate(d)


def test_catches_a_future_date(heldout):
    def mutate(d):
        d.transactions[0].value_date = date(2027, 1, 1)

    assert not _broken(heldout, mutate).ok


def test_catches_unbalanced_settlement_maths(heldout):
    def mutate(d):
        d.settlements[0].net_paise += rupees(50)

    assert not _broken(heldout, mutate).ok


def test_catches_a_short_payment_a_formula_can_explain(heldout):
    def mutate(d):
        t = next(x for x in d.truth if x.scenario == "short_payment")
        inv = next(i for i in d.invoices if i.id == t.invoice_id)
        txn = next(x for x in d.transactions if x.id == t.expected_txn_ids[0])
        txn.amount_paise = settlement.tds(inv.amount_paise, "TDS_2PCT").net_paise

    assert not _broken(heldout, mutate).ok


def test_catches_a_payment_for_an_invoice_marked_unpaid(heldout):
    def mutate(d):
        t = next(x for x in d.truth if x.scenario == "no_payment")
        inv = next(i for i in d.invoices if i.id == t.invoice_id)
        d.transactions.append(
            d.transactions[0].model_copy(
                update={
                    "id": "TXN-FAKE",
                    "amount_paise": inv.amount_paise,
                    "counterparty_name_clean": inv.counterparty_name_clean,
                }
            )
        )

    assert not _broken(heldout, mutate).ok


def test_catches_accidental_ambiguity(heldout):
    """Two invoices that could both claim one payment, where we intended one."""

    def mutate(d):
        a = next(t for t in d.truth if t.scenario == "clean_name_amount")
        b = next(t for t in d.truth if t.scenario == "alias_variation")
        src = next(i for i in d.invoices if i.id == a.invoice_id)
        dst = next(i for i in d.invoices if i.id == b.invoice_id)
        dst.counterparty_name_clean = src.counterparty_name_clean
        dst.amount_paise = src.amount_paise

    report = _broken(heldout, mutate)
    assert any("accidental ambiguity" in e for e in report.errors)


def test_catches_an_invoice_with_no_answer_key(heldout):
    def mutate(d):
        d.truth.pop()

    report = _broken(heldout, mutate)
    assert any("no answer key row" in e for e in report.errors)


def test_catches_a_truth_row_naming_a_missing_transaction(heldout):
    def mutate(d):
        d.truth[0].expected_txn_ids = ["TXN-DOES-NOT-EXIST"]

    assert not _broken(heldout, mutate).ok


# --- the generator must not depend on a lucky seed --------------------------


@pytest.mark.parametrize("seed", range(4000, 4025))
def test_any_seed_produces_a_valid_dataset(seed):
    """The one bug this catches is the expensive one.

    Seed 1025 originally produced two invoices that could both claim one
    payment, because NARMADA INFRA INDIA and YAMUNA INFRA INDIA score 81
    against each other and a 2% TDS deduction bridged their amounts. The
    answer key then claimed a single right answer where two existed.

    Both causes are now designed out - the company pool rejects confusable
    names, and amounts are separated by what the deduction formulas can turn
    them into - so this should hold for every seed, not just ours.
    """
    datasets = generate_all(seed)
    for split, data in datasets.items():
        report = validate(data)
        assert report.ok, f"seed {seed}, {split}: {report.errors[:3]}"
    assert validate_across_splits(datasets).ok
