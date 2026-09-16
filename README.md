# Insurance Claims & Reserving Analytics Platform

An end-to-end data engineering portfolio project demonstrating a production-style batch pipeline
for insurance claims processing, actuarial reserving, and business intelligence.

## Architecture

```
[Python Generator] → [S3 Bronze] → [PySpark Silver] → [Snowflake] → [dbt Gold] → [Dashboard]
                                                              ↑
                                                       [Airflow Orchestration]
```

**Stack:** AWS S3 · PySpark (EMR Serverless) · Snowflake · dbt · Apache Airflow · Terraform · Python 3.12

## Key Features

- Synthetic insurance claims data generation with actuarial distributions
  (Poisson claim frequency, lognormal severity, reporting lag, lapse behavior)
- PySpark silver layer: dedup, SCD2 policy dimension, Parquet partitioning
- dbt dimensional model: staging → intermediate → marts (core + actuarial)
- **Loss-development triangle with chain-ladder reserve estimation** (actuarial differentiator)
- Great Expectations data quality framework
- Full infrastructure-as-code via Terraform
- GitHub Actions CI: linting, tests, dbt compile check

## Project Structure

```
insurance-de-platform/
├── data_generator/        # Python package: synthetic data generation
├── spark_jobs/            # PySpark jobs (bronze → silver)
├── dbt_project/           # dbt models, tests, macros
├── airflow/               # DAGs and operators
├── terraform/             # IaC: S3, EMR, Snowflake
├── dashboard/             # Streamlit app
├── data_quality/          # Great Expectations suite
├── docs/                  # Architecture diagrams, design decisions
└── scripts/               # Dev utility scripts
```

## Getting Started

### Prerequisites
- Python 3.12+
- AWS account with appropriate IAM permissions
- Snowflake trial account (free)
- Terraform >= 1.5

### Local Setup

```bash
git clone https://github.com/YOUR_USERNAME/insurance-de-platform.git
cd insurance-de-platform

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy and fill in environment variables
cp .env.example .env

# Run the data generator (local mode)
python -m data_generator.cli generate --policies 10000 --years 5 --output ./data/raw
```

### Infrastructure

```bash
cd terraform/environments/dev
terraform init
terraform plan
terraform apply
```

## Business Metrics Produced

| Metric | Layer | Description |
|--------|-------|-------------|
| Loss Ratio | Marts/Core | Incurred losses / earned premium |
| Claim Frequency | Marts/Core | Claims per exposure unit |
| Claim Severity | Marts/Core | Average paid loss per claim |
| Policy Persistency | Marts/Core | % policies renewing each period |
| Loss Development Triangle | Marts/Actuarial | Cumulative paid losses by AY × dev period |
| Chain-Ladder Reserve | Marts/Actuarial | IBNR reserve estimate by accident year |

## Design Decisions

See [`docs/design_decisions.md`](docs/design_decisions.md) for a full writeup of architectural
tradeoffs.
