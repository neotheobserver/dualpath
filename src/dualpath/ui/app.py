from __future__ import annotations

import contextlib
import json
import os

import httpx
import streamlit as st
from dotenv import dotenv_values, find_dotenv

API_URL = (
    os.environ.get("DP_API_URL")
    or dotenv_values(find_dotenv(usecwd=True)).get("DP_API_URL")
    or "http://127.0.0.1:8000"
).rstrip("/")
TIMEOUT = httpx.Timeout(180.0, connect=10.0)

ROUTE_BADGE = {
    "sql": ("SQL only", "#0e7490"),
    "docs": ("Documents only", "#a21caf"),
    "both": ("SQL + Documents", "#15803d"),
    "direct": ("Conversational", "#525252"),
    "refuse": ("Out of scope", "#b45309"),
}

st.set_page_config(page_title="DualPath", page_icon="\U0001f500", layout="wide")


def api_get(path: str, **params):
    try:
        response = httpx.get(f"{API_URL}{path}", params=params, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        return {"_error": str(exc)}


def api_post(path: str, payload: dict):
    try:
        response = httpx.post(f"{API_URL}{path}", json=payload, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text
        with contextlib.suppress(Exception):
            detail = exc.response.json().get("detail", detail)
        return {"_error": f"{exc.response.status_code}: {detail}"}
    except Exception as exc:
        return {"_error": str(exc)}


def api_stream(payload: dict, on_node):
    final = None
    try:
        with httpx.stream("POST", f"{API_URL}/ask/stream", json=payload, timeout=TIMEOUT) as response:
            if response.status_code >= 400:
                response.read()
                detail = response.text
                with contextlib.suppress(Exception):
                    detail = response.json().get("detail", detail)
                return {"_error": f"{response.status_code}: {detail}"}
            for line in response.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                kind = event.get("event")
                if kind == "node":
                    on_node(event.get("node", ""), event.get("detail", ""))
                elif kind == "interrupt":
                    on_node("awaiting approval", "paused")
                elif kind == "done":
                    final = event.get("response")
                elif kind == "error":
                    return {"_error": event.get("detail", "unknown streaming error")}
    except Exception as exc:
        return {"_error": str(exc)}
    return final or {"_error": "the stream ended without a response"}


def render_trace(trace: list[dict]) -> None:
    if not trace:
        return
    rows = [
        {
            "step": f"{i + 1}. {event['node']}",
            "ms": round(float(event.get("elapsed_ms", 0)), 1),
            "detail": event.get("detail", ""),
        }
        for i, event in enumerate(trace)
    ]
    slowest = max((r["ms"] for r in rows), default=1.0)
    st.caption("Graph path — a node appearing twice is a repair or replan cycle")
    st.dataframe(
        rows,
        width="stretch",
        hide_index=True,
        column_config={
            "ms": st.column_config.ProgressColumn(
                "ms",
                min_value=0,
                max_value=max(1.0, slowest),
                format="%.0f ms",
            ),
            "detail": st.column_config.TextColumn("detail", width="large"),
        },
    )


def render_sql_panel(response: dict) -> None:
    attempts = response.get("sql_attempts") or []
    sql = response.get("sql") or ""
    if not sql and not attempts:
        st.info("This question did not need the warehouse.")
        return

    if sql:
        st.code(sql, language="sql")

    table = response.get("table")
    if table and table.get("columns"):
        rows = [dict(zip(table["columns"], row)) for row in table["rows"]]
        st.dataframe(rows, width="stretch", hide_index=True)
        caption = f"{table['row_count']} row(s) in {table.get('elapsed_ms', 0):.0f}ms"
        if table.get("truncated"):
            caption += " — truncated by the row cap"
        st.caption(caption)

    if len(attempts) > 1:
        st.markdown("**Self-repair cycle**")
        for attempt in attempts:
            if attempt["ok"]:
                st.success(
                    f"Attempt {attempt['attempt']}: {attempt['row_count']} row(s) "
                    f"in {attempt['elapsed_ms']:.0f}ms"
                )
            else:
                label = (
                    "blocked by the SQL firewall"
                    if attempt.get("blocked_by_guard")
                    else "failed in the database"
                )
                st.error(f"Attempt {attempt['attempt']} {label}: {attempt['error']}")
                with st.expander(f"statement {attempt['attempt']}", expanded=False):
                    st.code(attempt["sql"], language="sql")


def render_passages(response: dict) -> None:
    contexts = response.get("contexts") or []
    if not contexts:
        st.info("This question did not need the documents.")
        return

    cited = {c["marker"] for c in response.get("citations") or []}
    rows = [
        {
            "#": f"[{i}]",
            "cited": "yes" if f"[{i}]" in cited else "",
            "similarity": round(ctx.get("score", 0.0), 3),
            "source": ctx["source"],
            "section": ctx.get("section") or "",
        }
        for i, ctx in enumerate(contexts, start=1)
    ]
    st.caption(
        "Passages are numbered as the generator saw them. A row with an empty "
        "`cited` column was retrieved and not used — which is the signal that "
        "separates a retrieval problem from a prompting one."
    )
    st.dataframe(rows, width="stretch", hide_index=True)

    for i, ctx in enumerate(contexts, start=1):
        marker = "cited" if f"[{i}]" in cited else "not cited"
        section = ctx.get("section") or "(no section)"
        with st.expander(f"[{i}] {ctx['source']} — {section}  ({marker})"):
            st.text(ctx["text"])


def render_answer(response: dict) -> None:
    route = response.get("route", "direct")
    label, colour = ROUTE_BADGE.get(route, (route, "#525252"))

    st.markdown(
        f"<span style='background:{colour};color:#fff;padding:2px 10px;"
        f"border-radius:10px;font-size:0.8em'>{label}</span>",
        unsafe_allow_html=True,
    )
    if response.get("plan_rationale"):
        st.caption(response["plan_rationale"])

    st.markdown(response.get("answer") or "_(no answer)_")

    citations = response.get("citations") or []
    if citations:
        lines = [f"`{c['marker']}` **{c['source']}** — {c.get('section', '')}" for c in citations]
        st.markdown("**Sources**\n\n" + "\n\n".join(lines))

    usage = response.get("usage") or {}
    bits = [
        f"{response.get('elapsed_ms', 0):.0f} ms",
        f"{usage.get('total_tokens', 0)} tokens / {usage.get('calls', 0)} calls",
    ]
    if response.get("replans"):
        bits.append(f"{response['replans']} replan(s)")
    critique = response.get("critique")
    if critique and not critique.get("supported", True):
        bits.append("critique: " + "; ".join(critique.get("problems") or []))
    st.caption("  ·  ".join(bits))

    tabs = st.tabs(["Graph trace", "SQL", "Passages", "Raw JSON"])
    with tabs[0]:
        render_trace(response.get("trace") or [])
    with tabs[1]:
        render_sql_panel(response)
    with tabs[2]:
        render_passages(response)
    with tabs[3]:
        st.json(response)


with st.sidebar:
    st.title("DualPath")
    st.caption("Agentic RAG across structured and unstructured data")

    health = api_get("/health")
    if "_error" in health:
        st.error(
            f"Cannot reach the API at `{API_URL}`.\n\n"
            "Start it in another terminal:\n```\ndualpath serve\n```"
        )
        st.caption(health["_error"])
        st.stop()

    if health.get("status") == "ready":
        st.success("API ready")
    else:
        st.warning("API degraded")
        if not health.get("has_groq_key"):
            st.caption("GROQ_API_KEY is not set")

    st.metric("Warehouse rows", f"{health.get('warehouse_rows', 0):,}")
    st.metric("Document chunks", health.get("document_chunks", 0))
    st.caption(
        f"{health.get('warehouse_tables', 0)} table(s) · "
        f"Chroma: {health.get('chroma_mode', '?')}"
    )

    st.divider()
    st.subheader("Pipeline")
    stream_mode = st.toggle("Stream graph progress", value=True, help="Show each node as it runs.")
    enable_critique = st.toggle(
        "Self-critique", value=True, help="Audit the answer, and replan if it is unsupported."
    )
    enable_contextualize = st.toggle(
        "Resolve follow-ups", value=True, help='Lets "what about last year?" work.'
    )
    require_approval = st.toggle(
        "Require SQL approval",
        value=False,
        help="Pause the graph and show the statement before it runs.",
    )

    st.subheader("Retrieval")
    top_k = st.slider("Passages to the generator", 1, 15, 5)
    mmr_lambda = st.slider(
        "MMR λ  (1.0 = pure relevance)",
        0.0,
        1.0,
        0.6,
        0.05,
        help="Lower it when retrieved passages are near-duplicates.",
    )
    max_repairs = st.slider("Max SQL repair attempts", 0, 6, 3)

    st.divider()
    if st.session_state.get("thread_id"):
        st.caption(f"thread `{st.session_state['thread_id']}`")
    if st.button("New conversation", width="stretch"):
        st.session_state["messages"] = []
        st.session_state["thread_id"] = None
        st.session_state.pop("pending", None)
        st.rerun()

    with st.expander("What am I looking at?"):
        st.markdown(
            "Each question is **routed** by an LLM to the SQL warehouse, the "
            "document corpus, or both.\n\n"
            "Generated SQL passes a **firewall** — parsed, checked against the "
            "table allowlist, rejected unless it is a single read-only "
            "statement — before the database sees it.\n\n"
            "A statement that fails is **repaired** using the database's own "
            "error message, then re-validated.\n\n"
            "The drafted answer is **critiqued**; an unsupported answer sends "
            "the graph back to routing.\n\n"
            "Open **Graph trace** on any answer to see the path it took."
        )


st.session_state.setdefault("messages", [])
st.session_state.setdefault("thread_id", None)

if not st.session_state["messages"]:
    st.markdown("#### Ask something")
    st.caption("The interesting questions are the ones that need both a number and a rule.")
    examples = [
        (
            "Denials + policy (both)",
            "Which denial reason was most common in the third quarter of 2025, "
            "and what should a member do about it?",
        ),
        ("Pure SQL", "What was the total amount the plan paid by month in 2025?"),
        (
            "Pure documents",
            "How long does a member have to file an appeal, and are there any exceptions?",
        ),
        (
            "Network cost (both)",
            "How much more do members pay on average for out-of-network claims, "
            "and why does the policy allow that?",
        ),
    ]
    columns = st.columns(2)
    for index, (label, text) in enumerate(examples):
        with columns[index % 2]:
            if st.button(label, width="stretch", help=text):
                st.session_state["queued"] = text
                st.rerun()

for message in st.session_state["messages"]:
    with st.chat_message(message["role"]):
        if message["role"] == "user":
            st.markdown(message["content"])
        else:
            render_answer(message["response"])

pending = st.session_state.get("pending")
if pending:
    st.warning("The graph is paused, waiting for you to approve this statement.")
    st.code(pending["sql"], language="sql")
    edited = st.text_area("Edit before running (optional)", value=pending["sql"], height=140)
    left, middle, right = st.columns(3)
    decision = None
    if left.button("Approve", type="primary", width="stretch"):
        decision = "approve" if edited.strip() == pending["sql"].strip() else edited
    if middle.button("Reject", width="stretch"):
        decision = "reject"
    if right.button("Run edited SQL", width="stretch"):
        decision = edited
    if decision is not None:
        with st.spinner("resuming the graph..."):
            result = api_post(
                f"/threads/{st.session_state['thread_id']}/resume", {"decision": decision}
            )
        st.session_state.pop("pending", None)
        if "_error" in result:
            st.error(result["_error"])
        else:
            st.session_state["messages"].append({"role": "assistant", "response": result})
        st.rerun()
    st.caption(
        "An edited statement is re-validated by the firewall — a human is no more "
        "trusted than the model here."
    )

question = st.session_state.pop("queued", None) or st.chat_input(
    "Ask about claims, benefits, denials or policy...", disabled=bool(pending)
)

if question and not pending:
    st.session_state["messages"].append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    payload = {
        "question": question,
        "thread_id": st.session_state["thread_id"],
        "top_k_context": top_k,
        "mmr_lambda": mmr_lambda,
        "enable_critique": enable_critique,
        "enable_contextualize": enable_contextualize,
        "require_sql_approval": require_approval,
        "sql_max_repairs": max_repairs,
    }

    with st.chat_message("assistant"):
        if stream_mode:
            progress = st.empty()
            seen: list[str] = []

            def on_node(node: str, detail: str) -> None:
                seen.append(f"`{node}` {detail}" if detail else f"`{node}`")
                progress.markdown("→ " + "  \n→ ".join(seen[-8:]))

            response = api_stream(payload, on_node)
            progress.empty()
        else:
            with st.spinner("routing, querying, retrieving..."):
                response = api_post("/ask", payload)

        if "_error" in response:
            st.error(response["_error"])
        else:
            st.session_state["thread_id"] = response.get("thread_id")
            if response.get("awaiting_approval"):
                st.session_state["pending"] = {"sql": response.get("pending_sql", "")}
                st.rerun()
            render_answer(response)
            st.session_state["messages"].append({"role": "assistant", "response": response})
