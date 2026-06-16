"""
Main pipeline DAG: Insurance Claims & Reserving Analytics Platform.

Schedule: Daily at 06:00 UTC
Flow:
  generate_data → spark_bronze_to_silver → load_to_snowflake → dbt_run → dbt_test → quality_gate
"""
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator

default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="insurance_claims_pipeline",
    default_args=default_args,
    description="End-to-end insurance claims and reserving analytics pipeline",
    schedule_interval="0 6 * * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["insurance", "claims", "reserving"],
) as dag:

    generate_data = BashOperator(
        task_id="generate_synthetic_data",
        bash_command=(
            "python -m data_generator.cli generate "
            "--policies {{ var.value.n_policies }} "
            "--years {{ var.value.n_years }} "
            "--output s3://{{ var.value.s3_bucket }}/raw/"
        ),
        doc_md="Generate synthetic insurance data and land to S3 bronze zone.",
    )

    spark_silver = BashOperator(
        task_id="spark_bronze_to_silver",
        bash_command=(
            "spark-submit "
            "--master {{ var.value.spark_master }} "
            "spark_jobs/bronze_to_silver/main.py "
            "--input s3://{{ var.value.s3_bucket }}/raw/ "
            "--output s3://{{ var.value.s3_bucket }}/silver/"
        ),
        doc_md="PySpark job: clean, dedup, SCD2 policy dim, write Parquet silver layer.",
    )

    load_snowflake = BashOperator(
        task_id="load_to_snowflake",
        bash_command="python scripts/load_snowflake.py --source silver",
        doc_md="COPY INTO Snowflake RAW schema from S3 silver Parquet files.",
    )

    dbt_run = BashOperator(
        task_id="dbt_run",
        bash_command=(
            "dbt run "
            "--profiles-dir dbt_project "
            "--project-dir dbt_project "
            "--target prod"
        ),
        doc_md="Run all dbt models: staging → intermediate → marts.",
    )

    dbt_test = BashOperator(
        task_id="dbt_test",
        bash_command=(
            "dbt test "
            "--profiles-dir dbt_project "
            "--project-dir dbt_project "
            "--target prod"
        ),
        doc_md="Run dbt data tests; failure here blocks downstream consumers.",
    )

    quality_gate = BashOperator(
        task_id="great_expectations_checkpoint",
        bash_command="python data_quality/run_checkpoints.py",
        doc_md="Run Great Expectations checkpoints on marts output.",
    )

    # Pipeline order
    generate_data >> spark_silver >> load_snowflake >> dbt_run >> dbt_test >> quality_gate
