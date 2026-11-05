"""
CLI for the insurance data generator.

Usage:
  python -m data_generator.cli generate --policies 10000 --years 5
  python -m data_generator.cli generate-scaled --policies 2000000 --batch-size 100000

Note: Typer collapses a single-command app and rejects the subcommand name,
so keep at least two commands registered.
"""

import time

import typer

from data_generator.insurance_generator.batched import ScaledResult, generate_scaled
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
    years: int = typer.Option(5, "--years", "-y", help="Calendar years simulated"),
    seed: int = typer.Option(42, "--seed", help="Random seed for reproducibility"),
    output: str = typer.Option("./data/raw", "--output", "-o", help="Output directory"),
):
    """Generate small dev data: one Parquet file per table, built in memory."""
    cfg = GeneratorConfig(
        n_policies=policies, n_years=years, seed=seed, output_path=output
    )
    typer.echo(f"Generating {policies:,} policies over {years} years (seed={seed})")
    t0 = time.perf_counter()

    policy_df = generate_policies(cfg)
    claims_df = generate_claims(policy_df, cfg)
    premiums_df = generate_premiums(policy_df, cfg)

    for name, df in (
        ("policies", policy_df),
        ("claims", claims_df),
        ("premiums", premiums_df),
    ):
        path = write_parquet_local(df, output, name)
        typer.echo(f"  {name:<9} {len(df):>12,} rows  ->  {path}")

    typer.echo(f"Done in {time.perf_counter() - t0:.1f}s")


@app.command("generate-scaled")
def generate_scaled_cmd(
    policies: int = typer.Option(
        2_000_000, "--policies", "-p", help="Number of policies"
    ),
    years: int = typer.Option(5, "--years", "-y", help="Calendar years simulated"),
    seed: int = typer.Option(42, "--seed", help="Random seed for reproducibility"),
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
    cfg = GeneratorConfig(
        n_policies=policies, n_years=years, seed=seed, output_path=output
    )
    typer.echo(
        f"Generating {policies:,} policies over {years} years (seed={seed}), "
        f"{batch_size:,} per batch -> {output}"
    )
    t0 = time.perf_counter()

    def progress(done: int, total: int, r: ScaledResult) -> None:
        typer.echo(
            f"  batch {done}/{total}: {r.policies:,} policies, {r.claims:,} claims, "
            f"{r.premiums:,} premiums ({time.perf_counter() - t0:.1f}s)"
        )

    try:
        result = generate_scaled(cfg, output, batch_size, overwrite, progress)
    except FileExistsError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)

    typer.echo(
        f"Done in {time.perf_counter() - t0:.1f}s: {result.policies:,} policies, "
        f"{result.claims:,} claims, {result.premiums:,} premiums"
    )


if __name__ == "__main__":
    app()
