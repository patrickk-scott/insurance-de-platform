"""Generator configuration, driven by CLI args. All knobs live here.

Per-line-of-business settings are lists aligned with `lines_of_business`.
"""

from dataclasses import dataclass, field
from datetime import date


@dataclass
class GeneratorConfig:
    # Volume
    n_policies: int = 10_000
    n_years: int = 10  # calendar years simulated, starting at start_year
    seed: int = 42
    start_year: int = 2016

    # Policies become effective uniformly over the first N years. None means
    # every year, so each accident year has new business (a balanced triangle).
    writing_years: int | None = None

    # Valuation date: the "as of" date of the data. Claims reported after it,
    # and transactions after it, are not visible. None means the last day of
    # the simulation.
    valuation_date: date | None = None

    # Output
    output_path: str = "./data/raw"

    # Claim frequency: Poisson rate, claims per exposure-year
    claim_frequency_lambda: float = 0.15

    # Reporting lag (months): accident to report. Normal clipped at 0 days.
    lag_mean: float = 3.0
    lag_std: float = 2.5

    # Lapse / persistency: probability a policy does not renew at each anniversary
    annual_lapse_rate: float = 0.08

    # Lines of business
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

    # Severity: ultimate loss per claim ~ Lognormal(mu, sigma) by LOB.
    # mu is set so expected loss ratio is ~65% at the default frequency:
    #   E[severity] = 0.65 * mean premium / 0.15,  mu = ln(E) - sigma^2 / 2
    lob_severity_sigma: list = field(default_factory=lambda: [1.0, 1.1, 1.3, 1.4])
    lob_severity_mu: list = field(default_factory=lambda: [8.06, 8.36, 9.61, 9.00])

    # Settlement lag (months): report to final payment, Lognormal by LOB.
    # Short tail (property) to long tail (liability).
    lob_settlement_median_months: list = field(
        default_factory=lambda: [6.0, 4.0, 9.0, 30.0]
    )
    lob_settlement_sigma: list = field(default_factory=lambda: [0.8, 0.6, 0.8, 1.0])

    # Payments per claim: 1 + Poisson(lambda) by LOB, capped at max_payments.
    lob_extra_payments_lambda: list = field(
        default_factory=lambda: [1.0, 0.5, 1.5, 3.0]
    )
    max_payments: int = 12

    # Case reserve adequacy: initial case reserve = ultimate * factor,
    # factor ~ Lognormal(median, sigma). Median < 1 means adjusters
    # under-reserve at first, so incurred develops upward. The factor moves
    # toward 1 as the claim is paid down.
    case_adequacy_median: float = 0.85
    case_adequacy_sigma: float = 0.35

    # Reopens: share of claims reopened after closing, delay to reopen,
    # additional loss as a share of the original ultimate, time to re-close.
    reopen_prob: float = 0.05
    reopen_lag_median_months: float = 6.0
    reopen_severity_median: float = 0.25
    reclose_lag_median_months: float = 4.0

    # States
    states: list = field(
        default_factory=lambda: ["TX", "CA", "FL", "NY", "IL"]
        + ["PA", "OH", "GA", "NC", "MI"]
    )

    @property
    def sim_start(self) -> date:
        return date(self.start_year, 1, 1)

    @property
    def sim_end(self) -> date:
        """Last day of exposure in the simulation (inclusive)."""
        return date(self.start_year + self.n_years - 1, 12, 31)

    @property
    def valuation(self) -> date:
        return self.valuation_date or self.sim_end
