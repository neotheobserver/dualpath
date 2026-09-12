from __future__ import annotations

import logging

import sqlglot
from sqlglot import exp

from ..models import GuardVerdict, GuardViolation

DIALECT = "duckdb"

logging.getLogger("sqlglot").setLevel(logging.ERROR)

_READ_ONLY_ROOTS: tuple[type[exp.Expression], ...] = (
    exp.Select,
    exp.Union,
    exp.Except,
    exp.Intersect,
    exp.Subquery,
)

_FORBIDDEN_FUNCTIONS: frozenset[str] = frozenset(
    {
        # file readers
        "read_csv",
        "read_csv_auto",
        "read_parquet",
        "parquet_scan",
        "parquet_metadata",
        "parquet_schema",
        "read_json",
        "read_json_auto",
        "read_json_objects",
        "read_ndjson",
        "read_ndjson_auto",
        "read_ndjson_objects",
        "read_text",
        "read_blob",
        "read_xlsx",
        "csv_scan",
        "sniff_csv",
        "glob",
        # external systems
        "postgres_scan",
        "postgres_scan_pushdown",
        "postgres_query",
        "mysql_scan",
        "mysql_query",
        "sqlite_scan",
        "sqlite_query",
        "iceberg_scan",
        "iceberg_metadata",
        "delta_scan",
        "st_read",
        # extensions and environment
        "load_extension",
        "install_extension",
        "getenv",
        "shell",
        "system",
    }
)

_ROOT_LABELS: dict[type[exp.Expression], str] = {
    exp.Drop: "DROP",
    exp.Delete: "DELETE",
    exp.Insert: "INSERT",
    exp.Update: "UPDATE",
    exp.Create: "CREATE",
    exp.Alter: "ALTER",
    exp.TruncateTable: "TRUNCATE",
    exp.Attach: "ATTACH",
    exp.Copy: "COPY",
    exp.Pragma: "PRAGMA",
    exp.Set: "SET",
    exp.Install: "INSTALL",
    exp.Command: "an unrecognised command (possibly LOAD, EXPORT or CALL)",
}


def _function_names(tree: exp.Expression) -> set[str]:
    """Every function name appearing anywhere in the statement, lowercased."""
    names: set[str] = set()
    for node in tree.find_all(exp.Func):
        if isinstance(node, exp.Anonymous):
            raw = node.this
            if isinstance(raw, str):
                names.add(raw.lower())
            continue
        try:
            names.add(node.sql_name().lower())
        except Exception: 
            names.add(type(node).__name__.lower())
    return names


def _cte_names(tree: exp.Expression) -> set[str]:
    names: set[str] = set()
    for cte in tree.find_all(exp.CTE):
        alias = cte.alias
        if alias:
            names.add(alias.lower())
    return names


def _referenced_tables(tree: exp.Expression) -> list[str]:
    return [t.name for t in tree.find_all(exp.Table)]


def _enforce_limit(tree: exp.Expression, max_rows: int) -> tuple[exp.Expression, bool]:
    existing = tree.args.get("limit")

    if existing is not None:
        value = existing.expression if isinstance(existing, exp.Limit) else None
        if isinstance(value, exp.Literal) and not value.is_string:
            try:
                if int(value.this) <= max_rows:
                    return tree, False
            except (TypeError, ValueError):
                pass
        try:
            tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
            return tree, True
        except Exception:
            return tree, False

    try:
        tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
        return tree, True
    except Exception:
        return tree, False


def validate_sql(
    sql: str,
    *,
    allowed_tables: set[str],
    max_rows: int = 200,
    allow_multiple: bool = False,
) -> GuardVerdict:
    violations: list[GuardViolation] = []
    candidate = (sql or "").strip().rstrip(";").strip()

    if not candidate:
        return GuardVerdict(
            allowed=False,
            violations=[GuardViolation(rule="empty", detail="no statement was produced")],
        )

    try:
        statements = sqlglot.parse(candidate, dialect=DIALECT)
    except Exception as exc:
        return GuardVerdict(
            allowed=False,
            violations=[
                GuardViolation(
                    rule="unparseable",
                    detail=f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}",
                )
            ],
        )

    statements = [s for s in statements if s is not None]
    if not statements:
        return GuardVerdict(
            allowed=False,
            violations=[GuardViolation(rule="empty", detail="parsed to no statement")],
        )

    if len(statements) > 1 and not allow_multiple:
        return GuardVerdict(
            allowed=False,
            violations=[
                GuardViolation(
                    rule="multiple_statements",
                    detail=(
                        f"{len(statements)} statements were produced; only one is allowed. "
                        "Statement stacking is the classic injection vector."
                    ),
                )
            ],
        )

    tree = statements[0]

    if not isinstance(tree, _READ_ONLY_ROOTS):
        label = "a write or administrative statement"
        for node_type, name in _ROOT_LABELS.items():
            if isinstance(tree, node_type):
                label = name
                break
        violations.append(
            GuardViolation(
                rule="not_read_only",
                detail=f"statement is {label}; only SELECT and set operations are permitted",
            )
        )
        return GuardVerdict(allowed=False, violations=violations)

    known = {t.lower() for t in allowed_tables} | _cte_names(tree)
    referenced = _referenced_tables(tree)
    unknown = sorted({t for t in referenced if t.lower() not in known})
    if unknown:
        if any(t == "" for t in unknown):
            violations.append(
                GuardViolation(
                    rule="table_function",
                    detail=(
                        "the statement reads from a table function rather than a "
                        "warehouse table; file and external-database readers are "
                        "not permitted"
                    ),
                )
            )
        named = [t for t in unknown if t]
        if named:
            violations.append(
                GuardViolation(
                    rule="unknown_table",
                    detail=(
                        f"table(s) {', '.join(repr(t) for t in named)} are not in the "
                        f"warehouse; known tables are "
                        f"{', '.join(sorted(allowed_tables)) or '(none)'}"
                    ),
                )
            )

    used = _function_names(tree)
    forbidden = sorted(used & _FORBIDDEN_FUNCTIONS)
    if forbidden:
        violations.append(
            GuardViolation(
                rule="forbidden_function",
                detail=(
                    f"function(s) {', '.join(forbidden)} access the filesystem, an "
                    "external database, or the extension loader"
                ),
            )
        )

    if violations:
        return GuardVerdict(allowed=False, violations=violations)

    tree, injected = _enforce_limit(tree, max_rows)
    return GuardVerdict(
        allowed=True,
        sql=tree.sql(dialect=DIALECT, pretty=True),
        limit_injected=injected,
    )
