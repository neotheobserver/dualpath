"""
    START
      |
      v
    prepare ------> plan <--------------------------------+
                     |                                    |
      +--------------+---------------+--------+           | replan
      |              |               |        |           | (bounded)
      v              v               v        v           |
  direct_answer  refuse_answer  retrieve_docs |           |
      |              |               |        |           |
      |              |      needs_sql?        |           |
      |              |         |     |        |           |
      |              |         v     |        |           |
      |              |   write_sql <----------+           |
      |              |         |                          |
      |              |         v                          |
      |              |     write_sql                       |
      |              |         |                           |
      |              |         v                           |
      |              |    +-> guard_sql <--+               |
      |              |    |   |   |   |    |               |
      |              |    |   |   |   |  blocked,          |
      |              |    |   |   |   +--> repair_sql ---+  |
      |              |    |   |   |                      |  |
      |              |  edited |   +--> approve_sql      |  |
      |              |    |    |          (interrupt)    |  |
      |              |    +----|-------------+           |  |
      |              |         v             v           |  |
      |              |     execute_sql <-----+           |  |
      |              |         |   |                     |  |
      |              |         |   +-- error ------------+  |
      |              |         v                            |
      +-----------+   +---> synthesize                      |
                  |         |                               |
                  |         v                               |
                  |     critique --- insufficient -----------+
                  |         |
                  v         v
                 finalize ----> END
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from ..config import Settings
from ..llm.schemas import Route
from ..observability import get_logger
from .nodes import NodeSet
from .state import AgentState

log = get_logger("graph.build")

def _failed_attempts(state: AgentState) -> int:
    return sum(1 for a in (state.get("sql_attempts") or []) if not a.get("ok"))


def route_after_plan(state: AgentState) -> str:
    route_raw = (state.get("plan") or {}).get("route", Route.DOCS.value)
    try:
        route = Route(route_raw)
    except ValueError:
        route = Route.DOCS

    if route is Route.DIRECT:
        return "direct_answer"
    if route is Route.REFUSE:
        return "refuse_answer"
    if route is Route.SQL:
        return "write_sql"
    return "retrieve_docs"


def route_after_retrieve(state: AgentState) -> str:
    route_raw = (state.get("plan") or {}).get("route", Route.DOCS.value)
    return "write_sql" if route_raw == Route.BOTH.value else "synthesize"


def make_route_after_guard(settings: Settings):
    def route_after_guard(state: AgentState) -> str:
        if state.get("sql_abandoned"):
            return "synthesize"

        guard = state.get("guard") or {}
        if not guard.get("allowed", False):
            if _failed_attempts(state) <= settings.sql_max_repairs:
                return "repair_sql"
            return "synthesize"

        if not settings.require_sql_approval:
            return "execute_sql"

        if state.get("approval_note") == "edited by reviewer":
            return "execute_sql"

        return "approve_sql"

    return route_after_guard


def route_after_approve(state: AgentState) -> str:
    if state.get("sql_abandoned"):
        return "synthesize"
    if state.get("approval_note") == "edited by reviewer":
        return "guard_sql"
    return "execute_sql"


def make_route_after_execute(settings: Settings):
    def route_after_execute(state: AgentState) -> str:
        failed = state.get("sql_error") and not state.get("sql_abandoned")
        if failed and _failed_attempts(state) <= settings.sql_max_repairs:
            return "repair_sql"
        return "synthesize"

    return route_after_execute


def make_route_after_synthesize(settings: Settings):
    def route_after_synthesize(state: AgentState) -> str:
        return "critique" if settings.enable_critique else "finalize"

    return route_after_synthesize


def route_after_critique(state: AgentState) -> str:
    return "plan" if state.get("critique_feedback") else "finalize"

def build_graph(nodes: NodeSet, settings: Settings, checkpointer: Any | None = None):
    """Wire the nodes into a compiled LangGraph application."""
    graph = StateGraph(AgentState)

    graph.add_node("prepare", nodes.prepare)
    graph.add_node("plan", nodes.plan)
    graph.add_node("retrieve_docs", nodes.retrieve_docs)
    graph.add_node("write_sql", nodes.write_sql)
    graph.add_node("guard_sql", nodes.guard_sql)
    graph.add_node("approve_sql", nodes.approve_sql)
    graph.add_node("execute_sql", nodes.execute_sql)
    graph.add_node("repair_sql", nodes.repair_sql)
    graph.add_node("synthesize", nodes.synthesize)
    graph.add_node("critique", nodes.critique)
    graph.add_node("direct_answer", nodes.direct_answer)
    graph.add_node("refuse_answer", nodes.refuse_answer)
    graph.add_node("finalize", nodes.finalize)

    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "plan")

    graph.add_conditional_edges(
        "plan",
        route_after_plan,
        {
            "direct_answer": "direct_answer",
            "refuse_answer": "refuse_answer",
            "retrieve_docs": "retrieve_docs",
            "write_sql": "write_sql",
        },
    )

    graph.add_conditional_edges(
        "retrieve_docs",
        route_after_retrieve,
        {"write_sql": "write_sql", "synthesize": "synthesize"},
    )

    graph.add_edge("write_sql", "guard_sql")

    graph.add_conditional_edges(
        "guard_sql",
        make_route_after_guard(settings),
        {
            "approve_sql": "approve_sql",
            "execute_sql": "execute_sql",
            "repair_sql": "repair_sql",
            "synthesize": "synthesize",
        },
    )

    graph.add_conditional_edges(
        "approve_sql",
        route_after_approve,
        {
            "execute_sql": "execute_sql",
            "guard_sql": "guard_sql",
            "synthesize": "synthesize",
        },
    )

    graph.add_conditional_edges(
        "execute_sql",
        make_route_after_execute(settings),
        {"repair_sql": "repair_sql", "synthesize": "synthesize"},
    )

    graph.add_edge("repair_sql", "guard_sql")

    graph.add_conditional_edges(
        "synthesize",
        make_route_after_synthesize(settings),
        {"critique": "critique", "finalize": "finalize"},
    )

    graph.add_conditional_edges(
        "critique", route_after_critique, {"plan": "plan", "finalize": "finalize"}
    )

    graph.add_edge("direct_answer", "finalize")
    graph.add_edge("refuse_answer", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer)

