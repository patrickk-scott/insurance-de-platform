"""Unit tests for the loader's pure parts (no Snowflake connection)."""

import pyarrow as pa
import pytest

from data_generator.insurance_generator.batched import generate_scaled
from data_generator.insurance_generator.config import GeneratorConfig
from loader.snowflake_loader import (
    TABLES,
    copy_sql,
    create_load_table_sql,
    discover,
    put_statements,
    snowflake_type,
)


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    root = tmp_path_factory.mktemp("scaled")
    generate_scaled(
        GeneratorConfig(n_policies=1_500, seed=3), str(root), batch_size=500
    )
    return root


@pytest.mark.parametrize(
    ("arrow_type", "expected"),
    [
        (pa.int64(), "NUMBER(38,0)"),
        (pa.int16(), "NUMBER(38,0)"),
        (pa.float64(), "FLOAT"),
        (pa.bool_(), "BOOLEAN"),
        (pa.date32(), "DATE"),
        (pa.timestamp("us", tz="UTC"), "TIMESTAMP_TZ"),
        (pa.timestamp("s"), "TIMESTAMP_NTZ"),
        (pa.string(), "VARCHAR"),
        (pa.dictionary(pa.int8(), pa.string()), "VARCHAR"),
    ],
)
def test_snowflake_type(arrow_type, expected):
    assert snowflake_type(arrow_type) == expected


def test_unknown_type_fails_loudly():
    with pytest.raises(TypeError):
        snowflake_type(pa.list_(pa.int64()))


@pytest.mark.parametrize("table", list(TABLES))
def test_discover_every_generated_table(source, table):
    spec = discover(source, table)
    assert spec.local_rows > 0
    names = [c.name for c in spec.columns]
    assert len(names) == len(set(names))
    if spec.partition_col:
        assert spec.partition_col in names
        assert all("=" in d.name for d in spec.partition_dirs)


def test_partition_column_comes_from_path(source):
    """accident_year is not inside the claims files, only in folder names."""
    spec = discover(source, "claims")
    col = {c.name: c for c in spec.columns}["accident_year"]
    assert "METADATA$FILENAME" in col.expr
    assert "accident_year=" in col.expr
    other = {c.name: c for c in spec.columns}["claim_id"]
    assert other.expr == '$1:"claim_id"::NUMBER(38,0)'


def test_copy_columns_line_up_with_table(source):
    """COPY's column list and SELECT list must have the same length and order."""
    spec = discover(source, "claim_transactions")
    ddl = create_load_table_sql(spec)
    sql = copy_sql(spec)
    col_list = sql.split("(", 1)[1].split(")", 1)[0].split(", ")
    select_list = sql.split("SELECT", 1)[1].split("FROM @", 1)[0].strip().split(",\n")
    assert len(col_list) == len(select_list) == len(spec.columns) + 2
    for name in col_list:
        assert name in ddl
    assert col_list[-2:] == ["_SOURCE_FILE", "_LOADED_AT"]
    assert spec.load_table == "RAW.CLAIM_TRANSACTIONS__LOAD"


def test_put_keeps_partition_folders(source):
    spec = discover(source, "premiums")
    puts = put_statements(spec)
    assert len(puts) == len(spec.partition_dirs) > 1
    assert all("AUTO_COMPRESS = FALSE" in p for p in puts)
    assert any("'@RAW.LANDING/premiums/earned_year=" in p for p in puts)
    flat = put_statements(discover(source, "policies"))
    assert len(flat) == 1 and "'@RAW.LANDING/policies/'" in flat[0]


def test_missing_table_has_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="run the generator"):
        discover(tmp_path, "claims")
