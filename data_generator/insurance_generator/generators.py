"""
Core data generators: policies, claims, premiums, exposures.

Actuarial distributions used:
- Claim frequency: Poisson(λ)          — number of claims per policy per year
- Claim severity:  Lognormal(μ, σ)     — loss amount per claim
- Reporting lag:   Truncated Normal     — months from accident to report date
- Lapse:           Bernoulli(p)         — annual policy termination
"""
import uuid
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from faker import Faker

from .config import GeneratorConfig


fake = Faker()


def generate_policies(cfg: GeneratorConfig) -> pd.DataFrame:
    """Generate the master policy table."""
    rng = np.random.default_rng(cfg.seed)
    Faker.seed(cfg.seed)

    n = cfg.n_policies
    start_date = date(2019, 1, 1)

    # Random effective dates spread over first 2 years of simulation
    days_offset = rng.integers(0, 365 * 2, size=n)
    effective_dates = [start_date + timedelta(days=int(d)) for d in days_offset]

    lobs = rng.choice(
        cfg.lines_of_business,
        size=n,
        p=cfg.lob_weights,
    )
    states = rng.choice(cfg.states, size=n)

    # Annual premium: rough by LOB
    premium_map = {
        "Auto": (1200, 400),
        "Homeowners": (1800, 600),
        "Commercial Property": (8000, 3000),
        "General Liability": (5000, 2000),
    }
    premiums = np.array([
        max(100, rng.normal(premium_map[lob][0], premium_map[lob][1]))
        for lob in lobs
    ])

    policies = pd.DataFrame({
        "policy_id": [str(uuid.uuid4()) for _ in range(n)],
        "effective_date": effective_dates,
        "line_of_business": lobs,
        "state": states,
        "annual_premium": premiums.round(2),
        "policy_holder_id": [str(uuid.uuid4()) for _ in range(n)],
        "created_at": pd.Timestamp.utcnow(),
    })
    return policies


def generate_claims(
    policies: pd.DataFrame,
    cfg: GeneratorConfig,
) -> pd.DataFrame:
    """
    Generate claims for each policy-year using:
      - Poisson(λ) for claim count
      - Lognormal(μ, σ) for each loss amount
      - Truncated Normal for reporting lag (months)
    """
    rng = np.random.default_rng(cfg.seed + 1)
    rows = []

    sim_end = date(2019 + cfg.n_years, 12, 31)

    for _, policy in policies.iterrows():
        policy_start = policy["effective_date"]
        policy_end = sim_end
        current_date = policy_start
        active = True

        year = 0
        while active and current_date < policy_end:
            try:
                year_end = date(current_date.year + 1, current_date.month, current_date.day)
            except ValueError:
                # Feb 29 on non-leap year → use Feb 28
                year_end = date(current_date.year + 1, current_date.month, 28)
            year_end = min(year_end, policy_end)

            # Lapse check
            if rng.random() < cfg.annual_lapse_rate and year > 0:
                active = False
                break

            # Claim frequency: Poisson
            n_claims = rng.poisson(cfg.claim_frequency_lambda)

            for _ in range(n_claims):
                # Accident date: uniform within policy year
                span = (year_end - current_date).days
                if span <= 0:
                    continue
                accident_offset = rng.integers(0, span)
                accident_date = current_date + timedelta(days=int(accident_offset))

                # Reporting lag: truncated normal (min 0 days, mean=lag_mean months)
                lag_days = max(0, int(rng.normal(
                    cfg.lag_mean * 30, cfg.lag_std * 30
                )))
                report_date = accident_date + timedelta(days=lag_days)

                # Severity: lognormal
                loss_amount = float(rng.lognormal(
                    cfg.severity_mean_log, cfg.severity_sigma_log
                ))

                rows.append({
                    "claim_id": str(uuid.uuid4()),
                    "policy_id": policy["policy_id"],
                    "accident_date": accident_date,
                    "report_date": report_date,
                    "loss_amount": round(loss_amount, 2),
                    "accident_year": accident_date.year,
                    "line_of_business": policy["line_of_business"],
                    "state": policy["state"],
                    "status": rng.choice(
                        ["Open", "Closed", "Reopened"],
                        p=[0.30, 0.65, 0.05],
                    ),
                    "created_at": pd.Timestamp.utcnow(),
                })

            current_date = year_end
            year += 1

    return pd.DataFrame(rows)


def generate_premiums(
    policies: pd.DataFrame,
    cfg: GeneratorConfig,
) -> pd.DataFrame:
    """Generate monthly earned premium records per active policy."""
    rng = np.random.default_rng(cfg.seed + 2)
    rows = []

    sim_end = date(2019 + cfg.n_years, 12, 31)

    for _, policy in policies.iterrows():
        current = policy["effective_date"].replace(day=1)
        monthly_premium = round(policy["annual_premium"] / 12, 2)
        active = True
        month_count = 0

        while active and current <= sim_end:
            # Lapse modeled as monthly probability
            monthly_lapse = 1 - (1 - cfg.annual_lapse_rate) ** (1 / 12)
            if rng.random() < monthly_lapse and month_count > 0:
                active = False
                break

            rows.append({
                "premium_id": str(uuid.uuid4()),
                "policy_id": policy["policy_id"],
                "earned_period": current,
                "earned_premium": monthly_premium,
                "line_of_business": policy["line_of_business"],
                "state": policy["state"],
                "created_at": pd.Timestamp.utcnow(),
            })

            # Advance one month
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)
            month_count += 1

    return pd.DataFrame(rows)
