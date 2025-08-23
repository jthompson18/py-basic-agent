# app/agent/__main__.py
import asyncio
import typer
from .core import run_agent

app = typer.Typer(add_completion=False)


def _run(query: str):
    ans = asyncio.run(run_agent(query))
    print("\n=== FINAL ANSWER ===\n")
    print(ans)


@app.command("task")
def task_cmd(
    query: str = typer.Argument(..., help="The research/ETL query to run"),
):
    """Run a single research/ETL task (subcommand style)."""
    _run(query)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    query: str = typer.Option(
        None,
        "--task",
        "-t",
        help='Run a single research/ETL task (option style), e.g. `python -m agent --task "..."`',
    ),
):
    """
    Agent CLI.

    Examples:
      - python -m agent task "Who founded NVIDIA and when?"
      - python -m agent --task "Load ./data/county.csv and filter to WA"
    """
    if ctx.invoked_subcommand is None:
        if not query:
            typer.echo(ctx.get_help())
            raise typer.Exit(1)
        _run(query)


if __name__ == "__main__":
    app()
