"""
CLI for the insurance data generator.

Usage:
  python -m data_generator.cli generate --policies 10000 --years 5 --output ./data/raw
  python -m data_generator.cli generate --policies 50000 --years 7 --seed 99
"""
import time
from pathlib import Path

import typer

from data_generator.insurance_generator.config import GeneratorConfig
from data_generator.insurance_generator.generators import (
    generate_claims,
    generate_policies,
    generate_premiums,
)
from data_generator.insurance_generator.writer import write_parquet_local

app = typer.Typer(help="Insurance synthetic data generator.")


@app.command()
def generate(
    policies: int = typer.Option(10_000, "--policies", "-p", help="Number of policies"),
    years: int = typer.Option(5, "--years", "-y", help="Simulation years"),
    seed: int = typer.Option(42, "--seed", help="Random seed for reproducibility"),
    output: str = typer.Option("./data/raw", "--output", "-o", help="Output directory"),
):
    """Generate synthetic insurance policies, claims, and premiums."""
    cfg = GeneratorConfig(n_policies=policies, n_years=years, seed=seed, output_path=output)

    typer.echo(f"🏭  Generating {policies:,} policies over {years} years (seed={seed})...")
    t0 = time.time()

    typer.echo("  → Policies...")
    policy_df = generate_policies(cfg)
    path = write_parquet_local(policy_df, output, "policies")
    typer.echo(f"     ✓ {len(policy_df):,} rows  →  {path}")

    typer.echo("  → Claims...")
    claims_df = generate_claims(policy_df, cfg)
    path = write_parquet_local(claims_df, output, "claims")
    typer.echo(f"     ✓ {len(claims_df):,} rows  →  {path}")

    typer.echo("  → Premiums...")
    premiums_df = generate_premiums(policy_df, cfg)
    path = write_parquet_local(premiums_df, output, "premiums")
    typer.echo(f"     ✓ {len(premiums_df):,} rows  →  {path}")

    elapsed = time.time() - t0
    typer.echo(f"\n✅  Done in {elapsed:.1f}s")
    typer.echo(f"    Policies : {len(policy_df):,}")
    typer.echo(f"    Claims   : {len(claims_df):,}")
    typer.echo(f"    Premiums : {len(premiums_df):,}")


if __name__ == "__main__":
    app()
