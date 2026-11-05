"""Vectorized date and indexing helpers shared by the generators.

All dates are NumPy datetime64[D] arrays. Offsets always carry an explicit
unit (NumPy 2.5+ deprecates adding bare integers to datetime64).
"""

import numpy as np
import pandas as pd

DAYS_PER_YEAR = 365
DAYS_PER_MONTH = 365 / 12

ONE_DAY = np.timedelta64(1, "D")
ONE_MONTH = np.timedelta64(1, "M")


def days(n: np.ndarray) -> np.ndarray:
    return np.asarray(n, dtype=np.int64).astype("timedelta64[D]")


def months(n: np.ndarray) -> np.ndarray:
    return np.asarray(n, dtype=np.int64).astype("timedelta64[M]")


# Independent random streams per table, so changing how claims are drawn does
# not shift the policies that came before it.
STREAM_POLICIES = 0
STREAM_CLAIMS = 1


def make_rng(seed: int, batch: int, stream: int) -> np.random.Generator:
    """Deterministic RNG for (seed, batch, stream). Same inputs, same numbers."""
    return np.random.default_rng([seed, batch, stream])


# ── Date helpers (vectorized over datetime64[D] arrays) ─────────────────────


def date_parts(d: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Split a datetime64[D] array into integer year, month, day arrays."""
    months = d.astype("datetime64[M]")
    year = d.astype("datetime64[Y]").astype(np.int64) + 1970
    month = months.astype(np.int64) % 12 + 1
    day = (d - months.astype("datetime64[D]")).astype(np.int64) + 1
    return year, month, day


def is_leap(year: np.ndarray) -> np.ndarray:
    return (year % 4 == 0) & ((year % 100 != 0) | (year % 400 == 0))


def make_dates(year: np.ndarray, month: np.ndarray, day: np.ndarray) -> np.ndarray:
    """Build a datetime64[D] array from integer year, month, day arrays."""
    ym = (year - 1970).astype("datetime64[Y]").astype("datetime64[M]") + months(
        month - 1
    )
    return ym.astype("datetime64[D]") + days(day - 1)


def anniversary(year, month, day, k) -> np.ndarray:
    """k-th policy anniversary. A Feb 29 effective date renews on Feb 28
    in non-leap years."""
    target_year = year + k
    adj_day = np.where((month == 2) & (day == 29) & ~is_leap(target_year), 28, day)
    return make_dates(target_year, month, adj_day)


def group_position(counts: np.ndarray) -> np.ndarray:
    """For counts [2, 3] return [0, 1, 0, 1, 2]: each child row's position
    within its parent. Vectorized equivalent of groupby().cumcount()."""
    starts = np.cumsum(counts) - counts
    return np.arange(counts.sum()) - np.repeat(starts, counts)


def to_pandas_dates(d: np.ndarray) -> np.ndarray:
    return d.astype("datetime64[s]")


def now_utc() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")
