from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from dualpath.models import AnswerResponse

app = typer.Typer(
    help="DualPath -- agentic RAG over structured and unstructured data.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

_ROUTE_STYLE = {
    "sql": "cyan",
    "docs": "green",
    "both": "magenta",
    "direct": "blue",
    "refuse": "yellow",
}


def _agent(overrides: dict | None = None):
    from dualpath import DualPathAgent
    from dualpath.config import get_settings

    settings = get_settings()
    if overrides:
        settings = settings.model_copy(update=overrides)
    agent = DualPathAgent(settings)
    if not agent.has_model:
        agent.close()
        console.print(
            "[red]No usable GROQ_API_KEY.[/red] Run [bold]dualpath doctor[/bold] "
            "for details. Retrieval commands (search, sql) still work."
        )
        raise typer.Exit(1)
    return agent


@app.command()
def version() -> None:
    """Print the installed version."""
    from dualpath import __version__

    typer.echo(f"dualpath {__version__}")


@app.command()
def config() -> None:
    from rich.console import Console
    from rich.table import Table

    from dualpath.config import get_settings

    settings = get_settings()
    table = Table(title="DualPath configuration", title_style="bold")
    table.add_column("setting", style="cyan")
    table.add_column("value")
    for key, value in settings.redacted().items():
        table.add_row(key, str(value))
    Console().print(table)

@app.command("build-warehouse")
def build_warehouse_cmd(
    csv_dir: str = typer.Option(None, "--csv-dir", help="Override DP_WAREHOUSE_CSV_DIR."),
    recreate: bool = typer.Option(True, "--recreate/--append", help="Replace existing tables."),
) -> None:
    from rich.console import Console

    from dualpath.config import get_settings
    from dualpath.observability import configure_logging
    from dualpath.sql.warehouse import build_warehouse

    settings = get_settings()
    configure_logging(settings.log_level)
    console = Console()

    report = build_warehouse(
        csv_dir or settings.warehouse_csv_dir,
        settings.warehouse_path,
        recreate=recreate,
    )
    for name, rows in sorted(report.tables.items()):
        console.print(f"  [green]{name:16}[/green] {rows:>7,} rows")
    for name, why in report.skipped:
        console.print(f"  [yellow]{name:16}[/yellow] skipped -- {why}")
    console.print(f"\n{report.total_rows:,} rows in {report.warehouse_path}")


@app.command()
def schema() -> None:
    import duckdb
    from rich.console import Console

    from dualpath.config import get_settings
    from dualpath.sql.catalog import Catalog

    settings = get_settings()
    if not settings.warehouse_path.exists():
        raise typer.Exit(
            typer.echo("No warehouse. Run: dualpath build-warehouse", err=True) or 1
        )

    con = duckdb.connect(str(settings.warehouse_path), read_only=True)
    try:
        catalog = Catalog.from_connection(con, docs_dir=settings.warehouse_csv_dir)
    finally:
        con.close()

    console = Console()
    console.print(f"[bold]{len(catalog)} tables[/bold]\n")
    console.print(catalog.render())

@app.command("check-sql")
def check_sql_cmd(
    sql: str = typer.Argument(..., help="A candidate statement to validate."),
) -> None:
    import duckdb
    from rich.console import Console

    from dualpath.config import get_settings
    from dualpath.sql.catalog import Catalog
    from dualpath.sql.guard import validate_sql

    settings = get_settings()
    console = Console()

    allowed: set[str] = set()
    if settings.warehouse_path.exists():
        con = duckdb.connect(str(settings.warehouse_path), read_only=True)
        try:
            allowed = Catalog.from_connection(con).table_names
        finally:
            con.close()

    verdict = validate_sql(
        sql,
        allowed_tables=allowed,
        max_rows=settings.sql_max_rows,
        allow_multiple=settings.sql_allow_multiple,
    )

    if verdict.allowed:
        console.print("[bold green]ALLOWED[/bold green]")
        if verdict.limit_injected:
            console.print(f"[yellow]LIMIT {settings.sql_max_rows} injected[/yellow]")
        console.print(f"\n[dim]normalised:[/dim]\n{verdict.sql}")
        return

    console.print("[bold red]BLOCKED[/bold red]")
    for violation in verdict.violations:
        console.print(f"  [red]{violation.rule}[/red]: {violation.detail}")
    raise typer.Exit(1)

@app.command()
def sql(
    statement: str = typer.Argument(..., help="A SELECT to validate and run."),
    max_rows: int = typer.Option(None, "--max-rows", help="Override DP_SQL_MAX_ROWS."),
) -> None:
    from dualpath.config import get_settings
    from dualpath.observability import configure_logging
    from dualpath.sql.engine import SqlExecutionError, WarehouseEngine, WarehouseError
    from dualpath.sql.guard import validate_sql

    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        engine = WarehouseEngine(
            settings.warehouse_path,
            docs_dir=settings.warehouse_csv_dir,
            max_seconds=settings.sql_max_seconds,
            max_rows=max_rows or settings.sql_max_rows,
        )
    except WarehouseError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    with engine:
        verdict = validate_sql(
            statement,
            allowed_tables=engine.table_names(),
            max_rows=max_rows or settings.sql_max_rows,
            allow_multiple=settings.sql_allow_multiple,
        )
        if not verdict.allowed:
            console.print("[bold red]BLOCKED[/bold red]")
            for violation in verdict.violations:
                console.print(f"  [red]{violation.rule}[/red]: {violation.detail}")
            raise typer.Exit(1)

        try:
            result = engine.execute(verdict.sql)
        except SqlExecutionError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1)

    table = Table(box=None, pad_edge=False)
    for column in result.columns:
        table.add_column(column, overflow="fold")
    for row in result.rows[:50]:
        table.add_row(*("" if v is None else str(v) for v in row))
    console.print(table)

    suffix = " (truncated)" if result.truncated else ""
    console.print(f"\n[dim]{result.row_count} row(s) in {result.elapsed_ms:.0f} ms{suffix}[/dim]")


@app.command("inspect-chunks")
def inspect_chunks(
    path: str = typer.Argument(..., help="A file or a directory of documents."),
    show: int = typer.Option(3, "--show", help="How many chunks to print in full."),
) -> None:
    import statistics
    from pathlib import Path

    import tiktoken
    from rich.console import Console
    from rich.table import Table

    from dualpath.config import get_settings
    from dualpath.ingest.loaders import load_directory, load_document
    from dualpath.ingest.splitter import split_documents

    settings = get_settings()
    console = Console()
    target = Path(path)

    documents = [load_document(target)] if target.is_file() else load_directory(target)
    chunks = split_documents(
        documents,
        chunk_tokens=settings.chunk_tokens,
        chunk_overlap=settings.chunk_overlap,
    )
    if not chunks:
        console.print("[red]no chunks produced[/red]")
        raise typer.Exit(1)

    encoder = tiktoken.get_encoding("cl100k_base")
    sizes = sorted(len(encoder.encode(c.page_content)) for c in chunks)

    per_source: dict[str, int] = {}
    for chunk in chunks:
        per_source[chunk.metadata["source"]] = per_source.get(chunk.metadata["source"], 0) + 1

    table = Table(box=None, pad_edge=False)
    table.add_column("source", style="cyan")
    table.add_column("chunks", justify="right")
    for source, count in sorted(per_source.items()):
        table.add_row(source, str(count))
    console.print(table)

    console.print(
        f"\n[bold]{len(documents)} document(s) -> {len(chunks)} chunks[/bold]  "
        f"tokens: min={sizes[0]} median={statistics.median(sizes):.0f} max={sizes[-1]}  "
        f"budget={settings.chunk_tokens}"
    )
    small = sum(1 for s in sizes if s < 50)
    if small:
        console.print(
            f"[yellow]{small} chunk(s) under 50 tokens[/yellow] -- consider splitting on "
            "fewer heading levels (see section 8.3)"
        )

    for chunk in chunks[:show]:
        console.print(f"\n[dim]--- {chunk.metadata['source']} #{chunk.metadata['chunk_index']}[/dim]")
        console.print(f"[magenta]section:[/magenta] {chunk.metadata['section'] or '(none)'}")
        console.print(chunk.page_content[:400])

def _build_retrieval(settings):
    from dualpath.embeddings import FastEmbedEmbeddings
    from dualpath.stores.vectors import build_store

    embeddings = FastEmbedEmbeddings(
        model_name=settings.embedding_model,
        cache_dir=settings.embedding_cache_dir,
    )
    store = build_store(
        embeddings,
        mode=settings.chroma_mode,
        collection=settings.collection,
        path=str(settings.chroma_path),
        host=settings.chroma_host,
        port=settings.chroma_port,
        ssl=settings.chroma_ssl,
        api_key=settings.chroma_api_key,
        tenant=settings.chroma_tenant,
        database=settings.chroma_database,
    )
    return embeddings, store

@app.command()
def ingest(
    path: str = typer.Argument(None, help="File or directory. Defaults to DP_DOCS_DIR."),
    recreate: bool = typer.Option(False, "--recreate", help="Drop the collection first."),
) -> None:
    from pathlib import Path

    from rich.console import Console

    from dualpath.config import get_settings
    from dualpath.ingest.index import build_record_manager, index_documents
    from dualpath.ingest.loaders import load_directory, load_document
    from dualpath.ingest.splitter import split_documents
    from dualpath.observability import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level)
    console = Console()

    target = Path(path) if path else settings.docs_dir
    _, store = _build_retrieval(settings)

    if recreate:
        store.delete_collection()
        _, store = _build_retrieval(settings)
        console.print("[yellow]collection dropped[/yellow]")

    documents = [load_document(target)] if target.is_file() else load_directory(target)
    chunks = split_documents(
        documents,
        chunk_tokens=settings.chunk_tokens,
        chunk_overlap=settings.chunk_overlap,
    )
    console.print(f"{len(documents)} document(s) -> {len(chunks)} chunks")

    manager = build_record_manager(
        settings.record_manager_path, f"chroma/{settings.collection}"
    )
    result = index_documents(chunks, manager, store, full_refresh=target.is_dir())

    console.print(
        f"  added   [green]{result['num_added']}[/green]\n"
        f"  skipped [dim]{result['num_skipped']}[/dim]\n"
        f"  deleted [red]{result['num_deleted']}[/red]"
    )


@app.command()
def search(
    query: str = typer.Argument(..., help="What to look for."),
    k: int = typer.Option(None, "-k", help="Override DP_TOP_K_CONTEXT."),
    floor: float = typer.Option(None, "--floor", help="Override DP_SCORE_FLOOR."),
) -> None:
    from rich.console import Console

    from dualpath.config import get_settings
    from dualpath.models import Passage
    from dualpath.stores.vectors import search as vector_search

    settings = get_settings()
    console = Console()
    _, store = _build_retrieval(settings)

    hits = vector_search(
        store,
        query,
        k=k or settings.top_k_context,
        fetch_k=settings.top_k_candidates,
        mmr_lambda=settings.mmr_lambda,
        score_floor=settings.score_floor if floor is None else floor,
    )
    if not hits:
        console.print(
            f"[yellow]nothing above the similarity floor "
            f"({settings.score_floor if floor is None else floor:.2f})[/yellow]"
        )
        return

    for number, (document, score) in enumerate(hits, start=1):
        passage = Passage.from_document(document, score)
        console.print(f"\n[bold][{number}][/bold] [cyan]{passage.citation()}[/cyan]  {score:.3f}")
        console.print(passage.text[:400].strip())

@app.command()
def corpus() -> None:
    from rich.console import Console
    from rich.table import Table

    from dualpath.config import get_settings
    from dualpath.stores.vectors import collection_stats

    settings = get_settings()
    _, store = _build_retrieval(settings)
    stats = collection_stats(store)

    table = Table(box=None, pad_edge=False)
    table.add_column("source", style="cyan")
    table.add_column("chunks", justify="right")
    for source, count in stats.items():
        table.add_row(source, str(count))
    Console().print(table)
    Console().print(f"\n{sum(stats.values())} chunks from {len(stats)} source(s)")

@app.command()
def doctor(
    live: bool = typer.Option(True, "--live/--offline", help="Include checks that call Groq."),
) -> None:
    import importlib
    import sys

    from rich.console import Console
    from rich.table import Table

    from dualpath.config import get_settings

    settings = get_settings()
    console = Console()
    rows: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        rows.append((name, ok, detail))

    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    check("python", sys.version_info >= (3, 12), f"{version} (need >= 3.12)")

    for module, why in [
        ("duckdb", "the warehouse"),
        ("sqlglot", "the SQL firewall"),
        ("chromadb", "the vector store"),
        ("fastembed", "local embeddings"),
        ("langchain_core", "documents, prompts, indexing"),
        ("langchain_text_splitters", "chunking"),
        ("langchain_chroma", "the vector store wrapper"),
        ("langchain_classic", "the record manager"),
        ("langchain_groq", "the model"),
        ("langgraph", "the graph"),
        ("langgraph.checkpoint.sqlite", "conversational memory"),
    ]:
        try:
            importlib.import_module(module)
            check(f"import {module}", True, why)
        except ImportError as exc:
            check(f"import {module}", False, f"{why} -- {exc}")

    try:
        importlib.import_module("langchain_community")
        check(
            "langchain-community absent",
            False,
            "installed but unused -- it is sunset; see section 0.4",
        )
    except ImportError:
        check("langchain-community absent", True, "not installed, as intended")

    check(
        "warehouse",
        settings.warehouse_path.exists(),
        str(settings.warehouse_path) if settings.warehouse_path.exists() else "run build-warehouse",
    )
    if settings.warehouse_path.exists():
        import duckdb

        con = duckdb.connect(str(settings.warehouse_path), read_only=True)
        try:
            tables = con.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchone()[0]
        finally:
            con.close()
        check("warehouse tables", tables > 0, f"{tables} table(s)")

    try:
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

        serializer = JsonPlusSerializer()
        ok = hasattr(serializer, "dumps_typed")
        check(
            "checkpoint serializer",
            ok,
            "compatible"
            if ok
            else 'incompatible -- pip install -U "langgraph-checkpoint-sqlite>=3.1.0"',
        )
    except ImportError as exc:
        check("checkpoint serializer", False, str(exc)[:60])

    docs = list(settings.docs_dir.glob("*")) if settings.docs_dir.exists() else []
    check("document corpus", bool(docs), f"{len(docs)} file(s) in {settings.docs_dir}")

    try:
        _, store = _build_retrieval(settings)
        count = store._collection.count()
        check("vector index", count > 0, f"{count} chunk(s)" if count else "run: dualpath ingest")
    except Exception as exc:
        check("vector index", False, str(exc)[:70])

    check(
        "GROQ_API_KEY",
        settings.has_groq_key,
        "set" if settings.has_groq_key else "missing or still the placeholder",
    )

    if live and settings.has_groq_key:
        from dualpath.llm.chains import build_models
        from dualpath.llm.schemas import Plan

        try:
            models = build_models(settings)
            reply = models["util"].invoke("Reply with the single word: ok")
            check("groq reachable", bool(reply.content), f"{settings.util_model}")
            usage = getattr(reply, "usage_metadata", None) or {}
            check(
                "token accounting",
                bool(usage),
                f"{usage.get('input_tokens', '?')} in / {usage.get('output_tokens', '?')} out",
            )
        except Exception as exc:
            check("groq reachable", False, _explain_groq_error(exc))
            models = None

        if models:
            for method in ("json_schema", "json_mode", "function_calling"):
                try:
                    chain = models["util"].with_structured_output(Plan, method=method)
                    plan = chain.invoke(
                        "DOMAIN: test\n\nQUESTION: how many claims were denied?"
                    )
                    check(
                        f"structured output: {method}",
                        isinstance(plan, Plan),
                        f"route={plan.route.value}"
                        + ("  <- configured" if method == settings.structured_method else ""),
                    )
                except Exception as exc:
                    check(f"structured output: {method}", False, str(exc))
    elif live:
        check("groq reachable", False, "skipped -- no key")

    table = Table(box=None, pad_edge=False)
    table.add_column("")
    table.add_column("check", style="cyan")
    table.add_column("detail", style="dim")
    for name, ok, detail in rows:
        table.add_row("[green]OK[/green]" if ok else "[red]--[/red]", name, detail)
    console.print(table)

    failed = [name for name, ok, _ in rows if not ok]
    if failed:
        console.print(f"\n[red]{len(failed)} check(s) failed:[/red] {', '.join(failed)}")
        raise typer.Exit(1)
    console.print("\n[green]all checks passed[/green]")


def _explain_groq_error(exc: Exception) -> str:
    name = type(exc).__name__
    text = str(exc)
    if "AuthenticationError" in name or "401" in text:
        return "401 -- Groq rejected the key. Check GROQ_API_KEY in .env (no quotes, no trailing comment)."
    if "NotFoundError" in name or "404" in text or "does not exist" in text:
        return (
            "model not found -- Groq retires models. See console.groq.com/docs/models "
            "and update DP_GEN_MODEL / DP_SQL_MODEL / DP_UTIL_MODEL."
        )
    if "RateLimit" in name or "429" in text:
        return "429 -- rate limited. The free tier resets quickly; wait and retry."
    if "Connection" in name or "Timeout" in name:
        return "network -- could not reach api.groq.com."
    return f"{name}: {text[:60]}"


def _render_answer(
    response: AnswerResponse,
    *,
    show_sql: bool = True,
    show_table: bool = True,
    show_contexts: bool = False,
    show_trace: bool = True,
) -> None:
    style = _ROUTE_STYLE.get(response.route, "white")

    if response.awaiting_approval:
        console.print(
            Panel(
                Syntax(response.pending_sql, "sql", theme="ansi_dark", word_wrap=True),
                title="[yellow]SQL awaiting your approval[/yellow]",
                border_style="yellow",
            )
        )
        console.print(
            "Resume with:  [bold]dualpath ask --thread "
            f"{response.thread_id} --approve <approve|reject|SQL>[/bold]\n"
        )
        return

    if response.resolved_question and response.resolved_question != response.question:
        console.print(f"[dim]resolved: {response.resolved_question}[/dim]")

    console.print(
        Panel(
            response.answer or "(no answer)",
            title=f"[{style}]route={response.route}[/{style}]",
            border_style=style,
        )
    )

    if show_sql and response.sql:
        console.print(
            Panel(
                Syntax(response.sql, "sql", theme="ansi_dark", word_wrap=True),
                title="SQL that ran",
                border_style="cyan",
            )
        )

    if show_table and response.table is not None and response.table.columns:
        table = Table(
            title=f"{response.table.row_count} row(s)"
            + (" (truncated)" if response.table.truncated else ""),
            header_style="bold cyan",
        )
        for column in response.table.columns:
            table.add_column(column, overflow="fold")
        for row in response.table.rows[:25]:
            table.add_row(*["" if v is None else str(v) for v in row])
        console.print(table)
        if response.table.row_count > 25:
            console.print(f"[dim]... {response.table.row_count - 25} more row(s)[/dim]")

    if response.citations:
        cites = Table(title="Citations", header_style="bold magenta", show_lines=False)
        cites.add_column("#", justify="right")
        cites.add_column("Source")
        cites.add_column("Section", overflow="fold")
        for citation in response.citations:
            cites.add_row(citation.marker, citation.source, citation.section)
        console.print(cites)

    if response.sql_attempts and len(response.sql_attempts) > 1:
        console.print("\n[bold]SQL attempts[/bold] (the self-repair cycle)")
        for attempt in response.sql_attempts:
            if attempt.ok:
                console.print(
                    f"  [green]{attempt.attempt}. ok[/green] "
                    f"{attempt.row_count} row(s) in {attempt.elapsed_ms:.0f}ms"
                )
            else:
                label = "blocked by firewall" if attempt.blocked_by_guard else "failed"
                console.print(
                    f"  [red]{attempt.attempt}. {label}[/red] {attempt.error[:110]}"
                )

    if show_contexts and response.contexts:
        cited = {c.marker for c in response.citations}
        console.print("\n[bold]Retrieved passages[/bold]")
        inspector = Table(header_style="bold", show_lines=False)
        inspector.add_column("#", justify="right")
        inspector.add_column("Sim", justify="right")
        inspector.add_column("Cited")
        inspector.add_column("Source")
        inspector.add_column("Section", overflow="fold")
        for number, ctx in enumerate(response.contexts, start=1):
            inspector.add_row(
                str(number),
                f"{ctx.score:.3f}",
                "yes" if f"[{number}]" in cited else "",
                ctx.source,
                ctx.section,
            )
        console.print(inspector)

    if response.critique is not None and not response.critique.supported:
        problems = "; ".join(response.critique.problems) or response.critique.suggestion
        console.print(f"\n[yellow]critique:[/yellow] {problems}")

    if show_trace:
        bits = [f"{response.elapsed_ms:.0f}ms total"]
        if response.replans:
            bits.append(f"{response.replans} replan(s)")
        bits.append(f"{response.usage.total_tokens} tokens in {response.usage.calls} call(s)")
        console.print(f"\n[dim]{'  '.join(bits)}[/dim]")
        console.print(f"[dim]{response.stage_summary}[/dim]")


@app.command()
def ask(
    question: str | None = typer.Argument(None, help="The question."),
    thread: str | None = typer.Option(
        None, "--thread", "-t", help="Continue an existing conversation."
    ),
    approve: str | None = typer.Option(
        None,
        "--approve",
        help="Resume a paused thread: 'approve', 'reject', or replacement SQL.",
    ),
    contexts: bool = typer.Option(False, "--contexts", "-c", help="Show retrieved passages."),
    no_sql: bool = typer.Option(False, "--no-sql", help="Hide the generated SQL."),
    approve_sql: bool = typer.Option(
        False, "--approve-sql", help="Require human approval before SQL runs."
    ),
    no_critique: bool = typer.Option(False, "--no-critique", help="Skip self-critique."),
    top_k: int | None = typer.Option(None, "--top-k", help="Passages to retrieve."),
    stream: bool = typer.Option(False, "--stream", help="Show node-by-node progress."),
    as_json: bool = typer.Option(False, "--json", help="Emit the full response as JSON."),
) -> None:
    overrides: dict = {}
    if approve_sql:
        overrides["require_sql_approval"] = True
    if no_critique:
        overrides["enable_critique"] = False
    if top_k is not None:
        overrides["top_k_context"] = top_k

    agent = _agent(overrides or None)

    try:
        if approve is not None:
            if not thread:
                console.print("[red]--approve needs --thread.[/red]")
                raise typer.Exit(1)
            response = agent.resume(thread, approve)
        else:
            if not question:
                console.print("[red]Provide a question, or --thread with --approve.[/red]")
                raise typer.Exit(1)
            if stream and not as_json:
                response = None
                for event in agent.stream(question, thread_id=thread):
                    if event["event"] == "node":
                        detail = f" [dim]{event['detail']}[/dim]" if event["detail"] else ""
                        console.print(f"  [cyan]{event['node']}[/cyan]{detail}")
                    elif event["event"] == "interrupt":
                        console.print("  [yellow]paused for approval[/yellow]")
                    elif event["event"] == "done":
                        response = event["response"]
                console.print()
                if response is None:
                    console.print("[red]the graph produced no response[/red]")
                    raise typer.Exit(1)
            else:
                response = agent.ask(question, thread_id=thread)

        if as_json:
            console.print_json(response.model_dump_json(indent=2))
        else:
            _render_answer(response, show_sql=not no_sql, show_contexts=contexts)
            if not thread and not response.awaiting_approval:
                console.print(
                    f"[dim]follow up with: dualpath ask --thread {response.thread_id} "
                    '"..."[/dim]'
                )
    finally:
        agent.close()


@app.command()
def chat(
    thread: str | None = typer.Option(None, "--thread", "-t", help="Resume a conversation."),
    contexts: bool = typer.Option(False, "--contexts", "-c", help="Show retrieved passages."),
) -> None:
    agent = _agent()

    thread_id = thread
    console.print(
        Panel(
            "Questions are answered from the warehouse, the documents, or both.\n"
            "Follow-ups resolve against the conversation, so "
            '"what about last year?" works.\n\n'
            "[dim]/sql       show the last statement that ran\n"
            "/trace     show the last graph path\n"
            "/history   show the conversation as the agent sees it\n"
            "/new       start a fresh thread\n"
            "/exit      quit[/dim]",
            title="[bold green]DualPath[/bold green]",
            border_style="green",
        )
    )
    if thread_id:
        console.print(f"[dim]resuming thread {thread_id}[/dim]")

    last: AnswerResponse | None = None
    try:
        while True:
            try:
                line = console.input("\n[bold cyan]you[/bold cyan] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\nbye")
                break
            if not line:
                continue

            lowered = line.lower()
            if lowered in {"/exit", "/quit", "exit", "quit"}:
                console.print("bye")
                break
            if lowered == "/new":
                thread_id = None
                last = None
                console.print("[dim]started a new thread[/dim]")
                continue
            if lowered == "/sql":
                if last and last.sql:
                    console.print(Syntax(last.sql, "sql", theme="ansi_dark"))
                else:
                    console.print("[dim]no SQL has run yet[/dim]")
                continue
            if lowered == "/trace":
                if last:
                    for event in last.trace:
                        console.print(
                            f"  [cyan]{event.node:16}[/cyan] {event.elapsed_ms:7.0f}ms  "
                            f"[dim]{event.detail}[/dim]"
                        )
                else:
                    console.print("[dim]nothing yet[/dim]")
                continue
            if lowered == "/history":
                if thread_id:
                    for turn in agent.history(thread_id):
                        who = "you" if turn["role"] == "user" else "dualpath"
                        console.print(f"  [bold]{who}[/bold]: {turn['content'][:160]}")
                else:
                    console.print("[dim]no conversation yet[/dim]")
                continue

            with console.status("[dim]thinking...[/dim]"):
                response = agent.ask(line, thread_id=thread_id)
            thread_id = response.thread_id
            last = response
            _render_answer(response, show_contexts=contexts)

            if response.awaiting_approval:
                decision = console.input("[yellow]approve / reject / paste SQL:[/yellow] ").strip()
                with console.status("[dim]running...[/dim]"):
                    response = agent.resume(thread_id, decision or "reject")
                last = response
                _render_answer(response, show_contexts=contexts)
    finally:
        agent.close()


@app.command()
def serve(
    host: str = typer.Option(None, "--host"),
    port: int = typer.Option(None, "--port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes."),
) -> None:
    import uvicorn

    from dualpath.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "dualpath.api.main:app",
        host=host or settings.api_host,
        port=port or settings.api_port,
        reload=reload,
    )


@app.command()
def ui(port: int = typer.Option(8501, "--port")) -> None:
    import subprocess
    import sys
    from pathlib import Path

    app_path = Path(__file__).parent / "ui" / "app.py"
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(app_path), "--server.port", str(port)],
        check=False,
    )


if __name__ == "__main__":
    app()
