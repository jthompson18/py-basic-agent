# app/agent/__main__.py
import typer
from .repl import run_repl, run_task

app = typer.Typer(add_completion=False)


@app.command("task")
def task_cmd(
    query: str = typer.Argument(..., help="The research/ETL query to run"),
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose", help="Show internal steps"),
):
    run_task(query, verbose)


@app.command("repl")
def repl_cmd(
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose", help="Show internal steps"),
):
    run_repl(verbose)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    query: str = typer.Option(None, "--task", "-t",
                              help="Run a single task (non-REPL)"),
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose", help="Show internal steps"),
):
    if ctx.invoked_subcommand is None:
        if query:
            return run_task(query, verbose)
        return run_repl(verbose)


if __name__ == "__main__":
    app()
