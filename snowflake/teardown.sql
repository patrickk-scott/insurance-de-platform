-- =============================================================================
-- Teardown: removes EVERYTHING setup.sql creates, including all loaded data
-- and every dbt model in INSURANCE_PLATFORM.
-- =============================================================================
--
-- Use it to prove setup.sql rebuilds the environment from scratch, or to
-- clean up after the trial. Dropped databases can be restored with UNDROP
-- only within the Time Travel window (1 day on Standard edition).
--
-- Read before running. Run as ACCOUNTADMIN.

USE ROLE ACCOUNTADMIN;
ALTER WAREHOUSE IF EXISTS LOADING_WH UNSET RESOURCE_MONITOR;
ALTER WAREHOUSE IF EXISTS TRANSFORMING_WH UNSET RESOURCE_MONITOR;
DROP RESOURCE MONITOR IF EXISTS INSURANCE_PLATFORM_RM;

USE ROLE SYSADMIN;
DROP DATABASE IF EXISTS INSURANCE_PLATFORM;
DROP WAREHOUSE IF EXISTS LOADING_WH;
DROP WAREHOUSE IF EXISTS TRANSFORMING_WH;

USE ROLE SECURITYADMIN;
DROP USER IF EXISTS PIPELINE_SVC;
DROP ROLE IF EXISTS LOADER;
DROP ROLE IF EXISTS TRANSFORMER;
