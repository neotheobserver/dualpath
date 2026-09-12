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


if __name__ == "__main__":
    app()
