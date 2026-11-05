# Snowflake setup

Snowflake objects for the platform are defined as code here, not created by
clicking through the UI. [`setup.sql`](setup.sql) is idempotent: run it on a
fresh account to build everything, or rerun it any time to repair drift.

## What gets created

| Object | Name | Purpose |
|---|---|---|
| Role | `LOADER` | Writes raw data into `INSURANCE_PLATFORM.RAW` |
| Role | `TRANSFORMER` | Reads `RAW`, builds dbt models in its own schemas |
| Warehouse | `LOADING_WH` | XSMALL, 60s auto-suspend, loads only |
| Warehouse | `TRANSFORMING_WH` | XSMALL, 60s auto-suspend, dbt only |
| Database / schema | `INSURANCE_PLATFORM.RAW` | Landing zone; dbt creates the rest |
| Resource monitor | `INSURANCE_PLATFORM_RM` | 25 credits/month cap on both warehouses |
| Service user | `PIPELINE_SVC` | Key-pair auth for Airflow, loader, dbt |

Design notes:

- **Least privilege.** Pipelines never run as `ACCOUNTADMIN`. `LOADER` can
  write `RAW` but cannot build models; `TRANSFORMER` can read `RAW` but cannot
  change it. A bug in a dbt model cannot overwrite raw data.
- **Separate warehouses per workload.** Loads and dbt runs never queue behind
  each other, and credit use per workload shows up separately in billing.
  With 60s auto-suspend, an idle warehouse costs nothing, so two cost no more
  than one.
- **Future grants.** `TRANSFORMER` automatically gets `SELECT` on any table
  `LOADER` creates in `RAW` later. No re-granting after each new table.
- **Cost guard.** The resource monitor suspends the warehouses if monthly
  usage hits the cap, so a runaway job cannot drain the trial credits.
- **Key-pair auth for the service user.** `TYPE = SERVICE` users cannot use
  passwords. Snowflake is phasing out password-only logins for service
  accounts, and a key file is easier to keep out of shell history and logs.

## 1. Run the setup

1. Sign in to Snowsight as the trial signup user (it has `ACCOUNTADMIN`).
2. Open a new SQL worksheet, paste in `setup.sql`, and choose **Run All**.
3. Check the last three result panes:
   - both warehouses are `SUSPENDED` with `auto_suspend = 60` and
     `resource_monitor = INSURANCE_PLATFORM_RM`
   - `TRANSFORMER` has `USAGE` on `TRANSFORMING_WH` and the database
   - there are future `SELECT` grants in `RAW` for `TRANSFORMER`

Run it a second time. Nothing should change. That is the idempotency check.

## 2. Create a key pair for `PIPELINE_SVC`

Run on your Mac. The private key goes in `~/.snowflake/keys/`, outside the
repo, readable only by you.

```bash
mkdir -p ~/.snowflake/keys && chmod 700 ~/.snowflake/keys
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -nocrypt -out ~/.snowflake/keys/pipeline_svc.p8
chmod 600 ~/.snowflake/keys/pipeline_svc.p8
openssl rsa -in ~/.snowflake/keys/pipeline_svc.p8 -pubout | grep -v "PUBLIC KEY" | tr -d '\n'
```

The last command prints the public key on one line. In Snowsight, run:

```sql
USE ROLE SECURITYADMIN;
ALTER USER PIPELINE_SVC SET RSA_PUBLIC_KEY = '<paste the public key here>';
DESC USER PIPELINE_SVC;  -- RSA_PUBLIC_KEY_FP should now be set
```

The public key is safe to paste anywhere. The `.p8` file is the secret:
never commit it, paste it, or copy it into the repo. `.gitignore` blocks
`*.p8` and `*.pem` as a backstop.

The key is unencrypted (`-nocrypt`) for simple local development. For
anything shared, encrypt it and supply the passphrase through an environment
variable.

## 3. Point local tools at Snowflake

Copy the template and fill in your account identifier (Snowsight: your
name in the lower left, then **Account**, then copy the account identifier,
which looks like `ORGNAME-ACCOUNTNAME`):

```bash
cp .env.example .env
```

`.env` is gitignored. dbt reads these variables through
`dbt_project/profiles.yml`.

## Tearing down

[`teardown.sql`](teardown.sql) drops everything `setup.sql` creates,
**including all loaded data and dbt models**. Use it to prove the setup
rebuilds from scratch, or to clean up after the trial.

## Later

- S3 storage integration and external stage (once the bucket exists; needs
  `ACCOUNTADMIN`).
- A `REPORTER` role with read-only access to the marts for the dashboard.
- Possibly move these objects to the Terraform Snowflake provider.
