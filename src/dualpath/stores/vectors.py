from __future__ import annotations

from typing import Any, Literal

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from ..observability import get_logger

log = get_logger("vectors")

ChromaMode = Literal["local", "server", "cloud", "memory"]

COLLECTION_METADATA = {"hnsw:space": "cosine"}

class VectorStoreError(RuntimeError):
    """The vector store could not be reached or configured."""

def _build_client(
    mode: ChromaMode,
    *,
    path: str | None = None,
    host: str = "localhost",
    port: int = 8001,
    ssl: bool = False,
    api_key: str = "",
    tenant: str = "",
    database: str = "",
) -> Any:
    import chromadb

    if mode == "memory":
        return chromadb.EphemeralClient()
    if mode == "local":
        if not path:
            raise VectorStoreError("CHROMA_MODE=local needs CHROMA_PATH")
        return chromadb.PersistentClient(path=path)
    if mode == "server":
        return chromadb.HttpClient(host=host, port=port, ssl=ssl)
    if mode == "cloud":
        missing = [
            name
            for name, value in (
                ("CHROMA_API_KEY", api_key),
                ("CHROMA_TENANT", tenant),
                ("CHROMA_DATABASE", database),
            )
            if not value
        ]
        if missing:
            raise VectorStoreError(f"CHROMA_MODE=cloud needs {', '.join(missing)}")
        return chromadb.CloudClient(api_key=api_key, tenant=tenant, database=database)
    raise VectorStoreError(f"unknown CHROMA_MODE: {mode!r}")


def build_store(
    embeddings: Embeddings,
    *,
    mode: ChromaMode = "local",
    collection: str = "dualpath_docs",
    path: str | None = None,
    **client_kwargs: Any,
):
    from langchain_chroma import Chroma

    client = _build_client(mode, path=path, **client_kwargs)
    log.info("chroma mode=%s collection=%s", mode, collection)
    return Chroma(
        client=client,
        collection_name=collection,
        embedding_function=embeddings,
        collection_metadata=COLLECTION_METADATA,
    )


def search(
    store,
    query: str,
    *,
    k: int = 5,
    fetch_k: int = 20,
    mmr_lambda: float = 0.6,
    score_floor: float = 0.60,
) -> list[tuple[Document, float]]:

    retriever = store.as_retriever(
        search_type="mmr",
        search_kwargs={"k": k, "fetch_k": fetch_k, "lambda_mult": mmr_lambda},
    )
    selected = retriever.invoke(query)

    scored = {
        doc.page_content: 1.0 - distance
        for doc, distance in store.similarity_search_with_score(query, k=fetch_k)
    }

    out: list[tuple[Document, float]] = []
    for doc in selected:
        similarity = scored.get(doc.page_content, 0.0)
        if similarity >= score_floor:
            out.append((doc, similarity))
        else:
            log.debug("dropped passage below floor: %.3f < %.3f", similarity, score_floor)
    return sorted(out, key=lambda pair: pair[1], reverse=True)


def collection_stats(store) -> dict[str, int]:
    """Chunk count per source, for `dualpath corpus`."""
    data = store.get(include=["metadatas"])
    counts: dict[str, int] = {}
    for metadata in data.get("metadatas") or []:
        source = (metadata or {}).get("source", "?")
        counts[source] = counts.get(source, 0) + 1
    return dict(sorted(counts.items()))

