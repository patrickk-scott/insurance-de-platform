"""Tests for the claim lifecycle, valuation cut, and reserving behavior."""

import itertools
from datetime import date

import numpy as np
import pandas as pd
import pytest

from data_generator.insurance_generator.claims import generate_claims
from data_generator.insurance_generator.config import GeneratorConfig
from data_generator.insurance_generator.generators import generate_policies


def _generate(cfg):
    return generate_claims(generate_policies(cfg), cfg)


@pytest.fixture(scope="module")
def book():
    cfg = GeneratorConfig(n_policies=20_000, seed=11)
    return cfg, _generate(cfg)


@pytest.fixture(scope="module")
def settled():
    """Same book valued far in the future: every claim is reported and closed."""
    cfg = GeneratorConfig(n_policies=5_000, seed=5, valuation_date=date(2100, 1, 1))
    return cfg, _generate(cfg)


def _with_incurred(tx):
    return tx.assign(incurred=tx["paid_amount"] + tx["case_reserve_change"])


# ── Valuation cut ───────────────────────────────────────────────────────────


def test_nothing_visible_after_valuation(book):
    cfg, cs = book
    val = pd.Timestamp(cfg.valuation)
    assert (cs.claims["report_date"] <= val).all()
    assert (cs.transactions["transaction_date"] <= val).all()


def test_ibnr_claims_only_in_truth(book):
    """Claims reported after the valuation date exist only in the truth table."""
    _, cs = book
    ibnr = cs.truth[~cs.truth["is_reported"]]
    assert len(ibnr) > 0
    assert not ibnr["claim_id"].isin(cs.claims["claim_id"]).any()
    assert set(cs.claims["claim_id"]) == set(
        cs.truth.loc[cs.truth["is_reported"], "claim_id"]
    )


def test_every_reported_claim_has_open_transaction(book):
    _, cs = book
    opens = cs.transactions[cs.transactions["transaction_type"] == "OPEN"]
    assert opens["claim_id"].is_unique
    assert set(opens["claim_id"]) == set(cs.claims["claim_id"])
    j = opens.merge(cs.claims[["claim_id", "report_date"]])
    assert (j["transaction_date"] == j["report_date"]).all()


def test_transaction_keys_unique_and_hidden_keys_reserved(book):
    _, cs = book
    assert cs.transactions["transaction_id"].is_unique
    assert cs.transactions["transaction_id"].max() <= cs.n_txn_ids
    assert cs.n_claim_ids == len(cs.truth)


def test_moving_valuation_forward_keeps_existing_rows():
    """Later valuation reveals new rows but never changes or renumbers old ones.
    This is what makes incremental loads safe."""
    early = GeneratorConfig(n_policies=2_000, seed=9, valuation_date=date(2022, 12, 31))
    late = GeneratorConfig(n_policies=2_000, seed=9)
    a, b = _generate(early), _generate(late)
    cols = ["transaction_id", "claim_id", "transaction_date", "paid_amount"]
    merged = a.transactions[cols].merge(
        b.transactions[cols], how="left", indicator=True
    )
    assert (merged["_merge"] == "both").all()
    assert len(b.transactions) > len(a.transactions)


# ── Lifecycle and reconciliation ────────────────────────────────────────────


def test_settled_book_reconciles_to_ultimate(settled):
    """Once everything has settled, paid = true ultimate to the cent and every
    case reserve is back to zero."""
    _, cs = settled
    g = cs.transactions.groupby("claim_id").agg(
        paid=("paid_amount", "sum"), reserve=("case_reserve_change", "sum")
    )
    j = g.join(cs.truth.set_index("claim_id")["ultimate_loss"])
    assert len(j) == len(cs.truth)
    assert np.allclose(j["paid"], j["ultimate_loss"], atol=0.005)
    assert np.allclose(j["reserve"], 0, atol=0.005)
    assert (cs.claims["status"] == "Closed").all()


def test_case_reserve_never_negative(book):
    _, cs = book
    tx = cs.transactions.sort_values(["claim_id", "transaction_date", "transaction_id"])
    outstanding = tx.groupby("claim_id")["case_reserve_change"].cumsum()
    assert (outstanding > -0.005).all()
    assert (tx["paid_amount"] >= 0).all()


def test_status_matches_transactions(book):
    """Status is derived from the lifecycle, not drawn at random."""
    _, cs = book
    last = (
        cs.transactions.sort_values(["claim_id", "transaction_id"])
        .groupby("claim_id")["transaction_type"]
        .last()
    )
    j = cs.claims.set_index("claim_id").join(last.rename("last_txn"))
    closed = j["status"] == "Closed"
    assert (j.loc[closed, "last_txn"] == "CLOSE").all()
    assert (j.loc[~closed, "last_txn"] != "CLOSE").all()
    assert j.loc[closed, "closed_date"].notna().all()
    assert j.loc[~closed, "closed_date"].isna().all()


def test_liability_settles_slower_than_property(settled):
    _, cs = settled
    close = cs.transactions[cs.transactions["transaction_type"] == "CLOSE"]
    first_close = close.groupby("claim_id")["transaction_date"].min()
    c = cs.claims.set_index("claim_id")
    c = c.assign(lag=(first_close - c["report_date"]).dt.days)
    median = c.groupby("line_of_business", observed=True)["lag"].median()
    assert median["General Liability"] > 2 * median["Homeowners"]


def test_loss_ratio_near_target(book):
    cfg, cs = book
    from data_generator.insurance_generator.generators import generate_premiums

    premiums = generate_premiums(generate_policies(cfg), cfg)
    lr = cs.truth["ultimate_loss"].sum() / premiums["earned_premium"].sum()
    assert lr == pytest.approx(0.65, abs=0.08)


# ── Reserving: the data must support a chain ladder ─────────────────────────


def chain_ladder(tx: pd.DataFrame, valuation_year: int, col: str):
    """Reference volume-weighted chain ladder (annual ages, no tail factor).

    Test-only. The warehouse version is built in dbt; this exists to prove the
    generated data has realistic development and to cross-check dbt later.
    Returns (latest diagonal, estimated ultimate), both by accident year.
    """
    age = tx["transaction_year"].astype(int) - tx["accident_year"].astype(int) + 1
    inc = tx.assign(age=age).groupby(["accident_year", "age"])[col].sum()
    # Fix the age axis to 1..N. Ages with no activity (e.g. a fully settled
    # old year) would otherwise be missing columns, not zero columns.
    n_ages = valuation_year - int(tx["accident_year"].min()) + 1
    cum = (
        inc.unstack(fill_value=0)
        .reindex(columns=range(1, n_ages + 1), fill_value=0)
        .cumsum(axis=1)
    )
    max_age = {ay: valuation_year - ay + 1 for ay in cum.index}
    for ay in cum.index:
        cum.loc[ay, cum.columns > max_age[ay]] = np.nan
    ages = list(cum.columns)
    ldf = []
    for a1, a2 in itertools.pairwise(ages):
        known = cum[a2].notna()
        ldf.append(cum.loc[known, a2].sum() / cum.loc[known, a1].sum())
    cdf = list(np.cumprod(ldf[::-1])[::-1]) + [1.0]
    latest = cum.ffill(axis=1).iloc[:, -1]
    ultimate = pd.Series(
        {ay: latest[ay] * cdf[ages.index(max_age[ay])] for ay in cum.index}
    )
    return latest, ultimate


def test_losses_develop(book):
    """At the valuation date, reported paid and incurred are well below the
    true ultimate: there is development (and IBNR) for a reserve to estimate."""
    _cfg, cs = book
    tx = _with_incurred(cs.transactions)
    truth = cs.truth["ultimate_loss"].sum()
    paid = tx["paid_amount"].sum() / truth
    incurred = tx["incurred"].sum() / truth
    assert paid < 0.9
    assert paid < incurred < 1.0


@pytest.mark.parametrize("col", ["paid_amount", "incurred"])
def test_chain_ladder_recovers_truth(book, col):
    """Backtest: chain ladder on the visible data lands near the hidden truth.
    Measured error is ~1-2% on the total; 5% leaves room for random noise."""
    cfg, cs = book
    tx = _with_incurred(cs.transactions)
    _, ultimate = chain_ladder(tx, cfg.valuation.year, col)
    truth = cs.truth.groupby("accident_year")["ultimate_loss"].sum()
    assert ultimate.sum() / truth.sum() == pytest.approx(1.0, abs=0.05)
