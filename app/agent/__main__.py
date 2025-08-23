import asyncio
import re
import typer
from rich.console import Console
from rich.prompt import Prompt
from .core import run_agent
import urllib.parse

app = typer.Typer(add_completion=False)
console = Console()


def _emit_factory(verbose: bool):
    """Create a printer for internal events when verbose=True."""
    if not verbose:
        return lambda *_args, **_kwargs: None

    def emit(kind: str, payload):
        if kind == "step":
            console.print(
                f"[bold bright_black]Step {payload['n']}/{payload['max']}[/]")
        elif kind == "model":
            console.print(f"[cyan]MODEL[/]: {payload[:400]}")
        elif kind == "tool_call":
            tool = payload.get("tool")
            console.print(
                f"[yellow]TOOL CALL[/]: {tool} {payload.get('input')}")
        elif kind == "tool_result":
            tool = payload.get("tool")
            console.print(
                f"[green]TOOL RESULT[/]: {tool} → {(payload.get('preview') or '')[:400]}")
        elif kind == "summary":
            t = payload.get("type")
            console.print(f"[magenta]SUMMARY ({t})[/]: {payload.get('text')}")
        elif kind == "error":
            console.print(f"[red]ERROR[/]: {payload}")
        elif kind == "final":
            console.print(f"[bold white]FINAL[/]: {payload}")
    return emit


def _run_once(query: str, verbose: bool):
    emit = _emit_factory(verbose)
    ans = asyncio.run(run_agent(query, emit=emit, verbose=verbose))
    console.rule("[white]Answer")
    console.print(ans)
    console.rule()


def _detect_source_type(src: str) -> str | None:
    # robust extension check even with query strings/fragments
    path = urllib.parse.urlparse(src).path.lower()
    if path.endswith(".csv"):
        return "csv"
    if path.endswith(".json"):
        return "json"
    return None


@app.command("task")
def task_cmd(
    query: str = typer.Argument(..., help="The research/ETL query to run"),
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose", help="Show internal steps"),
):
    _run_once(query, verbose)


@app.command("repl")
def repl_cmd(
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose", help="Show internal steps"),
):
    console.print("[bold]Agent REPL[/] — type [green]/research ...[/], [green]/etl ...[/], [green]/etl_from_source <url>[/]. Type [yellow]exit()[/] or [yellow]/help[/] to quit/show help.")
    while True:
        try:
            line = Prompt.ask("[bold]>>[/]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[bold]bye![/]")
            break
        if not line:
            continue
        s = line.strip()
        if s == "exit()":
            console.print("[bold]bye![/]")
            break

        # /help
        if s == "/help":
            console.print("""[bold]Commands[/]
                [green]/research <question>[/]   — start a research run (web search + fetch + summaries)
                [green]/etl <instructions>[/]    — ETL run with free-form instructions (local CSVs supported)
                [green]/etl ./data/file.csv[/]   — quick profile/summary for a local CSV
                [green]/etl_from_source <url>[/] — ETL from a remote .csv or .json (validated)
                [yellow]exit()[/]                — quit

                [bold]Color legend (verbose on):[/]
                [bright_black]Step[/]           — loop step number
                [cyan]MODEL[/]                  — raw model output (truncated)
                [yellow]TOOL CALL[/]            — which tool was invoked + inputs
                [green]TOOL RESULT[/]           — summarized output/preview of the tool
                [magenta]SUMMARY (search/etl)[/]— LLM summaries of search results or ETL actions
                [red]ERROR[/]                   — tool or runtime errors
                [white]FINAL[/]                 — agent’s final answer
                """)
            continue

        # /research
        if s.startswith("/research "):
            query = s[len("/research "):].strip()
            _run_once(query, verbose)
            continue

        # /etl quick-path for local csv
        if s.startswith("/etl "):
            rest = s[len("/etl "):].strip()
            if re.match(r".*\.csv(\s*)$", rest) and " " not in rest.strip():
                query = f"ETL only: load_csv path {rest}; transform with: select all; finalize with a user summary and a 5-row preview."
            else:
                query = f"ETL only: {rest}"
            _run_once(query, verbose)
            continue

        # /etl_from_source <url to .csv or .json>
        if s.startswith("/etl_from_source "):
            src = s[len("/etl_from_source "):].strip()
            stype = _detect_source_type(src)
            if not stype:
                console.print(
                    "[red]Source must be a URL ending with .csv or .json[/]")
                continue
            if stype == "csv":
                query = f"ETL only: load_csv path {src}; transform with: select all; finalize with a user summary and a 5-row preview."
            else:
                query = f"ETL only: load_json path {src}; transform with: select all; finalize with a user summary and a 5-row preview."
            _run_once(query, verbose)
            continue

        # otherwise run as a plain task
        _run_once(s, verbose)


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
            return _run_once(query, verbose)
        # default to REPL
        return repl_cmd(verbose=verbose)


if __name__ == "__main__":
    app()
