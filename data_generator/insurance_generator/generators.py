"""
Core data generators: policies, claims, premiums.

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
- Claim frequency: Poisson(lambda * exposure fraction) per policy-year
- Claim severity:  Lognormal(mu, sigma) loss amount per claim
- Reporting lag:   Normal clipped at 0  days from accident to report

Keys are integer surrogate keys (int64), not UUID strings. Pass `id_offset`
to keep keys unique when generating in batches.
"""

import numpy as np
import pandas as pd

from .config import GeneratorConfig

DAYS_PER_YEAR = 365

# Date offsets always carry an explicit unit. NumPy 2.5+ deprecates adding
# bare integers to datetime64 and will turn it into an error.
ONE_DAY = np.timedelta64(1, "D")
ONE_MONTH = np.timedelta64(1, "M")


def _days(n: np.ndarray) -> np.ndarray:
    return np.asarray(n, dtype=np.int64).astype("timedelta64[D]")


def _months(n: np.ndarray) -> np.ndarray:
    return np.asarray(n, dtype=np.int64).astype("timedelta64[M]")


# Independent random streams per table, so changing how claims are drawn does
# not shift the policies that came before it.
STREAM_POLICIES = 0
STREAM_CLAIMS = 1


def make_rng(seed: int, batch: int, stream: int) -> np.random.Generator:
    """Deterministic RNG for (seed, batch, stream). Same inputs, same numbers."""
    return np.random.default_rng([seed, batch, stream])


# ── Date helpers (vectorized over datetime64[D] arrays) ─────────────────────


def _date_parts(d: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Split a datetime64[D] array into integer year, month, day arrays."""
    months = d.astype("datetime64[M]")
    year = d.astype("datetime64[Y]").astype(np.int64) + 1970
    month = months.astype(np.int64) % 12 + 1
    day = (d - months.astype("datetime64[D]")).astype(np.int64) + 1
    return year, month, day


def _is_leap(year: np.ndarray) -> np.ndarray:
    return (year % 4 == 0) & ((year % 100 != 0) | (year % 400 == 0))


def _make_dates(year: np.ndarray, month: np.ndarray, day: np.ndarray) -> np.ndarray:
    """Build a datetime64[D] array from integer year, month, day arrays."""
    ym = (year - 1970).astype("datetime64[Y]").astype("datetime64[M]") + _months(
        month - 1
    )
    return ym.astype("datetime64[D]") + _days(day - 1)


def _anniversary(year, month, day, k) -> np.ndarray:
    """k-th policy anniversary. A Feb 29 effective date renews on Feb 28
    in non-leap years."""
    target_year = year + k
    adj_day = np.where((month == 2) & (day == 29) & ~_is_leap(target_year), 28, day)
    return _make_dates(target_year, month, adj_day)


def _group_position(counts: np.ndarray) -> np.ndarray:
    """For counts [2, 3] return [0, 1, 0, 1, 2]: each child row's position
    within its parent. Vectorized equivalent of groupby().cumcount()."""
    starts = np.cumsum(counts) - counts
    return np.arange(counts.sum()) - np.repeat(starts, counts)


def _to_pandas_dates(d: np.ndarray) -> np.ndarray:
    return d.astype("datetime64[s]")


def _now_utc() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


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
    writing_days = min(cfg.writing_years * DAYS_PER_YEAR, sim_days)

    effective = sim_start + _days(rng.integers(0, writing_days, size=n))

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
    year, month, day = _date_parts(effective)
    max_years = cfg.sim_end.year - year + 1
    if cfg.annual_lapse_rate > 0:
        years_to_lapse = rng.geometric(cfg.annual_lapse_rate, size=n)
    else:
        years_to_lapse = np.full(n, np.iinfo(np.int32).max)
    active_years = np.minimum(years_to_lapse, max_years)
    is_lapsed = years_to_lapse < max_years

    exposure_end_excl = np.minimum(
        _anniversary(year, month, day, active_years), sim_end_excl
    )

    policy_id = np.arange(id_offset + 1, id_offset + n + 1, dtype=np.int64)
    return pd.DataFrame(
        {
            "policy_id": policy_id,
            # One holder per policy for now; kept as its own key for the
            # customer dimension later.
            "policy_holder_id": policy_id,
            "effective_date": _to_pandas_dates(effective),
            # Last covered day (inclusive). For in-force policies this is the
            # simulation end / valuation date.
            "exposure_end_date": _to_pandas_dates(exposure_end_excl - ONE_DAY),
            "line_of_business": pd.Categorical.from_codes(
                lob_idx, categories=cfg.lines_of_business
            ),
            "state": pd.Categorical.from_codes(state_idx, categories=cfg.states),
            "annual_premium": annual_premium,
            "active_years": active_years.astype(np.int16),
            "is_lapsed": is_lapsed,
            "created_at": _now_utc(),
        }
    )


def _exposure_arrays(policies: pd.DataFrame):
    """Pull the exposure window out of the policy table as datetime64[D]."""
    effective = policies["effective_date"].to_numpy().astype("datetime64[D]")
    end_excl = (
        policies["exposure_end_date"].to_numpy().astype("datetime64[D]") + ONE_DAY
    )
    return effective, end_excl


# ── Claims ──────────────────────────────────────────────────────────────────


def generate_claims(
    policies: pd.DataFrame,
    cfg: GeneratorConfig,
    id_offset: int = 0,
    rng: np.random.Generator | None = None,
) -> pd.DataFrame:
    """Generate claims for every in-force policy-year.

    Claim count per policy-year is Poisson(lambda * exposure fraction), so a
    policy-year cut short by the valuation date gets proportionally fewer
    claims. Accident dates fall uniformly inside the in-force part of the year.
    """
    rng = rng or make_rng(cfg.seed, 0, STREAM_CLAIMS)

    effective, end_excl = _exposure_arrays(policies)
    active_years = policies["active_years"].to_numpy().astype(np.int64)
    year, month, day = _date_parts(effective)

    # Expand policies into policy-years.
    py_pol = np.repeat(np.arange(len(policies)), active_years)
    k = _group_position(active_years)
    y, m, d = year[py_pol], month[py_pol], day[py_pol]
    py_start = _anniversary(y, m, d, k)
    py_full_end = _anniversary(y, m, d, k + 1)
    py_end = np.minimum(py_full_end, end_excl[py_pol])

    exposure_days = (py_end - py_start).astype(np.int64)
    full_days = (py_full_end - py_start).astype(np.int64)
    n_claims = rng.poisson(cfg.claim_frequency_lambda * exposure_days / full_days)

    # Expand policy-years into claims.
    c_py = np.repeat(np.arange(len(py_pol)), n_claims)
    c_pol = py_pol[c_py]
    n = len(c_py)

    accident = py_start[c_py] + _days(np.floor(rng.random(n) * exposure_days[c_py]))
    lag_days = np.maximum(
        0, rng.normal(cfg.lag_mean * 30, cfg.lag_std * 30, size=n)
    ).astype(np.int64)
    report = accident + _days(lag_days)
    loss = rng.lognormal(cfg.severity_mean_log, cfg.severity_sigma_log, size=n).round(2)
    status_idx = rng.choice(len(cfg.claim_statuses), size=n, p=cfg.claim_status_weights)

    return pd.DataFrame(
        {
            "claim_id": np.arange(id_offset + 1, id_offset + n + 1, dtype=np.int64),
            "policy_id": policies["policy_id"].to_numpy()[c_pol],
            "accident_date": _to_pandas_dates(accident),
            "report_date": _to_pandas_dates(report),
            "loss_amount": loss,
            "accident_year": _date_parts(accident)[0].astype(np.int16),
            "line_of_business": policies["line_of_business"].array.take(c_pol),
            "state": policies["state"].array.take(c_pol),
            "status": pd.Categorical.from_codes(
                status_idx, categories=cfg.claim_statuses
            ),
            "created_at": _now_utc(),
        }
    )


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
    effective, end_excl = _exposure_arrays(policies)
    annual_premium = policies["annual_premium"].to_numpy()

    first_month = effective.astype("datetime64[M]")
    last_month = (end_excl - ONE_DAY).astype("datetime64[M]")
    n_months = (last_month - first_month).astype(np.int64) + 1

    pm_pol = np.repeat(np.arange(len(policies)), n_months)
    month = first_month[pm_pol] + _months(_group_position(n_months))
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
            "earned_period": _to_pandas_dates(month_start),
            "earned_year": _date_parts(month_start)[0].astype(np.int16),
            "earned_days": earned_days.astype(np.int16),
            "earned_premium": earned,
            "line_of_business": policies["line_of_business"].array.take(pm_pol),
            "state": policies["state"].array.take(pm_pol),
            "created_at": _now_utc(),
        }
    )
