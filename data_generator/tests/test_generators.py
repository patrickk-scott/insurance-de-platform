"""Tests for the insurance data generators."""

import numpy as np
import pandas as pd
import pytest

from data_generator.insurance_generator.claims import generate_claims
from data_generator.insurance_generator.config import GeneratorConfig
from data_generator.insurance_generator.generators import (
    generate_policies,
    generate_premiums,
)


@pytest.fixture
def small_cfg():
    return GeneratorConfig(n_policies=100, n_years=3, seed=42)


@pytest.fixture(scope="module")
def book():
    """A larger book for invariant and statistical checks."""
    cfg = GeneratorConfig(n_policies=20_000, n_years=5, seed=7)
    policies = generate_policies(cfg)
    # Claim checks here use the truth table: every claim that occurred,
    # reported or not. Lifecycle and valuation tests are in test_claims.py.
    return (
        cfg,
        policies,
        generate_claims(policies, cfg).truth,
        generate_premiums(policies, cfg),
    )


# ── Policies ────────────────────────────────────────────────────────────────


def test_policies_shape(small_cfg):
    df = generate_policies(small_cfg)
    assert len(df) == small_cfg.n_policies
    assert df["policy_id"].nunique() == small_cfg.n_policies


def test_keys_are_integers(book):
    _, policies, claims, premiums = book
    for df, key in (
        (policies, "policy_id"),
        (claims, "claim_id"),
        (premiums, "premium_id"),
    ):
        assert df[key].dtype == np.int64
        assert df[key].is_unique
        assert df[key].min() == 1


def test_policies_premium_positive(small_cfg):
    df = generate_policies(small_cfg)
    assert (df["annual_premium"] > 0).all()


def test_policies_lob_distribution(small_cfg):
    df = generate_policies(small_cfg)
    assert set(df["line_of_business"]).issubset(set(small_cfg.lines_of_business))


def test_exposure_window_valid(book):
    cfg, policies, _, _ = book
    assert (policies["effective_date"] <= policies["exposure_end_date"]).all()
    assert (policies["exposure_end_date"] <= pd.Timestamp(cfg.sim_end)).all()
    assert (policies["active_years"] >= 1).all()
    # Policies that never lapsed are still in force at the valuation date.
    in_force = policies[~policies["is_lapsed"]]
    assert (in_force["exposure_end_date"] == pd.Timestamp(cfg.sim_end)).all()


def test_lapse_rate_matches_config(book):
    """Share of observed anniversaries that lapsed should be close to p."""
    cfg, policies, _, _ = book
    # A policy with active_years = a survived a-1 anniversaries, plus one more
    # (the lapse) if is_lapsed.
    anniversaries = (policies["active_years"] - 1 + policies["is_lapsed"]).sum()
    observed = policies["is_lapsed"].sum() / anniversaries
    assert observed == pytest.approx(cfg.annual_lapse_rate, abs=0.01)


# ── Claims ──────────────────────────────────────────────────────────────────


def test_claims_have_valid_policy_ids(small_cfg):
    policies = generate_policies(small_cfg)
    cs = generate_claims(policies, small_cfg)
    for df in (cs.claims, cs.truth):
        assert df["policy_id"].isin(policies["policy_id"]).all()


def test_claims_ultimate_positive(book):
    _, _, claims, _ = book
    assert len(claims) > 0
    assert (claims["ultimate_loss"] > 0).all()


def test_claims_report_after_accident(book):
    _, _, claims, _ = book
    assert (claims["report_date"] >= claims["accident_date"]).all()


def test_claims_inside_exposure_window(book):
    """No accident before the policy starts or after it lapses."""
    _, policies, claims, _ = book
    j = claims.merge(policies[["policy_id", "effective_date", "exposure_end_date"]])
    assert (j["accident_date"] >= j["effective_date"]).all()
    assert (j["accident_date"] <= j["exposure_end_date"]).all()
    assert (j["accident_year"] == j["accident_date"].dt.year).all()


def test_claim_frequency_matches_config(book):
    cfg, policies, claims, _ = book
    exposure_years = (
        (policies["exposure_end_date"] - policies["effective_date"]).dt.days + 1
    ).sum() / 365
    assert len(claims) / exposure_years == pytest.approx(
        cfg.claim_frequency_lambda, rel=0.05
    )


# ── Premiums ────────────────────────────────────────────────────────────────


def test_premiums_positive(book):
    _, _, _, premiums = book
    assert (premiums["earned_premium"] > 0).all()
    assert (premiums["earned_days"] >= 1).all()


def test_premiums_inside_exposure_window(book):
    """A lapsed policy stops earning premium in the same month it stops
    having claims: both come from the policy's exposure window."""
    _, policies, _, premiums = book
    j = premiums.merge(policies[["policy_id", "effective_date", "exposure_end_date"]])
    assert (
        j["earned_period"] >= j["effective_date"].dt.to_period("M").dt.start_time
    ).all()
    assert (j["earned_period"] <= j["exposure_end_date"]).all()


def test_earned_premium_is_pro_rata(book):
    """Total earned per policy = annual premium * days in force / 365."""
    _, policies, _, premiums = book
    earned = premiums.groupby("policy_id")["earned_premium"].sum()
    p = policies.set_index("policy_id")
    days = (p["exposure_end_date"] - p["effective_date"]).dt.days + 1
    expected = p["annual_premium"] * days / 365
    # Each month is rounded to the cent, so allow a cent per month.
    months = premiums.groupby("policy_id").size()
    assert ((earned - expected).abs() <= 0.01 * months).all()


# ── Reproducibility ─────────────────────────────────────────────────────────


def test_reproducibility(small_cfg):
    """Same seed should produce identical output for every table.

    created_at is a wall-clock load timestamp, not seeded data, so it is
    excluded from BOTH sides of each comparison.
    """

    def run():
        policies = generate_policies(small_cfg)
        cs = generate_claims(policies, small_cfg)
        premiums = generate_premiums(policies, small_cfg)
        tables = (policies, cs.claims, cs.transactions, cs.truth, premiums)
        return [df.drop(columns=["created_at"]) for df in tables]

    for first, second in zip(run(), run()):
        pd.testing.assert_frame_equal(first, second)


def test_different_seed_differs(small_cfg):
    a = generate_policies(small_cfg)
    b = generate_policies(GeneratorConfig(n_policies=100, n_years=3, seed=43))
    assert not a["annual_premium"].equals(b["annual_premium"])
