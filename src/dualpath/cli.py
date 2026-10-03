from __future__ import annotations

import typer

app = typer.Typer(help="DualPath -- agentic RAG over structured and unstructured data.")


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

if __name__ == "__main__":
    app()
