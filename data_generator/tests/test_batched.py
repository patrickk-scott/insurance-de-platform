"""Tests for batched generation and the CLI."""

import pyarrow as pa
import pyarrow.dataset as ds
import pytest
from typer.testing import CliRunner

from data_generator.cli import app
from data_generator.insurance_generator.batched import generate_scaled
from data_generator.insurance_generator.config import GeneratorConfig


def _read(root, table):
    return ds.dataset(root / table, format="parquet", partitioning="hive").to_table()


@pytest.fixture
def scaled(tmp_path):
    cfg = GeneratorConfig(n_policies=2_500, n_years=4, seed=1)
    # 2,500 / 1,000 -> 3 batches, last one partial.
    result = generate_scaled(cfg, str(tmp_path), batch_size=1_000)
    return tmp_path, result


def test_row_counts_on_disk_match(scaled):
    root, result = scaled
    assert result.batches == 3
    assert _read(root, "policies").num_rows == result.policies == 2_500
    assert _read(root, "claims").num_rows == result.claims
    assert _read(root, "premiums").num_rows == result.premiums


def test_keys_unique_across_batches(scaled):
    root, _ = scaled
    for table, key in (
        ("policies", "policy_id"),
        ("claims", "claim_id"),
        ("premiums", "premium_id"),
    ):
        col = _read(root, table).column(key).to_pandas()
        assert col.is_unique, f"{table}.{key} repeats across batches"


def test_hive_partitions_and_types(scaled):
    root, _ = scaled
    assert sorted(p.name for p in (root / "claims").iterdir())[0].startswith(
        "accident_year="
    )
    assert any((root / "premiums").glob("earned_year=*/part-*.parquet"))
    schema = _read(root, "claims").schema
    assert schema.field("accident_date").type == pa.date32()
    assert schema.field("claim_id").type == pa.int64()


def test_refuses_to_append_without_overwrite(scaled):
    root, result = scaled
    cfg = GeneratorConfig(n_policies=2_500, n_years=4, seed=1)
    with pytest.raises(FileExistsError):
        generate_scaled(cfg, str(root), batch_size=1_000)
    rerun = generate_scaled(cfg, str(root), batch_size=1_000, overwrite=True)
    assert rerun == result  # same seed and batch size: same data
    assert _read(root, "policies").num_rows == 2_500  # replaced, not appended


def test_cli_has_both_commands(tmp_path):
    runner = CliRunner()
    small = runner.invoke(
        app, ["generate", "--policies", "200", "--output", str(tmp_path / "raw")]
    )
    assert small.exit_code == 0, small.output
    assert (tmp_path / "raw" / "claims.parquet").exists()

    big = runner.invoke(
        app,
        ["generate-scaled", "--policies", "300", "--batch-size", "100"]
        + ["--output", str(tmp_path / "scaled")],
    )
    assert big.exit_code == 0, big.output
    assert "Done" in big.output
