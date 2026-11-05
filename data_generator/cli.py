"""
CLI for the insurance data generator.

Usage:
  python -m data_generator.cli generate --policies 10000
  python -m data_generator.cli generate-scaled --policies 2000000 --overwrite

Note: Typer collapses a single-command app and rejects the subcommand name,
so keep at least two commands registered.
"""

import time
from datetime import datetime

import typer

from data_generator.insurance_generator.batched import (
    TABLES,
    ScaledResult,
    generate_scaled,
)
from data_generator.insurance_generator.claims import generate_claims
from data_generator.insurance_generator.config import GeneratorConfig
from data_generator.insurance_generator.generators import (
    generate_policies,
    generate_premiums,
)
from data_generator.insurance_generator.writer import write_parquet_local

app = typer.Typer(help="Insurance synthetic data generator.")

YEARS_OPT = typer.Option(10, "--years", "-y", help="Calendar years simulated")
START_OPT = typer.Option(2016, "--start-year", help="First simulated year")
SEED_OPT = typer.Option(42, "--seed", help="Random seed for reproducibility")
VALUATION_OPT = typer.Option(
    None,
    "--valuation-date",
    formats=["%Y-%m-%d"],
    help="As-of date (default: last simulated day). Later claims/transactions "
    "are hidden.",
)


def _config(policies, years, start_year, seed, valuation, output) -> GeneratorConfig:
    return GeneratorConfig(
        n_policies=policies,
        n_years=years,
        start_year=start_year,
        seed=seed,
        valuation_date=valuation.date() if valuation else None,
        output_path=output,
    )


def _header(cfg: GeneratorConfig) -> str:
    return (
        f"{cfg.n_policies:,} policies, {cfg.start_year}-{cfg.sim_end.year}, "
        f"valued {cfg.valuation}, seed={cfg.seed}"
    )


@app.command()
def generate(
    policies: int = typer.Option(10_000, "--policies", "-p", help="Number of policies"),
    years: int = YEARS_OPT,
    start_year: int = START_OPT,
    seed: int = SEED_OPT,
    valuation: datetime | None = VALUATION_OPT,
    output: str = typer.Option("./data/raw", "--output", "-o", help="Output directory"),
):
    """Generate small dev data: one Parquet file per table, built in memory."""
    cfg = _config(policies, years, start_year, seed, valuation, output)
    typer.echo(f"Generating {_header(cfg)}")
    t0 = time.perf_counter()

    policy_df = generate_policies(cfg)
    cs = generate_claims(policy_df, cfg)
    tables = {
        "policies": policy_df,
        "claims": cs.claims,
        "claim_transactions": cs.transactions,
        "premiums": generate_premiums(policy_df, cfg),
        "claims_truth": cs.truth,
    }
    for name, df in tables.items():
        path = write_parquet_local(df, output, name)
        typer.echo(f"  {name:<18} {len(df):>12,} rows  ->  {path}")

    typer.echo(f"Done in {time.perf_counter() - t0:.1f}s")


@app.command("generate-scaled")
def generate_scaled_cmd(
    policies: int = typer.Option(
        2_000_000, "--policies", "-p", help="Number of policies"
    ),
    years: int = YEARS_OPT,
    start_year: int = START_OPT,
    seed: int = SEED_OPT,
    valuation: datetime | None = VALUATION_OPT,
    batch_size: int = typer.Option(
        100_000, "--batch-size", "-b", help="Policies per batch (bounds memory)"
    ),
    output: str = typer.Option(
        "./data/scaled", "--output", "-o", help="Output root directory"
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Replace existing generated tables"
    ),
):
    """Generate large data in batches to Hive-partitioned Parquet."""
    cfg = _config(policies, years, start_year, seed, valuation, output)
    typer.echo(f"Generating {_header(cfg)}, {batch_size:,} per batch -> {output}")
    t0 = time.perf_counter()

    def progress(done: int, total: int, r: ScaledResult) -> None:
        typer.echo(
            f"  batch {done}/{total}: {r.policies:,} policies, {r.claims:,} claims "
            f"({time.perf_counter() - t0:.1f}s)"
        )

    try:
        result = generate_scaled(cfg, output, batch_size, overwrite, progress)
    except FileExistsError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e

    typer.echo(f"Done in {time.perf_counter() - t0:.1f}s")
    for table in TABLES:
        typer.echo(f"  {table:<18} {getattr(result, table):>12,} rows")


if __name__ == "__main__":
    app()
