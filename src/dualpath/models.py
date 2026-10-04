from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Any

class ColumnInfo(BaseModel):
    name: str
    data_type: str
    description: str = ""

    def render(self) -> str:
        base = f"{self.name} {self.data_type}"
        return f"{base} -- {self.description}" if self.description else base


class TableInfo(BaseModel):
    name: str
    description: str = ""
    columns: list[ColumnInfo] = Field(default_factory=list)
    row_count: int = 0

    def render(self, include_descriptions: bool = True) -> str:
        lines = [f"TABLE {self.name}  ({self.row_count:,} rows)"]
        if include_descriptions and self.description:
            for para in self.description.split("\n"):
                lines.append(f"  # {para.strip()}")
        for col in self.columns:
            lines.append(f"  - {col.render() if include_descriptions else col.name}")
        return "\n".join(lines)

class GuardViolation(BaseModel):
    rule: str
    detail: str


class GuardVerdict(BaseModel):
    allowed: bool
    sql: str = ""
    violations: list[GuardViolation] = Field(default_factory=list)
    limit_injected: bool = False

    @property
    def reason(self) -> str:
        if self.allowed:
            return "allowed"
        return "; ".join(f"{v.rule}: {v.detail}" for v in self.violations)

class Passage(BaseModel):
    text: str
    source: str
    section: str = ""
    score: float = 0.0

    def citation(self) -> str:
        return f"{self.source} > {self.section}" if self.section else self.source

    @classmethod
    def from_document(cls, document: Any, score: float = 0.0) -> Passage:
        return cls(
            text=document.page_content,
            source=document.metadata.get("source", ""),
            section=document.metadata.get("section", ""),
            score=score,
        )


class Citation(BaseModel):
    marker: str
    source: str
    section: str = ""


class CritiqueResult(BaseModel):
    supported: bool
    problems: list[str] = Field(default_factory=list)
    suggestion: str = ""


class SqlAttempt(BaseModel):
    attempt: int
    sql: str
    ok: bool
    error: str = ""
    blocked_by_guard: bool = False
    row_count: int = 0
    elapsed_ms: float = 0.0


class TableResult(BaseModel):
    sql: str
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    elapsed_ms: float = 0.0

    def to_markdown(self, max_rows: int = 30) -> str:
        if not self.columns:
            return "(no result)"
        if not self.rows:
            return f"(query returned 0 rows)\ncolumns: {', '.join(self.columns)}"
        head = "| " + " | ".join(self.columns) + " |"
        sep = "| " + " | ".join("---" for _ in self.columns) + " |"
        body = []
        for row in self.rows[:max_rows]:
            cells = ["" if v is None else str(v) for v in row]
            body.append("| " + " | ".join(cells) + " |")
        out = [head, sep, *body]
        if self.row_count > max_rows:
            out.append(f"_... {self.row_count - max_rows} more row(s) not shown_")
        return "\n".join(out)


class TraceEvent(BaseModel):
    node: str
    detail: str = ""
    elapsed_ms: float = 0.0


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0

    def add(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += prompt + completion
        self.calls += 1


class AnswerResponse(BaseModel):
    question: str
    resolved_question: str = ""
    route: str = "direct"
    answer: str = ""
    plan_rationale: str = ""

    sql: str = ""
    table: TableResult | None = None
    sql_attempts: list[SqlAttempt] = Field(default_factory=list)

    citations: list[Citation] = Field(default_factory=list)
    contexts: list[Passage] = Field(default_factory=list)

    critique: CritiqueResult | None = None
    replans: int = 0

    trace: list[TraceEvent] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    elapsed_ms: float = 0.0

    thread_id: str = ""
    awaiting_approval: bool = False
    pending_sql: str = ""

    @property
    def stage_summary(self) -> str:
        return "  ".join(f"{t.node}={t.elapsed_ms:.0f}ms" for t in self.trace)
