"""
Batched, bounded-memory generation to Hive-partitioned Parquet.

Vectorization fixes speed. Batching fixes memory: instead of building one
giant DataFrame for 2M policies (~100M premium rows), we generate
`batch_size` policies at a time, write that batch's policies, claims and
premiums to disk, and drop them before the next batch. Peak memory is set by
batch_size, not by the total row count.

Measured on an Apple Silicon Mac (5 years, ~43 premium rows per policy):
    batch 100k: 2M policies peak 2.3 GB, 4M policies peak 2.4 GB (flat)
    batch 250k: 2M policies peak 5.5 GB, grows with total. The macOS allocator
                keeps a high-water mark for very large arrays; not a true
                leak (it is returned later), but it defeats the bound.
Smaller batches cost no speed here; they only make smaller files, which the
Spark silver job compacts anyway.

Output layout (Spark discovers the partition columns from the paths):
    <root>/policies/part-00000.parquet
    <root>/claims/accident_year=2021/part-00000-0.parquet
    <root>/premiums/earned_year=2021/part-00000-0.parquet

Determinism: batch b draws from make_rng(seed, b, stream), so the same seed
and batch_size always produce the same data. A different batch_size gives a
different (equally valid) random draw.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import GeneratorConfig
from .generators import (
    STREAM_CLAIMS,
    STREAM_POLICIES,
    generate_claims,
    generate_policies,
    generate_premiums,
    make_rng,
)
from .writer import write_parquet_partitioned

TABLES = ("policies", "claims", "premiums")


@dataclass
class ScaledResult:
    policies: int = 0
    claims: int = 0
    premiums: int = 0
    batches: int = 0


def generate_scaled(
    cfg: GeneratorConfig,
    output_dir: str,
    batch_size: int = 100_000,
    overwrite: bool = False,
    on_batch: Callable[[int, int, ScaledResult], None] | None = None,
) -> ScaledResult:
    """Generate cfg.n_policies policies in batches and stream them to disk."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    root = Path(output_dir)
    existing = [root / t for t in TABLES if (root / t).exists()]
    if existing:
        if not overwrite:
            raise FileExistsError(
                f"{root} already has generated tables; pass overwrite=True "
                "(--overwrite) to replace them. Appending would duplicate keys."
            )
        for path in existing:
            shutil.rmtree(path)

    result = ScaledResult()
    n_batches = -(-cfg.n_policies // batch_size)  # ceiling division

    for b in range(n_batches):
        n = min(batch_size, cfg.n_policies - result.policies)
        part = f"part-{b:05d}"

        policies = generate_policies(
            cfg,
            n=n,
            id_offset=result.policies,
            rng=make_rng(cfg.seed, b, STREAM_POLICIES),
        )
        claims = generate_claims(
            policies,
            cfg,
            id_offset=result.claims,
            rng=make_rng(cfg.seed, b, STREAM_CLAIMS),
        )
        premiums = generate_premiums(policies, cfg, id_offset=result.premiums)

        write_parquet_partitioned(policies, str(root / "policies"), part)
        write_parquet_partitioned(claims, str(root / "claims"), part, "accident_year")
        write_parquet_partitioned(premiums, str(root / "premiums"), part, "earned_year")

        result.policies += len(policies)
        result.claims += len(claims)
        result.premiums += len(premiums)
        result.batches += 1
        if on_batch:
            on_batch(b + 1, n_batches, result)

        # Release this batch before building the next one.
        del policies, claims, premiums

    return result
