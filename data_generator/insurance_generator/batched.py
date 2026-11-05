"""
Batched, bounded-memory generation to Hive-partitioned Parquet.

Vectorization fixes speed. Batching fixes memory: instead of building one
giant DataFrame for 2M policies (~100M premium rows), we generate
`batch_size` policies at a time, write that batch's policies, claims and
premiums to disk, and drop them before the next batch. Peak memory is set by
batch_size, not by the total row count.

Measured on an Apple Silicon Mac (5 years, ~43 premium rows per policy,
before claim transactions were added):
    batch 100k: 2M policies peak 2.3 GB, 4M policies peak 2.4 GB (flat)
    batch 250k: 2M policies peak 5.5 GB, grows with total. The macOS allocator
                keeps a high-water mark for very large arrays; not a true
                leak (it is returned later), but it defeats the bound.
Smaller batches cost no speed here; they only make smaller files, which the
Spark silver job compacts anyway.

Output layout (Spark discovers the partition columns from the paths):
    <root>/policies/part-00000.parquet
    <root>/claims/accident_year=2021/part-00000-0.parquet
    <root>/claim_transactions/transaction_year=2021/part-00000-0.parquet
    <root>/premiums/earned_year=2021/part-00000-0.parquet
    <root>/claims_truth/accident_year=2021/part-00000-0.parquet   (answer key)

claim_transactions is partitioned by transaction_year, not accident_year:
new activity lands in the newest partition, which is what an incremental
load wants to read.

Determinism: batch b draws from make_rng(seed, b, stream), so the same seed
and batch_size always produce the same data. A different batch_size gives a
different (equally valid) random draw.
"""

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .claims import generate_claims
from .config import GeneratorConfig
from .dates import STREAM_CLAIMS, STREAM_POLICIES, make_rng
from .generators import generate_policies, generate_premiums
from .writer import write_parquet_partitioned

TABLES = ("policies", "claims", "claim_transactions", "premiums", "claims_truth")


@dataclass
class ScaledResult:
    policies: int = 0
    claims: int = 0  # reported as of the valuation date
    claim_transactions: int = 0  # up to the valuation date
    premiums: int = 0
    claims_truth: int = 0  # all claims incl. IBNR
    batches: int = 0
    # Keys consumed, including rows hidden by the valuation cut.
    claim_ids: int = 0
    transaction_ids: int = 0


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
        cs = generate_claims(
            policies,
            cfg,
            id_offset=result.claim_ids,
            txn_id_offset=result.transaction_ids,
            rng=make_rng(cfg.seed, b, STREAM_CLAIMS),
        )
        premiums = generate_premiums(policies, cfg, id_offset=result.premiums)

        out = {
            "policies": (policies, None),
            "claims": (cs.claims, "accident_year"),
            "claim_transactions": (cs.transactions, "transaction_year"),
            "premiums": (premiums, "earned_year"),
            "claims_truth": (cs.truth, "accident_year"),
        }
        for table, (df, partition_col) in out.items():
            write_parquet_partitioned(df, str(root / table), part, partition_col)
            setattr(result, table, getattr(result, table) + len(df))
        result.claim_ids += cs.n_claim_ids
        result.transaction_ids += cs.n_txn_ids
        result.batches += 1
        if on_batch:
            on_batch(b + 1, n_batches, result)

        # Release this batch before building the next one.
        del policies, cs, premiums, out

    return result
