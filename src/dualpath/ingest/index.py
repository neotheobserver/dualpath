from __future__ import annotations

from pathlib import Path

from langchain_core.documents import Document

from ..observability import get_logger

log = get_logger("index")


def build_record_manager(db_path: str | Path, namespace: str):
    """The bookkeeping store. Creates its schema on first use."""
    from langchain_classic.indexes import SQLRecordManager

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    manager = SQLRecordManager(namespace, db_url=f"sqlite:///{db_path}")
    manager.create_schema()
    return manager


def index_documents(
    chunks: list[Document],
    record_manager,
    store,
    *,
    full_refresh: bool = False,
) -> dict[str, int]:

    from langchain_core.indexing import index

    result = index(
        chunks,
        record_manager,
        store,
        cleanup="full" if full_refresh else "incremental",
        source_id_key="source",
        key_encoder="sha256",
    )
    log.info(
        "indexed: +%d ~%d =%d -%d",
        result["num_added"],
        result["num_updated"],
        result["num_skipped"],
        result["num_deleted"],
    )
    return dict(result)

