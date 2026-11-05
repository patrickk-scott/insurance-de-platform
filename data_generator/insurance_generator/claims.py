"""
Claims: occurrence, full lifecycle, and the valuation-date cut.

Three outputs, mirroring how a real carrier's data looks:

- claims             Claim header, one row per REPORTED claim (report_date <=
                     valuation date). Status is derived from the lifecycle as
                     of the valuation date, not drawn at random.
- claim_transactions One row per financial event up to the valuation date:
                     OPEN (initial case reserve), PAYMENT (paid + reserve
                     change), CLOSE (final payment, reserve to zero),
                     REOPEN. Paid and incurred loss triangles are built from
                     this table.
- claims_truth       The simulator's answer key: every claim that occurred,
                     including IBNR (not yet reported), with its true
                     ultimate loss. A real pipeline never has this. It exists
                     to backtest the reserve estimate. Do not use it in
                     warehouse models other than an explicit backtest.

How a claim develops:
1. Ultimate loss ~ Lognormal(mu, sigma) by line of business.
2. Report date = accident + reporting lag. Claims reported after the
   valuation date are IBNR: in claims_truth only.
3. Settlement date = report + Lognormal settlement lag by LOB (long tail
   for liability).
4. 1 + Poisson(lambda) payments between report and settlement. Amounts split
   the ultimate with random weights; the last payment is on the settlement
   date and closes the claim.
5. Case reserve at report = ultimate * adequacy factor (median < 1, so
   incurred develops upward). After each payment the outstanding reserve is
   re-estimated, with the adequacy factor moving toward 1 as the claim
   matures.
6. A small share of claims reopen with additional loss.

The full lifecycle is simulated (including events after the valuation date)
and then cut. Keys are assigned before the cut, so moving the valuation date
forward later reveals new rows without renumbering existing ones.

Money is computed in integer cents so payments sum to the ultimate exactly
and every case reserve returns to exactly zero.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import GeneratorConfig
from .dates import (
    DAYS_PER_MONTH,
    STREAM_CLAIMS,
    anniversary,
    date_parts,
    days,
    group_position,
    make_rng,
    now_utc,
    to_pandas_dates,
)
from .generators import exposure_arrays

TXN_TYPES = ["OPEN", "PAYMENT", "CLOSE", "REOPEN"]
TXN_OPEN, TXN_PAYMENT, TXN_CLOSE, TXN_REOPEN = range(4)
CLAIM_STATUSES = ["Open", "Closed", "Reopened"]


@dataclass
class ClaimSet:
    claims: pd.DataFrame
    transactions: pd.DataFrame
    truth: pd.DataFrame
    # Keys used, including rows hidden by the valuation cut. Batches advance
    # their id offsets by these so keys stay unique.
    n_claim_ids: int
    n_txn_ids: int


def _lognormal_days(rng, median_months, sigma, size) -> np.ndarray:
    """Lognormal duration in whole days (at least 1) with the given median."""
    mu = np.log(np.asarray(median_months, dtype=float) * DAYS_PER_MONTH)
    return np.maximum(1, np.round(rng.lognormal(mu, sigma, size))).astype(np.int64)


def _occurrences(policies: pd.DataFrame, cfg: GeneratorConfig, rng):
    """Accident dates for every in-force policy-year (vectorized)."""
    effective, end_excl = exposure_arrays(policies)
    active_years = policies["active_years"].to_numpy().astype(np.int64)
    year, month, day = date_parts(effective)

    # Expand policies into policy-years.
    py_pol = np.repeat(np.arange(len(policies)), active_years)
    k = group_position(active_years)
    y, m, d = year[py_pol], month[py_pol], day[py_pol]
    py_start = anniversary(y, m, d, k)
    py_full_end = anniversary(y, m, d, k + 1)
    py_end = np.minimum(py_full_end, end_excl[py_pol])

    # Poisson count scaled by the in-force share of the policy year.
    exposure_days = (py_end - py_start).astype(np.int64)
    full_days = (py_full_end - py_start).astype(np.int64)
    n_claims = rng.poisson(cfg.claim_frequency_lambda * exposure_days / full_days)

    # Expand policy-years into claims; accident uniform within in-force days.
    c_py = np.repeat(np.arange(len(py_pol)), n_claims)
    accident = py_start[c_py] + days(
        np.floor(rng.random(len(c_py)) * exposure_days[c_py])
    )
    return py_pol[c_py], accident


def _lifecycle(lob_idx, report, ultimate, cfg: GeneratorConfig, rng):
    """Simulate every claim's transactions to final settlement.

    Inputs are per-claim arrays (ultimate in cents). Returns per-transaction
    arrays sorted by claim then event order, and per-claim lifecycle dates.
    """
    n = len(report)

    def by_lob(values):
        return np.asarray(values, dtype=float)[lob_idx]

    # Settlement and payment count.
    settle = report + days(
        _lognormal_days(
            rng,
            by_lob(cfg.lob_settlement_median_months),
            by_lob(cfg.lob_settlement_sigma),
            n,
        )
    )
    n_pay = 1 + np.minimum(
        rng.poisson(by_lob(cfg.lob_extra_payments_lambda)), cfg.max_payments - 1
    )

    # Initial case reserve.
    adequacy = rng.lognormal(
        np.log(cfg.case_adequacy_median), cfg.case_adequacy_sigma, n
    )
    reserve0 = np.round(ultimate * adequacy).astype(np.int64)

    # Payments: expand claims by n_pay. Payment times are uniform between
    # report and settlement, sorted within each claim: lexsort orders by
    # claim first, then by the random draw.
    pc = np.repeat(np.arange(n), n_pay)
    j = group_position(n_pay)
    u = rng.random(len(pc))
    u = u[np.lexsort((u, pc))]
    is_last = j == n_pay[pc] - 1
    span = (settle - report).astype(np.int64)[pc]
    pay_date = np.where(is_last, settle[pc], report[pc] + days(np.floor(u * span)))

    # Split the ultimate across payments with random weights. Round down all
    # but the last; the last takes the remainder, so the sum is exact.
    w = rng.exponential(size=len(pc))
    share = w / np.bincount(pc, weights=w, minlength=n)[pc]
    amount = np.floor(ultimate[pc] * share).astype(np.int64)
    others = np.bincount(pc, weights=np.where(is_last, 0, amount), minlength=n)
    amount = np.where(
        is_last, ultimate[pc] - np.round(others).astype(np.int64)[pc], amount
    )

    # Paid to date after each payment (cumulative sum within claim).
    csum = np.cumsum(amount)
    first = np.repeat(np.cumsum(n_pay) - n_pay, n_pay)
    paid_to_date = csum - (csum[first] - amount[first])

    # Outstanding case reserve after each payment. The adequacy factor decays
    # toward 1 as the remaining share shrinks: adequacy ** remaining_share.
    remaining = ultimate[pc] - paid_to_date
    remaining_share = remaining / np.maximum(ultimate[pc], 1)
    outstanding = np.round(remaining * adequacy[pc] ** remaining_share).astype(np.int64)
    outstanding_before = np.empty_like(outstanding)
    outstanding_before[1:] = outstanding[:-1]
    outstanding_before[j == 0] = reserve0[pc[j == 0]]
    reserve_change = outstanding - outstanding_before

    # Reopens: extra loss after settlement, then a second close.
    ri = np.flatnonzero(rng.random(n) < cfg.reopen_prob)
    reopen = settle[ri] + days(
        _lognormal_days(rng, cfg.reopen_lag_median_months, 0.6, len(ri))
    )
    extra = np.maximum(
        1,
        np.round(
            ultimate[ri]
            * rng.lognormal(np.log(cfg.reopen_severity_median), 0.5, len(ri))
        ),
    ).astype(np.int64)
    reopen_reserve = np.round(extra * adequacy[ri]).astype(np.int64)
    reclose = reopen + days(
        _lognormal_days(rng, cfg.reclose_lag_median_months, 0.6, len(ri))
    )

    # Assemble all events. seq orders events within a claim (OPEN first).
    zeros_n, zeros_r = np.zeros(n, np.int64), np.zeros(len(ri), np.int64)
    t_claim = np.concatenate([np.arange(n), pc, ri, ri])
    t_date = np.concatenate([report, pay_date, reopen, reclose])
    t_seq = np.concatenate([zeros_n, 1 + j, zeros_r + 1_000, zeros_r + 1_001])
    t_type = np.concatenate(
        [
            zeros_n + TXN_OPEN,
            np.where(is_last, TXN_CLOSE, TXN_PAYMENT),
            zeros_r + TXN_REOPEN,
            zeros_r + TXN_CLOSE,
        ]
    )
    t_paid = np.concatenate([zeros_n, amount, zeros_r, extra])
    t_reserve = np.concatenate(
        [reserve0, reserve_change, reopen_reserve, -reopen_reserve]
    )

    order = np.lexsort((t_seq, t_claim))
    txns = {
        "claim": t_claim[order],
        "date": t_date[order],
        "type": t_type[order],
        "paid": t_paid[order],
        "reserve_change": t_reserve[order],
    }

    total_extra = np.zeros(n, np.int64)
    total_extra[ri] = extra
    reopen_date = np.full(n, np.datetime64("NaT", "D"), dtype="datetime64[D]")
    reclose_date = reopen_date.copy()
    reopen_date[ri] = reopen
    reclose_date[ri] = reclose
    lifecycle = {
        "settle": settle,
        "reopen": reopen_date,
        "reclose": reclose_date,
        "ultimate": ultimate + total_extra,
    }
    return txns, lifecycle


def _status_as_of(lc: dict, valuation: np.datetime64):
    """Claim status and closed date as of the valuation date."""
    closed = lc["settle"] <= valuation
    reopened = lc["reopen"] <= valuation  # NaT compares False
    reclosed = lc["reclose"] <= valuation
    status = np.where(~closed, 0, np.where(reopened & ~reclosed, 2, 1))
    closed_date = np.where(
        status == 1,
        np.where(reclosed, lc["reclose"], lc["settle"]),
        np.datetime64("NaT", "D"),
    ).astype("datetime64[D]")
    return status, closed_date


def generate_claims(
    policies: pd.DataFrame,
    cfg: GeneratorConfig,
    id_offset: int = 0,
    txn_id_offset: int = 0,
    rng: np.random.Generator | None = None,
) -> ClaimSet:
    """Generate claim headers, transactions, and the truth table."""
    rng = rng or make_rng(cfg.seed, 0, STREAM_CLAIMS)
    valuation = np.datetime64(cfg.valuation, "D")

    c_pol, accident = _occurrences(policies, cfg, rng)
    n = len(c_pol)
    lob_codes = policies["line_of_business"].cat.codes.to_numpy()[c_pol]

    lag = np.maximum(0, rng.normal(cfg.lag_mean * 30, cfg.lag_std * 30, size=n))
    report = accident + days(lag)
    mu = np.asarray(cfg.lob_severity_mu)[lob_codes]
    sigma = np.asarray(cfg.lob_severity_sigma)[lob_codes]
    ultimate = np.maximum(1, np.round(rng.lognormal(mu, sigma) * 100)).astype(np.int64)

    txns, lc = _lifecycle(lob_codes, report, ultimate, cfg, rng)

    claim_id = np.arange(id_offset + 1, id_offset + n + 1, dtype=np.int64)
    policy_id = policies["policy_id"].to_numpy()[c_pol]
    accident_year = date_parts(accident)[0].astype(np.int16)
    lob = policies["line_of_business"].array.take(c_pol)
    state = policies["state"].array.take(c_pol)
    created_at = now_utc()

    truth = pd.DataFrame(
        {
            "claim_id": claim_id,
            "policy_id": policy_id,
            "accident_date": to_pandas_dates(accident),
            "report_date": to_pandas_dates(report),
            "accident_year": accident_year,
            "line_of_business": lob,
            "is_reported": report <= valuation,
            "ultimate_loss": lc["ultimate"] / 100,
            "created_at": created_at,
        }
    )

    reported = report <= valuation
    status, closed_date = _status_as_of(lc, valuation)
    claims = pd.DataFrame(
        {
            "claim_id": claim_id[reported],
            "policy_id": policy_id[reported],
            "accident_date": to_pandas_dates(accident[reported]),
            "report_date": to_pandas_dates(report[reported]),
            "accident_year": accident_year[reported],
            "line_of_business": lob[reported],
            "state": state[reported],
            "status": pd.Categorical.from_codes(
                status[reported], categories=CLAIM_STATUSES
            ),
            "closed_date": to_pandas_dates(closed_date[reported]),
            "created_at": created_at,
        }
    )

    n_txn = len(txns["claim"])
    txn_id = np.arange(txn_id_offset + 1, txn_id_offset + n_txn + 1, dtype=np.int64)
    visible = txns["date"] <= valuation
    tc = txns["claim"][visible]
    transactions = pd.DataFrame(
        {
            "transaction_id": txn_id[visible],
            "claim_id": claim_id[tc],
            "transaction_date": to_pandas_dates(txns["date"][visible]),
            "transaction_year": date_parts(txns["date"][visible])[0].astype(np.int16),
            "transaction_type": pd.Categorical.from_codes(
                txns["type"][visible], categories=TXN_TYPES
            ),
            "paid_amount": txns["paid"][visible] / 100,
            "case_reserve_change": txns["reserve_change"][visible] / 100,
            "accident_year": accident_year[tc],
            "line_of_business": lob.take(tc),
            "created_at": created_at,
        }
    )
    return ClaimSet(claims, transactions, truth, n_claim_ids=n, n_txn_ids=n_txn)
