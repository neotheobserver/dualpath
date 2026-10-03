from __future__ import annotations

from pathlib import Path

from langchain_core.embeddings import Embeddings

class FastEmbedEmbeddings(Embeddings):
    def __init__(
        self,
        model_name: str = "BAAI/bge-small-en-v1.5",
        cache_dir: str | Path | None = None,
        threads: int | None = None,
    ) -> None:
        self.model_name = model_name
        self.cache_dir = str(cache_dir) if cache_dir else None
        self.threads = threads
        self._model = None

    def _get_model(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(
                model_name=self.model_name,
                cache_dir=self.cache_dir,
                threads=self.threads,
            )
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._get_model().embed(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._get_model().query_embed([text]))).tolist()

    @property
    def dimension(self) -> int:
        """Vector width. Changing the model changes this -- re-ingest."""
        return len(self.embed_query("dimension probe"))

