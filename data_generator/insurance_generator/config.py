"""Generator configuration — driven by CLI args or .env."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GeneratorConfig:
    # Volume
    n_policies: int = 10_000
    n_years: int = 5
    seed: int = 42

    # Output
    output_path: str = "./data/raw"
    output_format: str = "parquet"  # parquet | csv

    # Claim distributions
    claim_frequency_lambda: float = 0.15   # Poisson λ: avg claims per policy/year
    severity_mean_log: float = 8.5         # lognormal μ (log scale)
    severity_sigma_log: float = 1.2        # lognormal σ (log scale)

    # Reporting lag (months): how long after accident until claim is reported
    lag_mean: float = 3.0
    lag_std: float = 2.5

    # Lapse / persistency
    annual_lapse_rate: float = 0.08        # 8% annual lapse probability

    # Lines of business
    lines_of_business: list = field(default_factory=lambda: [
        "Auto", "Homeowners", "Commercial Property", "General Liability"
    ])
    lob_weights: list = field(default_factory=lambda: [0.40, 0.30, 0.20, 0.10])

    # States
    states: list = field(default_factory=lambda: [
        "TX", "CA", "FL", "NY", "IL", "PA", "OH", "GA", "NC", "MI"
    ])
