from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..agent import DualPathAgent
from ..config import Settings, get_settings
from ..ingest.documents import IngestReport, delete_source as delete_source_chunks, ingest_documents
from ..models import AnswerResponse
from ..observability import configure_logging, get_logger
from ..stores.vectors import collection_stats

log = get_logger("api")

_state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info("starting DualPath API")
    if not settings.has_groq_key:
        log.error("GROQ_API_KEY is not set -- /ask will fail until it is")
    _state["settings"] = settings
    try:
        _state["agent"] = DualPathAgent(settings, configure_logs=False)
    except Exception as exc:
        log.error("agent could not be constructed: %s", exc)
        _state["agent"] = None
        _state["error"] = str(exc)
    yield
    agent = _state.get("agent")
    if agent is not None:
        agent.close()
    log.info("stopped")


app = FastAPI(
    title="DualPath",
    version=__version__,
    description=(
        "Agentic RAG across a SQL warehouse and a document corpus. Each question is "
        "routed to structured data, unstructured data, or both, by a LangGraph state "
        "machine with a SQL firewall and a self-repair cycle."
    ),
    lifespan=lifespan,
)


def get_agent() -> DualPathAgent:
    agent = _state.get("agent")
    if agent is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "The agent is not available: "
                f"{_state.get('error', 'unknown error')}. Check GROQ_API_KEY and run "
                "`dualpath doctor`."
            ),
        )
    return agent


def get_agent_optional() -> DualPathAgent | None:
    return _state.get("agent")


def settings_dep() -> Settings:
    return _state.get("settings") or get_settings()


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    thread_id: str | None = Field(
        None, description="Continue a conversation. Omit to start a new one."
    )
    top_k_context: int | None = Field(None, gt=0, le=50)
    mmr_lambda: float | None = Field(None, ge=0.0, le=1.0)
    enable_critique: bool | None = None
    enable_contextualize: bool | None = None
    require_sql_approval: bool | None = None
    max_replans: int | None = Field(None, ge=0, le=3)
    sql_max_repairs: int | None = Field(None, ge=0, le=10)

    def overrides(self) -> dict[str, Any]:
        fields = (
            "top_k_context",
            "mmr_lambda",
            "enable_critique",
            "enable_contextualize",
            "require_sql_approval",
            "max_replans",
            "sql_max_repairs",
        )
        return {f: getattr(self, f) for f in fields if getattr(self, f) is not None}


class ResumeRequest(BaseModel):
    decision: str = Field(
        ...,
        description=(
            "'approve', 'reject', or a replacement SELECT statement. A replacement "
            "is re-validated by the SQL firewall before it runs."
        ),
    )


class IngestPathRequest(BaseModel):
    path: str
    recreate: bool = False


class HealthResponse(BaseModel):
    status: str
    version: str
    has_groq_key: bool
    warehouse_tables: int
    warehouse_rows: int
    document_chunks: int
    chroma_mode: str


@app.get("/health", response_model=HealthResponse, tags=["ops"])
def health(
    settings: Settings = Depends(settings_dep),
    agent: DualPathAgent | None = Depends(get_agent_optional),
) -> HealthResponse:
    tables = rows = chunks = 0
    if agent is not None:
        if agent.engine is not None:
            stats = agent.engine.stats()
            tables = len(stats)
            rows = sum(stats.values())
        if agent.store is not None:
            try:
                chunks = agent.store._collection.count()
            except Exception:
                chunks = 0

    ready = agent is not None and agent.has_model and bool(tables or chunks)
    return HealthResponse(
        status="ready" if ready else "degraded",
        version=__version__,
        has_groq_key=settings.has_groq_key,
        warehouse_tables=tables,
        warehouse_rows=rows,
        document_chunks=chunks,
        chroma_mode=settings.chroma_mode,
    )


@app.get("/config", tags=["ops"])
def read_config(settings: Settings = Depends(settings_dep)) -> dict:
    return settings.redacted()


@app.get("/stats", tags=["ops"])
def stats(agent: DualPathAgent = Depends(get_agent)) -> dict:
    return {
        "warehouse": agent.engine.stats() if agent.engine else {},
        "documents": collection_stats(agent.store) if agent.store else {},
    }


@app.get("/schema", tags=["ops"])
def schema(agent: DualPathAgent = Depends(get_agent)) -> dict:
    if agent.engine is None:
        raise HTTPException(status_code=503, detail="no warehouse is configured")

    tables = agent.engine.catalog.tables
    return {
        "table_count": len(tables),
        "tables": [t.model_dump(mode="json") for t in tables],
        "rendered": agent.engine.catalog.render(),
    }


def _require_model(agent: DualPathAgent) -> None:
    if not agent.has_model:
        raise HTTPException(
            status_code=503,
            detail="No usable GROQ_API_KEY. Set it in .env and run `dualpath doctor`.",
        )


@app.post("/ask", response_model=AnswerResponse, tags=["ask"])
def ask(
    body: AskRequest,
    agent: DualPathAgent = Depends(get_agent),
) -> AnswerResponse:
    _require_model(agent)
    try:
        return agent.ask(
            body.question, thread_id=body.thread_id, overrides=body.overrides() or None
        )
    except Exception as exc:
        log.exception("ask failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


@app.post("/ask/stream", tags=["ask"])
def ask_stream(
    body: AskRequest,
    agent: DualPathAgent = Depends(get_agent),
) -> StreamingResponse:
    _require_model(agent)

    def generate() -> Iterator[str]:
        try:
            for item in agent.stream(
                body.question,
                thread_id=body.thread_id,
                overrides=body.overrides() or None,
            ):
                if item["event"] == "done":
                    yield _sse(
                        {"event": "done", "response": item["response"].model_dump(mode="json")}
                    )
                else:
                    yield _sse(item)
        except Exception as exc:
            log.exception("stream failed")
            yield _sse({"event": "error", "detail": str(exc)})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/threads/{thread_id}/resume", response_model=AnswerResponse, tags=["ask"])
def resume(
    thread_id: str,
    body: ResumeRequest,
    agent: DualPathAgent = Depends(get_agent),
) -> AnswerResponse:
    _require_model(agent)
    try:
        return agent.resume(thread_id, body.decision)
    except Exception as exc:
        log.exception("resume failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/threads/{thread_id}/history", tags=["ask"])
def history(thread_id: str, agent: DualPathAgent = Depends(get_agent)) -> dict:
    turns = agent.history(thread_id)
    if not turns:
        raise HTTPException(status_code=404, detail=f"no conversation {thread_id!r}")
    return {"thread_id": thread_id, "turns": turns}


@app.get("/threads/{thread_id}/pending", tags=["ask"])
def pending(thread_id: str, agent: DualPathAgent = Depends(get_agent)) -> dict:
    return {"thread_id": thread_id, "pending_sql": agent.pending_approval(thread_id)}


def _report_dict(report: IngestReport) -> dict:
    return {
        "indexed_files": report.indexed_files,
        "total_chunks": report.total_chunks,
        "added": report.added,
        "skipped": report.skipped,
        "deleted": report.deleted,
        "elapsed_s": round(report.elapsed_s, 2),
        "failures": [{"source": f.source, "error": f.error} for f in report.failures],
        "total_in_store": report.total_in_store,
    }


@app.get("/sources", tags=["ingest"])
def sources(agent: DualPathAgent = Depends(get_agent)) -> dict:
    if agent.store is None:
        raise HTTPException(status_code=503, detail="no vector store is configured")
    stats = collection_stats(agent.store)
    return {"sources": stats, "total_chunks": sum(stats.values())}


@app.delete("/sources/{name}", tags=["ingest"])
def delete_source(name: str, agent: DualPathAgent = Depends(get_agent)) -> dict:
    if agent.store is None:
        raise HTTPException(status_code=503, detail="no vector store is configured")
    removed = delete_source_chunks(agent.store, agent.settings, name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"no chunks for {name!r}")
    return {"source": name, "removed_chunks": removed}


@app.post("/ingest/upload", tags=["ingest"])
def ingest_upload(
    files: list[UploadFile] = File(...),
    agent: DualPathAgent = Depends(get_agent),
) -> dict:
    if agent.store is None:
        raise HTTPException(status_code=503, detail="no vector store is configured")

    staging = Path(tempfile.mkdtemp(prefix="dualpath-upload-"))
    try:
        for upload in files:
            name = Path(upload.filename or "unnamed").name
            with (staging / name).open("wb") as handle:
                shutil.copyfileobj(upload.file, handle)

        report = ingest_documents(
            staging, store=agent.store, settings=agent.settings, full_refresh=False
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return _report_dict(report)


@app.post("/ingest/path", tags=["ingest"])
def ingest_path(
    body: IngestPathRequest,
    agent: DualPathAgent = Depends(get_agent),
) -> dict:
    if agent.store is None:
        raise HTTPException(status_code=503, detail="no vector store is configured")
    try:
        report = ingest_documents(
            Path(body.path),
            store=agent.store,
            settings=agent.settings,
            recreate=body.recreate,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return _report_dict(report)
