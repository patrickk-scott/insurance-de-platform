"""
Core data generators: policies and premiums. Claims live in claims.py.

Everything here is vectorized with NumPy: no per-row Python loops. The trick
that makes that possible is "expand by repeat": compute how many child rows
each parent row needs (policy-years per policy, claims per policy-year, months
per policy), then np.repeat the parent index and do the per-row math on whole
arrays at once.

Exposure has ONE source of truth: `active_years` on the policy table. A policy
is in force from `effective_date` up to (not including) its `active_years`-th
anniversary, or the end of the simulation, whichever comes first. Claims and
premiums both derive their window from that, so a lapsed policy stops having
accidents and stops earning premium on the same day.

Actuarial distributions used:
- Lapse:          Geometric(p)       number of policy years until first non-renewal
- Earned premium: pro rata by day, one row per policy-month

Keys are integer surrogate keys (int64), not UUID strings. Pass `id_offset`
to keep keys unique when generating in batches.
"""

import numpy as np
import pandas as pd

from .config import GeneratorConfig
from .dates import (
    DAYS_PER_YEAR,
    ONE_DAY,
    ONE_MONTH,
    STREAM_POLICIES,
    anniversary,
    date_parts,
    days,
    group_position,
    make_rng,
    months,
    now_utc,
    to_pandas_dates,
)

# ── Policies ────────────────────────────────────────────────────────────────


def generate_policies(
    cfg: GeneratorConfig,
    n: int | None = None,
    id_offset: int = 0,
    rng: np.random.Generator | None = None,
) -> pd.DataFrame:
    """Generate the policy table, including each policy's exposure window."""
    n = cfg.n_policies if n is None else n
    rng = rng or make_rng(cfg.seed, 0, STREAM_POLICIES)

    sim_start = np.datetime64(cfg.sim_start, "D")
    sim_end_excl = np.datetime64(cfg.sim_end, "D") + ONE_DAY
    sim_days = int((sim_end_excl - sim_start).astype(np.int64))
    writing_years = cfg.writing_years or cfg.n_years
    writing_days = min(writing_years * DAYS_PER_YEAR, sim_days)

    effective = sim_start + days(rng.integers(0, writing_days, size=n))

    lob_idx = rng.choice(len(cfg.lines_of_business), size=n, p=cfg.lob_weights)
    state_idx = rng.integers(0, len(cfg.states), size=n)

    prem_mean = np.asarray(cfg.lob_premium_mean, dtype=float)[lob_idx]
    prem_std = np.asarray(cfg.lob_premium_std, dtype=float)[lob_idx]
    annual_premium = np.maximum(
        cfg.min_annual_premium, rng.normal(prem_mean, prem_std)
    ).round(2)

    # Exposure. Year 0 is always written. At each later anniversary the policy
    # lapses with probability p, so "years until first lapse" is Geometric(p).
    # The simulation window caps how many years we can observe.
    year, month, day = date_parts(effective)
    max_years = cfg.sim_end.year - year + 1
    if cfg.annual_lapse_rate > 0:
        years_to_lapse = rng.geometric(cfg.annual_lapse_rate, size=n)
    else:
        years_to_lapse = np.full(n, np.iinfo(np.int32).max)
    active_years = np.minimum(years_to_lapse, max_years)
    is_lapsed = years_to_lapse < max_years

    exposure_end_excl = np.minimum(
        anniversary(year, month, day, active_years), sim_end_excl
    )

    policy_id = np.arange(id_offset + 1, id_offset + n + 1, dtype=np.int64)
    return pd.DataFrame(
        {
            "policy_id": policy_id,
            # One holder per policy for now; kept as its own key for the
            # customer dimension later.
            "policy_holder_id": policy_id,
            "effective_date": to_pandas_dates(effective),
            # Last covered day (inclusive). For in-force policies this is the
            # simulation end / valuation date.
            "exposure_end_date": to_pandas_dates(exposure_end_excl - ONE_DAY),
            "line_of_business": pd.Categorical.from_codes(
                lob_idx, categories=cfg.lines_of_business
            ),
            "state": pd.Categorical.from_codes(state_idx, categories=cfg.states),
            "annual_premium": annual_premium,
            "active_years": active_years.astype(np.int16),
            "is_lapsed": is_lapsed,
            "created_at": now_utc(),
        }
    )


def exposure_arrays(policies: pd.DataFrame):
    """Pull the exposure window out of the policy table as datetime64[D]."""
    effective = policies["effective_date"].to_numpy().astype("datetime64[D]")
    end_excl = (
        policies["exposure_end_date"].to_numpy().astype("datetime64[D]") + ONE_DAY
    )
    return effective, end_excl


# ── Premiums ────────────────────────────────────────────────────────────────


def generate_premiums(
    policies: pd.DataFrame,
    cfg: GeneratorConfig,
    id_offset: int = 0,
) -> pd.DataFrame:
    """Generate monthly earned premium for every in-force policy-month.

    Earned premium is pro rata by days: annual_premium * days_in_force / 365.
    Partial first and last months earn partial premium. No randomness here:
    premium is fully determined by the policy's exposure window.
    """
    effective, end_excl = exposure_arrays(policies)
    annual_premium = policies["annual_premium"].to_numpy()

    first_month = effective.astype("datetime64[M]")
    last_month = (end_excl - ONE_DAY).astype("datetime64[M]")
    n_months = (last_month - first_month).astype(np.int64) + 1

    pm_pol = np.repeat(np.arange(len(policies)), n_months)
    month = first_month[pm_pol] + months(group_position(n_months))
    month_start = month.astype("datetime64[D]")
    month_end_excl = (month + ONE_MONTH).astype("datetime64[D]")

    in_force_start = np.maximum(month_start, effective[pm_pol])
    in_force_end = np.minimum(month_end_excl, end_excl[pm_pol])
    earned_days = (in_force_end - in_force_start).astype(np.int64)
    earned = (annual_premium[pm_pol] * earned_days / DAYS_PER_YEAR).round(2)

    n = len(pm_pol)
    return pd.DataFrame(
        {
            "premium_id": np.arange(id_offset + 1, id_offset + n + 1, dtype=np.int64),
            "policy_id": policies["policy_id"].to_numpy()[pm_pol],
            "earned_period": to_pandas_dates(month_start),
            "earned_year": date_parts(month_start)[0].astype(np.int16),
            "earned_days": earned_days.astype(np.int16),
            "earned_premium": earned,
            "line_of_business": policies["line_of_business"].array.take(pm_pol),
            "state": policies["state"].array.take(pm_pol),
            "created_at": now_utc(),
        }
    )
