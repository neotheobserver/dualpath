from __future__ import annotations

import contextlib
import datetime as dt
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..models import TableResult
from ..observability import get_logger
from .catalog import Catalog

log = get_logger("sql.engine")


class WarehouseError(RuntimeError):
    pass


class SqlExecutionError(RuntimeError):
    pass


def _coerce(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return str(value)
    if isinstance(value, bytes | bytearray):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, list | tuple):
        return [_coerce(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _coerce(v) for k, v in value.items()}
    return str(value)


class WarehouseEngine:
    def __init__(
        self,
        path: Path,
        *,
        docs_dir: Path | None = None,
        max_rows: int = 200,
        max_seconds: float = 20.0,
        memory_limit: str = "1GB",
    ) -> None:
        self.path = Path(path)
        self.max_rows = max_rows
        self.max_seconds = max_seconds

        if not self.path.exists():
            raise WarehouseError(
                f"No warehouse at {self.path}.\n"
                "Build it first:  dualpath build-warehouse"
            )

        try:
            import duckdb
        except ImportError as exc:
            raise WarehouseError("duckdb is not installed.") from exc

        try:
            self._con = duckdb.connect(str(self.path), read_only=True)
        except Exception as exc:
            raise WarehouseError(
                f"Could not open {self.path} read-only: {exc}\n"
                "If another DualPath process is writing to it (an ingest or "
                "build-warehouse run), wait for that to finish."
            ) from exc

        for statement in (
            "SET enable_external_access=false",
            f"SET memory_limit='{memory_limit}'",
        ):
            try:
                self._con.execute(statement)
            except Exception as exc:
                log.warning("could not apply %r: %s", statement, exc)

        self.catalog = Catalog.from_connection(self._con, docs_dir=docs_dir)
        log.info(
            "warehouse open: %d table(s), %s",
            len(self.catalog),
            ", ".join(sorted(self.catalog.table_names)) or "none",
        )

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._con.close()

    def __enter__(self) -> WarehouseEngine:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def execute(self, sql: str, *, max_rows: int | None = None) -> TableResult:
        cap = max_rows or self.max_rows
        cursor = self._con.cursor()
        timed_out = threading.Event()

        def _interrupt() -> None:
            timed_out.set()
            with contextlib.suppress(Exception):
                cursor.interrupt()

        watchdog = threading.Timer(self.max_seconds, _interrupt)
        watchdog.daemon = True
        watchdog.start()

        started = time.perf_counter()
        try:
            cursor.execute(sql)
            columns = [d[0] for d in (cursor.description or [])]
            raw = cursor.fetchmany(cap + 1)
        except Exception as exc:
            if timed_out.is_set():
                raise SqlExecutionError(
                    f"the query exceeded the {self.max_seconds:.0f}s time limit and was "
                    "cancelled; add a narrower filter or aggregate before returning rows"
                ) from exc
            raise SqlExecutionError(_clean_duckdb_error(exc)) from exc
        finally:
            watchdog.cancel()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            with contextlib.suppress(Exception):
                cursor.close()

        truncated = len(raw) > cap
        rows = [[_coerce(v) for v in row] for row in raw[:cap]]

        return TableResult(
            sql=sql,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=elapsed_ms,
        )

    def table_names(self) -> set[str]:
        return self.catalog.table_names

    def stats(self) -> dict[str, int]:
        return {t.name: t.row_count for t in self.catalog.tables}


def _clean_duckdb_error(exc: Exception) -> str:
    message = str(exc).strip()
    lines = [line for line in message.splitlines() if line.strip()]
    if not lines:
        return f"{type(exc).__name__}: unknown error"
    head = lines[0]
    for line in lines[1:]:
        if "Candidate binding" in line or "Did you mean" in line:
            head = f"{head} | {line.strip()}"
            break
    return head[:600]
