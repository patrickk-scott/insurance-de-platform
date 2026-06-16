"""Output writer: local Parquet or S3."""
import os
from pathlib import Path

import pandas as pd


def write_parquet_local(df: pd.DataFrame, output_dir: str, table_name: str) -> str:
    """Write a DataFrame to local Parquet and return the file path."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    path = os.path.join(output_dir, f"{table_name}.parquet")
    df.to_parquet(path, index=False, engine="pyarrow")
    return path


def write_parquet_s3(
    df: pd.DataFrame,
    bucket: str,
    prefix: str,
    table_name: str,
    partition_cols: list | None = None,
) -> str:
    """Write a DataFrame to S3 as Parquet (requires boto3 + AWS credentials)."""
    import boto3
    import pyarrow as pa
    import pyarrow.parquet as pq
    import io

    table = pa.Table.from_pandas(df)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    buf.seek(0)

    s3 = boto3.client("s3")
    key = f"{prefix}/{table_name}/{table_name}.parquet"
    s3.upload_fileobj(buf, bucket, key)
    return f"s3://{bucket}/{key}"
