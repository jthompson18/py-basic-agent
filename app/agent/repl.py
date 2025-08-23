# app/agent/repl.py
from __future__ import annotations

import asyncio
import os
import re
import shlex
import urllib.parse
from typing import Dict

from .mcp_client import mcp_manager, HAS_MCP

from rich.console import Console
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.document import Document

from .core import run_agent
from . import tools, llm

console = Console()

# ---------- tiny helpers ----------


def _help_text() -> str:
    return """[bold]Agent REPL — Help[/]

[bold]Commands[/]
  [green]/research <question>[/]                  
      Run a research task (Serper search + page fetch + LLM summaries).

  [green]/etl -p <path> -t "<transform>" [-l <out>][/]
      Local ETL with flags.
      • [bold]-p[/bold]  [white]<path>[/white] (required) — CSV or JSON file in your repo (mounted to /app or /app/data).
      • [bold]-t[/bold]  [white]"<transform>"[/white] (required) — transform DSL (see below).
      • [bold]-l[/bold]  [white]<out>[/white] (optional) — output path.
          - If omitted, saves to [white]./data/transformed_<input_basename>[/white] with format mirrored from input.
          - Format is taken from the [white]-l[/white] extension if given (e.g., .csv or .json).

  [green]/etl_from_source -p <url> -t "<transform>" [-l <out>][/]
      Remote ETL from a URL ending in .csv or .json (validated).
      Flags behave like /etl.

  [green]/where <path>[/]
      Show how a local path resolves inside the container and whether it exists.

  [yellow]exit()[/]
      Quit the REPL.

[bold]Transform DSL[/]
  Chain operations with semicolons. Works for CSV columns and JSON keys.
  • [bold]reorder:[/bold][white]colA,colB,colC[/white]
      Reorder columns; unspecified columns are appended in original order.
  • [bold]rename:[/bold][white]old1->new1,old2->new2[/white]
      Rename fields/columns. Quote names with spaces or numeric keys:
      [white]rename:'1958'->y1958,'unit price'->price[/white]

[green]/mcp add -n <name> -c "<command>" [--env KEY=VAL,KEY2=VAL][/]
  Start an MCP stdio server and register it.
  [green]/mcp list[/]                         — list connected MCP servers
  [green]/mcp default <name>[/]               — set default server
  [green]/mcp tools [<name>][/]               — list tools exposed by a server
  [green]/mcp call <tool> '<json>'[/]         — call a tool on the default server
  [green]/mcp call <name> <tool> '<json>'[/]  — call a tool on a specific server
  [green]/mcp remove <name>[/]                — disconnect & remove a server

[bold]Color legend (verbose on)[/]
  [bright_black]Step[/]           — agent loop step
  [cyan]MODEL[/]                  — raw model output (truncated)
  [yellow]TOOL CALL[/]            — which tool was invoked
  [green]TOOL RESULT[/]           — preview of the tool output
  [magenta]SUMMARY (search/etl)[/]— LLM summaries of search/ETL
  [red]ERROR[/]                   — errors
  [white]FINAL[/]                 — agent’s final answer

[bold]Keyboard shortcuts[/]
  Up/Down                      — navigate history (filtered)
  Tab                          — completion (commands, paths for -p/-l)
  Ctrl-R                       — reverse history search
  Ctrl-A / Ctrl-E              — start/end of line
  Alt-B / Alt-F                — move by word
  Ctrl-U / Ctrl-K / Ctrl-W     — kill to start / kill to end / delete previous word
  Ctrl-Y                       — yank (paste)
  Ctrl-L                       — clear screen
  Ctrl-C                       — cancel current line
  Ctrl-D                       — exit on empty line

[bold]Notes[/]
  • Start quiet with CLI flag: [white]--no-verbose[/white] (e.g., [white]docker compose run --rm app --no-verbose[/white]).
  • Local files live under your repo and are mounted at [white]/app[/white] and [white]/app/data[/white].
"""


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
            console.print(
                f"[yellow]TOOL CALL[/]: {payload.get('tool')} {payload.get('input')}")
        elif kind == "tool_result":
            console.print(
                f"[green]TOOL RESULT[/]: {payload.get('tool')} → {(payload.get('preview') or '')[:400]}")
        elif kind == "summary":
            console.print(
                f"[magenta]SUMMARY ({payload.get('type')})[/]: {payload.get('text')}")
        elif kind == "error":
            console.print(f"[red]ERROR[/]: {payload}")
        elif kind == "final":
            console.print(f"[bold white]FINAL[/]: {payload}")
    return emit


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


def _parse_flag_line(flag_line: str) -> Dict[str, str | None]:
    tokens = shlex.split(flag_line)
    out: Dict[str, str | None] = {"p": None, "t": None, "l": None}
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
            i += 1
    return out


def _parse_env_csv(s: str | None) -> dict:
    if not s:
        return {}
    out = {}
    for pair in s.split(","):
        if not pair.strip():
            continue
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _parse_mcp_add_flags(rest: str) -> dict:
    # /mcp add -n NAME -c "cmd ..." [--env K=V,K2=V]
    import shlex
    toks = shlex.split(rest)
    out = {"n": None, "c": None, "env": None}
    i = 0
    while i < len(toks):
        t = toks[i]
        if t == "-n" and i + 1 < len(toks):
            out["n"] = toks[i + 1]
            i += 2
        elif t == "-c" and i + 1 < len(toks):
            out["c"] = toks[i + 1]
            i += 2
        elif t == "--env" and i + 1 < len(toks):
            out["env"] = toks[i + 1]
            i += 2
        else:
            i += 1
    return out


def _build_transform_spec(spec_str: str) -> dict:
    spec = {"select": None, "rename": {}, "limit": None}
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
                        old, new = [x.strip().strip("'").strip('"')
                                    for x in pair.split("->", 1)]
                        if old and new:
                            spec["rename"][old] = new
        elif part.startswith("limit:"):
            val = part[len("limit:"):].strip()
            try:
                spec["limit"] = int(val)
            except ValueError:
                spec["limit"] = None

    if not spec["rename"]:
        spec.pop("rename", None)
    if not spec.get("select"):
        spec.pop("select", None)
    if spec.get("limit") is None:
        spec.pop("limit", None)
    return spec


async def _run_flagged_etl(path_or_url: str, transform_str: str, out_path: str | None, verbose: bool):
    try:
        stype = _detect_source_type(path_or_url)
        if not stype:
            console.print("[red]Source must end with .csv or .json[/]")
            return

        in_path = path_or_url
        final_out = out_path or _default_outpath(in_path)

        out_ext = os.path.splitext(
            urllib.parse.urlparse(final_out).path.lower())[1]
        if out_ext in (".csv", ".json"):
            out_fmt = out_ext.lstrip(".")
        else:
            out_fmt = "csv" if stype == "csv" else "json"
            final_out = final_out + ("." + out_fmt)

        # 1) load
        load_res = await tools.etl_tool("load_csv" if stype == "csv" else "load_json", path=in_path)

        # 2) transform + save
        spec = _build_transform_spec(transform_str)
        tr_res = await tools.etl_tool(
            "transform",
            path=in_path,
            spec=spec,
            save={"format": out_fmt, "path": final_out}
        )

        # 3) summarize via LLM
        summary = await llm.summarize_etl({"load": load_res, "transform": tr_res})

        console.print(
            f"[green]Saved:[/] {tr_res.get('saved_as') or final_out}")
        console.print(f"[magenta]SUMMARY (etl)[/]: {summary}")

    except FileNotFoundError as e:
        console.print(f"[red]File not found:[/] {e}")
        console.print(
            "[bright_black]Tip: place files in repo ./data/ (mounted to /app/data) and use: /etl -p ./data/your.csv -t \"...\"[/]")
    except Exception as e:
        console.print(f"[red]ETL error:[/] {type(e).__name__}: {e}")


def _make_key_bindings():
    kb = KeyBindings()

    @kb.add("c-c")
    def _(event):  # cancel line
        event.app.current_buffer.reset()

    @kb.add("c-d")
    def _(event):  # exit on empty, else delete
        buf = event.app.current_buffer
        if not buf.text:
            event.app.exit(result="exit()")
        else:
            buf.delete(1)

    @kb.add("c-l")
    def _(event):  # clear
        event.app.renderer.clear()

    return kb


class AgentCompleter(Completer):
    def __init__(self):
        self.commands = ["/research", "/etl",
                         "/etl_from_source", "/where", "/help", "exit()"]
        self.path_completer = PathCompleter(expanduser=True)

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        stripped = text.lstrip()

        # command suggestions
        if stripped.startswith("/") and " " not in stripped:
            prefix = stripped
            for cmd in self.commands:
                if cmd.startswith(prefix):
                    yield Completion(cmd, start_position=-len(prefix))
            return

        tokens = stripped.split()
        last = tokens[-1] if tokens else ""

        want_path = False
        frag = ""
        if last in ("-p", "-l"):
            want_path = True
            frag = ""
        elif any(last.startswith(pfx) for pfx in ("/", "./", "../", "data/", "/app/")):
            want_path = True
            frag = last

        if want_path:
            doc = Document(frag, cursor_position=len(frag))
            for c in self.path_completer.get_completions(doc, complete_event):
                yield Completion(c.text, start_position=-len(frag), display=c.display)


def _run_once(query: str, verbose: bool):
    emit = _emit_factory(verbose)
    ans = asyncio.run(run_agent(query, emit=emit, verbose=verbose))
    console.rule("[white]Answer")
    console.print(ans)
    console.rule()

# ---------- public API ----------


def run_task(query: str, verbose: bool = True) -> None:
    _run_once(query, verbose)


def run_repl(verbose: bool = True) -> None:
    console.print("[bold]Agent REPL[/] — type [green]/research ...[/], [green]/etl ...[/], [green]/etl_from_source ...[/]. Type [yellow]/help[/] or [yellow]exit()[/].")

    history_path = "/app/.data/.agent_history"
    session = PromptSession(
        message=">> ",
        history=FileHistory(history_path),
        auto_suggest=AutoSuggestFromHistory(),
        completer=AgentCompleter(),
        key_bindings=_make_key_bindings(),
    )

    while True:
        try:
            line = session.prompt(enable_history_search=True)
        except EOFError:
            console.print("\n[bold]bye![/]")
            break
        except KeyboardInterrupt:
            console.print("[bright_black](cancelled)[/]")
            continue

        if not line:
            continue

        s = line.strip()
        if s == "exit()":
            console.print("[bold]bye![/]")
            break

        if s == "/help":
            console.print(_help_text())
            continue

        if s.startswith("/research "):
            query = s[len("/research "):].strip()
            _run_once(query, verbose)
            continue

        if s.startswith("/where "):
            target = s[len("/where "):].strip()
            from .tools import _resolve_local_path
            resolved = _resolve_local_path(target)
            exists = os.path.exists(resolved)
            status = "[green]exists[/green]" if exists else "[red]missing[/red]"
            console.print(
                f"[white]/where[/white] {target} -> {resolved}  {status}")
            continue

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

            # MCP commands
    if s.startswith("/mcp "):
        if not HAS_MCP:
            console.print(
                "[red]MCP client not installed.[/] Add [white]mcp>=0.1[/white] to requirements.txt and rebuild.")
            return

        args = s[len("/mcp "):].strip()
        if args.startswith("add "):
            flags = _parse_mcp_add_flags(args[len("add "):])
            if not flags["n"] or not flags["c"]:
                console.print(
                    "[red]/mcp add -n <name> -c \"<command>\" [--env KEY=VAL,KEY2=VAL][/]")
                return
            envd = _parse_env_csv(flags["env"])
            try:
                asyncio.run(mcp_manager.add_stdio(
                    flags["n"], flags["c"], envd))
                console.print(
                    f"[green]MCP server '{flags['n']}' connected.[/]")
            except Exception as e:
                console.print(
                    f"[red]MCP add error:[/] {type(e).__name__}: {e}")
            return

        if args == "list":
            names = mcp_manager.list_servers()
            if not names:
                console.print("[bright_black](no MCP servers)[/]")
            else:
                default = mcp_manager.default_name
                for n in names:
                    star = " *default*" if n == default else ""
                    console.print(f"- {n}{star}")
            return

        if args.startswith("default "):
            name = args.split(maxsplit=1)[1].strip()
            try:
                mcp_manager.set_default(name)
                console.print(f"[green]Default MCP server set to[/] {name}")
            except Exception as e:
                console.print(f"[red]MCP default error:[/] {e}")
            return

        if args.startswith("tools"):
            parts = args.split(maxsplit=1)
            name = parts[1].strip() if len(parts) > 1 else None
            try:
                tools_list = asyncio.run(mcp_manager.list_tools(name))
                if not tools_list:
                    console.print("[bright_black](no tools reported)[/]")
                else:
                    for t in tools_list:
                        nm = t.get("name", "?")
                        desc = t.get("description", "")
                        console.print(f"- [white]{nm}[/white] — {desc}")
            except Exception as e:
                console.print(f"[red]MCP tools error:[/] {e}")
            return

        if args.startswith("call "):
            # /mcp call <tool> '<json>'   OR   /mcp call <name> <tool> '<json>'
            try:
                parts = shlex.split(args)  # reuse shlex
            except Exception as e:
                console.print(f"[red]Parse error:[/] {e}")
                return

            # parts e.g. ["call","server","tool","{...}"] or ["call","tool","{...}"]
            parts = parts[1:]  # drop "call"
            server = None
            tool = None
            payload = None
            if len(parts) == 2:
                tool, payload = parts
            elif len(parts) >= 3:
                server, tool, payload = parts[0], parts[1], " ".join(parts[2:])
            else:
                console.print(
                    "[red]Usage:[/] /mcp call <tool> '{json}'  OR  /mcp call <server> <tool> '{json}'")
                return
            import json
            try:
                args_obj = json.loads(payload)
            except Exception as e:
                console.print(f"[red]Bad JSON:[/] {e}")
                return
            try:
                res = asyncio.run(mcp_manager.call(tool, args_obj, server))
                console.print("[green]MCP result:[/]")
                console.print(res)
            except Exception as e:
                console.print(
                    f"[red]MCP call error:[/] {type(e).__name__}: {e}")
            return

        if args.startswith("remove "):
            name = args.split(maxsplit=1)[1].strip()
            try:
                asyncio.run(mcp_manager.remove(name))
                console.print(f"[green]MCP server '{name}' removed.[/]")
            except Exception as e:
                console.print(f"[red]MCP remove error:[/] {e}")
            return

        console.print("""[yellow]Unknown MCP command.[/]
            Try:
            /mcp add -n <name> -c "<command>" [--env KEY=VAL,KEY2=VAL]
            /mcp list
            /mcp default <name>
            /mcp tools [<name>]
            /mcp call <tool> '{json}'
            /mcp call <name> <tool> '{json}'
            /mcp remove <name>""")
        return

        _run_once(s, verbose)
