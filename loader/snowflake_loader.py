"""
Load generated Parquet into Snowflake INSURANCE_PLATFORM.RAW.

    python -m loader.snowflake_loader --source data/scaled
    python -m loader.snowflake_loader --source data/scaled --tables claims premiums

Per table:
  1. Discover the local dataset (Hive-partitioned Parquet) and read its schema.
     The Snowflake table definition is derived from that schema, so there is
     no hand-maintained DDL to drift out of sync with the generator.
  2. PUT the files to an internal stage, keeping the partition folders.
  3. COPY INTO a fresh <TABLE>__LOAD table.
  4. Check the loaded row count against the local count.
  5. ALTER TABLE ... SWAP WITH the live table, then drop the old copy.
     The swap is atomic: readers see the old data or the new data, never an
     empty or half-loaded table.

This is a full refresh, matching the generator, which rewrites the whole
dataset each run. Incremental loads come later.

Hive partition columns (accident_year, ...) are not stored inside the
Parquet files; they exist only in folder names like accident_year=2021/.
COPY rebuilds them from METADATA$FILENAME, or they would be lost.

Two lineage columns are added to every table: _SOURCE_FILE (the staged file
each row came from) and _LOADED_AT.

Runs as the LOADER role on LOADING_WH (from .env) and suspends the warehouse
when done.
"""

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds

# Local table folder -> Hive partition column (None = unpartitioned).
TABLES: dict[str, str | None] = {
    "policies": None,
    "claims": "accident_year",
    "claim_transactions": "transaction_year",
    "premiums": "earned_year",
    "claims_truth": "accident_year",
}

SCHEMA = "RAW"
STAGE = f"{SCHEMA}.LANDING"
FILE_FORMAT = f"{SCHEMA}.PARQUET_FMT"
LINEAGE_COLUMNS = [
    ("_SOURCE_FILE", "VARCHAR", "METADATA$FILENAME"),
    ("_LOADED_AT", "TIMESTAMP_TZ", "CURRENT_TIMESTAMP()"),
]


@dataclass
class Column:
    name: str  # lower case, as in the Parquet files
    sf_type: str
    expr: str  # SELECT expression over the staged file


@dataclass
class TableSpec:
    table: str  # local folder name
    local_dir: Path
    partition_col: str | None
    columns: list[Column]
    local_rows: int
    partition_dirs: list[Path] = field(default_factory=list)

    @property
    def target(self) -> str:
        return f"{SCHEMA}.{self.table.upper()}"

    @property
    def load_table(self) -> str:
        return f"{self.target}__LOAD"


# ── Pure helpers (unit tested, no Snowflake needed) ─────────────────────────


def snowflake_type(t: pa.DataType) -> str:
    """Map an Arrow type to the Snowflake column type used in RAW."""
    if pa.types.is_dictionary(t):
        return snowflake_type(t.value_type)
    if pa.types.is_boolean(t):
        return "BOOLEAN"
    if pa.types.is_integer(t):
        return "NUMBER(38,0)"
    if pa.types.is_floating(t):
        return "FLOAT"
    if pa.types.is_date(t):
        return "DATE"
    if pa.types.is_timestamp(t):
        return "TIMESTAMP_TZ" if t.tz else "TIMESTAMP_NTZ"
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        return "VARCHAR"
    raise TypeError(f"no Snowflake mapping for Arrow type {t}")


def column_expr(name: str, sf_type: str, partition_col: str | None) -> str:
    """SELECT expression for one column of a staged Parquet file."""
    if name == partition_col:
        # Value lives in the path: .../accident_year=2021/part-00000-0.parquet
        return (
            f"REGEXP_SUBSTR(METADATA$FILENAME, '{name}=([^/]+)', 1, 1, 'e')"
            f"::{sf_type}"
        )
    return f'$1:"{name}"::{sf_type}'


def discover(source: Path, table: str) -> TableSpec:
    """Read schema, row count and partition folders of one local table."""
    local_dir = source / table
    if not local_dir.is_dir():
        raise FileNotFoundError(f"{local_dir} not found; run the generator first")
    partition_col = TABLES[table]
    dataset = ds.dataset(
        local_dir, format="parquet", partitioning="hive" if partition_col else None
    )
    columns = []
    for f in dataset.schema:
        sf_type = snowflake_type(f.type)
        columns.append(
            Column(f.name, sf_type, column_expr(f.name, sf_type, partition_col))
        )
    if partition_col and partition_col not in dataset.schema.names:
        raise ValueError(f"{table}: partition column {partition_col} not in paths")
    partition_dirs = (
        sorted(p for p in local_dir.iterdir() if p.is_dir() and "=" in p.name)
        if partition_col
        else [local_dir]
    )
    return TableSpec(
        table, local_dir, partition_col, columns, dataset.count_rows(), partition_dirs
    )


def put_statements(spec: TableSpec) -> list[str]:
    """One PUT per folder, keeping the partition folder name in the stage path.
    Parquet is already compressed, so AUTO_COMPRESS is off."""
    statements = []
    for d in spec.partition_dirs:
        sub = "" if d == spec.local_dir else f"{d.name}/"
        statements.append(
            f"PUT 'file://{d.resolve()}/*.parquet' '@{STAGE}/{spec.table}/{sub}' "
            "AUTO_COMPRESS = FALSE OVERWRITE = TRUE PARALLEL = 8"
        )
    return statements


def create_load_table_sql(spec: TableSpec) -> str:
    cols = [f"{c.name.upper()} {c.sf_type}" for c in spec.columns]
    cols += [f"{name} {sf_type}" for name, sf_type, _ in LINEAGE_COLUMNS]
    return (
        f"CREATE OR REPLACE TABLE {spec.load_table} (\n    "
        + ",\n    ".join(cols)
        + "\n)"
    )


def copy_sql(spec: TableSpec) -> str:
    names = [c.name.upper() for c in spec.columns] + [n for n, _, _ in LINEAGE_COLUMNS]
    exprs = [c.expr for c in spec.columns] + [e for _, _, e in LINEAGE_COLUMNS]
    return (
        f"COPY INTO {spec.load_table} ({', '.join(names)})\n"
        f"FROM (\n    SELECT\n        " + ",\n        ".join(exprs) + "\n"
        f"    FROM @{STAGE}/{spec.table}/\n)\n"
        f"FILE_FORMAT = (FORMAT_NAME = '{FILE_FORMAT}')\n"
        "PATTERN = '.*[.]parquet'\n"
        "ON_ERROR = ABORT_STATEMENT"
    )


# ── Snowflake side ──────────────────────────────────────────────────────────


def connect():
    import snowflake.connector  # imported here so unit tests need no connector
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        authenticator="SNOWFLAKE_JWT",
        private_key_file=os.path.expanduser(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]),
        role=os.environ["SNOWFLAKE_LOADER_ROLE"],
        warehouse=os.environ["SNOWFLAKE_LOADER_WAREHOUSE"],
        database=os.environ["SNOWFLAKE_DATABASE"],
        schema=SCHEMA,
        session_parameters={"QUERY_TAG": "insurance-platform:loader"},
    )


def use_only_primary_role(cur) -> None:
    # Defense in depth for DEFAULT_SECONDARY_ROLES (see snowflake/setup.sql):
    # without this, a LOADER session would also carry TRANSFORMER's rights.
    cur.execute("USE SECONDARY ROLES NONE")


def ensure_stage(cur) -> None:
    # USE_LOGICAL_TYPE: read Parquet DATE/TIMESTAMP as dates and timestamps,
    # not raw integers.
    cur.execute(
        f"CREATE FILE FORMAT IF NOT EXISTS {FILE_FORMAT} "
        "TYPE = PARQUET USE_LOGICAL_TYPE = TRUE"
    )
    cur.execute(f"CREATE STAGE IF NOT EXISTS {STAGE} FILE_FORMAT = {FILE_FORMAT}")


def load_table(cur, spec: TableSpec) -> dict:
    timings = {}
    t0 = time.perf_counter()
    cur.execute(f"REMOVE @{STAGE}/{spec.table}/")  # drop stale files from last run
    for stmt in put_statements(spec):
        cur.execute(stmt)
    timings["put_s"] = time.perf_counter() - t0

    t1 = time.perf_counter()
    cur.execute(create_load_table_sql(spec))
    cur.execute(copy_sql(spec))
    cur.execute(f"SELECT COUNT(*) FROM {spec.load_table}")
    loaded = cur.fetchone()[0]
    timings["copy_s"] = time.perf_counter() - t1
    if loaded != spec.local_rows:
        cur.execute(f"DROP TABLE IF EXISTS {spec.load_table}")
        raise RuntimeError(
            f"{spec.target}: loaded {loaded:,} rows but local data has "
            f"{spec.local_rows:,}; live table left unchanged"
        )

    # Atomic replace. First run: create an empty target to swap with.
    cur.execute(f"CREATE TABLE IF NOT EXISTS {spec.target} LIKE {spec.load_table}")
    cur.execute(f"ALTER TABLE {spec.load_table} SWAP WITH {spec.target}")
    cur.execute(f"DROP TABLE {spec.load_table}")  # now holds the previous data
    timings["rows"] = loaded
    return timings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", default="data/scaled", type=Path)
    parser.add_argument("--tables", nargs="+", default=list(TABLES), choices=TABLES)
    args = parser.parse_args(argv)

    specs = [discover(args.source, t) for t in args.tables]
    with connect() as conn:
        cur = conn.cursor()
        warehouse = os.environ["SNOWFLAKE_LOADER_WAREHOUSE"]
        try:
            use_only_primary_role(cur)
            ensure_stage(cur)
            for spec in specs:
                print(f"{spec.target}: {spec.local_rows:,} rows from {spec.local_dir}")
                t = load_table(cur, spec)
                print(
                    f"  loaded {t['rows']:,} rows "
                    f"(put {t['put_s']:.1f}s, copy {t['copy_s']:.1f}s), swapped in"
                )
        finally:
            # Stop billing now rather than after the 60s auto-suspend. Errors
            # if already suspended; never let that mask a real load failure.
            try:
                cur.execute(f"ALTER WAREHOUSE {warehouse} SUSPEND")
            except Exception as e:  # noqa: BLE001
                print(f"  (warehouse not suspended: {str(e).splitlines()[0]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
