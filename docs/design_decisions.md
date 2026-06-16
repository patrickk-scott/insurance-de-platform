# Design Decisions

## Why Spark *and* dbt? (The most common interview question)

These two tools serve distinct layers with a clean boundary:

- **Spark** operates on raw files in S3 (the data lake). It handles schema enforcement,
  deduplication, SCD2 slowly-changing-dimension logic on the policy table, and writing
  optimally partitioned Parquet. These are file-level, big-data operations where you
  want distributed execution and don't need a warehouse.

- **dbt** operates inside Snowflake (the warehouse). Once data is in the warehouse,
  SQL is the right tool — better optimizer, no cluster spin-up cost, readable lineage.
  Rewriting dbt transforms in PySpark would be a performance regression and a readability
  regression. The line is: Spark for the lake, dbt for the warehouse.

## Why EMR Serverless over AWS Glue?

Glue abstracts Spark behind a managed ETL service, which is convenient but reduces
control. EMR Serverless gives direct access to the Spark API, more tuning options,
and is closer to what production data engineering teams run. For a portfolio project,
showing you can configure a Spark job directly is more valuable than showing you can
use a wizard.

## Why Snowflake over Redshift?

Snowflake's separation of storage and compute, its zero-copy cloning for dev/prod isolation,
and its Snowpark Python support make it the dominant choice in modern DE stacks. Redshift
is a fine warehouse but its tight AWS coupling and older architecture make it less common
in greenfield projects. Snowflake also appears more frequently in DE job postings.

## Why synthetic data vs. a public dataset?

Public datasets are standardized — everyone's portfolio uses the same Kaggle CSV.
Synthetic data generated from actuarial distributions shows domain knowledge you can't
fake, gives you full control over volume (millions of rows to justify Spark), and lets
you build the exact schema the downstream models need. It's also a Python showpiece.

## Why not streaming (Kafka)?

The business case for real-time claims analytics doesn't justify the operational overhead
of a streaming layer for a portfolio project. Simulating incremental/CDC-style batch loads
demonstrates you understand the concept without adding Kafka cluster management as a
distraction. Kafka is mentioned as a documented stretch goal.

## Python vs. SQL boundaries

| Layer | Tool | Reason |
|-------|------|--------|
| Data generation | Python (numpy/pandas) | Distribution math, file I/O |
| Lake processing | PySpark | Distributed file ops, SCD2 |
| Warehouse modeling | dbt/SQL | Optimizer-native, readable lineage |
| Chain-ladder reserve | Python model in dbt (Snowpark) | Matrix math awkward in SQL |
| Orchestration | Python (Airflow DAGs) | Control flow, operators |
| Quality checks | Python (Great Expectations) | Schema/stats assertions |
| Dashboard | Python (Streamlit) | Keep serving layer in one language |
