from __future__ import annotations

from typing import Any

from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable

from ..config import Settings
from ..observability import get_logger
from . import prompts, schemas

log = get_logger("llm")


class LLMNotConfigured(RuntimeError):
    """No usable API key. Raised at construction, not at first call."""


def build_models(settings: Settings) -> dict[str, Any]:
    if not settings.has_groq_key:
        raise LLMNotConfigured(
            "GROQ_API_KEY is not set (or is still the placeholder from "
            ".env.example). Get a free key at https://console.groq.com and put "
            "it in your .env, then run `dualpath doctor`."
        )

    from langchain_groq import ChatGroq

    def role(model: str, temperature: float) -> ChatGroq:
        return ChatGroq(
            model=model,
            api_key=settings.groq_api_key,
            temperature=temperature,
            max_tokens=settings.max_tokens,
            timeout=settings.request_timeout,
            max_retries=settings.max_retries,
        )

    return {
        "gen": role(settings.gen_model, settings.gen_temperature),
        "sql": role(settings.sql_model, settings.temperature),
        "util": role(settings.util_model, settings.temperature),
    }


def _structured(model: Any, schema: type, settings: Settings) -> Runnable:
    return model.with_structured_output(
        schema, method=settings.structured_method, include_raw=True
    ).with_retry(stop_after_attempt=2, wait_exponential_jitter=True)


def usage_of(message: Any) -> dict[str, Any]:
    usage = getattr(message, "usage_metadata", None) or {}
    return {
        "model": (getattr(message, "response_metadata", {}) or {}).get("model_name", ""),
        "prompt_tokens": usage.get("input_tokens", 0),
        "completion_tokens": usage.get("output_tokens", 0),
    }


def invoke_structured(chain: Runnable, payload: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    result = chain.invoke(payload)
    parsed = result.get("parsed")
    if parsed is None:
        raise LLMStructureError(
            f"model did not return the requested structure: {result.get('parsing_error')}"
        )
    return parsed, usage_of(result.get("raw"))


class LLMStructureError(RuntimeError):
    """The model could not produce the requested schema, twice."""


def build_chains(settings: Settings, models: dict[str, Any] | None = None) -> dict[str, Runnable]:
    models = models or build_models(settings)
    gen, sql, util = models["gen"], models["sql"], models["util"]

    return {
        "contextualize": prompts.CONTEXTUALIZE
        | _structured(util, schemas.Contextualised, settings),
        "plan": prompts.PLAN | _structured(util, schemas.Plan, settings),
        "write_sql": prompts.SQL_WRITE | _structured(sql, schemas.SqlDraft, settings),
        "repair_sql": prompts.SQL_REPAIR | _structured(sql, schemas.SqlDraft, settings),
        "critique": prompts.CRITIQUE | _structured(util, schemas.Critique, settings),
        "synthesize": prompts.SYNTHESIZE | gen | StrOutputParser(),
        "direct": prompts.DIRECT | util | StrOutputParser(),
    }
