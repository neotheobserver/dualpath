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


if __name__ == "__main__":
    app()
