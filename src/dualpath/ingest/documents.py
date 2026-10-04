from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..observability import get_logger
from .index import build_record_manager, index_documents
from .loaders import UnsupportedDocument, load_directory, load_document
from .splitter import split_documents

log = get_logger("ingest")


@dataclass
class IngestFailure:
    source: str
    error: str


@dataclass
class IngestReport:
    indexed_files: int = 0
    total_chunks: int = 0
    added: int = 0
    skipped: int = 0
    deleted: int = 0
    elapsed_s: float = 0.0
    total_in_store: int = 0
    failures: list[IngestFailure] = field(default_factory=list)


def _record_manager(settings: Settings):
    return build_record_manager(settings.record_manager_path, f"chroma/{settings.collection}")


def ingest_documents(
    target: Path,
    *,
    store,
    settings: Settings,
    full_refresh: bool | None = None,
    recreate: bool = False,
) -> IngestReport:
    started = time.perf_counter()
    target = Path(target)
    if not target.exists():
        raise FileNotFoundError(f"no such file or directory: {target}")

    report = IngestReport()
    manager = _record_manager(settings)

    if recreate:
        ids = store.get(include=[]).get("ids") or []
        if ids:
            store.delete(ids=ids)
            manager.delete_keys(ids)

    if target.is_file():
        documents = []
        try:
            documents.append(load_document(target))
        except (UnsupportedDocument, OSError) as exc:
            report.failures.append(IngestFailure(source=target.name, error=str(exc)))
    else:
        documents = load_directory(target)

    chunks = split_documents(
        documents,
        chunk_tokens=settings.chunk_tokens,
        chunk_overlap=settings.chunk_overlap,
    )
    result = index_documents(
        chunks,
        manager,
        store,
        full_refresh=target.is_dir() if full_refresh is None else full_refresh,
    )

    report.indexed_files = len(documents)
    report.total_chunks = len(chunks)
    report.added = result.get("num_added", 0)
    report.skipped = result.get("num_skipped", 0)
    report.deleted = result.get("num_deleted", 0)
    report.total_in_store = store._collection.count()
    report.elapsed_s = time.perf_counter() - started
    return report


def delete_source(store, settings: Settings, name: str) -> int:
    ids = store.get(where={"source": name}, include=[]).get("ids") or []
    if not ids:
        return 0
    store.delete(ids=ids)
    _record_manager(settings).delete_keys(ids)
    log.info("deleted %d chunk(s) from %s", len(ids), name)
    return len(ids)
