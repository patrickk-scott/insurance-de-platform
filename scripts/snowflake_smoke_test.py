"""
Snowflake smoke test: prove the pipeline can connect AND that permissions
match snowflake/setup.sql, including what each role must NOT be able to do.

Usage (reads .env in the repo root):
    python scripts/snowflake_smoke_test.py

Checks:
  1. Key-pair login as PIPELINE_SVC (no password)
  2. TRANSFORMER can run a query on TRANSFORMING_WH
  3. Secondary roles are off by default for PIPELINE_SVC
  4. TRANSFORMER cannot INSERT into an existing RAW table (skipped before
     the first load)
  5. TRANSFORMER cannot create tables in RAW (read-only on raw data)
  6. LOADER can create, write and drop a table in RAW
  7. LOADER cannot create schemas (only dbt/TRANSFORMER builds models)
Then suspends both warehouses so the test stops billing immediately.

Exit code 0 if every check passes, 1 otherwise. Usable as a pipeline
health check before a load.

Checks 3 and 4 run with the session's default roles, the way dbt connects.
Secondary roles were the gap: with DEFAULT_SECONDARY_ROLES = ('ALL'), a
TRANSFORMER session could INSERT into RAW through PIPELINE_SVC's LOADER role,
while CREATE TABLE (checked against the primary role only) was denied. So both
kinds of write are tested.

Permanent tables are used on purpose: Snowflake lets any role with USAGE on
a schema create TEMPORARY tables there (they are private to one session), so
a temporary table proves nothing about CREATE TABLE privileges.

Cost: resumes each XSMALL warehouse once (60s minimum each, about 0.03
credits total).
"""

import os
import sys
from pathlib import Path

import snowflake.connector
from dotenv import load_dotenv
from snowflake.connector.errors import ProgrammingError

REPO_ROOT = Path(__file__).resolve().parents[1]


def connect(role: str, warehouse: str):
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        authenticator="SNOWFLAKE_JWT",
        private_key_file=os.path.expanduser(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]),
        role=role,
        warehouse=warehouse,
        database=os.environ["SNOWFLAKE_DATABASE"],
        schema=os.environ["SNOWFLAKE_SCHEMA"],
        # Tags every query so it is easy to find in Snowsight query history.
        session_parameters={"QUERY_TAG": "insurance-platform:smoke-test"},
    )


results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(
        f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else "")
    )


def expect_denied(cur, sql: str, cleanup: str | None = None) -> tuple[bool, str]:
    """Run SQL that SHOULD fail with a privilege error. If it wrongly
    succeeds, run `cleanup` so the test leaves nothing behind."""
    try:
        cur.execute(sql)
    except ProgrammingError as e:
        return True, f"denied as expected: {e.msg.splitlines()[0][:70]}"
    if cleanup:
        cur.execute(cleanup)
    return False, "statement succeeded but should have been denied"


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    transform_role = os.environ["SNOWFLAKE_ROLE"]
    transform_wh = os.environ["SNOWFLAKE_WAREHOUSE"]
    loader_role = os.environ["SNOWFLAKE_LOADER_ROLE"]
    loader_wh = os.environ["SNOWFLAKE_LOADER_WAREHOUSE"]

    print(f"TRANSFORMER session ({transform_role} on {transform_wh})")
    with connect(transform_role, transform_wh) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_ACCOUNT(), "
            "CURRENT_REGION(), CURRENT_VERSION()"
        )
        user, role, account, region, version = cur.fetchone()
        check(
            "key-pair login",
            user == os.environ["SNOWFLAKE_USER"] and role == transform_role,
            f"{user} as {role}, account {account}, {region}, v{version}",
        )

        # GENERATOR forces real compute, so this proves warehouse access.
        cur.execute("SELECT SUM(SEQ4()) FROM TABLE(GENERATOR(ROWCOUNT => 1000))")
        check("warehouse query", cur.fetchone()[0] == 499500, f"{transform_wh} resumed")

        cur.execute("SELECT CURRENT_SECONDARY_ROLES()")
        secondary = cur.fetchone()[0]
        check(
            "secondary roles off by default",
            '"roles":""' in secondary.replace(" ", ""),
            f"CURRENT_SECONDARY_ROLES() = {secondary}",
        )

        cur.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = 'RAW' AND TABLE_NAME = 'POLICIES'"
        )
        if cur.fetchone()[0]:
            ok, detail = expect_denied(
                cur,
                "INSERT INTO RAW.POLICIES (POLICY_ID) VALUES (-1)",
                cleanup="DELETE FROM RAW.POLICIES WHERE POLICY_ID = -1",
            )
            check("TRANSFORMER cannot write RAW tables", ok, detail)
        else:
            print("  [SKIP] TRANSFORMER cannot write RAW tables  (no RAW.POLICIES yet)")

        ok, detail = expect_denied(
            cur,
            "CREATE TABLE smoke_should_fail (x INT)",
            cleanup="DROP TABLE IF EXISTS smoke_should_fail",
        )
        check("TRANSFORMER cannot create tables in RAW", ok, detail)

        cur.execute(f"ALTER WAREHOUSE {transform_wh} SUSPEND")

    print(f"LOADER session ({loader_role} on {loader_wh})")
    with connect(loader_role, loader_wh) as conn:
        cur = conn.cursor()
        cur.execute("CREATE OR REPLACE TABLE smoke_load_test (id INT, note STRING)")
        try:
            cur.execute("INSERT INTO smoke_load_test VALUES (1, 'ok'), (2, 'ok')")
            cur.execute("SELECT COUNT(*) FROM smoke_load_test")
            rows = cur.fetchone()[0]
        finally:
            cur.execute("DROP TABLE IF EXISTS smoke_load_test")
        check("LOADER can write RAW", rows == 2, "permanent table, dropped after")

        ok, detail = expect_denied(cur, "CREATE SCHEMA smoke_should_fail")
        check("LOADER cannot create schemas", ok, detail)

        cur.execute(f"ALTER WAREHOUSE {loader_wh} SUSPEND")

    failed = [name for name, ok, _ in results if not ok]
    print(
        f"\n{len(results) - len(failed)}/{len(results)} checks passed; warehouses suspended"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
