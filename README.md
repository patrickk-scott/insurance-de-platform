# Insurance Claims & Reserving Analytics Platform

A batch data pipeline for property and casualty insurance data. It generates
a synthetic book of policies, claims and premiums, loads it into Snowflake,
and models it with dbt into loss development triangles and a chain ladder
reserve estimate that is backtested against the true ultimate losses.

I built this to combine my actuarial and insurance software background with
data engineering. The reserving logic is the core of the project.

## Architecture

```
Built:    [Python generator] -> [Parquet] -> [Loader] -> [Snowflake RAW] -> [dbt: staging -> intermediate -> marts]
Planned:  [S3 bronze] -> [PySpark silver] -> [Snowflake]   orchestrated by Airflow, dashboard on the marts
```

**Stack:** Python 3.12 (NumPy, PyArrow) · Snowflake · dbt · GitHub Actions.
Planned: AWS S3 · PySpark on EMR Serverless · Apache Airflow · Terraform ·
Great Expectations · Streamlit.

## What is built

- **Synthetic data generator** (`data_generator/`). Vectorized NumPy,
  batched to Hive-partitioned Parquet. 2 million policies with full claim
  lifecycles in about 9 seconds at about 2.5 GB peak memory. Poisson
  frequency, lognormal severity by line, lapse at anniversaries, case
  reserves that develop toward ultimate, reopens, and a valuation date that
  hides claims not reported yet. Writes a separate answer key with each
  claim's true ultimate loss.
- **Snowflake setup as code** (`snowflake/`). Idempotent SQL for roles,
  warehouses, grants, a resource monitor and a key-pair service user, plus a
  smoke test that checks what each role must not be able to do.
- **Loader** (`loader/`). Derives table schemas from Parquet, stages and
  copies, checks row counts, and swaps tables atomically.
- **dbt models** (`dbt_project/`). Staging views, a long-format claim
  development model, paid and incurred loss triangles, age-to-age factors,
  chain ladder reserves (case, IBNR, total unpaid) and a backtest against
  the true ultimates. 58 models and tests pass in `dbt build`.
- **CI** (GitHub Actions). ruff, black and pytest on every pull request.

## Results

Chain ladder estimate of ultimate loss vs the generator's true ultimate,
all accident years combined:

| Line | Incurred method | Paid method |
|------|----------------:|------------:|
| All lines | +0.18% | +0.39% |
| General Liability | +0.30% | +2.10% |

The dbt chain ladder is cross-checked against an independent Python
implementation: incurred ultimates match to the cent, and paid ultimates
differ only by the tail factor, by design. See [`docs/design_decisions.md`](docs/design_decisions.md) for how
the tail factor was chosen and where the remaining error comes from.

## Business metrics

| Metric | Layer | Description | Status |
|--------|-------|-------------|--------|
| Loss development triangle | Marts / actuarial | Cumulative paid and incurred by accident year and age | Built |
| Chain ladder reserve | Marts / actuarial | Ultimate, case reserve, IBNR by accident year | Built |
| Reserve backtest | Marts / actuarial | Estimate vs true ultimate | Built |
| Loss ratio | Marts / core | Incurred losses / earned premium | Planned |
| Claim frequency | Marts / core | Claims per exposure year | Planned |
| Claim severity | Marts / core | Average loss per claim | Planned |
| Policy persistency | Marts / core | Share of policies renewing | Planned |

## Project structure

```
insurance-de-platform/
├── data_generator/   Synthetic data generator (package, CLI, tests)
├── loader/           Parquet to Snowflake RAW loader (with tests)
├── snowflake/        setup.sql, teardown.sql, setup guide
├── dbt_project/      dbt models, macros and tests
├── airflow/dags/     Pipeline DAG (skeleton)
├── scripts/          Snowflake smoke test
└── docs/             Design decisions
```

## Running it

Requires Python 3.12 and, for the warehouse steps, a Snowflake account.

```bash
git clone https://github.com/patrickk-scott/insurance-de-platform.git
cd insurance-de-platform
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# Tests
pytest

# Small dataset: one Parquet file per table in data/raw
python -m data_generator.cli generate --policies 10000

# Full dataset: batched, Hive-partitioned Parquet in data/scaled
python -m data_generator.cli generate-scaled --policies 2000000 --overwrite
```

Snowflake setup, key-pair auth and `.env` are covered in
[`snowflake/README.md`](snowflake/README.md). Then:

```bash
pip install "snowflake-connector-python>=3.10" "dbt-snowflake>=1.12,<1.13"
set -a; source .env; set +a
python scripts/snowflake_smoke_test.py
python -m loader.snowflake_loader --source data/scaled
cd dbt_project && dbt build --profiles-dir .
```

## Design decisions

[`docs/design_decisions.md`](docs/design_decisions.md) covers the tool
boundaries, data design, the reserving method choices, the Snowflake
security model, and known limitations.
