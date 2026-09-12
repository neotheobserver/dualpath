from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..observability import get_logger

log = get_logger("ingest.warehouse")

_SAFE_TABLE_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,62}$")
_SAFE_COLUMN_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,62}$")

_ALLOWED_TYPES = frozenset(
    {
        "VARCHAR",
        "BOOLEAN",
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "FLOAT",
        "DOUBLE",
        "DATE",
        "TIME",
        "TIMESTAMP",
        "TIMESTAMPTZ",
        "INTERVAL",
        "UUID",
        "BLOB",
    }
)


def _type_overrides(docs: dict, table: str) -> dict[str, str]:

    raw = (docs.get(table) or {}).get("types") or {}
    if not isinstance(raw, dict):
        log.warning("`types` for table %s must be an object; ignoring", table)
        return {}

    resolved: dict[str, str] = {}
    for column, declared in raw.items():
        type_name = str(declared).strip().upper()
        if not _SAFE_COLUMN_NAME.match(str(column)):
            log.warning("ignoring type override for unusable column name %r", column)
            continue
        if type_name not in _ALLOWED_TYPES:
            log.warning(
                "ignoring type %r for %s.%s; allowed types are %s",
                declared,
                table,
                column,
                ", ".join(sorted(_ALLOWED_TYPES)),
            )
            continue
        resolved[str(column)] = type_name
    return resolved


@dataclass
class BuildReport:
    tables: dict[str, int] = field(default_factory=dict)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    warehouse_path: Path | None = None

    @property
    def total_rows(self) -> int:
        return sum(self.tables.values())


def table_name_for(path: Path) -> str:
    """Derive a table name from a filename."""
    return path.stem.strip().lower().replace("-", "_").replace(" ", "_")


def build_warehouse(
    csv_dir: Path,
    warehouse_path: Path,
    *,
    recreate: bool = True,
) -> BuildReport:
    try:
        import duckdb
    except ImportError as exc: 
        raise RuntimeError("duckdb is not installed.") from exc

    csv_dir = Path(csv_dir)
    warehouse_path = Path(warehouse_path)

    if not csv_dir.is_dir():
        raise FileNotFoundError(
            f"No CSV directory at {csv_dir}.\n"
            "Generate the bundled sample warehouse with:\n"
            "  python scripts/generate_warehouse_csvs.py"
        )

    csv_files = sorted(
        p for p in csv_dir.glob("*.csv") if not p.name.startswith("_")
    )
    if not csv_files:
        raise FileNotFoundError(
            f"No CSV files in {csv_dir}.\n"
            "Generate the bundled sample warehouse with:\n"
            "  python scripts/generate_warehouse_csvs.py"
        )

    warehouse_path.parent.mkdir(parents=True, exist_ok=True)
    if recreate and warehouse_path.exists():
        warehouse_path.unlink()
        wal = warehouse_path.with_suffix(warehouse_path.suffix + ".wal")
        if wal.exists():
            wal.unlink()

    from .catalog import load_schema_docs

    schema_docs = load_schema_docs(csv_dir)

    report = BuildReport(warehouse_path=warehouse_path)
    con = duckdb.connect(str(warehouse_path))
    try:
        for csv_path in csv_files:
            name = table_name_for(csv_path)
            if not _SAFE_TABLE_NAME.match(name):
                report.skipped.append(
                    (csv_path.name, f"{name!r} is not a usable table name")
                )
                log.warning("skipping %s: unusable table name %r", csv_path.name, name)
                continue
            try:
                overrides = _type_overrides(schema_docs, name)
                types_clause = ""
                if overrides:
                    pairs = ", ".join(f"'{c}': '{t}'" for c, t in sorted(overrides.items()))
                    types_clause = f", types={{{pairs}}}"
                    log.info("type overrides for %s: %s", name, pairs)
                con.execute(
                    f'CREATE OR REPLACE TABLE "{name}" AS '
                    f"SELECT * FROM read_csv_auto(?, header=true, sample_size=-1"
                    f"{types_clause})",
                    [str(csv_path)],
                )
                count = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                report.tables[name] = int(count)
                log.info("loaded %s -> %s (%d rows)", csv_path.name, name, count)
            except Exception as exc:  #one badd file must not stop the build
                report.skipped.append((csv_path.name, str(exc).splitlines()[0][:200]))
                log.error("failed to load %s: %s", csv_path.name, exc)
        con.execute("CHECKPOINT")
    finally:
        con.close()

    return report


def describe_warehouse(warehouse_path: Path) -> dict[str, list[tuple[str, str]]]:
    import duckdb

    con = duckdb.connect(str(warehouse_path), read_only=True)
    try:
        tables = [
            row[0]
            for row in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='main' ORDER BY table_name"
            ).fetchall()
        ]
        return {
            table: con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema='main' AND table_name = ? ORDER BY ordinal_position",
                [table],
            ).fetchall()
            for table in tables
        }
    finally:
        con.close()
