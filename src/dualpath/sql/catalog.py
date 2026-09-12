from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import ColumnInfo, TableInfo
from ..observability import get_logger

log = get_logger("catalog")

SCHEMA_DOCS_FILENAME = "_schema_docs.json"


class Catalog:
    """the set of tables an agent is allowed to see."""

    def __init__(self, tables: list[TableInfo]) -> None:
        self.tables = sorted(tables, key=lambda t: t.name)
        self._by_name = {t.name.lower(): t for t in self.tables}


    @classmethod
    def from_connection(cls, con: Any, docs_dir: Path | None = None) -> Catalog:
        """introspect a DuckDB connection and merge in hand-written docs."""
        descriptions = load_schema_docs(docs_dir) if docs_dir else {}

        table_names = [
            row[0]
            for row in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' ORDER BY table_name"
            ).fetchall()
        ]

        tables: list[TableInfo] = []
        for name in table_names:
            columns = con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'main' AND table_name = ? "
                "ORDER BY ordinal_position",
                [name],
            ).fetchall()

            doc = descriptions.get(name, {})
            col_docs = doc.get("columns", {}) or {}

            row_count = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]

            tables.append(
                TableInfo(
                    name=name,
                    description=str(doc.get("description", "") or ""),
                    row_count=int(row_count),
                    columns=[
                        ColumnInfo(
                            name=col_name,
                            data_type=str(col_type),
                            description=str(col_docs.get(col_name, "") or ""),
                        )
                        for col_name, col_type in columns
                    ],
                )
            )

        undocumented = [t.name for t in tables if not t.description]
        if undocumented:
            log.debug(
                "tables without a description in %s: %s",
                SCHEMA_DOCS_FILENAME,
                ", ".join(undocumented),
            )
        return cls(tables)

    @property
    def table_names(self) -> set[str]:
        """The allowlist the firewall validates against."""
        return {t.name for t in self.tables}

    def __len__(self) -> int:
        return len(self.tables)

    def __bool__(self) -> bool:
        return bool(self.tables)

    def get(self, name: str) -> TableInfo | None:
        return self._by_name.get(name.lower())

    def subset(self, names: list[str]) -> list[TableInfo]:
        """Tables in `names`, in catalog order, silently skipping unknowns."""
        wanted = {n.lower() for n in names}
        return [t for t in self.tables if t.name.lower() in wanted]

    def summaries(self, max_chars: int = 240) -> str:

        if not self.tables:
            return "- (the warehouse is empty; run: dualpath build-warehouse)"
        lines = []
        for table in self.tables:
            summary = " ".join(table.description.split())
            if len(summary) > max_chars:
                summary = summary[: max_chars - 3].rstrip() + "..."
            lines.append(f"- {table.name} ({table.row_count:,} rows): {summary}")
        return "\n".join(lines)

    def render(self, names: list[str] | None = None) -> str:
        """Full DDL-ish rendering for the SQL writer."""
        tables = self.subset(names) if names else self.tables
        return "\n\n".join(t.render() for t in tables)


def load_schema_docs(docs_dir: Path) -> dict[str, dict]:

    path = Path(docs_dir) / SCHEMA_DOCS_FILENAME
    if not path.exists():
        log.info(
            "no %s in %s -- generated SQL will rely on column names alone",
            SCHEMA_DOCS_FILENAME,
            docs_dir,
        )
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("could not read %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        log.warning("%s must contain a JSON object keyed by table name", path)
        return {}
    return data
