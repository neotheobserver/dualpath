from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Route(str, Enum):
    SQL = "sql"
    DOCS = "docs"
    BOTH = "both"
    DIRECT = "direct"
    REFUSE = "refuse"

    @property
    def needs_sql(self) -> bool:
        return self in (Route.SQL, Route.BOTH)

    @property
    def needs_docs(self) -> bool:
        return self in (Route.DOCS, Route.BOTH)


class Plan(BaseModel):
    route: Route = Field(description="Which source(s) can answer this question.")
    rationale: str = Field(description="One sentence explaining the choice.")
    sql_intent: str = Field(
        default="",
        description=(
            "If the route uses sql: what to compute, in plain language, as a "
            "self-contained instruction. Otherwise an empty string."
        ),
    )
    doc_query: str = Field(
        default="",
        description=(
            "If the route uses docs: the search phrase to retrieve with, using "
            "the vocabulary the documents themselves would use. Otherwise an "
            "empty string."
        ),
    )


class SqlDraft(BaseModel):
    sql: str = Field(description="A single DuckDB SELECT statement. No semicolon.")
    explanation: str = Field(
        default="", description="One sentence: what this statement computes."
    )


class Critique(BaseModel):
    supported: bool = Field(
        description="True only if every factual claim is backed by the evidence provided."
    )
    problems: list[str] = Field(
        default_factory=list,
        description="Specific unsupported claims. Empty when supported is true.",
    )
    suggestion: str = Field(
        default="",
        description=(
            "If not supported: what a second attempt should do differently, "
            "as an instruction to the planner."
        ),
    )


class Contextualised(BaseModel):
    question: str = Field(
        description=(
            "The question, rewritten so it can be understood with no chat "
            "history. Unchanged if it was already self-contained."
        )
    )
