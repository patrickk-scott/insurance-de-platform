# Design Decisions

This document records the main choices I made building this platform, the
alternatives I considered, and why I went the way I did.

## Architecture and tool boundaries

### Spark for the lake, dbt for the warehouse

I split processing into two layers with one rule: Spark works on files,
dbt works on tables.

- **Spark (planned)** runs on raw files in S3: schema enforcement,
  deduplication, a Type 2 slowly changing dimension for policies, and
  partitioned Parquet output. These are file-level operations on data that
  is not in a warehouse yet, so distributed execution fits.
- **dbt** runs inside Snowflake. Once data is in the warehouse, SQL gets the
  warehouse optimizer, needs no cluster to start up, and gives readable
  lineage. Moving those transforms into PySpark would make them slower to
  run and harder to read.

### Python and SQL by layer

| Layer | Tool | Reason | Status |
|-------|------|--------|--------|
| Data generation | Python (NumPy, PyArrow) | Distribution math, vectorized, streams Parquet to disk | Built |
| Loading | Python (Snowflake connector) | Stage, COPY, verify, atomic swap | Built |
| Warehouse modeling | dbt / SQL | Optimizer-native, readable lineage | Built |
| Chain ladder reserve | dbt / SQL | Aggregations and window functions (see below) | Built |
| Lake processing | PySpark | Distributed file work, SCD2 | Planned |
| Orchestration | Airflow | Dependencies, retries, timeouts | Planned |
| Data quality | Great Expectations | Statistical checks beyond dbt tests | Planned |
| Dashboard | Streamlit | Serving layer stays in Python | Planned |

## Tool choices

### Snowflake over Redshift

Storage and compute are separate, compute bills per second, and warehouses
suspend themselves when idle. For a batch pipeline that runs for minutes a
day, that means I pay only for those minutes. Zero-copy cloning also makes
dev and prod copies cheap. Redshift would work, but its provisioned
clusters are a worse fit for short, bursty batch work.

### EMR Serverless over AWS Glue (planned)

Glue wraps Spark in a managed ETL service with its own abstractions
(DynamicFrames, job bookmarks). EMR Serverless runs plain Spark, so the job
code is portable and I control the Spark configuration directly. Both are
pay-per-use, so cost did not decide it.

### Batch, not streaming

Claims reserving is a periodic process: reserves are set at month, quarter
and year end. A streaming layer (Kafka) would add a lot of operations work
for no business benefit here. I plan to show change handling with
incremental batch loads instead. The generator already supports this: a
later valuation date adds rows without changing or renumbering earlier ones.

## Data

### Synthetic data instead of a public dataset

I wrote a generator instead of using a public dataset for three reasons:

1. **Control of volume.** I can generate 2 million policies or 20 million
   and measure how each stage scales.
2. **Control of schema.** The data has what reserving needs: a claim
   lifecycle with payments, case reserve changes, closes and reopens, and a
   valuation date that hides claims not yet reported.
3. **An answer key.** The generator knows every claim's true ultimate loss,
   including claims not reported yet (IBNR). It writes that to a separate
   `claims_truth` table, so I can backtest the reserve estimate against the
   truth. Real insurers only learn the truth years later.

The distributions are actuarial: Poisson claim frequency scaled by exposure,
lognormal severity calibrated to about a 65% loss ratio, lognormal
settlement lags from short tail (Homeowners, median 4 months) to long tail
(General Liability, median 30 months), case reserves that start at about
85% of ultimate and converge as claims are paid, and a 5% reopen rate.

### One exposure window per policy

Each policy carries one exposure window (`effective_date` to
`exposure_end_date`, with `active_years` and `is_lapsed`). Claims and
premiums are both derived from it. A lapsed policy therefore stops having
claims and stops earning premium on the same day. Tests check this. If
each table had its own lapse logic, they could disagree, and loss ratios
would be wrong in a way that is hard to see.

### Integer surrogate keys

I started with UUID strings and switched to int64 keys. At scale the string
columns took several times the memory of an integer column and dominated
the generator's footprint. Each batch takes an `id_offset`, so keys stay
unique across batches. Keys are assigned before the valuation cut, so
valuing the same book at a later date adds rows and never renumbers
existing ones. That is what makes incremental loads safe.

### Vectorized and batched generation

Scaling has two separate limits: speed and memory.

- **Speed:** the first version built rows in Python loops. Rewriting it as
  vectorized NumPy took 10,000 policies from 3.84 s to 0.027 s.
- **Memory:** one large in-memory DataFrame does not scale. The generator
  writes Hive-partitioned Parquet in batches of 100,000 policies. 2 million
  policies with full claim lifecycles take about 9 s with peak memory about
  2.5 GB, and peak memory stays flat as the total grows. Batches of 250,000
  pushed peak memory to 5.5 GB, so 100,000 is the default.

## Warehouse modeling (dbt)

### Staging: rename and cast only

One staging view per source table, with no joins and no business logic.
Every downstream model reads staging, never `RAW`. Staging tests guard the
contract with the generator: unique and not-null keys, foreign keys that
resolve, and accepted values for categorical columns. If the generator
changes shape, these fail before any mart is built on bad data.

### Money as NUMBER, ratios as FLOAT

Money is cast to `NUMBER(18,2)`. I measured the FLOAT error first: summing
$13.56B of payments as FLOAT was off by about $0.000002, so error size was
not the reason. The reason is exactness. On NUMBER, a closed claim's case
reserve sums to exactly zero and the triangle reconciles to the
transactions with `=`. Several tests rely on that.

Ratios go the other way. Snowflake's `NUMBER / NUMBER` keeps a fixed scale,
and the truncation compounded through the development factor product: my
chain ladder ultimates were off by up to $52 against a Python reference
implementation. Dividing as FLOAT fixed it, and all 20 ultimates now match
the reference to $0.00.

### Long-format triangle with a fixed age axis

`int_claim_development` stores one row per line of business, measure (paid
or incurred), accident year and development age, instead of a pivoted
triangle. Paid, incurred, each line and the all-lines total all go through
the same SQL, and a new accident year adds rows, not columns. Pivoting is
for display.

The age axis is fixed to 1..N with a generated spine. A fully settled old
accident year has no payments at late ages. Without the spine those cells
are missing instead of zero, and that accident year silently drops out of
the age-to-age factors. A test checks every expected cell exists exactly
once.

### Chain ladder in SQL, not a Python model

My first plan was a Snowpark Python model, on the assumption that the
chain ladder needs matrix math. When I wrote it out, it needs only grouped
sums for volume-weighted age-to-age factors and a running product for the
cumulative factors. SQL has no `PRODUCT()` aggregate, so I use
`exp(sum(ln(f)))` over a window, and a test enforces that every factor is
positive. Keeping it in SQL means one language in the warehouse layer and
no Python runtime in Snowflake.

### Tail factor

The oldest accident year is at age 10, so there is no observed development
after that. With a tail of 1.0, General Liability paid losses were
underestimated: ultimate by 2.4% and the reserve by 7%, because liability
claims are still paying at age 10. The true remaining paid development is
1.050. I compared two options:

- a fixed tail parameter, which I would have to choose by hand, or
- incurred over paid at the last age, per line. If incurred is fully
  developed, what paid still has to grow is the outstanding case reserve.
  This gives 1.046 for General Liability and 1.000 for the short-tail lines.

I chose incurred over paid. It comes from the data, it is per line, and it
lands close to the truth. The incurred tail is a dbt variable that defaults
to 1.0 (the measured truth is 1.000, and 1.004 for General Liability).

### Backtest

`fct_reserve_backtest` is the only model allowed to read `claims_truth`. If
any other model read it, the reserves would be estimated from the answer.
A dbt test fails the build if the all-lines estimate is more than 5% off.
Current results (estimated ultimate vs true ultimate):

| Line | Incurred method | Paid method |
|------|----------------:|------------:|
| All lines | +0.18% | +0.39% |
| General Liability | +0.30% | +2.10% |

Most of the General Liability paid error is accident year 2025 at age 1,
where the cumulative factor is 7.08 and small changes in early payments get
multiplied. This is a known weakness of the paid chain ladder on immature
years, not a bug. Bornhuetter-Ferguson is the usual fix and is on the list.

## Snowflake setup and security

### Everything as code

`snowflake/setup.sql` creates every role, warehouse, schema, grant, resource
monitor and the service user. It is idempotent (`IF NOT EXISTS` plus
`ALTER` to re-apply settings), so a rerun also repairs drift, such as
someone changing auto-suspend in the UI. `teardown.sql` removes it all, to
prove a rebuild from scratch works.

### Least privilege and separate compute

- `LOADER` writes `RAW` and cannot build models. `TRANSFORMER` reads `RAW`
  and cannot change it, so a bug in a dbt model cannot overwrite raw data.
- Each role has its own XSMALL warehouse with 60 s auto-suspend. Loads and
  dbt runs never queue behind each other, credit use per workload is
  visible, and an idle warehouse costs nothing.
- A resource monitor caps monthly credits.

### Secondary roles

While verifying the loader, I found that a session using `TRANSFORMER`
could still `INSERT` into `RAW`. New Snowflake users default to
`DEFAULT_SECONDARY_ROLES = ('ALL')`, so every role the user holds is active
alongside the session's primary role. Snowflake checks `CREATE` against the
primary role only, but DML against all active roles. The service user holds
both roles, so the read-only boundary was not real.

The fix is in two places: `setup.sql` sets the service user's default
secondary roles to none, and loader sessions run `USE SECONDARY ROLES NONE`
as a second guard. The smoke test (`scripts/snowflake_smoke_test.py`)
checks both what each role can do and what it must not be able to do,
including this `INSERT` case.

### Key-pair service user

`PIPELINE_SVC` is a `TYPE = SERVICE` user that cannot use a password. It
authenticates with an RSA key pair, and the private key lives outside the
repo. One user holds both roles for simplicity; a stricter setup would give
the loader and dbt separate users.

## Loading

The loader (`loader/snowflake_loader.py`) does a full refresh per table:

1. Derive the Snowflake table definition from the Parquet schema, so there
   is no hand-written DDL to drift from the generator.
2. `PUT` files to an internal stage, keeping partition folders.
3. `COPY INTO` a fresh `<TABLE>__LOAD` table. Hive partition columns are not
   stored in the Parquet files, so they are rebuilt from the file path.
4. Compare the loaded row count to the local count.
5. `ALTER TABLE ... SWAP WITH` the live table. Readers see the old data or
   the new data, never a half-loaded table.

Every row gets `_SOURCE_FILE` and `_LOADED_AT` for lineage. Loading from an
S3 external stage replaces the local `PUT` once the bucket exists (planned).

## Known limitations

- Development ages are annual and assume a year-end valuation date.
- Loads are full refreshes. Incremental loads are planned; the generator
  already supports them.
- The paid chain ladder overreacts on the youngest accident years
  (see Backtest).
- One service user holds both pipeline roles.
