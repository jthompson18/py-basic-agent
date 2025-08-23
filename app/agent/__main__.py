# app/agent/__main__.py
import asyncio
import os
import re
import shlex
import urllib.parse
import typer
from rich.console import Console
from rich.prompt import Prompt

from .core import run_agent
from . import tools, llm  # NEW

app = typer.Typer(add_completion=False)
console = Console()

# ---------- verbose emitter (unchanged) ----------


def _emit_factory(verbose: bool):
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

# ---------- helpers for flagged ETL ----------


def _basename_from_path_or_url(p: str) -> str:
    path = urllib.parse.urlparse(p).path or p
    return os.path.basename(path.rstrip("/"))


def _detect_source_type(src: str) -> str | None:
    path = urllib.parse.urlparse(src).path.lower()
    if path.endswith(".csv"):
        return "csv"
    if path.endswith(".json"):
        return "json"
    return None


def _default_outpath(input_path: str) -> str:
    base = _basename_from_path_or_url(input_path) or "data"
    return os.path.join("./data", f"transformed_{base}")


def _parse_flag_line(flag_line: str) -> dict:
    """
    Parse: -p <path|url> -t "<transform spec>" [-l <output-path>]
    Returns dict {'p':..., 't':..., 'l':... or None}
    """
    tokens = shlex.split(flag_line)
    out = {"p": None, "t": None, "l": None}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-p" and i + 1 < len(tokens):
            out["p"] = tokens[i + 1]
            i += 2
        elif tok == "-t" and i + 1 < len(tokens):
            out["t"] = tokens[i + 1]
            i += 2
        elif tok == "-l" and i + 1 < len(tokens):
            out["l"] = tokens[i + 1]
            i += 2
        else:
            # unknown token; skip
            i += 1
    return out


def _build_transform_spec(spec_str: str) -> dict:
    """
    DSL:
      reorder:colA,colB,colC; rename:old1->new1,old2->new2
    Output spec for ETL.transform():
      {'select': [...], 'rename': {'old':'new', ...}}
    """
    spec = {"select": None, "rename": {}}
    parts = [p.strip() for p in spec_str.split(";") if p.strip()]
    for part in parts:
        if part.startswith("reorder:"):
            cols = part[len("reorder:"):].strip()
            if cols:
                spec["select"] = [c.strip()
                                  for c in cols.split(",") if c.strip()]
        elif part.startswith("rename:"):
            mapping = part[len("rename:"):].strip()
            if mapping:
                pairs = [p.strip() for p in mapping.split(",") if p.strip()]
                for pair in pairs:
                    if "->" in pair:
                        old, new = [x.strip() for x in pair.split("->", 1)]
                        if old and new:
                            spec["rename"][old] = new
    # remove None if not set
    if not spec["rename"]:
        spec.pop("rename", None)
    if not spec.get("select"):
        spec.pop("select", None)
    return spec


async def _run_flagged_etl(path_or_url: str, transform_str: str, out_path: str | None, verbose: bool):
    try:
        stype = _detect_source_type(path_or_url)
        if not stype:
            console.print("[red]Source must end with .csv or .json[/]")
            return

        in_path = path_or_url
        final_out = out_path or _default_outpath(in_path)

        # decide output format by extension of out path; else mirror input type
        out_ext = os.path.splitext(
            urllib.parse.urlparse(final_out).path.lower())[1]
        if out_ext in (".csv", ".json"):
            out_fmt = out_ext.lstrip(".")
        else:
            out_fmt = "csv" if stype == "csv" else "json"
            final_out = final_out + ("." + out_fmt)

        # 1) load
        if stype == "csv":
            load_res = await tools.etl_tool("load_csv", path=in_path)
        else:
            load_res = await tools.etl_tool("load_json", path=in_path)

        # 2) transform (rename + reorder only)
        spec = _build_transform_spec(transform_str)
        tr_res = await tools.etl_tool(
            "transform",
            path=in_path,
            spec=spec,
            save={"format": out_fmt, "path": final_out}
        )

        # 3) summarize via LLM
        summary = await llm.summarize_etl({"load": load_res, "transform": tr_res})

        # 4) pretty print
        console.print(
            f"[green]Saved:[/] {tr_res.get('saved_as') or final_out}")
        console.print(f"[magenta]SUMMARY (etl)[/]: {summary}")

    except FileNotFoundError as e:
        console.print(f"[red]File not found:[/] {e}")
        console.print(
            "[bright_black]Tip: put files in your repo’s ./data/ (mounted to /app/data) and call: /etl -p ./data/your.csv -t \"...\"[/]")
    except Exception as e:
        console.print(f"[red]ETL error:[/] {type(e).__name__}: {e}")

# ---------- CLI commands ----------


@app.command("task")
def task_cmd(
    query: str = typer.Argument(..., help="The research/ETL query to run"),
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose", help="Show internal steps"),
):
    ans = _run_once(query, verbose)


@app.command("repl")
def repl_cmd(
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose", help="Show internal steps"),
):
    console.print("[bold]Agent REPL[/] — type [green]/research ...[/], [green]/etl ...[/], [green]/etl_from_source ...[/]. Type [yellow]/help[/] or [yellow]exit()[/].")
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
  [green]/research <question>[/]                  — start a research run (web search + fetch + summaries)
  [green]/etl -p <path> -t "<transform>" [-l out][/] 
      • path: local CSV/JSON (mounted under ./data or absolute in container)
      • transform: e.g. "reorder:date,region,units; rename:units->qty,unit_price->price"
      • out: optional; default ./data/transformed_<basename>.<same-ext>
  [green]/etl_from_source -p <url> -t "<transform>" [-l out][/]
      • url must end in .csv or .json
  [yellow]exit()[/]                               — quit

[bold]Transform ops[/]
  • reorder:colA,colB,colC          (reorder columns; others preserved at end)
  • rename:old1->new1,old2->new2    (rename columns/fields)

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

        # New flagged ETL (local or URL)
        if s.startswith("/etl "):
            flags = _parse_flag_line(s[len("/etl "):])
            if not flags["p"] or not flags["t"]:
                console.print(
                    "[red]/etl requires -p <path> and -t \"<transform>\"[/]")
                continue
            asyncio.run(_run_flagged_etl(
                flags["p"], flags["t"], flags["l"], verbose))
            continue

        if s.startswith("/etl_from_source "):
            flags = _parse_flag_line(s[len("/etl_from_source "):])
            if not flags["p"] or not flags["t"]:
                console.print(
                    "[red]/etl_from_source requires -p <url> and -t \"<transform>\"[/]")
                continue
            asyncio.run(_run_flagged_etl(
                flags["p"], flags["t"], flags["l"], verbose))
            continue

        if s.startswith("/where "):
            target = s[len("/where "):].strip()
            from .tools import _resolve_local_path
            resolved = _resolve_local_path(target)

            import os
            exists = os.path.exists(resolved)
            status = "[green]exists[/green]" if exists else "[red]missing[/red]"
            console.print(
                f"[white]/where[/white] {target} -> {resolved}  {status}")
            continue

        # fallback: run as a normal agent task
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
        return repl_cmd(verbose=verbose)


if __name__ == "__main__":
    app()
