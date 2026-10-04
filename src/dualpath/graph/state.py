from __future__ import annotations

from typing import Annotated, Any

from typing_extensions import TypedDict


class Reset(list):
    """A channel update that replaces the channel instead of appending to it.
    Returned by the entry node for per-turn channels. A plain `list` appends.
    """


def accumulate(left: list | None, right: list | None) -> list:
    if isinstance(right, Reset):
        return list(right)
    if right is None:
        return list(left or [])
    return list(left or []) + list(right)


class AgentState(TypedDict, total=False):
    question: str

    history: Annotated[list[dict[str, str]], accumulate]

    resolved_question: str
    plan: dict[str, Any]
    critique_feedback: str
    replans: int

    schema_tables: list[str]
    sql: str
    guard: dict[str, Any]
    table: dict[str, Any] | None
    sql_attempts: Annotated[list[dict[str, Any]], accumulate]
    sql_error: str
    sql_abandoned: bool

    contexts: list[dict[str, Any]]

    answer: str
    citations: list[dict[str, Any]]
    critique: dict[str, Any] | None

    trace: Annotated[list[dict[str, Any]], accumulate]
    usage: Annotated[list[dict[str, Any]], accumulate]

    awaiting_approval: bool
    approval_note: str
