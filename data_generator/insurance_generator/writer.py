"""Output writers: local Parquet (single file or Hive-partitioned) and S3."""

import io
import os
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# Columns stored as Parquet DATE (date32) rather than TIMESTAMP, so Snowflake
# and Spark load them as DATE without a cast.
DATE_COLUMNS = {
    "effective_date",
    "exposure_end_date",
    "accident_date",
    "report_date",
    "closed_date",
    "transaction_date",
    "earned_period",
}


def to_arrow(df: pd.DataFrame) -> pa.Table:
    """Convert to an Arrow table, casting calendar-date columns to date32."""
    table = pa.Table.from_pandas(df, preserve_index=False)
    for i, f in enumerate(table.schema):
        if f.name in DATE_COLUMNS and pa.types.is_timestamp(f.type):
            table = table.set_column(i, f.name, table.column(i).cast(pa.date32()))
    return table


def write_parquet_local(df: pd.DataFrame, output_dir: str, table_name: str) -> str:
    """Write a DataFrame to one local Parquet file and return the file path."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    path = os.path.join(output_dir, f"{table_name}.parquet")
    pq.write_table(to_arrow(df), path)
    return path


def write_parquet_partitioned(
    df: pd.DataFrame,
    root: str,
    part_name: str,
    partition_col: str | None = None,
) -> None:
    """Append one batch to a Hive-partitioned dataset.

    Each batch writes its own uniquely named file(s), e.g.
    claims/accident_year=2021/part-00003.parquet, so batches never overwrite
    each other and Spark can read the whole directory as one table.
    """
    table = to_arrow(df)
    if partition_col is None:
        Path(root).mkdir(parents=True, exist_ok=True)
        pq.write_table(table, os.path.join(root, f"{part_name}.parquet"))
        return
    pq.write_to_dataset(
        table,
        root_path=root,
        partition_cols=[partition_col],
        basename_template=f"{part_name}-{{i}}.parquet",
        existing_data_behavior="overwrite_or_ignore",
    )


def write_parquet_s3(
    df: pd.DataFrame,
    bucket: str,
    prefix: str,
    table_name: str,
) -> str:
    """Write a DataFrame to S3 as Parquet (requires boto3 + AWS credentials)."""
    import boto3

    buf = io.BytesIO()
    pq.write_table(to_arrow(df), buf)
    buf.seek(0)

    key = f"{prefix}/{table_name}/{table_name}.parquet"
    boto3.client("s3").upload_fileobj(buf, bucket, key)
    return f"s3://{bucket}/{key}"
