"""Tests for the insurance data generators."""
import pytest
import pandas as pd

from data_generator.insurance_generator.config import GeneratorConfig
from data_generator.insurance_generator.generators import (
    generate_policies,
    generate_claims,
    generate_premiums,
)


@pytest.fixture
def small_cfg():
    return GeneratorConfig(n_policies=100, n_years=3, seed=42)


def test_policies_shape(small_cfg):
    df = generate_policies(small_cfg)
    assert len(df) == small_cfg.n_policies
    assert "policy_id" in df.columns
    assert df["policy_id"].nunique() == small_cfg.n_policies


def test_policies_premium_positive(small_cfg):
    df = generate_policies(small_cfg)
    assert (df["annual_premium"] > 0).all()


def test_policies_lob_distribution(small_cfg):
    df = generate_policies(small_cfg)
    assert set(df["line_of_business"]).issubset(set(small_cfg.lines_of_business))


def test_claims_have_valid_policy_ids(small_cfg):
    policies = generate_policies(small_cfg)
    claims = generate_claims(policies, small_cfg)
    assert claims["policy_id"].isin(policies["policy_id"]).all()


def test_claims_loss_positive(small_cfg):
    policies = generate_policies(small_cfg)
    claims = generate_claims(policies, small_cfg)
    if len(claims) > 0:
        assert (claims["loss_amount"] > 0).all()


def test_claims_report_after_accident(small_cfg):
    policies = generate_policies(small_cfg)
    claims = generate_claims(policies, small_cfg)
    if len(claims) > 0:
        assert (claims["report_date"] >= claims["accident_date"]).all()


def test_premiums_positive(small_cfg):
    policies = generate_policies(small_cfg)
    premiums = generate_premiums(policies, small_cfg)
    assert (premiums["earned_premium"] > 0).all()


def test_reproducibility(small_cfg):
    """Same seed should produce identical output."""
    p1 = generate_policies(small_cfg)
    p2 = generate_policies(small_cfg)
    pd.testing.assert_frame_equal(p1, p2.drop(columns=["created_at"]), check_like=False,
                                   check_dtype=False)
