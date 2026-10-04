from __future__ import annotations

import re
import time
from typing import Any

from ..config import Settings
from ..llm.chains import LLMStructureError, invoke_structured, usage_of
from ..llm.prompts import REFUSAL_TEMPLATE, SQL_EXAMPLES
from ..llm.schemas import Route
from ..models import Citation, CritiqueResult, Passage, SqlAttempt, TableResult
from ..observability import get_logger
from ..sql.guard import validate_sql
from ..stores.vectors import search as vector_search
from .state import AgentState, Reset

log = get_logger("graph")

_MARKER_RE = re.compile(r"\[(\d{1,2})\]")

def _now_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0

class NodeSet:
    def __init__(
        self,
        settings: Settings,
        chains: dict[str, Any],
        engine: Any | None = None,
        store: Any | None = None,
    ) -> None:
        self.settings = settings
        self.chains = chains
        self.engine = engine
        self.store = store
        self._source_cache: list[str] | None = None

    def _trace(self, node: str, started: float, detail: str = "") -> list[dict]:
        return [{"node": node, "ms": round(_now_ms(started), 1), "detail": detail}]

    def _question(self, state: AgentState) -> str:
        return (state.get("resolved_question") or state.get("question") or "").strip()

    def _passages(self, state: AgentState) -> list[Passage]:
        return [Passage(**c) for c in (state.get("contexts") or [])]

    def _table(self, state: AgentState) -> TableResult | None:
        raw = state.get("table")
        return TableResult(**raw) if raw else None

    def _render_passages(self, passages: list[Passage]) -> str:
        if not passages:
            return "(no passages retrieved)"
        return "\n\n".join(
            f"[{i}] ({p.citation()})\n{p.text}" for i, p in enumerate(passages, start=1)
        )

    def prepare(self, state: AgentState) -> dict:
        started = time.perf_counter()
        question = (state.get("question") or "").strip()
        history = list(state.get("history") or [])

        resolved = question
        detail = "standalone"
        usage: list[dict] = []

        if self.settings.enable_contextualize and history:
            recent = history[-(self.settings.history_turns * 2) :]
            rendered = "\n".join(f"{m['role']}: {m['content']}" for m in recent)
            try:
                result, used = invoke_structured(
                    self.chains["contextualize"],
                    {"history": rendered, "question": question},
                )
                usage = [used]
                candidate = result.question.strip()
                if candidate:
                    resolved = candidate
                    detail = "rewritten" if candidate != question else "standalone"
            except (LLMStructureError, Exception) as exc:  # noqa: BLE001
                log.warning("contextualize failed, using the question as-is: %s", exc)
                detail = "rewrite failed"

        return {
            "resolved_question": resolved,
            "history": [{"role": "user", "content": question}],
            "trace": Reset(self._trace("prepare", started, detail)),
            "usage": Reset(usage),
            "sql_attempts": Reset([]),
            "contexts": [],
            "table": None,
            "sql": "",
            "guard": {},
            "sql_error": "",
            "sql_abandoned": False,
            "citations": [],
            "critique": None,
            "critique_feedback": "",
            "replans": 0,
            "awaiting_approval": False,
            "approval_note": "",
            "answer": "",
        }

    def plan(self, state: AgentState) -> dict:
        started = time.perf_counter()
        question = self._question(state)

        feedback = ""
        if state.get("critique_feedback"):
            feedback = (
                "\n\nIMPORTANT -- a previous attempt at this question was judged "
                f"insufficient for these reasons:\n{state['critique_feedback']}\n"
                "Choose a route that fixes that gap. If the previous attempt "
                "lacked numbers, include sql; if it lacked policy grounding, "
                "include docs."
            )

        table_summaries = self.engine.catalog.summaries() if self.engine else "(no warehouse)"
        sources = "\n".join(f"- {s}" for s in self._sources()) or "- (corpus is empty)"

        try:
            plan, used = invoke_structured(
                self.chains["plan"],
                {
                    "domain_description": self.settings.domain_description,
                    "table_summaries": table_summaries,
                    "doc_sources": sources,
                    "question": question,
                    "feedback": feedback,
                },
            )
            route = plan.route
        except Exception as exc:  # noqa: BLE001
            log.warning("planning failed, defaulting to docs: %s", exc)
            return {
                "plan": {"route": Route.DOCS.value, "rationale": f"planner failed: {exc}",
                         "sql_intent": "", "doc_query": question},
                "trace": self._trace("plan", started, "planner failed -> docs"),
            }

        if route.needs_sql and self.engine is None:
            route = Route.DOCS if route is Route.BOTH else Route.REFUSE
        if route.needs_docs and self.store is None:
            route = Route.SQL if route is Route.BOTH else Route.REFUSE

        return {
            "plan": {
                "route": route.value,
                "rationale": plan.rationale,
                "sql_intent": plan.sql_intent,
                "doc_query": plan.doc_query or question,
            },
            "trace": self._trace("plan", started, f"{route.value}: {plan.rationale[:70]}"),
            "usage": [used],
        }

    def _sources(self) -> list[str]:
        if self._source_cache is None:
            from ..stores.vectors import collection_stats

            self._source_cache = sorted(collection_stats(self.store)) if self.store else []
        return self._source_cache

    def retrieve_docs(self, state: AgentState) -> dict:
        started = time.perf_counter()
        if self.store is None:
            return {"contexts": [], "trace": self._trace("retrieve_docs", started, "no store")}

        plan = state.get("plan") or {}
        query = plan.get("doc_query") or self._question(state)

        hits = vector_search(
            self.store,
            query,
            k=self.settings.top_k_context,
            fetch_k=self.settings.top_k_candidates,
            mmr_lambda=self.settings.mmr_lambda,
            score_floor=self.settings.score_floor,
        )
        passages = [Passage.from_document(doc, score) for doc, score in hits]

        detail = (
            f"{len(passages)} passage(s), top={passages[0].score:.3f}"
            if passages
            else f"nothing above the floor ({self.settings.score_floor:.2f})"
        )
        return {
            "contexts": [p.model_dump(mode="json") for p in passages],
            "trace": self._trace("retrieve_docs", started, detail),
        }

    def write_sql(self, state: AgentState) -> dict:
        started = time.perf_counter()
        plan = state.get("plan") or {}
        intent = plan.get("sql_intent") or self._question(state)

        try:
            draft, used = invoke_structured(
                self.chains["write_sql"],
                {
                    "schema": self.engine.catalog.render(),
                    "examples": SQL_EXAMPLES,
                    "intent": intent,
                    "max_rows": self.settings.sql_max_rows,
                },
            )
        except Exception as exc:
            return {
                "sql": "",
                "sql_error": f"could not draft SQL: {exc}",
                "sql_abandoned": True,
                "trace": self._trace("write_sql", started, "draft failed"),
            }

        return {
            "sql": draft.sql.strip(),
            "sql_error": "",
            "trace": self._trace("write_sql", started, draft.explanation[:70]),
            "usage": [used],
        }

    def guard_sql(self, state: AgentState) -> dict:
        started = time.perf_counter()
        candidate = state.get("sql") or ""
        allowed = self.engine.table_names() if self.engine else set()

        verdict = validate_sql(
            candidate,
            allowed_tables=allowed,
            max_rows=self.settings.sql_max_rows,
            allow_multiple=self.settings.sql_allow_multiple,
        )
        attempt_number = len(state.get("sql_attempts") or []) + 1

        if not verdict.allowed:
            log.warning("firewall rejected a statement: %s", verdict.reason)
            return {
                "guard": verdict.model_dump(mode="json"),
                "sql_error": verdict.reason,
                "sql_attempts": [
                    SqlAttempt(
                        attempt=attempt_number,
                        sql=candidate,
                        ok=False,
                        error=verdict.reason,
                        blocked_by_guard=True,
                    ).model_dump(mode="json")
                ],
                "trace": self._trace("guard_sql", started, f"BLOCKED: {verdict.reason[:80]}"),
            }

        detail = "allowed" + (" (limit enforced)" if verdict.limit_injected else "")
        return {
            "sql": verdict.sql,
            "guard": verdict.model_dump(mode="json"),
            "sql_error": "",
            "trace": self._trace("guard_sql", started, detail),
        }

    def approve_sql(self, state: AgentState) -> dict:
        started = time.perf_counter()
        if not self.settings.require_sql_approval:
            return {"trace": self._trace("approve_sql", started, "approval disabled")}

        from langgraph.types import interrupt

        decision = interrupt(
            {
                "type": "sql_approval",
                "question": self._question(state),
                "sql": state.get("sql", ""),
                "guard": state.get("guard", {}),
            }
        )

        lowered = str(decision or "").strip().lower()
        if lowered in {"approve", "approved", "yes", "y", "ok", "true"}:
            return {
                "awaiting_approval": False,
                "approval_note": "approved",
                "trace": self._trace("approve_sql", started, "approved"),
            }
        if lowered in {"reject", "rejected", "no", "n", "false", ""}:
            return {
                "awaiting_approval": False,
                "approval_note": "rejected by reviewer",
                "sql_abandoned": True,
                "sql_error": "the reviewer rejected the statement",
                "trace": self._trace("approve_sql", started, "rejected"),
            }

        return {
            "awaiting_approval": False,
            "approval_note": "edited by reviewer",
            "sql": str(decision).strip(),
            "trace": self._trace("approve_sql", started, "edited"),
        }

    def execute_sql(self, state: AgentState) -> dict:
        started = time.perf_counter()
        statement = state.get("sql") or ""
        attempt_number = len(state.get("sql_attempts") or []) + 1

        try:
            result = self.engine.execute(statement)
        except Exception as exc:
            message = str(exc)
            log.info("sql failed (attempt %d): %s", attempt_number, message[:120])
            return {
                "sql_error": message,
                "sql_attempts": [
                    SqlAttempt(
                        attempt=attempt_number,
                        sql=statement,
                        ok=False,
                        error=message,
                        elapsed_ms=round(_now_ms(started), 1),
                    ).model_dump(mode="json")
                ],
                "trace": self._trace("execute_sql", started, f"error: {message[:70]}"),
            }

        return {
            "table": result.model_dump(mode="json"),
            "sql_error": "",
            "sql_attempts": [
                SqlAttempt(
                    attempt=attempt_number,
                    sql=statement,
                    ok=True,
                    row_count=result.row_count,
                    elapsed_ms=result.elapsed_ms,
                ).model_dump(mode="json")
            ],
            "trace": self._trace("execute_sql", started, f"{result.row_count} row(s)"),
        }

    def repair_sql(self, state: AgentState) -> dict:
        started = time.perf_counter()
        plan = state.get("plan") or {}

        try:
            draft, used = invoke_structured(
                self.chains["repair_sql"],
                {
                    "schema": self.engine.catalog.render(),
                    "intent": plan.get("sql_intent") or self._question(state),
                    "sql": state.get("sql") or "",
                    "error": state.get("sql_error") or "",
                    "max_rows": self.settings.sql_max_rows,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "sql_abandoned": True,
                "trace": self._trace("repair_sql", started, f"repair failed: {exc}"),
            }

        return {
            "sql": draft.sql.strip(),
            "approval_note": "",
            "trace": self._trace("repair_sql", started, draft.explanation[:70]),
            "usage": [used],
        }

    def synthesize(self, state: AgentState) -> dict:
        started = time.perf_counter()
        question = self._question(state)
        passages = self._passages(state)
        table = self._table(state)

        message = self.chains["synthesize"].invoke(
            {
                "question": question,
                "table": table.to_markdown() if table else "(no SQL was run)",
                "passages": self._render_passages(passages),
            }
        )
        answer = message.strip() if isinstance(message, str) else str(message).strip()

        citations = _parse_citations(answer, passages)
        return {
            "answer": answer,
            "citations": [c.model_dump(mode="json") for c in citations],
            "trace": self._trace(
                "synthesize", started, f"{len(answer)} chars, {len(citations)} citation(s)"
            ),
        }

    def critique(self, state: AgentState) -> dict:
        started = time.perf_counter()
        table = self._table(state)

        try:
            verdict, used = invoke_structured(
                self.chains["critique"],
                {
                    "question": self._question(state),
                    "table": table.to_markdown() if table else "(no SQL was run)",
                    "passages": self._render_passages(self._passages(state)),
                    "answer": state.get("answer") or "",
                },
            )
        except Exception as exc:
            log.warning("critique failed, accepting the answer: %s", exc)
            return {
                "critique": CritiqueResult(supported=True).model_dump(mode="json"),
                "trace": self._trace("critique", started, "unavailable"),
                "usage": [],
            }

        result = CritiqueResult(
            supported=verdict.supported,
            problems=[p for p in verdict.problems if p.strip()][:5],
            suggestion=verdict.suggestion,
        )
        replans = int(state.get("replans") or 0)

        updates: dict = {
            "critique": result.model_dump(mode="json"),
            "trace": self._trace(
                "critique",
                started,
                "supported" if result.supported else f"unsupported: {result.problems[:1]}",
            ),
            "usage": [used],
        }
        if not result.supported and replans < self.settings.max_replans:
            updates["replans"] = replans + 1
            updates["critique_feedback"] = (
                result.suggestion or "; ".join(result.problems) or "answer was unsupported"
            )
            log.info("critique sent the graph back to planning")

        return updates

    def direct_answer(self, state: AgentState) -> dict:
        started = time.perf_counter()
        message = self.chains["direct"].invoke(
            {"question": self._question(state), "domain": self.settings.domain_description}
        )
        answer = message.strip() if isinstance(message, str) else str(message).strip()
        return {
            "answer": answer,
            "citations": [],
            "trace": self._trace("direct_answer", started, ""),
        }

    def refuse_answer(self, state: AgentState) -> dict:
        started = time.perf_counter()
        return {
            "answer": REFUSAL_TEMPLATE.format(domain=self.settings.domain_description),
            "citations": [],
            "trace": self._trace("refuse_answer", started, "out of domain"),
        }

    def finalize(self, state: AgentState) -> dict:
        started = time.perf_counter()
        return {
            "history": [{"role": "assistant", "content": state.get("answer") or ""}],
            "trace": self._trace("finalize", started, ""),
        }

def _parse_citations(answer: str, passages: list[Passage]) -> list[Citation]:
    if not passages:
        return []

    citations: list[Citation] = []
    seen: set[int] = set()
    for match in _MARKER_RE.finditer(answer):
        marker = int(match.group(1))
        if marker in seen or not 1 <= marker <= len(passages):
            continue
        seen.add(marker)
        passage = passages[marker - 1]
        citations.append(
            Citation(marker=f"[{marker}]", source=passage.source, section=passage.section)
        )
    return citations
