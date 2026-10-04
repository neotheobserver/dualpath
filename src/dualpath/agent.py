from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from typing import Any

from .config import Settings, get_settings
from .embeddings import FastEmbedEmbeddings
from .graph.build import build_graph
from .graph.checkpointer import build_checkpointer, build_memory_checkpointer
from .graph.nodes import NodeSet
from .llm.chains import LLMNotConfigured, build_chains
from .models import (
    AnswerResponse,
    Citation,
    CritiqueResult,
    Passage,
    SqlAttempt,
    TableResult,
    TokenUsage,
    TraceEvent,
)
from .observability import configure_logging, get_logger
from .sql.engine import WarehouseEngine, WarehouseError
from .stores.vectors import VectorStoreError, build_store

log = get_logger("agent")


class DualPathAgent:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        chains: dict[str, Any] | None = None,
        store: Any | None = None,
        engine: WarehouseEngine | None = None,
        persist_memory: bool = True,
        configure_logs: bool = True,
    ) -> None:
        self.settings = settings or get_settings()
        if configure_logs:
            configure_logging(self.settings.log_level)

        self.chains = chains if chains is not None else self._build_chains()
        self.store = store if store is not None else self._build_store()
        self.engine = engine if engine is not None else self._build_engine()

        self.checkpointer = (
            build_checkpointer(self.settings.memory_path)
            if persist_memory and self.settings.memory_enabled
            else build_memory_checkpointer()
        )

        self._nodes = self._build_nodes(self.settings)
        self._graph = build_graph(self._nodes, self.settings, self.checkpointer)
        self._override_graphs: dict[tuple, Any] = {}

    def _build_chains(self) -> dict[str, Any]:
        try:
            return build_chains(self.settings)
        except LLMNotConfigured as exc:
            log.warning("model unavailable: %s", exc)
            return {}

    def _build_store(self) -> Any | None:
        try:
            embeddings = FastEmbedEmbeddings(
                model_name=self.settings.embedding_model,
                cache_dir=self.settings.embedding_cache_dir,
            )
            return build_store(
                embeddings,
                mode=self.settings.chroma_mode,
                collection=self.settings.collection,
                path=str(self.settings.chroma_path),
                host=self.settings.chroma_host,
                port=self.settings.chroma_port,
                ssl=self.settings.chroma_ssl,
                api_key=self.settings.chroma_api_key,
                tenant=self.settings.chroma_tenant,
                database=self.settings.chroma_database,
            )
        except (VectorStoreError, Exception) as exc:
            log.warning("vector store unavailable, document search disabled: %s", exc)
            return None

    def _build_engine(self) -> WarehouseEngine | None:
        try:
            return WarehouseEngine(
                self.settings.warehouse_path,
                docs_dir=self.settings.warehouse_csv_dir,
                max_rows=self.settings.sql_max_rows,
                max_seconds=self.settings.sql_max_seconds,
            )
        except WarehouseError as exc:
            log.warning("warehouse unavailable, SQL disabled: %s", exc)
            return None

    def _build_nodes(self, settings: Settings) -> NodeSet:
        return NodeSet(settings, self.chains, engine=self.engine, store=self.store)

    def _graph_for(self, overrides: dict[str, Any] | None):
        if not overrides:
            return self._graph
        signature = tuple(sorted((k, str(v)) for k, v in overrides.items()))
        if signature not in self._override_graphs:
            merged = self.settings.model_copy(update=overrides)
            self._override_graphs[signature] = build_graph(
                self._build_nodes(merged), merged, self.checkpointer
            )
        return self._override_graphs[signature]

    @property
    def has_model(self) -> bool:
        return bool(self.chains)

    @property
    def has_warehouse(self) -> bool:
        return self.engine is not None and len(self.engine.catalog) > 0

    @property
    def has_documents(self) -> bool:
        if self.store is None:
            return False
        try:
            return self.store._collection.count() > 0
        except Exception:
            return False

    def ask(
        self,
        question: str,
        *,
        thread_id: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> AnswerResponse:
        thread = thread_id or uuid.uuid4().hex[:12]
        graph = self._graph_for(overrides)
        started = time.perf_counter()

        raw = graph.invoke({"question": question}, config=self._config(thread))
        return self._to_response(raw, question=question, thread_id=thread, started=started)

    def resume(
        self,
        thread_id: str,
        decision: str,
        *,
        overrides: dict[str, Any] | None = None,
    ) -> AnswerResponse:
        from langgraph.types import Command

        graph = self._graph_for(overrides)
        started = time.perf_counter()
        raw = graph.invoke(Command(resume=decision), config=self._config(thread_id))
        snapshot = graph.get_state(self._config(thread_id))
        question = (snapshot.values or {}).get("question", "")
        return self._to_response(raw, question=question, thread_id=thread_id, started=started)

    def stream(
        self,
        question: str,
        *,
        thread_id: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        thread = thread_id or uuid.uuid4().hex[:12]
        graph = self._graph_for(overrides)
        started = time.perf_counter()

        for update in graph.stream(
            {"question": question}, config=self._config(thread), stream_mode="updates"
        ):
            for node, delta in (update or {}).items():
                if node == "__interrupt__":
                    yield {"event": "interrupt", "payload": _interrupt_payload(delta)}
                    continue
                yield {"event": "node", "node": node, "detail": _node_detail(delta)}

        snapshot = graph.get_state(self._config(thread))
        yield {
            "event": "done",
            "response": self._to_response(
                snapshot.values or {},
                question=question,
                thread_id=thread,
                started=started,
                interrupts=getattr(snapshot, "interrupts", ()) or (),
            ),
        }

    def history(self, thread_id: str) -> list[dict[str, str]]:
        snapshot = self._graph.get_state(self._config(thread_id))
        return list((snapshot.values or {}).get("history") or [])

    def pending_approval(self, thread_id: str) -> str | None:
        snapshot = self._graph.get_state(self._config(thread_id))
        for task in getattr(snapshot, "tasks", ()) or ():
            for pending in getattr(task, "interrupts", ()) or ():
                value = getattr(pending, "value", None)
                if isinstance(value, dict) and value.get("type") == "sql_approval":
                    return str(value.get("sql", ""))
        return None

    def close(self) -> None:
        if self.engine is not None:
            self.engine.close()

    def __enter__(self) -> DualPathAgent:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _config(self, thread_id: str) -> dict:
        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": self.settings.recursion_limit,
        }

    def _to_response(
        self,
        raw: dict[str, Any],
        *,
        question: str,
        thread_id: str,
        started: float,
        interrupts: tuple = (),
    ) -> AnswerResponse:
        plan = raw.get("plan") or {}

        usage = TokenUsage()
        for record in raw.get("usage") or []:
            usage.add(
                int(record.get("prompt_tokens") or 0),
                int(record.get("completion_tokens") or 0),
            )

        pending_sql = ""
        payload = _interrupt_payload(raw.get("__interrupt__")) or _interrupt_payload(interrupts)
        if isinstance(payload, dict) and payload.get("type") == "sql_approval":
            pending_sql = str(payload.get("sql", ""))

        table_raw = raw.get("table")
        critique_raw = raw.get("critique")

        return AnswerResponse(
            question=question,
            resolved_question=raw.get("resolved_question") or "",
            route=str(plan.get("route") or "direct"),
            answer=raw.get("answer") or "",
            plan_rationale=str(plan.get("rationale") or ""),
            sql=raw.get("sql") or "",
            table=TableResult(**table_raw) if table_raw else None,
            sql_attempts=[SqlAttempt(**a) for a in raw.get("sql_attempts") or []],
            citations=[Citation(**c) for c in raw.get("citations") or []],
            contexts=[Passage(**c) for c in raw.get("contexts") or []],
            critique=CritiqueResult(**critique_raw) if critique_raw else None,
            replans=int(raw.get("replans") or 0),
            trace=[
                TraceEvent(node=t["node"], detail=t.get("detail", ""), elapsed_ms=t.get("ms", 0.0))
                for t in raw.get("trace") or []
            ],
            usage=usage,
            elapsed_ms=round((time.perf_counter() - started) * 1000.0, 1),
            thread_id=thread_id,
            awaiting_approval=bool(pending_sql),
            pending_sql=pending_sql,
        )


def _interrupt_payload(raw: Any) -> Any:
    if not raw:
        return None
    if isinstance(raw, (list, tuple)):
        first = raw[0]
        return getattr(first, "value", first)
    return getattr(raw, "value", raw)


def _node_detail(delta: Any) -> str:
    if not isinstance(delta, dict):
        return ""
    events = delta.get("trace")
    if isinstance(events, list) and events:
        last = events[-1]
        if isinstance(last, dict):
            return str(last.get("detail") or "")
    return ""
