"""Generator configuration, driven by CLI args. All knobs live here."""

from dataclasses import dataclass, field
from datetime import date


@dataclass
class GeneratorConfig:
    # Volume
    n_policies: int = 10_000
    n_years: int = 5  # number of calendar years simulated, starting at start_year
    seed: int = 42
    start_year: int = 2019

    # Policies are written (become effective) during the first N years only,
    # so later years show a seasoned, lapsing book.
    writing_years: int = 2

    # Output
    output_path: str = "./data/raw"

    # Claim distributions
    claim_frequency_lambda: float = 0.15  # Poisson rate: claims per exposure-year
    severity_mean_log: float = 8.5  # lognormal mu (log scale)
    severity_sigma_log: float = 1.2  # lognormal sigma (log scale)

    # Reporting lag (months): how long after the accident the claim is reported.
    # Normal(mean, std) clipped at 0 days.
    lag_mean: float = 3.0
    lag_std: float = 2.5

    # Lapse / persistency: probability a policy does not renew at each anniversary
    annual_lapse_rate: float = 0.08

    # Lines of business, with (mean, std) of annual premium for each
    lines_of_business: list = field(
        default_factory=lambda: [
            "Auto",
            "Homeowners",
            "Commercial Property",
            "General Liability",
        ]
    )
    lob_weights: list = field(default_factory=lambda: [0.40, 0.30, 0.20, 0.10])
    lob_premium_mean: list = field(default_factory=lambda: [1200, 1800, 8000, 5000])
    lob_premium_std: list = field(default_factory=lambda: [400, 600, 3000, 2000])
    min_annual_premium: float = 100.0

    # States
    states: list = field(
        default_factory=lambda: [
            "TX",
            "CA",
            "FL",
            "NY",
            "IL",
            "PA",
            "OH",
            "GA",
            "NC",
            "MI",
        ]
    )

    # Claim status mix
    claim_statuses: list = field(default_factory=lambda: ["Open", "Closed", "Reopened"])
    claim_status_weights: list = field(default_factory=lambda: [0.30, 0.65, 0.05])

    @property
    def sim_start(self) -> date:
        return date(self.start_year, 1, 1)

    @property
    def sim_end(self) -> date:
        """Last day of the simulation (inclusive). Also the valuation date."""
        return date(self.start_year + self.n_years - 1, 12, 31)
