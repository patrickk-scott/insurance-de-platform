-- =============================================================================
-- Snowflake setup for the Insurance Claims & Reserving Analytics Platform
-- =============================================================================
--
-- Idempotent: safe to run any number of times. Every object uses
-- CREATE ... IF NOT EXISTS (never CREATE OR REPLACE, which would drop data
-- and grants), and settings that matter are re-applied with ALTER so a rerun
-- also repairs drift (e.g. someone changed auto-suspend in the UI).
--
-- Run as a user with ACCOUNTADMIN (the trial signup user has it), e.g. in a
-- Snowsight worksheet with "Run All". Each section switches to the
-- least-powerful system role that can do the job:
--   ACCOUNTADMIN  resource monitor (account-level cost guard)
--   SECURITYADMIN roles, users, grants
--   SYSADMIN      warehouses, database, schemas (owns all data objects)
--
-- What it creates:
--   Roles       LOADER       writes raw data into RAW
--               TRANSFORMER  reads RAW, builds dbt models in its own schemas
--   Warehouses  LOADING_WH       XSMALL, used only by LOADER
--               TRANSFORMING_WH  XSMALL, used only by TRANSFORMER
--               Separate compute per workload: loads and dbt runs never
--               queue behind each other, and credit use is attributable.
--               Both auto-suspend after 60s, so two warehouses cost nothing
--               extra while idle.
--   Database    INSURANCE_PLATFORM with schema RAW. dbt creates the
--               staging / intermediate / marts schemas itself.
--   Monitor     INSURANCE_PLATFORM_RM caps monthly credits on both
--               warehouses.
--   User        PIPELINE_SVC, a service user (key-pair auth, no password)
--               for Airflow, the loader and dbt.
--
-- Cost: XSMALL = 1 credit per hour of running time, billed per second with a
-- 60s minimum each time it resumes.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. Roles (SECURITYADMIN)
-- -----------------------------------------------------------------------------
USE ROLE SECURITYADMIN;

CREATE ROLE IF NOT EXISTS LOADER
    COMMENT = 'Loads raw files into INSURANCE_PLATFORM.RAW';
CREATE ROLE IF NOT EXISTS TRANSFORMER
    COMMENT = 'Reads RAW and builds dbt models';

-- Role hierarchy: SYSADMIN inherits both, so admins can see and manage
-- everything these roles create. Without this, objects created by a custom
-- role are invisible to SYSADMIN.
--
-- This is also how a human admin gets these roles: ACCOUNTADMIN includes
-- SYSADMIN, which now includes LOADER and TRANSFORMER, so `USE ROLE LOADER`
-- works with no per-user grant. (An earlier version granted the roles to
-- CURRENT_USER() directly; it failed on the trial account and was redundant.)
GRANT ROLE LOADER TO ROLE SYSADMIN;
GRANT ROLE TRANSFORMER TO ROLE SYSADMIN;


-- -----------------------------------------------------------------------------
-- 2. Warehouses (SYSADMIN)
-- -----------------------------------------------------------------------------
USE ROLE SYSADMIN;

CREATE WAREHOUSE IF NOT EXISTS LOADING_WH
    WAREHOUSE_SIZE = XSMALL
    AUTO_SUSPEND = 60
    AUTO_RESUME = TRUE
    INITIALLY_SUSPENDED = TRUE
    COMMENT = 'Raw data loads (LOADER role)';

CREATE WAREHOUSE IF NOT EXISTS TRANSFORMING_WH
    WAREHOUSE_SIZE = XSMALL
    AUTO_SUSPEND = 60
    AUTO_RESUME = TRUE
    INITIALLY_SUSPENDED = TRUE
    COMMENT = 'dbt runs (TRANSFORMER role)';

-- Re-apply settings: CREATE IF NOT EXISTS does nothing when the warehouse
-- already exists, so this is what makes a rerun fix drift. The statement
-- timeout stops a runaway query from burning credits for hours.
ALTER WAREHOUSE LOADING_WH SET
    WAREHOUSE_SIZE = XSMALL
    AUTO_SUSPEND = 60
    AUTO_RESUME = TRUE
    STATEMENT_TIMEOUT_IN_SECONDS = 3600;

ALTER WAREHOUSE TRANSFORMING_WH SET
    WAREHOUSE_SIZE = XSMALL
    AUTO_SUSPEND = 60
    AUTO_RESUME = TRUE
    STATEMENT_TIMEOUT_IN_SECONDS = 3600;


-- -----------------------------------------------------------------------------
-- 3. Database and raw schema (SYSADMIN owns them)
-- -----------------------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS INSURANCE_PLATFORM
    COMMENT = 'Insurance claims and reserving analytics';

CREATE SCHEMA IF NOT EXISTS INSURANCE_PLATFORM.RAW
    COMMENT = 'Landing zone: data as loaded from the lake, untransformed';


-- -----------------------------------------------------------------------------
-- 4. Grants (SECURITYADMIN). GRANT is naturally idempotent.
-- -----------------------------------------------------------------------------
USE ROLE SECURITYADMIN;

-- LOADER: its own warehouse, and full control of RAW (tables, stages,
-- file formats). It owns the raw tables it creates.
GRANT USAGE, OPERATE ON WAREHOUSE LOADING_WH TO ROLE LOADER;
GRANT USAGE ON DATABASE INSURANCE_PLATFORM TO ROLE LOADER;
GRANT USAGE, CREATE TABLE, CREATE STAGE, CREATE FILE FORMAT
    ON SCHEMA INSURANCE_PLATFORM.RAW TO ROLE LOADER;

-- TRANSFORMER: its own warehouse, read-only on RAW, and CREATE SCHEMA on the
-- database so dbt can create and own its staging / marts schemas.
GRANT USAGE, OPERATE ON WAREHOUSE TRANSFORMING_WH TO ROLE TRANSFORMER;
GRANT USAGE, CREATE SCHEMA ON DATABASE INSURANCE_PLATFORM TO ROLE TRANSFORMER;
GRANT USAGE ON SCHEMA INSURANCE_PLATFORM.RAW TO ROLE TRANSFORMER;
GRANT SELECT ON ALL TABLES IN SCHEMA INSURANCE_PLATFORM.RAW TO ROLE TRANSFORMER;
GRANT SELECT ON ALL VIEWS IN SCHEMA INSURANCE_PLATFORM.RAW TO ROLE TRANSFORMER;
-- Future grants: tables LOADER creates later are readable by TRANSFORMER
-- automatically, with no re-grant after each new load.
GRANT SELECT ON FUTURE TABLES IN SCHEMA INSURANCE_PLATFORM.RAW TO ROLE TRANSFORMER;
GRANT SELECT ON FUTURE VIEWS IN SCHEMA INSURANCE_PLATFORM.RAW TO ROLE TRANSFORMER;


-- -----------------------------------------------------------------------------
-- 5. Service user (SECURITYADMIN)
-- -----------------------------------------------------------------------------
-- TYPE = SERVICE: cannot log in with a password or through the UI; it
-- authenticates with an RSA key pair. Set the public key after creating it
-- (see snowflake/README.md). The public key is not a secret; the private key
-- stays on your machine and never goes in the repo.
--
-- One service user holds both roles for simplicity. A stricter setup gives
-- the loader and dbt separate users so each credential has one role.
CREATE USER IF NOT EXISTS PIPELINE_SVC
    TYPE = SERVICE
    DEFAULT_ROLE = TRANSFORMER
    DEFAULT_WAREHOUSE = TRANSFORMING_WH
    DEFAULT_NAMESPACE = INSURANCE_PLATFORM.RAW
    COMMENT = 'Airflow, loader and dbt service account (key-pair auth)';

GRANT ROLE LOADER TO USER PIPELINE_SVC;
GRANT ROLE TRANSFORMER TO USER PIPELINE_SVC;


-- -----------------------------------------------------------------------------
-- 6. Cost guard: resource monitor (ACCOUNTADMIN only)
-- -----------------------------------------------------------------------------
-- 25 credits per month across both warehouses (~25 hours of XSMALL time).
-- Notify at 75%, stop new queries at 90%, kill running queries at 100%.
-- Raise the quota deliberately if a real workload needs it.
USE ROLE ACCOUNTADMIN;

CREATE RESOURCE MONITOR IF NOT EXISTS INSURANCE_PLATFORM_RM
    WITH CREDIT_QUOTA = 25
    FREQUENCY = MONTHLY
    START_TIMESTAMP = IMMEDIATELY
    TRIGGERS
        ON 75 PERCENT DO NOTIFY
        ON 90 PERCENT DO SUSPEND
        ON 100 PERCENT DO SUSPEND_IMMEDIATE;

ALTER RESOURCE MONITOR INSURANCE_PLATFORM_RM SET
    CREDIT_QUOTA = 25
    TRIGGERS
        ON 75 PERCENT DO NOTIFY
        ON 90 PERCENT DO SUSPEND
        ON 100 PERCENT DO SUSPEND_IMMEDIATE;

ALTER WAREHOUSE LOADING_WH SET RESOURCE_MONITOR = INSURANCE_PLATFORM_RM;
ALTER WAREHOUSE TRANSFORMING_WH SET RESOURCE_MONITOR = INSURANCE_PLATFORM_RM;


-- -----------------------------------------------------------------------------
-- 7. Verify. Expected: both warehouses SUSPENDED, auto_suspend 60, monitor
--    INSURANCE_PLATFORM_RM; TRANSFORMER has SELECT on future RAW tables.
-- -----------------------------------------------------------------------------
USE ROLE SYSADMIN;
-- Exact names: a pattern like '%ING_WH' also matches Snowflake's own
-- SNOWFLAKE_LEARNING_WH. Snowsight shows only the last result; run any of
-- these on its own to see it.
SHOW WAREHOUSES LIKE 'LOADING_WH';
SHOW WAREHOUSES LIKE 'TRANSFORMING_WH';
SHOW GRANTS TO ROLE TRANSFORMER;
SHOW FUTURE GRANTS IN SCHEMA INSURANCE_PLATFORM.RAW;
