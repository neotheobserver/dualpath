from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..observability import get_logger

log = get_logger("memory")


def build_checkpointer(path: str | Path) -> Any:
    from langgraph.checkpoint.sqlite import SqliteSaver

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), check_same_thread=False)
    log.info("checkpointer at %s", path)
    return SqliteSaver(connection)


def build_memory_checkpointer() -> Any:
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()
