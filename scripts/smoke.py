from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import duckdb  

from dualpath.config import get_settings  
from dualpath.sql.catalog import Catalog  
from dualpath.sql.guard import validate_sql  

MUST_BLOCK: list[tuple[str, str]] = [
    # -- exfiltration via table functions --
    ("read_csv_auto", "SELECT * FROM read_csv_auto('/etc/passwd')"),
    ("read_csv", "SELECT * FROM read_csv('/etc/passwd', header=true)"),
    ("read_parquet", "SELECT * FROM read_parquet('/tmp/x.parquet')"),
    ("read_json", "SELECT * FROM read_json_auto('/tmp/x.json')"),
    ("glob", "SELECT * FROM glob('/*')"),
    # -- exfiltration via scalar functions (no FROM clause to inspect) --
    ("read_text", "SELECT read_text('/etc/passwd') AS x"),
    ("read_blob", "SELECT read_blob('/etc/passwd') AS x"),
    ("getenv", "SELECT getenv('GROQ_API_KEY') AS x"),
    # -- code loading and other databases --
    ("install", "INSTALL httpfs"),
    ("load", "LOAD httpfs"),
    ("attach", "ATTACH '/tmp/other.duckdb' AS other"),
    ("detach", "DETACH other"),
    # -- writes --
    ("drop", "DROP TABLE claims"),
    ("delete", "DELETE FROM claims"),
    ("update", "UPDATE claims SET plan_paid = 0"),
    ("insert", "INSERT INTO claims VALUES ('x')"),
    ("create", "CREATE TABLE evil (x INT)"),
    ("copy_to", "COPY claims TO '/tmp/out.csv'"),
    ("export", "EXPORT DATABASE '/tmp/dump'"),
    # -- administrative --
    ("set", "SET enable_external_access = true"),
    ("pragma", "PRAGMA database_list"),
    ("call", "CALL pragma_version()"),
    # -- statement stacking --
    ("stacked_drop", "SELECT 1; DROP TABLE claims"),
    ("stacked_attach", "SELECT 1; ATTACH '/tmp/o.db' AS o"),
    # -- obfuscation --
    ("case_mangled", "sELeCt * FrOm ReAd_CsV_AuTo('/etc/passwd')"),
    ("comment_split", "SELECT/**/read_text('/etc/passwd')"),
    ("nested_subquery", "SELECT * FROM (SELECT * FROM read_csv_auto('/etc/passwd')) t"),
    ("nested_cte", "WITH x AS (SELECT * FROM read_csv_auto('/etc/passwd')) SELECT * FROM x"),
    ("nested_join", "SELECT * FROM claims JOIN read_csv_auto('/etc/passwd') f ON true"),
    ("union_smuggle", "SELECT claim_id FROM claims UNION SELECT read_text('/etc/passwd')"),
    # -- unknown tables --
    ("unknown_table", "SELECT * FROM secrets"),
    ("typo_table", "SELECT * FROM claim"),
    # -- malformed --
    ("empty", ""),
    ("garbage", "not sql at all"),
    ("comment_only", "-- nothing here"),
]

MUST_ALLOW: list[tuple[str, str]] = [
    ("simple", "SELECT claim_id FROM claims LIMIT 5"),
    ("aggregate", "SELECT status, count(*) FROM claims GROUP BY 1"),
    ("join", """
        SELECT p.plan_name, count(*) AS n
        FROM claims c JOIN plans p ON p.plan_id = c.plan_id
        GROUP BY 1 ORDER BY n DESC
    """),
    ("cte", """
        WITH denied AS (SELECT * FROM claims WHERE status = 'denied')
        SELECT denial_code, count(*) FROM denied GROUP BY 1
    """),
    ("union", "SELECT claim_id FROM claims UNION SELECT claim_id FROM claims"),
    ("window", """
        SELECT claim_id, sum(plan_paid) OVER (PARTITION BY member_id) AS total
        FROM claims
    """),
    
    ("keyword_in_literal", "SELECT * FROM claims WHERE denial_code LIKE '%DROP TABLE%'"),
    ("semicolon_in_literal", "SELECT * FROM claims WHERE denial_code = 'a; DROP TABLE claims'"),
    ("trailing_semicolon", "SELECT claim_id FROM claims;"),
]


ENGINE_MUST_REFUSE = [
    ("read_csv_auto", "SELECT * FROM read_csv_auto('/etc/passwd')"),
    ("glob", "SELECT * FROM glob('/*')"),
    ("copy_to", "COPY claims TO '/tmp/leak.csv'"),
    ("attach", "ATTACH '/tmp/other.duckdb' AS other"),
    ("install", "INSTALL httpfs"),
    ("write", "CREATE TABLE evil (x INT)"),
]


def check_engine(settings) -> list[str]:
    import json

    from dualpath.sql.engine import SqlExecutionError, WarehouseEngine

    failures: list[str] = []
    with WarehouseEngine(
        settings.warehouse_path,
        docs_dir=settings.warehouse_csv_dir,
        max_seconds=settings.sql_max_seconds,
        max_rows=settings.sql_max_rows,
    ) as engine:
        for label, sql in ENGINE_MUST_REFUSE:
            try:
                engine.execute(sql)
                failures.append(f"ENGINE DID NOT REFUSE: {label}")
            except SqlExecutionError:
                pass

        result = engine.execute(f"SELECT * FROM claim_lines LIMIT {settings.sql_max_rows * 100}")
        if result.row_count > settings.sql_max_rows:
            failures.append(f"ROW CAP EXCEEDED: {result.row_count} > {settings.sql_max_rows}")
        if not result.truncated:
            failures.append("TRUNCATION NOT FLAGGED")

        row = engine.execute(
            "SELECT date_trunc('month', service_date) AS m, sum(plan_paid) AS paid "
            "FROM claims WHERE status = 'paid' GROUP BY 1 ORDER BY 1 LIMIT 3"
        )
        try:
            json.dumps(row.rows)
        except TypeError as exc:
            failures.append(f"RESULT NOT JSON-SAFE: {exc}")

    print(f"  engine: {len(ENGINE_MUST_REFUSE)} statements refused, row cap and coercion checked")
    return failures


def main() -> int:
    settings = get_settings()
    if not settings.warehouse_path.exists():
        print("no warehouse -- run: dualpath build-warehouse", file=sys.stderr)
        return 1

    con = duckdb.connect(str(settings.warehouse_path), read_only=True)
    try:
        allowed_tables = Catalog.from_connection(con).table_names
    finally:
        con.close()

    def check(sql: str):
        return validate_sql(
            sql,
            allowed_tables=allowed_tables,
            max_rows=settings.sql_max_rows,
            allow_multiple=settings.sql_allow_multiple,
        )

    failures: list[str] = []

    for label, sql in MUST_BLOCK:
        if check(sql).allowed:
            failures.append(f"NOT BLOCKED: {label}: {sql.strip()[:60]}")

    for label, sql in MUST_ALLOW:
        verdict = check(sql)
        if not verdict.allowed:
            failures.append(f"WRONGLY BLOCKED: {label}: {verdict.reason}")

    print(f"  firewall: {len(MUST_BLOCK)} attacks blocked, {len(MUST_ALLOW)} legitimate allowed")

    failures.extend(check_engine(settings))

    if failures:
        print(f"\n  {len(failures)} FAILURE(S):", file=sys.stderr)
        for line in failures:
            print(f"    {line}", file=sys.stderr)
        return 1

    print("  all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
